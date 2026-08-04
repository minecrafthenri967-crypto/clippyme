# Abschluss-Handoff — Gaming-Layout-Editor & Discord-Bot-Fallback

Stand: 04.08.2026 · Branch `claude/ai-clipper-pro-pipeline-ze09sn` · Commit `8a9d913`
(auf dem VPS deployed, `docker compose up --build -d` lief dort zuletzt erfolgreich durch)

Dieses Dokument beschreibt den Übergabestand der **letzten aktiven Arbeit** auf
diesem Branch — nicht die AI-Clipper-Pro-Pipeline (die ist fertig, gemerged
und läuft; siehe Git-Historie vor Commit `5cd2a7e`). Es ist bewusst ehrlich bei
den Lücken — Abschnitt 4 ist der wichtigste Teil.

---

## 1. Das Ziel — was gebaut werden soll

Zwei getrennte, in dieser Reihenfolge entstandene Vorhaben:

### 1.1 Discord-Bot: volle Clip-Qualität auch über Discords Upload-Limit hinaus
Der Genehmigungs-Bot (`discordbot/bot.py`) postet Clips zur Freigabe; ein Clip
über Discords Upload-Grenze (10/50/100 MB je nach Boost-Stufe) konnte bisher
nicht als Datei geschickt werden. Ziel: ein Fallback, der den Clip stattdessen
irgendwo hochlädt und einen Link postet — ohne dass der Nutzer eigene
Bankdaten/Kreditkarte hinterlegen muss.

### 1.2 Gaming-Reframe: Layout **zeichnen** statt Werte raten
Bisher war das `gaming`-Reframe (Facecam oben, Gameplay unten im 9:16-Ausschnitt)
weitgehend hartcodiert: Facecam-Anteil fix bei 35 %, Gameplay-Ausschnitt immer
horizontal zentriert über die volle Bildhöhe. Der Nutzer hat das per Skizze
konkretisiert (siehe Chat-Verlauf): **zu Beginn jedes Projekts einen
Screenshot aus dem Stream hochladen, darauf selbst einzeichnen** —

1. wo die Facecam ist,
2. wo das eigentliche Gameplay ist (nicht zwangsläufig mittig — Taskbar/Chat/
   Overlays sollen ausschließbar sein),
3. wo im fertigen 9:16-Bild der Text-Hook sitzt,
4. wo darin die Untertitel sitzen,

— und das **als benannte Vorlage pro Streamer/Spiel speichern**, damit es beim
nächsten Projekt (auch für einen anderen Streamer) wiederverwendbar ist, statt
jedes Mal neu einzuzeichnen.

---

## 2. Der aktuelle Stand — wo es steht

### 2.1 Discord-Bot-Fallback — **fertig und auf dem VPS in Betrieb**
Nach drei Anläufen (Cloudflare R2 → verworfen wegen möglicher Zahlungsmethode;
Backblaze B2 → verworfen, weil der Nutzer inzwischen doch einen Google-Cloud-
Dienstkonto-Schlüssel bekommen hat) steht die Lösung bei **Google Drive**
(Service-Account, `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` + `GOOGLE_DRIVE_FOLDER_ID`
in `.env.discord`). Läuft auf dem VPS, Container `clippyme-discordbot` aktiv.

Dabei nebenbei ein echter Bug gefunden und behoben: die Download-Reaktion
verlinkte bei einem zu großen, aber bereits komponierten Clip (Untertitel/Hook
eingebrannt) auf die **rohe, nicht komponierte** Datei statt auf die tatsächlich
komponierte. Fix: `_compose_full` gibt jetzt `(pfad, komponierte_url)` zurück,
der Link nutzt die komponierte URL (`baa0d32`).

### 2.2 Gaming-Layout-Editor — **Backend + Editor-UI gebaut, gerendert & im Browser noch nicht bestätigt**

| Baustein | Backend | Editor-UI | Mit dem VPS verbunden |
|---|---|---|---|
| Facecam-Anteil einstellbar (0,35 → 0,45, Env-Override) | ✅ | ✅ (Vorschau-Regler) | ✅ |
| Gameplay-Bereich zeichenbar (statt immer zentriert) | ✅ | ✅ | ✅ |
| Text-Hook-Position frei (nicht nur oben/Mitte/unten) | ✅ | ✅ (Vorschau-Regler) | ✅ |
| Untertitel-Position frei | ✅ | ✅ (Vorschau-Regler) | ✅ |
| **Benannte Vorlage speichern/laden (pro Streamer)** | ✅ (`stream_layouts`-API) | ❌ **fehlt** | ❌ |

Die Editor-Oberfläche (`streamLayoutEditor.jsx`) ist fertig und lässt live
zeichnen + in einer 9:16-Vorschau verschieben — sie speichert die Werte aber
nur in den laufenden Projekt-Optionen (`opts.gamingFacecamBox` etc.), **nicht**
über die extra dafür gebaute `/api/config/stream-layouts`-Route. Die Vorlagen-
Speicherung, die der Nutzer explizit wollte („damit es auch für andere
Streamer funktioniert"), ist auf Backend-Seite fertig und getestet, aber im
Dashboard **an nichts angeschlossen**. Siehe Abschnitt 4.1 — das ist die
größte offene Lücke.

Alle Backend-Rechenfunktionen (Geometrie, Positions-Umrechnung) sind mit reinen
Host-Tests abgesichert (2322 Backend-Tests grün, 269 Frontend-Tests grün,
Ruff + ESLint sauber, Frontend-Build sauber). **Aber:** kein einziger dieser
Tests rendert eine echte Videodatei durch `create_gaming_frame` — siehe 4.2.

---

## 3. Die Dateien — woran gearbeitet wurde

### 3.1 Discord-Bot-Fallback
| Datei | Rolle |
|---|---|
| `discordbot/bot.py` | Google-Drive-Upload (`_upload_to_drive`), Fix für `_compose_full`/`link_url` |
| `discordbot/requirements.txt` | `google-auth` |
| `.env.discord.example` | Einrichtungsschritte Google Cloud Console |

### 3.2 Gaming-Layout-Editor — Backend (Python)
| Datei | Rolle |
|---|---|
| `src/clippyme/pipeline/reframe_ops.py` | **Rein, cv2-frei.** `gaming_facecam_fraction()` (Env-Override, geklemmt 0,15–0,75), `gaming_seam_overlay_y()`, `resolve_box_from_fractions()` (generalisiert aus der alten Facecam-only-Funktion), `resolve_gameplay_box_from_fractions()` |
| `src/clippyme/pipeline/reframe.py` | `create_gaming_frame()` nimmt jetzt `gameplay_box` + `facecam_fraction` entgegen statt fix zentriert/0,35 zu rechnen; `process_video_to_vertical()` neue Parameter `gaming_gameplay_box`, `gaming_split_fraction` |
| `src/clippyme/pipeline/main.py` | Neue CLI-Flags `--gaming-gameplay-x/y/w/h`, `--gaming-split` |
| `src/clippyme/pipeline/orchestrator.py` | Reicht dieselben Parameter durch |
| `src/clippyme/domain/job_results.py` | `build_main_cmd`: `_validate_gaming_box` (generalisiert), `_validate_gaming_split`, neue argv-Flags |
| `src/clippyme/domain/reframe_service.py` | Post-hoc-Reframe-Endpunkt (`POST /api/reframe`) reicht Gameplay-Box + Split ebenfalls durch |
| `src/clippyme/domain/hooks.py` | `HOOK_POSITIONS` inkl. neuem `"seam"`; `resolve_hook_overlay_y()` als reine, jetzt getestete Funktion extrahiert; akzeptiert zusätzlich eine 0..1-Bruchzahl (`parse_hook_position_fraction`) |
| `src/clippyme/domain/subtitles.py` | `parse_position_fraction()`, `margin_v_for_fraction()` — Untertitel-Position ebenfalls per Bruchzahl möglich; **außerdem**: alle sechs Preset-Schriftgrößen 35–43 → 52–64 (~2 % → ~3 % Bildhöhe, aus einem früheren Teilauftrag „Untertitel größer") |
| `src/clipper_pro/render/captions_ops.py` | `DEFAULT_FONT_SCALE` 2,4 → 1,6 — musste zusammen mit der Preset-Größe angepasst werden, sonst wären clipper-pros Untertitel ungewollt mitgewachsen |
| `src/clippyme/api/schemas.py` | `GamingGameplayBox`, `GAMING_SPLIT_MIN/MAX`, `StreamLayoutRequest`; `gaming_gameplay_box` + `gaming_split` auf `ProcessRequest`/`BatchRequest`/Reframe-Request |
| `src/clippyme/api/app.py` | `/api/process` (JSON **und** Multipart), `/api/batch`, `POST /api/reframe/{job}/{clip}` reichen die neuen Felder durch |
| `src/clippyme/storage/config_store.py` | **Neu:** `stream_layouts`-Namensraum — `list_stream_layouts`, `save_stream_layout`, `delete_stream_layout`. Jedes Geometrie-Feld einzeln optional; ein Kasten, der über den Bildrand hinausragt, wird **abgelehnt statt beschnitten** |
| `src/clippyme/api/config_routes.py` | `GET/POST /api/config/stream-layouts`, `DELETE .../{layout_id}` |

### 3.3 Gaming-Layout-Editor — Frontend (React)
| Datei | Rolle |
|---|---|
| `dashboard/src/redesign/streamLayoutEditor.jsx` | **Neu.** Zwei-Panel-Editor: links Screenshot mit zeichenbaren Facecam-/Gameplay-Kästen, rechts eine aus genau diesen Ausschnitten zusammengesetzte 9:16-Live-Vorschau mit drei ziehbaren Linien (Split/Hook/Untertitel) |
| `dashboard/src/lib/layoutGeometry.js` | **Neu, rein.** Spiegelt `expand_box_to_aspect` aus Python — die Vorschau rechnet **dieselbe** Geometrie wie der echte Renderer, keine Annäherung |
| `dashboard/src/redesign/create.jsx` | Ersetzt den alten `FacecamBoxPicker` durch `StreamLayoutEditor` |
| `dashboard/src/redesign/RedesignApp.jsx`, `presets.js` | Neue Optionsfelder (`gamingGameplayBox`, `gamingSplit`, `gamingHookY`, `gamingSubtitleY`) in Default-Zustand + „Als Standard speichern" aufgenommen |
| `dashboard/src/redesign/realApi.js`, `dashboard/src/lib/api.js` | Senden der neuen Felder an `/api/process` und `/api/batch` |
| `dashboard/src/i18n/de.js`, `en.js` | Neue Texte unter `layout.*` |

### 3.4 Tests (neu/erweitert)
`tests/pipeline/test_reframe_ops.py`, `tests/domain/test_hook_overlay.py`,
`tests/domain/test_subtitle_style.py`, `tests/domain/test_job_results.py`,
`tests/storage/test_config_store.py`, `tests/api/test_config_routes.py`,
`dashboard/src/lib/layoutGeometry.test.js`, `dashboard/src/redesign/create.test.jsx`.

### 3.5 Dokumentation
`CLAUDE.md` (Abschnitte Reframe + neuer Abschnitt „Stream layouts"), `README.md`
(API-Tabelle), `.env.example` (`REFRAME_GAMING_FACECAM_FRACTION`).

---

## 4. Woran es gescheitert ist — was **nicht** verifiziert ist

### 4.1 Benannte Vorlagen sind nicht ans Dashboard angeschlossen ⚠️ (größte Lücke)
Das war ausdrücklich der Wunsch des Nutzers („damit es auch für andere
Streamer funktioniert") und die Backend-Seite (`config_store.save_stream_layout`
+ `/api/config/stream-layouts`) ist fertig und getestet — aber **kein
Frontend-Code ruft diese Route je auf**. Aktuell verschwindet ein gezeichnetes
Layout, sobald ein neues Projekt gestartet wird, außer man nutzt den
allgemeinen „Als Standard speichern"-Mechanismus (der die *gesamte* Aufnahme-
Rezeptur speichert, nicht gezielt ein benanntes Streamer-Layout). Es gibt also
noch **keinen Weg im Dashboard**, ein Layout unter einem Namen zu sichern und
später aus einer Liste auszuwählen.

### 4.2 Kein einziges echtes Rendering mit den neuen Parametern ⚠️
Alle Tests zu `create_gaming_frame`, `gaming_facecam_fraction`,
`resolve_gameplay_box_from_fractions` etc. sind **reine Python-Host-Tests**
(nackte Zahlen/Arrays, kein cv2, keine echte Videodatei). Es gibt:
- **keinen** `pytest -m integration`-Test, der `create_gaming_frame` mit einer
  echten `gameplay_box`/`facecam_fraction` gegen ein echtes Frame laufen lässt
  (zum Vergleich: `reframe/detect.py` aus der alten Pipeline hat so einen Test,
  dieser neue Code nicht),
- **keinen** tatsächlich durchgerechneten Clip mit `reframe_mode=gaming` +
  gezeichneter Gameplay-Box + „seam"-Hook + Bruchzahl-Untertitel-Position.

Die Docker-Integrationssuite ist in dieser Entwicklungsumgebung nicht
verfügbar (kein Docker) — das ist wie beim vorherigen Handoff eine
Umgebungsgrenze, kein bekannter Fehler, aber eben auch kein Beweis, dass das
gerenderte Bild wirklich so aussieht wie die Vorschau verspricht.

### 4.3 Die 9:16-Vorschau wurde nie in einem echten Browser angesehen
Die Frontend-Tests (`create.test.jsx`, `layoutGeometry.test.js`) laufen in
jsdom — sie bestätigen, dass die richtigen Funktionen mit den richtigen Werten
aufgerufen werden, nicht wie das Ziehen der Kästen/Linien sich tatsächlich
anfühlt oder ob CSS/Layout in einem echten Chrome/Firefox genauso aussieht wie
berechnet. Der Nutzer war beim Ausprobieren im echten Dashboard, als dieses
Dokument angefordert wurde — sein Ergebnis dazu steht noch aus.

### 4.4 Die alte nginx/CLIPPYME_PUBLIC_URL-Einrichtung ist ein Fragment
Vor der Google-Drive-Entscheidung wurde für den Discord-Fallback testweise
eine nginx+certbot-Route auf dem VPS vorbereitet (`clippyme.159-195-219-11.sslip.io`,
`/etc/nginx/sites-available/clippyme-videos`). Ob diese Konfiguration auf dem
VPS tatsächlich noch existiert oder nie fertig eingerichtet wurde, ist
**unbekannt** — die Entscheidung fiel danach auf R2, dann B2, dann Google
Drive, und die nginx-Route wurde nie wieder erwähnt. Falls sie halbfertig
liegt, ist sie harmlos (eigener Serverblock, keine Kollision mit der
bestehenden Seite), aber unaufgeräumt.

### 4.5 Zwei separate Discord-Bot-Instanzen kurzzeitig möglich
Während der Fehlersuche wurde `docker compose --profile discord up -d discordbot`
sowohl auf dem Windows-Entwicklungsrechner des Nutzers als auch (danach) auf
dem VPS ausgeführt, mit vermutlich demselben `DISCORD_BOT_TOKEN`. Beide wurden
am Ende zwar per `docker compose down` auf Windows gestoppt, aber es wurde
**nicht bestätigt**, ob zwischenzeitlich beide Instanzen gleichzeitig liefen
und dadurch Ereignisse doppelt verarbeitet haben könnten.

---

## 5. Der nächste Schritt

In dieser Reihenfolge:

### Schritt 1 — Vorlagen-UI bauen ⭐ (schließt die größte Lücke aus 4.1)
Im `StreamLayoutEditor` (oder direkt im Create-Tab darüber) fehlt:
- Eine Liste vorhandener Vorlagen (`GET /api/config/stream-layouts`) zum
  Auswählen — beim Auswählen füllen sich `facecam`/`gameplay`/`split`/
  `hook_y`/`subtitle_y` in die aktuellen Projekt-Optionen.
- Ein „Als Vorlage speichern"-Knopf (Name eingeben → `POST
  /api/config/stream-layouts` mit den aktuell gezeichneten Werten).
- Ein Löschen-Knopf pro Vorlage (`DELETE .../{layout_id}`).

Backend und Route existieren bereits vollständig und sind getestet — das ist
reine Frontend-Verdrahtung, ähnlich zu `caption_presets` im `PublishModal`
(dort existiert bereits genau dieses Muster: Liste + Speichern + Löschen).

### Schritt 2 — Ein echtes Gaming-Video einmal komplett durchrendern ⭐
Schließt die Lücke aus 4.2/4.3. Im Dashboard (läuft auf dem VPS):
1. Ein kurzes Gaming-Video mit Facecam-Overlay einreichen, Reframe = Gaming.
2. Screenshot hochladen, Facecam **und** Gameplay einzeichnen (bewusst einen
   Screenshot mit Taskbar/Chat-Overlay wählen, um genau den Fall zu prüfen,
   den die alte Zentrierung nicht konnte).
3. Split, Hook-Position („seam"), Untertitel-Position in der Vorschau
   verschieben.
4. Fertigen Clip ansehen: Sitzt die Facecam wie in der Vorschau? Ist das
   Gameplay-Rechteck tatsächlich der gezeichnete Ausschnitt und nicht die alte
   Zentrierung? Sitzt der Hook auf dem Übergang? Sitzen die Untertitel an der
   gezogenen Stelle?

Bei einer Abweichung zwischen Vorschau und echtem Rendering zuerst
`dashboard/src/lib/layoutGeometry.js` gegen `reframe_ops.expand_box_to_aspect`
und `reframe.create_gaming_frame` vergleichen — das ist die einzige Stelle,
an der beide Seiten unabhängig dieselbe Rechnung machen müssten.

### Schritt 3 — Docker-Integrationstest für `create_gaming_frame` ergänzen
Sobald Docker verfügbar ist (auf dem VPS oder lokal mit laufendem Docker
Desktop):
```sh
docker compose run --rm -u root backend sh -lc \
  "pip install -q pytest && pytest -m integration -k gaming"
```
Aktuell gibt es dafür noch keinen Test — einen `pytest.mark.integration`-Test
schreiben, der ein echtes Testframe (numpy-Array oder kleines Testvideo) durch
`create_gaming_frame` mit gesetzter `gameplay_box` schickt und prüft, dass die
Ausgabe-Dimensionen und der Bildausschnitt stimmen.

### Schritt 4 — nginx-Fragment auf dem VPS aufräumen (siehe 4.4)
Prüfen, ob `/etc/nginx/sites-available/clippyme-videos` existiert; falls ja,
entweder fertig einrichten (falls doch noch als zweiter Fallback gewünscht)
oder sauber entfernen (`sudo rm`, `sudo nginx -t`, `sudo systemctl reload nginx`).

### Danach möglich (offen, nicht eingeplant)
- Eine Vorlage direkt beim Anlegen eines Live-Monitors auswählbar machen
  (aktuell nur im Create-Tab verdrahtet)
- Prüfen, ob `_detect_gaming_facecam`s Auto-Erkennung als Startpunkt für eine
  neue Vorlage vorausgefüllt werden könnte, statt komplett neu zu zeichnen
