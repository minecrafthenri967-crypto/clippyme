"""YouTube/Twitch/Kick download + filename/cookies helpers.

Extracted from ``pipeline.main`` as part of the decomposition. Depends only on
``yt_dlp`` + stdlib (no cv2/torch/mediapipe), so it imports and is testable on
the host.
"""
import ipaddress
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import yt_dlp

from clippyme.netutil import resolve_host_addresses


# Remote URL jobs are intentionally limited to the platforms ClippyMe actually
# supports.  The old validator accepted every public HTTP(S) host; because
# yt-dlp follows redirects and extractor-provided media URLs, that exposed a
# broad server-side fetch primitive even though the first hostname was checked
# for private IPs.  Exact official hosts + HTTPS keep user jobs on the expected
# trust boundary while still covering YouTube, Twitch clips/VODs and Kick VODs.
# Split out as its own set (not just a comment) because _player_client_chain's
# retry-with-a-different-player-client trick is YouTube-extractor-specific —
# see _is_youtube_url below.
_YOUTUBE_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
})
_SUPPORTED_SOURCE_HOSTS = _YOUTUBE_HOSTS | frozenset({
    "twitch.tv",
    "www.twitch.tv",
    "m.twitch.tv",
    "clips.twitch.tv",
    "kick.com",
    "www.kick.com",
})


def _is_youtube_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "").lower() in _YOUTUBE_HOSTS
    except ValueError:
        return False


def validate_supported_source_url(url: str) -> str:
    """Validate a user/monitor URL before yt-dlp is allowed to resolve it."""
    raw = (url or "").strip()
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid source URL") from exc
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _SUPPORTED_SOURCE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError(
            "source URL must be an official HTTPS YouTube, Twitch, or Kick URL"
        )
    return raw


def _reject_rebound_internal(url: str) -> None:
    """Re-resolve the URL host at download time and refuse internal ranges.

    This is a second line of defence against DNS rebinding after the API-layer
    validation. Resolution failures are left to yt-dlp, but any internal address
    in a mixed answer is rejected.
    """
    try:
        host = urlparse(url).hostname
        if not host:
            return
        try:
            ip_obj = ipaddress.ip_address(host)
            addrs = [ip_obj]
        except ValueError:
            addrs = resolve_host_addresses(host, timeout=5.0)
        if any(
            a.is_private
            or a.is_loopback
            or a.is_link_local
            or a.is_reserved
            or a.is_multicast
            or a.is_unspecified
            for a in addrs
        ):
            raise ValueError(f"refusing download: {host} resolves to an internal address")
    except ValueError:
        raise
    except Exception:
        # A transient resolver failure is not an SSRF bypass now that the host
        # itself is an exact supported-platform allow-list entry. yt-dlp will
        # surface the actual network error to the job.
        return


def sanitize_filename(filename):
    """Remove invalid characters from filename."""
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = filename.replace(' ', '_')
    filename = filename.lstrip('-.')
    return filename[:100] or 'video'


def _resolve_cookies_path(explicit: str | None) -> str | None:
    """Resolve the cookies.txt path used by yt-dlp.

    Precedence:
      1. Explicit path passed on the CLI / by the caller.
      2. Repo-root ``data/cookies.txt`` (the path the dashboard writes to).
      3. ``YOUTUBE_COOKIES`` env var materialized into ``data/cookies_env.txt``.
      4. None (no cookies).
    """
    if explicit:
        return explicit
    repo_root_cookies = os.path.join("data", "cookies.txt")
    if os.path.exists(repo_root_cookies):
        return os.path.abspath(repo_root_cookies)
    env_cookies = os.environ.get("YOUTUBE_COOKIES")
    if env_cookies:
        env_path = os.path.join("data", "cookies_env.txt")
        os.makedirs(os.path.dirname(env_path) or ".", exist_ok=True)
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(env_cookies)
        return os.path.abspath(env_path)
    return None


# Video format ladder. avc1 (H.264) first, since it's the cheapest to decode
# on a CPU-only box (GPU_RUNTIME=cpu is the documented default) — but capped
# to the SAME height as every later rung, not left unbounded. Many YouTube
# uploads only serve 1080p+ as VP9/AV1, with avc1 topping out around 720p; a
# ladder that tries unbounded avc1 first and only falls back to other codecs
# once avc1 fails outright never reaches that fallback, because a 720p avc1
# rendition typically DOES exist — it just silently ends up much smaller than
# what the video actually offers. That matters beyond the download itself:
# reframe.py sets the pipeline's OUTPUT_HEIGHT to the SOURCE height, so a
# download quietly capped at 720p caps every rendered clip at 720p too.
_DEFAULT_MAX_DOWNLOAD_HEIGHT = 1080
# 0 = uncapped ("true best" — can be 4K+, slower and much bigger; more crop
# headroom for reframe's subject-tracking zoom). Settings' "Download quality"
# control persists one of these via CLIPPYME_MAX_DOWNLOAD_HEIGHT (same
# env-write path as every other Settings key — config_store.save_persistent_config).
_VALID_MAX_DOWNLOAD_HEIGHTS = (720, 1080, 1440, 2160, 0)


def resolve_max_download_height() -> int:
    """The configured download-quality cap in pixels of height (0 = uncapped)."""
    raw = (os.environ.get("CLIPPYME_MAX_DOWNLOAD_HEIGHT") or "").strip()
    if not raw:
        return _DEFAULT_MAX_DOWNLOAD_HEIGHT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_DOWNLOAD_HEIGHT
    return value if value in _VALID_MAX_DOWNLOAD_HEIGHTS else _DEFAULT_MAX_DOWNLOAD_HEIGHT


def build_format_ladder(max_height: int) -> str:
    """yt-dlp format selector for a given height cap (0 = uncapped).

    Resolution-first, deliberately. ``/``-separated selectors are tried left
    to right and the FIRST match wins, so any rung that hard-filters on a
    codec outranks every later rung — including the ones that would find a
    higher resolution. An earlier ladder opened with
    ``bestvideo[height<=H][vcodec^=avc1][ext=mp4]``, and because YouTube
    serves avc1 only up to 1080p (1440p/2160p exist solely as VP9/AV1), that
    rung matched a 1080p avc1 stream for EVERY cap. Raising the download
    quality to 1440p or 4K therefore fetched a byte-identical 1080p file —
    the setting appeared to do nothing, and every clip was reframed from a
    source with far fewer real pixels than the user had asked for.

    The avc1 preference itself is sound (cheapest CPU decode on the
    GPU_RUNTIME=cpu default) but belongs in ``build_format_sort`` as a
    TIEBREAK, where it wins whenever avc1 can actually deliver the chosen
    resolution and steps aside when it cannot. ``best{h}`` stays as the
    whole-formats fallback (Kick/Twitch VODs are sometimes only pre-muxed).
    """
    h = f'[height<={max_height}]' if max_height else ''
    return f'bestvideo{h}+bestaudio/best{h}'


def build_format_sort() -> list[str]:
    """yt-dlp ``format_sort``: resolution first, then cheap-to-decode codecs.

    This is where the codec preference lives now (see ``build_format_ladder``).
    Sorting only ever breaks ties BETWEEN equally-good matches, so it can
    never cost resolution the way a codec-filtered selector rung does: at
    1080p, where avc1 exists, h264 still wins for cheap CPU decode; at 1440p
    and above, where YouTube offers only VP9/AV1, the higher resolution is
    taken instead of silently falling back to a 1080p avc1 rendition.
    """
    return ["res", "vcodec:h264", "ext:mp4"]

# Player-client fallback chain (mid-2026 verified bot-resistance order).
_DEFAULT_PLAYER_CLIENTS = ("default", "tv+tv_embedded", "web_safari")


def _player_client_chain():
    """Return player-client attempts, optionally overridden by the environment."""
    raw = (os.environ.get("YTDLP_PLAYER_CLIENTS") or "").strip()
    if raw:
        chain = [a.strip() for a in raw.split(",") if a.strip()]
        if chain:
            return chain
    return list(_DEFAULT_PLAYER_CLIENTS)


def download_attempt_chain(url: str):
    """Per-attempt player-client specs for one download.

    YouTube gets the real fallback chain: tv/tv_embedded/web_safari use a
    different auth flow than the default web client and often sail past a
    bot-check wall it just hit.

    Twitch/Kick get the same NUMBER of attempts but all on "default", because
    ``extractor_args={"youtube": ...}`` is a no-op for the twitch:vod/kick
    extractors — switching "clients" there re-issues the byte-identical
    request. The attempts themselves still matter: the retry loop's 5s backoff
    is the ONLY retry covering an ``extract_info`` failure (a transient 403 on
    VOD metadata), since yt-dlp's own ``retries``/``fragment_retries`` apply to
    the download phase, not extraction.
    """
    chain = _player_client_chain()
    return chain if _is_youtube_url(url) else ["default"] * len(chain)


def _extractor_args_for(attempt: str):
    """Map an attempt spec to yt-dlp extractor_args, or None for defaults."""
    if not attempt or attempt.lower() == "default":
        return None
    clients = [c.strip() for c in attempt.split("+") if c.strip()]
    return {"youtube": {"player_client": clients}}


def _resolve_proxy() -> str | None:
    """Optional upstream proxy for yt-dlp (``YTDLP_PROXY``, e.g. a residential
    proxy or an SSH ``-D`` SOCKS tunnel back to a home connection).

    Cookies alone don't stop YouTube's bot-check on a server deployment: the
    check also weighs the REQUEST'S OWN IP, and datacenter/VPS ranges get
    flagged far more readily than the residential IP the cookies were
    originally exported from — so a fresh cookies.txt can still get walled
    within hours. Routing through a proxy that matches the cookies' origin
    is the mitigation that actually reduces how often that happens, rather
    than just re-uploading cookies after each wall.
    """
    raw = (os.environ.get("YTDLP_PROXY") or "").strip()
    return raw or None


def _is_cookie_bot_check_error(msg: str) -> bool:
    """True for YouTube's "sign in to confirm you're not a bot" wall specifically,
    as opposed to other fatal reasons (private/removed/geo-blocked) that a fresh
    cookies file or a proxy can't do anything about."""
    m = (msg or "").lower()
    return "sign in to confirm you" in m and "bot" in m


_COOKIE_BOT_CHECK_HINT = (
    "YouTube's bot-check rejected the cookies — usually the server's own "
    "(often datacenter/VPS) IP being flagged, not stale cookies. Set "
    "YTDLP_PROXY to a proxy/SSH tunnel matching where the cookies were "
    "exported to cut down how often this recurs, or re-upload fresh cookies "
    "from a logged-in, non-VPN browser for a temporary unblock."
)


def classify_download_error(msg: str) -> str:
    """Classify a yt-dlp error as ``retry`` or ``fatal``."""
    m = (msg or "").lower()
    fatal_signals = (
        "sign in to confirm you're not a bot",
        "sign in to confirm youre not a bot",
        "confirm your age",
        "private video",
        "this video is private",
        "video has been removed",
        "removed by the user",
        "account associated with this video has been terminated",
        "video is no longer available",
        "video unavailable",
        "not available in your country",
        "not available in your location",
        "blocked it in your country",
        "geo-restrict",
        "geo restrict",
        "geoblock",
        "geo-block",
        "geo block",
    )
    if any(s in m for s in fatal_signals):
        return "fatal"
    retry_signals = (
        "http error 403",
        "403 forbidden",
        "403:",
        "requested format is not available",
        "requested format not available",
        "no formats found",
        "no video formats",
        "empty formats",
    )
    if any(s in m for s in retry_signals):
        return "retry"
    return "fatal"


# Shared with live_monitor.build_backfill_cmd, which shells out to yt-dlp
# directly rather than going through YoutubeDL() — one string, not two drifting
# copies.
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


SOURCE_INFO_FILENAME = "source_info.json"


def _write_source_info(output_dir, info):
    """Persist source-channel metadata as a best-effort sidecar."""
    try:
        from clippyme.domain.banner import suggest_banner

        channel_url = info.get("channel_url") or info.get("uploader_url")
        webpage_url = info.get("webpage_url") or info.get("original_url")
        uploader_id = info.get("uploader_id") or info.get("channel_id")
        banner = suggest_banner(channel_url or webpage_url or "", channel_hint=uploader_id)
        data = {
            "uploader_id": uploader_id,
            "channel_url": channel_url,
            "webpage_url": webpage_url,
            "banner": banner,
        }
        tmp = os.path.join(output_dir, SOURCE_INFO_FILENAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(output_dir, SOURCE_INFO_FILENAME))
    except Exception as exc:  # pragma: no cover - telemetry only, never fatal
        print(f"   ⚠️  source_info capture skipped: {exc}")


def download_youtube_video(url, output_dir=".", cookies_file_path=None):
    """Download a supported remote source with yt-dlp.

    Returns the downloaded path and sanitized title. Both metadata extraction
    and the actual download use the same player-client attempt.
    """
    url = validate_supported_source_url(url)
    _reject_rebound_internal(url)
    print(f"🔍 Debug: yt-dlp version: {yt_dlp.version.__version__}")
    print("📥 Downloading remote video...")
    step_start_time = time.time()

    cookies_path = _resolve_cookies_path(cookies_file_path)
    if cookies_path:
        print(f"🍪 Using cookies file: {cookies_path}")
    else:
        print("⚠️ No cookies file found.")

    # Verbose mode can leak paths, request URLs and headers into job logs, so it
    # stays opt-in. TLS verification stays on unless explicitly overridden.
    ydl_verbose = os.environ.get('YTDLP_VERBOSE') == '1'
    common_ydl_opts = {
        'quiet': not ydl_verbose,
        'verbose': ydl_verbose,
        'no_warnings': False,
        'cookiefile': cookies_path if cookies_path else None,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'nocheckcertificate': os.environ.get('YTDLP_NOCHECKCERT') == '1',
        'throttledratelimit': int(
            (os.environ.get('YTDLP_THROTTLED_RATE') or '').strip() or 100 * 1024
        ),
        'proxy': _resolve_proxy(),
        'cachedir': False,
        'remote_components': ['ejs:github'],
        'http_headers': {
            'User-Agent': DEFAULT_USER_AGENT,
        },
    }

    chain = download_attempt_chain(url)
    switches_client = _is_youtube_url(url)
    last_error = RuntimeError("download attempt chain was empty")
    for i, attempt in enumerate(chain, 1):
        extractor_args = _extractor_args_for(attempt)
        attempt_opts = {**common_ydl_opts}
        if extractor_args:
            attempt_opts['extractor_args'] = extractor_args
        print(f"🔁 Download attempt {i}/{len(chain)} (player_client: {attempt})")
        try:
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', 'remote_video')
                sanitized_title = sanitize_filename(video_title)
                _write_source_info(output_dir, info)

            output_template = os.path.join(output_dir, f'{sanitized_title}.%(ext)s')
            expected_file = os.path.join(output_dir, f'{sanitized_title}.mp4')
            if os.path.exists(expected_file):
                os.remove(expected_file)
                print("🗑️  Removed existing file to re-download with H.264 codec")

            ydl_opts = {
                **attempt_opts,
                'format': build_format_ladder(resolve_max_download_height()),
                # Codec preference as a tiebreak, never as a resolution filter
                # — see build_format_ladder's note on the 1080p trap.
                'format_sort': build_format_sort(),
                'outtmpl': output_template,
                'merge_output_format': 'mp4',
                'overwrites': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            downloaded_file = os.path.join(output_dir, f'{sanitized_title}.mp4')
            if not os.path.exists(downloaded_file):
                for filename in os.listdir(output_dir):
                    if filename.startswith(sanitized_title) and filename.endswith('.mp4'):
                        downloaded_file = os.path.join(output_dir, filename)
                        break
            if not os.path.isfile(downloaded_file):
                raise FileNotFoundError("yt-dlp completed without producing an MP4 file")

            step_end_time = time.time()
            print(
                f"✅ Video downloaded in {step_end_time - step_start_time:.2f}s: "
                f"{downloaded_file}"
            )
            return downloaded_file, sanitized_title
        except Exception as exc:
            last_error = exc
            kind = classify_download_error(str(exc))
            # The bot-check wall is classified "fatal" (it's not a transient
            # network blip retry_signals covers) but it is specifically NOT
            # fatal across player clients: tv/tv_embedded/web_safari use a
            # different auth flow than the default web client and often sail
            # straight past a wall the web client just hit — a real, free
            # workaround this loop already had the machinery for (the player-
            # client chain) but never applied here, since "fatal" used to mean
            # "give up immediately" regardless of untried clients. Only after
            # every client in the chain has hit the SAME wall is it actually
            # exhausted, at which point the YTDLP_PROXY-hint error below fires.
            bot_check = _is_cookie_bot_check_error(str(exc))
            if (kind == "retry" or bot_check) and i < len(chain):
                reason = (
                    "bot-check wall — trying a different player client"
                    if (bot_check and switches_client) else f"retryable: {exc}"
                )
                next_step = "next player_client" if switches_client else "retry"
                print(f"⚠️ Attempt {i} failed ({reason}); {next_step} in 5s...")
                time.sleep(5)
                continue
            break

    print("🚨 SOURCE DOWNLOAD ERROR 🚨", file=sys.stderr)
    if _is_cookie_bot_check_error(str(last_error)):
        error_msg = f"""

❌ ================================================================= ❌
❌ FATAL ERROR: YOUTUBE REJECTED THE COOKIES (bot-check wall)
❌ ================================================================= ❌

YouTube is showing "sign in to confirm you're not a bot" even though a
cookies file is configured. This is usually NOT stale cookies — YouTube's
bot-check also weighs the server's own IP address, and datacenter/VPS
ranges get flagged far more readily than the residential connection the
cookies were originally exported from. A freshly re-uploaded cookies file
can get walled again within hours on a flagged IP.

What actually helps, in order of effort:
1. Route yt-dlp through a proxy that matches where the cookies came from
   (e.g. an SSH -D SOCKS tunnel to your home connection, or a residential
   proxy service): set YTDLP_PROXY, e.g. YTDLP_PROXY=socks5://127.0.0.1:1080
   or an http(s):// proxy URL. This reduces how often the wall reappears,
   rather than just re-uploading cookies after each one.
2. Re-export fresh cookies from a browser that is logged in and not behind
   a VPN, then re-upload via Settings -> Cookies.
3. For a single video: download it manually and use the 'Upload Video' tab.

Technical Details: {last_error}
    """
    else:
        error_msg = f"""

❌ ================================================================= ❌
❌ FATAL ERROR: SOURCE DOWNLOAD FAILED
❌ ================================================================= ❌

The remote platform refused or could not complete the download.

Suggested workaround:
1. Download the video manually to your computer.
2. Use the 'Upload Video' tab in this app to process it.

Technical Details: {last_error}
    """
    print(error_msg, file=sys.stdout)
    print(error_msg, file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    time.sleep(0.5)
    if _is_cookie_bot_check_error(str(last_error)):
        # The ASCII banner above only reaches container logs. RuntimeState.fail()
        # (domain/runtime_state.py) records str(exception)[-2000:] as the
        # dashboard-facing failure reason, so the explanation has to ride in the
        # raised exception itself, not just stdout, or the user only ever sees
        # yt-dlp's bare one-liner in the UI.
        raise RuntimeError(f"{last_error} — {_COOKIE_BOT_CHECK_HINT}") from last_error
    raise last_error
