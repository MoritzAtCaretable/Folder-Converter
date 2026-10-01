"""
media_preview — what the trim view needs to show a file: a waveform, a
filmstrip of video frames, a playable preview copy for formats the web view
can't play (mkv, avi, wma, …) and a tiny local HTTP server that streams the
media to <video>/<audio>. WebKit needs HTTP range requests to seek, which
file:// URLs outside the UI folder don't offer.
"""

import base64
import mimetypes
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from converter_core import NO_WINDOW

CONTENT_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wma": "audio/x-ms-wma",
}


def waveform(ffmpeg, src, buckets=1200, duration=None):
    """Peak per bucket (0..1) of the first audio stream; [] if there is none."""
    rate = 4000 if not duration or duration < 1800 else 1000  # keeps long files light
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(src), "-map", "0:a:0", "-ac", "1",
         "-ar", str(rate), "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        capture_output=True, creationflags=NO_WINDOW)
    raw = result.stdout
    samples = array("h")
    samples.frombytes(raw[:len(raw) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    if not n:
        return []
    buckets = max(1, min(buckets, n))
    step = n / buckets
    peaks = []
    for i in range(buckets):
        seg = samples[int(i * step):max(int(i * step) + 1, int((i + 1) * step))]
        peaks.append(round(max(max(seg), -min(seg)) / 32768, 3))
    return peaks


def filmstrip(ffmpeg, src, duration, count=10, height=112):
    """`count` frames side by side as a JPEG data URL (None if that fails)."""
    count = max(2, min(int(count), 40))
    span = max(0.0, (duration or 0) - 0.3)
    cmd = [ffmpeg, "-v", "error"]
    for i in range(count):
        cmd += ["-ss", f"{span * (i + 0.5) / count:.3f}", "-i", str(src)]
    chains = "".join(f"[{i}:v:0]scale=-2:{height},setsar=1[v{i}];" for i in range(count))
    stack = "".join(f"[v{i}]" for i in range(count)) + f"hstack=inputs={count}[out]"
    cmd += ["-filter_complex", chains + stack, "-map", "[out]", "-frames:v", "1",
            "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    result = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if result.returncode != 0 or not result.stdout:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii")


def make_proxy(ffmpeg, src, kind, out_dir, name):
    """Playable stand-in (H.264/AAC) for previewing; returns its path or None."""
    if kind == "video":
        out = Path(out_dir) / f"{name}.mp4"
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src),
               "-vf", "scale=-2:'trunc(min(480,ih)/2)*2'", "-c:v", "libx264",
               "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
    else:
        out = Path(out_dir) / f"{name}.m4a"
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-vn",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    result = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    return out if result.returncode == 0 and out.exists() else None


class MediaServer:
    """Serves registered files on 127.0.0.1 (random port, random path) with
    range support. Only files handed to url() are reachable."""

    def __init__(self):
        self._files = {}                 # key -> Path
        self._urls = {}                  # Path -> url
        self._token = secrets.token_urlsafe(12)
        self._tmp = None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self._serve(head=True)

            def do_GET(self):
                self._serve(head=False)

            def _serve(self, head):
                parts = unquote(self.path.split("?")[0]).strip("/").split("/")
                path = (server._files.get(Path(parts[1]).stem)
                        if len(parts) == 2 and parts[0] == server._token else None)
                if path is None or not path.is_file():
                    self.send_error(404)
                    return
                size = path.stat().st_size
                start, end = 0, size - 1
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    a, _, b = rng[6:].split(",")[0].partition("-")
                    try:
                        if a:
                            start, end = int(a), (min(int(b), size - 1) if b else size - 1)
                        elif b:
                            start = max(0, size - int(b))
                    except ValueError:
                        pass
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                ctype = (CONTENT_TYPES.get(path.suffix.lower())
                         or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if head:
                    return
                try:
                    with open(path, "rb") as f:
                        f.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = f.read(min(256 * 1024, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass                 # the player cancelled the request — normal while seeking

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def url(self, path):
        path = Path(path)
        if path not in self._urls:
            key = secrets.token_urlsafe(9)
            self._files[key] = path
            port = self._httpd.server_address[1]
            self._urls[path] = f"http://127.0.0.1:{port}/{self._token}/{key}{path.suffix.lower()}"
        return self._urls[path]

    def temp_dir(self):
        if self._tmp is None:
            self._tmp = Path(tempfile.mkdtemp(prefix="folder_converter_"))
        return self._tmp

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
