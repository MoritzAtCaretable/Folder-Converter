# Folder Converter

Ein kleines Desktop-Programm zum **stapelweisen Umwandeln von Medien** (Video, Audio, Bilder)
per FFMPEG — mit ein paar praktischen Extras: Hintergrund entfernen (einfarbig **oder** per KI),
KI-Upscaling, Drag-&-Drop eines ganzen Ordners, Presets und einem Update-Knopf.

Läuft auf **macOS** und **Windows**.

---

## Was das Programm kann

- **Umwandeln in Stapeln**: einen Ordner auswählen, Zielformat wählen, fertig.
  - Video → `webm`, `mov`, `mp4` (transparente Quellen bleiben in `webm` transparent)
  - Audio → `mp3`, `opus`, `wav` (inkl. Peak-Normalisierung und Trimmen)
  - Bilder → `png`, `jpg`, `webp` (Skalieren, Zuschneiden, Qualität)
- **Hintergrund entfernen** (nur Bilder, für `png`/`webp`):
  - **Color-Key** – entfernt eine bestimmte Farbe (Pipette „From image" holt sie direkt aus dem Bild)
  - **KI (automatisch)** – erkennt das Motiv und schneidet den Rest frei; optional mit Alpha-Matting für feine Kanten
- **Upscaling** (nur Bilder) per **Real-ESRGAN** – 2×/3×/4×, Modus für Fotos oder Illustrationen
- **Drag-&-Drop**: Ordner irgendwo aufs Fenster ziehen
- **Presets** für wiederkehrende Einstellungen
- **Doppelklick** auf eine Datei in der Liste öffnet sie in der Standard-App (Vorschau/QuickTime/VLC)
- **Update suchen**-Knopf: holt die neueste Version und startet neu

Die Oberfläche ist im Caretable-Design gebaut und läuft in einem nativen Fenster
(WKWebView auf macOS, WebView2 auf Windows) — kein Browser nötig.

---

## Installation

> Python, FFMPEG und Git müssen **nicht** vorab installiert sein — der Installer kümmert sich
> bei Bedarf selbst darum.

Es gibt zwei Wege, das Projekt zu holen. **Weg A (per Git)** wird empfohlen, weil der
Update-Knopf danach direkt funktioniert. **Weg B (ZIP)** geht auch — der Installer richtet die
Git-Verbindung dann nachträglich ein, damit der Update-Knopf trotzdem läuft.

### macOS

**Weg A – per Git (empfohlen):**
```
git clone https://github.com/MoritzAtCaretable/Folder-Converter.git
cd Folder-Converter
bash install.sh
```

**Weg B – per ZIP:** Auf GitHub „Code → Download ZIP", entpacken, dann im Terminal in den
Ordner wechseln und den Installer starten:
```
cd /Pfad/zum/entpackten/Ordner
bash install.sh
```

Der Installer installiert bei Bedarf Homebrew, Python, FFMPEG und Git, richtet die
Python-Pakete ein und erzeugt am Ende **`Folder Converter.app`** im Ordner.

### Windows

**Weg A – per Git (empfohlen):**
```
git clone https://github.com/MoritzAtCaretable/Folder-Converter.git
cd Folder-Converter
install.bat
```

**Weg B – per ZIP:** ZIP herunterladen, entpacken, in den Ordner wechseln und `install.bat`
per Doppelklick starten.

Der Installer installiert bei Bedarf Python, Git und FFMPEG (über winget), richtet die
Python-Pakete ein und erzeugt am Ende **`Folder Converter.lnk`** im Ordner.

> Hinweis Windows: Falls Python gerade erst frisch installiert wurde, das Fenster einmal
> schließen, neu öffnen und `install.bat` erneut starten (damit Windows Python im Pfad findet).

---

## Starten

Nach der Installation liegt im Ordner eine Startdatei:

- **macOS:** `Folder Converter.app` — Doppelklick startet die App (ohne Terminal).
  Du kannst sie auf den Schreibtisch ziehen oder per Rechtsklick → **„Alias erzeugen"**
  eine Verknüpfung dorthin legen.
- **Windows:** `Folder Converter.lnk` — Doppelklick startet die App.
  Bei Bedarf auf den Desktop kopieren.

Alternativ direkt im Terminal (im Projektordner):
```
.venv/bin/python Converter.py          # macOS
.venv\Scripts\python Converter.py      # Windows
```

---

## Aktualisieren

Rechts oben im Programm gibt es den Knopf **Update suchen**. Ein Klick prüft, ob es eine neuere
Version gibt; nach einer Bestätigung wird sie geholt und das Programm startet automatisch neu.
Voraussetzung ist, dass das Projekt per Git verbunden ist — das ist nach `install.sh`/`install.bat`
automatisch der Fall (auch bei ZIP-Download).

> Hinweis zum Umstieg auf die neue Oberfläche: Ältere Installationen laufen noch ohne das Paket
> `pywebview`. Nach dem Update fragt die App deshalb einmalig, ob der Installer laufen soll — er
> richtet alles in `.venv/` ein. Danach die App einfach wieder öffnen.

---

## Kurzanleitung zur Bedienung

1. **Ordner wählen** — auf das Fenster ziehen oder „Ordner wählen“ / „Durchsuchen…“.
2. **Zielformat** unter „Konvertieren nach“ auswählen. Je nach Format erscheinen passende
   Optionen (Video, Audio, Bild).
3. Dateien in der Liste **anhaken**: Klick schaltet um, Shift-Klick hakt einen Bereich an,
   Ziehen über mehrere Zeilen hakt alle an (am Rand scrollt die Liste mit). Das Kästchen in
   der Kopfzeile wählt alle; die Filter darüber zeigen nur ein Format.
4. **Konvertierung starten** unten rechts (■ daneben bricht nach Rückfrage ab). Das Ergebnis
   landet in einem Unterordner `<Format> - converted` — vorhandene Dateien werden nie
   überschrieben.

Die Griffe unter Dateiliste und Protokoll ändern per Ziehen die Höhe, ein Doppelklick setzt
sie zurück.

Beim ersten Einsatz der **KI-Hintergrundentfernung** lädt das Programm einmalig ein
Modell (~170 MB, Fortschritt sichtbar). Das **Upscaling** braucht das mitgelieferte
Real-ESRGAN (siehe unten).

---

## Real-ESRGAN (fürs Upscaling)

Das Upscaling nutzt das Programm `realesrgan-ncnn-vulkan`, das im Ordner **mitgeliefert** wird:

```
realesrgan/
  macos/     → realesrgan-ncnn-vulkan   + models/
  windows/   → realesrgan-ncnn-vulkan.exe + models/
```

Die App findet es automatisch (der Unterordner passend zum Betriebssystem). Ausführrechte und
die macOS-Quarantäne setzt sie beim ersten Upscaling selbst — es ist nichts von Hand zu tun.

---

## Problemlösung

- **„Real-ESRGAN nicht gefunden"** → Der `realesrgan/`-Ordner fehlt oder liegt falsch. Er muss neben
  `Converter.py` liegen, mit `realesrgan/macos/realesrgan-ncnn-vulkan` (bzw. `windows/…exe`) und
  dem `models/`-Ordner direkt daneben.
- **KI-Hintergrund: „No onnxruntime backend found"** → Die KI-Engine fehlt. Einfach den Installer
  erneut ausführen — er installiert `rembg[cpu]` isoliert ins `.venv/` des Ordners. (Manuell im
  Ordner: `.venv/bin/python -m pip install "rembg[cpu]"`, Anführungszeichen wichtig.)
- **Update-Knopf sagt „noch nicht mit Git verbunden"** → Einmal den Installer laufen lassen; er
  stellt die Git-Verbindung her.
- **App startet nicht / fragt nach dem Installer** → `pywebview` fehlt in der Python-Umgebung.
  Installer erneut ausführen (`bash install.sh` bzw. `install.bat`).
- **FFMPEG nicht gefunden** (roter Punkt oben rechts) → `brew install ffmpeg` (macOS) bzw.
  FFMPEG über winget (Windows); der Installer macht das normalerweise automatisch.

---

## Was landet im Repository?

Ins Git gehören: `Converter.py` (Start), `converter_core.py` (Konvertier-Logik),
`webui_api.py` (Brücke zur Oberfläche), der Ordner `webui/` (HTML/CSS/JS, Schriften, Icon),
`requirements.txt`, die Icons, `install.sh`/`install.bat`, `README.md` und der
`realesrgan/`-Ordner. **Nicht** ins Git gehören die maschinenspezifischen Startdateien
(`Folder Converter.app`, `*.lnk`) und Caches — die stehen bereits in der `.gitignore` und werden
lokal vom Installer erzeugt.