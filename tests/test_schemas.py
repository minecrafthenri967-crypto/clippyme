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
