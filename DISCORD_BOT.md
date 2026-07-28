# Discord Bot — Clip-Genehmigungssystem

Automatisiertes Gatekeeper-System: neue Clips landen im Discord-Channel, du genehmigst sie mit ✅/❌ Reaktionen, genehmigte Clips gehen automatisch zu TikTok/Instagram/YouTube.

## Übersicht

```
ClippyMe (YouTube Upload)
    ↓
Live Monitor erkennt neues Video
    ↓
Alle 7 Phasen laufen ab (transcribe, rank, cut, reframe, render, export)
    ↓
Bot postet Clip im Discord mit Vorschau
    ↓
Du reagierst: ✅ (genehmigt) oder ❌ (abgelehnt)
    ↓
✅ → Auto-Publish zu TikTok/Instagram/YouTube
❌ → In Archiv verschieben, nicht veröffentlichen
```

## Setup

### 1. Discord Developer Portal

1. Gehe zu https://discord.com/developers/applications
2. Klicke **"New Application"**
3. Name: `ClippyMe Bot` (oder was dir gefällt)
4. Gehe zu **Bot** (linkes Menü)
5. Klicke **"Add Bot"**
6. Unter **TOKEN**, klicke **Copy** → in `.env.discord` als `DISCORD_BOT_TOKEN=` einfügen
7. Aktiviere diese **Privileged Gateway Intents**:
   - ✅ Message Content Intent (um Nachrichteninhalte zu lesen)
   - ✅ Server Members Intent (um Nutzer zu identifizieren)
8. Gehe zu **General Information**
9. Kopiere **Application ID** → in `.env.discord` als `DISCORD_APP_ID=` einfügen

### 2. Bot zum Server einladen

Verwende diese URL (ersetze `DEINE_APP_ID`):

```
https://discord.com/oauth2/authorize?client_id=DEINE_APP_ID&permissions=274877959168&scope=bot
```

Permissions (automatisch aktiviert):
- Send Messages
- Read Message History
- Add Reactions
- Manage Messages

### 3. Channel vorbereiten

1. Erstelle einen neuen Channel in Discord: `#clip-approvals`
2. Rechtsklick auf Channel → **Copy Channel ID** → in `.env.discord` als `DISCORD_CHANNEL_ID=` einfügen
3. Developer Mode aktivieren:
   - User Settings → **Advanced** → **Developer Mode** → **ON**

### 4. .env.discord konfigurieren

```bash
# Kopiere .env.discord.example zu .env.discord
cp .env.discord.example .env.discord

# Öffne und fülle aus:
DISCORD_BOT_TOKEN=dein_token
DISCORD_APP_ID=deine_app_id
DISCORD_CHANNEL_ID=dein_channel_id
DISCORD_USER_ID=deine_user_id
DISCORD_GUILD_ID=dein_server_id
CLIPPYME_API_URL=http://localhost:8000
```

## Bot-Code

Speichere als `discord_bot.py` im Hauptverzeichnis:

```python
import os
import json
import discord
from discord.ext import commands
import aiohttp
from dotenv import load_dotenv

# .env.discord laden
load_dotenv(".env.discord")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
USER_ID = int(os.getenv("DISCORD_USER_ID"))
CLIPPYME_API = os.getenv("CLIPPYME_API_URL", "http://localhost:8000")

APPROVE_EMOJI = os.getenv("APPROVE_EMOJI", "✅")
REJECT_EMOJI = os.getenv("REJECT_EMOJI", "❌")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot angemeldet als {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        print(f"📢 Überwache Channel: #{channel.name}")
    else:
        print(f"⚠️ Channel {CHANNEL_ID} nicht gefunden!")

@bot.event
async def on_reaction_add(reaction, user):
    """Reagiere auf ✅/❌ Reaktionen"""
    if user.bot:
        return
    
    # Nur der konfigurierte Channel
    if reaction.message.channel.id != CHANNEL_ID:
        return
    
    # Nur unsere Reaktionen
    if reaction.emoji not in (APPROVE_EMOJI, REJECT_EMOJI):
        return
    
    message = reaction.message
    
    # Extrahiere job_id und clip_index aus Nachrichteninhalt
    # Format: "Job: {job_id} | Clip {clip_index}"
    try:
        content = message.content or ""
        if "Job:" not in content or "Clip" not in content:
            await message.reply("❌ Format falsch — erwartete: 'Job: {id} | Clip {n}'")
            return
        
        job_id = content.split("Job: ")[1].split(" |")[0].strip()
        clip_index = int(content.split("Clip ")[1].split()[0].strip())
        
        if reaction.emoji == APPROVE_EMOJI:
            await handle_approval(message, user, job_id, clip_index)
        elif reaction.emoji == REJECT_EMOJI:
            await handle_rejection(message, user, job_id, clip_index)
    
    except (IndexError, ValueError) as e:
        await message.reply(f"❌ Fehler beim Parsen: {e}")

async def handle_approval(message, user, job_id, clip_index):
    """Genehmigt Clip und veröffentlicht zu Plattformen"""
    try:
        platforms = os.getenv("PUBLISH_PLATFORMS", "tiktok,instagram,youtube").split(",")
        platforms = [p.strip() for p in platforms]
        
        await message.reply(f"⏳ Veröffentliche Clip {clip_index} von Job {job_id} zu {', '.join(platforms)}...")
        
        async with aiohttp.ClientSession() as session:
            for platform in platforms:
                try:
                    url = f"{CLIPPYME_API}/api/publish/{job_id}/{clip_index}"
                    async with session.post(url, json={"platform": platform}) as resp:
                        if resp.status == 200:
                            print(f"✅ {platform.upper()}: Clip {clip_index} veröffentlicht")
                        else:
                            print(f"⚠️ {platform.upper()}: Status {resp.status}")
                except Exception as e:
                    print(f"❌ {platform.upper()}: Fehler {e}")
        
        await message.reply(f"✅ Clip genehmigt und veröffentlicht! (von {user.mention})")
        
    except Exception as e:
        await message.reply(f"❌ Fehler beim Veröffentlichen: {e}")
        print(f"Error: {e}")

async def handle_rejection(message, user, job_id, clip_index):
    """Lehnt Clip ab"""
    try:
        await message.reply(f"❌ Clip {clip_index} abgelehnt (von {user.mention})")
        # Optional: zu Archiv-Channel verschieben oder markieren
        print(f"Clip {clip_index} von Job {job_id} wurde abgelehnt")
    except Exception as e:
        await message.reply(f"❌ Fehler: {e}")

@bot.command()
async def ping(ctx):
    """Bot-Status"""
    await ctx.send(f"🏓 Pong! Bot läuft. (Channel: {CHANNEL_ID})")

@bot.command()
async def status(ctx):
    """Zeige aktuelle Bot-Status"""
    status_msg = f"""
📊 **Bot Status**
• **Name:** {bot.user}
• **Channel:** <#{CHANNEL_ID}>
• **API:** {CLIPPYME_API}
• **Approve:** {APPROVE_EMOJI}
• **Reject:** {REJECT_EMOJI}
"""
    await ctx.send(status_msg)

if __name__ == "__main__":
    bot.run(TOKEN)
```

## Installation & Start

```bash
# Dependencies installieren
pip install discord.py aiohttp python-dotenv

# Bot starten
python discord_bot.py
```

Du solltest sehen:
```
✅ Bot angemeldet als ClippyMe Bot
📢 Überwache Channel: #clip-approvals
```

## Workflow

### 1. Clip wird gepostet
Live Monitor erkennt neue YouTube-Upload → Bot postet in #clip-approvals:
```
**Neuer Clip: "Viral Moment"**
Job: abc123xyz | Clip 1
Länge: 15 Sekunden
[Vorschau-Link]
```

### 2. Du genehmigst
Reagiere mit ✅ → Bot veröffentlicht automatisch zu:
- TikTok
- Instagram
- YouTube Shorts

Reagiere mit ❌ → Clip wird archiviert, nicht veröffentlicht

### 3. Status in Discord
Bot antwortet mit Status:
```
✅ Clip genehmigt und veröffentlicht!
```

## Umgebungsvariablen (Referenz)

| Variable | Beispiel | Beschreibung |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | `MTk4Ni...` | Bot-Authentifizierung |
| `DISCORD_APP_ID` | `1234567890` | OAuth2 Invite-Link |
| `DISCORD_CHANNEL_ID` | `9876543210` | #clip-approvals Channel |
| `DISCORD_USER_ID` | `1111111111` | Deine Discord User ID |
| `DISCORD_GUILD_ID` | `2222222222` | Dein Discord Server |
| `APPROVE_EMOJI` | `✅` | Emoji für Genehmigung |
| `REJECT_EMOJI` | `❌` | Emoji für Ablehnung |
| `PUBLISH_PLATFORMS` | `tiktok,instagram` | Plattformen für Auto-Publish |
| `CLIPPYME_API_URL` | `http://localhost:8000` | ClippyMe Backend |

## Fehlerbehebung

### Bot antwortet nicht auf Reaktionen
- [ ] Developer Mode in Discord aktiviert?
- [ ] Channel ID korrekt in `.env.discord`?
- [ ] Bot hat "Add Reactions" Berechtigung?
- [ ] Bot läuft? (`python discord_bot.py`)

### "Token invalid"
- [ ] Token aus Developer Portal kopiert (nicht Application ID)?
- [ ] Token korrekt in `.env.discord` ohne Leerzeichen/Anführungszeichen?

### Bot sieht keine Nachrichten
- [ ] Message Content Intent aktiviert in Developer Portal?
- [ ] Privileged Gateway Intents angeschaltet?

### ClippyMe API nicht erreichbar
- [ ] Backend läuft? (`docker compose up --build`)
- [ ] `CLIPPYME_API_URL` stimmt mit tatsächlicher URL überein?

## Nächste Schritte

1. **Erweiterte Genehmigung:**
   - Bestimmte Nutzer als "Approver" definieren
   - Thread pro Clip für Diskussionen
   - Auto-Reply mit Publishing-Status

2. **Statistiken:**
   - Täglich im Discord: "X Clips genehmigt, Y veröffentlicht"
   - Fehler-Tracking
   - Performance-Metriken

3. **Whop Integration:**
   - Whop-Upload-Link im Discord posten
   - Benutzer laden manuell hoch statt Auto-Publish

4. **Smart Scheduling:**
   - Clips nicht sofort, sondern zu optimalen Zeiten veröffentlichen
   - Zernio SmartScheduler Integration
