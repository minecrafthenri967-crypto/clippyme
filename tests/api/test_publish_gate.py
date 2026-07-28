"""Optional PUBLISH_GATE_TOKEN gate (security.enforce_publish_gate).

Routes publishing through one external approval caller (e.g. a Discord bot).
Unset token (the default) must be a byte-identical no-op; set token must 403
every /api/publish call that doesn't carry a matching X-Publish-Gate-Token
header. Pure host tests — no cv2/ffmpeg.
"""
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from clippyme.api import app as app_module
from clippyme.api.security import enforce_publish_gate
from clippyme.domain.publish_service import configured_publish_gate_token, publish_gate_active

ORIGIN = {"Origin": "http://localhost:5175"}


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


# --- configured_publish_gate_token / publish_gate_active --------------------

def test_noop_when_unset(monkeypatch):
    monkeypatch.delenv("PUBLISH_GATE_TOKEN", raising=False)
    assert configured_publish_gate_token() is None
    assert publish_gate_active() is False
    enforce_publish_gate(_FakeRequest())  # must not raise


def test_blank_env_counts_as_unset(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "   ")
    assert configured_publish_gate_token() is None
    assert publish_gate_active() is False
    enforce_publish_gate(_FakeRequest())


def test_active_when_set(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    assert configured_publish_gate_token() == "s3cret"
    assert publish_gate_active() is True


# --- enforce_publish_gate ----------------------------------------------------

def test_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        enforce_publish_gate(_FakeRequest())
    assert exc.value.status_code == 403


def test_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    with pytest.raises(HTTPException):
        enforce_publish_gate(_FakeRequest({"x-publish-gate-token": "nope"}))


def test_correct_token_accepted(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    enforce_publish_gate(_FakeRequest({"x-publish-gate-token": "s3cret"}))  # must not raise


# --- integration: real /api/publish endpoint ---------------------------------

def _client():
    return TestClient(app_module.app, headers=ORIGIN)


def _publish_body():
    return {"platforms": [{"platform": "tiktok", "accountId": "acc-1"}]}


def test_publish_endpoint_403s_without_gate_token(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    r = _client().post(f"/api/publish/{uuid.uuid4()}/0", json=_publish_body())
    assert r.status_code == 403
    assert "Discord approval" in r.json()["detail"]


def test_publish_endpoint_rejects_wrong_gate_token(monkeypatch):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    r = _client().post(
        f"/api/publish/{uuid.uuid4()}/0", json=_publish_body(),
        headers={"X-Publish-Gate-Token": "nope"},
    )
    assert r.status_code == 403


def test_publish_endpoint_passes_gate_with_correct_token(monkeypatch):
    """Gate itself must not be what blocks this call — a nonexistent job/clip
    still 404s past it, proving the gate check let the request through."""
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")
    r = _client().post(
        f"/api/publish/{uuid.uuid4()}/0", json=_publish_body(),
        headers={"X-Publish-Gate-Token": "s3cret"},
    )
    assert r.status_code != 403


def test_publish_endpoint_noop_when_gate_unset(monkeypatch):
    monkeypatch.delenv("PUBLISH_GATE_TOKEN", raising=False)
    r = _client().post(f"/api/publish/{uuid.uuid4()}/0", json=_publish_body())
    assert r.status_code != 403
