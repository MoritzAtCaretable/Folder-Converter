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

REM 3. Python-Pakete
echo -^> Installiere Python-Pakete...
python -m pip install --upgrade customtkinter tkinterdnd2 pillow "rembg[cpu]"

REM 4. Verknuepfung im Ordner erzeugen (nicht auf dem Desktop)
echo -^> Erzeuge Verknuepfung...
python Converter.py --make-app

echo.
echo Fertig. 'Folder Converter.lnk' liegt in diesem Ordner.
echo Kopiere sie auf den Desktop, wenn du magst.
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