"""PUBLISH_GATE_TOKEN must stop LiveMonitor's own auto-publish outright.

Unlike the HTTP endpoint (api/test_publish_gate.py), this loop has no request
to attach a header to, so "gate active" means it refuses to call
social_publisher.publish_clip at all — the clip stays finished-but-unpublished
on disk for whatever external approval workflow holds the token.
"""
import asyncio
import os

import pytest

from clippyme.domain.live_monitor import LiveMonitor
from clippyme.integrations import social_publisher


def _monitor(tmp_path):
    return LiveMonitor(id="kick:foo", jobs={}, job_queue=None, output_dir=str(tmp_path))


def _entry():
    return {
        "job_id": "job-1",
        "clip": {"video_url": "/videos/job-1/clip_1.mp4"},
        "composed_path": None,
    }


def test_publish_gate_active_skips_publish_call(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLISH_GATE_TOKEN", "s3cret")

    def _boom(**kwargs):
        raise AssertionError("publish_clip must not be called while the gate is active")

    monkeypatch.setattr(social_publisher, "publish_clip", _boom)

    mon = _monitor(tmp_path)
    asyncio.run(mon._publish_one(_entry()))

    assert mon.clips_published == 0
    assert mon._published == set()
    assert mon._pending_publish == []  # left alone, not queued — never drains


def test_publish_gate_inactive_reaches_publish_call(monkeypatch, tmp_path):
    """Sanity check the test's own wiring: without the gate, the same entry
    does reach publish_clip (so the skip above is provably the gate's doing)."""
    monkeypatch.delenv("PUBLISH_GATE_TOKEN", raising=False)
    called = {}

    def _fake_publish_clip(**kwargs):
        called.update(kwargs)
        return {"post_id": "p1", "scheduled_for": None}

    monkeypatch.setattr(social_publisher, "publish_clip", _fake_publish_clip)

    mon = _monitor(tmp_path)
    mon.cfg = {
        "title_template": "", "caption_template": "", "platforms": {},
        "timezone": "Europe/Rome", "delete_after_publish": False,
    }
    clip_path = os.path.join(str(tmp_path), "job-1", "clip_1.mp4")
    os.makedirs(os.path.dirname(clip_path), exist_ok=True)
    with open(clip_path, "w") as fh:
        fh.write("fake")

    entry = _entry()
    entry["composed_path"] = clip_path
    asyncio.run(mon._publish_one(entry))

    assert called  # publish_clip was reached
    assert mon.clips_published == 1
