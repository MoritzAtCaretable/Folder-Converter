#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "════════════════════════════════════════"
echo "  Folder Converter — Einrichtung (macOS)"
echo "════════════════════════════════════════"

# ─────────────────────────────────────────────
# HIER ANPASSEN: Repo-URL (für den ZIP→Git-Fall)
# ─────────────────────────────────────────────
REPO_URL="https://github.com/MoritzAtCaretable/Folder-Converter.git"

# 1. Homebrew sicherstellen
if ! command -v brew >/dev/null 2>&1; then
    echo "→ Installiere Homebrew…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"; fi
if [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"; fi

# 2. git, python, ffmpeg sicherstellen
for pkg in git python ffmpeg; do
    if ! command -v "$pkg" >/dev/null 2>&1; then
        echo "→ Installiere $pkg…"; brew install "$pkg"
    fi
done

# 3. git-graft: Falls dieser Ordner KEIN Git-Checkout ist (ZIP-Download),
#    nachträglich zu einem machen — dann funktioniert der Update-Button.
#    Es wird nichts gelöscht: nur der .git-Ordner wird "aufgepfropft".
if [ ! -d ".git" ] && command -v git >/dev/null 2>&1; then
    echo "→ Kein Git-Checkout erkannt (vermutlich ZIP). Richte Git-Verbindung ein…"
    TMP="$(mktemp -d)"
    if git clone --depth 1 "$REPO_URL" "$TMP/repo" >/dev/null 2>&1; then
        mv "$TMP/repo/.git" "./.git"
        rm -rf "$TMP"
        git reset --hard HEAD >/dev/null 2>&1 || true
        echo "✓ Git-Verbindung hergestellt — Update-Button ist jetzt aktiv."
    else
        rm -rf "$TMP"
        echo "⚠ Git-Verbindung fehlgeschlagen (Zugriff/Netz?). Läuft trotzdem, aber ohne Update-Button."
    fi
fi

# 4. venv + Pakete — ALLES isoliert im Ordner, nichts systemweit.
#    Ein venv ist an seinen absoluten Pfad gebunden; wurde der Ordner verschoben,
#    zeigt es ins Leere und wird neu gebaut.
VENV_DIR="$(pwd)/.venv"
if [ -d ".venv" ]; then
    if [ -f ".venv/pyvenv.cfg" ] && grep -q "$VENV_DIR" ".venv/pyvenv.cfg" 2>/dev/null; then
        :  # venv passt zum aktuellen Pfad
    elif [ -x ".venv/bin/python" ] && .venv/bin/python -c '' 2>/dev/null; then
        :  # venv funktioniert
    else
        echo "→ Vorhandenes venv passt nicht zu diesem Ordner (verschoben?) — wird neu gebaut…"
        rm -rf .venv
    fi
fi
if [ ! -d ".venv" ]; then
    echo "→ Virtuelle Umgebung anlegen…"
    python3 -m venv .venv
fi
echo "→ Pakete installieren…"
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt

# 5. App-Bundle im Ordner erzeugen — mit dem venv-Python, damit der Launcher
#    aufs venv zeigt (nicht aufs globale Python).
echo "→ Erzeuge 'Folder Converter.app'…"
.venv/bin/python Converter.py --make-app || true

echo ""
echo "✓ Fertig. 'Folder Converter.app' liegt in diesem Ordner."
echo "  Alle Pakete stecken isoliert in .venv/ — es wurde nichts systemweit installiert."
echo "  Zieh die App auf den Desktop oder mach per Rechtsklick → 'Alias erzeugen' eine Verknüpfung."
