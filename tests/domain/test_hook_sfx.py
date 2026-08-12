"""Tests for the hook impact sound (domain.hook_sfx).

Pure builder only — the ffmpeg invocation itself is exercised by the Docker
integration suite, same split as teaser.py / player_image.py. Since this is
audio (nothing to eyeball), correctness here means: the graph references the
right inputs, the timing/labels are self-consistent, and no filter can
silently break the whole -filter_complex on a malformed value.
"""
import pytest

from clippyme.domain.hook_sfx import build_hook_sfx_filter


def test_filter_wires_the_two_synthesized_sources_and_the_original_audio():
    fc = build_hook_sfx_filter()
    assert "[1:a]" in fc  # the thump source
    assert "[2:a]" in fc  # the click source
    assert "[0:a]" in fc  # the clip's own audio


def test_filter_output_label_is_outa():
    fc = build_hook_sfx_filter()
    assert fc.endswith("[outa]")


def test_offset_zero_produces_no_delay():
    fc = build_hook_sfx_filter(offset=0.0)
    assert "adelay=0|0" in fc


def test_offset_is_converted_to_milliseconds_on_both_channels():
    fc = build_hook_sfx_filter(offset=1.25)
    assert "adelay=1250|1250" in fc


def test_duck_window_starts_at_the_offset_and_spans_duck_duration():
    fc = build_hook_sfx_filter(offset=0.5, duck_duration=0.3)
    assert "between(t,0.500,0.800)" in fc


def test_gain_is_applied_to_the_mixed_hit_not_the_original_audio():
    fc = build_hook_sfx_filter(gain_db=-9.5)
    assert "volume=-9.50dB" in fc


def test_amix_never_renormalizes_so_the_hit_does_not_get_quietly_attenuated():
    """amix defaults to normalize=true, which would divide both inputs down
    just because a second stream joined — normalize=0 is what keeps the hit
    (and the duck) at the levels this module actually set."""
    fc = build_hook_sfx_filter()
    assert fc.count("normalize=0") == 2  # the thump+click mix AND the final mix


@pytest.mark.parametrize("label", ["[thump]", "[click]", "[hit]", "[sfx]", "[ducked]"])
def test_every_intermediate_label_is_produced_and_consumed_exactly_once(label):
    """An orphan label (produced but never consumed, or vice versa) makes
    ffmpeg fail the whole -filter_complex, not just this effect."""
    fc = build_hook_sfx_filter()
    assert fc.count(label) == 2, f"{label} must appear exactly twice (def + use)"


def test_custom_thump_and_click_parameters_reach_the_graph():
    fc = build_hook_sfx_filter(thump_duration=0.2, click_duration=0.03, click_highpass=2000.0)
    assert "st=0.080:d=0.12" in fc  # thump decay starts at duration-0.12
    assert "d=0.030[click]" in fc
    assert "highpass=f=2000" in fc


def test_short_thump_duration_does_not_produce_a_negative_decay_start():
    """thump_duration - 0.12 would go negative for a very short thump; the
    decay start must clamp to 0 rather than emit a nonsensical fade offset."""
    fc = build_hook_sfx_filter(thump_duration=0.05)
    assert "st=0.000:d=0.12" in fc
