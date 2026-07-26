"""Phase 4: conversion to/from the host snapper, and the cascade's real effect.

The cascade arithmetic belongs to ``clippyme.pipeline.cut_ops`` and is tested
there. These tests cover what phase 4 adds — the type conversion, the movement
record, the renderability guard — plus a few end-to-end assertions that the
cascade is actually wired up and doing its job on realistic input.
"""

import json

import pytest

from clipper_pro import cut as phase4
from clipper_pro.config import Settings
from clipper_pro.cut.snap_ops import (
    candidates_to_clips,
    clips_to_candidates,
    describe_movement,
    validate_snapped,
    words_to_dicts,
)
from clipper_pro.errors import ToolFailureError
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import Candidate, RubricScores, SourceMedia, Word


def _sentence(text, start, step=0.4):
    """Word objects for a sentence, one word every ``step`` seconds."""
    return [
        Word(tok, start + i * step, start + i * step + step * 0.7)
        for i, tok in enumerate(text.split())
    ]


def _transcript(*sentences):
    words: list[Word] = []
    for text, start in sentences:
        words.extend(_sentence(text, start))
    return TranscriptResult(words=words, language="en", text=" ".join(s[0] for s in sentences))


@pytest.fixture
def workspace(tmp_path):
    from clipper_pro.workspace import init

    root = tmp_path / "run"
    init(str(root))
    return str(root)


class TestWordsToDicts:
    def test_uses_the_word_key_the_snapper_reads(self):
        # cut_ops.sentence_boundaries looks for terminal punctuation under "word";
        # renaming it would silently disable the sentence stage.
        assert words_to_dicts([Word("hi.", 1.0, 1.4)]) == [
            {"word": "hi.", "start": 1.0, "end": 1.4}
        ]

    def test_empty_input(self):
        assert words_to_dicts([]) == []


class TestCandidateConversion:
    def test_round_trips_when_nothing_moves(self):
        original = [Candidate(10.0, 40.0, title="t", reason="r", scores=RubricScores(hook=8))]
        clips = candidates_to_clips(original)
        rebuilt = clips_to_candidates(clips, original)
        assert rebuilt[0].start == 10.0 and rebuilt[0].end == 40.0
        assert rebuilt[0].snapped_from is None  # "unchanged", not "phase 4 ran"
        assert rebuilt[0].scores.hook == 8
        assert rebuilt[0].title == "t" and rebuilt[0].reason == "r"

    def test_records_the_original_edges_when_they_move(self):
        original = [Candidate(10.0, 40.0)]
        rebuilt = clips_to_candidates([{"start": 9.5, "end": 41.2}], original)
        assert rebuilt[0].snapped_from == (10.0, 40.0)
        assert (rebuilt[0].start, rebuilt[0].end) == (9.5, 41.2)

    def test_output_is_in_time_order(self):
        original = [Candidate(100.0, 130.0), Candidate(10.0, 40.0)]
        clips = candidates_to_clips(original)
        assert [c.start for c in clips_to_candidates(clips, original)] == [10.0, 100.0]

    def test_degenerate_range_falls_back_to_the_original(self):
        original = [Candidate(10.0, 40.0)]
        rebuilt = clips_to_candidates([{"start": 40.0, "end": 10.0}], original)
        assert (rebuilt[0].start, rebuilt[0].end) == (10.0, 40.0)

    @pytest.mark.parametrize("bad", [None, "abc", float("nan"), float("inf")])
    def test_unusable_edge_falls_back_to_the_original(self, bad):
        original = [Candidate(10.0, 40.0)]
        rebuilt = clips_to_candidates([{"start": bad, "end": 40.0}], original)
        assert rebuilt[0].start == 10.0

    def test_length_mismatch_is_rejected(self):
        # The snapper mutates in place; adding or dropping entries would silently
        # pair the wrong scores with the wrong ranges.
        with pytest.raises(ValueError, match="must not add or drop"):
            clips_to_candidates([{"start": 1.0, "end": 2.0}], [Candidate(1, 2), Candidate(3, 4)])


class TestDescribeMovement:
    def test_reports_both_deltas_with_signs(self):
        candidate = Candidate(9.5, 41.0, snapped_from=(10.0, 40.0))
        text = describe_movement(candidate)
        assert "start 10.00→9.50s (-0.50)" in text
        assert "end 40.00→41.00s (+1.00)" in text

    def test_unmoved_candidate_says_so(self):
        assert describe_movement(Candidate(10.0, 40.0)) == "edges unchanged"


class TestValidateSnapped:
    def test_clean_set_has_no_problems(self):
        assert validate_snapped(
            [Candidate(0, 30), Candidate(40, 70)], source_duration=100.0
        ) == []

    def test_overlap_is_reported(self):
        problems = validate_snapped(
            [Candidate(0, 45), Candidate(40, 70)], source_duration=100.0
        )
        assert "overlaps the next clip by 5.00s" in problems[0]

    def test_running_past_the_source_is_reported(self):
        problems = validate_snapped([Candidate(80, 120)], source_duration=100.0)
        assert "past the" in problems[0]

    def test_unknown_source_duration_skips_that_check(self):
        assert validate_snapped([Candidate(80, 120)], source_duration=0.0) == []

    def test_tiny_float_overshoot_is_tolerated(self):
        # Snapping arithmetic lands a hair past the end; that is not a defect.
        assert validate_snapped([Candidate(80, 100.005)], source_duration=100.0) == []

    def test_out_of_order_input_is_checked_in_time_order(self):
        assert validate_snapped(
            [Candidate(40, 70), Candidate(0, 30)], source_duration=100.0
        ) == []


class TestRunCut:
    def test_snaps_a_mid_sentence_edge_onto_a_sentence_boundary(self, workspace):
        # "So I quit my job." runs 10.0-12.1; the raw pick starts mid-sentence at
        # 10.9 and should be walked back to the sentence onset.
        transcript = _transcript(
            ("So I quit my job.", 10.0),
            ("Everyone said I was insane and they were right about it.", 13.0),
            ("But here is the part nobody tells you about money.", 20.0),
        )
        candidates = [Candidate(10.9, 24.3, title="c", scores=RubricScores(hook=9))]
        snapped = phase4.run_cut(
            candidates, transcript,
            SourceMedia(path="/v.mp4", duration=40.0),
            workspace, settings=Settings(snap_silence=False),
        )
        assert snapped[0].snapped_from == (10.9, 24.3)
        # Moved earlier, onto a real boundary rather than mid-word.
        assert snapped[0].start < 10.9

    def test_writes_the_cuts_artifact_with_stage_paths(self, workspace):
        transcript = _transcript(("So I quit my job.", 10.0), ("Then this happened next.", 14.0))
        snapped = phase4.run_cut(
            [Candidate(10.9, 22.0)], transcript,
            SourceMedia(path="/v.mp4", duration=40.0),
            workspace, settings=Settings(snap_silence=False),
        )
        with open(f"{workspace}/analysis/cuts.json") as fh:
            payload = json.load(fh)
        assert payload["clips_moved"] == len([c for c in snapped if c.snapped_from])
        assert payload["silences_detected"] == 0
        assert len(payload["clips"]) == 1

    def test_scores_and_prose_survive_the_snap(self, workspace):
        transcript = _transcript(("So I quit my job.", 10.0), ("Then this happened next.", 14.0))
        snapped = phase4.run_cut(
            [Candidate(10.9, 22.0, title="The pivot", reason="why", scores=RubricScores(hook=9, emotion=7))],
            transcript, SourceMedia(path="/v.mp4", duration=40.0),
            workspace, settings=Settings(snap_silence=False),
        )
        assert snapped[0].title == "The pivot" and snapped[0].reason == "why"
        assert snapped[0].scores.hook == 9 and snapped[0].scores.emotion == 7

    def test_adjacent_clips_do_not_overlap_after_expansion(self, workspace):
        # Sentence expansion is neighbour-clamped; two back-to-back picks must
        # not grow into each other.
        transcript = _transcript(
            ("So I quit my job and it was the best thing.", 0.0),
            ("Everyone said I was insane about the whole plan.", 10.0),
            ("But here is what nobody ever tells you clearly.", 20.0),
            ("Money buys time and never actually buys freedom.", 30.0),
        )
        candidates = [Candidate(0.5, 14.0, title="a"), Candidate(15.0, 33.0, title="b")]
        snapped = phase4.run_cut(
            candidates, transcript, SourceMedia(path="/v.mp4", duration=45.0),
            workspace, settings=Settings(snap_silence=False),
        )
        assert validate_snapped(snapped, source_duration=45.0) == []

    def test_no_candidates_is_an_error(self, workspace):
        with pytest.raises(ToolFailureError, match="no candidates to snap"):
            phase4.run_cut(
                [], _transcript(("a b c.", 0.0)),
                SourceMedia(path="/v.mp4", duration=10.0), workspace,
                settings=Settings(snap_silence=False),
            )

    def test_empty_transcript_is_an_error(self, workspace):
        with pytest.raises(ToolFailureError, match="transcript is empty"):
            phase4.run_cut(
                [Candidate(0, 30)], TranscriptResult(),
                SourceMedia(path="/v.mp4", duration=40.0), workspace,
                settings=Settings(snap_silence=False),
            )

    def test_silence_stage_is_skipped_without_audio(self, workspace, capsys):
        transcript = _transcript(("So I quit my job.", 10.0), ("Then this happened next.", 14.0))
        phase4.run_cut(
            [Candidate(10.9, 22.0)], transcript,
            SourceMedia(path="/v.mp4", duration=40.0, audio_path="/does/not/exist.flac"),
            workspace, settings=Settings(snap_silence=True),
        )
        assert "stage 3 skipped" in capsys.readouterr().err

    def test_unrenderable_result_is_refused(self, workspace, monkeypatch):
        # If the cascade ever produced overlapping edges, that must surface here
        # rather than as a confusing ffmpeg error several phases later.
        monkeypatch.setattr(
            phase4, "validate_snapped", lambda *a, **k: ["clip[0]: overlaps the next clip"]
        )
        with pytest.raises(ToolFailureError, match="not renderable"):
            phase4.run_cut(
                [Candidate(0, 30)], _transcript(("a b c.", 0.0)),
                SourceMedia(path="/v.mp4", duration=40.0), workspace,
                settings=Settings(snap_silence=False),
            )


class TestDetectSilences:
    def test_missing_file_yields_no_silences(self):
        assert phase4.detect_silences("/does/not/exist.flac") == []

    def test_empty_path_yields_no_silences(self):
        assert phase4.detect_silences("") == []
