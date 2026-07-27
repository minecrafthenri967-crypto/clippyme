"""Cross-phase data contracts: validation and JSON round-tripping."""

import pytest

from clipper_pro.errors import ValidationError
from clipper_pro.types import AudioEvent, Candidate, RubricScores, SourceMedia, Word


class TestWord:
    def test_round_trips_through_json_shape(self):
        word = Word("hello", 1.0, 1.4, speaker=2, confidence=0.98)
        assert Word.from_dict(word.to_dict()) == word

    def test_duration_is_derived(self):
        assert Word("x", 1.0, 1.5).duration == pytest.approx(0.5)

    def test_accepts_the_provider_word_key(self):
        # Deepgram emits "word"; our own artifacts use "text".
        assert Word.from_dict({"word": "hi", "start": 0, "end": 1}).text == "hi"

    def test_rejects_reversed_timing(self):
        with pytest.raises(ValidationError, match="precedes start"):
            Word("x", 5.0, 4.0)

    def test_zero_length_word_is_allowed(self):
        # Providers do emit these for elided words; phase 4 handles them.
        assert Word("x", 2.0, 2.0).duration == 0.0


class TestAudioEvent:
    def test_round_trips(self):
        event = AudioEvent("laughter", 12.0, 13.5, confidence=0.8)
        assert AudioEvent.from_dict(event.to_dict()) == event

    def test_requires_a_kind(self):
        with pytest.raises(ValidationError, match="non-empty kind"):
            AudioEvent("", 1.0, 2.0)

    @pytest.mark.parametrize("start,end", [(-1.0, 2.0), (5.0, 5.0), (5.0, 4.0)])
    def test_rejects_impossible_spans(self, start, end):
        with pytest.raises(ValidationError):
            AudioEvent("laughter", start, end)


class TestRubricScores:
    def test_weights_sum_to_one(self):
        assert sum(RubricScores.WEIGHTS.values()) == pytest.approx(1.0)

    def test_total_is_the_weighted_mean(self):
        assert RubricScores(10, 10, 10, 10, 10).total == pytest.approx(10.0)
        assert RubricScores().total == pytest.approx(0.0)

    def test_hook_outweighs_density(self):
        # A short-form viewer decides in ~2s, so the rubric must not let a
        # dense-but-unhooked clip outrank a strongly hooked one.
        hooked = RubricScores(hook=10)
        dense = RubricScores(density=10)
        assert hooked.total > dense.total

    @pytest.mark.parametrize("axis", list(RubricScores.WEIGHTS))
    def test_rejects_scores_outside_the_scale(self, axis):
        with pytest.raises(ValidationError, match="0–10"):
            RubricScores(**{axis: 11.0})
        with pytest.raises(ValidationError):
            RubricScores(**{axis: -1.0})

    def test_to_dict_exposes_the_total_and_from_dict_ignores_it(self):
        scores = RubricScores(8, 6, 7, 9, 5)
        payload = scores.to_dict()
        assert payload["total"] == scores.total
        assert RubricScores.from_dict(payload) == scores

    def test_from_dict_defaults_missing_axes(self):
        assert RubricScores.from_dict({"hook": 5}) == RubricScores(hook=5)


class TestCandidate:
    def test_round_trips_with_scores_and_snap_history(self):
        candidate = Candidate(
            start=10.0, end=48.0, title="The pivot",
            reason="clean setup and payoff",
            scores=RubricScores(9, 7, 8, 8, 6),
            snapped_from=(10.5, 47.2),
        )
        assert Candidate.from_dict(candidate.to_dict()) == candidate

    def test_duration_is_derived_and_serialised(self):
        candidate = Candidate(start=10.0, end=48.0)
        assert candidate.duration == pytest.approx(38.0)
        assert candidate.to_dict()["duration"] == pytest.approx(38.0)

    def test_snap_history_is_optional(self):
        candidate = Candidate(start=1.0, end=2.0)
        assert candidate.to_dict()["snapped_from"] is None
        assert Candidate.from_dict(candidate.to_dict()).snapped_from is None

    @pytest.mark.parametrize("start,end", [(-1.0, 10.0), (10.0, 10.0), (10.0, 9.0)])
    def test_rejects_impossible_ranges(self, start, end):
        with pytest.raises(ValidationError):
            Candidate(start=start, end=end)


class TestSourceMedia:
    def test_round_trips(self):
        media = SourceMedia("/v/a.mp4", 3600.0, title="Talk", url="https://x/y", audio_path="/a.flac")
        assert SourceMedia.from_dict(media.to_dict()) == media

    def test_audio_path_defaults_empty_until_phase_one_fills_it(self):
        assert SourceMedia("/v/a.mp4", 10.0).audio_path == ""

    def test_from_dict_tolerates_a_minimal_record(self):
        media = SourceMedia.from_dict({"path": "/v/a.mp4"})
        assert media.duration == 0.0 and media.title == ""
