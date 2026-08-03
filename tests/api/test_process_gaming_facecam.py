"""gaming_facecam_position/size on /api/process, /api/batch and
/api/reframe must reach the actual subprocess argv (job_results.build_main_cmd
/ reframe_service.run_reframe) — a manual facecam position is useless if it
gets validated but silently dropped before reaching the pipeline.

Same TestClient-without-lifespan pattern as test_process_campaign_tag.py: the
built cmd is inspected straight out of the in-memory ``jobs`` dict, no real
subprocess/queue dispatch needed.
"""
import pytest
from fastapi.testclient import TestClient

from clippyme.api import app as app_module


@pytest.fixture
def client(monkeypatch, tmp_path):
    outputs = tmp_path / "output"
    outputs.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))
    return TestClient(app_module.app, headers={"Origin": "http://localhost:5175"})


def test_process_json_threads_manual_facecam_position(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "reframe_mode": "gaming",
            "gaming_facecam_position": "top-left",
            "gaming_facecam_size": "L",
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert cmd[cmd.index("--gaming-facecam-position") + 1] == "top-left"
    assert cmd[cmd.index("--gaming-facecam-size") + 1] == "L"


def test_process_json_auto_facecam_omits_flags(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "reframe_mode": "gaming"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert "--gaming-facecam-position" not in cmd


def test_process_multipart_threads_manual_facecam_position(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        files={"file": ("v.mp4", b"some bytes", "video/mp4")},
        data={
            "reframe_mode": "gaming",
            "gaming_facecam_position": "bottom-right",
            "gaming_facecam_size": "S",
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert cmd[cmd.index("--gaming-facecam-position") + 1] == "bottom-right"
    assert cmd[cmd.index("--gaming-facecam-size") + 1] == "S"


def test_process_rejects_invalid_facecam_position(client):
    resp = client.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "gaming_facecam_position": "middle-earth",
        },
    )
    assert resp.status_code == 400


def test_batch_threads_manual_facecam_position_per_job(client):
    resp = client.post(
        "/api/batch",
        headers={"X-Gemini-Key": "k"},
        json={
            "urls": ["https://www.youtube.com/watch?v=abcdefghijk"],
            "reframe_mode": "gaming",
            "gaming_facecam_position": "top-right",
            "gaming_facecam_size": "M",
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["job_id"]
    cmd = app_module.jobs[job_id]["cmd"]
    assert cmd[cmd.index("--gaming-facecam-position") + 1] == "top-right"
