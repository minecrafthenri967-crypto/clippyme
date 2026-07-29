"""Host tests for clippyme.domain.job_artifacts (pure filesystem helpers)."""
import json
import os

import pytest

from clippyme.domain import job_artifacts as ja


def _write_meta(job_dir, base, data):
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, f"{base}_metadata.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def test_find_job_metadata_path_returns_match(tmp_path):
    out = str(tmp_path)
    _write_meta(os.path.join(out, "job1"), "vid", {"a": 1})
    assert ja.find_job_metadata_path("job1", out).endswith("vid_metadata.json")


def test_find_job_metadata_path_returns_newest_when_multiple(tmp_path):
    """With >1 metadata file, the newest-by-mtime wins (consistent with
    job_results._pick_latest_metadata) — not filesystem glob order."""
    out = str(tmp_path)
    job_dir = os.path.join(out, "job1")
    old = _write_meta(job_dir, "old", {"v": "old"})
    new = _write_meta(job_dir, "new", {"v": "new"})
    # Make `new` strictly newer regardless of write timing.
    os.utime(old, (1, 1))
    os.utime(new, (10_000_000, 10_000_000))
    assert ja.find_job_metadata_path("job1", out) == new


def test_find_job_metadata_path_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ja.find_job_metadata_path("nope", str(tmp_path))


def test_load_job_metadata_roundtrip(tmp_path):
    out = str(tmp_path)
    _write_meta(os.path.join(out, "job1"), "vid", {"clips": [1, 2, 3]})
    path, data = ja.load_job_metadata("job1", out)
    assert data["clips"] == [1, 2, 3]
    assert os.path.basename(path) == "vid_metadata.json"


def test_save_job_metadata_atomic_roundtrip(tmp_path):
    meta_path = str(tmp_path / "vid_metadata.json")
    ja.save_job_metadata(meta_path, {"x": "y"})
    with open(meta_path) as f:
        assert json.load(f) == {"x": "y"}
    # tmp sidecar must not linger
    assert not os.path.exists(meta_path + ".tmp")


def test_save_job_metadata_cleans_tmp_on_serialization_failure(tmp_path):
    meta_path = str(tmp_path / "vid_metadata.json")

    class _Unserializable:
        pass

    with pytest.raises(TypeError):
        ja.save_job_metadata(meta_path, {"bad": _Unserializable()})
    # Failed write leaves neither a tmp nor a corrupt target file
    assert not os.path.exists(meta_path + ".tmp")
    assert not os.path.exists(meta_path)


def test_save_job_metadata_overwrite_preserves_old_on_failure(tmp_path):
    meta_path = str(tmp_path / "vid_metadata.json")
    ja.save_job_metadata(meta_path, {"v": 1})

    class _Bad:
        pass

    with pytest.raises(TypeError):
        ja.save_job_metadata(meta_path, {"v": _Bad()})
    # Atomic replace means the original survives a failed rewrite
    with open(meta_path) as f:
        assert json.load(f) == {"v": 1}


def test_relocate_root_job_artifacts_moves_metadata_into_job_dir(tmp_path):
    out = str(tmp_path)
    # main.py wrote metadata into output/ root instead of output/<job_id>/
    stray = os.path.join(out, "job9_vid_metadata.json")
    with open(stray, "w") as f:
        json.dump({"ok": True}, f)
    job_dir = os.path.join(out, "job9")

    assert ja.relocate_root_job_artifacts("job9", job_dir, out) is True
    assert os.path.exists(os.path.join(job_dir, "job9_vid_metadata.json"))
    assert not os.path.exists(stray)


def test_relocate_root_job_artifacts_no_match_returns_false(tmp_path):
    out = str(tmp_path)
    assert ja.relocate_root_job_artifacts("ghost", os.path.join(out, "ghost"), out) is False


def test_record_clip_publish_appends_to_clip_entry(tmp_path):
    out = str(tmp_path)
    _write_meta(os.path.join(out, "job1"), "vid", {"shorts": [{"start": 0}, {"start": 10}]})
    ja.record_clip_publish("job1", 1, out, {"platforms": ["tiktok"], "post_id": "p1"})
    _, data = ja.load_job_metadata("job1", out)
    assert data["shorts"][0].get("published", []) == []
    assert data["shorts"][1]["published"] == [{"platforms": ["tiktok"], "post_id": "p1"}]


def test_record_clip_publish_appends_multiple_records(tmp_path):
    out = str(tmp_path)
    _write_meta(os.path.join(out, "job1"), "vid", {"shorts": [{"start": 0}]})
    ja.record_clip_publish("job1", 0, out, {"post_id": "p1"})
    ja.record_clip_publish("job1", 0, out, {"post_id": "p2"})
    _, data = ja.load_job_metadata("job1", out)
    assert [r["post_id"] for r in data["shorts"][0]["published"]] == ["p1", "p2"]


def test_record_clip_publish_out_of_range_index_is_noop(tmp_path):
    out = str(tmp_path)
    _write_meta(os.path.join(out, "job1"), "vid", {"shorts": [{"start": 0}]})
    ja.record_clip_publish("job1", 5, out, {"post_id": "p1"})
    _, data = ja.load_job_metadata("job1", out)
    assert data["shorts"][0].get("published", []) == []


# --- delete_clip_artifacts ---------------------------------------------------

def _make_clip_files(job_dir, clip_filename, title):
    """Write the raw render, its source slice, cover, and composed output —
    the full on-disk footprint delete_clip_artifacts is expected to clear."""
    os.makedirs(job_dir, exist_ok=True)
    stem = os.path.splitext(clip_filename)[0]
    paths = {
        "clip": os.path.join(job_dir, clip_filename),
        "source": os.path.join(job_dir, f"source_{clip_filename}"),
        "cover": os.path.join(job_dir, f"{stem}_cover.jpg"),
        "composed": os.path.join(job_dir, f"{title}.mp4"),
    }
    for p in paths.values():
        open(p, "wb").close()
    return paths


def test_delete_clip_artifacts_removes_all_four_files(tmp_path):
    out = str(tmp_path)
    job_dir = os.path.join(out, "job1")
    _write_meta(job_dir, "vid", {"shorts": [
        {"video_title_for_youtube_short": "My Clip", "start": 0},
    ]})
    paths = _make_clip_files(job_dir, "My Clip_clip_1.mp4", "My Clip")

    ja.delete_clip_artifacts("job1", 0, out, {"video_title_for_youtube_short": "My Clip"},
                              paths["clip"])

    for p in paths.values():
        assert not os.path.exists(p), p


def test_delete_clip_artifacts_marks_metadata_deleted_and_keeps_positions(tmp_path):
    out = str(tmp_path)
    job_dir = os.path.join(out, "job1")
    _write_meta(job_dir, "vid", {"shorts": [
        {"video_title_for_youtube_short": "First", "start": 0},
        {"video_title_for_youtube_short": "Second", "start": 10},
    ]})
    paths = _make_clip_files(job_dir, "First_clip_1.mp4", "First")

    ja.delete_clip_artifacts("job1", 0, out, {"video_title_for_youtube_short": "First"},
                              paths["clip"])

    _, data = ja.load_job_metadata("job1", out)
    assert data["shorts"][0]["deleted_after_publish"] is True
    # The OTHER clip's entry (and list position) is untouched.
    assert "deleted_after_publish" not in data["shorts"][1]
    assert data["shorts"][1]["video_title_for_youtube_short"] == "Second"


def test_delete_clip_artifacts_missing_files_does_not_raise(tmp_path):
    out = str(tmp_path)
    job_dir = os.path.join(out, "job1")
    _write_meta(job_dir, "vid", {"shorts": [{"video_title_for_youtube_short": "Gone"}]})
    # No files ever created — every target is missing.
    ja.delete_clip_artifacts("job1", 0, out, {"video_title_for_youtube_short": "Gone"},
                              os.path.join(job_dir, "Gone_clip_1.mp4"))
    _, data = ja.load_job_metadata("job1", out)
    assert data["shorts"][0]["deleted_after_publish"] is True


def test_delete_clip_artifacts_out_of_range_index_still_removes_files(tmp_path):
    """No matching metadata entry to mark, but the disk cleanup must still
    happen — the two are independent best-effort steps."""
    out = str(tmp_path)
    job_dir = os.path.join(out, "job1")
    _write_meta(job_dir, "vid", {"shorts": [{"video_title_for_youtube_short": "Only"}]})
    paths = _make_clip_files(job_dir, "Only_clip_1.mp4", "Only")

    ja.delete_clip_artifacts("job1", 7, out, {"video_title_for_youtube_short": "Only"},
                              paths["clip"])

    assert not os.path.exists(paths["clip"])
    assert not os.path.exists(paths["source"])


def test_delete_clip_artifacts_missing_metadata_does_not_raise(tmp_path):
    """No metadata file at all (e.g. already cleaned up) — still best-effort,
    never propagates."""
    out = str(tmp_path)
    job_dir = os.path.join(out, "nope")
    os.makedirs(job_dir, exist_ok=True)
    clip_path = os.path.join(job_dir, "X_clip_1.mp4")
    open(clip_path, "wb").close()

    ja.delete_clip_artifacts("nope", 0, out, {"video_title_for_youtube_short": "X"}, clip_path)
    # No metadata to update, but the file removal still ran.
    assert not os.path.exists(clip_path)
