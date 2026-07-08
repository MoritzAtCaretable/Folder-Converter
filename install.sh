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

# 4. Python-Pakete
echo "→ Installiere Python-Pakete…"
python3 -m pip install --break-system-packages --upgrade \
    customtkinter tkinterdnd2 pillow "rembg[cpu]"

# 5. App-Bundle im Ordner erzeugen (nicht auf dem Desktop)
echo "→ Erzeuge 'Folder Converter.app'…"
python3 Converter.py --make-app || true

echo ""
echo "✓ Fertig. 'Folder Converter.app' liegt in diesem Ordner."
echo "  Zieh sie auf den Desktop oder mach per Rechtsklick → 'Alias erzeugen' eine Verknüpfung."