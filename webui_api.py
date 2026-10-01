"""
webui_api — Brücke zwischen der HTML-Oberfläche (webui/) und der
Konvertier-Logik in converter_core.py.

Das Frontend ruft die öffentlichen Methoden über window.pywebview.api auf.
Jede Methode meldet Fehler als {"ok": False, "error": "…"}, statt eine
Ausnahme ins Frontend zu werfen — ein Fehler darf die Oberfläche nicht
blockieren. Der Stapellauf läuft in einem eigenen Thread; Protokoll, Status
und Fortschritt holt sich die Oberfläche per poll().

Interner Zustand beginnt mit "_": pywebview reicht nur Attribute ohne
Unterstrich ans Frontend weiter.
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from functools import wraps
from pathlib import Path

import converter_core as core


def _guard(fn):
    """Fehler nie ins Frontend werfen — immer als Ergebnis melden."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and "ok" not in result:
                result = {"ok": True, **result}
            return result if result is not None else {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"{e}"}
    return wrapper


class Api:
    def __init__(self) -> None:
        self._window = None
        self._lock = threading.RLock()
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._engine = core.ConversionEngine(log=self._log, status=self._set_status,
                                             progress=self._set_progress)
        self._folder: Path | None = None
        self._files: dict[str, Path] = {}      # relativer Pfad -> Datei, sortiert
        self._presets = core.load_presets()
        self._running = False
        self._status = {"title": "", "text": ""}
        self._progress = {"done": 0, "total": 0}
        self._outcome = {"id": "", "state": "idle"}
        self._picker = None                    # (Pfad, PIL-Bild) der Pipette

    # ------------------------------------------------------------- Fenster

    def _attach(self, window) -> None:
        """Hängt das Fenster an und fängt Ordner ab, die darauf gezogen werden."""
        self._window = window
        window.events.loaded += self._bind_drop

    def _bind_drop(self) -> None:
        from webview.dom import DOMEventHandler
        self._window.dom.document.events.drop += DOMEventHandler(self._on_drop, True, True)

    def _on_drop(self, event) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        folder = next((p for p in paths if os.path.isdir(p)), None)
        self._window.evaluate_js(f"window.onFolderDrop({json.dumps(folder)})")

    def _shutdown(self) -> None:
        """Beim Schließen: laufenden Stapel stoppen, KI-Prozess beenden."""
        if self._running:
            self._engine.cancel()
        self._engine.shutdown_ai_worker()

    # ------------------------------------------------------------- Zustand

    def _preset_list(self) -> list:
        return [{"name": n, "values": v} for n, v in self._presets.items()]

    @_guard
    def get_state(self) -> dict:
        esrgan = core.resolve_realesrgan(core.load_config().get("realesrgan_path"))
        return {
            "ffmpeg": self._engine.ffmpeg_found,
            "ffmpeg_path": self._engine.ffmpeg,
            "realesrgan": esrgan is not None,
            "defaults": core.DEFAULT_FORM,
            "default_crf": core.DEFAULT_CRF,
            "formats": {"video": core.VIDEO_FORMATS, "audio": core.AUDIO_FORMATS,
                        "image": core.IMAGE_FORMATS},
            "presets": self._preset_list(),
            "running": self._running,
        }

    # -------------------------------------------------------------- Ordner

    @_guard
    def choose_folder(self) -> dict:
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:                       # pywebview < 5
            kind = webview.FOLDER_DIALOG
        start = str(self._folder.parent if self._folder else Path.home())
        result = self._window.create_file_dialog(kind, directory=start)
        if not result:
            return {"folder": None}
        return self._load_folder(result if isinstance(result, str) else result[0])

    @_guard
    def load_folder(self, path: str) -> dict:
        return self._load_folder(path)

    def _load_folder(self, path: str) -> dict:
        if self._running:
            raise ValueError("Während der Konvertierung kann kein anderer Ordner "
                             "geladen werden.")
        folder = Path(path)
        if not folder.is_dir():
            raise ValueError("Das war kein Ordner — bitte einen Ordner ablegen.")
        files = []
        for root, dirs, fs in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.endswith(core.OUTPUT_SUFFIX)]
            for f in fs:
                if Path(f).suffix.lower().lstrip(".") in core.MEDIA_EXTS:
                    files.append(Path(root) / f)
        files.sort(key=lambda p: str(p).lower())
        with self._lock:
            self._folder = folder
            self._files = {str(p.relative_to(folder)): p for p in files}
        found = (f"{len(files)} Mediendatei(en) gefunden." if files
                 else "keine Mediendateien gefunden.")
        self._log(f"Ordner geladen: {folder.name} — {found}")
        return {"folder": {"name": folder.name, "path": str(folder)},
                "files": [self._file_info(rel, p) for rel, p in self._files.items()]}

    @staticmethod
    def _file_info(rel: str, path: Path) -> dict:
        ext = path.suffix.lower().lstrip(".")
        return {"rel": rel, "dir": rel[:len(rel) - len(path.name)], "name": path.name,
                "ext": ext, "kind": core.media_kind(ext)}

    def _file(self, rel: str) -> Path:
        path = self._files.get(rel)
        if path is None:
            raise ValueError("Diese Datei ist nicht mehr in der Liste.")
        return path

    @_guard
    def open_file(self, rel: str) -> dict:
        """Doppelklick: Datei in der Standard-App öffnen."""
        path = self._file(rel)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # noqa
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self._log(f"{path.name} konnte nicht geöffnet werden: {e}")
            raise
        self._log(f"Geöffnet: {path.name}")
        return {}

    # ------------------------------------------------------------- Presets

    @_guard
    def save_preset(self, name: str, values: dict) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("Bitte einen Namen für das Preset eingeben.")
        try:
            form = core.coerce_form(values or {})
        except ValueError:
            raise ValueError("Erst die Zahlenfelder korrigieren, dann das Preset speichern.")
        with self._lock:
            self._presets[name] = form
            written = core.write_presets(self._presets)
        self._log(f"Preset gespeichert: {name}")
        if not written:
            self._log("Die Presets-Datei konnte nicht geschrieben werden.")
        return {"presets": self._preset_list(), "name": name}

    @_guard
    def delete_preset(self, name: str) -> dict:
        with self._lock:
            if name not in self._presets:
                raise ValueError(f"Preset „{name}“ gibt es nicht mehr.")
            del self._presets[name]
            core.write_presets(self._presets)
        self._log(f"Preset gelöscht: {name}")
        return {"presets": self._preset_list()}

    # ------------------------------------------------------------- Pipette

    @_guard
    def picker_open(self, rel: str) -> dict:
        """Lädt ein Bild für die Pipette „Aus Bild“ und liefert eine Vorschau."""
        path = self._file(rel)
        if path.suffix.lower().lstrip(".") not in core.IMAGE_EXTS:
            raise ValueError(f"{path.name} ist kein Bild — bitte ein png/jpg/webp wählen.")
        try:
            from PIL import Image
        except ImportError:
            raise ValueError("Für die Pipette wird Pillow gebraucht — bitte den "
                             "Installer erneut ausführen.")
        Image.MAX_IMAGE_PIXELS = None
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            raise ValueError(f"{path.name} lässt sich nicht öffnen: {e}")
        ow, oh = img.size
        scale = min(1000 / ow, 760 / oh, 1.0)
        disp = img.resize((max(1, int(ow * scale)), max(1, int(oh * scale))),
                          Image.LANCZOS) if scale < 1.0 else img
        buf = io.BytesIO()
        disp.save(buf, format="PNG")
        with self._lock:
            self._picker = (path, img)
        return {"name": path.name, "width": ow, "height": oh,
                "src": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}

    @_guard
    def picker_pick(self, x: int, y: int) -> dict:
        """Exakte Farbe am Bildpunkt (x, y) des Originalbilds."""
        with self._lock:
            if self._picker is None:
                raise ValueError("Die Pipette ist nicht mehr offen.")
            path, img = self._picker
            self._picker = None
        ow, oh = img.size
        x = min(ow - 1, max(0, int(x)))
        y = min(oh - 1, max(0, int(y)))
        r, g, b = img.getpixel((x, y))[:3]
        hexv = f"#{r:02X}{g:02X}{b:02X}"
        self._log(f"Farbe {hexv} aus {path.name} übernommen.")
        return {"hex": hexv}

    @_guard
    def picker_close(self) -> dict:
        with self._lock:
            self._picker = None
        return {}

    # ---------------------------------------------------------------- Lauf

    @_guard
    def start(self, rels: list, values: dict) -> dict:
        try:
            form = core.coerce_form(values or {})
        except ValueError:
            raise ValueError("Bitte die Zahlenfelder prüfen — Auflösung, CRF, dB usw. "
                             "müssen Zahlen sein.")
        with self._lock:
            if self._running:
                raise ValueError("Es läuft bereits eine Konvertierung.")
            if self._folder is None:
                raise ValueError("Bitte zuerst einen Ordner wählen.")
            files = [self._files[r] for r in (rels or []) if r in self._files]
            if not files:
                raise ValueError("Mindestens eine Datei auswählen (Kopfzeile anklicken "
                                 "für alle).")
            folder, target = self._folder, form["target"]
            job = uuid.uuid4().hex
            self._running = True
            self._engine.cancel_event.clear()
            self._status = {"title": f"Konvertiere 1/{len(files)}", "text": files[0].name}
            self._progress = {"done": 0, "total": len(files)}
            self._outcome = {"id": job, "state": "running"}
        self._log("─" * 40)
        self._log(f"Konvertiere {len(files)} Datei(en) nach {target} …")
        threading.Thread(target=self._worker,
                         args=(job, folder, files, target, core.conversion_opts(form)),
                         daemon=True).start()
        return {"job": job, "total": len(files)}

    def _worker(self, job, folder, files, target, opts) -> None:
        outcome = {"id": job, "state": "failed", "ok": 0, "total": len(files)}
        try:
            result = self._engine.run(folder, files, target, opts)
            outcome.update(result, state="cancelled" if result["cancelled"] else "done")
        except FileNotFoundError as e:
            outcome["error"] = str(e)
            self._log(f"✗ Fehler: {e} — ist FFmpeg installiert?")
        except Exception as e:
            outcome["error"] = str(e)
            self._log(f"✗ Fehler: {e}")
        finally:
            self._engine.cancel_event.clear()
            self._engine.current_proc = None
            with self._lock:
                self._running = False
                self._outcome = outcome

    @_guard
    def cancel(self) -> dict:
        if not self._running:
            return {}
        self._engine.cancel()
        self._log("Breche ab …")
        return {}

    def _log(self, text: str) -> None:
        for line in str(text).splitlines() or [""]:
            self._log_queue.put(line)

    def _set_status(self, title: str, text: str = "") -> None:
        with self._lock:
            self._status = {"title": title, "text": text}

    def _set_progress(self, done: int, total: int) -> None:
        with self._lock:
            self._progress = {"done": done, "total": total}

    @_guard
    def poll(self) -> dict:
        lines = []
        try:
            while True:
                lines.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        with self._lock:
            return {"lines": lines, "running": self._running,
                    "status": dict(self._status), "progress": dict(self._progress),
                    "outcome": dict(self._outcome)}

    # -------------------------------------------------------------- Update

    @staticmethod
    def _git_env() -> dict:
        # Don't let git block on a credential prompt when there's no terminal.
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    @_guard
    def check_update(self) -> dict:
        repo = str(core._app_dir())
        if not (Path(repo) / ".git").exists():
            return {"state": "nogit"}
        if self._running:
            raise ValueError("Bitte warten, bis die Konvertierung beendet ist.")
        try:
            f = subprocess.run(["git", "-C", repo, "fetch", "--quiet"],
                               capture_output=True, text=True, timeout=90,
                               env=self._git_env(), creationflags=core.NO_WINDOW)
            if f.returncode != 0:
                return {"state": "error",
                        "message": (f.stderr or f.stdout or "fetch failed").strip()}
            c = subprocess.run(["git", "-C", repo, "rev-list", "--count", "HEAD..@{u}"],
                               capture_output=True, text=True, timeout=30,
                               creationflags=core.NO_WINDOW)
        except FileNotFoundError:
            return {"state": "error", "message": "git ist nicht installiert."}
        if c.returncode != 0:
            return {"state": "error",
                    "message": (c.stderr or c.stdout or "compare failed").strip()}
        behind = int((c.stdout or "0").strip() or "0")
        return {"state": "behind" if behind > 0 else "current", "behind": behind}

    @_guard
    def apply_update(self) -> dict:
        if self._running:
            raise ValueError("Bitte warten, bis die Konvertierung beendet ist.")
        r = subprocess.run(["git", "-C", str(core._app_dir()), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=180,
                           env=self._git_env(), creationflags=core.NO_WINDOW)
        out = (r.stdout + "\n" + r.stderr).strip()
        self._log(out or "(keine Ausgabe)")
        if r.returncode != 0:
            return {"ok": False, "error": out[-600:] or "git pull fehlgeschlagen"}
        return {"output": out}

    @_guard
    def restart(self) -> dict:
        self._engine.shutdown_ai_worker()
        script = os.path.abspath(sys.argv[0])
        os.execv(sys.executable, [sys.executable, script])
        return {}
