"""Argument-parsing contract between job_results.build_main_cmd and
orchestrator._parse_args.

These two live in different files and are maintained independently — a flag
added to one and not the other is invisible to every other test, since unit
tests for each side use their own hand-built argv. It only surfaces at
runtime as an argparse crash (unrecognized flag) or, worse, an AttributeError
deep inside `_render_one_clip` reading an attribute the parser never defined
— which is exactly what happened when --gaming-gameplay-x/y/w/h and
--gaming-split were added to job_results.py and main.py's parser, but not to
orchestrator.py's own separate one. That crash fired for EVERY clip in EVERY
job (not just gaming ones), since _render_one_clip reads those attributes
unconditionally regardless of reframe_mode.
"""
from clippyme.domain.job_results import build_main_cmd
from clippyme.pipeline.orchestrator import _parse_args


def _argv_after_the_module_name(cmd: list[str]) -> list[str]:
    # build_main_cmd's argv is ["python", "-u", "-m", "clippyme.pipeline.orchestrator", ...] —
    # _parse_args only wants what comes after that.
    return cmd[cmd.index("clippyme.pipeline.orchestrator") + 1:]


def test_gameplay_box_and_split_flags_are_parseable_by_the_orchestrator():
    cmd = build_main_cmd(
        url="https://x.test/v", output_dir="/tmp/o", reframe_mode="gaming",
        gaming_facecam_box={"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.28},
        gaming_gameplay_box={"x": 0.3, "y": 0.4, "w": 0.35, "h": 0.5},
        gaming_split=0.4,
    )
    args = _parse_args(_argv_after_the_module_name(cmd))
    assert args.gaming_gameplay_x == 0.3
    assert args.gaming_gameplay_y == 0.4
    assert args.gaming_gameplay_w == 0.35
    assert args.gaming_gameplay_h == 0.5
    assert args.gaming_split == 0.4


def test_gaming_gameplay_box_and_split_attributes_exist_even_when_unset():
    # Every ordinary (non-gaming, or gaming-without-a-drawn-layout) job takes
    # this path. `_render_one_clip` reads these attributes unconditionally, so
    # a plain job must not crash with AttributeError just because gaming
    # fields were never in argv.
    args = _parse_args(["-u", "https://x.test/v", "-o", "/tmp/o"])
    assert args.gaming_gameplay_x is None
    assert args.gaming_split is None


def test_full_argv_round_trip_derives_none_gameplay_box_and_split(monkeypatch):
    # Mirrors run()'s own post-parse derivation (see orchestrator.run), since
    # that derivation — not just the raw parsed fractions — is what
    # _render_one_clip actually consumes.
    args = _parse_args(["-u", "https://x.test/v", "-o", "/tmp/o"])
    gameplay_fracs = (args.gaming_gameplay_x, args.gaming_gameplay_y,
                      args.gaming_gameplay_w, args.gaming_gameplay_h)
    gameplay_box = (
        {"x": gameplay_fracs[0], "y": gameplay_fracs[1], "w": gameplay_fracs[2], "h": gameplay_fracs[3]}
        if all(v is not None for v in gameplay_fracs) else None
    )
    assert gameplay_box is None


def test_facecam_box_flags_still_parseable_alongside_the_new_ones():
    cmd = build_main_cmd(
        url="https://x.test/v", output_dir="/tmp/o", reframe_mode="gaming",
        gaming_facecam_box={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
    )
    args = _parse_args(_argv_after_the_module_name(cmd))
    assert args.gaming_facecam_x == 0.1
    assert args.gaming_facecam_w == 0.3
