"""Host-unit tests for the hook overlay filter builder (pure, #5 animated hooks)."""
from clippyme.domain.hooks import _enable_suffix, build_hook_overlay_filter


# --- _enable_suffix (generalized for the player-image overlay's arbitrary window) --

def test_enable_suffix_none_is_empty_regardless_of_start():
    assert _enable_suffix(None) == ""
    assert _enable_suffix(None, 3) == ""


def test_enable_suffix_default_start_unchanged():
    assert _enable_suffix(4) == ":enable='between(t,0,4)'"


def test_enable_suffix_explicit_start():
    assert _enable_suffix(6, 3) == ":enable='between(t,3,6)'"


def test_static_is_legacy_byte_identical():
    assert build_hook_overlay_filter(12, 340) == "[0:v][1:v]overlay=12:340"


def test_static_coerces_ints():
    assert build_hook_overlay_filter(12.9, 340.2) == "[0:v][1:v]overlay=12:340"


def test_animate_has_fade_and_eased_slide():
    f = build_hook_overlay_filter(10, 200, animate=True)
    assert "fade=t=in:st=0:d=0.4:alpha=1" in f
    assert "format=yuva420p" in f
    # ease-out-cubic via pow(1-p,3), commas escaped for the filtergraph parser.
    assert "pow(1-min(t/0.4\\,1)\\,3)" in f
    assert f.startswith("[1:v]")
    assert "overlay=10:200+40*pow" in f


def test_animate_custom_params():
    f = build_hook_overlay_filter(0, 0, animate=True, dur=0.6, slide_px=80)
    assert "d=0.6" in f
    assert "overlay=0:0+80*pow" in f


# --- hook visibility window (first-4s hook, whole-clip when reframe disabled) --

def test_static_enable_window_applied():
    f = build_hook_overlay_filter(12, 340, enable_end=4)
    assert f == "[0:v][1:v]overlay=12:340:enable='between(t,0,4)'"


def test_static_no_enable_window_when_none():
    f = build_hook_overlay_filter(12, 340, enable_end=None)
    assert "enable" not in f


def test_animate_enable_window_applied():
    f = build_hook_overlay_filter(10, 200, animate=True, enable_end=4)
    assert f.endswith(":enable='between(t,0,4)'")


def test_logo_filter_enable_window_only_on_hook():
    from clippyme.domain.hooks import build_hook_logo_filter

    f = build_hook_logo_filter(10, 20, "scale=100:-1", "5", "7", enable_end=4)
    assert f == (
        "[0:v][1:v]overlay=10:20:enable='between(t,0,4)'[vh];"
        "[2:v]scale=100:-1[lg];[vh][lg]overlay=5:7"
    )
    # logo overlay part must stay untouched (no enable clause)
    assert "overlay=5:7" in f and "overlay=5:7:enable" not in f


# --- resolve_hook_overlay_y (placement policy, incl. the gaming seam) --------

from clippyme.domain.hooks import HOOK_POSITIONS, resolve_hook_overlay_y  # noqa: E402


def test_top_is_the_default_and_the_unknown_value_fallback():
    # Overlay params are a free-form dict (validated as scalars, not against an
    # allow-list), so a typo must still render rather than raise.
    assert resolve_hook_overlay_y("top", 1920, 200) == 384
    assert resolve_hook_overlay_y("wat", 1920, 200) == 384


def test_center_centres_the_box_not_its_top_edge():
    assert resolve_hook_overlay_y("center", 1920, 200) == 860
    assert resolve_hook_overlay_y("middle", 1920, 200) == 860  # legacy alias


def test_bottom_sits_at_70_percent():
    assert resolve_hook_overlay_y("bottom", 1920, 200) == 1344


def test_seam_straddles_the_gaming_split(monkeypatch):
    monkeypatch.delenv("REFRAME_GAMING_FACECAM_FRACTION", raising=False)
    # 1920 * 0.45 = 864; a 200px box centred on the cut starts at 764. The
    # point is that the box's MIDDLE lands on the seam, so it reads as one
    # element bridging facecam and gameplay.
    assert resolve_hook_overlay_y("seam", 1920, 200) == 764


def test_seam_follows_the_configured_facecam_fraction(monkeypatch):
    monkeypatch.setenv("REFRAME_GAMING_FACECAM_FRACTION", "0.6")
    # Moving the split must move the hook with it, or the two drift apart.
    assert resolve_hook_overlay_y("seam", 1920, 200) == 1052


def test_offset_nudges_by_percent_of_frame_height():
    base = resolve_hook_overlay_y("top", 1920, 200)
    assert resolve_hook_overlay_y("top", 1920, 200, offset_y=10) == base + 192


def test_offset_cannot_push_the_box_off_canvas():
    assert resolve_hook_overlay_y("top", 1920, 200, offset_y=500) == 1720
    assert resolve_hook_overlay_y("top", 1920, 200, offset_y=-500) == 0


def test_seam_is_an_advertised_position():
    assert "seam" in HOOK_POSITIONS


# --- fractional hook placement (drawn in the layout editor) ------------------

from clippyme.domain.hooks import parse_hook_position_fraction  # noqa: E402


def test_fraction_centres_the_box_on_that_height():
    # Drawn at 40% of the frame: the box's MIDDLE lands there, matching how
    # the seam and the editor's preview both behave.
    assert resolve_hook_overlay_y(0.4, 1920, 200) == 668


def test_fraction_accepts_a_numeric_string():
    # Overlay params ride as a free-form scalar dict; a form/JSON round-trip
    # can hand this over as text.
    assert resolve_hook_overlay_y("0.4", 1920, 200) == 668


def test_fraction_is_clamped_to_the_frame():
    assert resolve_hook_overlay_y(1.0, 1920, 200) == 1720
    assert resolve_hook_overlay_y(0.0, 1920, 200) == 0


def test_keywords_still_win_over_fraction_parsing():
    # "top" is not a number, so the keyword branch must still run.
    assert resolve_hook_overlay_y("top", 1920, 200) == 384


def test_parse_rejects_out_of_range_and_non_numeric():
    assert parse_hook_position_fraction(1.5) is None
    assert parse_hook_position_fraction(-0.1) is None
    assert parse_hook_position_fraction("bottom") is None
    assert parse_hook_position_fraction(True) is None  # bool is not a position
    assert parse_hook_position_fraction(0.5) == 0.5


# --- add_hook_to_video: the ffmpeg INPUT flags for the animated hook ---------
#
# The tests above pin the filter STRING; nothing pinned the command built
# around it, which is exactly where the animated hook silently broke: the hook
# PNG was fed as a bare `-i image.png`, i.e. ONE frame at t=0, while the
# animation applies `fade=t=in:st=0:alpha=1` — alpha 0 at exactly t=0. That
# single fully-transparent frame was then held for the whole clip, so the hook
# rendered INVISIBLE with no error at all (verified by render: 1/255 deviation
# from the bare background with animate, 223/255 without).

import subprocess  # noqa: E402

import pytest  # noqa: E402

from clippyme.domain import hooks as hooks_mod  # noqa: E402


@pytest.fixture
def _capture_ffmpeg(tmp_path, monkeypatch):
    """Run add_hook_to_video with every subprocess faked; return the argv."""
    video = tmp_path / "in.mp4"
    video.write_bytes(b"x")
    captured = {}

    monkeypatch.setattr(hooks_mod.subprocess, "check_output",
                        lambda *a, **k: b"1080x1920")
    monkeypatch.setattr(hooks_mod, "create_hook_image",
                        lambda *a, **k: (str(tmp_path / "hook.png"), 300, 120))

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(hooks_mod.subprocess, "run", _fake_run)

    def _run(animate):
        hooks_mod.add_hook_to_video(
            str(video), "HOOK", str(tmp_path / "out.mp4"),
            style={"animate": animate}, hook_duration=4,
        )
        return captured["cmd"]

    return _run


def test_animated_hook_loops_the_still_and_bounds_the_output(_capture_ffmpeg):
    cmd = _capture_ffmpeg(True)
    # The image input must loop, or its single t=0 frame is what the alpha
    # fade sees — and that frame is fully transparent.
    assert "-loop" in cmd, "animated hook needs -loop on the image input"
    assert cmd[cmd.index("-loop") + 1] == "1"
    assert cmd.index("-loop") < cmd.index("-filter_complex")
    # A looping image input never ends: without -shortest the output runs
    # forever (measured: a 3s clip was past 366s and still growing).
    assert "-shortest" in cmd, "a looping image input must be bounded"


def test_static_hook_keeps_its_single_image_input(_capture_ffmpeg):
    """The working (non-animated) path must stay byte-identical."""
    cmd = _capture_ffmpeg(False)
    assert "-loop" not in cmd
    assert "-shortest" not in cmd
