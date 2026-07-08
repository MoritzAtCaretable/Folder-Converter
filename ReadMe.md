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
- **⬇ Update**-Knopf: holt die neueste Version und startet neu

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

Alternativ direkt im Terminal:
```
python3 Converter.py      # macOS
python Converter.py       # Windows
```

---

## Aktualisieren

Rechts oben im Programm gibt es den Knopf **⬇ Update**. Ein Klick holt die neueste Version
und startet das Programm automatisch neu. Voraussetzung ist, dass das Projekt per Git verbunden
ist — das ist nach `install.sh`/`install.bat` automatisch der Fall (auch bei ZIP-Download).

---

## Kurzanleitung zur Bedienung

1. **Ordner wählen** — auf das Fenster ziehen oder „Browse folder".
2. **Zielformat** oben auswählen. Je nach Format erscheinen passende Optionen
   (Bild-Optionen, Audio-Optionen, …).
3. Optional Dateien in der Liste **an-/abwählen** (Mehrfachauswahl mit Shift/Ctrl,
   Ziehen am Rand scrollt automatisch weiter).
4. **Convert** unten rechts. Das Ergebnis landet in einem Unterordner
   `<Format> - converted` — vorhandene Dateien werden nie überschrieben.

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

- **„Real-ESRGAN not found"** → Der `realesrgan/`-Ordner fehlt oder liegt falsch. Er muss neben
  `Converter.py` liegen, mit `realesrgan/macos/realesrgan-ncnn-vulkan` (bzw. `windows/…exe`) und
  dem `models/`-Ordner direkt daneben.
- **KI-Hintergrund: „No onnxruntime backend found"** → Die KI-Engine fehlt. Einmalig ausführen:
  `python3 -m pip install --break-system-packages "rembg[cpu]"` (Anführungszeichen wichtig).
- **Update-Knopf sagt „No git connection"** → Einmal den Installer laufen lassen; er stellt die
  Git-Verbindung her.
- **FFMPEG nicht gefunden** → `brew install ffmpeg` (macOS) bzw. FFMPEG über winget (Windows);
  der Installer macht das normalerweise automatisch.

---

## Was landet im Repository?

Ins Git gehören: `Converter.py`, die Icons, `install.sh`/`install.bat`, `README.md` und der
`realesrgan/`-Ordner. **Nicht** ins Git gehören die maschinenspezifischen Startdateien
(`Folder Converter.app`, `*.lnk`) und Caches — die stehen bereits in der `.gitignore` und werden
lokal vom Installer erzeugt.