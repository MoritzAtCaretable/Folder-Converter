@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ============================================
echo   Folder Converter - Einrichtung (Windows)
echo ============================================

REM --- HIER ANPASSEN: Repo-URL (fuer den ZIP-Fall) ---
set "REPO_URL=https://github.com/MoritzAtCaretable/Folder-Converter.git"

REM 1. Python, git, ffmpeg sicherstellen (via winget)
where python >nul 2>&1 || winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
where git    >nul 2>&1 || winget install -e --id Git.Git          --accept-source-agreements --accept-package-agreements
where ffmpeg >nul 2>&1 || winget install -e --id Gyan.FFmpeg      --accept-source-agreements --accept-package-agreements

REM 2. git-graft: ZIP-Ordner in ein Git-Checkout verwandeln (nichts wird geloescht)
if not exist ".git" (
    where git >nul 2>&1 && call :graft
)

REM 3. venv + Pakete — ALLES isoliert im Ordner, nichts global.
if not exist ".venv" (
    echo -^> Virtuelle Umgebung anlegen...
    python -m venv .venv
)
echo -^> Installiere Python-Pakete...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

REM 4. Verknuepfung mit dem venv-Python erzeugen, damit der Launcher aufs venv zeigt.
echo -^> Erzeuge Verknuepfung...
.venv\Scripts\python Converter.py --make-app

echo.
echo Fertig. 'Folder Converter.lnk' liegt in diesem Ordner.
echo Alle Pakete stecken isoliert in .venv\ - es wurde nichts global installiert.
echo Kopiere die Verknuepfung auf den Desktop, wenn du magst.
echo (Falls Python gerade erst installiert wurde: Fenster schliessen, neu oeffnen und install.bat erneut starten.)
pause
exit /b 0

:graft
echo -^> Kein Git-Checkout erkannt (vermutlich ZIP). Richte Git-Verbindung ein...
set "TMP=%TEMP%\fc_graft_%RANDOM%"
git clone --depth 1 "%REPO_URL%" "%TMP%\repo" >nul 2>&1
if exist "%TMP%\repo\.git" (
    move "%TMP%\repo\.git" ".\.git" >nul
    rmdir /s /q "%TMP%"
    git reset --hard HEAD >nul 2>&1
    echo    Git-Verbindung hergestellt - Update-Button ist jetzt aktiv.
) else (
    if exist "%TMP%" rmdir /s /q "%TMP%"
    echo    Git-Verbindung fehlgeschlagen. Laeuft trotzdem, aber ohne Update-Button.
)
exit /b 0
