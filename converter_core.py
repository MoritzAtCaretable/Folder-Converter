"""
converter_core — the Folder Converter's conversion logic, without any UI.

Formats and defaults, presets, the FFMPEG commands, background removal
(colour key, or AI via rembg), Real-ESRGAN upscaling and the batch run itself.
The UI (webui_api.py + webui/) only calls ConversionEngine.run() and the
preset helpers.

Presets describe the OUTPUT recipe only; the input is whatever you select.
Stored in ~/.folder_converter/presets.json
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Source for the rembg worker subprocess. rembg/onnxruntime can deadlock when
# imported on a background thread of a GUI process on macOS, so we run it in
# its own process and talk to it over stdin/stdout with one JSON request per line.
AI_WORKER_SRC = r'''
import sys, json
_out = sys.stdout            # keep the real stdout for our JSON protocol
sys.stdout = sys.stderr      # send any library chatter to stderr instead
def emit(o):
    _out.write(json.dumps(o) + "\n"); _out.flush()
try:
    from rembg import remove, new_session
    from PIL import Image, ImageFilter
    Image.MAX_IMAGE_PIXELS = None
    sess = new_session()
except BaseException as e:
    emit({"ready": False, "err": repr(e)}); sys.exit(1)
def clean_edges(img, erode, feather):
    if erode <= 0 and feather <= 0:
        return img
    a = img.getchannel("A")
    for _ in range(int(erode)):
        a = a.filter(ImageFilter.MinFilter(3))   # shrink mask ~1px, clips the fringe
    if feather > 0:
        a = a.filter(ImageFilter.GaussianBlur(feather))
    img.putalpha(a)
    return img
emit({"ready": True})
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        img = Image.open(req["in"]).convert("RGBA")
        if req.get("matting"):
            out = remove(img, session=sess, alpha_matting=True,
                         alpha_matting_foreground_threshold=int(req.get("mat_fg", 240)),
                         alpha_matting_background_threshold=int(req.get("mat_bg", 10)),
                         alpha_matting_erode_size=int(req.get("mat_erode", 10)))
        else:
            out = remove(img, session=sess)
        out = clean_edges(out, req.get("erode", 0), req.get("feather", 0.0))
        out.save(req["out"])
        emit({"ok": True})
    except Exception as e:
        emit({"ok": False, "err": repr(e)})
'''

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VIDEO_EXTS = {"mp4", "mov", "mkv", "avi", "webm", "flv", "wmv", "m4v", "mpg", "mpeg"}
AUDIO_EXTS = {"wav", "mp3", "flac", "aac", "ogg", "opus", "m4a", "wma"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

VIDEO_FORMATS = ["webm", "mov", "mp4"]
AUDIO_FORMATS = ["mp3", "opus", "wav"]
IMAGE_FORMATS = ["png", "jpg", "webp"]
TARGET_FORMATS = VIDEO_FORMATS + AUDIO_FORMATS + IMAGE_FORMATS

DEFAULT_CRF = {"webm": "30", "mov": "23", "mp4": "23"}
RESIZE_OPS = ["Crop", "Stretch", "Fit (pad)"]
IMG_OPS = ["Stretch", "Fit (keep aspect)", "Crop",
           "Width (keep aspect)", "Height (keep aspect)"]
SAMPLE_RATES = ["Keep", "48000", "44100", "96000"]
CHANNELS = ["Keep", "1", "2"]

OUTPUT_SUFFIX = " - converted"
PRESET_FILE = Path.home() / ".folder_converter" / "presets.json"

# When launched via pythonw (no console), child processes would otherwise flash
# a console window each time — this suppresses that on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# Large or 16-bit images can decode to multi-GB frames; ffmpeg's default single
# allocation cap (~2 GB) then rejects them with a "no packets" error. Raise it.
MAX_ALLOC = 8 * 1024 ** 3  # 8 GiB


def media_kind(ext):
    """'video' | 'audio' | 'image' for a file extension (without the dot)."""
    ext = ext.lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "image"


def _app_dir():
    """The folder the app lives in (where this script sits)."""
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path(os.path.abspath(sys.argv[0])).parent


def asset_path(name):
    """Locate a bundled file (icon, realesrgan/, …) sitting next to this script,
    regardless of how it was launched (`python converter.py` or the .app launcher)."""
    bases = []
    try:
        bases.append(Path(__file__).resolve().parent)
    except NameError:
        pass
    bases.append(Path(os.path.abspath(sys.argv[0])).parent)
    for base in bases:
        p = base / name
        if p.exists():
            return p
    return bases[0] / name


def resolve_ffmpeg():
    """Find ffmpeg even when PATH is minimal (e.g. launched from a Mac app icon)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                 "/usr/bin/ffmpeg", "/snap/bin/ffmpeg"):
        if os.path.exists(cand):
            return cand
    return None


CONFIG_FILE = Path.home() / ".folder_converter" / "config.json"


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def _realesrgan_binname():
    return "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"


def _realesrgan_os_subdir():
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return "linux"


def resolve_realesrgan(configured=None):
    """Find the realesrgan-ncnn-vulkan binary. Order: user-set path (only if it
    really points at the binary), a bundled realesrgan/ folder next to the app
    (searched recursively, so an extra dated subfolder from the zip is fine),
    PATH, then common locations."""
    binname = _realesrgan_binname()
    if configured and os.path.exists(configured) and Path(configured).name == binname:
        return configured
    root = asset_path("realesrgan")
    direct = root / _realesrgan_os_subdir() / binname
    if direct.exists():
        return str(direct)
    if root.exists():
        try:
            for cand in root.rglob(binname):
                return str(cand)
        except Exception:
            pass
    found = shutil.which(binname)
    if found:
        return found
    cands = ["/opt/homebrew/bin/" + binname, "/usr/local/bin/" + binname]
    home = Path.home()
    for d in (home / "Downloads", home / "Desktop", home):
        cands.append(str(d / "realesrgan-ncnn-vulkan" / binname))
    for cand in cands:
        if os.path.exists(cand):
            return cand
    return None


# ---------------------------------------------------------------------------
# Form (= one output recipe) and presets
# ---------------------------------------------------------------------------

DEFAULT_FORM = {
    "target": "webm", "resize": False, "width": 1920, "height": 1080,
    "resize_op": "Crop", "crf": 30, "normalize": False, "target_db": 0.0,
    "bitrate": "128k", "samplerate": "Keep", "channels": "Keep",
    "trim": False, "trim_start": 0.0, "trim_end": 0.0,
    "img_resize": False, "img_width": 2500, "img_height": 2500,
    "img_op": "Stretch", "img_quality": 90,
    "img_trim": False, "img_trim_l": 0, "img_trim_r": 0,
    "img_trim_t": 0, "img_trim_b": 0,
    "img_bg_mode": "off", "img_bg_color": "#FFFFFF",
    "img_bg_similarity": 0.15, "img_bg_blend": 0.0,
    "img_ai_erode": 1, "img_ai_feather": 0.5,
    "img_ai_matting": False, "img_ai_fg": 240, "img_ai_bg": 10,
    "img_ai_mat_erode": 10,
    "img_upscale": False, "img_upscale_factor": "4", "img_upscale_mode": "Photo",
}

INT_FIELDS = {"width", "height", "crf", "img_width", "img_height", "img_quality",
              "img_trim_l", "img_trim_r", "img_trim_t", "img_trim_b",
              "img_ai_erode", "img_ai_fg", "img_ai_bg", "img_ai_mat_erode"}
FLOAT_FIELDS = {"target_db", "trim_start", "trim_end",
                "img_bg_similarity", "img_bg_blend", "img_ai_feather"}
BOOL_FIELDS = {"resize", "normalize", "trim", "img_resize", "img_trim",
               "img_ai_matting", "img_upscale"}

DEFAULT_PRESETS = {
    "To Opus (peak normalize)": {
        "target": "opus", "resize": False, "width": 1920, "height": 1080,
        "resize_op": "Crop", "crf": 30, "normalize": True, "target_db": 0.0,
        "bitrate": "128k", "samplerate": "48000", "channels": "2",
    },
    "To WebM (crop 1800x1080)": {
        "target": "webm", "resize": True, "width": 1800, "height": 1080,
        "resize_op": "Crop", "crf": 30, "normalize": False, "target_db": 0.0,
        "bitrate": "128k", "samplerate": "Keep", "channels": "Keep",
    },
    "To PNG (resize 2500, stretch)": {
        "target": "png", "img_resize": True, "img_width": 2500,
        "img_height": 2500, "img_op": "Stretch", "img_quality": 90,
    },
}

RENAME_MAP = {
    "WAV -> Opus (peak normalize)": "To Opus (peak normalize)",
    "MOV -> WebM (crop 1800x1080)": "To WebM (crop 1800x1080)",
}


def coerce_form(values):
    """Turn raw form values (numbers may arrive as text) into a typed recipe.
    Raises ValueError if a numeric field isn't a number."""
    form = {}
    for key, default in DEFAULT_FORM.items():
        v = values.get(key, default)
        if key in INT_FIELDS:
            form[key] = int(str(v).strip())
        elif key in FLOAT_FIELDS:
            form[key] = float(str(v).strip().replace(",", "."))
        elif key in BOOL_FIELDS:
            form[key] = bool(v)
        else:
            form[key] = str(v).strip()
    if form["target"] not in TARGET_FORMATS:
        raise ValueError(f"unknown target format: {form['target']}")
    return form


def conversion_opts(form):
    """The recipe as the conversion expects it ('Keep' → leave unchanged)."""
    opts = dict(form)
    opts["samplerate"] = None if form["samplerate"] == "Keep" else form["samplerate"]
    opts["channels"] = None if form["channels"] == "Keep" else form["channels"]
    return opts


def write_presets(presets):
    try:
        PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
        PRESET_FILE.write_text(json.dumps(presets, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_presets():
    presets = None
    try:
        if PRESET_FILE.exists():
            presets = json.loads(PRESET_FILE.read_text(encoding="utf-8"))
    except Exception:
        presets = None
    seeded = presets is None
    if seeded:
        presets = {name: dict(p) for name, p in DEFAULT_PRESETS.items()}
    changed = False
    for old, new in RENAME_MAP.items():
        if old in presets and new not in presets:
            presets[new] = presets.pop(old)
            changed = True
    for p in presets.values():
        if "img_trim_px" in p:  # migrate old single-value trim to per-edge
            v = p.pop("img_trim_px")
            for k in ("img_trim_l", "img_trim_r", "img_trim_t", "img_trim_b"):
                p.setdefault(k, v)
            changed = True
        if "img_bg_remove" in p:  # migrate old single bg toggle to mode string
            p.setdefault("img_bg_mode", "color" if p.pop("img_bg_remove") else "off")
            changed = True
        for k, v in DEFAULT_FORM.items():
            if k not in p:
                p[k] = v
                changed = True
    if seeded or changed:
        write_presets(presets)
    return presets


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

class ConversionEngine:
    """Runs a batch — without any UI.

    Feedback goes out through three callbacks, called from the worker thread
    (so they must be thread-safe):
      log(text), status(title, detail), progress(done, total)
    """

    def __init__(self, log=print, status=None, progress=None):
        self.log = log
        self.status = status or (lambda title, detail="": None)
        self.progress = progress or (lambda done, total: None)
        found = resolve_ffmpeg()
        self.ffmpeg_found = found is not None
        self.ffmpeg = found or "ffmpeg"
        self.cancel_event = threading.Event()
        self.current_proc = None
        self._ai_proc = None
        self._ai_q = None
        self._ai_err = None
        self._upscaler_prepared = False
        atexit.register(self.shutdown_ai_worker)

    def cancel(self):
        self.cancel_event.set()
        proc = self.current_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _ffmpeg_error(self, err):
        """Pull the meaningful cause out of ffmpeg's stderr, not the generic tail."""
        lines = [l.strip() for l in (err or "").splitlines() if l.strip()]
        keys = ("error", "cannot", "invalid", "unsupported", "no such",
                "permission", "exceeds", "too large", "allocate", "get_buffer",
                "not found", "no space")
        hits = [l for l in lines
                if any(k in l.lower() for k in keys)
                and "received no packets" not in l.lower()
                and "conversion failed" not in l.lower()]
        if hits:
            return " | ".join(hits[:2])
        return lines[-1] if lines else "unbekannter Fehler"

    def detect_max_volume(self, src):
        result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-i", str(src),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, creationflags=NO_WINDOW)
        m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
        return float(m.group(1)) if m else None

    def detect_duration(self, src):
        """Read the media duration in seconds from ffmpeg's info output."""
        result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-i", str(src)],
            capture_output=True, text=True, creationflags=NO_WINDOW)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if not m:
            return None
        h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mnt * 60 + s

    def detect_dimensions(self, src):
        """Read the first video/image stream's pixel dimensions (w, h)."""
        result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-i", str(src)],
            capture_output=True, text=True, creationflags=NO_WINDOW)
        for line in result.stderr.splitlines():
            if "Video:" in line:
                m = re.search(r"\b(\d+)x(\d+)\b", line)
                if m:
                    return int(m.group(1)), int(m.group(2))
        return None

    def detect_has_alpha(self, src):
        """True if the source's video stream uses an alpha-bearing pixel format."""
        result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-i", str(src)],
            capture_output=True, text=True, creationflags=NO_WINDOW)
        tokens = ("yuva", "rgba", "argb", "abgr", "bgra", "ya8", "ya16",
                  "gbrap", "rgba64")
        for line in result.stderr.splitlines():
            if "Video:" in line:
                low = line.lower()
                return any(tok in low for tok in tokens)
        return False

    def _ensure_rembg_model(self):
        """Pre-download the u2net model with visible progress in the app log.
        rembg's own download is silent (it writes to the terminal, not our log),
        which makes the first run look frozen. Downloading it ourselves to the
        location rembg expects (~/.u2net/u2net.onnx) gives the user feedback."""
        import urllib.request
        model_dir = Path.home() / ".u2net"
        model_path = model_dir / "u2net.onnx"
        if model_path.exists() and model_path.stat().st_size > 50_000_000:
            return  # already present
        model_dir.mkdir(parents=True, exist_ok=True)
        url = ("https://github.com/danielgatis/rembg/releases/download/"
               "v0.0.0/u2net.onnx")
        self.log("   Erster KI-Lauf: Modell wird geladen (ca. 170 MB, einmalig) …")
        tmp = model_path.with_name("u2net.onnx.part")
        last = [-5]

        def hook(blocks, bsize, total):
            if total > 0:
                pct = min(100, int(blocks * bsize * 100 / total))
                if pct >= last[0] + 5:
                    last[0] = pct
                    self.log(f"      Modell-Download: {pct} %")

        try:
            urllib.request.urlretrieve(url, tmp, reporthook=hook)
            tmp.replace(model_path)
            self.log("   Modell geladen.")
        except Exception as e:
            try:
                tmp.unlink()
            except Exception:
                pass
            self.log(f"   (Vorab-Download fehlgeschlagen: {e} — rembg lädt es selbst)")

    def _ai_readline(self, timeout):
        """Read one line from the AI worker, honouring cancel; None on timeout."""
        import queue
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.cancel_event.is_set():
                return "__CANCEL__"
            try:
                return self._ai_q.get(timeout=0.5)
            except queue.Empty:
                continue
        return None

    def _ai_read_json(self, timeout):
        """Read lines until a JSON object arrives. Returns (msg | '__CANCEL__' | None, noise)."""
        noise = []
        for _ in range(100):
            line = self._ai_readline(timeout)
            if line == "__CANCEL__":
                return "__CANCEL__", noise
            if line is None:
                return None, noise
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line), noise
            except Exception:
                noise.append(line)
        return None, noise

    def shutdown_ai_worker(self):
        proc = self._ai_proc
        self._ai_proc = None
        if proc and proc.poll() is None:
            try:
                proc.stdin.write('{"cmd": "quit"}\n')
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass

    def _ai_stderr_tail(self):
        try:
            return " | ".join(list(self._ai_err)[-4:]) if self._ai_err else ""
        except Exception:
            return ""

    def _ensure_ai_worker(self):
        """Start the rembg worker process if needed. Returns None or an error string."""
        if self._ai_proc is not None and self._ai_proc.poll() is None:
            return None
        # make sure the model is present first (visible download), so the worker
        # doesn't have to fetch it silently
        self._ensure_rembg_model()
        self.log("   KI-Engine wird in einem eigenen Prozess gestartet (einmalig) …")
        import queue
        from collections import deque
        self._ai_q = queue.Queue()
        self._ai_err = deque(maxlen=50)
        try:
            self._ai_proc = subprocess.Popen(
                [sys.executable, "-u", "-c", AI_WORKER_SRC],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
        except Exception as e:
            return f"KI-Prozess konnte nicht gestartet werden: {e}"

        def out_reader(p, q):
            try:
                for ln in p.stdout:
                    q.put(ln)
            except Exception:
                pass
            q.put(None)

        def err_reader(p, buf):
            try:
                for ln in p.stderr:
                    buf.append(ln.rstrip())
            except Exception:
                pass

        threading.Thread(target=out_reader, args=(self._ai_proc, self._ai_q),
                         daemon=True).start()
        threading.Thread(target=err_reader, args=(self._ai_proc, self._ai_err),
                         daemon=True).start()

        msg, noise = self._ai_read_json(300)  # generous: import + model load
        if msg == "__CANCEL__":
            return "cancelled"
        if msg is None:
            tail = self._ai_stderr_tail() or " | ".join(noise[-4:])
            self.shutdown_ai_worker()
            return ("KI-Engine ist nicht gestartet: "
                    + (tail or "keine Antwort innerhalb von 5 Minuten "
                               "(Problem mit onnxruntime/Umgebung)."))
        if not msg.get("ready"):
            tail = self._ai_stderr_tail() or " | ".join(noise[-4:])
            self.shutdown_ai_worker()
            return (f"KI-Engine konnte nicht starten: {msg.get('err', 'unbekannt')}"
                    + (f" | {tail}" if tail else ""))
        self.log("   KI-Engine bereit.")
        return None

    def _upscale_image(self, src, out_png, factor, mode):
        """Upscale an image with realesrgan-ncnn-vulkan. Returns None or an error."""
        exe = resolve_realesrgan(load_config().get("realesrgan_path"))
        if not exe:
            return ("Real-ESRGAN nicht gefunden — den Ordner realesrgan-ncnn-vulkan "
                    "unter 'realesrgan/' neben die App legen oder so installieren, "
                    "dass er im PATH liegt.")
        if not self._upscaler_prepared:
            folder = str(Path(exe).parent)
            if os.name != "nt":
                try:  # explicitly make the BINARY executable (u+rwX alone won't add
                      # the x-bit if the zip extracted it without one)
                    os.chmod(exe, os.stat(exe).st_mode | 0o755)
                except Exception:
                    pass
                try:  # readable models + executable subdirs
                    subprocess.run(["chmod", "-R", "u+rwX", folder],
                                   capture_output=True)
                except Exception:
                    pass
            if sys.platform == "darwin":  # clear Gatekeeper quarantine
                try:
                    subprocess.run(["xattr", "-dr", "com.apple.quarantine", folder],
                                   capture_output=True)
                except Exception:
                    pass
            self._upscaler_prepared = True
        model = "realesrgan-x4plus-anime" if mode == "Illustration" else "realesrgan-x4plus"
        cmd = [exe, "-i", str(src), "-o", str(out_png),
               "-n", model, "-s", str(factor), "-f", "png"]
        models_dir = Path(exe).parent / "models"
        if models_dir.exists():
            cmd += ["-m", str(models_dir)]
        try:
            self.current_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=NO_WINDOW)
            _, err = self.current_proc.communicate()
            rc = self.current_proc.returncode
            self.current_proc = None
        except PermissionError:
            return ("Real-ESRGAN darf nicht ausgeführt werden. Einmalig im Terminal beheben:\n"
                    f'      chmod +x "{exe}"\n'
                    f'      xattr -dr com.apple.quarantine "{Path(exe).parent}"')
        except Exception as e:
            return f"Real-ESRGAN konnte nicht gestartet werden: {e}"
        if rc != 0 or not Path(out_png).exists():
            tail = " ".join((err or "").strip().splitlines()[-2:])[:200]
            return f"Real-ESRGAN fehlgeschlagen: {tail or 'unbekannter Fehler'}"
        return None

    def _ai_cutout(self, src, out_png, ai_opts=None):
        """Run rembg in the worker process. Returns None on success, else an error."""
        err = self._ensure_ai_worker()
        if err:
            return err
        req = {"in": str(src), "out": str(out_png)}
        req.update(ai_opts or {})
        try:
            self._ai_proc.stdin.write(json.dumps(req) + "\n")
            self._ai_proc.stdin.flush()
        except Exception as e:
            self.shutdown_ai_worker()
            return f"KI-Engine nicht erreichbar: {e}"
        msg, noise = self._ai_read_json(600)
        if msg == "__CANCEL__":
            return "cancelled"
        if msg is None:
            tail = self._ai_stderr_tail() or " | ".join(noise[-4:])
            self.shutdown_ai_worker()
            return "KI-Engine reagiert nicht mehr" + (f": {tail}" if tail else ".")
        if msg.get("ok"):
            return None
        return f"KI-Hintergrundentfernung fehlgeschlagen: {msg.get('err', 'unbekannt')}"

    def build_command(self, src, dst, target, opts, gain_db, duration=None,
                      dims=None, has_alpha=False):
        is_audio = target in AUDIO_FORMATS
        is_image = target in IMAGE_FORMATS
        cmd = [self.ffmpeg, "-hide_banner", "-max_alloc", str(MAX_ALLOC),
               "-y", "-i", str(src)]

        if is_audio and opts["trim"] and duration is not None:
            start = max(0.0, opts["trim_start"])
            keep = duration - start - max(0.0, opts["trim_end"])
            if start > 0:
                cmd += ["-ss", f"{start:.3f}"]
            cmd += ["-t", f"{keep:.3f}"]

        if is_image:
            parts = []
            l = max(0, opts["img_trim_l"]); r = max(0, opts["img_trim_r"])
            t = max(0, opts["img_trim_t"]); b = max(0, opts["img_trim_b"])
            trim_on = opts["img_trim"] and (l + r + t + b) > 0
            if trim_on:
                # shave the requested px off each chosen edge
                parts.append(f"crop=iw-{l + r}:ih-{t + b}:{l}:{t}")
            if opts["img_resize"]:
                w, h, op = opts["img_width"], opts["img_height"], opts["img_op"]
                if op == "Stretch":
                    parts.append(f"scale={w}:{h}")
                elif op == "Fit (keep aspect)":
                    parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
                elif op == "Crop":
                    parts.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                                 f"crop={w}:{h}")
                elif op == "Width (keep aspect)":
                    parts.append(f"scale={w}:-1")
                else:  # Height (keep aspect)
                    parts.append(f"scale=-1:{h}")
            elif trim_on and dims:
                # no resize requested: stretch the trimmed image back to original size
                parts.append(f"scale={dims[0]}:{dims[1]}")
            if parts:
                chain = ",".join(parts)
                # premultiplied alpha avoids white/dark halos on transparent edges
                if target in ("png", "webp"):
                    chain = (f"format=rgba,premultiply=inplace=1,{chain},"
                             f"unpremultiply=inplace=1")
                geo_chain = chain
            else:
                geo_chain = ""

            vf = []
            if opts.get("img_bg_mode") == "color":
                col = opts["img_bg_color"].strip()
                col = "0x" + col[1:] if col.startswith("#") else col  # #RRGGBB -> 0xRRGGBB
                vf.append("format=rgba")
                vf.append(f"colorkey={col}:{opts['img_bg_similarity']}:"
                          f"{opts['img_bg_blend']}")
            if geo_chain:
                vf.append(geo_chain)
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd += ["-frames:v", "1"]
            q = max(1, min(100, opts["img_quality"]))
            if target == "jpg":
                cmd += ["-q:v", str(round(31 - (q - 1) / 99 * 29))]  # 1..100 -> 31..2
            elif target == "webp":
                cmd += ["-c:v", "libwebp", "-quality", str(q)]
            # png is lossless; no quality flag
            cmd.append(str(dst))
            return cmd

        if not is_audio and opts["resize"]:
            w, h, op = opts["width"], opts["height"], opts["resize_op"]
            if op == "Crop":
                vf = f"crop={w}:{h}"
            elif op == "Stretch":
                vf = f"scale={w}:{h}"
            else:  # Fit (pad)
                vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
            cmd += ["-vf", vf]

        if opts["normalize"] and gain_db is not None:
            cmd += ["-af", f"volume={gain_db}dB"]
        if opts["samplerate"]:
            cmd += ["-ar", opts["samplerate"]]
        if opts["channels"]:
            cmd += ["-ac", opts["channels"]]

        if is_audio:
            cmd += ["-vn"]
            if target == "wav":
                cmd += ["-c:a", "pcm_s16le"]
            elif target == "mp3":
                cmd += ["-c:a", "libmp3lame", "-b:a", opts["bitrate"]]
            elif target == "opus":
                cmd += ["-c:a", "libopus", "-b:a", opts["bitrate"]]
        else:
            if target == "webm" and has_alpha:
                # VP9 alpha is unreliable; VP8 (libvpx) + yuva420p is the standard
                # transparent-webm path. -auto-alt-ref 0 keeps the alpha plane intact.
                cmd += ["-c:v", "libvpx", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                        "-crf", str(opts["crf"]), "-b:v", "2M",
                        "-c:a", "libopus", "-b:a", opts["bitrate"]]
            elif target == "webm":
                cmd += ["-c:v", "libvpx-vp9", "-crf", str(opts["crf"]),
                        "-b:v", "0", "-row-mt", "1",
                        "-c:a", "libopus", "-b:a", opts["bitrate"]]
            else:  # mp4 / mov (H.264 — no transparency support)
                cmd += ["-c:v", "libx264", "-crf", str(opts["crf"]),
                        "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", opts["bitrate"]]

        cmd.append(str(dst))
        return cmd

    @staticmethod
    def _unique_path(dst):
        """If dst exists, return name01/name02/... so nothing gets overwritten."""
        if not dst.exists():
            return dst
        n = 1
        while True:
            cand = dst.with_name(f"{dst.stem}{n:02d}{dst.suffix}")
            if not cand.exists():
                return cand
            n += 1

    def run(self, folder, files, target, opts):
        """Convert `files` (paths below `folder`) to `target`. Blocks until done
        or cancelled; returns {"ok", "total", "out_root", "cancelled"}."""
        folder = Path(folder)
        out_root = folder / f"{target}{OUTPUT_SUFFIX}"
        total = len(files)
        ok = 0
        for i, src in enumerate(files, 1):
            if self.cancel_event.is_set():
                break
            rel = src.relative_to(folder)
            dst = out_root / rel.with_suffix(f".{target}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = self._unique_path(dst)  # never overwrite: name, name01, name02, …

            gain_db = None
            if opts["normalize"] and target not in IMAGE_FORMATS:
                self.status(f"Analysiere {i}/{total}", src.name)
                max_vol = self.detect_max_volume(src)
                if self.cancel_event.is_set():
                    break
                if max_vol is None:
                    self.log(f"   {src.name}: Pegel nicht lesbar, Normalisierung übersprungen")
                else:
                    gain_db = round(opts["target_db"] - max_vol, 1)

            duration = None
            if opts["trim"] and target in AUDIO_FORMATS:
                duration = self.detect_duration(src)
                if self.cancel_event.is_set():
                    break
                if duration is None:
                    self.log(f"   {src.name}: Länge nicht lesbar, Kürzen übersprungen")
                else:
                    keep = duration - opts["trim_start"] - opts["trim_end"]
                    if keep <= 0:
                        self.log(f"   ÜBERSPRUNGEN {rel}: Kürzen ({opts['trim_start']} s + "
                                 f"{opts['trim_end']} s) lässt von {duration:.2f} s "
                                 f"nichts übrig")
                        self.progress(i, total)
                        continue

            dims = None
            edge_sum = (opts["img_trim_l"] + opts["img_trim_r"]
                        + opts["img_trim_t"] + opts["img_trim_b"])
            if (target in IMAGE_FORMATS and opts["img_trim"] and edge_sum > 0
                    and not opts["img_resize"]):
                dims = self.detect_dimensions(src)
                if self.cancel_event.is_set():
                    break
                if dims is None:
                    self.log(f"   {src.name}: Größe nicht lesbar, beschneide ohne "
                             f"Zurückstrecken (Ergebnis wird etwas kleiner)")
                elif (opts["img_trim_l"] + opts["img_trim_r"] >= dims[0]
                      or opts["img_trim_t"] + opts["img_trim_b"] >= dims[1]):
                    self.log(f"   ÜBERSPRUNGEN {rel}: Beschneiden entfernt das ganze "
                             f"{dims[0]}x{dims[1]}-Bild")
                    self.progress(i, total)
                    continue

            has_alpha = False
            if target == "webm":
                has_alpha = self.detect_has_alpha(src)
                if self.cancel_event.is_set():
                    break

            self.status(f"Konvertiere {i}/{total}", src.name)
            self.log(f"→ {rel}")
            # ffmpeg on macOS Homebrew often lacks libwebp, so for webp we let
            # ffmpeg produce a lossless PNG (all trim/resize/alpha handling intact)
            # and encode the final webp with Pillow, which bundles its own encoder.
            webp_via_pillow = (target == "webp")
            ff_dst = dst.with_suffix(".png") if webp_via_pillow else dst
            ff_target = "png" if webp_via_pillow else target

            # AI background removal can't be done by ffmpeg: run rembg first and
            # feed the transparent cutout into the normal trim/resize/encode flow.
            ff_src = src
            up_temp = None
            ai_temp = None
            ai_error = None
            if target in IMAGE_FORMATS and opts.get("img_upscale"):
                self.status(f"Hochskalieren {i}/{total}", src.name)
                mode = opts.get("img_upscale_mode", "Photo")
                self.log(f"   hochskalieren ×{opts.get('img_upscale_factor', '4')} "
                         f"({'Foto' if mode == 'Photo' else mode}) …")
                up_temp = dst.with_name(dst.stem + "__up.png")
                up_err = self._upscale_image(ff_src, up_temp,
                                             opts.get("img_upscale_factor", "4"),
                                             opts.get("img_upscale_mode", "Photo"))
                if up_err:
                    ai_error = up_err
                    up_temp = None
                else:
                    ff_src = up_temp
                    if dims is not None:  # trim-rescale target must follow the new size
                        dims = self.detect_dimensions(ff_src) or dims

            if ai_error is None and target in IMAGE_FORMATS and opts.get("img_bg_mode") == "ai":
                self.status(f"KI-Freistellung {i}/{total}", src.name)
                self.log("   Hintergrund mit KI entfernen …")
                ai_temp = dst.with_name(dst.stem + "__aicut.png")
                err_msg = self._ai_cutout(ff_src, ai_temp, {
                    "erode": opts.get("img_ai_erode", 0),
                    "feather": opts.get("img_ai_feather", 0.0),
                    "matting": opts.get("img_ai_matting", False),
                    "mat_fg": opts.get("img_ai_fg", 240),
                    "mat_bg": opts.get("img_ai_bg", 10),
                    "mat_erode": opts.get("img_ai_mat_erode", 10),
                })
                if err_msg:
                    ai_error = err_msg
                    ai_temp = None
                else:
                    ff_src = ai_temp

            if ai_error is not None:
                rc, err = 1, ai_error
            else:
                self.current_proc = subprocess.Popen(
                    self.build_command(ff_src, ff_dst, ff_target, opts, gain_db,
                                       duration, dims, has_alpha),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=NO_WINDOW)
                _, err = self.current_proc.communicate()
                rc = self.current_proc.returncode
                self.current_proc = None

            if self.cancel_event.is_set():
                self.log(f"   gestoppt bei {rel} (diese Datei ist eventuell unvollständig)")
                break
            if rc == 0 and webp_via_pillow:
                try:
                    from PIL import Image
                    im = Image.open(ff_dst)
                    im.save(dst, "WEBP", quality=max(1, min(100, opts["img_quality"])))
                    im.close()
                    try:
                        ff_dst.unlink()
                    except Exception:
                        pass
                except ImportError:
                    rc, err = 1, "Pillow fehlt — bitte den Installer erneut ausführen"
                except Exception as e:
                    rc, err = 1, f"WebP-Kodierung fehlgeschlagen: {e}"
            if rc == 0:
                ok += 1
                self.log(f"   ✓ {dst.relative_to(folder)}")
            else:
                self.log(f"   ✗ Fehlgeschlagen: {self._ffmpeg_error(err)}")
            if ai_temp is not None:
                try:
                    ai_temp.unlink()
                except Exception:
                    pass
            if up_temp is not None:
                try:
                    up_temp.unlink()
                except Exception:
                    pass
            self.progress(i, total)

        cancelled = self.cancel_event.is_set()
        if cancelled:
            self.log(f"Abgebrochen nach {ok}/{total}.")
        else:
            self.log(f"✔ Fertig: {ok}/{total} erfolgreich. Ausgabe in {out_root}")
        self.cancel_event.clear()
        self.current_proc = None
        return {"ok": ok, "total": total, "out_root": str(out_root), "cancelled": cancelled}
