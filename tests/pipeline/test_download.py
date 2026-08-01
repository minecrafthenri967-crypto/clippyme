"""Tests for clippyme.pipeline.download helpers (host-runnable; no network)."""
import os

import pytest

from clippyme import netutil
from clippyme.pipeline import download as dl


def _fake_getaddrinfo(*ips):
    """Build a getaddrinfo stub returning the given IP strings."""
    def _stub(host, port, *a, **k):
        return [(None, None, None, "", (ip, 0)) for ip in ips]
    return _stub


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc",
    "https://youtu.be/abcdefghijk?t=1",
    "https://www.twitch.tv/videos/123",
    "https://clips.twitch.tv/FancyClip",
    "https://kick.com/video/1234",
])
def test_validate_supported_source_url_accepts_official_https_hosts(url):
    assert dl.validate_supported_source_url(url) == url


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=abc",
    "https://example.com/video.mp4",
    "https://youtube.com.evil.example/watch?v=abc",
    "https://user@www.youtube.com/watch?v=abc",
    "https://www.youtube.com:444/watch?v=abc",
    "file:///etc/passwd",
    "not a url",
])
def test_validate_supported_source_url_rejects_untrusted_sources(url):
    with pytest.raises(ValueError):
        dl.validate_supported_source_url(url)


def test_reject_rebound_literal_internal_ip_raises():
    with pytest.raises(ValueError):
        dl._reject_rebound_internal("http://127.0.0.1/video")


def test_reject_rebound_literal_public_ip_passes():
    # 8.8.8.8 is public — the lower-level rebound guard remains generic.
    dl._reject_rebound_internal("http://8.8.8.8/video")


def test_reject_rebound_all_internal_resolution_raises(monkeypatch):
    monkeypatch.setattr(netutil.socket, "getaddrinfo", _fake_getaddrinfo("192.168.1.10", "127.0.0.1"))
    with pytest.raises(ValueError):
        dl._reject_rebound_internal("http://rebind.evil.test/x")


def test_reject_rebound_public_resolution_passes(monkeypatch):
    monkeypatch.setattr(netutil.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    dl._reject_rebound_internal("http://example.com/x")  # no raise


def test_reject_rebound_mixed_public_and_internal_raises(monkeypatch):
    # ANY internal address → reject. A split-horizon / round-robin host that
    # returns one public + one loopback/private address must NOT pass.
    monkeypatch.setattr(netutil.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34", "10.0.0.1"))
    with pytest.raises(ValueError):
        dl._reject_rebound_internal("http://example.com/x")


def test_reject_rebound_no_host_returns_none():
    assert dl._reject_rebound_internal("not a url") is None


def test_reject_rebound_resolution_failure_is_swallowed(monkeypatch):
    def _boom(*a, **k):
        raise OSError("dns down")
    monkeypatch.setattr(netutil.socket, "getaddrinfo", _boom)
    assert dl._reject_rebound_internal("http://example.com/x") is None


def test_sanitize_filename_strips_invalid_chars():
    assert dl.sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"


def test_sanitize_filename_replaces_spaces():
    assert dl.sanitize_filename("my cool video") == "my_cool_video"


def test_sanitize_filename_truncates_to_100():
    assert len(dl.sanitize_filename("x" * 250)) == 100


def test_resolve_cookies_explicit_wins(tmp_path):
    explicit = str(tmp_path / "given.txt")
    assert dl._resolve_cookies_path(explicit) == explicit


def test_resolve_cookies_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("YOUTUBE_COOKIES", raising=False)
    os.makedirs("data", exist_ok=True)
    open(os.path.join("data", "cookies.txt"), "w").close()
    resolved = dl._resolve_cookies_path(None)
    assert resolved.endswith(os.path.join("data", "cookies.txt"))
    assert os.path.isabs(resolved)


def test_resolve_cookies_from_env_materializes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YOUTUBE_COOKIES", "# Netscape cookie\nfoo\tbar")
    resolved = dl._resolve_cookies_path(None)
    assert resolved.endswith(os.path.join("data", "cookies_env.txt"))
    with open(resolved) as f:
        assert "Netscape" in f.read()


def test_resolve_cookies_none_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("YOUTUBE_COOKIES", raising=False)
    assert dl._resolve_cookies_path(None) is None


# ── player-client fallback chain ──────────────────────────────────────────

def test_player_client_chain_default(monkeypatch):
    monkeypatch.delenv("YTDLP_PLAYER_CLIENTS", raising=False)
    assert dl._player_client_chain() == ["default", "tv+tv_embedded", "web_safari"]


def test_player_client_chain_env_override(monkeypatch):
    monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", " web_safari , tv , default ")
    assert dl._player_client_chain() == ["web_safari", "tv", "default"]


def test_player_client_chain_blank_env_falls_back(monkeypatch):
    monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", "   ")
    assert dl._player_client_chain() == ["default", "tv+tv_embedded", "web_safari"]


def test_extractor_args_default_is_none():
    assert dl._extractor_args_for("default") is None
    assert dl._extractor_args_for("") is None


def test_extractor_args_single_client():
    assert dl._extractor_args_for("web_safari") == {
        "youtube": {"player_client": ["web_safari"]}
    }


def test_extractor_args_joined_clients():
    assert dl._extractor_args_for("tv+tv_embedded") == {
        "youtube": {"player_client": ["tv", "tv_embedded"]}
    }


# ── classify_download_error (retry vs fatal) ──────────────────────────────

@pytest.mark.parametrize("msg", [
    "ERROR: unable to download video data: HTTP Error 403: Forbidden",
    "Requested format is not available",
    "requested format not available. Use --list-formats",
    "No video formats found!; please report this issue",
    "empty formats returned by extractor",
    "403 Forbidden",
])
def test_classify_retry(msg):
    assert dl.classify_download_error(msg) == "retry"


@pytest.mark.parametrize("msg", [
    "ERROR: Sign in to confirm you're not a bot. Use --cookies",
    "ERROR: Private video. Sign in if you've been granted access",
    "This video is private",
    "Video unavailable. This video has been removed by the user",
    "Video unavailable",
    "The uploader has not made this video available in your country",
    "The uploader has blocked it in your country on copyright grounds",
    "This video is no longer available because the account was terminated",
    "some totally unrecognised failure mode",
    "",
])
def test_classify_fatal(msg):
    assert dl.classify_download_error(msg) == "fatal"


def test_classify_bot_wall_beats_any_incidental_403():
    msg = "Sign in to confirm you're not a bot (HTTP Error 403)"
    assert dl.classify_download_error(msg) == "fatal"


# --- proxy knob + cookie bot-check error classification --------------------

def test_resolve_proxy_unset_is_none(monkeypatch):
    monkeypatch.delenv("YTDLP_PROXY", raising=False)
    assert dl._resolve_proxy() is None


def test_resolve_proxy_blank_is_none(monkeypatch):
    monkeypatch.setenv("YTDLP_PROXY", "   ")
    assert dl._resolve_proxy() is None


def test_resolve_proxy_returns_configured_value(monkeypatch):
    monkeypatch.setenv("YTDLP_PROXY", "socks5://127.0.0.1:1080")
    assert dl._resolve_proxy() == "socks5://127.0.0.1:1080"


@pytest.mark.parametrize("msg", [
    "ERROR: Sign in to confirm you're not a bot. Use --cookies",
    "sign in to confirm you're not a bot (HTTP Error 403)",
    "Sign in to confirm youre not a bot",
])
def test_is_cookie_bot_check_error_matches(msg):
    assert dl._is_cookie_bot_check_error(msg) is True


@pytest.mark.parametrize("msg", [
    "This video is private",
    "Video unavailable. This video has been removed by the user",
    "The uploader has not made this video available in your country",
    "",
    None,
])
def test_is_cookie_bot_check_error_does_not_match_other_fatal_reasons(msg):
    assert dl._is_cookie_bot_check_error(msg) is False


class _FakeBotCheckYDL:
    """Stand-in for yt_dlp.YoutubeDL that always hits the bot-check wall."""

    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=False):
        raise RuntimeError(
            "ERROR: [youtube] Tq6zVW9BQKc: Sign in to confirm you’re not "
            "a bot. Use --cookies-from-browser or --cookies for the "
            "authentication."
        )


def test_download_youtube_video_wraps_bot_check_hint_into_raised_error(monkeypatch, tmp_path):
    # RuntimeState.fail() (domain/runtime_state.py) only ever records
    # str(exception) — the dashboard never sees the ASCII banner printed to
    # stdout, so the proxy/re-upload hint has to live in the raised message.
    monkeypatch.setattr(dl.yt_dlp, "YoutubeDL", _FakeBotCheckYDL)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as exc_info:
        dl.download_youtube_video(
            "https://www.youtube.com/watch?v=Tq6zVW9BQKc", output_dir=str(tmp_path)
        )
    msg = str(exc_info.value)
    assert "Sign in to confirm you" in msg
    assert "YTDLP_PROXY" in msg


class _FakeChainRetryYDL:
    """First attempt (player_client 'default', i.e. no extractor_args) hits
    the bot-check wall; the second (a real player_client) succeeds — tv/
    tv_embedded/web_safari use a different auth flow than the default web
    client and often get past a wall the web client just hit, for free."""

    attempts: list = []

    def __init__(self, opts):
        self._opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=False):
        extractor_args = self._opts.get("extractor_args")
        _FakeChainRetryYDL.attempts.append(extractor_args)
        if extractor_args is None:
            raise RuntimeError("ERROR: Sign in to confirm you're not a bot. Use --cookies")
        return {"title": "ok video"}

    def download(self, urls):
        out_path = self._opts["outtmpl"].replace("%(ext)s", "mp4")
        open(out_path, "w").close()


def test_download_youtube_video_retries_other_player_clients_past_bot_check(monkeypatch, tmp_path):
    _FakeChainRetryYDL.attempts = []
    monkeypatch.setattr(dl.yt_dlp, "YoutubeDL", _FakeChainRetryYDL)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    path, title = dl.download_youtube_video(
        "https://www.youtube.com/watch?v=abc12345678", output_dir=str(tmp_path)
    )
    assert os.path.isfile(path)
    assert title == "ok_video"
    # First attempt (default client, no extractor_args) hit the wall; the
    # chain advanced to a real player_client instead of giving up immediately.
    assert _FakeChainRetryYDL.attempts[0] is None
    assert _FakeChainRetryYDL.attempts[1] is not None


# --- download quality: format ladder + its env-configured cap --------------

def test_format_ladder_applies_the_same_cap_to_every_rung():
    ladder = dl.build_format_ladder(1080)
    rungs = ladder.split('/')
    assert len(rungs) == 5
    # Every rung mentions the SAME height cap — the bug being fixed is a
    # ladder where only the early (avc1) rungs were capped and a later rung
    # was unbounded, silently capping a video's avc1 rendition far below what
    # the video actually offers in another codec.
    for rung in rungs:
        assert '[height<=1080]' in rung
    assert rungs[-1] == 'best[height<=1080]'


def test_format_ladder_uncapped_when_height_is_zero():
    ladder = dl.build_format_ladder(0)
    assert '[height<=' not in ladder
    assert ladder.endswith('/best')


def test_format_ladder_still_prefers_avc1_first_for_cheap_cpu_decode():
    rungs = dl.build_format_ladder(1080).split('/')
    assert 'vcodec^=avc1' in rungs[0]
    assert 'vcodec^=avc1' in rungs[1]
    # ...but rungs beyond that accept any codec, so a video whose 1080p only
    # exists as vp9/av1 still gets the resolution rather than falling all the
    # way to a lower-resolution avc1 rendition.
    assert 'vcodec' not in rungs[2]


@pytest.mark.parametrize("raw,expected", [
    (None, 1080),        # unset → default
    ("", 1080),          # blank → default
    ("1080", 1080),
    ("720", 720),
    ("1440", 1440),
    ("2160", 2160),
    ("0", 0),            # explicit uncapped
    ("not a number", 1080),
    ("900", 1080),       # not one of the offered choices → default, not clamped
])
def test_resolve_max_download_height(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CLIPPYME_MAX_DOWNLOAD_HEIGHT", raising=False)
    else:
        monkeypatch.setenv("CLIPPYME_MAX_DOWNLOAD_HEIGHT", raw)
    assert dl.resolve_max_download_height() == expected
