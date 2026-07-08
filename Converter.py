"""
Folder Converter — drag-and-drop batch media converter built on FFMPEG.

Workflow:
  1. Drag a folder onto the drop zone (or click "Browse folder").
  2. Filter by format if you like, then select files — click, shift+click for
     a range, ctrl/cmd+click to toggle, or tick "Select all".
  3. Pick a target and adjust parameters, or load a saved preset.
  4. Convert (lower-right); the red ■ beside it cancels after confirmation.

UI notes:
  - Drag the thin handle under the file list or the console to resize them.
    If the app grows past the window, a scrollbar appears; a "Reset size"
    chip shows top-right to snap panels back to default.
  - Save current is green when settings differ from every preset, and grey/
    disabled when they already match one.

Presets describe the OUTPUT recipe only; the input is whatever you select.
Stored in ~/.folder_converter/presets.json

Requirements (install once):
    pip install customtkinter tkinterdnd2
    pip install pillow            # only needed if you export to .webp
And ffmpeg must be on your PATH.   Run:  python converter.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

# Source for the rembg worker subprocess. rembg/onnxruntime can deadlock when
# imported on a background thread inside a Tkinter app on macOS, so we run it in
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

MAC_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Folder Converter</string>
  <key>CFBundleDisplayName</key><string>Folder Converter</string>
  <key>CFBundleIdentifier</key><string>local.folder-converter</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
</dict>
</plist>
"""


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


GREY = ("gray60", "gray35")
GREEN = "#2fa572"
GREEN_HOVER = "#1d6f4d"
RED = "#c0392b"
RED_HOVER = "#922b21"

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


def _app_dir():
    """The folder the app lives in (where this script sits)."""
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path(os.path.abspath(sys.argv[0])).parent


def build_launcher(log=print):
    """Create a double-clickable launcher next to the app (not on the Desktop)."""
    if os.name == "nt":
        _build_windows_shortcut(log)
    elif sys.platform == "darwin":
        _build_macos_app(log)
    else:
        log("Launcher creation is set up for Windows and macOS only.")


def _build_macos_app(log=print):
    script = os.path.abspath(sys.argv[0])
    python = sys.executable
    workdir = str(_app_dir())
    app = _app_dir() / "Folder Converter.app"
    macos = app / "Contents" / "MacOS"
    try:
        macos.mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_text(MAC_PLIST, encoding="utf-8")
        res = app / "Contents" / "Resources"
        res.mkdir(parents=True, exist_ok=True)
        icns = asset_path("icon.icns")
        if icns.exists():
            shutil.copyfile(icns, res / "icon.icns")
        launcher = macos / "launcher"
        launcher.write_text(
            "#!/bin/bash\n"
            'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
            f'cd "{workdir}"\n'
            f'exec "{python}" "{script}"\n',
            encoding="utf-8")
        os.chmod(launcher, 0o755)
        log(f"Created 'Folder Converter.app' in the app folder:\n   {app}\n"
            "Drag it to your Desktop, or right-click → Make Alias to put a link there.")
    except Exception as e:
        log(f"Couldn't create the app bundle: {e}")


def _build_windows_shortcut(log=print):
    script = os.path.abspath(sys.argv[0])
    pyw = Path(sys.executable).with_name("pythonw.exe")  # launches without a console
    target = str(pyw if pyw.exists() else sys.executable)
    workdir = str(_app_dir())
    lnk = _app_dir() / "Folder Converter.lnk"
    esc = lambda s: str(s).replace("'", "''")
    ico = asset_path("icon.ico")
    icon_src = str(ico) if ico.exists() else target
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{esc(lnk)}'); "
        f"$s.TargetPath = '{esc(target)}'; "
        f"$s.Arguments = '\"{esc(script)}\"'; "
        f"$s.WorkingDirectory = '{esc(workdir)}'; "
        f"$s.IconLocation = '{esc(icon_src)},0'; "
        "$s.Save()"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode == 0 and lnk.exists():
            log(f"Created 'Folder Converter.lnk' in the app folder:\n   {lnk}\n"
                "Copy it to your Desktop if you like.")
        else:
            log(f"Couldn't create shortcut: "
                f"{(r.stderr or '').strip() or 'unknown error'}")
    except Exception as e:
        log(f"Couldn't create shortcut: {e}")


class DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


class ConverterApp(DnDCTk):
    def __init__(self):
        super().__init__()
        self.title("Folder Converter")
        self._rembg_session = None
        self._rembg_remove = None
        self._ai_proc = None
        self._ai_q = None
        self._ai_err = None
        import atexit
        atexit.register(self._shutdown_ai_worker)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(820, sw - 40), min(900, sh - 80)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(680, 480)
        try:
            for cand in ("icon_window.png", "icon.png"):
                p = asset_path(cand)
                if p.exists():
                    self._icon_img = tk.PhotoImage(file=str(p))
                    self.iconphoto(True, self._icon_img)
                    break
        except Exception:
            pass
        if os.name == "nt":
            try:
                ico = asset_path("icon.ico")
                if ico.exists():
                    self.iconbitmap(str(ico))
            except Exception:
                pass
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.folder = None
        self.found_exts = {}
        self.all_media_files = []
        self.displayed_files = []
        self.presets = self._load_presets()

        self.cancel_event = threading.Event()
        self.current_proc = None

        # resizable-panel state
        self._default_heights = {"files": 200, "log": 150}
        self._heights = dict(self._default_heights)
        self._containers = {}

        self._build_ui()
        self._refresh_save_state()

        self.ffmpeg = resolve_ffmpeg()
        if self.ffmpeg is None:
            self.ffmpeg = "ffmpeg"
            self.log("WARNING: ffmpeg was not found. Install it with "
                     "'brew install ffmpeg' (macOS), then relaunch.")

    # -- Preset persistence ------------------------------------------------

    def _load_presets(self):
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
            self._write_presets(presets)
        return presets

    def _write_presets(self, presets):
        try:
            PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
            PRESET_FILE.write_text(json.dumps(presets, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    # -- UI ----------------------------------------------------------------

    def _build_ui(self):
        bold14 = ctk.CTkFont(size=14, weight="bold")

        # Pinned footer: status + progress on top, Convert / ■ Cancel lower-right
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=20, pady=(6, 14))
        top_row = ctk.CTkFrame(footer, fg_color="transparent")
        top_row.pack(side="top", fill="x")
        self.status = ctk.CTkLabel(top_row, text="", text_color="gray", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.update_btn = ctk.CTkButton(
            top_row, text="⬇ Update", width=92, height=26,
            fg_color="transparent", border_width=1,
            text_color=("gray25", "gray75"), hover_color=("gray80", "gray25"),
            command=self.update_app)
        self.update_btn.pack(side="right")
        row = ctk.CTkFrame(footer, fg_color="transparent")
        row.pack(side="top", fill="x", pady=(6, 0))
        self.progress = ctk.CTkProgressBar(row)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pct_label = ctk.CTkLabel(row, text="", width=44, text_color="gray")
        self.pct_label.pack(side="left", padx=(0, 12))
        self.cancel_btn = ctk.CTkButton(row, text="■", width=52, height=46,
                                        font=ctk.CTkFont(size=16),
                                        fg_color=RED, hover_color=RED_HOVER,
                                        command=self.cancel_conversion, state="disabled")
        self.cancel_btn.pack(side="right")
        self.convert_btn = ctk.CTkButton(row, text="Convert", width=160, height=46,
                                         font=ctk.CTkFont(size=15, weight="bold"),
                                         command=self.start_conversion, state="disabled")
        self.convert_btn.pack(side="right", padx=(0, 12))

        # Scrollable content area (everything else lives in here)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(side="top", fill="both", expand=True)
        self.scroll.grid_columnconfigure(0, weight=1)
        c = self.scroll

        # Reset-size chip (top-right, hidden until a panel is enlarged)
        self.reset_btn = ctk.CTkButton(self, text="⤢ Reset size", width=110, height=26,
                                       font=ctk.CTkFont(size=11),
                                       fg_color=("gray75", "gray30"),
                                       hover_color=("gray65", "gray40"),
                                       command=self.reset_layout)

        # Drop zone
        self.drop_zone = ctk.CTkFrame(c, height=90, fg_color=("gray85", "gray20"),
                                      corner_radius=12)
        self.drop_zone.grid(row=0, column=0, padx=20, pady=(16, 8), sticky="ew")
        self.drop_zone.grid_propagate(False)
        self.drop_label = ctk.CTkLabel(self.drop_zone, text="Drag a folder here  —  or",
                                       font=ctk.CTkFont(size=15))
        self.drop_label.place(relx=0.5, rely=0.34, anchor="center")
        ctk.CTkButton(self.drop_zone, text="Browse folder", width=140,
                      command=self.browse_folder).place(relx=0.5, rely=0.74, anchor="center")
        # whole-window drag & drop is registered in _setup_qol()

        # File picker
        src_frame = ctk.CTkFrame(c)
        src_frame.grid(row=1, column=0, padx=20, pady=8, sticky="ew")
        src_frame.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(src_frame, fg_color="transparent")
        head.grid(row=0, column=0, padx=12, pady=(10, 2), sticky="ew")
        ctk.CTkLabel(head, text="Files to convert", font=bold14).pack(side="left")
        self.filter_menu = ctk.CTkOptionMenu(head, values=["All"], width=120,
                                             command=self.apply_filter)
        self.filter_menu.set("All")
        self.filter_menu.pack(side="right")
        ctk.CTkLabel(head, text="Filter:").pack(side="right", padx=(0, 6))

        tools = ctk.CTkFrame(src_frame, fg_color="transparent")
        tools.grid(row=1, column=0, padx=12, pady=2, sticky="ew")
        self.select_all_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(tools, text="Select all", variable=self.select_all_var,
                        command=self.toggle_select_all).pack(side="left")
        ctk.CTkButton(tools, text="Deselect", width=90,
                      command=self.select_none).pack(side="left", padx=12)
        self.count_label = ctk.CTkLabel(tools, text="", text_color="gray")
        self.count_label.pack(side="right")

        lb_outer = ctk.CTkFrame(src_frame, fg_color="transparent",
                                height=self._heights["files"])
        lb_outer.grid(row=2, column=0, padx=12, pady=(4, 0), sticky="ew")
        lb_outer.grid_propagate(False)
        lb_outer.grid_columnconfigure(0, weight=1)
        lb_outer.grid_rowconfigure(0, weight=1)
        self.file_listbox = tk.Listbox(lb_outer, selectmode="extended",
                                       activestyle="none", borderwidth=0,
                                       highlightthickness=1, relief="flat")
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        sb = ctk.CTkScrollbar(lb_outer, command=self.file_listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.file_listbox.configure(yscrollcommand=sb.set)
        self.file_listbox.bind("<<ListboxSelect>>", self._update_counts)
        self.file_listbox.bind("<Double-Button-1>", self._open_selected)
        self._style_listbox()
        self._containers["files"] = lb_outer
        self._make_grip(src_frame, "files", lb_outer, min_h=100).grid(
            row=3, column=0, padx=12, pady=(3, 10), sticky="ew")

        # Presets
        preset_row = ctk.CTkFrame(c, fg_color="transparent")
        preset_row.grid(row=2, column=0, padx=20, pady=(4, 0), sticky="ew")
        ctk.CTkLabel(preset_row, text="Preset:", font=bold14).pack(side="left", padx=(0, 10))
        names = list(self.presets.keys()) or ["(none)"]
        self.preset_menu = ctk.CTkOptionMenu(preset_row, values=names, width=240,
                                             command=self.apply_preset)
        self.preset_menu.set(names[0])
        self.preset_menu.pack(side="left")
        self.save_btn = ctk.CTkButton(preset_row, text="Save current", width=120,
                                      fg_color=GREEN, hover_color=GREEN_HOVER,
                                      command=self.save_preset)
        self.save_btn.pack(side="left", padx=(28, 10))
        ctk.CTkButton(preset_row, text="Delete", width=80, fg_color=RED,
                      hover_color=RED_HOVER, command=self.delete_preset).pack(side="left")

        # Target
        target_row = ctk.CTkFrame(c, fg_color="transparent")
        target_row.grid(row=3, column=0, padx=20, pady=(8, 0), sticky="ew")
        ctk.CTkLabel(target_row, text="Convert to:", font=bold14).pack(side="left", padx=(0, 8))
        self.target_menu = ctk.CTkOptionMenu(target_row, values=TARGET_FORMATS, width=120,
                                             command=self.on_target_change)
        self.target_menu.set("webm")
        self.target_menu.pack(side="left")

        # Video options
        self.video_frame = ctk.CTkFrame(c)
        self.video_frame.grid(row=4, column=0, padx=20, pady=8, sticky="ew")
        ctk.CTkLabel(self.video_frame, text="Video",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=8, padx=12, pady=(8, 2), sticky="w")
        self.resize_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.video_frame, text="Resize to", variable=self.resize_var).grid(
            row=1, column=0, padx=(12, 6), pady=8, sticky="w")
        self.width_entry = self._entry(self.video_frame, "1920", 70, 1, 1)
        ctk.CTkLabel(self.video_frame, text="x").grid(row=1, column=2, padx=4)
        self.height_entry = self._entry(self.video_frame, "1080", 70, 1, 3)
        self.resize_op = ctk.CTkOptionMenu(self.video_frame, values=RESIZE_OPS, width=110)
        self.resize_op.grid(row=1, column=4, padx=12, pady=8)
        ctk.CTkLabel(self.video_frame, text="Quality (CRF):").grid(row=1, column=5, padx=(20, 6))
        self.crf_entry = self._entry(self.video_frame, DEFAULT_CRF["webm"], 60, 1, 6)

        # Audio options
        self.audio_frame = ctk.CTkFrame(c)
        self.audio_frame.grid(row=5, column=0, padx=20, pady=8, sticky="ew")
        ctk.CTkLabel(self.audio_frame, text="Audio",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=9, padx=12, pady=(8, 2), sticky="w")
        self.normalize_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.audio_frame, text="Peak normalize to",
                        variable=self.normalize_var).grid(
            row=1, column=0, padx=(12, 6), pady=8, sticky="w")
        self.target_db_entry = self._entry(self.audio_frame, "0", 55, 1, 1)
        ctk.CTkLabel(self.audio_frame, text="dB").grid(row=1, column=2, padx=(4, 20))
        ctk.CTkLabel(self.audio_frame, text="Bitrate:").grid(row=1, column=3, padx=(0, 6))
        self.bitrate_entry = self._entry(self.audio_frame, "128k", 70, 1, 4)
        ctk.CTkLabel(self.audio_frame, text="Sample rate:").grid(row=1, column=5, padx=(20, 6))
        self.samplerate_menu = ctk.CTkOptionMenu(self.audio_frame, values=SAMPLE_RATES, width=90)
        self.samplerate_menu.grid(row=1, column=6, pady=8)
        ctk.CTkLabel(self.audio_frame, text="Channels:").grid(row=1, column=7, padx=(20, 6))
        self.channels_menu = ctk.CTkOptionMenu(self.audio_frame, values=CHANNELS, width=80)
        self.channels_menu.grid(row=1, column=8, padx=(0, 12), pady=8)
        self.trim_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.audio_frame, text="Trim — cut from start",
                        variable=self.trim_var).grid(
            row=2, column=0, padx=(12, 6), pady=(0, 8), sticky="w")
        self.trim_start_entry = self._entry(self.audio_frame, "0", 55, 2, 1)
        ctk.CTkLabel(self.audio_frame, text="s").grid(row=2, column=2, padx=(4, 20),
                                                      pady=(0, 8))
        ctk.CTkLabel(self.audio_frame, text="from end:").grid(row=2, column=3,
                                                              padx=(0, 6), pady=(0, 8))
        self.trim_end_entry = self._entry(self.audio_frame, "0", 55, 2, 4)
        ctk.CTkLabel(self.audio_frame, text="s").grid(row=2, column=5, padx=(4, 0),
                                                      pady=(0, 8), sticky="w")

        # Image options (shares row 4 with video; only one shows at a time)
        self.image_frame = ctk.CTkFrame(c)
        self.image_frame.grid(row=4, column=0, padx=20, pady=8, sticky="ew")
        ctk.CTkLabel(self.image_frame, text="Image",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=8, padx=12, pady=(8, 2), sticky="w")
        self.img_resize_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.image_frame, text="Resize to",
                        variable=self.img_resize_var).grid(
            row=1, column=0, padx=(12, 6), pady=8, sticky="w")
        self.img_width_entry = self._entry(self.image_frame, "2500", 70, 1, 1)
        ctk.CTkLabel(self.image_frame, text="x").grid(row=1, column=2, padx=4)
        self.img_height_entry = self._entry(self.image_frame, "2500", 70, 1, 3)
        self.img_op = ctk.CTkOptionMenu(self.image_frame, values=IMG_OPS, width=180)
        self.img_op.grid(row=1, column=4, padx=12, pady=8)
        ctk.CTkLabel(self.image_frame, text="Quality:").grid(row=1, column=5, padx=(20, 6))
        self.img_quality_entry = self._entry(self.image_frame, "90", 55, 1, 6)
        ctk.CTkLabel(self.image_frame, text="(jpg/webp · 1–100)").grid(
            row=1, column=7, padx=(6, 12))
        trimrow = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        trimrow.grid(row=2, column=0, columnspan=8, padx=12, pady=(0, 8), sticky="w")
        self.img_trim_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(trimrow, text="Trim edges",
                        variable=self.img_trim_var).pack(side="left")
        self.img_trim_entries = {}
        for key, label in (("l", "L"), ("r", "R"), ("t", "T"), ("b", "B")):
            ctk.CTkLabel(trimrow, text=label).pack(side="left", padx=(12, 3))
            e = ctk.CTkEntry(trimrow, width=46)
            e.insert(0, "0")
            e.pack(side="left")
            self.img_trim_entries[key] = e
        ctk.CTkLabel(trimrow, text="px  → stretch back to original size").pack(
            side="left", padx=(10, 0))
        bgrow = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        bgrow.grid(row=3, column=0, columnspan=8, padx=12, pady=(0, 4), sticky="w")
        self.img_bg_color_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bgrow, text="Remove bg — color key",
                        variable=self.img_bg_color_var,
                        command=self._bg_color_toggle).pack(side="left")
        ctk.CTkLabel(bgrow, text="color").pack(side="left", padx=(12, 4))
        self.img_bg_color_entry = ctk.CTkEntry(bgrow, width=80)
        self.img_bg_color_entry.insert(0, "#FFFFFF")
        self.img_bg_color_entry.pack(side="left")
        ctk.CTkButton(bgrow, text="Pick", width=50,
                      command=self._pick_bg_color).pack(side="left", padx=(6, 0))
        ctk.CTkButton(bgrow, text="From image", width=90,
                      command=self._pick_from_image).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(bgrow, text="tolerance").pack(side="left", padx=(12, 4))
        self.img_bg_sim_entry = ctk.CTkEntry(bgrow, width=46)
        self.img_bg_sim_entry.insert(0, "0.15")
        self.img_bg_sim_entry.pack(side="left")
        ctk.CTkLabel(bgrow, text="soft edge").pack(side="left", padx=(12, 4))
        self.img_bg_blend_entry = ctk.CTkEntry(bgrow, width=46)
        self.img_bg_blend_entry.insert(0, "0.0")
        self.img_bg_blend_entry.pack(side="left")
        airow = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        airow.grid(row=4, column=0, columnspan=8, padx=12, pady=(0, 8), sticky="w")
        self.img_bg_ai_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(airow, text="Remove bg — AI (auto cutout)",
                        variable=self.img_bg_ai_var,
                        command=self._bg_ai_toggle).pack(side="left")
        ctk.CTkLabel(airow, text="edge shrink").pack(side="left", padx=(12, 4))
        self.img_ai_erode_entry = ctk.CTkEntry(airow, width=40)
        self.img_ai_erode_entry.insert(0, "1")
        self.img_ai_erode_entry.pack(side="left")
        ctk.CTkLabel(airow, text="px   feather").pack(side="left", padx=(8, 4))
        self.img_ai_feather_entry = ctk.CTkEntry(airow, width=40)
        self.img_ai_feather_entry.insert(0, "0.5")
        self.img_ai_feather_entry.pack(side="left")
        matrow = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        matrow.grid(row=5, column=0, columnspan=8, padx=12, pady=(0, 8), sticky="w")
        self.img_ai_matting_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(matrow, text="Alpha matting (finer edges, slower)",
                        variable=self.img_ai_matting_var).pack(side="left")
        ctk.CTkLabel(matrow, text="fg").pack(side="left", padx=(12, 3))
        self.img_ai_fg_entry = ctk.CTkEntry(matrow, width=46)
        self.img_ai_fg_entry.insert(0, "240")
        self.img_ai_fg_entry.pack(side="left")
        ctk.CTkLabel(matrow, text="bg").pack(side="left", padx=(10, 3))
        self.img_ai_bg_entry = ctk.CTkEntry(matrow, width=46)
        self.img_ai_bg_entry.insert(0, "10")
        self.img_ai_bg_entry.pack(side="left")
        ctk.CTkLabel(matrow, text="edge size").pack(side="left", padx=(10, 3))
        self.img_ai_mat_erode_entry = ctk.CTkEntry(matrow, width=46)
        self.img_ai_mat_erode_entry.insert(0, "10")
        self.img_ai_mat_erode_entry.pack(side="left")
        uprow = ctk.CTkFrame(self.image_frame, fg_color="transparent")
        uprow.grid(row=6, column=0, columnspan=8, padx=12, pady=(0, 8), sticky="w")
        self.img_upscale_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(uprow, text="Upscale (Real-ESRGAN)",
                        variable=self.img_upscale_var).pack(side="left")
        ctk.CTkLabel(uprow, text="×").pack(side="left", padx=(12, 2))
        self.img_upscale_factor = ctk.CTkOptionMenu(uprow, values=["2", "3", "4"],
                                                    width=64)
        self.img_upscale_factor.set("4")
        self.img_upscale_factor.pack(side="left")
        ctk.CTkLabel(uprow, text="mode").pack(side="left", padx=(12, 4))
        self.img_upscale_mode = ctk.CTkOptionMenu(uprow, values=["Photo", "Illustration"],
                                                  width=120)
        self.img_upscale_mode.set("Photo")
        self.img_upscale_mode.pack(side="left")
        self.image_frame.grid_remove()
        self._update_panels(self.target_menu.get())

        # Console output (resizable)
        log_outer = ctk.CTkFrame(c, fg_color="transparent", height=self._heights["log"])
        log_outer.grid(row=6, column=0, padx=20, pady=(8, 0), sticky="ew")
        log_outer.grid_propagate(False)
        log_outer.grid_columnconfigure(0, weight=1)
        log_outer.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(log_outer)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")
        self._containers["log"] = log_outer
        self._make_grip(c, "log", log_outer, min_h=80).grid(
            row=7, column=0, padx=20, pady=(3, 8), sticky="ew")
        self._setup_qol()

    def _setup_qol(self):
        """Whole-window drop overlay, inner-first scrolling, and drag-autoscroll."""
        self._lb_scan_id = None
        self._lb_scan_dir = 0
        self._drop_hide_id = None

        # --- 1) whole-window drag & drop with a "drop here" overlay ---
        self._drop_overlay = ctk.CTkFrame(self, fg_color=("gray80", "gray12"))
        card = ctk.CTkFrame(self._drop_overlay, fg_color=("gray70", "gray22"),
                            corner_radius=20, border_width=3,
                            border_color=("gray45", "gray55"))
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.66, relheight=0.46)
        ctk.CTkLabel(card, text="⬇\n\nDrop folder here",
                     font=ctk.CTkFont(size=26, weight="bold"),
                     justify="center").place(relx=0.5, rely=0.5, anchor="center")
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<DropEnter>>", self._on_drag_enter)
        self.dnd_bind("<<DropPosition>>", self._on_drag_position)
        self.dnd_bind("<<DropLeave>>", self._on_drag_leave)
        self.dnd_bind("<<Drop>>", self._on_drag_drop)

        # --- 2) inner-first mouse-wheel scrolling for the file list and the log ---
        for w in (self.file_listbox, getattr(self.log_box, "_textbox", None)):
            if w is None:
                continue
            w.bind("<MouseWheel>", lambda e, t=w: self._inner_wheel(e, t))
            w.bind("<Button-4>", lambda e, t=w: self._inner_wheel(e, t))
            w.bind("<Button-5>", lambda e, t=w: self._inner_wheel(e, t))

        # --- 3) auto-scroll while drag-/shift-selecting in the file list ---
        self.file_listbox.bind("<B1-Motion>", self._lb_drag)
        self.file_listbox.bind("<Shift-B1-Motion>", self._lb_drag)
        self.file_listbox.bind("<ButtonRelease-1>", self._lb_release, add="+")

    # ---- whole-window drop overlay ----
    def _show_drop_overlay(self):
        self._drop_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._drop_overlay.lift()

    def _hide_drop_overlay(self):
        self._drop_hide_id = None
        # lower() first — restacking repaints reliably during a drag (unlike a bare
        # place_forget, whose repaint macOS defers until the drag ends), then unmap.
        self._drop_overlay.lower()
        self._drop_overlay.place_forget()
        self.update_idletasks()

    def _pointer_in_window(self, event):
        try:
            x, y = event.x_root, event.y_root
            wx, wy = self.winfo_rootx(), self.winfo_rooty()
            return (wx <= x < wx + self.winfo_width()
                    and wy <= y < wy + self.winfo_height())
        except Exception:
            return True

    def _cancel_drop_watchdog(self):
        if self._drop_hide_id:
            try:
                self.after_cancel(self._drop_hide_id)
            except Exception:
                pass
            self._drop_hide_id = None

    def _arm_drop_watchdog(self):
        # backup: if drag events stop arriving entirely, hide anyway.
        self._cancel_drop_watchdog()
        self._drop_hide_id = self.after(250, self._hide_drop_overlay)

    def _on_drag_enter(self, event):
        self._show_drop_overlay()
        self._arm_drop_watchdog()
        return event.action

    def _on_drag_position(self, event):
        # hide as soon as the dragged item is off the window, without waiting for
        # a (sometimes-missing) DropLeave event.
        if self._pointer_in_window(event):
            self._show_drop_overlay()
            self._arm_drop_watchdog()
        else:
            self._cancel_drop_watchdog()
            self._hide_drop_overlay()
        return event.action

    def _on_drag_leave(self, event):
        self._cancel_drop_watchdog()
        self._hide_drop_overlay()

    def _on_drag_drop(self, event):
        self._cancel_drop_watchdog()
        self._hide_drop_overlay()
        self.handle_drop(event)
        return event.action

    # ---- inner-first scrolling ----
    def _wheel_units(self, event):
        n = getattr(event, "num", None)
        if n == 4:
            return -1
        if n == 5:
            return 1
        d = getattr(event, "delta", 0)
        if d == 0:
            return 0
        if abs(d) >= 120:      # Windows: multiples of 120
            return -int(d / 120)
        return -1 if d > 0 else 1   # macOS: small integer deltas

    def _outer_scroll(self, units):
        cv = getattr(self.scroll, "_parent_canvas", None)
        if cv is not None and units:
            cv.yview_scroll(units, "units")

    def _inner_wheel(self, event, target):
        units = self._wheel_units(event)
        if not units:
            return "break"
        try:
            top, bottom = target.yview()
        except Exception:
            self._outer_scroll(units)
            return "break"
        # scroll the inner widget until it hits an edge, then hand off to the window
        if units < 0 and top <= 0.0001:
            self._outer_scroll(units)
        elif units > 0 and bottom >= 0.9999:
            self._outer_scroll(units)
        else:
            target.yview_scroll(units, "units")
        return "break"

    # ---- drag-select auto-scroll in the file list ----
    def _lb_extend_to(self, idx):
        lb = self.file_listbox
        try:
            anchor = int(lb.index("anchor"))
        except Exception:
            anchor = idx
        lo, hi = sorted((anchor, idx))
        lb.selection_clear(0, "end")
        lb.selection_set(lo, hi)
        lb.activate(idx)
        lb.see(idx)
        self._update_counts()

    def _lb_autoscan(self):
        if self._lb_scan_dir == 0:
            return
        lb = self.file_listbox
        lb.yview_scroll(self._lb_scan_dir, "units")
        h = max(1, lb.winfo_height())
        idx = lb.nearest(0 if self._lb_scan_dir < 0 else h - 1)
        self._lb_extend_to(idx)
        self._lb_scan_id = self.after(60, self._lb_autoscan)

    def _lb_start_scan(self, direction):
        if self._lb_scan_dir == direction and self._lb_scan_id:
            return
        self._lb_stop_scan()
        self._lb_scan_dir = direction
        self._lb_autoscan()

    def _lb_stop_scan(self):
        self._lb_scan_dir = 0
        if self._lb_scan_id:
            try:
                self.after_cancel(self._lb_scan_id)
            except Exception:
                pass
            self._lb_scan_id = None

    def _lb_drag(self, event):
        lb = self.file_listbox
        h = lb.winfo_height()
        if event.y < 0:
            self._lb_start_scan(-1)
        elif event.y >= h:
            self._lb_start_scan(1)
        else:
            self._lb_stop_scan()
            self._lb_extend_to(lb.nearest(event.y))
        return "break"

    def _lb_release(self, event):
        self._lb_stop_scan()

    def _entry(self, parent, default, width, row, col):
        e = ctk.CTkEntry(parent, width=width)
        e.insert(0, default)
        e.grid(row=row, column=col, pady=8)
        return e

    def _set_entry(self, entry, value):
        entry.delete(0, "end")
        entry.insert(0, str(value))

    def _bg_color_toggle(self):
        if self.img_bg_color_var.get():
            self.img_bg_ai_var.set(False)

    def _bg_ai_toggle(self):
        if self.img_bg_ai_var.get():
            self.img_bg_color_var.set(False)

    def _pick_bg_color(self):
        from tkinter import colorchooser
        cur = self.img_bg_color_entry.get().strip() or "#FFFFFF"
        try:
            res = colorchooser.askcolor(color=cur, title="Pick background color")
        except Exception:
            res = None
        if res and res[1]:
            self._set_entry(self.img_bg_color_entry, res[1].upper())

    def _pick_from_image(self):
        """In-app eyedropper: show the selected image and click to grab a color."""
        sel = self.file_listbox.curselection()
        if not sel:
            self.log("Select an image in the list first, then 'From image'.")
            return
        path = self.displayed_files[sel[0]]
        if path.suffix.lower().lstrip(".") not in IMAGE_EXTS:
            self.log(f"{path.name} isn't an image — pick from a png/jpg/webp.")
            return
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.log("Pillow is needed for the eyedropper — run: pip3 install pillow")
            return
        try:
            Image.MAX_IMAGE_PIXELS = None
            img = Image.open(path).convert("RGB")
        except Exception as e:
            self.log(f"Couldn't open {path.name}: {e}")
            return

        ow, oh = img.size
        scale = min(1000 / ow, 760 / oh, 1.0)
        dw, dh = max(1, int(ow * scale)), max(1, int(oh * scale))
        disp = img.resize((dw, dh), Image.LANCZOS) if scale < 1.0 else img

        win = tk.Toplevel(self)
        win.title(f"Pick color — {path.name}")
        photo = ImageTk.PhotoImage(disp, master=win)
        canvas = tk.Canvas(win, width=dw, height=dh, highlightthickness=0,
                           cursor="crosshair")
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas._photo = photo  # keep a reference so it isn't garbage-collected
        info = tk.Label(win, text="Move over the image, click to grab the color")
        info.pack(pady=6)

        def at(e):
            x = min(ow - 1, max(0, int(e.x / scale)))
            y = min(oh - 1, max(0, int(e.y / scale)))
            return x, y, img.getpixel((x, y))[:3]

        def on_move(e):
            x, y, (r, g, b) = at(e)
            info.configure(text=f"#{r:02X}{g:02X}{b:02X}   at ({x}, {y})")

        def on_click(e):
            x, y, (r, g, b) = at(e)
            hexv = f"#{r:02X}{g:02X}{b:02X}"
            self._set_entry(self.img_bg_color_entry, hexv)
            self.img_bg_color_var.set(True)
            self.img_bg_ai_var.set(False)
            self.log(f"Picked {hexv} from {path.name}")
            win.destroy()

        canvas.bind("<Motion>", on_move)
        canvas.bind("<Button-1>", on_click)
        win.after(100, win.lift)

    def _style_listbox(self):
        dark = ctk.get_appearance_mode() == "Dark"
        self.file_listbox.configure(
            bg="#2b2b2b" if dark else "#ffffff",
            fg="#dce4ee" if dark else "#1a1a1a",
            selectbackground="#1f6aa5" if dark else "#3a7ebf",
            selectforeground="#ffffff",
            highlightbackground="#565b5e" if dark else "#979da2",
            highlightcolor="#1f6aa5" if dark else "#3a7ebf",
        )

    # -- Resizable panels --------------------------------------------------

    def _make_grip(self, parent, key, container, min_h):
        grip = ctk.CTkFrame(parent, height=8, corner_radius=4,
                            fg_color=("gray70", "gray35"), cursor="sb_v_double_arrow")
        grip.bind("<Button-1>", lambda e: self._grip_press(e, key))
        grip.bind("<B1-Motion>", lambda e: self._grip_drag(e, container, key, min_h))
        return grip

    def _grip_press(self, event, key):
        self._drag_y0 = event.y_root
        self._drag_h0 = self._heights[key]

    def _grip_drag(self, event, container, key, min_h):
        new_h = max(min_h, self._drag_h0 + (event.y_root - self._drag_y0))
        self._heights[key] = new_h
        container.configure(height=new_h)
        self._update_reset_visibility()

    def _update_reset_visibility(self):
        bigger = any(self._heights[k] > self._default_heights[k] + 2
                     for k in self._heights)
        if bigger:
            self.reset_btn.place(relx=1.0, y=8, x=-18, anchor="ne")
        else:
            self.reset_btn.place_forget()

    def reset_layout(self):
        for key, container in self._containers.items():
            self._heights[key] = self._default_heights[key]
            container.configure(height=self._heights[key])
        self._update_reset_visibility()
        try:
            self.scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def _update_panels(self, target):
        is_video = target in VIDEO_FORMATS
        is_audio = target in AUDIO_FORMATS
        is_image = target in IMAGE_FORMATS
        (self.video_frame.grid() if is_video else self.video_frame.grid_remove())
        (self.audio_frame.grid() if (is_video or is_audio) else self.audio_frame.grid_remove())
        (self.image_frame.grid() if is_image else self.image_frame.grid_remove())

    def on_target_change(self, target):
        self._update_panels(target)
        if target in VIDEO_FORMATS:
            self._set_entry(self.crf_entry, DEFAULT_CRF.get(target, "23"))

    # -- Logging (main thread only) ----------------------------------------

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_status(self, text):
        self.status.configure(text=text)

    def _set_progress(self, done, total):
        frac = done / total if total else 0
        self.progress.set(frac)
        self.pct_label.configure(text=f"{int(frac * 100)}%")

    # -- Folder handling ---------------------------------------------------

    def handle_drop(self, event):
        for p in self.tk.splitlist(event.data):
            if os.path.isdir(p):
                self.load_folder(p)
                return
        self.log("That wasn't a folder. Drop a folder, please.")

    def browse_folder(self):
        path = ctk.filedialog.askdirectory()
        if path:
            self.load_folder(path)

    def load_folder(self, path):
        self.folder = Path(path)
        self.drop_label.configure(text=f"{self.folder.name}\n{self.folder}")
        self.scan_folder()

    def scan_folder(self):
        self.found_exts = {}
        files = []
        for root, dirs, fs in os.walk(self.folder):
            dirs[:] = [d for d in dirs if not d.endswith(OUTPUT_SUFFIX)]
            for f in fs:
                ext = Path(f).suffix.lower().lstrip(".")
                if ext in MEDIA_EXTS:
                    self.found_exts[ext] = self.found_exts.get(ext, 0) + 1
                    files.append(Path(root) / f)
        self.all_media_files = sorted(files, key=lambda p: str(p).lower())
        self.filter_menu.configure(values=["All"] + [f".{e}" for e in sorted(self.found_exts)])
        self.filter_menu.set("All")
        self.apply_filter()
        if self.all_media_files:
            self.convert_btn.configure(state="normal")
            self.set_status(f"Found {len(self.all_media_files)} media file(s).")
        else:
            self.convert_btn.configure(state="disabled")
            self.set_status("No media files found.")

    # -- File list ---------------------------------------------------------

    def apply_filter(self, *_):
        choice = self.filter_menu.get()
        if choice == "All":
            self.displayed_files = list(self.all_media_files)
        else:
            ext = choice.lstrip(".")
            self.displayed_files = [f for f in self.all_media_files
                                    if f.suffix.lower().lstrip(".") == ext]
        self.file_listbox.delete(0, "end")
        for f in self.displayed_files:
            self.file_listbox.insert("end", str(f.relative_to(self.folder)))
        self.select_all_var.set(False)
        self._update_counts()

    def toggle_select_all(self):
        if self.select_all_var.get():
            self.file_listbox.selection_set(0, "end")
        else:
            self.file_listbox.selection_clear(0, "end")
        self._update_counts()

    def select_none(self):
        self.file_listbox.selection_clear(0, "end")
        self.select_all_var.set(False)
        self._update_counts()

    def _open_selected(self, event):
        """Double-click: open the file under the cursor in its default app."""
        idx = self.file_listbox.nearest(event.y)
        if 0 <= idx < len(self.displayed_files):
            self._open_file(self.displayed_files[idx])

    def _open_file(self, path):
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # noqa
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.log(f"Opened {path.name}")
        except Exception as e:
            self.log(f"Couldn't open {path.name}: {e}")

    def _update_counts(self, *_):
        shown = self.file_listbox.size()
        selected = len(self.file_listbox.curselection())
        self.count_label.configure(text=f"{shown} shown · {selected} selected")
        if shown and selected == shown:
            self.select_all_var.set(True)
        elif selected == 0:
            self.select_all_var.set(False)

    # -- Presets -----------------------------------------------------------

    def read_form(self):
        return {
            "target": self.target_menu.get(),
            "resize": self.resize_var.get(),
            "width": int(self.width_entry.get()),
            "height": int(self.height_entry.get()),
            "resize_op": self.resize_op.get(),
            "crf": int(self.crf_entry.get()),
            "normalize": self.normalize_var.get(),
            "target_db": float(self.target_db_entry.get()),
            "trim": self.trim_var.get(),
            "trim_start": float(self.trim_start_entry.get()),
            "trim_end": float(self.trim_end_entry.get()),
            "bitrate": self.bitrate_entry.get().strip(),
            "samplerate": self.samplerate_menu.get(),
            "channels": self.channels_menu.get(),
            "img_resize": self.img_resize_var.get(),
            "img_width": int(self.img_width_entry.get()),
            "img_height": int(self.img_height_entry.get()),
            "img_op": self.img_op.get(),
            "img_quality": int(self.img_quality_entry.get()),
            "img_trim": self.img_trim_var.get(),
            "img_trim_l": int(self.img_trim_entries["l"].get()),
            "img_trim_r": int(self.img_trim_entries["r"].get()),
            "img_trim_t": int(self.img_trim_entries["t"].get()),
            "img_trim_b": int(self.img_trim_entries["b"].get()),
            "img_bg_mode": ("ai" if self.img_bg_ai_var.get()
                            else "color" if self.img_bg_color_var.get() else "off"),
            "img_bg_color": self.img_bg_color_entry.get().strip(),
            "img_bg_similarity": float(self.img_bg_sim_entry.get()),
            "img_bg_blend": float(self.img_bg_blend_entry.get()),
            "img_ai_erode": int(self.img_ai_erode_entry.get()),
            "img_ai_feather": float(self.img_ai_feather_entry.get()),
            "img_ai_matting": self.img_ai_matting_var.get(),
            "img_ai_fg": int(self.img_ai_fg_entry.get()),
            "img_ai_bg": int(self.img_ai_bg_entry.get()),
            "img_ai_mat_erode": int(self.img_ai_mat_erode_entry.get()),
            "img_upscale": self.img_upscale_var.get(),
            "img_upscale_factor": self.img_upscale_factor.get(),
            "img_upscale_mode": self.img_upscale_mode.get(),
        }

    def apply_preset(self, name):
        p = self.presets.get(name)
        if not p:
            return
        target = p.get("target", "webm")
        self.target_menu.set(target)
        self._update_panels(target)
        self.resize_var.set(p.get("resize", False))
        self._set_entry(self.width_entry, p.get("width", 1920))
        self._set_entry(self.height_entry, p.get("height", 1080))
        self.resize_op.set(p.get("resize_op", "Crop"))
        self._set_entry(self.crf_entry, p.get("crf", 30))
        self.normalize_var.set(p.get("normalize", False))
        self._set_entry(self.target_db_entry, p.get("target_db", 0))
        self.trim_var.set(p.get("trim", False))
        self._set_entry(self.trim_start_entry, p.get("trim_start", 0))
        self._set_entry(self.trim_end_entry, p.get("trim_end", 0))
        self._set_entry(self.bitrate_entry, p.get("bitrate", "128k"))
        self.samplerate_menu.set(p.get("samplerate", "Keep"))
        self.channels_menu.set(p.get("channels", "Keep"))
        self.img_resize_var.set(p.get("img_resize", False))
        self._set_entry(self.img_width_entry, p.get("img_width", 2500))
        self._set_entry(self.img_height_entry, p.get("img_height", 2500))
        self.img_op.set(p.get("img_op", "Stretch"))
        self._set_entry(self.img_quality_entry, p.get("img_quality", 90))
        self.img_trim_var.set(p.get("img_trim", False))
        for key in ("l", "r", "t", "b"):
            self._set_entry(self.img_trim_entries[key], p.get(f"img_trim_{key}", 0))
        mode = p.get("img_bg_mode", "off")
        self.img_bg_color_var.set(mode == "color")
        self.img_bg_ai_var.set(mode == "ai")
        self._set_entry(self.img_bg_color_entry, p.get("img_bg_color", "#FFFFFF"))
        self._set_entry(self.img_bg_sim_entry, p.get("img_bg_similarity", 0.15))
        self._set_entry(self.img_bg_blend_entry, p.get("img_bg_blend", 0.0))
        self._set_entry(self.img_ai_erode_entry, p.get("img_ai_erode", 1))
        self._set_entry(self.img_ai_feather_entry, p.get("img_ai_feather", 0.5))
        self.img_ai_matting_var.set(p.get("img_ai_matting", False))
        self._set_entry(self.img_ai_fg_entry, p.get("img_ai_fg", 240))
        self._set_entry(self.img_ai_bg_entry, p.get("img_ai_bg", 10))
        self._set_entry(self.img_ai_mat_erode_entry, p.get("img_ai_mat_erode", 10))
        self.img_upscale_var.set(p.get("img_upscale", False))
        self.img_upscale_factor.set(str(p.get("img_upscale_factor", "4")))
        self.img_upscale_mode.set(p.get("img_upscale_mode", "Photo"))
        self.log(f"Loaded preset: {name}")

    def save_preset(self):
        try:
            form = self.read_form()
        except ValueError:
            self.log("Fix the numeric fields before saving a preset.")
            return
        dialog = ctk.CTkInputDialog(text="Name for this preset:", title="Save preset")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        self.presets[name] = form
        if not self._write_presets(self.presets):
            self.log("Couldn't write the presets file.")
        self.preset_menu.configure(values=list(self.presets.keys()))
        self.preset_menu.set(name)
        self.log(f"Saved preset: {name}")

    def delete_preset(self):
        name = self.preset_menu.get()
        if name not in self.presets:
            return
        del self.presets[name]
        self._write_presets(self.presets)
        names = list(self.presets.keys()) or ["(none)"]
        self.preset_menu.configure(values=names)
        self.preset_menu.set(names[0])
        self.log(f"Deleted preset: {name}")

    def _refresh_save_state(self):
        """Green when settings differ from every preset; grey+disabled when matched."""
        matches = False
        try:
            matches = self.read_form() in self.presets.values()
        except ValueError:
            matches = False
        if matches:
            self.save_btn.configure(fg_color=GREY, hover_color=GREY, state="disabled")
        else:
            self.save_btn.configure(fg_color=GREEN, hover_color=GREEN_HOVER, state="normal")
        self.after(400, self._refresh_save_state)

    # -- App launcher (created in the app's own folder, not the Desktop) ----

    def create_shortcut(self):
        build_launcher(self.log)

    # -- Self-update (git pull + restart) ----------------------------------

    def update_app(self):
        repo = str(_app_dir())
        if not (Path(repo) / ".git").exists():
            self.log("No git connection here yet — run the installer once "
                     "(install.sh / install.bat); it enables updates.")
            return
        self.log("Checking for updates…")
        self.update_btn.configure(state="disabled")
        try:
            r = subprocess.run(["git", "-C", repo, "pull", "--ff-only"],
                               capture_output=True, text=True, creationflags=NO_WINDOW)
        except FileNotFoundError:
            self.log("git isn't installed — can't update.")
            self.update_btn.configure(state="normal")
            return
        out = (r.stdout + "\n" + r.stderr).strip()
        self.log(out or "(no output)")
        self.update_btn.configure(state="normal")
        if r.returncode != 0:
            self.log("Update failed — see the message above.")
            return
        if "up to date" in out.lower():
            self.log("Already on the latest version.")
            return
        self.log("Updated — restarting…")
        self.after(700, self._restart_app)

    def _restart_app(self):
        try:
            self._shutdown_ai_worker()
        except Exception:
            pass
        script = os.path.abspath(sys.argv[0])
        try:
            os.execv(sys.executable, [sys.executable, script])
        except Exception as e:
            self.log(f"Couldn't restart automatically ({e}). Please reopen the app.")

    # -- Conversion --------------------------------------------------------

    def start_conversion(self):
        target = self.target_menu.get()
        try:
            form = self.read_form()
        except ValueError:
            self.log("Check your numeric fields (resolution / CRF / dB must be numbers).")
            return
        opts = dict(form)
        opts["samplerate"] = None if form["samplerate"] == "Keep" else form["samplerate"]
        opts["channels"] = None if form["channels"] == "Keep" else form["channels"]

        files = [self.displayed_files[i] for i in self.file_listbox.curselection()]
        if not files:
            self.log("Select at least one file (tick Select all, or click / shift+click).")
            return

        self.cancel_event.clear()
        self.progress.set(0)
        self.pct_label.configure(text="0%")
        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        threading.Thread(target=self._run, args=(files, target, opts), daemon=True).start()

    def cancel_conversion(self):
        if not messagebox.askyesno("Cancel conversion",
                                   "Stop the current conversion batch?"):
            return
        self.cancel_event.set()
        if self.current_proc and self.current_proc.poll() is None:
            try:
                self.current_proc.terminate()
            except Exception:
                pass
        self.log("Cancelling…")

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
        return lines[-1] if lines else "unknown error"

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
        """Pre-download the u2net model with visible progress in the app console.
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
        self.after(0, self.log,
                   "   first AI run: downloading model (~170 MB, one-time)…")
        tmp = model_path.with_name("u2net.onnx.part")
        last = [-5]

        def hook(blocks, bsize, total):
            if total > 0:
                pct = min(100, int(blocks * bsize * 100 / total))
                if pct >= last[0] + 5:
                    last[0] = pct
                    self.after(0, self.log, f"      model download: {pct}%")

        try:
            urllib.request.urlretrieve(url, tmp, reporthook=hook)
            tmp.replace(model_path)
            self.after(0, self.log, "   model downloaded.")
        except Exception as e:
            try:
                tmp.unlink()
            except Exception:
                pass
            self.after(0, self.log,
                       f"   (couldn't pre-download: {e}; letting rembg handle it)")

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

    def _shutdown_ai_worker(self):
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
        self.after(0, self.log,
                   "   starting AI engine in a separate process (one-time)…")
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
            return f"couldn't start the AI process: {e}"

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
            self._shutdown_ai_worker()
            return ("AI engine didn't start: "
                    + (tail or "no response within 5 min (onnxruntime/environment issue)."))
        if not msg.get("ready"):
            tail = self._ai_stderr_tail() or " | ".join(noise[-4:])
            self._shutdown_ai_worker()
            return (f"AI engine could not start: {msg.get('err', 'unknown')}"
                    + (f" | {tail}" if tail else ""))
        self.after(0, self.log, "   AI engine ready.")
        return None

    def _upscale_image(self, src, out_png, factor, mode):
        """Upscale an image with realesrgan-ncnn-vulkan. Returns None or an error."""
        exe = resolve_realesrgan(load_config().get("realesrgan_path"))
        if not exe:
            return ("Real-ESRGAN not found — place the realesrgan-ncnn-vulkan folder "
                    "under 'realesrgan/' next to the app, or install it so it's on PATH.")
        if not getattr(self, "_upscaler_prepared", False):
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
            return ("Real-ESRGAN binary isn't allowed to run. Fix it once in Terminal:\n"
                    f'      chmod +x "{exe}"\n'
                    f'      xattr -dr com.apple.quarantine "{Path(exe).parent}"')
        except Exception as e:
            return f"couldn't run Real-ESRGAN: {e}"
        if rc != 0 or not Path(out_png).exists():
            tail = " ".join((err or "").strip().splitlines()[-2:])[:200]
            return f"Real-ESRGAN failed: {tail or 'unknown error'}"
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
            self._shutdown_ai_worker()
            return f"AI engine not reachable: {e}"
        msg, noise = self._ai_read_json(600)
        if msg == "__CANCEL__":
            return "cancelled"
        if msg is None:
            tail = self._ai_stderr_tail() or " | ".join(noise[-4:])
            self._shutdown_ai_worker()
            return "AI engine stopped responding" + (f": {tail}" if tail else ".")
        if msg.get("ok"):
            return None
        return f"AI background removal failed: {msg.get('err', 'unknown')}"

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

    def _run(self, files, target, opts):
        out_root = self.folder / f"{target}{OUTPUT_SUFFIX}"
        total = len(files)
        ok = 0
        for i, src in enumerate(files, 1):
            if self.cancel_event.is_set():
                break
            rel = src.relative_to(self.folder)
            dst = out_root / rel.with_suffix(f".{target}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = self._unique_path(dst)  # never overwrite: name, name01, name02, …

            gain_db = None
            if opts["normalize"] and target not in IMAGE_FORMATS:
                self.after(0, self.set_status, f"Analyzing {i}/{total}: {src.name}")
                max_vol = self.detect_max_volume(src)
                if self.cancel_event.is_set():
                    break
                if max_vol is None:
                    self.after(0, self.log, f"   {src.name}: couldn't read level, skipping normalize")
                else:
                    gain_db = round(opts["target_db"] - max_vol, 1)

            duration = None
            if opts["trim"] and target in AUDIO_FORMATS:
                duration = self.detect_duration(src)
                if self.cancel_event.is_set():
                    break
                if duration is None:
                    self.after(0, self.log,
                               f"   {src.name}: couldn't read duration, skipping trim")
                else:
                    keep = duration - opts["trim_start"] - opts["trim_end"]
                    if keep <= 0:
                        self.after(0, self.log,
                                   f"   SKIPPED {rel}: trim ({opts['trim_start']}s + "
                                   f"{opts['trim_end']}s) leaves nothing of "
                                   f"{duration:.2f}s")
                        self.after(0, self._set_progress, i, total)
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
                    self.after(0, self.log,
                               f"   {src.name}: couldn't read size, trimming without "
                               f"rescale (output will be slightly smaller)")
                elif (opts["img_trim_l"] + opts["img_trim_r"] >= dims[0]
                      or opts["img_trim_t"] + opts["img_trim_b"] >= dims[1]):
                    self.after(0, self.log,
                               f"   SKIPPED {rel}: trim removes the whole "
                               f"{dims[0]}x{dims[1]} image")
                    self.after(0, self._set_progress, i, total)
                    continue

            has_alpha = False
            if target == "webm":
                has_alpha = self.detect_has_alpha(src)
                if self.cancel_event.is_set():
                    break

            self.after(0, self.set_status, f"Converting {i}/{total}: {src.name}")
            self.after(0, self.log, f"-> {rel}")
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
                self.after(0, self.set_status,
                           f"Upscaling {i}/{total}: {src.name}")
                self.after(0, self.log,
                           f"   upscaling ×{opts.get('img_upscale_factor', '4')} "
                           f"({opts.get('img_upscale_mode', 'Photo')})…")
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
                self.after(0, self.set_status,
                           f"AI background removal {i}/{total}: {src.name}")
                self.after(0, self.log, "   removing background with AI…")
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
                self.after(0, self.log, f"   stopped during {rel} (that file may be incomplete)")
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
                    rc, err = 1, "Pillow not installed — run: pip3 install pillow"
                except Exception as e:
                    rc, err = 1, f"webp encode failed: {e}"
            if rc == 0:
                ok += 1
            else:
                self.after(0, self.log, f"   FAILED: {self._ffmpeg_error(err)}")
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
            self.after(0, self._set_progress, i, total)

        if self.cancel_event.is_set():
            self.after(0, self.set_status, f"Cancelled — {ok}/{total} done before stopping.")
            self.after(0, self.log, f"Cancelled after {ok}/{total}.")
        else:
            self.after(0, self.set_status, f"Done. {ok}/{total} converted.")
            self.after(0, self.log, f"Finished: {ok}/{total} succeeded. Output in {out_root}")
        self.after(0, self._conversion_finished)

    def _conversion_finished(self):
        self.convert_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.cancel_event.clear()
        self.current_proc = None


if __name__ == "__main__":
    if any(a in ("--make-app", "--make-launcher") for a in sys.argv[1:]):
        build_launcher(print)
    else:
        ConverterApp().mainloop()