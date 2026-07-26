"""Workspace layout, manifest persistence, and resume semantics."""

import json

import pytest

from clipper_pro import SCHEMA_VERSION
from clipper_pro.errors import ValidationError
from clipper_pro.workspace import (
    MANIFEST_NAME,
    SUBDIRS,
    init,
    load,
    manifest_path,
    record_artifact,
    save,
)


class TestInit:
    def test_creates_every_phase_directory(self, tmp_path):
        init(str(tmp_path / "run"))
        for name in SUBDIRS:
            assert (tmp_path / "run" / name).is_dir()

    def test_writes_a_readable_manifest(self, tmp_path):
        manifest = init(str(tmp_path / "run"))
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["artifacts"] == {}
        assert (tmp_path / "run" / MANIFEST_NAME).is_file()

    def test_reinit_reuses_an_existing_workspace(self, tmp_path):
        root = str(tmp_path / "run")
        init(root)
        record_artifact(root, "ingest", {"path": "/a.flac"})
        # Resumability: a second init must not wipe what phase 1 already did.
        assert init(root)["artifacts"]["ingest"] == {"path": "/a.flac"}

    def test_exist_ok_false_refuses_to_reuse(self, tmp_path):
        root = str(tmp_path / "run")
        init(root)
        with pytest.raises(ValidationError, match="already exists"):
            init(root, exist_ok=False)


class TestLoad:
    def test_missing_manifest_is_rejected(self, tmp_path):
        with pytest.raises(ValidationError, match="no workspace manifest"):
            load(str(tmp_path))

    def test_corrupt_manifest_names_the_file(self, tmp_path):
        (tmp_path / MANIFEST_NAME).write_text("{not json")
        with pytest.raises(ValidationError, match="not valid JSON"):
            load(str(tmp_path))

    def test_future_schema_version_is_refused(self, tmp_path):
        (tmp_path / MANIFEST_NAME).write_text(
            json.dumps({"schema_version": SCHEMA_VERSION + 1, "artifacts": {}})
        )
        with pytest.raises(ValidationError, match="schema version"):
            load(str(tmp_path))

    def test_missing_artifacts_key_is_tolerated(self, tmp_path):
        (tmp_path / MANIFEST_NAME).write_text(json.dumps({"schema_version": SCHEMA_VERSION}))
        assert load(str(tmp_path))["artifacts"] == {}


class TestRecordArtifact:
    def test_records_and_persists(self, tmp_path):
        root = str(tmp_path / "run")
        init(root)
        record_artifact(root, "ingest", {"audio": "/a.flac"})
        assert load(root)["artifacts"]["ingest"] == {"audio": "/a.flac"}

    def test_phases_accumulate_rather_than_overwrite_each_other(self, tmp_path):
        root = str(tmp_path / "run")
        init(root)
        record_artifact(root, "ingest", {"a": 1})
        record_artifact(root, "transcribe", {"b": 2})
        assert set(load(root)["artifacts"]) == {"ingest", "transcribe"}

    def test_rerunning_a_phase_replaces_its_own_entry(self, tmp_path):
        root = str(tmp_path / "run")
        init(root)
        record_artifact(root, "ingest", {"take": 1})
        record_artifact(root, "ingest", {"take": 2})
        assert load(root)["artifacts"]["ingest"] == {"take": 2}

    def test_unnamed_phase_is_rejected(self, tmp_path):
        root = str(tmp_path / "run")
        init(root)
        with pytest.raises(ValidationError, match="phase name is required"):
            record_artifact(root, "", {})


def test_save_leaves_no_temp_file_behind(tmp_path):
    # The write is tmp+replace so a crash cannot leave a half-parsed manifest;
    # the temp file must not survive a successful write either.
    root = str(tmp_path / "run")
    init(root)
    save(root, load(root))
    assert not (tmp_path / "run" / (MANIFEST_NAME + ".tmp")).exists()
    assert manifest_path(root).endswith(MANIFEST_NAME)
