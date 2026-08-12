"""Tests for compose.py's hook-impact-sound layer.

``_apply_hook_sfx`` is tested directly (like ``_apply_teaser``): it owns real
logic (the has_audio gate) even though the render itself is a thin call-out.
"""
import asyncio
import os

from clippyme.domain import compose


def _touch(path):
    with open(path, "wb") as f:
        f.write(b"x")
    return path


def _stub_probe(monkeypatch, has_audio=True):
    monkeypatch.setattr(compose, "_probe_qa", lambda p: (2.0, has_audio, 1234))


def _stub_render(monkeypatch, calls):
    def fake(video_path, output_path, **kwargs):
        calls.append({"input": video_path, "output": output_path, **kwargs})
        _touch(output_path)
        return True

    import clippyme.domain.hook_sfx as sfx_mod
    monkeypatch.setattr(sfx_mod, "render_hook_sfx", fake)
    return calls


def _run(tmp_path):
    return asyncio.run(compose._apply_hook_sfx(
        _touch(str(tmp_path / "in.mp4")), str(tmp_path), 0, [],
    ))


def test_renders_and_returns_the_new_path(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)
    calls = _stub_render(monkeypatch, [])

    out = _run(tmp_path)

    assert os.path.basename(out) == "composed_hooksfx_0.mp4"
    assert calls[0]["offset"] == 0.0


def test_silent_clip_skips_the_layer_instead_of_faking_audio(tmp_path, monkeypatch):
    _stub_probe(monkeypatch, has_audio=False)
    calls = _stub_render(monkeypatch, [])

    out = _run(tmp_path)

    assert os.path.basename(out) == "in.mp4"
    assert calls == []


def test_render_failure_skips_the_layer_instead_of_failing_the_compose(tmp_path, monkeypatch):
    _stub_probe(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    import clippyme.domain.hook_sfx as sfx_mod
    monkeypatch.setattr(sfx_mod, "render_hook_sfx", boom)

    out = _run(tmp_path)
    assert os.path.basename(out) == "in.mp4"


def test_unprobeable_input_skips_the_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(compose, "_probe_qa", lambda p: (None, False, None))
    calls = _stub_render(monkeypatch, [])

    out = _run(tmp_path)
    assert os.path.basename(out) == "in.mp4" and calls == []


# --- gating inside the full layer pipeline (hook_active AND hook_params.sfx) -

def test_sfx_only_fires_when_hook_params_sfx_is_true(tmp_path, monkeypatch):
    """Same clip, hook on, sfx flag off — the impact layer must not fire."""
    calls = []
    _stub_probe(monkeypatch)

    async def fake_hook_sfx(*a, **k):
        calls.append(1)
        return a[0]

    monkeypatch.setattr(compose, "_apply_hook_sfx", fake_hook_sfx)

    async def fake_hook(current_input, job_dir, clip_index, hook_params,
                        intermediate_files, **kw):
        out = os.path.join(job_dir, f"composed_hook_{clip_index}.mp4")
        _touch(out)
        return out

    monkeypatch.setattr(compose, "_apply_hook", fake_hook)

    base = _touch(str(tmp_path / "clip_0.mp4"))
    asyncio.run(compose.compose_layers(
        base_clip=base, job_dir=str(tmp_path), clip_index=0,
        metadata={}, clip_info={"start": 0, "end": 10}, toggles={"hook": True},
        hook_params={"text": "hi"}, subtitle_params={},
    ))
    assert calls == []


def test_sfx_fires_when_both_hook_and_sfx_flag_are_on(tmp_path, monkeypatch):
    calls = []
    _stub_probe(monkeypatch)

    async def fake_hook_sfx(current_input, *a, **k):
        calls.append(1)
        return current_input

    monkeypatch.setattr(compose, "_apply_hook_sfx", fake_hook_sfx)

    async def fake_hook(current_input, job_dir, clip_index, hook_params,
                        intermediate_files, **kw):
        out = os.path.join(job_dir, f"composed_hook_{clip_index}.mp4")
        _touch(out)
        return out

    monkeypatch.setattr(compose, "_apply_hook", fake_hook)

    base = _touch(str(tmp_path / "clip_0.mp4"))
    asyncio.run(compose.compose_layers(
        base_clip=base, job_dir=str(tmp_path), clip_index=0,
        metadata={}, clip_info={"start": 0, "end": 10}, toggles={"hook": True},
        hook_params={"text": "hi", "sfx": True}, subtitle_params={},
    ))
    assert calls == [1]


def test_sfx_does_not_fire_without_the_hook_toggle_even_if_flag_is_set(tmp_path, monkeypatch):
    """sfx is meaningless without a visible hook — the flag alone must not
    be enough if the hook toggle itself is off."""
    calls = []

    async def fake_hook_sfx(*a, **k):
        calls.append(1)
        return a[0]

    monkeypatch.setattr(compose, "_apply_hook_sfx", fake_hook_sfx)

    base = _touch(str(tmp_path / "clip_0.mp4"))
    asyncio.run(compose.compose_layers(
        base_clip=base, job_dir=str(tmp_path), clip_index=0,
        metadata={}, clip_info={"start": 0, "end": 10}, toggles={"logo": True},
        hook_params={"text": "hi", "sfx": True}, subtitle_params={},
        logo_params={"enabled": False},
    ))
    assert calls == []
