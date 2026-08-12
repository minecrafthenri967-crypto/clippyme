"""Tests for the karaoke word "pop" (scale bounce on the active word).

Pure builder tests (``build_pop_transform_tags``) plus structural assertions
on ``generate_ass_karaoke``'s output — that pop=True emits the expected
``\\t(...)`` sequence and pop=False (the default) emits none, so the new
option can never leak into the existing behaviour.

The ffmpeg/libass render itself (does the glyph actually get bigger on
screen) is verified separately in tests/domain/test_subtitle_pop_render.py,
gated behind ffmpeg + a usable font, since pixel measurement needs a real
render and this file stays host-only.
"""
import re

import pytest

from clippyme.domain.subtitles import (
    POP_DOWN_MS,
    POP_MIN_WORD_MS,
    POP_UP_MS,
    build_pop_transform_tags,
    generate_ass_karaoke,
)

_TRANSFORM_RE = re.compile(r"\\t\((\d+),(\d+),\\fscx(\d+)\\fscy\3\)")


def _transcript(words):
    """words: list of (text, start, end) in seconds."""
    return {"segments": [{"words": [
        {"word": w, "start": s, "end": e} for w, s, e in words
    ]}]}


# --- build_pop_transform_tags ------------------------------------------------

def test_resets_to_100_before_animating():
    tags = build_pop_transform_tags(0, 400)
    assert tags.startswith("\\fscx100\\fscy100")


def test_two_phase_animation_up_then_back_down():
    tags = build_pop_transform_tags(1000, 400)
    matches = _TRANSFORM_RE.findall(tags)
    assert len(matches) == 2
    (t1, t_mid, scale_up), (t_mid2, t2, scale_down) = matches
    assert t_mid == t_mid2  # the down-phase starts exactly where the up-phase ends
    assert int(scale_up) > 100  # pop UP
    assert int(scale_down) == 100  # settle back to normal


def test_timing_is_relative_to_the_passed_offset_not_zero():
    """t1_ms is the word's position within the whole DIALOGUE EVENT — a word
    starting 1000ms into the line must animate starting at 1000, not at 0."""
    tags = build_pop_transform_tags(1000, 400)
    t1 = int(_TRANSFORM_RE.findall(tags)[0][0])
    assert t1 == 1000


def test_up_and_down_phase_durations_match_the_constants_when_it_fits():
    tags = build_pop_transform_tags(0, 1000)  # plenty of room
    (t1, t_mid, _), (_, t2, _) = _TRANSFORM_RE.findall(tags)
    assert int(t_mid) - int(t1) == POP_UP_MS
    assert int(t2) - int(t_mid) == POP_DOWN_MS


def test_very_short_word_is_skipped_entirely():
    """Below POP_MIN_WORD_MS a two-phase animation would just flicker."""
    assert build_pop_transform_tags(0, POP_MIN_WORD_MS - 1) == ""


def test_word_too_short_for_the_full_cycle_is_compressed_not_overflowed():
    """A word shorter than up_ms+down_ms must not push the pop into the NEXT
    word's own \\k window — both phases compress to fit inside this word."""
    word_ms = 50  # well under POP_UP_MS + POP_DOWN_MS (150)
    tags = build_pop_transform_tags(200, word_ms)
    (t1, t_mid, _), (_, t2, _) = _TRANSFORM_RE.findall(tags)
    assert int(t1) == 200
    assert int(t2) - int(t1) <= word_ms
    assert int(t2) <= 200 + word_ms


@pytest.mark.parametrize("word_ms", [21, 50, 100, 149, 150, 151, 400, 5000])
def test_animation_never_extends_past_the_words_own_window(word_ms):
    tags = build_pop_transform_tags(500, word_ms)
    (t1, _, _), (_, t2, _) = _TRANSFORM_RE.findall(tags)
    assert int(t2) - int(t1) <= word_ms


def test_custom_scale_and_phase_durations_are_honoured():
    tags = build_pop_transform_tags(0, 1000, scale=150, up_ms=30, down_ms=40)
    (t1, t_mid, scale_up), (_, t2, scale_down) = _TRANSFORM_RE.findall(tags)
    assert int(scale_up) == 150
    assert int(scale_down) == 100
    assert int(t_mid) - int(t1) == 30
    assert int(t2) - int(t_mid) == 40


# --- generate_ass_karaoke wiring --------------------------------------------

def test_pop_false_emits_no_transform_tags(tmp_path):
    transcript = _transcript([("hello", 0.0, 0.4), ("world", 0.4, 0.9)])
    out = tmp_path / "sub.ass"
    ok = generate_ass_karaoke(transcript, 0.0, 1.0, str(out), pop=False)
    assert ok
    content = out.read_text()
    assert "\\t(" not in content


def test_pop_true_emits_transform_tags_for_each_word(tmp_path):
    transcript = _transcript([("hello", 0.0, 0.4), ("world", 0.4, 0.9)])
    out = tmp_path / "sub.ass"
    ok = generate_ass_karaoke(transcript, 0.0, 1.0, str(out), pop=True)
    assert ok
    content = out.read_text()
    assert content.count("\\t(") == 4  # 2 words * 2 transform phases each


def test_pop_defaults_to_off(tmp_path):
    """The default must not change the output of every existing recipe that
    never mentions `pop` at all."""
    transcript = _transcript([("hello", 0.0, 0.4)])
    out = tmp_path / "sub.ass"
    generate_ass_karaoke(transcript, 0.0, 1.0, str(out))
    assert "\\t(" not in out.read_text()


def test_second_words_offset_accounts_for_the_first_words_duration(tmp_path):
    """elapsed_ms must accumulate across words in the SAME line — the second
    word's pop should start at (first word's \\k duration), not at 0."""
    transcript = _transcript([("hi", 0.0, 0.5), ("there", 0.5, 1.0)])
    out = tmp_path / "sub.ass"
    generate_ass_karaoke(transcript, 0.0, 1.0, str(out), pop=True,
                         words_per_group=2)
    content = out.read_text()
    starts = [int(m.group(1)) for m in _TRANSFORM_RE.finditer(content)]
    # First word's two phases both start at/after 0; second word's first
    # phase must start at ~500ms (its own \k duration is 0.5s = 50cs).
    assert starts[0] == 0
    assert starts[2] == 500


# --- number emphasis --------------------------------------------------------

from clippyme.domain.subtitles import (  # noqa: E402
    DEFAULT_EMPHASIS_COLOR,
    build_emphasis_color_tags,
    word_has_digit,
)

PRIMARY = "&H00FF00FF&"
SECONDARY = "&H00FFFFFF&"
EMPHASIS = "&H00952DFF&"


@pytest.mark.parametrize("word,expected", [
    ("3", True), ("$50", True), ("2024", True), ("10x", True), ("100%", True),
    ("free", False), ("the", False), ("", False), (None, False),
])
def test_word_has_digit(word, expected):
    assert word_has_digit(word) is expected


def test_emphasis_word_gets_the_same_colour_for_both_karaoke_states():
    """The whole point: a number reads as important throughout the line, not
    only once the karaoke sweep reaches it — so primary and secondary must
    be identical for an emphasis word, unlike a normal word."""
    tags = build_emphasis_color_tags(True, PRIMARY, SECONDARY, EMPHASIS)
    assert f"\\1c{EMPHASIS}" in tags
    assert f"\\2c{EMPHASIS}" in tags


def test_normal_word_keeps_its_own_karaoke_colour_pair():
    tags = build_emphasis_color_tags(False, PRIMARY, SECONDARY, EMPHASIS)
    assert f"\\1c{PRIMARY}" in tags
    assert f"\\2c{SECONDARY}" in tags


def test_no_bold_tag_is_ever_emitted():
    """The style's own [V4+ Styles] line already sets Bold=-1 (every karaoke
    word is bold by default, verified by real render) — an explicit \\b0 on
    non-emphasis words would make them THINNER than that default instead of
    leaving normal words untouched, and ASS has no way to go bolder than
    Bold=-1 to add real contrast on emphasis words either. Colour is the
    entire effect; \\b must never appear here."""
    for is_emphasis in (True, False):
        tags = build_emphasis_color_tags(is_emphasis, PRIMARY, SECONDARY, EMPHASIS)
        assert "\\b" not in tags


# --- generate_ass_karaoke wiring (emphasize_numbers) ------------------------

def test_emphasize_numbers_false_emits_no_emphasis_tags(tmp_path):
    transcript = _transcript([("we", 0.0, 0.3), ("made", 0.3, 0.6), ("3", 0.6, 0.9)])
    out = tmp_path / "sub.ass"
    generate_ass_karaoke(transcript, 0.0, 1.0, str(out), emphasize_numbers=False)
    content = out.read_text()
    assert "\\1c" not in content and "\\b0" not in content and "\\b1" not in content


def test_emphasize_numbers_true_marks_only_the_numeric_word(tmp_path):
    transcript = _transcript([("we", 0.0, 0.3), ("made", 0.3, 0.6), ("3", 0.6, 0.9)])
    out = tmp_path / "sub.ass"
    generate_ass_karaoke(transcript, 0.0, 1.0, str(out), emphasize_numbers=True,
                         words_per_group=3, emphasis_color="#123456")
    content = out.read_text()
    assert content.count("&H00563412") == 2  # "3" gets it for BOTH \1c and \2c
    assert "\\b" not in content  # never touches weight (see build_emphasis_color_tags)


def test_emphasis_colour_override_reaches_the_output(tmp_path):
    transcript = _transcript([("5", 0.0, 0.5)])
    out = tmp_path / "sub.ass"
    generate_ass_karaoke(transcript, 0.0, 1.0, str(out), emphasize_numbers=True,
                         emphasis_color="#00FF00")
    content = out.read_text()
    assert "\\1c&H0000FF00" in content  # ASS is BGR: green -> 00FF00


def test_invalid_emphasis_color_raises(tmp_path):
    transcript = _transcript([("5", 0.0, 0.5)])
    out = tmp_path / "sub.ass"
    with pytest.raises(ValueError, match="emphasis_color"):
        generate_ass_karaoke(transcript, 0.0, 1.0, str(out), emphasis_color="not-a-color")


def test_default_emphasis_color_is_not_any_preset_highlight_color():
    """Checked against every SUBTITLE_PRESETS highlight_color at design time
    — pin it so a future preset addition can't silently collide and make the
    emphasis invisible (identical to the 'already spoken' karaoke colour)."""
    from clippyme.domain.subtitles import SUBTITLE_PRESETS

    used = {p["highlight_color"].upper() for p in SUBTITLE_PRESETS.values()}
    assert DEFAULT_EMPHASIS_COLOR.upper() not in used
