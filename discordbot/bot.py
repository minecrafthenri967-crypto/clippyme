"""ClippyMe Discord gatekeeper — approve clips with a reaction before they publish.

Polls ``GET /api/history`` for finished, not-yet-published clips and posts each
one to a Discord channel. Reacting with the approve emoji publishes it through
``POST /api/publish/{job}/{clip}``; the reject emoji leaves it alone. A third,
download emoji (DOWNLOAD_EMOJI) replies with the FULL-quality file — the
posted preview is deliberately shrunk to fit Discord's upload limit, so it is
never what you want to actually save.

Runs as a compose service (``docker compose up``) alongside the backend, or
standalone with ``python discordbot/bot.py``. Configuration comes from the
environment either way — compose injects it via ``env_file: .env.discord``, and
the standalone path loads that same file directly.

HOW THE VIDEO REACHES DISCORD (four tiers, in order):
  1. Compose (subtitles/hook — same recipe as publish, see below) via
     ``POST /api/compose``, then ffmpeg-shrink that result (DISCORD_PREVIEW_*)
     purely so it fits Discord's upload limit; the publish step re-composes at
     full quality from the untouched source, so the shrink never touches what
     actually gets uploaded to TikTok/YouTube. If nothing is configured to
     burn in at all (BURN_SUBTITLES/BURN_HOOK/BURN_SMARTCUT all off), the raw
     clip already IS the final look and is posted directly. Otherwise, a
     compose or shrink failure does NOT fall back to the raw clip — Discord
     approval must preview exactly what would publish, so the clip is
     retried on the next poll (POLL_SECONDS apart) up to
     DISCORD_COMPOSE_MAX_ATTEMPTS attempts. After the cap, one failure notice
     is posted (no video, nothing to approve) and the clip is marked handled
     so it stops retrying.
  2. A Google Drive link, for clips over Discord's limit, when
     GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE + GOOGLE_DRIVE_FOLDER_ID are set (see
     the constants below for the one-time Google Cloud setup). Preferred over
     CLIPPYME_PUBLIC_URL because it needs no public exposure of the backend.
  3. A public link via CLIPPYME_PUBLIC_URL, for clips over Discord's limit,
     when Drive isn't configured.
  4. No video, plus a message saying exactly what to configure.
A localhost URL is never posted: it would resolve on the *viewer's* machine,
so it is always dead. Requires ffmpeg in this container's image (the backend
does every real render/compose pass — this is only for the shrink step).

CAPTIONS AND HOOKS: the pipeline renders clips raw and burns overlays at
publish time, so both the preview compose and the publish body send
``compose_first`` with the toggles below — otherwise a clip reaches TikTok
with no subtitles, and Discord shows something different from what publishes.

LIVE MONITOR: if one is running for the same channel, keep its publishing
paused (``POST /api/live-monitor/{id}/publishing {"enabled": false}``, or start
it with ``publishing_enabled: false``). Resuming it later drains a queue that
would re-publish clips already approved here.
"""

import asyncio
import json
import os
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Optional: only needed for the Google Drive oversized-clip fallback. Guarded
# so a standalone run (bare `python discordbot/bot.py`, per this module's own
# docstring) without `pip install google-auth` still starts — Drive support
# just degrades to unavailable rather than crashing the whole bot.
try:
    from google.auth.transport.requests import Request as _GoogleAuthRequest
    from google.oauth2 import service_account as _google_service_account
    _GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    _GOOGLE_AUTH_AVAILABLE = False

# Compose supplies these via env_file; standalone runs read the file directly.
# Never overrides an already-set variable, so compose always wins.
load_dotenv(".env.discord")


def _flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = _int("DISCORD_CHANNEL_ID", 0)
STATS_CHANNEL_ID = _int("STATS_CHANNEL_ID", 0)

# Where the bot talks to the backend. Inside compose this is the service name
# (http://backend:8000); standalone on the same host it is localhost. Either
# way this value is for API calls only and never appears in a Discord message.
CLIPPYME_API = os.getenv("CLIPPYME_API_URL", "http://localhost:8000")
# Publicly reachable address of the SAME instance, e.g. https://clips.example.de
# — only used for clickable links. Leave empty if the backend is not exposed.
CLIPPYME_PUBLIC_URL = (os.getenv("CLIPPYME_PUBLIC_URL", "") or "").rstrip("/")
# Google Drive fallback for clips too large for Discord's upload limit —
# preferred over CLIPPYME_PUBLIC_URL since it needs no public exposure of the
# backend. One-time setup (Google Cloud Console): create a project, enable
# the Drive API, create a service account, download its JSON key, drop it
# into this bot's state directory (the same volume as DISCORD_STATE_FILE —
# e.g. data/discordbot/service_account.json on the host), then share a
# Drive folder with the service account's email (Editor access) and put that
# folder's ID here. Both must be set for Drive uploads to be attempted.
GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE = (os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "") or "").strip()
GOOGLE_DRIVE_FOLDER_ID = (os.getenv("GOOGLE_DRIVE_FOLDER_ID", "") or "").strip()
_GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# Where finished clips live (ClippyMe's output/). Enables direct upload.
CLIPPYME_OUTPUT_DIR = os.getenv("CLIPPYME_OUTPUT_DIR", "output")
# Must match the backend's PUBLISH_GATE_TOKEN when that's set. That gate
# refuses every /api/publish call without this header — including the
# dashboard's own Publish button and Live Monitor auto-publish — so this bot
# becomes the only path that can actually publish a clip. Leave both unset to
# keep publishing open to any caller, as before this gate existed.
PUBLISH_GATE_TOKEN = (os.getenv("PUBLISH_GATE_TOKEN", "") or "").strip()
# Which named Zernio account this bot instance publishes through (see
# storage.config_store's profile namespace on the backend). "default"
# reproduces the single-account behavior every install had before profiles
# existed. A second campaign (e.g. eBay Live) is run as a SECOND bot
# container with its own .env.discord (own channel + ZERNIO_PROFILE) rather
# than one bot juggling multiple profiles/channels. Also the filter
# poll_new_clips applies to GET /api/history: a job's zernioProfile (set at
# submission, see job_artifacts.save_job_campaign) must match this bot's own
# profile, or its clips are skipped — otherwise every bot would post every
# clip regardless of which campaign it belongs to.
ZERNIO_PROFILE = (os.getenv("ZERNIO_PROFILE", "") or "default").strip().lower() or "default"
# Discord's per-server upload limit: 10 MB unboosted, 50 at level 2, 100 at 3.
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 10)
# The composed (subtitles/hook burned in) file is re-encoded at THIS size for
# the Discord preview only — a 2K/4K source composes well past Discord's
# limit, and the actual publish upload is untouched, always full quality.
DISCORD_PREVIEW_MAX_HEIGHT = _int("DISCORD_PREVIEW_MAX_HEIGHT", 960)
DISCORD_PREVIEW_CRF = _int("DISCORD_PREVIEW_CRF", 30)

APPROVE_EMOJI = os.getenv("APPROVE_EMOJI", "✅")
REJECT_EMOJI = os.getenv("REJECT_EMOJI", "❌")
# Reacting with this sends the FULL-quality file (no Discord-preview shrink)
# as a fresh reply — for saving the clip (e.g. a phone's "Save Video" to the
# camera roll) at the same quality that would actually publish, since the
# message already posted for approval is deliberately shrunk to fit Discord's
# upload limit.
DOWNLOAD_EMOJI = os.getenv("DOWNLOAD_EMOJI", "📥")
PUBLISH_PLATFORMS = [
    p.strip().lower()
    for p in os.getenv("PUBLISH_PLATFORMS", "tiktok,instagram,youtube").split(",")
    if p.strip()
]
POLL_SECONDS = max(15, _int("POLL_SECONDS", 60))
# How many poll cycles (POLL_SECONDS apart) a clip's compose is retried
# before giving up: Discord approval previews exactly what would publish, so
# a compose failure is retried rather than falling back to an incomplete raw
# clip. After this many attempts a single failure notice is posted instead of
# retrying forever.
DISCORD_COMPOSE_MAX_ATTEMPTS = max(1, _int("DISCORD_COMPOSE_MAX_ATTEMPTS", 10))
TIMEZONE = os.getenv("PUBLISH_TIMEZONE", "Europe/Rome")

BURN_SUBTITLES = _flag("BURN_SUBTITLES", True)
BURN_HOOK = _flag("BURN_HOOK", True)
BURN_SMARTCUT = _flag("BURN_SMARTCUT", False)
SUBTITLE_PRESET = os.getenv("SUBTITLE_PRESET", "hormozi_bold")
SUBTITLE_POSITION = os.getenv("SUBTITLE_POSITION", "bottom")
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Montserrat-Black")
HOOK_POSITION = os.getenv("HOOK_POSITION", "top")

# Which (job, clip) pairs were already posted. Kept on a mounted volume in
# compose so a container restart does not re-post the whole backlog.
STATE_FILE = os.getenv(
    "DISCORD_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_posted.json"),
)

if not TOKEN:
    raise SystemExit("DISCORD_BOT_TOKEN is not set (check .env.discord)")
if not CHANNEL_ID:
    raise SystemExit("DISCORD_CHANNEL_ID is not set (check .env.discord)")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

_stats = {"approved": 0, "rejected": 0}
# Zernio account ids, fetched at startup. Publishing needs one per platform.
_zernio_accounts: dict = {}
# Title/hook text per posted clip, so approving does not re-query the history.
_clip_meta: dict = {}
# Consecutive compose-failure count per (job_id, idx), so a clip is retried
# on the next poll instead of falling back to posting the raw (not fully
# composed) clip. Cleared on success or once DISCORD_COMPOSE_MAX_ATTEMPTS is
# reached. In-memory only — a restart just resets the count, which is fine
# since compose failures are usually transient (backend load, ffmpeg hiccup).
_compose_failures: dict = {}


def _load_posted() -> set:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return {(job_id, idx) for job_id, idx in json.load(fh)}
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return set()


def _save_posted(posted: set) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump([[job_id, idx] for job_id, idx in sorted(posted)], fh)
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        # A read-only mount must not take the bot down — worst case it
        # re-posts after a restart, which is visible and recoverable.
        print(f"WARNING: could not persist posted-state: {exc}", flush=True)


_posted = _load_posted()


def _local_clip_path(video_url: str):
    """Map '/videos/<job>/<file>' to a path under the output directory.

    None when the directory is unset, the file is missing, or the resolved
    path escapes the output root (guards a crafted filename in metadata).
    """
    if not video_url.startswith("/videos/"):
        return None
    relative = video_url[len("/videos/"):]
    root = os.path.realpath(CLIPPYME_OUTPUT_DIR)
    candidate = os.path.realpath(os.path.join(root, relative))
    if not (candidate == root or candidate.startswith(root + os.sep)):
        return None
    return candidate if os.path.isfile(candidate) else None


# Loaded lazily (first upload attempt), cached — building/refreshing
# credentials is cheap but there's no reason to touch the key file every time.
# `False` is a "tried once, unusable" sentinel distinct from `None` ("not
# attempted yet") so a missing/malformed key file doesn't get re-parsed (and
# re-logged) on every single oversized clip.
_drive_credentials = None


def _load_drive_credentials():
    global _drive_credentials
    if _drive_credentials is not None:
        return _drive_credentials or None
    if not _GOOGLE_AUTH_AVAILABLE or not GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE or not GOOGLE_DRIVE_FOLDER_ID:
        _drive_credentials = False
        return None
    try:
        _drive_credentials = _google_service_account.Credentials.from_service_account_file(
            GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE, scopes=_GOOGLE_DRIVE_SCOPES
        )
    except Exception as exc:
        print(f"WARNING: could not load Google Drive service account key: {exc}", flush=True)
        _drive_credentials = False
        return None
    return _drive_credentials


def _drive_access_token():
    """Blocking: mints/refreshes the service-account access token. Run via
    asyncio.to_thread — google-auth's HTTP calls are synchronous."""
    creds = _load_drive_credentials()
    if not creds:
        return None
    if not creds.valid:
        try:
            creds.refresh(_GoogleAuthRequest())
        except Exception as exc:
            print(f"WARNING: Google Drive token refresh failed: {exc}", flush=True)
            return None
    return creds.token


async def _upload_to_drive(local_path: str, filename: str):
    """Upload a clip too large for Discord to Google Drive (resumable upload,
    service-account auth) and return a shareable 'anyone with the link' view
    URL, or None if Drive isn't configured or anything about the upload
    failed — this is a best-effort fallback, never fatal to the bot."""
    token = await asyncio.to_thread(_drive_access_token)
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
                json={"name": filename, "parents": [GOOGLE_DRIVE_FOLDER_ID]},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"Drive upload session init failed: {resp.status} {text[:200]}", flush=True)
                    return None
                upload_url = resp.headers.get("Location")
            if not upload_url:
                print("Drive upload session init returned no Location header", flush=True)
                return None

            size = os.path.getsize(local_path)
            with open(local_path, "rb") as fh:
                async with session.put(
                    upload_url, data=fh, headers={"Content-Length": str(size)},
                    timeout=aiohttp.ClientTimeout(total=1800),
                ) as resp:
                    if resp.status not in (200, 201):
                        text = await resp.text()
                        print(f"Drive upload failed: {resp.status} {text[:200]}", flush=True)
                        return None
                    file_id = (await resp.json()).get("id")
            if not file_id:
                return None

            async with session.post(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"type": "anyone", "role": "reader"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    # Upload itself succeeded — only the "anyone with the
                    # link" sharing failed. Still return the link; worst case
                    # only the service account can open it until fixed.
                    print(f"Drive permission grant failed: {resp.status} {text[:200]}", flush=True)
        return f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as exc:
        print(f"Drive upload error: {exc}", flush=True)
        return None


async def _fetch_zernio_accounts() -> dict:
    """Fetch connected Zernio accounts (tiktok/instagram/youtube -> id) for
    this bot's configured ZERNIO_PROFILE."""
    try:
        async with aiohttp.ClientSession() as session, session.get(
            f"{CLIPPYME_API}/api/config/zernio?profile={quote(ZERNIO_PROFILE)}"
        ) as resp:
            if resp.status != 200:
                print(f"WARNING: /api/config/zernio returned {resp.status}", flush=True)
                return {}
            data = await resp.json()
    except Exception as exc:
        print(f"WARNING: could not reach ClippyMe at {CLIPPYME_API}: {exc}", flush=True)
        return {}

    if not data.get("configured"):
        print("WARNING: Zernio is not configured — approving will report the reason.", flush=True)
    return data.get("accounts") or {}


def _build_compose_toggles(hook_text: str, recipe=None):
    """The layer toggles + params shared by the publish body and the
    Discord-preview compose call — same recipe either way, so what gets
    approved in Discord is what gets published, just at a smaller file size.

    ``recipe`` is the job's OWN stored recipe (``composeRecipe`` from
    /api/history, written at submit time from the Create tab). When present it
    wins outright: this bot's env vars below are only a fallback for jobs
    submitted before recipes were stored, or by a caller that sent none.
    Before this, the bot always composed from its own env — so a hook the user
    configured WITH a background, at a position drawn in the layout editor,
    reached Discord (and then TikTok) with no background at HOOK_POSITION.
    """
    if recipe:
        toggles = dict(recipe.get("toggles") or {})
        # The hook layer is skipped by the backend on empty text anyway, but
        # keep the toggle honest so the "is anything burnable" checks below
        # (and the publish body's `any(toggles.values())`) agree.
        toggles["hook"] = bool(toggles.get("hook")) and bool(hook_text)
        hook_params = dict(recipe.get("hook_params") or {})
        if toggles["hook"]:
            # The recipe stores job-wide hook STYLE; the text is per clip.
            hook_params["text"] = hook_text
        else:
            hook_params = {}
        subtitle_params = dict(recipe.get("subtitle_params") or {}) if toggles.get("subtitles") else {}
        return toggles, hook_params, subtitle_params

    toggles = {
        "smartcut": BURN_SMARTCUT,
        "subtitles": BURN_SUBTITLES,
        # An empty hook text makes the backend skip the layer anyway.
        "hook": BURN_HOOK and bool(hook_text),
        "logo": False,
        "grade": False,
        "banner": False,
    }
    hook_params = (
        {"text": hook_text, "position": HOOK_POSITION, "size": "S", "offset_y": 0}
        if toggles["hook"] else {}
    )
    subtitle_params = (
        {
            "preset": SUBTITLE_PRESET,
            "mode": "karaoke",
            "display_mode": "word_group",
            "font": SUBTITLE_FONT,
            "position": SUBTITLE_POSITION,
            "align": "center",
            "font_color": "#FFFFFF",
            "outline_color": "#000000",
            "offset_y": 0,
        }
        if toggles["subtitles"] else {}
    )
    return toggles, hook_params, subtitle_params


def _build_publish_body(title: str, hook_text: str, recipe=None):
    """Body for POST /api/publish/{job}/{clip}, or None if no account matches.

    The API requires ``platforms`` to be a non-empty list of
    ``{platform, accountId}`` — a bare platform name is rejected with a 422.

    ``recipe`` is the job's own stored compose recipe (see
    ``_build_compose_toggles``); the logo/grade/banner layers below come from
    it too, so a publish burns exactly what the Discord preview showed.
    """
    targets = [
        {"platform": name, "accountId": _zernio_accounts[name]}
        for name in PUBLISH_PLATFORMS
        if _zernio_accounts.get(name)
    ]
    if not targets:
        return None

    toggles, hook_params, subtitle_params = _build_compose_toggles(hook_text, recipe)
    r = recipe or {}

    body = {
        "title": (title or "Clip")[:100],
        "caption": (title or "Clip")[:2200],
        "platforms": targets,
        "schedule_mode": "now",
        "timezone": TIMEZONE,
        "zernio_profile": ZERNIO_PROFILE,
    }
    if any(t["platform"] == "tiktok" for t in targets):
        body["tiktok_settings"] = {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        }

    if any(toggles.values()):
        body.update({
            "compose_first": True,
            "toggles": toggles,
            "hook_params": hook_params,
            "subtitle_params": subtitle_params,
            "logo_params": r.get("logo_params") or {},
            "grade_params": r.get("grade_params") or {},
            "banner_params": r.get("banner_params") or {},
            "player_image_params": r.get("player_image_params") or {},
            "drop_ranges": [],
        })
    return body


async def _compose_full(job_id: str, idx: int, hook_text: str, recipe=None):
    """Compose (subtitles/hook — same recipe as publish) via the backend and
    return (local_path, composed_url) for the FULL-quality composed file, or
    None when there is nothing to burn in (raw clip already looks final) or
    compose failed. Shared by ``_compose_preview`` (which additionally
    shrinks the result for Discord and only needs local_path) and the
    download-reaction handler, which also needs composed_url — linking a
    clip that IS composed by its own pre-compose ``video_url`` would serve a
    file missing the burned-in subtitles/hook."""
    toggles, hook_params, subtitle_params = _build_compose_toggles(hook_text, recipe)
    if not any(toggles.values()):
        return None  # nothing to burn in — raw clip already looks final

    url = f"{CLIPPYME_API}/api/compose/{job_id}/{idx}"
    r = recipe or {}
    payload = {
        "toggles": toggles, "hook_params": hook_params, "subtitle_params": subtitle_params,
        "logo_params": r.get("logo_params") or {},
        "grade_params": r.get("grade_params") or {},
        "banner_params": r.get("banner_params") or {},
        "player_image_params": r.get("player_image_params") or {},
        "drop_ranges": [],
    }
    try:
        async with aiohttp.ClientSession() as session, session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=300)
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"Compose failed for {job_id}/{idx}: {resp.status} {text[:200]}", flush=True)
                return None
            composed = (await resp.json()).get("composed_url", "")
    except asyncio.TimeoutError:
        # str(exc) is empty for asyncio.TimeoutError, which used to print as
        # "Compose request failed for X/Y: " with nothing after the colon —
        # indistinguishable from a crash. Name it so a slow/overloaded host
        # is visible in the logs instead of looking like a silent failure.
        print(f"Compose request timed out for {job_id}/{idx} after 300s", flush=True)
        return None
    except Exception as exc:
        print(f"Compose request failed for {job_id}/{idx}: {exc}", flush=True)
        return None

    local_path = _local_clip_path(composed)
    return (local_path, composed) if local_path else None


async def _compose_preview(job_id: str, idx: int, hook_text: str, recipe=None):
    """Full-quality compose (see ``_compose_full``), then shrink the result
    for Discord: a 2K/4K source composes to a file well over Discord's upload
    limit, and a dead oversized-clip link isn't a preview. Returns a path to a
    small temp file the caller must delete, or None if compose/shrink failed
    (caller falls back to posting the raw clip)."""
    composed = await _compose_full(job_id, idx, hook_text, recipe)
    if not composed:
        return None
    composed_path, _composed_url = composed

    preview_path = f"/tmp/preview_{job_id}_{idx}.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", composed_path,
        "-vf", f"scale=-2:{DISCORD_PREVIEW_MAX_HEIGHT}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(DISCORD_PREVIEW_CRF),
        "-c:a", "aac", "-b:a", "96k",
        preview_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0 or not os.path.isfile(preview_path):
            print(f"Preview shrink failed for {job_id}/{idx}: {stderr.decode(errors='replace')[-300:]}",
                  flush=True)
            return None
        return preview_path
    except Exception as exc:
        print(f"Preview shrink failed for {job_id}/{idx}: {exc}", flush=True)
        return None


@bot.event
async def on_ready():
    global _zernio_accounts
    print(f"Logged in as {bot.user}", flush=True)
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        print(f"Watching channel: #{channel.name}", flush=True)
    else:
        print(f"WARNING: channel {CHANNEL_ID} not found — is the bot on the server?", flush=True)

    out_dir = os.path.realpath(CLIPPYME_OUTPUT_DIR)
    if os.path.isdir(out_dir):
        print(f"Clip uploads from: {out_dir} (up to {MAX_UPLOAD_MB} MB)", flush=True)
    else:
        print(f"WARNING: output directory not found: {out_dir}", flush=True)
        print("         -> set CLIPPYME_OUTPUT_DIR, or videos cannot be uploaded.", flush=True)
    if CLIPPYME_PUBLIC_URL:
        print(f"Public URL for oversized clips: {CLIPPYME_PUBLIC_URL}", flush=True)

    _zernio_accounts = await _fetch_zernio_accounts()
    if _zernio_accounts:
        print(f"Zernio profile '{ZERNIO_PROFILE}' accounts: {', '.join(sorted(_zernio_accounts))}", flush=True)
    else:
        print(f"NOTE: no Zernio accounts on profile '{ZERNIO_PROFILE}' — "
              "approving will explain why nothing posted.", flush=True)

    burn = [n for n, on in (("subtitles", BURN_SUBTITLES), ("hook", BURN_HOOK),
                            ("smart-cut", BURN_SMARTCUT)) if on]
    print(f"Burned in at publish: {', '.join(burn) if burn else 'nothing (raw clip)'}", flush=True)

    if not poll_new_clips.is_running():
        poll_new_clips.start()


@tasks.loop(seconds=POLL_SECONDS)
async def poll_new_clips():
    """Ask ClippyMe for finished clips that have not been published yet."""
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return

    try:
        async with aiohttp.ClientSession() as session, session.get(
            f"{CLIPPYME_API}/api/history"
        ) as resp:
            if resp.status != 200:
                print(f"WARNING: /api/history returned {resp.status}", flush=True)
                return
            data = await resp.json()
    except Exception as exc:
        print(f"Could not fetch history: {exc}", flush=True)
        return

    for job in data.get("jobs", []):
        job_id = job.get("jobId")
        if not job_id:
            continue
        # Multi-campaign setup: one bot container per campaign, each watching
        # the SAME /api/history. Without this filter every bot would post
        # every clip — including another campaign's, whose approval would
        # then publish through THIS bot's (wrong) Zernio account. Jobs
        # submitted before this field existed default to "default", which is
        # exactly the profile a single-campaign install's one bot uses.
        if (job.get("zernioProfile") or "default") != ZERNIO_PROFILE:
            continue
        clips = job.get("clips", [])
        for idx, clip in enumerate(clips):
            key = (job_id, idx)
            if key in _posted:
                continue
            if clip.get("published"):
                _posted.add(key)
                _save_posted(_posted)
                continue
            await _post_clip(channel, job_id, idx, clip, len(clips),
                             recipe=job.get("composeRecipe"))


async def _send_clip_message(channel, job_id: str, idx: int, header: str,
                              local_path, video_url: str, note: str) -> None:
    """Post one clip message: attach the file if it fits Discord's upload
    limit, else fall back to Google Drive / a public link. Shared by the
    composed-preview path and the nothing-configured-to-burn-in (raw clip)
    path in ``_post_clip`` — same size/attachment/reaction logic either way."""
    attachment = None
    body = header

    if local_path:
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        if size_mb <= MAX_UPLOAD_MB:
            attachment = discord.File(local_path, filename=os.path.basename(local_path))
            body += note
        else:
            # Google Drive is preferred over CLIPPYME_PUBLIC_URL for
            # oversized clips — no need to expose the backend publicly.
            drive_link = await _upload_to_drive(local_path, os.path.basename(local_path))
            if drive_link:
                body += (f"{drive_link}\n"
                         f"_({size_mb:.1f} MB — too large for Discord, uploaded to Google Drive instead)_\n\n{note}")
            elif CLIPPYME_PUBLIC_URL:
                body += (f"{CLIPPYME_PUBLIC_URL}{video_url}\n"
                         f"_({size_mb:.1f} MB — too large to upload)_\n\n{note}")
            else:
                body += (f"_Clip is {size_mb:.1f} MB, over Discord's {MAX_UPLOAD_MB} MB limit. "
                         f"Set GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE/GOOGLE_DRIVE_FOLDER_ID or "
                         f"CLIPPYME_PUBLIC_URL to link it instead._\n\n{note}")
    elif CLIPPYME_PUBLIC_URL:
        body += f"{CLIPPYME_PUBLIC_URL}{video_url}\n\n{note}"
    else:
        body += ("_No video attached: file not found and no public URL set. "
                 f"Check CLIPPYME_OUTPUT_DIR / CLIPPYME_PUBLIC_URL._\n\n{note}")

    try:
        sent = (await channel.send(body, file=attachment) if attachment
                else await channel.send(body))
        await sent.add_reaction(APPROVE_EMOJI)
        await sent.add_reaction(REJECT_EMOJI)
        await sent.add_reaction(DOWNLOAD_EMOJI)
    except discord.HTTPException as exc:
        # Usually the file was over the server's real limit after all. Retry
        # once without it so the clip can still be approved.
        print(f"Could not post clip {idx} (job {job_id}): {exc}", flush=True)
        if attachment is None:
            return
        fallback = header + (
            f"{CLIPPYME_PUBLIC_URL}{video_url}\n\n" if CLIPPYME_PUBLIC_URL
            else "_Video upload to Discord failed (file too large?)._\n\n"
        ) + note
        try:
            sent = await channel.send(fallback)
            await sent.add_reaction(APPROVE_EMOJI)
            await sent.add_reaction(REJECT_EMOJI)
            await sent.add_reaction(DOWNLOAD_EMOJI)
        except Exception as exc2:
            print(f"Fallback post also failed: {exc2}", flush=True)
    except Exception as exc:
        print(f"Could not post clip {idx} (job {job_id}): {exc}", flush=True)


async def _post_clip(channel, job_id: str, idx: int, clip: dict, total: int, recipe=None) -> None:
    """Post one clip for approval, but only once it is fully composed
    (subtitles/hook burned in), when the bot is configured to burn anything
    in at all. A compose failure is retried on the next poll cycle instead of
    falling back to the raw clip — Discord approval must preview exactly what
    would publish, and an incomplete clip approved here would look nothing
    like what lands on TikTok/YouTube. After DISCORD_COMPOSE_MAX_ATTEMPTS
    failed attempts, one failure notice is posted (no video, nothing to
    approve — there's nothing valid to approve) and the clip is marked
    handled so it stops retrying forever."""
    title = clip.get("title") or "(untitled)"
    hook_text = clip.get("viral_hook_text") or clip.get("hook_text") or ""
    duration = round(max(0.0, clip.get("end", 0) - clip.get("start", 0)), 1)
    video_url = clip.get("video_url", "")
    key = (job_id, idx)

    header = (
        f"**{title}**\n"
        f"Job: {job_id} | Clip {idx}  (#{idx + 1} of {total})\n"
        f"Length: {duration}s\n"
    )

    toggles, _, _ = _build_compose_toggles(hook_text, recipe)
    if not any(toggles.values()):
        # Nothing is configured to burn in — the raw clip already IS the
        # final look, so there's nothing to compose, wait for, or retry.
        note = (
            "_Preview is the raw clip — nothing is configured to burn in "
            "(BURN_SUBTITLES/BURN_HOOK/BURN_SMARTCUT are all off)._\n\n"
            f"{APPROVE_EMOJI} approve  ·  {REJECT_EMOJI} reject  ·  {DOWNLOAD_EMOJI} full-quality download"
        )
        await _send_clip_message(channel, job_id, idx, header, _local_clip_path(video_url), video_url, note)
        _clip_meta[key] = {"title": title, "hook_text": hook_text, "video_url": video_url,
                       "recipe": recipe}
        _posted.add(key)
        _save_posted(_posted)
        return

    # Preview shows subtitles/hook burned in — same recipe as publish, so
    # approving here and what lands on TikTok/YouTube match. Shrunk to
    # DISCORD_PREVIEW_MAX_HEIGHT/CRF purely for Discord's upload limit; the
    # actual publish re-composes at full quality from the untouched source.
    preview_path = await _compose_preview(job_id, idx, hook_text, recipe)
    if preview_path is None:
        attempts = _compose_failures.get(key, 0) + 1
        if attempts < DISCORD_COMPOSE_MAX_ATTEMPTS:
            _compose_failures[key] = attempts
            print(f"Compose not ready for {job_id}/{idx} yet (attempt {attempts}/"
                  f"{DISCORD_COMPOSE_MAX_ATTEMPTS}) — retrying next poll ({POLL_SECONDS}s).", flush=True)
            return
        # Cap reached — stop retrying, but do not silently drop the clip
        # either: post one clear notice so it's obvious something needs a
        # human to look at the backend logs.
        _compose_failures.pop(key, None)
        try:
            await channel.send(
                header + f"_Could not compose this clip (subtitles/hook burn-in) after "
                f"{DISCORD_COMPOSE_MAX_ATTEMPTS} attempts — check the backend logs. Nothing "
                "to approve here; re-run the clip from the ClippyMe dashboard once fixed._"
            )
        except Exception as exc:
            print(f"Could not post compose-failure notice for {job_id}/{idx}: {exc}", flush=True)
        _posted.add(key)
        _save_posted(_posted)
        return

    _compose_failures.pop(key, None)
    note = (
        "_Preview: subtitles/hook burned in (shrunk for Discord — publish is full quality)._\n\n"
        f"{APPROVE_EMOJI} approve  ·  {REJECT_EMOJI} reject  ·  {DOWNLOAD_EMOJI} full-quality download"
    )
    try:
        await _send_clip_message(channel, job_id, idx, header, preview_path, video_url, note)
    finally:
        # The preview is a throwaway re-encode made just for this message —
        # never leave it behind in the container's /tmp.
        try:
            os.remove(preview_path)
        except OSError:
            pass

    _clip_meta[key] = {"title": title, "hook_text": hook_text, "video_url": video_url,
                       "recipe": recipe}
    _posted.add(key)
    _save_posted(_posted)


@poll_new_clips.before_loop
async def before_poll():
    await bot.wait_until_ready()


@bot.event
async def on_raw_reaction_add(payload):
    """Raw variant, not on_reaction_add: that one only fires for messages still
    in discord.py's in-memory cache, which a bot restart empties — exactly what
    happens here since a code/config change means recreating the container.
    A raw event carries just IDs, so the message is always fetched fresh."""
    if payload.channel_id != CHANNEL_ID:
        return
    if str(payload.emoji) not in (APPROVE_EMOJI, REJECT_EMOJI, DOWNLOAD_EMOJI):
        return
    if payload.member is not None and payload.member.bot:
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return
    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return

    user = payload.member or bot.get_user(payload.user_id) or await bot.fetch_user(payload.user_id)

    content = message.content or ""
    if "Job:" not in content or "Clip" not in content:
        return

    try:
        job_id = content.split("Job: ")[1].split(" |")[0].strip()
        clip_index = int(content.split("Clip ")[1].split()[0].strip())
    except (IndexError, ValueError) as exc:
        await message.reply(f"{REJECT_EMOJI} Could not parse this message: {exc}")
        return

    if str(payload.emoji) == APPROVE_EMOJI:
        await handle_approval(message, user, job_id, clip_index)
    elif str(payload.emoji) == REJECT_EMOJI:
        await handle_rejection(message, user, job_id, clip_index)
    else:
        await handle_download_request(message, user, job_id, clip_index)


async def handle_approval(message, user, job_id, clip_index):
    meta = _clip_meta.get((job_id, clip_index), {})
    title = meta.get("title") or (message.content or "").split("\n")[0].strip("* ")
    hook_text = meta.get("hook_text", "")

    body = _build_publish_body(title, hook_text, meta.get("recipe"))
    if body is None:
        await message.reply(
            f"{REJECT_EMOJI} No connected Zernio account for "
            f"{', '.join(PUBLISH_PLATFORMS)} — connect one in ClippyMe Settings, "
            f"then run `!zernio` here to reload."
        )
        return

    names = ", ".join(t["platform"] for t in body["platforms"])
    await message.reply(f"⏳ Publishing clip {clip_index} (job {job_id}) to {names}…")

    url = f"{CLIPPYME_API}/api/publish/{job_id}/{clip_index}"
    headers = {"X-Publish-Gate-Token": PUBLISH_GATE_TOKEN} if PUBLISH_GATE_TOKEN else {}
    try:
        # Publish can compose (ffmpeg) AND upload to multiple platforms
        # sequentially server-side before responding — aiohttp's 300s default
        # is routinely too short for that, and asyncio.TimeoutError's own
        # str() is empty, which used to print as "Network error while
        # publishing: " with nothing after the colon (indistinguishable from
        # a crash). 1800s matches this bot's other big-media-upload timeout
        # (_upload_to_drive's PUT); name the timeout explicitly so a real
        # backend hang is still visible instead of looking silent.
        async with aiohttp.ClientSession() as session, session.post(
            url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=1800)
        ) as resp:
            text = await resp.text()
            status = resp.status
    except asyncio.TimeoutError:
        await message.reply(
            f"{REJECT_EMOJI} Publishing clip {clip_index} (job {job_id}) timed out after 30 minutes — "
            "check the backend logs; the upload to Zernio may still be running."
        )
        return
    except Exception as exc:
        await message.reply(f"{REJECT_EMOJI} Network error while publishing: {exc}")
        return

    if status == 200:
        await message.reply(f"✅ Published to {names} — approved by {user.mention}")
        _stats["approved"] += 1
    else:
        await message.reply(f"⚠️ Error {status}: {text[:500]}")
    await post_stats_update()


async def handle_rejection(message, user, job_id, clip_index):
    await message.reply(
        f"{REJECT_EMOJI} Clip {clip_index} (job {job_id}) rejected by {user.mention}"
    )
    _stats["rejected"] += 1
    await post_stats_update()


async def handle_download_request(message, user, job_id, clip_index):
    """DOWNLOAD_EMOJI reaction: reply with the FULL-quality file — the message
    already posted for approval is deliberately shrunk to fit Discord's upload
    limit (DISCORD_PREVIEW_MAX_HEIGHT/CRF), so it is never what you want to
    save from the app (e.g. a phone's "Save Video" into the camera roll)."""
    meta = _clip_meta.get((job_id, clip_index))
    if meta is None:
        await message.reply(
            f"{REJECT_EMOJI} Clip {clip_index} (job {job_id}) is no longer known to "
            "this bot (it was posted before a restart) — re-check it in the ClippyMe dashboard."
        )
        return
    title = meta.get("title") or "Clip"
    hook_text = meta.get("hook_text", "")
    video_url = meta.get("video_url", "")

    # Composing (subtitles/hook burn-in) can take minutes on a loaded host —
    # without this, the reaction looks like it did nothing at all (unlike
    # handle_approval, which sends its own "Publishing..." ack immediately).
    await message.reply(f"⏳ Preparing full-quality download of **{title}**…")

    composed = await _compose_full(job_id, clip_index, hook_text, meta.get("recipe"))
    full_path, composed_url = composed if composed else (None, None)
    local_path = full_path or _local_clip_path(video_url)
    # The link must point at whatever local_path actually is — video_url is
    # the pre-compose raw clip, missing any burned-in subtitles/hook that
    # composed_url (when compose ran) has.
    link_url = composed_url if full_path else video_url

    try:
        if local_path:
            size_mb = os.path.getsize(local_path) / (1024 * 1024)
            if size_mb <= MAX_UPLOAD_MB:
                await message.reply(
                    f"**{title}** — full quality, requested by {user.mention}",
                    file=discord.File(local_path, filename=os.path.basename(local_path)),
                )
                return
            drive_link = await _upload_to_drive(local_path, os.path.basename(local_path))
            if drive_link:
                await message.reply(
                    f"**{title}**\n{drive_link}\n"
                    f"_({size_mb:.1f} MB — over Discord's {MAX_UPLOAD_MB} MB limit, uploaded to Google Drive instead)_"
                )
                return
            if CLIPPYME_PUBLIC_URL and link_url:
                await message.reply(
                    f"**{title}**\n{CLIPPYME_PUBLIC_URL}{link_url}\n"
                    f"_({size_mb:.1f} MB — over Discord's {MAX_UPLOAD_MB} MB limit, linked instead)_"
                )
                return
            await message.reply(
                f"{REJECT_EMOJI} **{title}** is {size_mb:.1f} MB — over Discord's "
                f"{MAX_UPLOAD_MB} MB limit and no Google Drive or CLIPPYME_PUBLIC_URL "
                "is set to link it instead."
            )
            return
        if CLIPPYME_PUBLIC_URL and link_url:
            await message.reply(f"**{title}**\n{CLIPPYME_PUBLIC_URL}{link_url}")
            return
        await message.reply(
            f"{REJECT_EMOJI} Could not find the full-quality file for **{title}** "
            "(check CLIPPYME_OUTPUT_DIR / CLIPPYME_PUBLIC_URL)."
        )
    except discord.HTTPException as exc:
        print(f"Could not send full-quality clip {clip_index} (job {job_id}): {exc}", flush=True)
        drive_link = await _upload_to_drive(local_path, os.path.basename(local_path)) if local_path else None
        if drive_link:
            await message.reply(f"**{title}**\n{drive_link}\n_(Discord upload failed, uploaded to Google Drive instead)_")
        elif CLIPPYME_PUBLIC_URL and link_url:
            await message.reply(
                f"**{title}**\n{CLIPPYME_PUBLIC_URL}{link_url}\n_(upload failed, linked instead)_"
            )


async def post_stats_update():
    if not STATS_CHANNEL_ID:
        return
    channel = bot.get_channel(STATS_CHANNEL_ID)
    if channel is None:
        return
    await channel.send(
        f"📊 Total: {_stats['approved']} approved, {_stats['rejected']} rejected"
    )


@bot.command()
async def ping(ctx):
    await ctx.send(f"🏓 Pong! Channel: <#{CHANNEL_ID}>")


@bot.command()
async def status(ctx):
    burn = [n for n, on in (("subtitles", BURN_SUBTITLES), ("hook", BURN_HOOK),
                            ("smart-cut", BURN_SMARTCUT)) if on]
    accounts = ", ".join(sorted(_zernio_accounts)) or "none"
    out_dir = os.path.realpath(CLIPPYME_OUTPUT_DIR)
    video_mode = (
        f"file upload from {out_dir} (up to {MAX_UPLOAD_MB} MB)"
        if os.path.isdir(out_dir)
        else (f"link via {CLIPPYME_PUBLIC_URL}" if CLIPPYME_PUBLIC_URL
              else "NO video (not configured)")
    )
    oversized_fallback = (
        "Google Drive"
        if (GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE and GOOGLE_DRIVE_FOLDER_ID)
        else (f"link via {CLIPPYME_PUBLIC_URL}" if CLIPPYME_PUBLIC_URL else "none configured")
    )
    await ctx.send(
        f"**Bot status**\n"
        f"Channel: <#{CHANNEL_ID}>\n"
        f"API: {CLIPPYME_API}\n"
        f"Video in Discord: {video_mode}\n"
        f"Oversized-clip fallback: {oversized_fallback}\n"
        f"Zernio profile: {ZERNIO_PROFILE}\n"
        f"Zernio accounts: {accounts}\n"
        f"Burned in at publish: {', '.join(burn) if burn else 'nothing'}\n"
        f"Approved: {_stats['approved']} | Rejected: {_stats['rejected']}\n"
        f"Polling every {POLL_SECONDS}s"
    )


@bot.command(name="zernio")
async def zernio_refresh(ctx):
    """Reload Zernio accounts without restarting the bot."""
    global _zernio_accounts
    _zernio_accounts = await _fetch_zernio_accounts()
    await ctx.send(
        f"Zernio profile '{ZERNIO_PROFILE}' accounts: "
        f"{', '.join(sorted(_zernio_accounts)) or 'none found'}"
    )


if __name__ == "__main__":
    bot.run(TOKEN)
