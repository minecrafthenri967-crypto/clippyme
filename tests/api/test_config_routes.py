"""Behavioural tests for the config-family HTTP surface.

These routes (``/api/config`` + cookies/fonts/logo/zernio/models) were the one
state-changing surface with **zero** endpoint coverage. They are guarded by
``require_trusted_config_request`` and do their own upload validation
(magic-byte checks, size caps, name allow-lists), so a silent regression here
is a security regression.

The suite is deliberately import-module-agnostic: every route is exercised
through the composed ``app`` object, so it holds whether the handlers live in
``app.py`` or in an extracted ``config_routes`` router. Disk writes are
isolated by ``chdir`` into a tmp dir (every path these handlers touch is
relative: ``data/cookies.txt``, ``data/logo.png``, ``data/fonts/``), and the
masking test is a real save→load round-trip — no monkeypatching of the
persistence layer.

TestClient is used WITHOUT its context manager so the FastAPI lifespan
(workers, journal recovery) never starts — we only want the routing + handler
bodies.
"""
import base64
import struct

import pytest
from fastapi.testclient import TestClient

import clippyme.api.app as app_module
import clippyme.api.config_routes as config_module

# A trusted browser origin (in the default allow-list) — the gate accepts it
# via its Origin branch without needing a private client IP.
ORIGIN = {"Origin": "http://localhost:5175"}

# Tiny structurally-valid sfnt and a real 1x1 PNG.
TTF_MAGIC = (
    b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 0, 0, 0)
    + b"head" + b"\x00" * 4 + struct.pack(">II", 28, 4) + b"data"
)
PNG_MAGIC = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
NETSCAPE_COOKIES = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tk\tv\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A trusted-origin client whose disk writes land under a tmp dir."""
    monkeypatch.chdir(tmp_path)
    return TestClient(app_module.app, headers=ORIGIN)


# --- trusted-client gate ----------------------------------------------------

def test_cross_site_request_rejected(client):
    r = client.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_untrusted_origin_rejected(client):
    r = client.get("/api/config", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


# --- /api/config round-trip + secret masking --------------------------------

def test_config_roundtrip_masks_secrets(client):
    """POST then GET: secret keys come back masked, plain flags verbatim."""
    r = client.post(
        "/api/config",
        json={"keys": {"GEMINI_API_KEY": "abcd12345678wxyz", "GEMINI_MODEL": "gemini-3.5-flash"}},
    )
    assert r.status_code == 200 and r.json()["success"] is True

    got = client.get("/api/config").json()
    # 16-char secret → first4…last4, never verbatim.
    assert got["GEMINI_API_KEY"] == "abcd...wxyz"
    assert "12345678" not in got["GEMINI_API_KEY"]
    # Non-secret flag passes through untouched.
    assert got["GEMINI_MODEL"] == "gemini-3.5-flash"


def test_config_short_secret_fully_masked(client):
    client.post("/api/config", json={"keys": {"HF_TOKEN": "short"}})
    got = client.get("/api/config").json()
    assert got["HF_TOKEN"] == "********"


def test_config_rejects_unknown_key(client):
    response = client.post("/api/config", json={"keys": {"GEMNI_API_KEY": "typo"}})
    assert response.status_code == 422


def test_config_rejects_invalid_provider_and_model(client):
    assert client.post(
        "/api/config", json={"keys": {"TRANSCRIPTION_PROVIDER": "other"}}
    ).status_code == 422
    assert client.post(
        "/api/config", json={"keys": {"GEMINI_MODEL": "not-gemini"}}
    ).status_code == 422


# --- /api/config/models (network call stubbed) ------------------------------

def test_models_lists_via_provided_key(client, monkeypatch):
    monkeypatch.setattr(config_module, "list_available_models", lambda key: ["gemini-a", "gemini-b"])
    r = client.get("/api/config/models", headers={"X-Gemini-Key": "dummy"})
    assert r.status_code == 200
    assert r.json() == ["gemini-a", "gemini-b"]


# --- cookies ----------------------------------------------------------------

def test_cookies_upload_status_delete(client):
    assert client.get("/api/config/cookies/status").json() == {"configured": False}

    r = client.post("/api/config/cookies", files={"cookies_file": ("cookies.txt", NETSCAPE_COOKIES)})
    assert r.status_code == 200
    assert client.get("/api/config/cookies/status").json() == {"configured": True}

    assert client.request("DELETE", "/api/config/cookies").status_code == 200
    assert client.get("/api/config/cookies/status").json() == {"configured": False}


def test_cookies_reject_non_netscape(client):
    r = client.post("/api/config/cookies", files={"cookies_file": ("c.txt", b"just some random text no tabs")})
    assert r.status_code == 400


def test_cookies_reject_non_utf8(client):
    r = client.post("/api/config/cookies", files={"cookies_file": ("c.txt", b"\xff\xfe\x00bad")})
    assert r.status_code == 400


def test_cookies_reject_oversize(client):
    big = b"# Netscape HTTP Cookie File\n" + b"a\t" * (6 * 1024 * 1024)
    r = client.post("/api/config/cookies", files={"cookies_file": ("c.txt", big)})
    assert r.status_code == 413


# --- fonts ------------------------------------------------------------------

def test_font_upload_and_delete(client):
    r = client.post("/api/config/fonts", files={"font_file": ("Stratos.ttf", TTF_MAGIC)})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Stratos"
    assert "Stratos" in body["fonts"]

    assert client.request("DELETE", "/api/config/fonts/Stratos").status_code == 200


def test_font_reject_bad_extension(client):
    r = client.post("/api/config/fonts", files={"font_file": ("evil.exe", TTF_MAGIC)})
    assert r.status_code == 400


def test_font_reject_bad_name(client):
    r = client.post("/api/config/fonts", files={"font_file": ("../etc/passwd.ttf", TTF_MAGIC)})
    # basename strips the traversal; the resulting stem still fails the
    # allow-list or writes safely — either way it must never 200 with a path.
    assert r.status_code in (400, 200)
    if r.status_code == 200:
        assert "/" not in r.json()["name"]


def test_font_reject_non_font_bytes(client):
    r = client.post("/api/config/fonts", files={"font_file": ("fake.ttf", b"not a real font at all")})
    assert r.status_code == 400


def test_font_delete_missing_is_404(client):
    assert client.request("DELETE", "/api/config/fonts/DoesNotExist").status_code == 404


# --- logo -------------------------------------------------------------------

def test_logo_upload_status_delete(client):
    assert client.get("/api/config/logo/status").json() == {"configured": False}

    r = client.post("/api/config/logo", files={"logo_file": ("logo.png", PNG_MAGIC)})
    assert r.status_code == 200
    assert client.get("/api/config/logo/status").json() == {"configured": True}

    assert client.request("DELETE", "/api/config/logo").status_code == 200
    assert client.get("/api/config/logo/status").json() == {"configured": False}


def test_logo_reject_non_png(client):
    r = client.post("/api/config/logo", files={"logo_file": ("logo.png", b"GIF89a not a png")})
    assert r.status_code == 400


def test_logo_rejects_truncated_png(client):
    response = client.post(
        "/api/config/logo", files={"logo_file": ("logo.png", b"\x89PNG\r\n\x1a\n" + b"broken")}
    )
    assert response.status_code == 400


# --- player images ------------------------------------------------------------

def test_player_image_list_starts_empty(client):
    r = client.get("/api/config/player-images")
    assert r.status_code == 200
    assert r.json()["players"] == []


def test_player_image_upload_list_delete(client):
    r = client.post(
        "/api/config/player-images",
        data={"name": "LeBron James"},
        files={"image_file": ("photo.png", PNG_MAGIC)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "LeBron James"
    assert "LeBron James" in body["players"]

    got = client.get("/api/config/player-images")
    assert got.json()["players"] == ["LeBron James"]

    assert client.request("DELETE", "/api/config/player-images/LeBron James").status_code == 200
    assert client.get("/api/config/player-images").json()["players"] == []


def test_player_image_accepts_accented_name(client):
    r = client.post(
        "/api/config/player-images",
        data={"name": "Ronald Acuña"},
        files={"image_file": ("photo.png", PNG_MAGIC)},
    )
    assert r.status_code == 200
    assert "Ronald Acuña" in r.json()["players"]


def test_player_image_rejects_path_traversal_name(client):
    r = client.post(
        "/api/config/player-images",
        data={"name": "../../etc/passwd"},
        files={"image_file": ("photo.png", PNG_MAGIC)},
    )
    assert r.status_code == 400


def test_player_image_rejects_non_png(client):
    r = client.post(
        "/api/config/player-images",
        data={"name": "LeBron James"},
        files={"image_file": ("photo.png", b"not a png at all")},
    )
    assert r.status_code == 400


def test_player_image_rejects_truncated_png(client):
    r = client.post(
        "/api/config/player-images",
        data={"name": "LeBron James"},
        files={"image_file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"broken")},
    )
    assert r.status_code == 400


def test_player_image_delete_missing_is_404(client):
    r = client.request("DELETE", "/api/config/player-images/Nobody")
    assert r.status_code == 404


def test_player_image_upload_oversized_is_413(client, monkeypatch):
    import clippyme.api.config_routes as config_module
    monkeypatch.setattr(config_module, "PLAYER_IMAGE_MAX_BYTES", 4)
    r = client.post(
        "/api/config/player-images",
        data={"name": "LeBron James"},
        files={"image_file": ("photo.png", PNG_MAGIC)},
    )
    assert r.status_code == 413


# --- zernio -----------------------------------------------------------------

def test_zernio_config_roundtrip(client):
    r = client.post(
        "/api/config/zernio",
        json={"api_key": "zk_test_secret_key", "accounts": {"tiktok": "acc1"}, "timezone": "Europe/Rome"},
    )
    assert r.status_code == 200
    status = r.json()
    # api_key must never echo verbatim.
    assert "zk_test_secret_key" not in str(status)

    got = client.get("/api/config/zernio")
    assert got.status_code == 200


def test_zernio_accounts_requires_key(client, monkeypatch):
    # No key configured (fresh tmp cwd) → 400 before any network call.
    monkeypatch.setattr(config_module, "load_zernio_config", lambda profile="default": {})
    r = client.get("/api/zernio/accounts")
    assert r.status_code == 400


# --- zernio profiles ----------------------------------------------------------


def test_zernio_profiles_list_starts_with_default_only(client):
    r = client.get("/api/config/zernio/profiles")
    assert r.status_code == 200
    assert r.json()["profiles"] == [{"id": "default", "label": "Default", "configured": False}]


def test_zernio_profiles_create_then_configure_isolated(client):
    r = client.post("/api/config/zernio/profiles", json={"id": "ebay_live", "label": "eBay Live"})
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["profiles"]]
    assert ids == ["default", "ebay_live"]

    r = client.post(
        "/api/config/zernio?profile=ebay_live",
        json={"api_key": "zk_ebay_secret", "timezone": "Europe/Rome"},
    )
    assert r.status_code == 200
    assert r.json()["configured"] is True

    # Default profile is untouched by the write to "ebay_live".
    default_status = client.get("/api/config/zernio").json()
    assert default_status["configured"] is False
    ebay_status = client.get("/api/config/zernio?profile=ebay_live").json()
    assert ebay_status["configured"] is True


def test_zernio_profiles_create_rejects_default(client):
    r = client.post("/api/config/zernio/profiles", json={"id": "default"})
    assert r.status_code == 422  # schema-level rejection


def test_zernio_profiles_create_duplicate_is_400(client):
    client.post("/api/config/zernio/profiles", json={"id": "ebay_live"})
    r = client.post("/api/config/zernio/profiles", json={"id": "ebay_live"})
    assert r.status_code == 400


def test_zernio_profiles_rename_updates_label(client):
    client.post("/api/config/zernio/profiles", json={"id": "ebay_live", "label": "Old"})
    r = client.patch("/api/config/zernio/profiles/ebay_live", json={"label": "New Label"})
    assert r.status_code == 200
    ebay = next(p for p in r.json()["profiles"] if p["id"] == "ebay_live")
    assert ebay["label"] == "New Label"


def test_zernio_profiles_rename_missing_profile_is_404(client):
    r = client.patch("/api/config/zernio/profiles/ghost", json={"label": "New"})
    assert r.status_code == 404


def test_zernio_profiles_delete_removes_it(client):
    client.post("/api/config/zernio/profiles", json={"id": "ebay_live"})
    r = client.delete("/api/config/zernio/profiles/ebay_live")
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["profiles"]] == ["default"]


def test_zernio_profiles_delete_missing_is_404(client):
    r = client.delete("/api/config/zernio/profiles/ghost")
    assert r.status_code == 404


def test_zernio_profiles_delete_default_is_400(client):
    r = client.delete("/api/config/zernio/profiles/default")
    assert r.status_code == 400


# --- caption presets ----------------------------------------------------------


def test_caption_presets_list_starts_empty(client):
    r = client.get("/api/config/caption-presets")
    assert r.status_code == 200
    assert r.json()["presets"] == []


def test_caption_presets_save_then_list(client):
    r = client.post("/api/config/caption-presets", json={
        "id": "johns_breaks", "label": "John's Card Breaks",
        "text": "#eBayLive #JohnsCardBreaks @johnscardbreaks #tradingcards #breaks",
    })
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert len(presets) == 1
    assert presets[0]["id"] == "johns_breaks"
    assert presets[0]["label"] == "John's Card Breaks"

    got = client.get("/api/config/caption-presets")
    assert got.json()["presets"][0]["id"] == "johns_breaks"


def test_caption_presets_save_upserts_by_id(client):
    client.post("/api/config/caption-presets", json={"id": "s1", "label": "One", "text": "a"})
    r = client.post("/api/config/caption-presets", json={"id": "s1", "label": "One Renamed", "text": "b"})
    presets = r.json()["presets"]
    assert len(presets) == 1
    assert presets[0]["label"] == "One Renamed"
    assert presets[0]["text"] == "b"


def test_caption_presets_rejects_invalid_id(client):
    r = client.post("/api/config/caption-presets", json={"id": "Has Spaces", "label": "L", "text": "t"})
    assert r.status_code == 422


def test_caption_presets_rejects_blank_label(client):
    r = client.post("/api/config/caption-presets", json={"id": "s1", "label": "", "text": "t"})
    assert r.status_code == 422


def test_caption_presets_delete_removes_it(client):
    client.post("/api/config/caption-presets", json={"id": "s1", "label": "One", "text": "t"})
    r = client.delete("/api/config/caption-presets/s1")
    assert r.status_code == 200
    assert r.json()["presets"] == []


def test_caption_presets_delete_missing_is_404(client):
    r = client.delete("/api/config/caption-presets/ghost")
    assert r.status_code == 404
