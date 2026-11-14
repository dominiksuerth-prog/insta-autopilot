# Instagram-Autopilot (GitHub-Variante)

Läuft komplett kostenlos auf GitHub: Claude schreibt Karussell-Posts, GitHub Actions rendert die Slides,
GitHub Pages stellt die Bilder bereit, du gibst per Telegram frei, der Post geht auf Instagram.
Die Einrichtung steht Schritt für Schritt in der Setup-Anleitung (Doc).

## Was wo liegt

| Datei / Ordner | Zweck |
| --- | --- |
| `config.yaml` | Einstellungen, Hauptschalter `enabled`, Kanäle an/aus (`active`) |
| `channels/<kanal>/topics.txt` | Themenliste, eine Zeile = ein Post |
| `channels/<kanal>/facts.md` | Faktenbasis – nur diese Zahlen landen in Posts |
| `channels/<kanal>/style.md` | Ton und Regeln des Kanals |
| `channels/<kanal>/theme.css` | Farben und Schriften |
| `templates/slide.html` | Slide-Layouts (cover, point, list, cta) |
| `docs/` | Öffentliche Website: Slides, Link-Seite, Impressum |
| `data/state.json` | Fortschritt und Posts (automatisch, enthält keine Schlüssel) |

## Workflows (Reiter "Actions")

| Workflow | Wann |
| --- | --- |
| Posts erzeugen | Mo, Di, Do, Fr morgens – oder per "Run workflow" |
| Telegram-Freigaben | alle 15 Minuten |
| Setup prüfen | per "Run workflow" |
| Website veröffentlichen | wenn du etwas in `docs/` änderst |

Zugangsschlüssel liegen nur in Settings → Secrets and variables → Actions – nie in Dateien.
