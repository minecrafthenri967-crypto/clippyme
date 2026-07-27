"""Phase 6: trajectory simplification, crop expression, filtergraph, argv."""

import json
import os
from itertools import pairwise

import pytest

from clipper_pro import render as phase6
from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError, ValidationError
from clipper_pro.reframe.plan import CameraPlan
from clipper_pro.render import build_render_command, clip_output_name
from clipper_pro.render.filtergraph_ops import (
    MAX_EXPRESSION_CHARS,
    build_filtergraph,
    crop_x_expression,
    simplify_trajectory,
)
from clipper_pro.types import CameraKeyframe, SourceMedia
from clipper_pro.workspace import init as workspace_init


def _kfs(xs, fps=30.0, start=0.0, width=607.0, height=1080.0):
    return [
        CameraKeyframe(time=start + i / fps, x=x, y=0.0, width=width, height=height)
        for i, x in enumerate(xs)
    ]


class TestSimplifyTrajectory:
    def test_a_straight_line_reduces_to_its_endpoints(self):
        # The whole point: a smoothed trajectory is mostly straight.
        anchors = simplify_trajectory(_kfs([0, 10, 20, 30, 40, 50]))
        assert len(anchors) == 2

    def test_a_constant_path_reduces_to_two_points(self):
        assert len(simplify_trajectory(_kfs([100] * 50))) == 2

    def test_a_corner_is_preserved(self):
        anchors = simplify_trajectory(_kfs([0, 0, 0, 0, 100, 200, 300]))
        assert len(anchors) >= 3

    def test_hundreds_of_frames_collapse_to_a_handful(self):
        # A per-frame expression would be a megabyte of command line.
        xs = [100.0] * 150 + list(range(100, 900, 8)) + [900.0] * 150
        anchors = simplify_trajectory(_kfs(xs))
        assert len(anchors) < 12

    def test_error_stays_within_tolerance(self):
        xs = [i * i * 0.05 for i in range(200)]
        keyframes = _kfs(xs)
        anchors = simplify_trajectory(keyframes, tolerance=2.0)
        # Every original point must sit within tolerance of the retained polyline
        # under linear interpolation — which is what ffmpeg will do.
        for keyframe in keyframes:
            assert abs(_interpolate(anchors, keyframe.time) - keyframe.x) <= 2.0 + 1e-6

    def test_tighter_tolerance_keeps_more_anchors(self):
        xs = [i * i * 0.05 for i in range(200)]
        loose = simplify_trajectory(_kfs(xs), tolerance=16.0)
        tight = simplify_trajectory(_kfs(xs), tolerance=0.5)
        assert len(tight) > len(loose)

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_degenerate_inputs_pass_through(self, n):
        assert len(simplify_trajectory(_kfs([100] * n))) == n

    def test_negative_tolerance_is_rejected(self):
        with pytest.raises(ValidationError, match="tolerance"):
            simplify_trajectory(_kfs([1, 2, 3]), tolerance=-1.0)


def _interpolate(anchors, t):
    """Linear interpolation through anchors — mirrors what the expression does."""
    if t <= anchors[0][0]:
        return anchors[0][1]
    for (t0, x0), (t1, x1) in pairwise(anchors):
        if t0 <= t <= t1:
            if t1 == t0:
                return x1
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return anchors[-1][1]


class TestCropXExpression:
    def test_a_static_path_is_a_bare_constant(self):
        # No reason to make ffmpeg evaluate a conditional per frame.
        assert crop_x_expression([(0.0, 250.0), (5.0, 250.0)]) == "250.000"

    def test_a_single_anchor_is_a_constant(self):
        assert crop_x_expression([(0.0, 42.0)]) == "42.000"

    def test_a_moving_path_produces_a_conditional(self):
        expression = crop_x_expression([(0.0, 0.0), (2.0, 200.0)])
        assert expression.startswith("if(lt(t,")
        assert "*(t-" in expression

    def test_time_offset_rebases_onto_the_trimmed_output(self):
        # ffmpeg's t restarts at 0 for the trimmed clip; forgetting this would
        # slide the whole camera path by the clip's start time.
        expression = crop_x_expression([(10.0, 0.0), (12.0, 200.0)], time_offset=10.0)
        assert "t-0.000" in expression
        assert "12.000" not in expression

    def test_expression_reproduces_the_anchors(self):
        anchors = [(0.0, 100.0), (1.0, 400.0), (2.0, 200.0)]
        expression = crop_x_expression(anchors)
        for time, expected in anchors[:-1]:
            assert _eval_ffmpeg_expr(expression, time) == pytest.approx(expected, abs=0.01)

    def test_interpolates_between_anchors(self):
        expression = crop_x_expression([(0.0, 0.0), (2.0, 200.0)])
        assert _eval_ffmpeg_expr(expression, 1.0) == pytest.approx(100.0, abs=0.1)

    def test_past_the_last_anchor_holds_the_final_value(self):
        expression = crop_x_expression([(0.0, 0.0), (2.0, 200.0)])
        assert _eval_ffmpeg_expr(expression, 99.0) == pytest.approx(200.0, abs=0.1)

    def test_zero_length_segment_is_skipped(self):
        expression = crop_x_expression([(0.0, 0.0), (1.0, 50.0), (1.0, 50.0), (2.0, 100.0)])
        assert _eval_ffmpeg_expr(expression, 1.5) == pytest.approx(75.0, abs=1.0)

    def test_no_anchors_is_rejected(self):
        with pytest.raises(ValidationError, match="no anchors"):
            crop_x_expression([])


def _eval_ffmpeg_expr(expression: str, t: float) -> float:
    """Evaluate the generated ffmpeg expression in Python, for verification."""
    import re

    py = expression.replace("if(lt(", "IF(LT(")
    # if(lt(t,X),A,B) -> (A if t<X else B); innermost-first via repeated substitution.
    pattern = re.compile(r"IF\(LT\(t,([-\d.]+)\),(.*)\)$", re.DOTALL)
    while (match := pattern.match(py)):
        threshold, rest = match.group(1), match.group(2)
        depth, split = 0, None
        for i, ch in enumerate(rest):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                split = i
                break
        true_part, false_part = rest[:split], rest[split + 1:]
        if t < float(threshold):
            py = true_part
            break
        py = false_part
    return float(eval(py, {"__builtins__": {}}, {"t": t}))


class TestBuildFiltergraph:
    def test_chains_crop_scale_and_setsar(self):
        graph, _anchors = build_filtergraph(_kfs([100, 200, 300]), start=0.0)
        assert graph.startswith("crop=w=607:h=1080:x='")
        assert ",scale=1080:1920:flags=lanczos" in graph
        # setsar=1 stops a non-square pixel aspect surviving into the output,
        # which some players honour and others ignore.
        assert graph.endswith(",setsar=1")

    def test_uses_the_geometry_phase_five_chose(self):
        graph, _ = build_filtergraph(
            _kfs([100, 100], width=405.0, height=720.0), start=0.0
        )
        assert "crop=w=405:h=720" in graph

    def test_honours_a_custom_output_size(self):
        graph, _ = build_filtergraph(
            _kfs([100, 100]), start=0.0, output_width=720, output_height=1280
        )
        assert "scale=720:1280" in graph

    def test_reports_the_anchor_count(self):
        _graph, anchors = build_filtergraph(_kfs([100] * 40), start=0.0)
        assert anchors == 2

    def test_rebases_the_expression_onto_the_clip_start(self):
        graph, _ = build_filtergraph(
            _kfs([0, 200, 400], start=30.0), start=30.0
        )
        assert "t-0.000" in graph

    def test_a_pathological_path_is_coarsened_not_left_oversized(self):
        # Alternating extremes defeat simplification at a fine tolerance; the
        # builder must raise the tolerance rather than emit a command line
        # ffmpeg would reject.
        xs = [0.0 if i % 2 else 900.0 for i in range(4000)]
        graph, _anchors = build_filtergraph(_kfs(xs, fps=60.0), start=0.0)
        assert len(graph) <= MAX_EXPRESSION_CHARS + 200

    def test_no_keyframes_is_rejected(self):
        with pytest.raises(ValidationError, match="without keyframes"):
            build_filtergraph([], start=0.0)

    @pytest.mark.parametrize("size", [(0, 1920), (1080, 0)])
    def test_invalid_output_size_is_rejected(self, size):
        with pytest.raises(ValidationError, match="output dimensions"):
            build_filtergraph(_kfs([1, 2]), start=0.0,
                              output_width=size[0], output_height=size[1])


class TestBuildRenderCommand:
    def _plan(self, start=5.0, end=20.0):
        return CameraPlan(
            clip_index=0, start=start, end=end, source_width=1920,
            source_height=1080, fps=30.0,
            keyframes=_kfs([300, 400, 500], start=start),
        )

    def test_is_a_single_ffmpeg_invocation(self):
        argv, _ = build_render_command("/v.mp4", self._plan(), "/out.mp4")
        assert argv[0] == "ffmpeg"
        # One -i and one -filter:v — the whole point of the phase.
        assert argv.count("-i") == 1
        assert argv.count("-filter:v") == 1

    def test_seeks_after_the_input_for_frame_accuracy(self):
        # Input seeking is GOP-aligned; a clip starting half a GOP early would
        # desync from the camera path the expression assumes.
        argv, _ = build_render_command("/v.mp4", self._plan(), "/out.mp4")
        assert argv.index("-i") < argv.index("-ss")

    def test_trims_to_the_clip_duration(self):
        argv, _ = build_render_command("/v.mp4", self._plan(5.0, 20.0), "/out.mp4")
        assert argv[argv.index("-ss") + 1] == "5.000"
        assert argv[argv.index("-t") + 1] == "15.000"

    def test_uses_the_requested_crf(self):
        argv, _ = build_render_command("/v.mp4", self._plan(), "/out.mp4", crf=18)
        assert "18" in argv[argv.index("-crf") + 1: argv.index("-crf") + 2]

    def test_reencodes_audio_rather_than_copying(self):
        # A stream copy would keep GOP-aligned packet boundaries and drift
        # against the frame-accurate video trim.
        argv, _ = build_render_command("/v.mp4", self._plan(), "/out.mp4")
        assert argv[argv.index("-c:a") + 1] == "aac"

    def test_output_is_the_last_argument(self):
        argv, _ = build_render_command("/v.mp4", self._plan(), "/out.mp4")
        assert argv[-1] == "/out.mp4"

    def test_paths_are_passed_as_argv_not_shell(self):
        weird = '/tmp/a video "x".mp4'
        argv, _ = build_render_command(weird, self._plan(), "/out.mp4")
        assert argv[argv.index("-i") + 1] == weird


class TestClipOutputName:
    def test_prefixes_with_a_sorting_index(self):
        # Index-first so an editor importing into CapCut gets timeline order.
        assert clip_output_name(0, "The pivot").startswith("clip_01_")
        assert clip_output_name(9, "x").startswith("clip_10_")

    def test_sanitises_a_title(self):
        name = clip_output_name(0, 'Why I Quit! (2024) 🎬')
        assert name.endswith(".mp4")
        assert all(c.isalnum() or c in "-_." for c in name)

    def test_untitled_clip_still_gets_a_name(self):
        assert clip_output_name(2) == "clip_03.mp4"

    def test_is_deterministic(self):
        assert clip_output_name(0, "a") == clip_output_name(0, "a")

    def test_truncates_a_long_title(self):
        assert len(clip_output_name(0, "x" * 300)) < 80


class TestRunRender:
    """Orchestration: renders.json must always reflect what actually finished.

    ``_run_ffmpeg`` is stubbed rather than shelling out to a real ffmpeg — these
    tests are about the loop's bookkeeping, not about encoding, which
    build_render_command / filtergraph_ops already cover.
    """

    def _plan(self, index, start):
        return CameraPlan(
            clip_index=index, start=start, end=start + 10.0, source_width=1920,
            source_height=1080, fps=30.0,
            keyframes=[
                CameraKeyframe(time=start, x=0.0, y=0.0, width=607.0, height=1080.0)
            ],
        )

    def _workspace_and_source(self, tmp_path):
        work = str(tmp_path / "run")
        workspace_init(work)
        source = tmp_path / "v.mp4"
        source.write_bytes(b"\x00")
        return work, SourceMedia(path=str(source), duration=60.0)

    def test_writes_the_renders_artifact_on_full_success(self, tmp_path, monkeypatch):
        def fake_run_ffmpeg(argv, *, timeout):
            with open(argv[-1], "wb") as fh:
                fh.write(b"\x00" * 100)

        monkeypatch.setattr(phase6, "_run_ffmpeg", fake_run_ffmpeg)
        monkeypatch.setattr(phase6, "require_binary", lambda name: f"/usr/bin/{name}")
        work, media = self._workspace_and_source(tmp_path)

        outputs = phase6.run_render(
            [self._plan(0, 0.0), self._plan(1, 20.0)], media, work,
            settings=Settings(),
        )
        assert len(outputs) == 2
        with open(f"{work}/analysis/renders.json") as fh:
            assert len(json.load(fh)["clips"]) == 2

    def test_mid_loop_failure_still_persists_what_finished(self, tmp_path, monkeypatch):
        # Shape of a real failure hit in the wild: ffmpeg succeeds on clip 1,
        # then fails on clip 2 (there, ffmpeg's faststart rewrite hit a WSL/
        # DrvFs file-lock: "Unable to re-open output file for shifting data").
        # Before this fix, renders.json was written only once, after the whole
        # loop succeeded — a crash here left a PRIOR, unrelated run's manifest
        # untouched, and phase 7 went on to report that stale clip count as if
        # it belonged to this run.
        calls = {"n": 0}

        def flaky_run_ffmpeg(argv, *, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                with open(argv[-1], "wb") as fh:
                    fh.write(b"\x00" * 100)
                return
            raise ToolFailureError("ffmpeg exited with 254: Unable to re-open output file")

        monkeypatch.setattr(phase6, "_run_ffmpeg", flaky_run_ffmpeg)
        monkeypatch.setattr(phase6, "require_binary", lambda name: f"/usr/bin/{name}")
        work, media = self._workspace_and_source(tmp_path)

        # Seed a stale manifest from an earlier, unrelated 7-clip run.
        os.makedirs(f"{work}/analysis", exist_ok=True)
        with open(f"{work}/analysis/renders.json", "w") as fh:
            json.dump(
                {"crf": 18, "output_size": "1080x1920",
                 "clips": [{"clip_index": i} for i in range(7)]},
                fh,
            )

        with pytest.raises(ToolFailureError, match="re-open output file"):
            phase6.run_render(
                [self._plan(0, 0.0), self._plan(1, 20.0)], media, work,
                settings=Settings(),
            )

        with open(f"{work}/analysis/renders.json") as fh:
            payload = json.load(fh)
        # Reflects the one clip that actually finished — neither the stale 7
        # nor an empty/unwritten file.
        assert len(payload["clips"]) == 1
        assert payload["clips"][0]["clip_index"] == 0

    def test_missing_source_video_is_rejected_before_ffmpeg(self, tmp_path):
        work = str(tmp_path / "run")
        workspace_init(work)
        media = SourceMedia(path=str(tmp_path / "missing.mp4"), duration=60.0)
        with pytest.raises(ToolFailureError, match="source video not found"):
            phase6.run_render([self._plan(0, 0.0)], media, work, settings=Settings())

    def test_no_plans_is_rejected(self, tmp_path):
        work, media = self._workspace_and_source(tmp_path)
        with pytest.raises(ToolFailureError, match="no camera plans"):
            phase6.run_render([], media, work, settings=Settings())
