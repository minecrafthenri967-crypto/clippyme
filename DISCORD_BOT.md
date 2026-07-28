# Discord Bot — Clip-Freigabe vor der Veröffentlichung

Neue Clips landen als Video in einem Discord-Channel. Du reagierst mit ✅ oder ❌
— genehmigte Clips gehen automatisch zu TikTok/Instagram/YouTube.

```
Video verarbeitet (Create-Tab oder Live Monitor)
    ↓
Bot postet den Clip in Discord (Video direkt im Chat)
    ↓
✅ → wird veröffentlicht (mit Untertiteln + Hook eingebrannt)
❌ → passiert nichts, Clip bleibt liegen
```

---

## Einrichtung auf dem Server (netcup)

### 1. Discord-Bot anlegen (einmalig)

1. https://discord.com/developers/applications → **New Application**
2. Links **Bot** → **Add Bot** → **Reset Token** → Token kopieren
3. Bei **Privileged Gateway Intents** einschalten:
   - ✅ **Message Content Intent**
   - ✅ **Server Members Intent**
4. Falls **"Requires OAuth2 Code Grant"** an ist: **ausschalten**
   (sonst kommt beim Einladen „integration requires code grant")
5. Links **General Information** → **Application ID** kopieren

### 2. Bot auf deinen Server einladen

Diese URL öffnen, `DEINE_APP_ID` ersetzen:

```
https://discord.com/oauth2/authorize?client_id=DEINE_APP_ID&permissions=274877959168&scope=bot
```

### 3. Channel vorbereiten

1. In Discord: **User Settings → Advanced → Developer Mode → ON**
2. Channel anlegen, z.B. `#clip-freigabe`
3. Rechtsklick auf den Channel → **ID kopieren**

### 4. Konfiguration anlegen

Auf dem Server, im ClippyMe-Ordner:

```bash
cp .env.discord.example .env.discord
nano .env.discord
```

Nur diese zwei Felder sind zwingend:

```
DISCORD_BOT_TOKEN=dein_token_aus_schritt_1
DISCORD_CHANNEL_ID=deine_channel_id_aus_schritt_3
```

Alles andere hat sinnvolle Standardwerte. `CLIPPYME_API_URL` und
`CLIPPYME_OUTPUT_DIR` musst du **nicht** anfassen — die setzt Docker selbst.

### 5. Starten

```bash
docker compose --profile discord up --build -d
```

Der Bot läuft ab jetzt dauerhaft mit und startet nach einem Server-Neustart
automatisch wieder (`restart: unless-stopped`).

**Logs anschauen:**
```bash
docker compose logs -f discordbot
```

Erwartete Ausgabe:
```
Logged in as ClippyMe Gatekeeper#2642
Watching channel: #clip-freigabe
Clip uploads from: /app/output (up to 10 MB)
Zernio accounts: instagram, tiktok, youtube
Burned in at publish: subtitles, hook
```

**Testen:** Tippe `!ping` im Channel — der Bot antwortet mit „Pong".
Mit `!status` zeigt er seine komplette Konfiguration.

---

## Wichtig: Live Monitor

Wenn ein Live Monitor läuft, muss dessen **eigenes Publishing pausiert bleiben**
— sonst veröffentlicht ClippyMe selbst, bevor du in Discord reagieren konntest.

Beim Starten des Monitors mitgeben:
```json
{ "publishing_enabled": false }
```

Oder nachträglich:
```bash
curl -X POST http://localhost:8000/api/live-monitor/DEINE_MONITOR_ID/publishing \
  -H "Content-Type: application/json" -d '{"enabled": false}'
```

⚠️ **Diese Pause nicht wieder aufheben.** Die pausierten Clips sammeln sich in
einer Warteschlange, die beim Fortsetzen alles nachträglich veröffentlicht —
auch Clips, die du hier schon live gestellt hast (Doppel-Posts).

---

## Einstellungen

Alle in `.env.discord`, danach `docker compose --profile discord up -d --build`.

| Variable | Standard | Bedeutung |
|----------|----------|-----------|
| `DISCORD_BOT_TOKEN` | — | **Pflicht.** Bot-Token aus dem Developer Portal |
| `DISCORD_CHANNEL_ID` | — | **Pflicht.** Channel für die Freigaben |
| `PUBLISH_PLATFORMS` | `tiktok,instagram,youtube` | Wohin genehmigte Clips gehen. Plattformen ohne verbundenes Zernio-Konto werden übersprungen |
| `BURN_SUBTITLES` | `true` | Untertitel beim Veröffentlichen einbrennen |
| `BURN_HOOK` | `true` | Text-Hook einbrennen |
| `BURN_SMARTCUT` | `false` | Stille/Füllwörter entfernen |
| `SUBTITLE_PRESET` | `hormozi_bold` | Untertitel-Stil |
| `MAX_UPLOAD_MB` | `10` | Discord-Limit deines Servers (ohne Boost 10, Stufe 2 = 50, Stufe 3 = 100) |
| `CLIPPYME_PUBLIC_URL` | _(leer)_ | Öffentliche Adresse für zu große Clips, z.B. `https://clips.deinedomain.de` |
| `POLL_SECONDS` | `60` | Wie oft nach neuen Clips geschaut wird |
| `STATS_CHANNEL_ID` | _(leer)_ | Optionaler Channel für „X genehmigt, Y abgelehnt" |

---

## Wie das Video in Discord landet

Drei Stufen, in dieser Reihenfolge:

1. **Direkter Upload** aus `output/` — funktioniert auch, wenn das Backend nur
   auf localhost lauscht. Das Video ist direkt im Chat abspielbar.
2. **Öffentlicher Link** über `CLIPPYME_PUBLIC_URL` — falls der Clip größer ist
   als Discords Limit.
3. **Kein Video**, dafür ein Hinweis, was zu konfigurieren ist.

Ein `localhost`-Link wird nie gepostet: der würde auf den Rechner des
*Betrachters* zeigen, nicht auf den Server, und wäre damit immer tot.

---

## Untertitel & Hooks

ClippyMe rendert Clips zunächst **roh** — Untertitel und Hook werden erst beim
Veröffentlichen eingebrannt. Der Bot schickt deshalb `compose_first` mit.

Das heißt: **die Vorschau in Discord ist der rohe Clip**, der veröffentlichte
hat Untertitel und Hook. Über `BURN_SUBTITLES` / `BURN_HOOK` steuerbar.

---

## Fehlersuche

**Bot startet nicht:**
```bash
docker compose logs discordbot
```
- `DISCORD_BOT_TOKEN is not set` → `.env.discord` fehlt oder ist leer
- `LoginFailure` → Token falsch, im Developer Portal neu generieren

**Bot läuft, aber postet nichts:**
- `WARNING: channel ... not found` → Bot ist nicht auf dem Server (Schritt 2)
- Keine fertigen Clips vorhanden? Prüfe den Verlauf im Dashboard
- Clips schon veröffentlicht? Die werden übersprungen

**„No connected Zernio account":**
- In ClippyMe → Einstellungen → Zernio ein Konto verbinden
- Dann `!zernio` im Channel tippen (lädt neu, ohne Neustart)

**Bot reagiert nicht auf ✅:**
- Message Content Intent im Developer Portal aktiviert?
- Reagierst du im richtigen Channel (`DISCORD_CHANNEL_ID`)?

---

## Ohne Docker starten (Alternative)

```bash
pip install -r discordbot/requirements.txt
python discordbot/bot.py
```

Dann in `.env.discord` zusätzlich setzen:
```
CLIPPYME_API_URL=http://localhost:8000
CLIPPYME_OUTPUT_DIR=output
```

⚠️ Auf einem Server stirbt der Prozess, sobald du die SSH-Verbindung schließt.
Der Docker-Weg oben ist deshalb die bessere Wahl.
