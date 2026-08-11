"""Tests for compose.py's player-image layer: caching, matching, the
Smart-Cut timestamp remap interplay, and where it sits in the layer order.

``_apply_player_image`` is tested directly (like a mini-orchestrator, not a
thin ``_apply_*`` wrapper) since it owns real new logic (detect-once-cache,
re-match-every-call, remap-or-skip) — the underlying domain calls
(``detect_player_mentions``, ``list_player_images``, ``match_player_image``,
``add_player_image_to_video``) are monkeypatched at their source module so
the deferred ``from ... import ...`` inside the function picks up the fakes.
"""
import asyncio
import json
import os

import pytest

from clippyme.domain import compose
from clippyme.domain import job_artifacts as ja


def _touch(path):
    with open(path, "wb") as f:
        f.write(b"x")
    return path


def _write_metadata(job_dir, shorts):
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, "vid_metadata.json")
    with open(path, "w") as f:
        json.dump({"shorts": shorts}, f)
    return path


TRANSCRIPT = {
    "segments": [{"words": [
        {"word": "and", "start": 0.0, "end": 0.5},
        {"word": "he", "start": 0.5, "end": 0.8},
        {"word": "pulls", "start": 0.8, "end": 1.2},
        {"word": "LeBron", "start": 5.0, "end": 5.4},
        {"word": "James", "start": 5.4, "end": 5.8},
    ]}],
}


def _run_apply(
    tmp_path, monkeypatch, *,
    clip_info=None, mentions_result=None, library=None, match=None,
    render_ok=True, config=None, smartcut_rendered=False, drop_ranges=None,
    detect_side_effect=None, merged_segments=None,
):
    job_dir = str(tmp_path)
    metadata_path = _write_metadata(job_dir, [{"start": 0, "end": 30}])
    current_input = _touch(os.path.join(job_dir, "clip_0.mp4"))
    clip_info = clip_info if clip_info is not None else {"start": 0, "end": 30}
    metadata = {"transcript": TRANSCRIPT}

    if merged_segments is not None:
        # Controlled kept-segments list instead of the real silence detector
        # (which would also auto-cut the fixture transcript's word gaps) —
        # isolates the remap integration from analyze_silences's own tuning.
        monkeypatch.setattr(compose, "analyze_silences", lambda *a, **k: (merged_segments, {}))

    detect_calls = []

    def fake_detect(**kwargs):
        detect_calls.append(kwargs)
        if detect_side_effect is not None:
            raise detect_side_effect
        return mentions_result if mentions_result is not None else []

    monkeypatch.setattr("clippyme.domain.player_detect.detect_player_mentions", fake_detect)
    monkeypatch.setattr(
        "clippyme.domain.player_image.list_player_images",
        lambda: library if library is not None else [])
    monkeypatch.setattr(
        "clippyme.domain.player_image.match_player_image",
        lambda name, names=None: match)
    monkeypatch.setattr(
        "clippyme.storage.config_store.load_persistent_config",
        lambda: config or {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-3.5-flash"})
    monkeypatch.setattr(compose, "_probe_qa", lambda path: (30.0, True, 100))

    render_calls = []

    def fake_render(video_path, image_path, output_path, **kwargs):
        render_calls.append(kwargs)
        if not render_ok:
            raise RuntimeError("ffmpeg exploded")
        _touch(output_path)
        return True

    monkeypatch.setattr("clippyme.domain.player_image.add_player_image_to_video", fake_render)

    intermediate_files = []
    result = asyncio.run(compose._apply_player_image(
        current_input, job_dir, 0, {}, clip_info, metadata, metadata_path,
        drop_ranges, smartcut_rendered, intermediate_files,
    ))
    return result, detect_calls, render_calls, metadata_path, clip_info, intermediate_files


# --- caching -----------------------------------------------------------------

def test_first_call_detects_and_caches_into_metadata(tmp_path, monkeypatch):
    result, detect_calls, render_calls, metadata_path, clip_info, _ = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}],
        library=["LeBron James"], match="LeBron James",
    )
    assert len(detect_calls) == 1
    assert clip_info["player_mentions"] == [
        {"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}]
    with open(metadata_path) as f:
        saved = json.load(f)
    assert saved["shorts"][0]["player_mentions"] == [
        {"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}]
    assert len(render_calls) == 1
    assert result == os.path.join(str(tmp_path), "composed_player_image_0.mp4")


def test_second_call_does_not_redetect_when_clip_info_already_cached(tmp_path, monkeypatch):
    clip_info = {"start": 0, "end": 30, "player_mentions": [
        {"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}]}
    _, detect_calls, render_calls, _, _, _ = _run_apply(
        tmp_path, monkeypatch, clip_info=clip_info,
        library=["LeBron James"], match="LeBron James",
    )
    assert detect_calls == []  # never called — already cached
    assert len(render_calls) == 1  # matching + render still run every time


def test_never_caches_on_detection_failure(tmp_path, monkeypatch):
    result, detect_calls, render_calls, metadata_path, clip_info, _ = _run_apply(
        tmp_path, monkeypatch, detect_side_effect=RuntimeError("network blip"),
    )
    assert len(detect_calls) == 1
    assert "player_mentions" not in clip_info
    with open(metadata_path) as f:
        saved = json.load(f)
    assert "player_mentions" not in saved["shorts"][0]
    assert render_calls == []
    assert result == os.path.join(str(tmp_path), "clip_0.mp4")  # unchanged


# --- no-match handling ---------------------------------------------------

def test_no_mentions_detected_skips_layer(tmp_path, monkeypatch):
    result, _, render_calls, _, _, _ = _run_apply(tmp_path, monkeypatch, mentions_result=[])
    assert render_calls == []
    assert result == os.path.join(str(tmp_path), "clip_0.mp4")


def test_empty_library_skips_layer(tmp_path, monkeypatch):
    result, _, render_calls, _, _, _ = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}],
        library=[],
    )
    assert render_calls == []
    assert result == os.path.join(str(tmp_path), "clip_0.mp4")


def test_no_library_match_skips_layer(tmp_path, monkeypatch):
    result, _, render_calls, _, _, _ = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "Nobody", "timestamp": 5.0, "confidence": 0.9}],
        library=["LeBron James"], match=None,
    )
    assert render_calls == []
    assert result == os.path.join(str(tmp_path), "clip_0.mp4")


# --- Smart Cut interplay ---------------------------------------------------

def test_smartcut_off_uses_raw_timestamp_unmodified(tmp_path, monkeypatch):
    _, _, render_calls, _, _, _ = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}],
        library=["LeBron James"], match="LeBron James", smartcut_rendered=False,
    )
    assert render_calls[0]["start"] == 5.0


def test_smartcut_on_remaps_timestamp_through_kept_segments(tmp_path, monkeypatch):
    # Kept spans: [0,2) and [4,30) — original t=5.0 is 1s into the second
    # kept span; first kept span is 2s long, so output position = 2+1 = 3.
    _, _, render_calls, _, _, _ = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}],
        library=["LeBron James"], match="LeBron James", smartcut_rendered=True,
        merged_segments=[(0.0, 2.0), (4.0, 30.0)],
    )
    assert render_calls[0]["start"] == pytest.approx(3.0)


def test_smartcut_on_skips_when_moment_was_cut_away(tmp_path, monkeypatch):
    # The mention at t=5.0 falls inside a dropped span [4,6) → no output position.
    result, _, render_calls, _, _, _ = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}],
        library=["LeBron James"], match="LeBron James", smartcut_rendered=True,
        merged_segments=[(0.0, 4.0), (6.0, 30.0)],
    )
    assert render_calls == []
    assert result == os.path.join(str(tmp_path), "clip_0.mp4")


# --- render failure never propagates ----------------------------------------

def test_render_failure_is_swallowed_and_returns_unchanged(tmp_path, monkeypatch):
    result, _, render_calls, _, _, intermediate_files = _run_apply(
        tmp_path, monkeypatch,
        mentions_result=[{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}],
        library=["LeBron James"], match="LeBron James", render_ok=False,
    )
    assert len(render_calls) == 1
    assert result == os.path.join(str(tmp_path), "clip_0.mp4")
    # The failed output was tracked for cleanup but never actually written.
    pi_output = os.path.join(str(tmp_path), "composed_player_image_0.mp4")
    assert pi_output in intermediate_files
    assert not os.path.exists(pi_output)


# --- compose_layers-level ordering ------------------------------------------

def _install_recording_stubs(monkeypatch, order):
    async def fake_subtitles(current_input, job_dir, clip_index, metadata,
                             clip_info, subtitle_params, intermediate_files, pre_vf=None):
        order.append("subtitles")
        out = os.path.join(job_dir, f"composed_sub_{clip_index}.mp4")
        _touch(out)
        intermediate_files.append(out)
        return out

    async def fake_hook(current_input, job_dir, clip_index, hook_params,
                        intermediate_files, logo_params=None, reframe_mode=None,
                        teaser_offset=0.0):
        order.append("hook")
        out = os.path.join(job_dir, f"composed_hook_{clip_index}.mp4")
        _touch(out)
        intermediate_files.append(out)
        return out

    async def fake_player_image(current_input, job_dir, clip_index, player_image_params,
                                clip_info, metadata, metadata_path, drop_ranges,
                                smartcut_rendered, intermediate_files,
                                teaser_offset=0.0):
        order.append("player_image")
        out = os.path.join(job_dir, f"composed_player_image_{clip_index}.mp4")
        _touch(out)
        intermediate_files.append(out)
        return out

    async def fake_banner(current_input, job_dir, clip_index, banner_params,
                          clip_info, intermediate_files):
        order.append("banner")
        out = os.path.join(job_dir, f"composed_banner_{clip_index}.mp4")
        _touch(out)
        intermediate_files.append(out)
        return out

    monkeypatch.setattr(compose, "_apply_subtitles", fake_subtitles)
    monkeypatch.setattr(compose, "_apply_hook", fake_hook)
    monkeypatch.setattr(compose, "_apply_player_image", fake_player_image)
    monkeypatch.setattr(compose, "_apply_banner", fake_banner)


def test_player_image_runs_after_hook_and_before_banner(tmp_path, monkeypatch):
    order = []
    _install_recording_stubs(monkeypatch, order)
    base_clip = _touch(str(tmp_path / "clip_0.mp4"))
    asyncio.run(compose.compose_layers(
        base_clip=base_clip, job_dir=str(tmp_path), clip_index=0,
        metadata={"transcript": {}}, clip_info={"start": 0, "end": 30},
        toggles={"hook": True, "player_image": True, "banner": True},
        hook_params={"text": "Watch this", "position": "top", "size": "M"},
        subtitle_params={"mode": "karaoke"},
        banner_params={"enabled": True, "platform": "kick", "handle": "grenbaud"},
        player_image_params={},
    ))
    assert order == ["hook", "player_image", "banner"]
