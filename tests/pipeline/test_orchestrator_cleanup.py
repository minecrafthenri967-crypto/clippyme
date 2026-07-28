from pathlib import Path

from clippyme.pipeline.orchestrator import _cleanup_completed


class _State:
    def __init__(self, checkpoint_dir):
        self.checkpoint_dir = str(checkpoint_dir)


def test_completed_cleanup_deletes_source_but_keeps_reframe_slice(tmp_path, monkeypatch):
    """A local upload used to be exempted here (kept until the retention sweep
    or a manual History delete) — that was an inconsistency, not a feature:
    nothing depends on the original file surviving a successful run, whether
    it arrived as a URL download or a local upload. The reframe source slice
    is a SEPARATE file and must never be touched by this cleanup."""
    source_slice = tmp_path / "source_clip.mp4"
    source_slice.write_bytes(b"source")
    checkpoint = tmp_path / ".clippyme_checkpoint"
    checkpoint.mkdir()
    (checkpoint / "transcript.json").write_text("{}", encoding="utf-8")
    input_video = tmp_path / "upload.mp4"
    input_video.write_bytes(b"input")
    monkeypatch.delenv("CLIPPYME_KEEP_CHECKPOINTS", raising=False)

    _cleanup_completed(
        output_dir=str(tmp_path),
        state=_State(checkpoint),
        input_video=str(input_video),
        keep_original=False,
        all_clips_ready=True,
    )

    assert source_slice.exists()
    assert not input_video.exists()
    assert not checkpoint.exists()


def test_keep_original_preserves_the_source_regardless_of_origin(tmp_path, monkeypatch):
    input_video = tmp_path / "download.mp4"
    input_video.write_bytes(b"input")
    checkpoint = tmp_path / ".clippyme_checkpoint"
    checkpoint.mkdir()
    monkeypatch.setenv("CLIPPYME_KEEP_CHECKPOINTS", "1")

    _cleanup_completed(
        output_dir=str(tmp_path),
        state=_State(checkpoint),
        input_video=str(input_video),
        keep_original=True,
        all_clips_ready=True,
    )

    assert input_video.exists()
    assert checkpoint.exists()


def test_source_removed_after_completed_cleanup(tmp_path, monkeypatch):
    input_video = tmp_path / "download.mp4"
    input_video.write_bytes(b"input")
    checkpoint = tmp_path / ".clippyme_checkpoint"
    checkpoint.mkdir()
    monkeypatch.setenv("CLIPPYME_KEEP_CHECKPOINTS", "1")

    _cleanup_completed(
        output_dir=str(tmp_path),
        state=_State(checkpoint),
        input_video=str(input_video),
        keep_original=False,
        all_clips_ready=True,
    )

    assert not input_video.exists()
    assert checkpoint.exists()


def test_partial_success_keeps_checkpoint_for_resume(tmp_path, monkeypatch):
    checkpoint = tmp_path / ".clippyme_checkpoint"
    checkpoint.mkdir()
    input_video = tmp_path / "upload.mp4"
    input_video.write_bytes(b"input")
    monkeypatch.delenv("CLIPPYME_KEEP_CHECKPOINTS", raising=False)

    _cleanup_completed(
        output_dir=str(tmp_path),
        state=_State(checkpoint),
        input_video=str(input_video),
        keep_original=False,
        all_clips_ready=False,
    )

    assert Path(checkpoint).exists()


def test_missing_input_video_does_not_raise(tmp_path, monkeypatch):
    """A resumed job can reach cleanup with an already-gone input_video
    (e.g. a retried attempt) — this must stay a no-op, not an exception."""
    checkpoint = tmp_path / ".clippyme_checkpoint"
    checkpoint.mkdir()
    monkeypatch.delenv("CLIPPYME_KEEP_CHECKPOINTS", raising=False)

    _cleanup_completed(
        output_dir=str(tmp_path),
        state=_State(checkpoint),
        input_video=str(tmp_path / "gone.mp4"),
        keep_original=False,
        all_clips_ready=True,
    )

    assert not checkpoint.exists()
