"""Burned-in karaoke captions: settings, escaping, filter placement, generation.

Captions add no paid dependency — the word timings are phase 2's, already
billed, and the burn-in rides the ffmpeg pass phase 6 was going to run anyway.
The ASS document itself comes from ``clippyme.domain.subtitles``, which has its
own tests; what is covered here is the join.
"""

import os

import pytest

from clipper_pro.config import CAPTION_POSITIONS, CAPTION_PRESETS, Settings
from clipper_pro.errors import ValidationError
from clipper_pro.render.captions import write_clip_captions
from clipper_pro.render.captions_ops import (
    CaptionSettings,
    build_ass_filter,
    escape_filter_value,
    preset_base_font_size,
    resolve_font_size,
    scaled_font_size,
    words_to_transcript,
)
from clipper_pro.render.filtergraph_ops import build_filtergraph
from clipper_pro.types import CameraKeyframe, Word


def _words(text="So I quit my job. Everyone said I was insane.", start=0.0, step=0.4):
    return [
        Word(tok, start + i * step, start + i * step + step * 0.8)
        for i, tok in enumerate(text.split())
    ]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _kfs(n=3):
    return [
        CameraKeyframe(time=i / 30, x=100.0, y=0.0, width=607.0, height=1080.0)
        for i in range(n)
    ]


class TestCaptionSettings:
    def test_defaults_are_off_with_a_short_form_style(self):
        s = CaptionSettings()
        assert s.enabled is False
        assert s.preset == "hormozi_bold" and s.words_per_group == 3

    def test_rejects_an_unknown_preset_with_the_options(self):
        with pytest.raises(ValidationError, match="unknown caption preset"):
            CaptionSettings(preset="sparkles")

    def test_rejects_an_unknown_position(self):
        with pytest.raises(ValidationError, match="unknown caption position"):
            CaptionSettings(position="middle-left")

    @pytest.mark.parametrize("count", [0, -1, 13, 99])
    def test_rejects_an_unusable_group_size(self, count):
        with pytest.raises(ValidationError, match="words_per_group"):
            CaptionSettings(words_per_group=count)

    @pytest.mark.parametrize("size", [5, 500])
    def test_rejects_an_absurd_font_size(self, size):
        with pytest.raises(ValidationError, match="font_size"):
            CaptionSettings(font_size=size)

    def test_is_immutable(self):
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            CaptionSettings().enabled = True

    def test_config_and_render_preset_lists_stay_in_step(self):
        # config duplicates them to avoid importing the render stack; this is
        # the guard that keeps the two copies honest.
        assert tuple(CAPTION_PRESETS) == tuple(
            __import__(
                "clipper_pro.render.captions_ops", fromlist=["CAPTION_PRESETS"]
            ).CAPTION_PRESETS
        )
        assert tuple(CAPTION_POSITIONS) == tuple(
            __import__(
                "clipper_pro.render.captions_ops", fromlist=["CAPTION_POSITIONS"]
            ).CAPTION_POSITIONS
        )

    def test_every_preset_is_constructible(self):
        for preset in CAPTION_PRESETS:
            assert CaptionSettings(preset=preset).preset == preset


class TestFontSizing:
    """The presets ship at ~2% of frame height — a subtitle, not a short-form
    caption. Scaling lifts them to ~5% while keeping their relative design."""

    def test_scaling_lifts_a_preset_into_the_short_form_range(self):
        size = resolve_font_size(CaptionSettings(preset="hormozi_bold"))
        # 1920-tall frame: 5-6% is the scroll-stopping range.
        assert 0.045 < size / 1920 < 0.07

    def test_every_preset_lands_in_a_readable_range(self):
        for preset in CAPTION_PRESETS:
            size = resolve_font_size(CaptionSettings(preset=preset))
            assert 0.035 < size / 1920 < 0.08, f"{preset} → {size}"

    def test_relative_proportions_survive(self):
        # minimal_clean is designed smaller than fire_impact; flattening every
        # preset onto one number would erase six deliberate designs.
        assert (
            resolve_font_size(CaptionSettings(preset="minimal_clean"))
            < resolve_font_size(CaptionSettings(preset="fire_impact"))
        )

    def test_an_explicit_size_overrides_the_scale(self):
        assert resolve_font_size(CaptionSettings(font_size=60, scale=4.0)) == 60

    def test_scale_is_applied(self):
        base = preset_base_font_size("hormozi_bold")
        assert resolve_font_size(CaptionSettings(scale=2.0)) == base * 2

    @pytest.mark.parametrize("scale", [0.0, 0.1, 9.0, -1.0])
    def test_an_absurd_scale_is_rejected(self, scale):
        with pytest.raises(ValidationError, match="scale"):
            CaptionSettings(scale=scale)

    def test_scaling_is_clamped_to_something_renderable(self):
        assert scaled_font_size(43, 8.0) <= 400
        assert scaled_font_size(1, 0.2) >= 10

    def test_an_unknown_preset_base_yields_no_override(self, monkeypatch):
        # Falling back to the generator's own default beats guessing.
        monkeypatch.setattr(
            "clipper_pro.render.captions_ops.preset_base_font_size", lambda p: None
        )
        assert resolve_font_size(CaptionSettings()) is None


class TestWordsToTranscript:
    def test_produces_the_shape_the_generator_reads(self):
        payload = words_to_transcript([Word("hi", 1.0, 1.4)])
        assert payload["segments"][0]["words"] == [
            {"word": "hi", "start": 1.0, "end": 1.4}
        ]

    def test_keeps_everything_in_one_segment(self):
        # The karaoke grouper re-splits on its own semantic boundaries;
        # pre-segmenting would impose breaks it did not choose.
        words = _words()
        payload = words_to_transcript(words)
        assert len(payload["segments"]) == 1
        assert len(payload["segments"][0]["words"]) == len(words)

    def test_empty_input(self):
        assert words_to_transcript([])["segments"][0]["words"] == []


class TestEscaping:
    def test_a_plain_path_is_unchanged(self):
        assert escape_filter_value("/home/u/runs/a.ass") == "/home/u/runs/a.ass"

    def test_a_colon_is_escaped(self):
        # A colon separates filter options; unescaped it would split the path
        # into nonsense options rather than failing loudly.
        assert escape_filter_value("C:/x.ass") == "C\\:/x.ass"

    def test_a_backslash_is_escaped_first(self):
        # Escaping backslash after the others would double their escapes.
        assert escape_filter_value("a\\b:c") == "a\\\\b\\:c"

    def test_a_quote_is_escaped(self):
        assert escape_filter_value("it's.ass") == "it\\'s.ass"

    def test_a_windows_path_survives_intact(self):
        out = escape_filter_value(r"C:\Users\Henri\a.ass")
        assert out == r"C\:\\Users\\Henri\\a.ass"


class TestAssFilter:
    def test_builds_the_filter(self):
        assert build_ass_filter("/w/c.ass") == "ass=f=/w/c.ass"

    def test_includes_the_fonts_dir_when_given(self):
        # Without it libass silently falls back to a system sans, losing the
        # bundled display faces the presets name.
        assert build_ass_filter("/w/c.ass", "/fonts") == "ass=f=/w/c.ass:fontsdir=/fonts"

    def test_escapes_both_paths(self):
        out = build_ass_filter("/w/a:b.ass", "/f:g")
        assert "a\\:b.ass" in out and "f\\:g" in out

    def test_no_path_is_rejected(self):
        with pytest.raises(ValidationError, match="without a subtitle file"):
            build_ass_filter("")


class TestFiltergraphPlacement:
    def test_captions_come_last_after_the_scale(self):
        # The ASS document is authored at 1080x1920; burning before the scale
        # would position text in crop coordinates and then resample it.
        graph, _ = build_filtergraph(
            _kfs(), start=0.0, captions_filter="ass=f=/w/c.ass"
        )
        assert graph.index("scale=") < graph.index("ass=")
        assert graph.index("setsar=1") < graph.index("ass=")
        assert graph.endswith("ass=f=/w/c.ass")

    def test_no_captions_leaves_the_graph_unchanged(self):
        plain, _ = build_filtergraph(_kfs(), start=0.0)
        assert "ass=" not in plain
        assert plain.endswith("setsar=1")

    def test_the_crop_expression_is_untouched_by_captions(self):
        plain, _ = build_filtergraph(_kfs(), start=0.0)
        with_caps, _ = build_filtergraph(_kfs(), start=0.0, captions_filter="ass=f=/c.ass")
        assert with_caps.startswith(plain)


class TestWriteClipCaptions:
    def test_writes_an_ass_file_with_karaoke_timing(self, tmp_path):
        path = write_clip_captions(
            _words(), str(tmp_path), 0,
            start=0.0, end=10.0, settings=CaptionSettings(enabled=True),
        )
        assert path and os.path.isfile(path)
        text = _read(path)
        assert "[Script Info]" in text
        assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
        assert "Dialogue:" in text
        assert "\\k" in text  # karaoke word timing

    def test_named_by_clip_index_so_a_rerun_overwrites(self, tmp_path):
        first = write_clip_captions(
            _words(), str(tmp_path), 2, start=0.0, end=10.0,
            settings=CaptionSettings(enabled=True),
        )
        second = write_clip_captions(
            _words(), str(tmp_path), 2, start=0.0, end=10.0,
            settings=CaptionSettings(enabled=True),
        )
        assert first == second
        assert os.path.basename(first) == "clip_03.ass"

    def test_timings_are_in_source_time_not_clip_relative(self, tmp_path):
        """Regression: captions vanished `start` seconds into every clip.

        Phase 6 seeks with `-ss` *after* `-i`, and with output seeking the
        filter graph still sees each frame's original timestamp — so the `ass`
        filter matches events against source time. A clip-relative document
        therefore ran out exactly `start` seconds in, which looked like a
        styling problem and was really a timebase mismatch.
        """
        path = write_clip_captions(
            _words(start=300.0), str(tmp_path), 0,
            start=300.0, end=310.0, settings=CaptionSettings(enabled=True),
        )
        text = _read(path)
        assert "0:05:00" in text          # the real source time
        assert "0:00:00.00," not in text  # the clip-relative time that broke it

    def test_only_words_inside_the_clip_are_written(self, tmp_path):
        # Source time must not mean "ship the whole video's captions per clip".
        words = _words("one two three", start=0.0) + _words("far later on", start=500.0)
        path = write_clip_captions(
            words, str(tmp_path), 0, start=0.0, end=10.0,
            settings=CaptionSettings(enabled=True),
        )
        text = _read(path)
        assert "ONE" in text
        assert "LATER" not in text

    def test_a_clip_starting_late_still_gets_its_captions(self, tmp_path):
        # The exact shape of the bug: words at 300s, clip at 300s.
        path = write_clip_captions(
            _words("late clip words here", start=300.0), str(tmp_path), 0,
            start=299.0, end=320.0, settings=CaptionSettings(enabled=True),
        )
        events = [ln for ln in _read(path).splitlines() if ln.startswith("Dialogue:")]
        assert events
        # Every event must sit inside the clip's own source window.
        assert all("0:05:0" in ln for ln in events)

    def test_disabled_writes_nothing(self, tmp_path):
        assert write_clip_captions(
            _words(), str(tmp_path), 0, start=0.0, end=10.0,
            settings=CaptionSettings(enabled=False),
        ) is None
        assert not os.path.exists(os.path.join(tmp_path, "captions"))

    def test_no_words_writes_nothing(self, tmp_path):
        assert write_clip_captions(
            [], str(tmp_path), 0, start=0.0, end=10.0,
            settings=CaptionSettings(enabled=True),
        ) is None

    def test_a_range_with_no_words_degrades_instead_of_failing(self, tmp_path, capsys):
        # A clip whose window holds no speech should still render, silently.
        result = write_clip_captions(
            _words(start=0.0), str(tmp_path), 0,
            start=500.0, end=520.0, settings=CaptionSettings(enabled=True),
        )
        assert result is None
        assert "no words in range" in capsys.readouterr().err

    def test_a_generator_failure_degrades_instead_of_failing_the_render(
        self, tmp_path, monkeypatch, capsys
    ):
        # Styling must never cost a finished render.
        import clipper_pro.render.captions as mod

        def boom(*a, **k):
            raise RuntimeError("font exploded")

        monkeypatch.setattr(
            "clippyme.domain.subtitles.generate_ass_karaoke", boom, raising=True
        )
        assert mod.write_clip_captions(
            _words(), str(tmp_path), 0, start=0.0, end=10.0,
            settings=CaptionSettings(enabled=True),
        ) is None
        assert "caption generation failed" in capsys.readouterr().err

    def test_uppercase_is_applied(self, tmp_path):
        path = write_clip_captions(
            _words("hello world there"), str(tmp_path), 0,
            start=0.0, end=10.0,
            settings=CaptionSettings(enabled=True, uppercase=True),
        )
        assert "HELLO" in _read(path)

    def test_ass_braces_in_the_transcript_cannot_inject_directives(self, tmp_path):
        # ASR can echo on-screen text; a token like {\an8} would otherwise be
        # read by libass as a positioning override.
        hostile = [Word("{\\an8}gotcha", 0.0, 0.5), Word("word", 0.6, 1.0)]
        path = write_clip_captions(
            hostile, str(tmp_path), 0, start=0.0, end=5.0,
            settings=CaptionSettings(enabled=True),
        )
        text = _read(path)
        dialogue = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
        assert dialogue
        assert "\\an8" not in " ".join(dialogue)


class TestSettingsIntegration:
    def test_captions_default_off_in_configuration(self):
        assert Settings().captions is False

    def test_env_enables_them(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_PRO_CAPTIONS", "1")
        monkeypatch.setenv("CLIPPER_PRO_CAPTION_PRESET", "neon_glow")
        s = Settings.from_env()
        assert s.captions is True and s.caption_preset == "neon_glow"

    def test_an_unknown_preset_in_the_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_PRO_CAPTION_PRESET", "sparkles")
        assert Settings.from_env().caption_preset == "hormozi_bold"
