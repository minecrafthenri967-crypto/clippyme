"""Tests for compose.py's cold-open teaser layer.

``_apply_teaser`` is tested directly (like ``_apply_player_image``) because it
owns real logic: probing, the Smart-Cut remap decision, and — most importantly
— the OFFSET it hands back. That offset is what keeps every later timed layer
honest, so the tests here focus on it as hard as on the render itself.
"""
import asyncio
import os

import pytest

from clippyme.domain import compose


def _touch(path):
    with open(path, "wb") as f:
        f.write(b"x")
    return path


CLIP = {"start": 100.0, "end": 160.0, "peak_start": 130.0, "peak_end": 132.0}


def _stub_probe(monkeypatch, duration=60.0, has_audio=True):
    monkeypatch.setattr(compose, "_probe_qa", lambda p: (duration, has_audio, 1234))


def _stub_prepend(monkeypatch, calls):
    """Fake the ffmpeg render, recording its kwargs and creating the output."""
    def fake(video_path, output_path, **kwargs):
        calls.append({"input": video_path, "output": output_path, **kwargs})
        _touch(output_path)
        return True

    import clippyme.domain.teaser as teaser_mod
    monkeypatch.setattr(teaser_mod, "prepend_teaser", fake)
    return calls


def _run(tmp_path, clip=None, toggles_params=None, smartcut_rendered=False,
         metadata=None, drop_ranges=None):
    return asyncio.run(compose._apply_teaser(
        _touch(str(tmp_path / "in.mp4")), str(tmp_path), 0,
        toggles_params or {}, clip if clip is not None else dict(CLIP),
        metadata or {}, drop_ranges, smartcut_rendered, [],
    ))


def test_teaser_renders_and_reports_its_own_length_as_the_offset(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    out, offset = _run(tmp_path)

    assert os.path.basename(out) == "composed_teaser_0.mp4"
    assert offset == 2.0
    assert calls[0]["start"] == 30.0 and calls[0]["end"] == 32.0


def test_offset_matches_the_trimmed_window_when_the_peak_was_overlong(tmp_path, monkeypatch):
    """The offset must describe what was ACTUALLY prepended, not what Gemini
    asked for — a later layer shifted by the wrong amount fires off-beat."""
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    clip = {**CLIP, "peak_end": 145.0}  # 15s — trimmed to the 3s max
    _out, offset = _run(tmp_path, clip=clip)

    rendered = calls[0]["end"] - calls[0]["start"]
    assert offset == rendered == 3.0


def test_no_peak_returns_the_input_untouched_and_a_zero_offset(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    clip = {**CLIP, "peak_start": None, "peak_end": None}
    out, offset = _run(tmp_path, clip=clip)

    assert offset == 0.0
    assert os.path.basename(out) == "in.mp4"
    assert calls == [], "no ffmpeg pass should have been spawned"


def test_render_failure_skips_the_layer_instead_of_failing_the_compose(tmp_path, monkeypatch):
    """Same defensive posture as _apply_banner: a broken teaser costs the
    teaser, not the whole clip the user is waiting for."""
    _stub_probe(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    import clippyme.domain.teaser as teaser_mod
    monkeypatch.setattr(teaser_mod, "prepend_teaser", boom)

    out, offset = _run(tmp_path)
    assert offset == 0.0 and os.path.basename(out) == "in.mp4"


def test_unprobeable_input_skips_the_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(compose, "_probe_qa", lambda p: (None, True, None))
    calls = _stub_prepend(monkeypatch, [])

    _out, offset = _run(tmp_path)
    assert offset == 0.0 and calls == []


def test_silent_input_renders_without_the_audio_branch(tmp_path, monkeypatch):
    _stub_probe(monkeypatch, has_audio=False)
    calls = _stub_prepend(monkeypatch, [])

    _run(tmp_path)
    assert calls[0]["has_audio"] is False


def test_smartcut_remap_only_runs_when_smartcut_actually_rendered(tmp_path, monkeypatch):
    """smart_cut can decline to render even with the toggle on; remapping then
    would shift the window against a cut that never happened."""
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])
    monkeypatch.setattr(
        compose, "analyze_silences",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not remap")),
    )

    _run(tmp_path, smartcut_rendered=False)
    assert calls[0]["start"] == 30.0


def test_smartcut_rendered_remaps_the_window(tmp_path, monkeypatch):
    _stub_probe(monkeypatch, duration=50.0)
    calls = _stub_prepend(monkeypatch, [])
    # 10s removed at 5-15 → everything after shifts 10s earlier.
    monkeypatch.setattr(compose, "analyze_silences",
                        lambda *a, **k: ([(0.0, 5.0), (15.0, 60.0)], None))

    _out, offset = _run(tmp_path, smartcut_rendered=True,
                        metadata={"transcript": {"segments": []}})

    assert calls[0]["start"] == 20.0 and calls[0]["end"] == 22.0
    assert offset == 2.0


def test_peak_cut_away_by_smartcut_skips_the_teaser(tmp_path, monkeypatch):
    _stub_probe(monkeypatch, duration=25.0)
    calls = _stub_prepend(monkeypatch, [])
    monkeypatch.setattr(compose, "analyze_silences",
                        lambda *a, **k: ([(0.0, 5.0), (40.0, 60.0)], None))

    _out, offset = _run(tmp_path, smartcut_rendered=True,
                        metadata={"transcript": {"segments": []}})

    assert offset == 0.0 and calls == []


def test_max_duration_param_reaches_the_window_resolver(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    _out, offset = _run(tmp_path, toggles_params={"max_duration": 1.2},
                        clip={**CLIP, "peak_end": 140.0})
    assert offset == pytest.approx(1.2)
    assert calls[0]["end"] - calls[0]["start"] == pytest.approx(1.2)


def test_fade_param_reaches_the_renderer(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    _run(tmp_path, toggles_params={"fade": 0.3})
    assert calls[0]["fade"] == 0.3


# --- the offset actually reaching the later timed layers --------------------

def test_hook_window_is_extended_by_the_teaser_length(tmp_path, monkeypatch):
    """Without this the teaser eats into the hook's 4s and the clip's real
    opening gets only the remainder."""
    seen = {}

    def fake_add_hook(video, text, out, position, font_scale, offset_y,
                      style, logo, duration):
        seen["duration"] = duration
        _touch(out)

    import clippyme.domain.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "add_hook_to_video", fake_add_hook)

    asyncio.run(compose._apply_hook(
        _touch(str(tmp_path / "in.mp4")), str(tmp_path), 0,
        {"text": "hi"}, [], teaser_offset=2.5,
    ))
    assert seen["duration"] == 6.5


def test_letterbox_hook_stays_full_clip_even_with_a_teaser(tmp_path, monkeypatch):
    """reframe_mode 'disabled' means "hook for the whole clip" — a teaser must
    not turn that back into a bounded window."""
    seen = {}

    def fake_add_hook(video, text, out, position, font_scale, offset_y,
                      style, logo, duration):
        seen["duration"] = duration
        _touch(out)

    import clippyme.domain.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "add_hook_to_video", fake_add_hook)

    asyncio.run(compose._apply_hook(
        _touch(str(tmp_path / "in.mp4")), str(tmp_path), 0,
        {"text": "hi"}, [], reframe_mode="disabled", teaser_offset=2.5,
    ))
    assert seen["duration"] is None


def test_transition_params_reach_the_renderer(tmp_path, monkeypatch):
    """The punch is the default; a recipe can still override it per job."""
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    _run(tmp_path, toggles_params={
        "transition": "fade", "punch": 1.3, "punch_duration": 0.4,
    })
    assert calls[0]["transition"] == "fade"
    assert calls[0]["punch"] == 1.3
    assert calls[0]["punch_duration"] == 0.4


def test_transition_defaults_to_the_zoom_punch(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)
    calls = _stub_prepend(monkeypatch, [])

    _run(tmp_path)
    assert calls[0]["transition"] == "punch"
