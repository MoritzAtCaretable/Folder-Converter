"""
Folder Converter — drag-and-drop batch media converter built on FFMPEG.

Workflow:
  1. Drag a folder onto the window (or "Ordner wählen" / "Durchsuchen…").
  2. Filter by format if you like, then tick files — click toggles, shift+click
     ticks a range, dragging over rows ticks them all; the header box ticks all.
  3. Pick a target and adjust parameters, or load a saved preset.
  4. "Konvertierung starten" (lower-right); the red ■ beside it cancels after
     confirmation.

The window is native (WKWebView on macOS, WebView2 on Windows) and shows the
Caretable-design UI from webui/. The conversion logic lives in converter_core.py,
the bridge between both in webui_api.py.

Presets describe the OUTPUT recipe only; the input is whatever you select.
Stored in ~/.folder_converter/presets.json

Requirements: install.sh / install.bat set everything up in .venv/ (see
requirements.txt), and ffmpeg must be on your PATH.
Run:  python Converter.py            (--make-app builds the launcher)
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from converter_core import NO_WINDOW, _app_dir, asset_path

APP_TITLE = "Folder Converter"

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


# ---------------------------------------------------------------------------
# Start-up: make sure pywebview is there
# ---------------------------------------------------------------------------
# Older installs run on a global Python without pywebview. After "Update
# suchen" they restart straight into this file — so instead of failing
# silently, switch to the folder's .venv if the installer already built one,
# or offer to run the installer.

def _venv_python():
    base = _app_dir() / ".venv"
    for cand in (base / "bin" / "python", base / "Scripts" / "pythonw.exe",
                 base / "Scripts" / "python.exe"):
        if cand.exists():
            return str(cand)
    return None


def _in_app_venv():
    try:
        return Path(sys.prefix).resolve() == (_app_dir() / ".venv").resolve()
    except Exception:
        return False


def _ask(title, text):
    """Yes/no question without the web UI (tkinter, else AppleScript)."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        answer = messagebox.askyesno(title, text, parent=root)
        root.destroy()
        return answer
    except Exception:
        pass
    if sys.platform == "darwin":
        quote = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        r = subprocess.run(
            ["osascript", "-e",
             f'display dialog "{quote(text)}" with title "{quote(title)}" '
             'buttons {"Abbrechen", "Installer starten"} default button 2'],
            capture_output=True, text=True)
        return r.returncode == 0 and "Installer starten" in r.stdout
    print(f"{title}: {text}", file=sys.stderr)
    return False


def _run_installer():
    folder = str(_app_dir())
    if sys.platform == "darwin":
        cmd = f"cd {shlex.quote(folder)} && bash install.sh"
        cmd = cmd.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", 'tell application "Terminal"',
                        "-e", "activate", "-e", f'do script "{cmd}"', "-e", "end tell"])
    elif os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", "install.bat"], cwd=folder)
    else:
        print(f"Bitte im Ordner {folder} den Installer ausführen.", file=sys.stderr)


def _ensure_webview():
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        pass
    venv = _venv_python()
    if venv and not _in_app_venv():
        probe = subprocess.run([venv, "-c", "import webview"], capture_output=True,
                               creationflags=NO_WINDOW)
        if probe.returncode == 0:
            os.execv(venv, [venv, os.path.abspath(sys.argv[0])] + sys.argv[1:])
    if _ask(APP_TITLE,
            "Die neue Oberfläche braucht eine zusätzliche Komponente (pywebview).\n\n"
            "Soll der Installer jetzt einmal laufen? Er richtet alles im App-Ordner "
            "ein. Wenn er fertig ist, die App einfach wieder öffnen."):
        _run_installer()
    return False


def _set_dock_icon():
    """Without this, macOS shows the Python rocket in the Dock."""
    if sys.platform != "darwin":
        return
    try:
        import AppKit
        icon = AppKit.NSImage.alloc().initWithContentsOfFile_(str(asset_path("icon.png")))
        if icon is not None:
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(icon)
    except Exception:
        pass


def main():
    if not _ensure_webview():
        return 1
    import webview
    from webui_api import Api

    index = asset_path("webui") / "index.html"
    if not index.exists():
        print(f"Oberfläche nicht gefunden: {index}", file=sys.stderr)
        return 1

    width, height = 1440, 940
    try:
        screen = webview.screens[0]
        width, height = min(width, screen.width - 40), min(height, screen.height - 80)
    except Exception:
        pass

    api = Api()
    window = webview.create_window(
        APP_TITLE,
        url=str(index),
        js_api=api,
        width=width,
        height=height,
        min_size=(960, 640),
        background_color="#F5F7FC",
        text_select=False,
    )
    api._attach(window)
    _set_dock_icon()
    try:
        webview.start()
    finally:
        api._shutdown()
    return 0


if __name__ == "__main__":
    if any(a in ("--make-app", "--make-launcher") for a in sys.argv[1:]):
        build_launcher(print)
    else:
        sys.exit(main())
