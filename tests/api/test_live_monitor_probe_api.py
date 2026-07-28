"""POST /api/live-monitor/probe — connectivity/detection check, no monitor
started. TestClient is used WITHOUT its context manager so the FastAPI
lifespan never starts (matches test_live_monitor_config_api.py)."""
from fastapi.testclient import TestClient

from clippyme.api import app as app_module

ORIGIN = {"Origin": "http://localhost:5175"}


def test_probe_success(monkeypatch):
    from clippyme.domain import live_monitor as lm

    async def fake_probe(platform, channel, mode, pc):
        return {"ok": True, "mode": mode, "platform": platform, "channel": channel,
                "live": True, "started_at": None}
    monkeypatch.setattr(lm, "probe_channel", fake_probe)

    client = TestClient(app_module.app, headers=ORIGIN)
    r = client.post("/api/live-monitor/probe",
                     json={"platform": "kick", "channel": "foo", "mode": "live"})

    assert r.status_code == 200, r.text
    assert r.json()["live"] is True


def test_probe_invalid_channel_maps_to_400(monkeypatch):
    from clippyme.domain import live_monitor as lm

    async def fake_probe(platform, channel, mode, pc):
        from clippyme.domain.errors import ValidationError
        raise ValidationError("invalid channel")
    monkeypatch.setattr(lm, "probe_channel", fake_probe)

    client = TestClient(app_module.app, headers=ORIGIN)
    r = client.post("/api/live-monitor/probe",
                     json={"platform": "kick", "channel": "foo", "mode": "live"})

    assert r.status_code == 400, r.text


def test_probe_rejects_bad_platform_at_schema_level():
    client = TestClient(app_module.app, headers=ORIGIN)
    r = client.post("/api/live-monitor/probe",
                     json={"platform": "not-a-real-platform", "channel": "foo"})
    assert r.status_code == 422, r.text


def test_probe_untrusted_origin_rejected():
    client = TestClient(app_module.app, headers={"Origin": "https://evil.example"})
    r = client.post("/api/live-monitor/probe",
                     json={"platform": "kick", "channel": "foo", "mode": "live"})
    assert r.status_code in (403, 400)
