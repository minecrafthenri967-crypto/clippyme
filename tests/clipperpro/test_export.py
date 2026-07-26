"""Phase 7: report assembly and Markdown rendering."""

import json

import pytest

from clipper_pro import export as phase7
from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError
from clipper_pro.export.report_ops import build_report, format_timecode, render_markdown
from clipper_pro.types import Candidate, RubricScores, SourceMedia


def _candidate(start, end, title, hook=8, **axes):
    return Candidate(
        start=start, end=end, title=title, reason=f"why {title}",
        scores=RubricScores(hook=hook, **axes),
    )


def _render(index, path="/r/clip.mp4", size=1000):
    return {"clip_index": index, "path": path, "bytes": size}


class TestFormatTimecode:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "0:00"), (9, "0:09"), (65, "1:05"), (600, "10:00"), (3661, "1:01:01")],
    )
    def test_formats_for_scrubbing(self, seconds, expected):
        # Nobody locates 1487.2 seconds in a source by eye.
        assert format_timecode(seconds) == expected

    def test_negative_clamps_to_zero(self):
        assert format_timecode(-5) == "0:00"


class TestBuildReport:
    def test_ranks_by_score_not_timeline(self):
        # This is the one artifact whose job is ranking.
        report = build_report(
            [_candidate(0, 20, "weak", hook=1), _candidate(60, 80, "strong", hook=10)],
            [_render(0), _render(1)],
        )
        assert [c["title"] for c in report["clips"]] == ["strong", "weak"]
        assert [c["rank"] for c in report["clips"]] == [1, 2]

    def test_carries_all_five_axes_per_clip(self):
        report = build_report([_candidate(0, 20, "a", hook=9, emotion=6)], [_render(0)])
        axes = report["clips"][0]["axes"]
        assert set(axes) == set(RubricScores.WEIGHTS)
        assert axes["hook"] == 9 and axes["emotion"] == 6

    def test_carries_the_reason(self):
        report = build_report([_candidate(0, 20, "a")], [_render(0)])
        assert report["clips"][0]["reason"] == "why a"

    def test_includes_the_source_timecode(self):
        report = build_report([_candidate(125, 150, "a")], [_render(0)])
        assert report["clips"][0]["timecode"] == "2:05"

    def test_reports_what_snapping_moved(self):
        candidate = Candidate(10.5, 40.2, title="a", snapped_from=(10.0, 41.0))
        report = build_report([candidate], [_render(0)])
        assert report["clips"][0]["snapped_from"] == [10.0, 41.0]

    def test_links_each_clip_to_its_rendered_file(self):
        report = build_report([_candidate(0, 20, "a")], [_render(0, "/r/clip_01.mp4", 4242)])
        clip = report["clips"][0]
        assert clip["file"] == "/r/clip_01.mp4" and clip["bytes"] == 4242
        assert clip["rendered"] is True

    def test_unrendered_candidate_is_reported_not_dropped(self):
        # A missing render is exactly what the reader needs to know about.
        report = build_report([_candidate(0, 20, "a")], [])
        assert report["clips"][0]["rendered"] is False
        assert report["clips"][0]["file"] is None

    def test_records_the_weights_used(self):
        report = build_report([_candidate(0, 20, "a")], [_render(0)])
        assert report["weights"]["hook"] == pytest.approx(0.30)

    def test_states_the_intermediate_profile_explicitly(self):
        # The most likely thing to be misread about this output.
        report = build_report([_candidate(0, 20, "a")], [_render(0)], crf=18)
        assert "intermediate" in report["export"]["profile"].lower()
        assert report["export"]["crf"] == 18

    def test_untitled_clip_gets_a_fallback_label(self):
        report = build_report([Candidate(0, 20)], [_render(0)])
        assert report["clips"][0]["title"] == "Clip 1"

    def test_ties_break_by_start_time(self):
        report = build_report(
            [_candidate(60, 80, "later", hook=5), _candidate(0, 20, "earlier", hook=5)],
            [_render(0), _render(1)],
        )
        assert [c["title"] for c in report["clips"]] == ["earlier", "later"]

    def test_malformed_render_records_are_ignored(self):
        report = build_report([_candidate(0, 20, "a")], [None, "junk", _render(0)])
        assert report["clips"][0]["rendered"] is True

    def test_carries_the_source_and_ranker(self):
        report = build_report(
            [_candidate(0, 20, "a")], [_render(0)],
            source_title="My Talk", source_path="/v/t.mp4", ranker="deepseek",
        )
        assert report["source"] == {"title": "My Talk", "path": "/v/t.mp4"}
        assert report["ranker"] == "deepseek"


class TestRenderMarkdown:
    def _report(self):
        return build_report(
            [
                _candidate(0, 20, "The pivot", hook=9, emotion=7),
                Candidate(60, 85, title="The reversal", reason="pays off the claim",
                          scores=RubricScores(hook=6), snapped_from=(59.5, 86.0)),
            ],
            [_render(0, "/r/clip_01.mp4"), _render(1, "/r/clip_02.mp4")],
            source_title="My Talk", ranker="deepseek", crf=18, output_size="1080x1920",
        )

    def test_has_a_title_and_a_summary_line(self):
        md = render_markdown(self._report())
        assert md.startswith("# Clip draft — My Talk")
        assert "2 clips (2 rendered)" in md
        assert "deepseek" in md and "CRF 18" in md

    def test_renders_a_comparison_table(self):
        # The decision is comparative; a column of hook scores ranks eleven clips
        # far faster than eleven paragraphs.
        md = render_markdown(self._report())
        assert "| # | At | Len | Score |" in md
        assert md.count("|---") >= 1

    def test_table_has_one_row_per_clip(self):
        md = render_markdown(self._report())
        rows = [line for line in md.splitlines() if line.startswith("| ") and "**" in line]
        assert len(rows) == 2

    def test_explains_each_moment(self):
        md = render_markdown(self._report())
        assert "## Why these moments" in md
        assert "pays off the claim" in md

    def test_notes_the_snapping_movement(self):
        md = render_markdown(self._report())
        assert "Edges snapped from 59.50–86.00s" in md

    def test_points_at_the_rendered_file(self):
        assert "`/r/clip_01.mp4`" in render_markdown(self._report())

    def test_flags_an_unrendered_clip(self):
        md = render_markdown(build_report([_candidate(0, 20, "a")], []))
        assert "**not rendered**" in md

    def test_states_the_weights(self):
        md = render_markdown(self._report())
        assert "Score weights: hook 30%" in md

    def test_carries_the_intermediate_warning(self):
        assert "intermediate" in render_markdown(self._report()).lower()

    def test_empty_report_does_not_crash(self):
        md = render_markdown({"source": {}, "export": {}, "clips": [], "weights": {}})
        assert "Clip draft" in md


class TestRunExport:
    @pytest.fixture
    def workspace(self, tmp_path):
        from clipper_pro.workspace import init

        root = tmp_path / "run"
        init(str(root))
        return str(root)

    def test_writes_both_forms(self, workspace):
        path = phase7.run_export(
            [_candidate(0, 20, "a")], [_render(0)],
            SourceMedia(path="/v.mp4", duration=60.0, title="T"), workspace,
            settings=Settings(), ranker="deepseek",
        )
        assert path.endswith("draft.json")
        with open(path) as fh:
            assert json.load(fh)["clips"][0]["title"] == "a"
        with open(f"{workspace}/reports/draft.md") as fh:
            assert "Clip draft" in fh.read()

    def test_both_forms_agree_on_the_score(self, workspace):
        # Generated from one assembled report so they cannot diverge.
        phase7.run_export(
            [_candidate(0, 20, "a", hook=9)], [_render(0)],
            SourceMedia(path="/v.mp4", duration=60.0), workspace, settings=Settings(),
        )
        with open(f"{workspace}/reports/draft.json") as fh:
            score = json.load(fh)["clips"][0]["score"]
        with open(f"{workspace}/reports/draft.md") as fh:
            assert f"**{score:.2f}**" in fh.read()

    def test_warns_about_unrendered_clips(self, workspace, capsys):
        phase7.run_export(
            [_candidate(0, 20, "a")], [],
            SourceMedia(path="/v.mp4", duration=60.0), workspace, settings=Settings(),
        )
        assert "no rendered file" in capsys.readouterr().err

    def test_reports_the_top_clip(self, workspace, capsys):
        phase7.run_export(
            [_candidate(0, 20, "weak", hook=1), _candidate(60, 85, "strong", hook=10)],
            [_render(0), _render(1)],
            SourceMedia(path="/v.mp4", duration=120.0), workspace, settings=Settings(),
        )
        assert "'strong'" in capsys.readouterr().err

    def test_nothing_to_report_is_an_error(self, workspace):
        with pytest.raises(ToolFailureError, match="nothing to report"):
            phase7.run_export(
                [], [], SourceMedia(path="/v.mp4", duration=60.0), workspace,
                settings=Settings(),
            )

    def test_leaves_no_temp_files(self, workspace):
        import os

        phase7.run_export(
            [_candidate(0, 20, "a")], [_render(0)],
            SourceMedia(path="/v.mp4", duration=60.0), workspace, settings=Settings(),
        )
        assert not any(
            f.endswith(".tmp") for f in os.listdir(os.path.join(workspace, "reports"))
        )
