"""Tests for clippyme.domain.history_service.

Covers strict UUID validation and disk scanning (valid/invalid dirs, missing
metadata, corrupt JSON, mtime-descending sort). Uses tmp_path so no real
output dir is read.
"""
import json
import os

from clippyme.domain import history_service as hs

VALID_UUID = "12345678-1234-4123-8123-1234567890ab"
VALID_UUID_2 = "abcdef01-2345-4678-9abc-def012345678"


def test_is_valid_job_id_accepts_uuid4():
    assert hs.is_valid_job_id(VALID_UUID) is True


def test_is_valid_job_id_rejects_non_str():
    assert hs.is_valid_job_id(None) is False
    assert hs.is_valid_job_id(12345) is False
    assert hs.is_valid_job_id(b"bytes") is False


def test_is_valid_job_id_rejects_loose_garbage():
    # The old loose regex accepted 36 hyphens / wrong version nibble.
    assert hs.is_valid_job_id("-" * 36) is False
    assert hs.is_valid_job_id("12345678-1234-1123-8123-1234567890ab") is False  # v1, not v4
    assert hs.is_valid_job_id("not-a-uuid") is False


def _make_job(output_dir, job_id, *, clips, mtime=None):
    job_dir = os.path.join(output_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)
    meta = os.path.join(job_dir, "myvideo_metadata.json")
    with open(meta, "w") as f:
        json.dump({"shorts": clips, "cost_analysis": {"total_cost": 0.42}}, f)
    # Create the referenced clip files so they count.
    for i, _ in enumerate(clips):
        open(os.path.join(job_dir, f"myvideo_clip_{i + 1}.mp4"), "wb").close()
    if mtime is not None:
        os.utime(job_dir, (mtime, mtime))
    return job_dir


def test_scan_history_empty_dir(tmp_path):
    assert hs.scan_history(str(tmp_path)) == []


def test_scan_history_skips_invalid_job_dirs(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "not-a-uuid"))
    _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}])
    out = hs.scan_history(str(tmp_path))
    assert len(out) == 1
    assert out[0]["jobId"] == VALID_UUID


def test_scan_history_skips_dir_without_metadata(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), VALID_UUID))  # no metadata file
    assert hs.scan_history(str(tmp_path)) == []


def test_scan_history_tolerates_corrupt_metadata(tmp_path):
    job_dir = os.path.join(str(tmp_path), VALID_UUID)
    os.makedirs(job_dir)
    with open(os.path.join(job_dir, "x_metadata.json"), "w") as f:
        f.write("{broken")
    # Corrupt JSON is swallowed per-entry, not raised.
    assert hs.scan_history(str(tmp_path)) == []


def test_scan_history_sorted_by_mtime_desc(tmp_path):
    _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}], mtime=1000)
    _make_job(str(tmp_path), VALID_UUID_2, clips=[{"start": 0, "end": 10}], mtime=2000)
    out = hs.scan_history(str(tmp_path))
    assert [e["jobId"] for e in out] == [VALID_UUID_2, VALID_UUID]


def test_scan_history_reports_clip_count_and_cost(tmp_path):
    _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}, {"start": 11, "end": 20}])
    out = hs.scan_history(str(tmp_path))
    assert out[0]["clipCount"] == 2
    assert out[0]["cost"] == 0.42


def test_scan_history_surfaces_title_matching_source(tmp_path):
    _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}])
    out = hs.scan_history(str(tmp_path))
    assert out[0]["title"] == out[0]["source"] == "myvideo"


def test_scan_history_resolves_title_based_clip_filename(tmp_path):
    # New-format metadata (task 4): file on disk is the sanitized-title name,
    # persisted as clip_filename, not the positional myvideo_clip_1.mp4.
    job_dir = os.path.join(str(tmp_path), VALID_UUID)
    os.makedirs(job_dir)
    clips = [{"start": 0, "end": 10, "clip_filename": "my_viral_title_clip_1.mp4"}]
    with open(os.path.join(job_dir, "myvideo_metadata.json"), "w") as f:
        json.dump({"shorts": clips, "cost_analysis": {"total_cost": 0.42}}, f)
    open(os.path.join(job_dir, "my_viral_title_clip_1.mp4"), "wb").close()

    out = hs.scan_history(str(tmp_path))
    assert len(out) == 1
    assert out[0]["clipCount"] == 1
    assert out[0]["clips"][0]["video_url"] == f"/videos/{VALID_UUID}/my_viral_title_clip_1.mp4"


def test_scan_history_surfaces_published_records(tmp_path):
    clips = [
        {"start": 0, "end": 10, "published": [{"platforms": ["tiktok"], "post_id": "p1"}]},
        {"start": 11, "end": 20},
    ]
    _make_job(str(tmp_path), VALID_UUID, clips=clips)
    out = hs.scan_history(str(tmp_path))
    assert out[0]["clips"][0]["published"] == [{"platforms": ["tiktok"], "post_id": "p1"}]
    assert out[0]["clips"][1]["published"] == []
    assert out[0]["publishedCount"] == 1


def test_scan_history_surfaces_hook_text(tmp_path):
    # backfill_hook_text guarantees every clip a non-empty viral_hook_text;
    # an external consumer (e.g. an approval bot compositing before publish)
    # needs it exposed under the API's own field name, not the raw one.
    clips = [{"start": 0, "end": 10, "viral_hook_text": "Wait for it"}]
    _make_job(str(tmp_path), VALID_UUID, clips=clips)
    out = hs.scan_history(str(tmp_path))
    assert out[0]["clips"][0]["hook_text"] == "Wait for it"


def test_scan_history_hook_text_defaults_empty(tmp_path):
    clips = [{"start": 0, "end": 10}]
    _make_job(str(tmp_path), VALID_UUID, clips=clips)
    out = hs.scan_history(str(tmp_path))
    assert out[0]["clips"][0]["hook_text"] == ""


def test_scan_history_zernio_profile_defaults_to_default(tmp_path):
    # A job submitted before campaign tagging existed has no sidecar file.
    _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}])
    out = hs.scan_history(str(tmp_path))
    assert out[0]["zernioProfile"] == "default"


def test_scan_history_surfaces_tagged_zernio_profile(tmp_path):
    from clippyme.domain.job_artifacts import save_job_campaign

    job_dir = _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}])
    save_job_campaign(job_dir, "dja")
    out = hs.scan_history(str(tmp_path))
    assert out[0]["zernioProfile"] == "dja"


def test_scan_history_compose_recipe_is_none_when_untagged(tmp_path):
    # No stored preference → consumers keep their own defaults, exactly as
    # they behaved before recipes were persisted.
    _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}])
    out = hs.scan_history(str(tmp_path))
    assert out[0]["composeRecipe"] is None


def test_scan_history_surfaces_the_stored_compose_recipe(tmp_path):
    # This is what lets an external approval bot burn the layers the user
    # configured in Create instead of a recipe built from its own env vars.
    from clippyme.domain.job_artifacts import save_job_campaign

    recipe = {
        "toggles": {"hook": True, "subtitles": True},
        "hook_params": {"position": 0.42, "bg_enabled": True},
    }
    job_dir = _make_job(str(tmp_path), VALID_UUID, clips=[{"start": 0, "end": 10}])
    save_job_campaign(job_dir, "dja", compose=recipe)
    out = hs.scan_history(str(tmp_path))
    assert out[0]["composeRecipe"] == recipe
    assert out[0]["zernioProfile"] == "dja"
