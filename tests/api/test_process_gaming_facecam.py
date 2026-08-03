"""gaming_facecam_box on /api/process, /api/batch and /api/reframe must reach
the actual subprocess argv (job_results.build_main_cmd / reframe_service.
run_reframe) — a manually drawn facecam box is useless if it gets validated
but silently dropped before reaching the pipeline.

Same TestClient-without-lifespan pattern as test_process_campaign_tag.py: the
built cmd is inspected straight out of the in-memory ``jobs`` dict, no real
subprocess/queue dispatch needed.
"""
import json

import pytest
from fastapi.testclient import TestClient

from clippyme.api import app as app_module

_BOX = {"x": 0.6, "y": 0.05, "w": 0.35, "h": 0.3}


@pytest.fixture
def client(monkeypatch, tmp_path):
    outputs = tmp_path / "output"
    outputs.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))
    return TestClient(app_module.app, headers={"Origin": "http://localhost:5175"})


def test_process_json_threads_manual_facecam_box(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "reframe_mode": "gaming",
            "gaming_facecam_box": _BOX,
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert cmd[cmd.index("--gaming-facecam-x") + 1] == "0.6"
    assert cmd[cmd.index("--gaming-facecam-y") + 1] == "0.05"
    assert cmd[cmd.index("--gaming-facecam-w") + 1] == "0.35"
    assert cmd[cmd.index("--gaming-facecam-h") + 1] == "0.3"


def test_process_json_no_box_omits_flags(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "reframe_mode": "gaming"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert "--gaming-facecam-x" not in cmd


def test_process_multipart_threads_manual_facecam_box(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        files={"file": ("v.mp4", b"some bytes", "video/mp4")},
        data={
            "reframe_mode": "gaming",
            "gaming_facecam_box": json.dumps(_BOX),
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert cmd[cmd.index("--gaming-facecam-x") + 1] == "0.6"
    assert cmd[cmd.index("--gaming-facecam-w") + 1] == "0.35"


def test_process_rejects_invalid_facecam_box(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "gaming_facecam_box": {"x": 1.5, "y": 0, "w": 0.1, "h": 0.1},
        },
    )
    assert resp.status_code == 400


def test_process_multipart_rejects_malformed_facecam_box_json(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        files={"file": ("v.mp4", b"some bytes", "video/mp4")},
        data={"reframe_mode": "gaming", "gaming_facecam_box": "{not json"},
    )
    assert resp.status_code == 400


def test_batch_threads_manual_facecam_box_per_job(client):
    resp = client.post(
        "/api/batch",
        headers={"X-Gemini-Key": "k"},
        json={
            "urls": ["https://www.youtube.com/watch?v=abcdefghijk"],
            "reframe_mode": "gaming",
            "gaming_facecam_box": _BOX,
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert cmd[cmd.index("--gaming-facecam-x") + 1] == "0.6"
