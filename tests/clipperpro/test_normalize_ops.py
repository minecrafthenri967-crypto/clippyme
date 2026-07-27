"""Phase 2 pure logic: transcript dict → Word / AudioEvent."""

import pytest

from clipper_pro.transcribe.normalize_ops import (
    EVENT_MIN_DURATION,
    normalize_event_kind,
    parse_audio_events,
    parse_words,
    weave_text,
)
from clipper_pro.types import AudioEvent, Word


def _transcript(words, events=None, **extra):
    payload = {"segments": [{"text": "s", "start": 0, "end": 10, "words": words}]}
    if events is not None:
        payload["audio_events"] = events
    payload.update(extra)
    return payload


class TestParseWords:
    def test_flattens_segments_into_words(self):
        payload = {
            "segments": [
                {"words": [{"word": "hello", "start": 0.0, "end": 0.4}]},
                {"words": [{"word": "world", "start": 0.5, "end": 0.9}]},
            ]
        }
        words, dropped = parse_words(payload)
        assert [w.text for w in words] == ["hello", "world"]
        assert dropped == 0

    def test_maps_probability_to_confidence(self):
        words, _ = parse_words(_transcript([{"word": "a", "start": 0, "end": 1, "probability": 0.72}]))
        assert words[0].confidence == pytest.approx(0.72)

    def test_accepts_confidence_key_as_well(self):
        words, _ = parse_words(_transcript([{"word": "a", "start": 0, "end": 1, "confidence": 0.5}]))
        assert words[0].confidence == pytest.approx(0.5)

    def test_missing_confidence_defaults_to_one(self):
        words, _ = parse_words(_transcript([{"word": "a", "start": 0, "end": 1}]))
        assert words[0].confidence == 1.0

    def test_word_level_speaker_is_preserved(self):
        words, _ = parse_words(_transcript([{"word": "a", "start": 0, "end": 1, "speaker": 2}]))
        assert words[0].speaker == 2

    def test_segment_speaker_fills_in_when_words_lack_one(self):
        payload = {
            "segments": [{"speaker": 1, "words": [{"word": "a", "start": 0, "end": 1}]}]
        }
        words, _ = parse_words(payload)
        assert words[0].speaker == 1

    def test_word_speaker_wins_over_the_segment_label(self):
        payload = {
            "segments": [
                {"speaker": 1, "words": [{"word": "a", "start": 0, "end": 1, "speaker": 3}]}
            ]
        }
        words, _ = parse_words(payload)
        assert words[0].speaker == 3

    def test_output_is_sorted_by_start(self):
        # Utterance-based segmentation can interleave when speakers overlap, and
        # phase 4's boundary search assumes monotonic input.
        payload = {
            "segments": [
                {"words": [{"word": "late", "start": 5.0, "end": 5.5}]},
                {"words": [{"word": "early", "start": 1.0, "end": 1.5}]},
            ]
        }
        words, _ = parse_words(payload)
        assert [w.text for w in words] == ["early", "late"]

    def test_reversed_timestamps_are_repaired_not_dropped(self):
        # One bad word must not cost a paid hour of transcription.
        words, dropped = parse_words(_transcript([{"word": "a", "start": 5.0, "end": 4.0}]))
        assert dropped == 0
        assert (words[0].start, words[0].end) == (5.0, 5.0)

    @pytest.mark.parametrize(
        "bad",
        [
            {"word": "", "start": 0, "end": 1},
            {"word": "a", "start": None, "end": 1},
            {"word": "a", "start": 0, "end": "abc"},
            {"word": "a", "start": -1.0, "end": 1},
            {"word": "a", "end": 1},
            "not a dict",
        ],
    )
    def test_unusable_entries_are_dropped_and_counted(self, bad):
        words, dropped = parse_words(_transcript([{"word": "ok", "start": 0, "end": 1}, bad]))
        assert [w.text for w in words] == ["ok"]
        assert dropped == 1

    def test_nan_and_inf_are_treated_as_unparseable(self):
        # These would poison every downstream comparison rather than erroring.
        words, dropped = parse_words(
            _transcript([
                {"word": "a", "start": float("nan"), "end": 1},
                {"word": "b", "start": 0, "end": float("inf")},
            ])
        )
        assert words == [] and dropped == 2

    @pytest.mark.parametrize("payload", [None, {}, {"segments": []}, "junk", {"segments": [None]}])
    def test_degenerate_payloads_yield_no_words(self, payload):
        words, _ = parse_words(payload)
        assert words == []

    def test_zero_width_word_is_kept(self):
        words, dropped = parse_words(_transcript([{"word": "a", "start": 2.0, "end": 2.0}]))
        assert len(words) == 1 and dropped == 0


class TestNormalizeEventKind:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("(laughter)", "laughter"),
            ("[APPLAUSE]", "applause"),
            ("  Music  ", "music"),
            ("(crowd  cheering)", "crowd cheering"),
            ("<laugh>", "laugh"),
            ("{applause}", "applause"),
        ],
    )
    def test_strips_wrappers_and_normalises_case(self, raw, expected):
        assert normalize_event_kind(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "()", None])
    def test_empty_input_yields_empty_kind(self, raw):
        assert normalize_event_kind(raw) == ""


class TestParseAudioEvents:
    def test_extracts_and_normalises_events(self):
        payload = _transcript(
            [{"word": "a", "start": 0, "end": 1}],
            events=[{"text": "(laughter)", "start": 12.0, "end": 13.5}],
        )
        events, dropped = parse_audio_events(payload)
        assert events == [AudioEvent("laughter", 12.0, 13.5)]
        assert dropped == 0

    def test_absent_key_is_not_an_error(self):
        # Deepgram has no audio_events key at all; phase 3 degrades to text-only.
        events, dropped = parse_audio_events(_transcript([{"word": "a", "start": 0, "end": 1}]))
        assert events == [] and dropped == 0

    def test_instantaneous_event_is_padded_not_discarded(self):
        events, dropped = parse_audio_events(
            _transcript([], events=[{"text": "(laugh)", "start": 4.0, "end": 4.0}])
        )
        assert dropped == 0
        assert events[0].end == pytest.approx(4.0 + EVENT_MIN_DURATION)

    def test_reversed_event_span_is_repaired(self):
        events, _ = parse_audio_events(
            _transcript([], events=[{"text": "(laugh)", "start": 4.0, "end": 3.0}])
        )
        assert events[0].start == 4.0 and events[0].end > 4.0

    def test_events_are_sorted_by_start(self):
        events, _ = parse_audio_events(
            _transcript([], events=[
                {"text": "(applause)", "start": 30.0, "end": 31.0},
                {"text": "(laughter)", "start": 10.0, "end": 11.0},
            ])
        )
        assert [e.kind for e in events] == ["laughter", "applause"]

    def test_accepts_a_kind_key_as_well_as_text(self):
        events, _ = parse_audio_events(
            _transcript([], events=[{"kind": "applause", "start": 1.0, "end": 2.0}])
        )
        assert events[0].kind == "applause"

    def test_confidence_is_carried_when_present(self):
        events, _ = parse_audio_events(
            _transcript([], events=[{"text": "(laugh)", "start": 1.0, "end": 2.0, "confidence": 0.6}])
        )
        assert events[0].confidence == pytest.approx(0.6)

    @pytest.mark.parametrize(
        "bad",
        [
            {"text": "", "start": 1.0, "end": 2.0},
            {"text": "(laugh)", "start": None, "end": 2.0},
            {"text": "(laugh)", "start": -1.0, "end": 2.0},
            "not a dict",
        ],
    )
    def test_unusable_events_are_dropped_and_counted(self, bad):
        events, dropped = parse_audio_events(_transcript([], events=[bad]))
        assert events == [] and dropped == 1


class TestWeaveText:
    def test_interleaves_events_at_their_timestamps(self):
        words = [Word("I", 0.0, 0.2), Word("quit", 0.3, 0.7), Word("anyway", 2.0, 2.5)]
        events = [AudioEvent("laughter", 1.0, 1.8)]
        assert weave_text(words, events) == "I quit (laughter) anyway"

    def test_words_only_when_there_are_no_events(self):
        assert weave_text([Word("a", 0, 1), Word("b", 1, 2)], []) == "a b"

    def test_empty_inputs_yield_an_empty_string(self):
        assert weave_text([], []) == ""

    def test_event_precedes_a_word_sharing_its_timestamp(self):
        # Deterministic ordering so a cached transcript and a fresh one match.
        words = [Word("after", 5.0, 5.4)]
        events = [AudioEvent("applause", 5.0, 5.2)]
        assert weave_text(words, events) == "(applause) after"

    def test_events_before_all_speech_lead_the_text(self):
        assert weave_text([Word("hi", 9.0, 9.3)], [AudioEvent("music", 0.0, 1.0)]) == (
            "(music) hi"
        )
