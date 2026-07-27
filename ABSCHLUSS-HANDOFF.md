# Abschluss-Handoff — AI-Clipper Pro

Stand: 27.07.2026 · Branch `claude/ai-clipper-pro-pipeline-ze09sn` · Commit `c659d9f`
· PR [#1](https://github.com/minecrafthenri967-crypto/clippyme/pull/1) (offen, nicht gemerged)

Dieses Dokument beschreibt den Übergabestand: was gebaut wurde, was davon
nachweislich funktioniert, was **nicht** verifiziert ist, und was als Nächstes
zu tun ist. Es ist bewusst ehrlich bei den Lücken — die offenen Punkte in
Abschnitt 4 sind der wichtigste Teil.

---

## 1. Das Ziel — was gebaut werden soll

Ein **eigenständiges Programm namens „AI-Clipper Pro"**, das aus einem langen
Video (YouTube-Link oder lokale Datei) automatisch fertige, hochkant-formatige
Kurzvideos (9:16, für TikTok / Reels / Shorts) erzeugt — inklusive Untertitel,
Text-Hook und Bewertung, welcher Clip das meiste Potenzial hat.

Es lebt im selben Repository wie ClippyMe, ist aber ein **separates Paket**
(`src/clipper_pro/`). Es importiert weder die FastAPI-App noch die Job-Verwaltung
von ClippyMe. Die einzige geteilte Nutzung sind reine Rechenmodule von ClippyMe
(z. B. die Kantenschnitt-Mathematik in `cut_ops.py`), damit dieselbe Logik nicht
zweimal existiert und auseinanderläuft.

### Die sieben Phasen

| # | Phase | Was sie tut |
|---|-------|-------------|
| 1 | `ingest` | Video herunterladen, Audiospur als mono 16 kHz FLAC extrahieren |
| 2 | `transcribe` | Wortgenaues Transkript inkl. Sprecher-Zuordnung (Deepgram Nova-3 **oder** ElevenLabs Scribe) |
| 3 | `rank` | Beste Momente nach einem 5-Achsen-Viralitäts-Raster bewerten (DeepSeek **oder** Gemini); liefert auch den Text-Hook mit |
| 4 | `cut` | Clip-Ränder sauber auf Wort-, Satz- und Stille-Grenzen schnappen |
| 5 | `reframe` | Kamerafahrt für den 9:16-Ausschnitt planen (folgt dem Sprecher, 200 ms Vorlauf, geglättet) |
| 6 | `render` | Jeden Clip in **einem** ffmpeg-Durchlauf rendern — inkl. eingebrannter Karaoke-Untertitel und Text-Hook |
| 7 | `export` | Bewerteten Entwurfs-Report als JSON + Markdown schreiben (für den manuellen Feinschliff in CapCut) |

### Zwei zusätzliche Bedienoberflächen (keine Phasen)

- **`clipper-pro web`** — lokale Weboberfläche (nur localhost), um einen Lauf per
  Browser zu starten und die Clips anzusehen.
- **`clipper-pro watch`** — **Kanal-Beobachter**. Fragt YouTube-Kanäle regelmäßig
  nach neuen Uploads und fährt bei jedem neuen Video die Phasen 1–7 durch. Gedacht
  zum Laufenlassen über Tage, ohne dass jemand danebensitzt.

### Die drei Kostenschutz-Regeln des Beobachters

Weil der Beobachter unbeaufsichtigt läuft und jede Phase 2/3 echtes Geld kostet,
sind drei Regeln fest eingebaut (`watch/state_ops.py`, rein rechnerisch, voll getestet):

1. **Erster Kontakt mit einem Kanal verarbeitet nichts.** Die ~15 Videos, die
   beim ersten Blick schon im Feed stehen, werden nur als „gesehen" vermerkt.
   Sonst würde ein frisch gestarteter Beobachter sofort 15 Videos abrechnen.
   Wer das ausdrücklich will, setzt `--catchup backfill`.
2. **Ein fehlschlagendes Video wird höchstens `max_attempts`-mal (Standard 3)
   erneut versucht**, danach in Ruhe gelassen. Sonst würde ein dauerhaft kaputtes
   Video bei jedem Poll erneut Geld kosten.
3. **Phasen, die im Manifest des Laufs schon eingetragen sind, werden übersprungen.**
   Ein Wiederholungsversuch nach einem Render-Fehler zahlt Transkription und
   Ranking **nicht** ein zweites Mal.

Der Beobachter **veröffentlicht bewusst nichts**. Sein Ergebnis ist ein fertiger
Arbeitsordner pro Video; was davon rausgeht, entscheidet ein separater Schritt.

---

## 2. Der aktuelle Stand — wo es steht

**Alle sieben Phasen sind implementiert**, dazu die Weboberfläche und der
Kanal-Beobachter. Der Code ist fertig, getestet und gepusht.

| Nachweis | Ergebnis |
|---|---|
| Testsuite `tests/clipperpro/` | **810 Tests, alle grün** (ca. 8 Sek.) |
| Gesamte Backend-Testsuite | 1930 Tests grün |
| Linter (CI-Regelsatz `E9,F63,F7,F82`) | sauber |
| CI auf PR #1 | **alle 4 Jobs grün** (Backend, Frontend, Frontend-Audit, Docker-Integration) |
| Merge-Status PR #1 | `clean` — kein Konflikt mit `main` |
| Umfang | 73 Dateien, 14.543 Zeilen, 12 Commits |

### Was echt (nicht simuliert) verifiziert wurde

- **ffmpeg-Pfade**: echter 48 kHz → 16 kHz Downmix, echte Stille-Erkennung, die
  den Kantenschnitt steuert, echter Ein-Durchlauf-Render, der eine abspielbare
  1080×1920 h264/AAC-Datei erzeugt.
- **Kompletter 7-Phasen-Durchlauf** von Anfang bis Ende — mit simulierten
  Netzwerkantworten für Phase 2 und 3, aber echtem ffmpeg für alles andere.
  Ergebnis: zwei tatsächlich gerenderte Clips plus Entwurfs-Report.

### Der Einstiegspunkt

```
clipper-pro {ingest,transcribe,rank,cut,reframe,render,export,web,watch}
```

---

## 3. Die Dateien — woran gearbeitet wurde

47 Python-Module unter `src/clipper_pro/`. Die wichtigsten:

### Kern / Gerüst
| Datei | Rolle |
|---|---|
| `pipeline.py` | **Zentrale Orchestrierung.** `run_phase(phase, work_dir, source=, options=)`. CLI, Web-UI und Beobachter sind alle nur dünne Übersetzer darüber — damit sie nicht auseinanderdriften. |
| `cli.py` | Kommandozeile → `PhaseOptions` |
| `workspace.py` | Arbeitsordner + Manifest (jede Phase trägt ihr Ergebnis ein → Wiederaufsetzpunkt) |
| `types.py` | Die Datenverträge zwischen den Phasen |
| `config.py` | Alle `CLIPPER_PRO_*`-Umgebungsvariablen |
| `errors.py` | Fehlerklassen + Exit-Codes |

### Die Phasen
| Ordner | Inhalt |
|---|---|
| `ingest/` | `source.py` (Download), `audio.py` (FLAC-Extraktion), `*_ops.py` (reine Logik) |
| `transcribe/` | `providers.py` (Deepgram/ElevenLabs), `base.py` (Fähigkeiten pro Anbieter), `cache.py`, `normalize_ops.py` |
| `rank/` | `providers.py` (DeepSeek/Gemini), `rubric_ops.py` (5-Achsen-Raster), `cache.py` (SQLite-Prompt-Cache) |
| `cut/` | `snap_ops.py` — dünner Adapter auf ClippyMes `cut_ops.snap_clips_to_transcript`, **keine Kopie der Mathematik** |
| `reframe/` | `speaker_ops.py`, `camera_ops.py`, `plan.py` (alle rein) + `detect.py` (**einziges Modul mit cv2/MediaPipe**) |
| `render/` | `filtergraph_ops.py` (crop-Ausdruck), `captions.py` + `captions_ops.py`, `hooks.py` + `hooks_ops.py` |
| `export/` | `report_ops.py` — JSON + Markdown aus einem gemeinsamen Dokument |

### Die Treiber
| Ordner | Inhalt |
|---|---|
| `web/` | `app.py` (Routen), `runs.py` (reiner Lauf-Zustand), `worker.py` (Hintergrund-Thread), `static/index.html` |
| `watch/` | `state_ops.py` (**die drei Kostenregeln, rein + getestet**), `state.py` (atomares Speichern, 0o600), `feed.py` (YouTube-Feed), `__init__.py` (die Schleife) |

### Grundregel im ganzen Paket
Module mit der Endung `*_ops.py` sind **stdlib-only** und laufen ohne schwere
Abhängigkeiten in der schnellen Testsuite. Die Module daneben machen die
Ein-/Ausgabe. Deshalb sind 810 Tests in 8 Sekunden durch.

### Berührte Dateien außerhalb von `src/clipper_pro/`
- `tests/clipperpro/` — 22 Testdateien (Ordner bewusst ohne Unterstrich geschrieben,
  damit das Testpaket das echte Paket nicht überdecken kann)
- `.env.example` — alle `CLIPPER_PRO_*`-Schalter dokumentiert (Zeilen 197–289)
- `CLAUDE.md` — Architektur-Beschreibung für künftige Arbeit am Code
- `pyproject.toml` — Einstiegspunkt `clipper-pro`

---

## 4. Woran es gescheitert ist — was **nicht** verifiziert ist

Das hier ist der ehrliche Teil. Der Code ist vollständig, aber diese Dinge sind
in dieser Umgebung **nie gegen die Realität gelaufen**:

### 4.1 Es wurde noch nie ein echter API-Aufruf gemacht ⚠️ (größtes Restrisiko)
Deepgram, ElevenLabs, DeepSeek und Gemini sind in **allen** Tests simuliert.
Phase 2 und Phase 3 haben noch nie mit einem echten Dienst gesprochen. Das
bedeutet konkret: Authentifizierung, Antwortformat und Fehlerverhalten der echten
Dienste sind **ungeprüft**. Wenn beim ersten echten Lauf etwas bricht, dann mit
hoher Wahrscheinlichkeit hier.

### 4.2 Kein echter YouTube-Download, kein echter Feed-Abruf
Die Sandbox, in der entwickelt wurde, blockiert ausgehende Verbindungen zu
YouTube (Proxy antwortet mit `403 CONNECT`). Deshalb:
- Phase 1 wurde nie gegen eine echte YouTube-URL ausgeführt (der ffmpeg-Teil
  dagegen schon, mit lokalen Dateien).
- Der Feed-Abruf des Beobachters (`watch/feed.py`) wurde nie gegen den echten
  YouTube-Feed ausgeführt. Die Zustandslogik dahinter ist voll getestet, der
  Netzwerkzugriff selbst nicht.

**Das ist eine Umgebungsgrenze, kein bekannter Fehler** — aber eben auch kein Beweis,
dass es funktioniert.

### 4.3 `reframe/detect.py` — inzwischen getestet, und dabei drei echte Fehler gefunden
**Erledigt.** Das Modul ist jetzt abgedeckt (`tests/clipperpro/test_reframe_detect.py`,
29 Host-Tests + `test_reframe_detect_integration.py`, 5 Docker-Tests). Der Test
hat dabei bewiesen, dass die Sprecher-Verfolgung **gar nicht lief** — drei Fehler,
die keiner der 810 vorherigen Tests bemerken konnte, weil alle `locate=`
explizit übergaben und den echten Pfad damit umgingen:

1. `detect.py` importierte `detect_faces` — diese Funktion existiert nicht. Sie
   heißt `detect_face_candidates`. Jeder Aufruf lief in einen `ImportError`.
2. `detect_face_candidates` liefert `[{"box": [x,y,w,h], "score": …}]`, der Code
   zerlegte das Ergebnis aber als 4er-Sequenz (`box[:4]`) → `TypeError` auf einem
   Dict.
3. Der `ImportError`-Schutz in `_default_locator` umschloss den **Import** statt
   den **Aufruf**. Da `detect.py` cv2 erst im Funktionskörper importiert, griff
   der Schutz nie — statt „mittiger Ausschnitt" stürzte Phase 5 ab.

Alle drei sind behoben und durch Regressionstests abgesichert (jeder Fix wurde
gegengeprüft: Fehler wieder eingebaut → Test schlägt fehl).

Was weiterhin **nicht** verifiziert ist: die *Qualität* der Verfolgung. Die
Docker-Tests prüfen den Vertrag (Namen, Datenformat, Seek-Verhalten von echtem
cv2), nicht ob die Kamera das richtige Gesicht wählt — dafür bräuchte es
Testmaterial mit echten Gesichtern. Und die Docker-Tests selbst sind in dieser
Umgebung **nie gelaufen** (kein Docker verfügbar); sie sind geschrieben, aber
unausgeführt.

### 4.4 Es existiert noch keine `.env`
Im Projekt liegt nur `.env.example`. Ohne kopierte und ausgefüllte `.env` kann
kein echter Lauf starten. (Siehe Schritt 2 unten.)

### 4.5 Der PR ist gemerged
**Erledigt.** PR #1 wurde am 27.07.2026 nach `main` gemerged (Merge-Commit
`b222350`). Schritt 1 in Abschnitt 5 ist damit hinfällig.

### 4.6 Kleinigkeit: 3 kosmetische Linter-Hinweise
`ruff` mit seinem vollen Standard-Regelsatz meldet **3-mal** `RUF046`
(überflüssiges `int()` um ein bereits ganzzahliges `round()`) in `src/clipper_pro`.
**Der CI-Regelsatz des Projekts ist sauber** — das sind Stilhinweise, keine
Fehler. Aufräumen ist optional. (Die frühere Angabe „8" war zu hoch.)

### 4.7 Bewusst nicht gebaut
- **Veröffentlichen aus dem Beobachter heraus** — absichtlich ausgelassen. Ein
  unbeaufsichtigter Prozess, der selbstständig postet, ist eine andere
  Risikoklasse als einer, der nur Dateien schreibt.
- **Discord-Torwächter** (war als möglicher Schritt 3 im Gespräch) — nie begonnen.
- **Skaffold / Kubernetes / Cloud-Deployment** aus dem ursprünglichen „Masterplan"
  — nach Prüfung verworfen, weil im Repository nichts davon existiert und der
  Nutzen den Aufwand hier nicht rechtfertigt.

---

## 5. Der nächste Schritt

In dieser Reihenfolge:

### Schritt 1 — ~~PR #1 mergen~~ ✅ erledigt
PR #1 ist gemerged (siehe 4.5). Weiter mit Schritt 2.

**Zusätzlich empfohlen:** die neuen Docker-Tests einmal wirklich ausführen —
sie sind geschrieben, aber in der Entwicklungsumgebung nie gelaufen:
```sh
docker compose run --rm -u root backend sh -lc \
  "pip install -q pytest && pytest -m integration -k reframe_detect"
```
Das prüft die Sprecher-Verfolgung gegen echtes cv2/MediaPipe — genau die Stelle,
an der die drei Fehler aus 4.3 saßen.

### Schritt 2 — `.env` anlegen und Schlüssel eintragen
```sh
cd clippyme
cp .env.example .env
```
Dann in der **neuen `.env`** (nicht in `.env.example`) diese Zeilen ausfüllen —
und bei den auskommentierten Zeilen das führende `#` **entfernen**:

```sh
GEMINI_API_KEY=...            # https://aistudio.google.com/apikey
DEEPGRAM_API_KEY=...          # https://console.deepgram.com
DEEPSEEK_API_KEY=...          # https://platform.deepseek.com
CLIPPER_PRO_RANKER=deepseek   # oder: gemini
```
`.env` steht in `.gitignore` (Zeile 42) — die Schlüssel landen also nicht im Repository.

### Schritt 3 — Ein einziges kurzes Video von Hand durchfahren ⭐
**Das ist der eigentlich wichtige Schritt**, weil er genau die Lücken aus
Abschnitt 4.1 und 4.2 schließt. Ein *kurzes* Video wählen (5–10 Minuten), damit
der erste echte Test wenig kostet:

```sh
clipper-pro ingest     --work-dir ./testlauf "<YOUTUBE-URL>"
clipper-pro transcribe --work-dir ./testlauf
clipper-pro rank       --work-dir ./testlauf
clipper-pro cut        --work-dir ./testlauf
clipper-pro reframe    --work-dir ./testlauf
clipper-pro render     --work-dir ./testlauf --captions --hooks
clipper-pro export     --work-dir ./testlauf
```
Phasenweise, nicht alles auf einmal — dann sieht man sofort, **welche** Phase
bricht. Danach die Clips in `./testlauf/` ansehen: Sitzt der Ausschnitt? Sind die
Untertitel lesbar? Passt der Hook?

### Schritt 4 — Den Beobachter genau einmal probelaufen lassen
Erst wenn Schritt 3 sauber durchlief:
```sh
clipper-pro watch --channel "@einkanal" --runs-dir ~/clipper-pro-runs --once
```
`--once` heißt: einmal abfragen, dann beenden. Beim **allerersten** Lauf auf einem
Kanal wird erwartungsgemäß **nichts verarbeitet** — er merkt sich nur den Bestand
(Regel 1 aus Abschnitt 1). Das ist kein Fehler, das ist der Kostenschutz.
Ab dem zweiten Aufruf wird jedes wirklich neue Video verarbeitet.

Erst danach das `--once` weglassen und dauerhaft laufen lassen.

### Danach möglich (offen, nicht eingeplant)
- Veröffentlichungs-Schritt hinter den Beobachter hängen (mit menschlicher Freigabe)
- Discord-Torwächter
- `reframe/detect.py` mit einem Docker-Integrationstest absichern
- Die 8 `RUF046`-Hinweise aufräumen
