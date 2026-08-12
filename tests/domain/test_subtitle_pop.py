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
