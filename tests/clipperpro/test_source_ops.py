"""Source classification and local-path resolution."""

import os

import pytest

from clipper_pro.errors import ValidationError
from clipper_pro.ingest.source_ops import (
    SOURCE_LOCAL,
    SOURCE_REMOTE,
    classify_source,
    resolve_local_source,
)


class TestClassifySource:
    @pytest.mark.parametrize(
        "url",
        [
            "https://youtu.be/abc123",
            "https://www.youtube.com/watch?v=abc",
            "http://example.com/v.mp4",
            "  https://kick.com/x  ",
        ],
    )
    def test_http_urls_are_remote(self, url):
        assert classify_source(url) == SOURCE_REMOTE

    @pytest.mark.parametrize(
        "path",
        ["/videos/a.mp4", "a.mp4", "./rel/a.mkv", "~/Movies/a.mov", r"C:\videos\a.mp4"],
    )
    def test_paths_are_local(self, path):
        # A Windows drive letter parses as a one-character "scheme"; it must not
        # be mistaken for a URL.
        assert classify_source(path) == SOURCE_LOCAL

    @pytest.mark.parametrize("ref", ["file:///etc/passwd", "ftp://host/v.mp4", "data:video/mp4;base64,AA"])
    def test_other_schemes_are_rejected_not_coerced_to_paths(self, ref):
        # Coercing an unknown scheme into "a path" is how a path ends up fetched.
        with pytest.raises(ValidationError, match="unsupported source scheme"):
            classify_source(ref)

    @pytest.mark.parametrize("ref", ["", "   ", None])
    def test_empty_reference_is_rejected(self, ref):
        with pytest.raises(ValidationError, match="empty"):
            classify_source(ref)

    def test_classification_does_not_vet_the_host(self):
        # Platform allow-listing belongs to clippyme.pipeline.download; this
        # function answers only "remote or local".
        assert classify_source("https://evil.invalid/v.mp4") == SOURCE_REMOTE


class TestResolveLocalSource:
    def test_returns_an_absolute_path(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00")
        resolved = resolve_local_source(str(video))
        assert resolved == str(video)
        assert os.path.isabs(resolved)

    def test_expands_user_and_relative_segments(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00")
        monkeypatch.chdir(tmp_path)
        assert resolve_local_source("./clip.mp4") == str(video)

    def test_missing_file_is_rejected(self, tmp_path):
        with pytest.raises(ValidationError, match="not found"):
            resolve_local_source(str(tmp_path / "nope.mp4"))

    def test_directory_is_rejected(self, tmp_path):
        with pytest.raises(ValidationError, match="not a file"):
            resolve_local_source(str(tmp_path))

    def test_unsupported_container_is_rejected(self, tmp_path):
        doc = tmp_path / "notes.txt"
        doc.write_text("hi")
        with pytest.raises(ValidationError, match="unsupported source container"):
            resolve_local_source(str(doc))

    @pytest.mark.parametrize("suffix", [".mp4", ".MKV", ".mov", ".webm", ".avi", ".m4v"])
    def test_accepts_the_documented_containers_case_insensitively(self, tmp_path, suffix):
        video = tmp_path / f"clip{suffix}"
        video.write_bytes(b"\x00")
        assert resolve_local_source(str(video)) == str(video)
