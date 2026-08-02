"""zernio_profile on /api/process and /api/batch must persist as the job's
campaign tag (job_artifacts.save_job_campaign) so an external approval bot
scoped to one campaign can filter GET /api/history to only its own clips.

Same TestClient-without-lifespan pattern as test_process_upload.py: the
handler writes the sidecar synchronously before the job ever reaches a
background worker, so no real subprocess/queue dispatch is needed here.
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from clippyme.api import app as app_module
from clippyme.domain.job_artifacts import load_job_campaign


@pytest.fixture
def client(monkeypatch, tmp_path):
    outputs = tmp_path / "output"
    outputs.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(outputs))
    return TestClient(app_module.app, headers={"Origin": "http://localhost:5175"}), outputs


def test_process_json_persists_the_tagged_campaign(client):
    tc, outputs = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "zernio_profile": "dja"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert load_job_campaign(os.path.join(str(outputs), job_id)) == "dja"


def test_process_json_untagged_defaults_to_default_campaign(client):
    tc, outputs = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert load_job_campaign(os.path.join(str(outputs), job_id)) == "default"


def test_process_json_rejects_invalid_zernio_profile(client):
    tc, _ = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "zernio_profile": "Not Valid!"},
    )
    assert resp.status_code == 400


def test_process_multipart_persists_the_tagged_campaign(client):
    tc, outputs = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        files={"file": ("v.mp4", b"some bytes", "video/mp4")},
        data={"zernio_profile": "ebay_live"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert load_job_campaign(os.path.join(str(outputs), job_id)) == "ebay_live"


def test_batch_persists_the_tagged_campaign_per_job(client):
    tc, outputs = client
    resp = tc.post(
        "/api/batch",
        headers={"X-Gemini-Key": "k"},
        json={
            "urls": [
                "https://www.youtube.com/watch?v=abcdefghijk",
                "https://www.youtube.com/watch?v=zyxwvutsrqp",
            ],
            "zernio_profile": "dja",
        },
    )
    assert resp.status_code == 200
    for job in resp.json()["jobs"]:
        assert load_job_campaign(os.path.join(str(outputs), job["job_id"])) == "dja"


def test_batch_untagged_defaults_to_default_campaign(client):
    tc, outputs = client
    resp = tc.post(
        "/api/batch",
        headers={"X-Gemini-Key": "k"},
        json={"urls": ["https://www.youtube.com/watch?v=abcdefghijk"]},
    )
    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["job_id"]
    assert load_job_campaign(os.path.join(str(outputs), job_id)) == "default"


def test_history_surfaces_the_tagged_campaign_end_to_end(client):
    tc, outputs = client
    resp = tc.post(
        "/api/process",
        headers={"X-Gemini-Key": "k"},
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "zernio_profile": "dja"},
    )
    job_id = resp.json()["job_id"]
    # scan_history only lists jobs with a *_metadata.json — write a minimal
    # one so this job is visible, mirroring what the pipeline would produce.
    job_dir = os.path.join(str(outputs), job_id)
    with open(os.path.join(job_dir, "video_metadata.json"), "w") as f:
        json.dump({"shorts": []}, f)

    history = tc.get("/api/history").json()
    entry = next(j for j in history["jobs"] if j["jobId"] == job_id)
    assert entry["zernioProfile"] == "dja"
