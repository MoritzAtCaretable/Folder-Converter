# Folder Converter — Ideen für weitere Funktionen

Stand: Analyse nach dem Redesign. Aufwand grob geschätzt (S = Stunden, M = ein Tag, L = mehrere Tage).

## Umgesetzt

- **Trimmen mit Vorschau** — Videos und Audios aus der Auswahl untereinander laden
  (Videovorschau, Filmstreifen, Wellenform), per Griffen oder Zeitfeldern zuschneiden, einzeln
  oder alle auf einmal speichern. „Für alle" schneidet bei jeder Datei gleich viel vom Anfang und
  vom Ende ab. Ausgabe in `trim - converted/`, Originale bleiben unverändert.

## Schneller und angenehmer arbeiten

| Idee | Nutzen | Aufwand |
|---|---|---|
| **Fortschritt innerhalb einer Datei** (`ffmpeg -progress`) mit Restzeit | Bei langen Videos sieht man heute nur „1/1“ — wirkt wie eingefroren | S |
| **Mehrere Dateien parallel** (2–4 FFmpeg-Prozesse) | Bilder- und Audiostapel deutlich schneller | M |
| **Hardware-Kodierung** auf dem Mac (VideoToolbox) als Option | H.264/HEVC-Videos mehrfach schneller | S |
| **„Ausgabeordner öffnen“** nach dem Lauf, Doppelklick im Protokoll öffnet die Datei | Spart den Weg in den Finder | S |
| **Fehlgeschlagene erneut versuchen** — nur die Dateien mit ✗ neu starten | Weniger Suchen im Protokoll | S |
| **Vorhandene Ergebnisse überspringen** statt `name01` anzulegen | Abgebrochene Stapel einfach fortsetzen | S |
| **Suche und Sortierung** in der Dateiliste (Name, Größe, Dauer) | Große Ordner übersichtlicher | S |
| **Dateigröße/Dauer/Auflösung** als Spalten, nach dem Lauf „vorher → nachher“ | Sieht sofort, was die Konvertierung spart | M |
| **Einzelne Dateien** per Drag & Drop (nicht nur Ordner) | Schneller für Einzelfälle | S |
| **Eigener Ausgabeordner** und Namensschema (Präfix/Suffix, Nummerierung) | Ergebnisse direkt dort, wo sie gebraucht werden | M |

## Video

| Idee | Nutzen | Aufwand |
|---|---|---|
| **Ton entfernen / Audio extrahieren** (Video → mp3/wav direkt) | Häufiger Handgriff, heute nur über Umwege | S |
| **Standbild exportieren** (Frame an Position X oder das Vorschaubild) | Thumbnails für Kurse/Apps | S |
| **GIF-Export** mit Palette, FPS und Breite | Kurze Animationen für Präsentationen | S |
| **FPS ändern, Ziel-Dateigröße** (z. B. „unter 20 MB“ per Zwei-Pass) | Upload-Limits einhalten | M |
| **Drehen/Spiegeln**, Seitenverhältnis-Zuschnitt (9:16, 1:1) | Hochkant-Videos für Social/App | S |
| **Ein-/Ausblenden** (Fade) für Video und Ton | Saubere Clip-Übergänge | S |
| **Clips zusammenfügen** (mehrere Dateien in Listenreihenfolge) | Passt gut zur neuen Trimm-Ansicht | M |
| **Mehrere Bereiche pro Datei** im Trimmer (Teile ausschneiden) und Zoom in die Zeitleiste | Präzise Schnitte bei langen Aufnahmen | M |

## Audio

| Idee | Nutzen | Aufwand |
|---|---|---|
| **Lautheit nach EBU R128** (`loudnorm`) zusätzlich zur Peak-Normalisierung | Gleich laute Dateien statt nur gleicher Spitzen | S |
| **Stille am Anfang/Ende automatisch entfernen** (`silenceremove`) | Ideal für TTS-/Sprachaufnahmen | S |
| **Ein-/Ausblenden** in Sekunden | Kein Knacken am Schnitt | S |
| **Kanäle trennen/zusammenführen**, Mono-Mixdown | Aufnahmen mit Stereomikro bereinigen | S |

## Bild

| Idee | Nutzen | Aufwand |
|---|---|---|
| **Vorher/Nachher-Vorschau** für Hintergrundentfernung und Farbschlüssel | Einstellungen testen, bevor der Stapel läuft | M |
| **Hintergrundfarbe beim JPG-Export** (Transparenz → Weiß/eigene Farbe) | Transparenz geht heute einfach verloren (oft schwarzer Rand/Hintergrund) | S |
| **Weitere Formate**: HEIC (iPhone) und AVIF lesen, AVIF schreiben | iPhone-Fotos direkt verarbeiten | M |
| **Drehen/Spiegeln, Rand/Leinwand, Wasserzeichen** | Häufige Kleinanpassungen | M |
| **Metadaten (EXIF/GPS) entfernen** | Datenschutz beim Weitergeben | S |

## Organisation und Qualität

| Idee | Nutzen | Aufwand |
|---|---|---|
| **Presets exportieren/importieren** (Datei fürs Team) | Alle arbeiten mit denselben Rezepten | S |
| **Hell/Dunkel-Umschaltung** wie in Content Image Automation | Einheitlich mit den anderen Apps | S |
| **Automatische Tests** (Befehlsbau, Engine-Lauf mit Mini-Dateien) im Repo | Updates sicher ausliefern | M |
| **Hinweis auf fehlende Encoder** der installierten FFmpeg-Version (z. B. libvorbis, libwebp fehlen bei Homebrew) | Verständliche Meldung statt FFmpeg-Fehler | S |
