"""Shared phase orchestration: workspace chaining, dispatch, artifact recording.

The phases themselves are covered by their own modules' tests; this covers the
layer that wires them together — the one both front ends depend on.
"""

import json
import os

import pytest

from clipper_pro import pipeline
from clipper_pro.errors import ValidationError
from clipper_pro.pipeline import (
    PHASE_LABELS,
    PHASES,
    PhaseOptions,
    candidates_from_workspace,
    media_from_workspace,
    plans_from_workspace,
    renders_from_workspace,
    run_phase,
    snapped_or_ranked,
    transcript_from_workspace,
)
from clipper_pro.types import SourceMedia
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import load as workspace_load
from clipper_pro.workspace import record_artifact


@pytest.fixture
def work(tmp_path):
    root = str(tmp_path / "run")
    workspace_init(root)
    return root


def _write_analysis(work, name, payload):
    os.makedirs(os.path.join(work, "analysis"), exist_ok=True)
    with open(os.path.join(work, "analysis", name), "w") as fh:
        json.dump(payload, fh)


class TestPhaseRegistry:
    def test_the_seven_phases_are_in_pipeline_order(self):
        assert PHASES == (
            "ingest", "transcribe", "rank", "cut", "reframe", "render", "export",
        )

    def test_every_phase_has_a_label_for_a_ui(self):
        assert set(PHASE_LABELS) == set(PHASES)
        assert all(PHASE_LABELS[p] for p in PHASES)

    def test_an_unknown_phase_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown phase"):
            run_phase("polish", "/w")

    def test_ingest_without_a_source_is_rejected(self):
        with pytest.raises(ValidationError, match="needs a source"):
            run_phase("ingest", "/w")


class TestPhaseOptions:
    def test_defaults_change_nothing(self):
        opts = PhaseOptions()
        assert opts.sample_rate is None and opts.crf is None
        assert opts.centred is False and opts.require_events is False

    def test_is_immutable(self):
        # Options are threaded through a whole run; a phase must not be able to
        # rewrite them for the phases after it.
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            PhaseOptions().centred = True


class TestWorkspaceChaining:
    def test_media_is_recovered_from_the_ingest_artifact(self, work):
        record_artifact(work, "ingest", {
            "phase": "ingest",
            "source": {"path": "/v/a.mp4", "duration": 12.0, "audio_path": "/a.flac"},
        })
        media = media_from_workspace(work)
        assert media.path == "/v/a.mp4" and media.audio_path == "/a.flac"

    def test_missing_ingest_artifact_names_the_fix(self, work):
        with pytest.raises(ValidationError, match="run 'clipper-pro ingest' first"):
            media_from_workspace(work)

    def test_malformed_ingest_artifact_is_rejected(self, work):
        record_artifact(work, "ingest", {"phase": "ingest"})  # no source record
        with pytest.raises(ValidationError, match="no ingest artifact"):
            media_from_workspace(work)

    def test_transcript_is_recovered(self, work):
        _write_analysis(work, "transcript.json", {
            "words": [{"text": "hi", "start": 0.0, "end": 0.4}], "language": "en",
        })
        result = transcript_from_workspace(work)
        assert [w.text for w in result.words] == ["hi"]

    def test_missing_transcript_names_the_fix(self, work):
        with pytest.raises(ValidationError, match="run 'clipper-pro transcribe' first"):
            transcript_from_workspace(work)

    def test_corrupt_transcript_is_rejected(self, work):
        os.makedirs(os.path.join(work, "analysis"), exist_ok=True)
        with open(os.path.join(work, "analysis", "transcript.json"), "w") as fh:
            fh.write("{not json")
        with pytest.raises(ValidationError, match="not valid JSON"):
            transcript_from_workspace(work)

    def test_candidates_are_recovered(self, work):
        _write_analysis(work, "candidates.json", {
            "clips": [{"start": 1.0, "end": 20.0, "title": "a"}],
        })
        assert candidates_from_workspace(work)[0].title == "a"

    def test_missing_candidates_names_the_fix(self, work):
        with pytest.raises(ValidationError, match="run 'clipper-pro rank' first"):
            candidates_from_workspace(work)


class TestSnappedOrRanked:
    def test_prefers_the_snapped_cuts(self, work):
        _write_analysis(work, "candidates.json", {"clips": [{"start": 1.0, "end": 20.0, "title": "raw"}]})
        _write_analysis(work, "cuts.json", {"clips": [{"start": 1.5, "end": 20.5, "title": "snapped"}]})
        assert snapped_or_ranked(work)[0].title == "snapped"

    def test_falls_back_to_the_raw_candidates(self, work):
        # So reframe still works if the user skipped `cut`.
        _write_analysis(work, "candidates.json", {"clips": [{"start": 1.0, "end": 20.0, "title": "raw"}]})
        assert snapped_or_ranked(work)[0].title == "raw"

    def test_corrupt_cuts_is_rejected_rather_than_silently_falling_back(self, work):
        # Falling back here would quietly discard the snapping the user asked
        # for and render unsnapped edges without saying so.
        _write_analysis(work, "candidates.json", {"clips": [{"start": 1.0, "end": 20.0}]})
        os.makedirs(os.path.join(work, "analysis"), exist_ok=True)
        with open(os.path.join(work, "analysis", "cuts.json"), "w") as fh:
            fh.write("{not json")
        with pytest.raises(ValidationError, match="unreadable"):
            snapped_or_ranked(work)


class TestPlansAndRenders:
    def test_plans_are_recovered(self, work):
        _write_analysis(work, "reframe.json", {"clips": [{
            "clip_index": 0, "start": 0.0, "end": 10.0, "source_width": 1920,
            "source_height": 1080, "fps": 30.0, "keyframes": [],
        }]})
        assert plans_from_workspace(work)[0].clip_index == 0

    def test_missing_plans_names_the_fix(self, work):
        with pytest.raises(ValidationError, match="run 'clipper-pro reframe' first"):
            plans_from_workspace(work)

    def test_missing_renders_is_empty_not_an_error(self, work):
        # Phase 7 reports an unrendered clip rather than refusing to run.
        assert renders_from_workspace(work) == []

    def test_corrupt_renders_is_empty_not_an_error(self, work):
        os.makedirs(os.path.join(work, "analysis"), exist_ok=True)
        with open(os.path.join(work, "analysis", "renders.json"), "w") as fh:
            fh.write("{not json")
        assert renders_from_workspace(work) == []


class TestRunPhaseRecordsArtifacts:
    def test_ingest_records_its_artifact_and_returns_the_payload(self, tmp_path, monkeypatch):
        work = str(tmp_path / "run")
        audio = tmp_path / "a.flac"
        audio.write_bytes(b"\x00" * 100)
        source = tmp_path / "v.mp4"
        source.write_bytes(b"\x00" * 1000)

        monkeypatch.setattr(
            pipeline, "run_ingest",
            lambda *a, **k: SourceMedia(
                path=str(source), duration=20.0, title="v", audio_path=str(audio)
            ),
        )

        payload = run_phase("ingest", work, source=str(source))
        assert payload["phase"] == "ingest"
        assert payload["audio"]["sample_rate"] == 16_000
        # Recorded so the next phase finds it without being told a path.
        assert workspace_load(work)["artifacts"]["ingest"]["phase"] == "ingest"

    def test_options_reach_the_phase(self, tmp_path, monkeypatch):
        work = str(tmp_path / "run")
        source = tmp_path / "v.mp4"
        source.write_bytes(b"\x00")
        seen = {}

        def fake_ingest(src, wd, *, settings=None, cookies_file=None, overwrite=False):
            seen.update(sample_rate=settings.asr_sample_rate, overwrite=overwrite)
            return SourceMedia(path=str(source), duration=1.0, audio_path=str(source))

        monkeypatch.setattr(pipeline, "run_ingest", fake_ingest)
        run_phase(
            "ingest", work, source=str(source),
            options=PhaseOptions(sample_rate=8000, overwrite=True),
        )
        assert seen == {"sample_rate": 8000, "overwrite": True}
