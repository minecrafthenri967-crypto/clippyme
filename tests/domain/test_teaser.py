"""Tests for the cold-open teaser layer (domain.teaser).

Pure halves only — the ffmpeg invocation itself is exercised by the Docker
integration suite, same split as logo.py / player_image.py.

The recurring theme: resolve_teaser_window returns None a LOT, and every one
of those cases is correct behaviour rather than a failure. A teaser built from
a guessed window opens the video on the wrong footage, which is worse for the
thing it exists to improve (the first two seconds) than having no teaser.
"""
import pytest

from clippyme.domain.teaser import (
    DEFAULT_TEASER_MAX_DURATION,
    MIN_TEASER_DURATION,
    build_punch_zoom_expr,
    build_teaser_filter,
    resolve_teaser_window,
)


def _clip(**overrides):
    """A clip spanning source seconds 100-160 with its peak at 130-132."""
    base = {"start": 100.0, "end": 160.0, "peak_start": 130.0, "peak_end": 132.0}
    base.update(overrides)
    return base


# --- resolve_teaser_window --------------------------------------------------

def test_window_is_rebased_from_absolute_source_time_to_clip_relative():
    """peak_start/peak_end are ABSOLUTE source seconds like start/end; the
    renderer needs them relative to the clip's own timeline."""
    assert resolve_teaser_window(_clip(), video_duration=60.0) == (30.0, 32.0)


def test_no_peak_means_no_teaser():
    assert resolve_teaser_window(
        _clip(peak_start=None, peak_end=None), video_duration=60.0) is None


def test_half_specified_peak_means_no_teaser():
    assert resolve_teaser_window(_clip(peak_end=None), video_duration=60.0) is None


def test_non_numeric_clip_start_is_survivable():
    assert resolve_teaser_window(
        _clip(start="nonsense"), video_duration=60.0) is None


def test_peak_at_the_very_top_of_the_clip_is_rejected():
    """A teaser of the clip's own opening replays what the viewer is about to
    see anyway — pointless, and it burns the seconds it was meant to buy."""
    assert resolve_teaser_window(
        _clip(peak_start=100.3, peak_end=102.3), video_duration=60.0) is None


def test_peak_just_past_the_lead_threshold_is_kept():
    window = resolve_teaser_window(
        _clip(peak_start=101.5, peak_end=103.5), video_duration=60.0)
    assert window == (1.5, 3.5)


def test_too_short_a_window_is_rejected():
    assert resolve_teaser_window(
        _clip(peak_end=130.0 + MIN_TEASER_DURATION / 2), video_duration=60.0) is None


def test_overlong_window_is_trimmed_to_max_duration_from_its_start():
    """Gemini is asked for <=3s. A longer one keeps its (word-anchored) start
    and loses the tail rather than being dropped outright."""
    window = resolve_teaser_window(
        _clip(peak_start=130.0, peak_end=145.0), video_duration=60.0)
    assert window == (30.0, 30.0 + DEFAULT_TEASER_MAX_DURATION)


def test_max_duration_is_caller_overridable():
    window = resolve_teaser_window(
        _clip(peak_start=130.0, peak_end=145.0), video_duration=60.0, max_duration=1.5)
    assert window == (30.0, 31.5)


def test_window_is_clamped_to_the_actual_rendered_duration():
    """The clip on disk can be shorter than end-start (Smart Cut, encode
    rounding); the teaser must never seek past the real end."""
    window = resolve_teaser_window(_clip(), video_duration=31.0)
    assert window == (30.0, 31.0)


def test_clamping_to_a_short_file_can_leave_too_little_to_be_a_teaser():
    """Same clamp as above, but this time it eats below MIN_TEASER_DURATION —
    a 0.5s flash reads as a stutter, not a moment."""
    assert resolve_teaser_window(_clip(), video_duration=30.5) is None


# --- Smart Cut interaction --------------------------------------------------

def test_window_is_remapped_through_smart_cuts_kept_segments():
    """Smart Cut shortened the clip, so a pre-cut timestamp means nothing until
    it is remapped onto the rendered timeline."""
    # A 10s span was removed at 5-15, so everything after 15 shifts 10s earlier.
    kept = [(0.0, 5.0), (15.0, 60.0)]
    window = resolve_teaser_window(_clip(), video_duration=50.0, kept_segments=kept)
    assert window == (20.0, 22.0)


def test_peak_cut_away_by_smart_cut_means_no_teaser():
    kept = [(0.0, 5.0), (40.0, 60.0)]  # 30..32 was removed entirely
    assert resolve_teaser_window(
        _clip(), video_duration=25.0, kept_segments=kept) is None


def test_partially_cut_peak_means_no_teaser():
    """Only the peak's tail survived. Half a moment is not the moment — skip
    rather than show a truncated fragment."""
    kept = [(0.0, 31.0), (40.0, 60.0)]  # peak 30..32 straddles the cut
    assert resolve_teaser_window(
        _clip(), video_duration=41.0, kept_segments=kept) is None


def test_empty_kept_segments_means_no_teaser_not_a_crash():
    assert resolve_teaser_window(_clip(), video_duration=60.0, kept_segments=[]) is None


# --- build_teaser_filter ----------------------------------------------------

def test_filter_trims_the_peak_and_concats_the_full_body():
    fc = build_teaser_filter(30.0, 32.0, fade=0.0)
    assert "trim=start=30.000:end=32.000" in fc
    assert "atrim=start=30.000:end=32.000" in fc
    # The body branch is the WHOLE input — the clip still plays in full.
    assert "[0:v]setpts=PTS-STARTPTS[bv]" in fc
    assert "concat=n=2:v=1:a=1[outv][outa]" in fc


def test_filter_fades_both_video_and_audio_on_the_teaser_tail():
    """With sound on, a hard mid-word audio cut at the jump back is the most
    jarring part of the transition — the audio fade is not optional dressing."""
    fc = build_teaser_filter(30.0, 32.0, fade=0.2, transition="fade")
    assert "fade=t=out:st=1.800:d=0.200" in fc
    assert "afade=t=out:st=1.800:d=0.200" in fc


def test_fade_is_capped_at_half_the_teaser_so_it_is_not_all_fade():
    fc = build_teaser_filter(30.0, 31.0, fade=5.0, transition="fade")
    assert "fade=t=out:st=0.500:d=0.500" in fc


def test_zero_fade_emits_no_fade_filter():
    fc = build_teaser_filter(30.0, 32.0, fade=0.0, transition="fade")
    assert "fade=" not in fc


def test_filter_without_audio_never_references_an_audio_stream():
    """A video-only input would make the audio branch fail the whole pass."""
    fc = build_teaser_filter(30.0, 32.0, has_audio=False)
    assert "[0:a]" not in fc
    assert "concat=n=2:v=1:a=0[outv]" in fc


@pytest.mark.parametrize("fade", [0.0, 0.12, 0.5])
def test_filter_labels_stay_balanced(fade):
    """Every declared label is consumed by the concat — an orphan label makes
    ffmpeg fail the whole graph."""
    fc = build_teaser_filter(30.0, 33.0, fade=fade)
    for label in ("[tv]", "[ta]", "[bv]", "[ba]"):
        assert fc.count(label) == 2, f"{label} must be produced once and consumed once"


# --- zoom punch transition --------------------------------------------------
#
# The punch is what makes the jump back read as "that was a flash-forward"
# rather than as a glitch. Its ffmpeg graph has two sharp edges that these
# tests pin: the crop needs literal frame dimensions (inside crop, `iw` is
# already the SCALED width), and a float undershoot of one pixel aborts the
# whole render with "Invalid too big or non positive size".

def test_punch_scales_up_and_crops_back_to_the_original_frame():
    fc = build_teaser_filter(30.0, 32.0, width=1080, height=1920)
    assert "scale=w='ceil(iw*(" in fc
    assert "eval=frame" in fc
    assert "crop=1080:1920" in fc


def test_punch_guards_the_crop_with_ceil():
    """At zoom 1.0 the scaled frame is EXACTLY the crop size; without ceil a
    float undershoot makes ffmpeg abort the render, not just skip the effect."""
    fc = build_teaser_filter(30.0, 32.0, width=1080, height=1920)
    assert "ceil(iw*" in fc and "ceil(ih*" in fc


def test_punch_falls_back_to_the_fade_without_frame_dimensions():
    """crop cannot express "the size I had before the scale", so no dimensions
    means no punch — degrade rather than emit a graph that fails at render."""
    fc = build_teaser_filter(30.0, 32.0, transition="punch", width=None, height=None)
    assert "crop=" not in fc
    assert "fade=t=out" in fc


def test_unknown_transition_falls_back_to_the_default():
    fc = build_teaser_filter(30.0, 32.0, transition="bogus", width=1080, height=1920)
    assert "crop=1080:1920" in fc


def test_fade_transition_has_no_zoom():
    fc = build_teaser_filter(30.0, 32.0, transition="fade", width=1080, height=1920)
    assert "crop=" not in fc and "fade=t=out" in fc


def test_none_transition_has_neither_zoom_nor_any_fade():
    fc = build_teaser_filter(30.0, 32.0, transition="none", width=1080, height=1920)
    assert "crop=" not in fc
    assert "fade=" not in fc  # covers afade too


def test_audio_fade_rides_the_punch_as_well():
    """The punch is a VISUAL cue; it does nothing about sound cutting off
    mid-word, which is the part that actually sounds broken."""
    fc = build_teaser_filter(30.0, 32.0, transition="punch", width=1080, height=1920)
    assert "afade=t=out" in fc


# --- build_punch_zoom_expr --------------------------------------------------

def _zoom_at(expr, t):
    """Evaluate the ffmpeg zoom expression in Python for a given t."""
    import re
    py = expr.replace("\\,", ",").replace("pow(", "__pow(")
    return eval(py, {"__pow": pow, "max": max, "min": min, "t": t})  # noqa: S307


def test_zoom_expression_is_flat_before_the_punch_window():
    expr = build_punch_zoom_expr(2.0, 1.12, 0.25)
    assert _zoom_at(expr, 0.0) == 1.0
    assert _zoom_at(expr, 1.7) == 1.0


def test_zoom_expression_reaches_the_full_zoom_at_the_cut():
    expr = build_punch_zoom_expr(2.0, 1.12, 0.25)
    assert _zoom_at(expr, 2.0) == pytest.approx(1.12)


def test_zoom_expression_never_dips_below_one():
    """Below 1.0 the scaled frame is smaller than the crop — a hard render
    abort, so this is a correctness bound, not an aesthetic one."""
    expr = build_punch_zoom_expr(2.0, 1.12, 0.25)
    for t in (-1.0, 0.0, 0.5, 1.75, 1.9, 2.0, 5.0):
        assert _zoom_at(expr, t) >= 1.0


def test_zoom_expression_accelerates_rather_than_creeping():
    """Squared ramp: at the halfway point of the window the zoom must still be
    well under half of the total travel, or it reads as a slow push."""
    expr = build_punch_zoom_expr(2.0, 1.20, 0.40)
    midpoint = _zoom_at(expr, 1.8)   # halfway through the 0.4s window
    assert 1.0 < midpoint < 1.10


def test_punch_duration_longer_than_the_teaser_is_clamped():
    expr = build_punch_zoom_expr(1.0, 1.12, 5.0)
    assert _zoom_at(expr, 0.0) == 1.0
    assert _zoom_at(expr, 1.0) == pytest.approx(1.12)


def test_default_punch_is_a_real_lunge_not_a_subtle_nudge():
    """1.12/0.25 read as too subtle in practice — pins the stronger default so
    a future edit can't quietly soften it back down."""
    from clippyme.domain.teaser import DEFAULT_PUNCH_DURATION, DEFAULT_PUNCH_ZOOM

    assert DEFAULT_PUNCH_ZOOM >= 1.3
    expr = build_punch_zoom_expr(2.0, DEFAULT_PUNCH_ZOOM, DEFAULT_PUNCH_DURATION)
    assert _zoom_at(expr, 2.0) == pytest.approx(DEFAULT_PUNCH_ZOOM)
