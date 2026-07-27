"""Phase 6's text hook: the ASS document, the writer, and the render wiring.

The three properties the feature exists for each get a test that would fail if
someone quietly regressed it: the hook spans the whole clip, it is never centred,
and the border is selectable.
"""

from __future__ import annotations

import os

import pytest

from clipper_pro.config import HOOK_POSITIONS as CONFIG_HOOK_POSITIONS
from clipper_pro.config import HOOK_STYLES as CONFIG_HOOK_STYLES
from clipper_pro.errors import ValidationError
from clipper_pro.render.hooks import write_clip_hook
from clipper_pro.render.hooks_ops import (
    HOOK_MAX_WORDS,
    HOOK_POSITIONS,
    HOOK_STYLES,
    HookSettings,
    build_hook_ass,
    hook_line_count,
    normalise_hook_text,
    wrap_hook_text,
)


def _settings(**kwargs) -> HookSettings:
    return HookSettings(**{"enabled": True, **kwargs})


# --- settings validation ----------------------------------------------------


def test_config_mirrors_the_render_tuples():
    """config duplicates these so it stays importable without the render stack."""
    assert CONFIG_HOOK_STYLES == HOOK_STYLES
    assert CONFIG_HOOK_POSITIONS == HOOK_POSITIONS


def test_there_is_no_centre_position():
    """A centred overlay lands on the speaker's face after the 9:16 crop."""
    assert "center" not in HOOK_POSITIONS
    assert "centre" not in HOOK_POSITIONS
    assert "middle" not in HOOK_POSITIONS
    with pytest.raises(ValidationError, match="cover the speaker"):
        _settings(position="center")


@pytest.mark.parametrize("style", HOOK_STYLES)
def test_every_declared_style_builds(style):
    assert build_hook_ass("three word hook", settings=_settings(style=style),
                          start=0.0, end=10.0)


@pytest.mark.parametrize("position", HOOK_POSITIONS)
def test_every_declared_position_builds(position):
    assert build_hook_ass("three word hook", settings=_settings(position=position),
                          start=0.0, end=10.0)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"style": "neon"}, "unknown hook style"),
        ({"position": "left"}, "unknown hook position"),
        ({"font": "Bad,Font"}, "invalid hook font"),
        ({"font_size": 5}, "font_size must be within"),
        ({"font_size": 900}, "font_size must be within"),
        ({"max_words": 1}, "max_words must be within"),
        ({"max_chars_per_line": 2}, "max_chars_per_line must be within"),
    ],
)
def test_bad_settings_are_rejected(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        _settings(**kwargs)


def test_font_name_with_a_comma_cannot_reach_the_style_line():
    """A comma would be a field separator in the ASS style row, not text."""
    with pytest.raises(ValidationError):
        _settings(font="Montserrat,ExtraBold")


# --- text normalisation -----------------------------------------------------


def test_word_count_is_capped_not_rejected():
    text = "one two three four five six seven eight nine ten eleven"
    assert normalise_hook_text(text) == "one two three four five six seven eight"
    assert len(normalise_hook_text(text).split()) == HOOK_MAX_WORDS


def test_whitespace_collapses():
    assert normalise_hook_text("  a \n b\t\tc ") == "a b c"


def test_ass_override_braces_are_stripped():
    """Braces open an override block; text carrying them could inject directives."""
    hooked = normalise_hook_text(r"real {\pos(0,0)} words")
    assert "{" not in hooked and "}" not in hooked and "\\" not in hooked
    assert "real" in hooked and "words" in hooked


def test_empty_text_yields_empty_string():
    assert normalise_hook_text("") == ""
    assert normalise_hook_text("   ") == ""
    assert normalise_hook_text(None) == ""


# --- wrapping ---------------------------------------------------------------


def test_wrapping_respects_the_line_limit():
    lines = wrap_hook_text("this is a hook that runs long", max_chars_per_line=12)
    assert all(len(line) <= 12 for line in lines)
    assert " ".join(lines) == "this is a hook that runs long"


def test_an_overlong_word_gets_its_own_line_rather_than_being_broken():
    lines = wrap_hook_text("hi Donaudampfschifffahrt now", max_chars_per_line=10)
    assert "Donaudampfschifffahrt" in lines


def test_hook_line_count_matches_the_wrap():
    settings = _settings(max_chars_per_line=12)
    assert hook_line_count("this is a hook that runs long", settings) == 3
    assert hook_line_count("", settings) == 0


# --- the document -----------------------------------------------------------


def _event_line(document: str) -> str:
    return next(ln for ln in document.splitlines() if ln.startswith("Dialogue:"))


def test_the_event_spans_the_whole_clip():
    """The point of the feature: a looping viewer always sees the hook."""
    document = build_hook_ass(
        "nobody warned me", settings=_settings(), start=12.0, end=47.5
    )
    event = _event_line(document)
    _, start, end, *_ = event.split(",")
    assert start.removeprefix("Dialogue: 1").lstrip() == "0:00:12.00"
    assert end == "0:00:47.50"


def test_times_are_source_relative_not_clip_relative():
    """`-ss` follows `-i`, so the filter graph still sees source timestamps."""
    document = build_hook_ass("a b c", settings=_settings(), start=300.0, end=320.0)
    assert "0:05:00.00,0:05:20.00" in _event_line(document)


def test_only_one_event_is_emitted():
    document = build_hook_ass("a b c", settings=_settings(), start=0.0, end=30.0)
    assert sum(ln.startswith("Dialogue:") for ln in document.splitlines()) == 1


def test_boxed_light_is_dark_text_on_a_white_slab():
    document = build_hook_ass("a b c", settings=_settings(style="boxed_light"),
                              start=0.0, end=5.0)
    style = next(ln for ln in document.splitlines() if ln.startswith("Style:"))
    fields = style.split(",")
    # BorderStyle=3 is the opaque-box mode; the outline colour paints the slab.
    assert fields[15] == "3"
    assert "&H00FFFFFF" in style  # white slab
    assert "&H001E1E1E" in style  # near-black text


def test_outline_style_keeps_the_footage_visible():
    document = build_hook_ass("a b c", settings=_settings(style="outline"),
                              start=0.0, end=5.0)
    fields = next(ln for ln in document.splitlines() if ln.startswith("Style:")).split(",")
    assert fields[15] == "1"      # BorderStyle 1 = stroke, no slab
    assert int(fields[16]) > 0    # a stroke thick enough to read against video


def test_shadow_style_has_no_stroke():
    document = build_hook_ass("a b c", settings=_settings(style="shadow"),
                              start=0.0, end=5.0)
    fields = next(ln for ln in document.splitlines() if ln.startswith("Style:")).split(",")
    assert int(fields[16]) == 0   # outline
    assert int(fields[17]) > 0    # shadow


def test_upper_positions_align_to_the_top_edge():
    for position in ("top", "upper_third"):
        document = build_hook_ass("a b c", settings=_settings(position=position),
                                  start=0.0, end=5.0)
        fields = next(ln for ln in document.splitlines()
                      if ln.startswith("Style:")).split(",")
        assert fields[18] == "8"  # Alignment: top-centre


def test_lower_positions_align_to_the_bottom_edge():
    for position in ("lower_third", "bottom"):
        document = build_hook_ass("a b c", settings=_settings(position=position),
                                  start=0.0, end=5.0)
        fields = next(ln for ln in document.splitlines()
                      if ln.startswith("Style:")).split(",")
        assert fields[18] == "2"  # Alignment: bottom-centre


def test_a_third_position_sits_further_from_its_edge_than_the_flush_one():
    def margin(position: str) -> int:
        document = build_hook_ass("a b c", settings=_settings(position=position),
                                  start=0.0, end=5.0)
        fields = next(ln for ln in document.splitlines()
                      if ln.startswith("Style:")).split(",")
        return int(fields[21])  # MarginV

    assert margin("upper_third") > margin("top")
    assert margin("lower_third") > margin("bottom")


def test_the_hook_never_reaches_the_vertical_middle():
    """Whatever the position, the text stays out of the centre band."""
    height = 1920
    for position in HOOK_POSITIONS:
        document = build_hook_ass(
            "one two three four five six seven eight",
            settings=_settings(position=position), start=0.0, end=5.0,
        )
        fields = next(ln for ln in document.splitlines()
                      if ln.startswith("Style:")).split(",")
        alignment, margin_v, font_size = int(fields[18]), int(fields[21]), int(fields[2])
        # Two lines at this width; measure the block's inner edge from its anchor.
        block = font_size * 2.4
        inner = margin_v + block if alignment == 8 else height - margin_v - block
        assert inner < height * 0.42 or inner > height * 0.58, position


def test_play_resolution_follows_the_delivery_frame():
    document = build_hook_ass("a b c", settings=_settings(), start=0.0, end=5.0,
                              play_res_x=720, play_res_y=1280)
    assert "PlayResX: 720" in document
    assert "PlayResY: 1280" in document


def test_long_text_is_broken_with_ass_line_breaks():
    document = build_hook_ass(
        "this hook is deliberately far too long for one line",
        settings=_settings(max_chars_per_line=14), start=0.0, end=5.0,
    )
    assert r"\N" in _event_line(document)


def test_wrapping_is_left_to_us_not_libass():
    document = build_hook_ass("a b c", settings=_settings(), start=0.0, end=5.0)
    assert "WrapStyle: 2" in document


def test_uppercase_is_opt_in():
    lower = build_hook_ass("keep my case", settings=_settings(), start=0.0, end=5.0)
    upper = build_hook_ass("keep my case", settings=_settings(uppercase=True),
                           start=0.0, end=5.0)
    assert "keep my case" in _event_line(lower)
    assert "KEEP MY CASE" in _event_line(upper)


def test_empty_text_produces_no_document():
    assert build_hook_ass("", settings=_settings(), start=0.0, end=5.0) == ""


def test_an_inverted_range_produces_no_document():
    assert build_hook_ass("a b c", settings=_settings(), start=9.0, end=9.0) == ""


def test_hook_draws_above_captions():
    """Layer 1 beats the caption document's layer 0 where the two overlap."""
    assert _event_line(
        build_hook_ass("a b c", settings=_settings(), start=0.0, end=5.0)
    ).startswith("Dialogue: 1,")


# --- the writer -------------------------------------------------------------


def test_writer_creates_the_file(tmp_path):
    path = write_clip_hook("stop scrolling right now", str(tmp_path), 0,
                           start=0.0, end=20.0, settings=_settings())
    assert path == os.path.join(str(tmp_path), "hooks", "clip_01.ass")
    # Wrapped with the ASS line break, so match the words not the whole phrase.
    assert "stop scrolling right" in open(path, encoding="utf-8").read()


def test_writer_names_files_by_clip_index(tmp_path):
    path = write_clip_hook("a b c", str(tmp_path), 11,
                           start=0.0, end=5.0, settings=_settings())
    assert os.path.basename(path) == "clip_12.ass"


def test_writer_skips_when_hooks_are_disabled(tmp_path):
    assert write_clip_hook("a b c", str(tmp_path), 0, start=0.0, end=5.0,
                           settings=HookSettings(enabled=False)) is None
    assert not os.path.isdir(os.path.join(str(tmp_path), "hooks"))


def test_writer_returns_none_for_a_clip_with_no_hook_text(tmp_path, capsys):
    """The ranker returning no hook is ordinary — render the clip anyway."""
    assert write_clip_hook("", str(tmp_path), 0, start=0.0, end=5.0,
                           settings=_settings()) is None
    assert "no hook text" in capsys.readouterr().err


# --- filtergraph and render wiring ------------------------------------------


def _keyframes():
    from clipper_pro.types import CameraKeyframe

    return [
        CameraKeyframe(time=t / 30.0, x=100.0, y=0.0, width=607.0, height=1080.0)
        for t in range(30)
    ]


def test_the_hook_filter_is_appended_after_the_scale():
    from clipper_pro.render.filtergraph_ops import build_filtergraph

    graph, _ = build_filtergraph(_keyframes(), start=0.0, hook_filter="ass=f=/w/h.ass")
    assert graph.endswith(",ass=f=/w/h.ass")
    assert graph.index("scale=") < graph.index("ass=f=/w/h.ass")


def test_the_hook_is_drawn_after_the_captions():
    """Where they collide, the element that must stay legible wins."""
    from clipper_pro.render.filtergraph_ops import build_filtergraph

    graph, _ = build_filtergraph(
        _keyframes(), start=0.0,
        captions_filter="ass=f=/w/c.ass", hook_filter="ass=f=/w/h.ass",
    )
    assert graph.index("/w/c.ass") < graph.index("/w/h.ass")


def test_no_hook_leaves_the_graph_unchanged():
    from clipper_pro.render.filtergraph_ops import build_filtergraph

    plain, _ = build_filtergraph(_keyframes(), start=0.0)
    with_none, _ = build_filtergraph(_keyframes(), start=0.0, hook_filter=None)
    assert plain == with_none


def test_render_burns_the_candidate_hook_in_the_same_pass(tmp_path, monkeypatch):
    """One ffmpeg invocation carries the hook — no second encode."""
    from clipper_pro import render as phase6
    from clipper_pro.config import Settings
    from clipper_pro.reframe.plan import CameraPlan
    from clipper_pro.types import Candidate, SourceMedia
    from clipper_pro.workspace import init as workspace_init

    work = str(tmp_path / "run")
    workspace_init(work)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not really a video")

    calls: list[list[str]] = []

    def fake_ffmpeg(argv, *, timeout):
        calls.append(argv)
        # Stand in for the encode so the wiring is what is under test.
        open(argv[-1], "wb").write(b"x")

    monkeypatch.setattr(phase6, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(phase6, "require_binary", lambda name: f"/usr/bin/{name}")

    plan = CameraPlan(
        clip_index=0, start=0.0, end=1.0,
        source_width=1920, source_height=1080, fps=30.0,
        keyframes=_keyframes(),
    )
    phase6.run_render(
        [plan],
        SourceMedia(path=str(source), duration=60.0),
        work,
        settings=Settings(),
        candidates=[Candidate(start=0.0, end=1.0, hook_text="nobody warned me")],
        hooks=_settings(),
    )

    assert len(calls) == 1, "the hook must not cost a second pass"
    graph = calls[0][calls[0].index("-filter:v") + 1]
    assert "hooks/clip_01.ass" in graph
    document = open(os.path.join(work, "hooks", "clip_01.ass"), encoding="utf-8").read()
    assert "nobody warned me" in document

    with open(os.path.join(work, "analysis", "renders.json"), encoding="utf-8") as fh:
        import json

        assert json.load(fh)["clips"][0]["hook"] == "nobody warned me"


def test_render_skips_the_hook_when_the_ranker_returned_none(tmp_path, monkeypatch):
    from clipper_pro import render as phase6
    from clipper_pro.config import Settings
    from clipper_pro.reframe.plan import CameraPlan
    from clipper_pro.types import Candidate, SourceMedia
    from clipper_pro.workspace import init as workspace_init

    work = str(tmp_path / "run")
    workspace_init(work)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not really a video")

    calls: list[list[str]] = []
    monkeypatch.setattr(
        phase6, "_run_ffmpeg",
        lambda argv, *, timeout: (calls.append(argv), open(argv[-1], "wb").write(b"x")),
    )
    monkeypatch.setattr(phase6, "require_binary", lambda name: f"/usr/bin/{name}")

    plan = CameraPlan(
        clip_index=0, start=0.0, end=1.0,
        source_width=1920, source_height=1080, fps=30.0,
        keyframes=_keyframes(),
    )
    phase6.run_render(
        [plan], SourceMedia(path=str(source), duration=60.0), work,
        settings=Settings(),
        candidates=[Candidate(start=0.0, end=1.0, hook_text="")],
        hooks=_settings(),
    )
    assert "ass=" not in calls[0][calls[0].index("-filter:v") + 1]


# --- configuration and front ends -------------------------------------------


def test_hooks_default_off_in_configuration():
    from clipper_pro.config import Settings

    assert Settings().hooks is False
    assert Settings().hook_position == "upper_third"
    assert Settings().hook_style == "boxed_light"


def test_env_enables_and_styles_the_hook(monkeypatch):
    from clipper_pro.config import Settings

    monkeypatch.setenv("CLIPPER_PRO_HOOKS", "1")
    monkeypatch.setenv("CLIPPER_PRO_HOOK_STYLE", "outline")
    monkeypatch.setenv("CLIPPER_PRO_HOOK_POSITION", "lower_third")
    monkeypatch.setenv("CLIPPER_PRO_HOOK_FONT_SIZE", "120")
    settings = Settings.from_env()
    assert settings.hooks is True
    assert settings.hook_style == "outline"
    assert settings.hook_position == "lower_third"
    assert settings.hook_font_size == 120


def test_an_unknown_style_in_the_env_falls_back(monkeypatch):
    from clipper_pro.config import Settings

    monkeypatch.setenv("CLIPPER_PRO_HOOK_STYLE", "rainbow")
    assert Settings.from_env().hook_style == "boxed_light"


def test_cli_flags_reach_the_phase_options():
    from clipper_pro.cli import build_parser
    from clipper_pro.cli import _options_from_args

    args = build_parser().parse_args([
        "render", "--work-dir", "/w", "--hooks",
        "--hook-style", "outline", "--hook-position", "bottom",
        "--hook-font-size", "90",
    ])
    options = _options_from_args(args)
    assert options.hooks is True
    assert options.hook_style == "outline"
    assert options.hook_position == "bottom"
    assert options.hook_font_size == 90


def test_no_hooks_flag_overrides_configuration():
    from clipper_pro.cli import build_parser

    args = build_parser().parse_args(["render", "--work-dir", "/w", "--no-hooks"])
    assert args.hooks is False


def test_cli_rejects_a_centre_hook_position():
    from clipper_pro.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["render", "--work-dir", "/w", "--hook-position", "center"]
        )


def test_web_start_body_carries_the_hook_settings():
    from clipper_pro.web.app import StartRunRequest

    options = StartRunRequest(
        source="https://example.com/v", hooks=True,
        hook_style="outline", hook_position="bottom", hook_font_size=90,
    ).to_options()
    assert options.hooks is True
    assert options.hook_style == "outline"
    assert options.hook_position == "bottom"
    assert options.hook_font_size == 90


def test_web_rejects_an_unknown_hook_style():
    import pydantic

    from clipper_pro.web.app import StartRunRequest

    with pytest.raises(pydantic.ValidationError):
        StartRunRequest(source="https://example.com/v", hook_style="rainbow")


def test_web_rejects_a_centre_hook_position():
    import pydantic

    from clipper_pro.web.app import StartRunRequest

    with pytest.raises(pydantic.ValidationError):
        StartRunRequest(source="https://example.com/v", hook_position="center")


def test_web_health_offers_the_hook_choices(tmp_path):
    from fastapi.testclient import TestClient

    from clipper_pro.web.app import create_app

    body = TestClient(create_app(str(tmp_path / "runs"))).get("/api/health").json()
    assert body["hook_styles"] == list(HOOK_STYLES)
    assert body["hook_positions"] == list(HOOK_POSITIONS)
