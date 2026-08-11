"""Tests for clippyme.schemas (top-level, dependency-free of api/pipeline).

Covers the shared timestamp-coercion helper (extracted from ViralClip so
PlayerMention can reuse it) and the new PlayerMention/PlayerMentionsResponse
contract for the player-image-overlay Gemini call.
"""
import pytest
from pydantic import ValidationError

from clippyme.schemas import (
    PlayerMention,
    PlayerMentionsResponse,
    ViralClip,
    _coerce_timestamp_value,
)


# --- _coerce_timestamp_value (shared helper) --------------------------------

def test_coerce_timestamp_passes_through_numeric():
    assert _coerce_timestamp_value(12.5) == 12.5


def test_coerce_timestamp_passes_through_plain_float_string():
    assert _coerce_timestamp_value("12.5") == "12.5"  # single dot: untouched


def test_coerce_timestamp_mm_ss_dotted():
    assert _coerce_timestamp_value("25.17.724") == pytest.approx(25 * 60 + 17.724)


def test_coerce_timestamp_hh_mm_ss_dotted():
    assert _coerce_timestamp_value("1.25.17.724") == pytest.approx(3600 + 25 * 60 + 17.724)


def test_coerce_timestamp_mm_ss_colon():
    assert _coerce_timestamp_value("25:17.724") == pytest.approx(25 * 60 + 17.724)


def test_coerce_timestamp_hh_mm_ss_colon():
    assert _coerce_timestamp_value("1:25:17") == pytest.approx(3600 + 25 * 60 + 17)


def test_coerce_timestamp_garbage_string_passes_through_unchanged():
    assert _coerce_timestamp_value("not-a-timestamp") == "not-a-timestamp"


def test_coerce_timestamp_blank_string_passes_through():
    assert _coerce_timestamp_value("   ") == "   "


# --- ViralClip regression (extraction must not change behavior) ------------

def test_viral_clip_still_coerces_dotted_timestamps():
    clip = ViralClip(
        start="1.25.17.724", end="1.25.40.0", viral_score=80,
        viral_reason="a" * 25,
    )
    assert clip.start == pytest.approx(3600 + 25 * 60 + 17.724)
    assert clip.end == pytest.approx(3600 + 25 * 60 + 40.0)


def test_viral_clip_rejects_end_before_start():
    with pytest.raises(ValidationError):
        ViralClip(start=10, end=5, viral_score=50, viral_reason="a" * 25)


# --- PlayerMention / PlayerMentionsResponse ---------------------------------

def test_player_mention_roundtrip():
    m = PlayerMention(player_name="LeBron James", timestamp=12.5, confidence=0.9)
    assert m.player_name == "LeBron James"
    assert m.timestamp == 12.5
    assert m.confidence == 0.9


def test_player_mention_coerces_dotted_timestamp():
    m = PlayerMention(player_name="Steph Curry", timestamp="1.02.5", confidence=0.5)
    assert m.timestamp == pytest.approx(62.5)


def test_player_mention_normalizes_whitespace_in_name():
    m = PlayerMention(player_name="  LeBron   James  ", timestamp=1, confidence=0.5)
    assert m.player_name == "LeBron James"


def test_player_mention_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        PlayerMention(player_name="X", timestamp=1, confidence=1.5)
    with pytest.raises(ValidationError):
        PlayerMention(player_name="X", timestamp=1, confidence=-0.1)


def test_player_mention_rejects_negative_timestamp():
    with pytest.raises(ValidationError):
        PlayerMention(player_name="X", timestamp=-1, confidence=0.5)


def test_player_mention_rejects_empty_name():
    with pytest.raises(ValidationError):
        PlayerMention(player_name="", timestamp=1, confidence=0.5)


def test_player_mentions_response_defaults_to_empty_list():
    assert PlayerMentionsResponse().mentions == []


def test_player_mentions_response_holds_multiple_mentions():
    resp = PlayerMentionsResponse(mentions=[
        {"player_name": "A", "timestamp": 1, "confidence": 0.5},
        {"player_name": "B", "timestamp": 2, "confidence": 0.9},
    ])
    assert len(resp.mentions) == 2
    assert resp.mentions[1].player_name == "B"


def test_player_mentions_response_caps_at_twenty():
    with pytest.raises(ValidationError):
        PlayerMentionsResponse(mentions=[
            {"player_name": "A", "timestamp": i, "confidence": 0.5} for i in range(21)
        ])


# --- ViralClip peak window (cold-open teaser source) ------------------------
#
# The peak marks the strongest 1-3s inside a clip, used to build a teaser that
# plays before the clip's real start. It is OPTIONAL by design: these tests
# pin that a missing, malformed or nonsensical peak costs us the peak only —
# never the clip, which is still perfectly publishable without a teaser.

def _clip(**overrides):
    base = {
        "start": 10.0,
        "end": 40.0,
        "viral_score": 80,
        "viral_reason": "A specific, non-generic reason long enough to pass validation.",
    }
    base.update(overrides)
    return base


def test_peak_window_accepted_when_inside_the_clip():
    clip = ViralClip.model_validate(_clip(peak_start=30.0, peak_end=32.0))
    assert clip.peak_start == 30.0
    assert clip.peak_end == 32.0


def test_peak_defaults_to_none_when_gemini_omits_it():
    clip = ViralClip.model_validate(_clip())
    assert clip.peak_start is None and clip.peak_end is None


def test_peak_coerces_dotted_time_strings_like_start_and_end():
    clip = ViralClip.model_validate(_clip(peak_start="0.30.500", peak_end="0.32.500"))
    assert clip.peak_start == pytest.approx(30.5)
    assert clip.peak_end == pytest.approx(32.5)


@pytest.mark.parametrize("peak_start,peak_end,why", [
    (32.0, 30.0, "reversed"),
    (30.0, 30.0, "zero length"),
    (5.0, 12.0, "starts before the clip"),
    (35.0, 45.0, "ends after the clip"),
    (30.0, 30.4, "shorter than MIN_PEAK_DURATION"),
    (12.0, 39.0, "longer than MAX_PEAK_DURATION — a second clip, not a moment"),
])
def test_nonsensical_peak_is_cleared_but_the_clip_survives(peak_start, peak_end, why):
    clip = ViralClip.model_validate(_clip(peak_start=peak_start, peak_end=peak_end))
    assert clip.peak_start is None and clip.peak_end is None, why
    # The clip itself is untouched — this is the whole point.
    assert clip.start == 10.0 and clip.end == 40.0


@pytest.mark.parametrize("junk", ["", "not a time", {}, [], "  "])
def test_unparseable_peak_never_rejects_the_clip(junk):
    """A malformed peak must NOT raise. start/end are load-bearing so garbage
    there rightly kills the clip; the peak is a bonus and must never cost us
    an otherwise-perfect clip."""
    clip = ViralClip.model_validate(_clip(peak_start=junk, peak_end=junk))
    assert clip.peak_start is None and clip.peak_end is None
    assert clip.viral_score == 80


def test_half_specified_peak_is_cleared_on_both_sides():
    clip = ViralClip.model_validate(_clip(peak_start=30.0))
    assert clip.peak_start is None and clip.peak_end is None


def test_peak_survives_model_dump_into_the_clip_dict():
    """validate_and_dedupe returns model_dump()s — the peak has to ride along
    into the metadata for the teaser renderer to ever see it."""
    dumped = ViralClip.model_validate(_clip(peak_start=30.0, peak_end=32.0)).model_dump()
    assert dumped["peak_start"] == 30.0 and dumped["peak_end"] == 32.0
