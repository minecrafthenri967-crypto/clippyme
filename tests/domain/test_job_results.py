"""Tests for clippyme.domain.job_results command/result helpers."""
import json

import pytest

from clippyme.domain.job_results import (
    MAX_INSTRUCTIONS_LEN,
    _build_clips,
    build_main_cmd,
    load_final_result,
)


def test_url_job_builds_expected_argv():
    cmd = build_main_cmd(url="https://youtu.be/abc", output_dir="output")
    assert cmd[:4] == ["python", "-u", "-m", "clippyme.pipeline.orchestrator"]
    assert "-u" in cmd and "https://youtu.be/abc" in cmd
    assert cmd[cmd.index("-o") + 1] == "output"


def test_input_path_job_uses_dash_i():
    cmd = build_main_cmd(input_path="uploads/clip.mp4", output_dir="output")
    assert "-i" in cmd and "uploads/clip.mp4" in cmd
    assert "-u" not in cmd[4:]


def test_cookies_appended_only_when_file_exists(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape")
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", cookies_path=str(cookies))
    assert "-c" in cmd and str(cookies) in cmd


def test_cookies_skipped_when_file_missing(tmp_path):
    missing = tmp_path / "nope.txt"
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", cookies_path=str(missing))
    assert "-c" not in cmd


def test_optional_flags_assembled():
    cmd = build_main_cmd(
        url="https://x.com/v", output_dir="o",
        instructions="focus on hooks", reframe_mode="disabled",
        language="it", no_zoom=True, skip_analysis=True,
    )
    assert cmd[cmd.index("--instructions") + 1] == "focus on hooks"
    assert cmd[cmd.index("--reframe-mode") + 1] == "disabled"
    assert cmd[cmd.index("--language") + 1] == "it"
    assert "--no-zoom" in cmd
    assert "--skip-analysis" in cmd


def test_reframe_mode_auto_is_omitted():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", reframe_mode="auto")
    assert "--reframe-mode" not in cmd


def test_build_main_cmd_monitor_flag():
    cmd = build_main_cmd(input_path="/x.mp4", output_dir="/out", monitor=True)
    assert "--monitor" in cmd


def test_build_main_cmd_no_monitor_by_default():
    cmd = build_main_cmd(input_path="/x.mp4", output_dir="/out")
    assert "--monitor" not in cmd


def test_reframe_mode_subject_is_forwarded():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", reframe_mode="subject")
    assert cmd[cmd.index("--reframe-mode") + 1] == "subject"


def test_reframe_mode_object_legacy_alias_is_accepted():
    from clippyme.domain.job_results import canonical_reframe_mode

    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", reframe_mode="object")
    assert cmd[cmd.index("--reframe-mode") + 1] == "object"
    assert canonical_reframe_mode("object") == "subject"
    assert canonical_reframe_mode("auto") == "auto"
    assert canonical_reframe_mode(None) is None


def test_reframe_mode_gaming_is_forwarded():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", reframe_mode="gaming")
    assert cmd[cmd.index("--reframe-mode") + 1] == "gaming"


_BOX = {"x": 0.6, "y": 0.05, "w": 0.35, "h": 0.3}


def test_gaming_facecam_box_is_forwarded():
    cmd = build_main_cmd(
        url="https://x.com/v", output_dir="o", reframe_mode="gaming",
        gaming_facecam_box=_BOX,
    )
    assert cmd[cmd.index("--gaming-facecam-x") + 1] == "0.6"
    assert cmd[cmd.index("--gaming-facecam-y") + 1] == "0.05"
    assert cmd[cmd.index("--gaming-facecam-w") + 1] == "0.35"
    assert cmd[cmd.index("--gaming-facecam-h") + 1] == "0.3"


def test_gaming_facecam_no_box_omits_flags():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", reframe_mode="gaming")
    assert "--gaming-facecam-x" not in cmd


def test_gaming_facecam_box_ignored_outside_gaming_mode():
    # A manual facecam box only makes sense in gaming mode — it must not leak
    # into argv for other reframe modes even if somehow supplied.
    cmd = build_main_cmd(
        url="https://x.com/v", output_dir="o", reframe_mode="auto",
        gaming_facecam_box=_BOX,
    )
    assert "--gaming-facecam-x" not in cmd


@pytest.mark.parametrize("bad_box", [
    {"x": 0.6, "y": 0.05, "w": 0.35},  # missing key
    {"x": 0.6, "y": 0.05, "w": 0.35, "h": 0.3, "extra": 1},  # extra key
    {"x": 1.5, "y": 0.05, "w": 0.35, "h": 0.3},  # out of [0,1]
    {"x": -0.1, "y": 0.05, "w": 0.35, "h": 0.3},  # negative
    {"x": 0.6, "y": 0.05, "w": 0, "h": 0.3},  # zero width
    {"x": 0.8, "y": 0.05, "w": 0.35, "h": 0.3},  # x+w > 1
    {"x": 0.6, "y": 0.85, "w": 0.35, "h": 0.3},  # y+h > 1
    {"x": "0.6", "y": 0.05, "w": 0.35, "h": 0.3},  # wrong type
])
def test_invalid_gaming_facecam_box_rejected(bad_box):
    with pytest.raises(ValueError, match="invalid gaming_facecam_box"):
        build_main_cmd(
            url="https://x.com/v", output_dir="o", reframe_mode="gaming",
            gaming_facecam_box=bad_box,
        )


def test_model_forwarded_when_valid():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", model="gemini-2.5-pro")
    assert cmd[cmd.index("--model") + 1] == "gemini-2.5-pro"


def test_model_omitted_when_none_or_blank():
    assert "--model" not in build_main_cmd(url="https://x.com/v", output_dir="o")
    assert "--model" not in build_main_cmd(url="https://x.com/v", output_dir="o", model="")
    assert "--model" not in build_main_cmd(url="https://x.com/v", output_dir="o", model="   ")


def test_model_future_gemini_family_accepted():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", model="gemini-3-pro")
    assert cmd[cmd.index("--model") + 1] == "gemini-3-pro"


@pytest.mark.parametrize("bad", [
    "gpt-4o",
    "gemini",
    "gemini-2.5-pro; rm -rf",
    "--inject",
    "gemini-" + "x" * 100,
])
def test_model_rejects_invalid(bad):
    with pytest.raises(ValueError):
        build_main_cmd(url="https://x.com/v", output_dir="o", model=bad)


def test_language_multi_is_omitted():
    cmd = build_main_cmd(url="https://x.com/v", output_dir="o", language="multi")
    assert "--language" not in cmd


def test_invalid_reframe_mode_rejected():
    with pytest.raises(ValueError, match="invalid reframe_mode"):
        build_main_cmd(url="https://x.com/v", output_dir="o", reframe_mode="zoomzoom")


def test_unsupported_language_rejected():
    with pytest.raises(ValueError, match="unsupported language"):
        build_main_cmd(url="https://x.com/v", output_dir="o", language="klingon")


def test_overlong_instructions_rejected():
    with pytest.raises(ValueError, match="instructions too long"):
        build_main_cmd(
            url="https://x.com/v", output_dir="o",
            instructions="x" * (MAX_INSTRUCTIONS_LEN + 1),
        )


def test_url_starting_with_dash_rejected():
    with pytest.raises(ValueError, match="url must not start with '-'"):
        build_main_cmd(url="--config=/etc/evil", output_dir="o")


def test_url_with_leading_whitespace_dash_rejected():
    with pytest.raises(ValueError, match="url must not start with '-'"):
        build_main_cmd(url="   --help", output_dir="o")


def test_input_path_starting_with_dash_rejected():
    with pytest.raises(ValueError, match="input_path must not start with '-'"):
        build_main_cmd(input_path="-rf", output_dir="o")


def test_build_clips_uses_clip_filename_from_metadata(tmp_path):
    (tmp_path / "My Title_clip_1.mp4").write_bytes(b"\x00")
    data = {"shorts": [{"clip_filename": "My Title_clip_1.mp4", "start": 0, "end": 5}]}
    result = _build_clips(data, "vid", "job1", str(tmp_path), only_ready=True)
    assert len(result) == 1
    assert result[0]["video_url"] == "/videos/job1/My Title_clip_1.mp4"


def test_build_clips_legacy_metadata_positional_unchanged(tmp_path):
    (tmp_path / "vid_clip_1.mp4").write_bytes(b"\x00")
    data = {"shorts": [{"start": 0, "end": 5}]}
    result = _build_clips(data, "vid", "job1", str(tmp_path), only_ready=True)
    assert len(result) == 1
    assert result[0]["video_url"] == "/videos/job1/vid_clip_1.mp4"


@pytest.mark.parametrize("bad", ["../evil.mp4", "sub/dir_clip_1.mp4", "sub\\dir_clip_1.mp4"])
def test_build_clips_ignores_tampered_clip_filename(tmp_path, bad):
    (tmp_path / "vid_clip_1.mp4").write_bytes(b"\x00")
    data = {"shorts": [{"clip_filename": bad, "start": 0, "end": 5}]}
    result = _build_clips(data, "vid", "job1", str(tmp_path), only_ready=True)
    assert len(result) == 1
    assert result[0]["video_url"] == "/videos/job1/vid_clip_1.mp4"


def test_build_clips_partial_job_mid_processing_first_clip_ready(tmp_path):
    (tmp_path / "Ready Clip Title_clip_1.mp4").write_bytes(b"\x00")
    data = {"shorts": [
        {"clip_filename": "Ready Clip Title_clip_1.mp4", "start": 0, "end": 5},
        {"start": 5, "end": 10},
    ]}
    result = _build_clips(data, "vid", "job1", str(tmp_path), only_ready=True)
    assert len(result) == 1
    assert result[0]["video_url"] == "/videos/job1/Ready Clip Title_clip_1.mp4"
    assert result[0]["original_index"] == 0


def test_build_clips_skips_deleted_after_publish_even_in_final_result(tmp_path):
    (tmp_path / "vid_clip_1.mp4").write_bytes(b"\x00")
    (tmp_path / "vid_clip_3.mp4").write_bytes(b"\x00")
    data = {"shorts": [
        {"start": 0, "end": 5},
        {"start": 5, "end": 10, "deleted_after_publish": True},
        {"start": 10, "end": 15},
    ]}
    result = _build_clips(data, "vid", "job1", str(tmp_path), only_ready=False)
    assert [clip["original_index"] for clip in result] == [0, 2]
    assert all(not clip.get("deleted_after_publish") for clip in result)


def test_load_final_result_surfaces_gemini_exhausted(tmp_path):
    metadata = tmp_path / "vid_metadata.json"
    metadata.write_text(json.dumps({"shorts": [], "gemini_exhausted": True}))
    result = load_final_result("job1", str(tmp_path))
    assert result["gemini_exhausted"] is True


def test_load_final_result_includes_runtime_operations(tmp_path):
    from clippyme.domain.runtime_state import RuntimeState

    RuntimeState(str(tmp_path), job_id="job1").start("quality", progress=92)
    (tmp_path / "vid_metadata.json").write_text(json.dumps({"shorts": []}))
    result = load_final_result("job1", str(tmp_path))
    assert result["operations"]["stage"] == "quality"
    assert result["operations"]["progress"] == 92


# --- drawn gameplay region + split in argv ----------------------------------

from clippyme.domain.job_results import build_main_cmd as _bmc  # noqa: E402
import pytest as _pytest  # noqa: E402

_GAMEPLAY_BOX = {"x": 0.3, "y": 0.4, "w": 0.35, "h": 0.55}


def test_gameplay_box_reaches_argv_for_gaming():
    cmd = _bmc(url="https://x.test/v", output_dir="/tmp/o",
               reframe_mode="gaming", gaming_gameplay_box=_GAMEPLAY_BOX)
    for flag, key in (("--gaming-gameplay-x", "x"), ("--gaming-gameplay-y", "y"),
                      ("--gaming-gameplay-w", "w"), ("--gaming-gameplay-h", "h")):
        assert cmd[cmd.index(flag) + 1] == str(_GAMEPLAY_BOX[key])


def test_gameplay_box_ignored_outside_gaming_mode():
    # A layout drawn for gaming must not silently reshape an `auto` job.
    cmd = _bmc(url="https://x.test/v", output_dir="/tmp/o",
               reframe_mode="auto", gaming_gameplay_box=_GAMEPLAY_BOX)
    assert "--gaming-gameplay-x" not in cmd


def test_split_reaches_argv_and_is_range_checked():
    cmd = _bmc(url="https://x.test/v", output_dir="/tmp/o",
               reframe_mode="gaming", gaming_split=0.5)
    assert cmd[cmd.index("--gaming-split") + 1] == "0.5"
    for bad in (0.05, 0.9):
        with _pytest.raises(ValueError, match="gaming_split"):
            _bmc(url="https://x.test/v", output_dir="/tmp/o",
                 reframe_mode="gaming", gaming_split=bad)


def test_gameplay_box_is_validated_like_the_facecam_box():
    with _pytest.raises(ValueError, match="gaming_gameplay_box"):
        _bmc(url="https://x.test/v", output_dir="/tmp/o", reframe_mode="gaming",
             gaming_gameplay_box={"x": 0.8, "y": 0.0, "w": 0.5, "h": 0.5})
