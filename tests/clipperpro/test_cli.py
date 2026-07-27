"""CLI surface: argument parsing, option translation, dispatch, exit codes.

The CLI's job is now argv in, :func:`clipper_pro.pipeline.run_phase` out — the
orchestration it used to carry is covered by ``test_pipeline.py``.
"""

import json

import pytest

from clipper_pro import cli
from clipper_pro.errors import ValidationError
from clipper_pro.pipeline import PHASES


class TestParser:
    def test_every_phase_is_listed(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--help"])
        out = capsys.readouterr().out
        for phase in PHASES:
            assert phase in out

    def test_the_web_ui_is_offered(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--help"])
        assert "web" in capsys.readouterr().out

    def test_ingest_requires_a_work_dir(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["ingest", "video.mp4"])

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([])

    def test_transcribe_provider_is_constrained(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                ["transcribe", "--work-dir", "/w", "--provider", "whisper"]
            )

    def test_transcribe_accepts_the_documented_flags(self):
        args = cli.build_parser().parse_args(
            ["transcribe", "--work-dir", "/w", "--provider", "elevenlabs",
             "--require-events", "--no-cache"]
        )
        assert args.provider == "elevenlabs"
        assert args.require_events is True and args.no_cache is True

    def test_web_defaults_to_localhost_only(self):
        # Binding beyond loopback would expose a service that runs ffmpeg and
        # yt-dlp on the user's machine; that must be a deliberate choice.
        args = cli.build_parser().parse_args(["web"])
        assert args.host == "127.0.0.1"


class TestOptionsFromArgs:
    def _opts(self, argv):
        return cli._options_from_args(cli.build_parser().parse_args(argv))

    def test_ingest_flags_are_translated(self):
        opts = self._opts(
            ["ingest", "v.mp4", "--work-dir", "/w", "--sample-rate", "8000", "--overwrite"]
        )
        assert opts.sample_rate == 8000 and opts.overwrite is True

    def test_transcribe_flags_are_translated(self):
        opts = self._opts(
            ["transcribe", "--work-dir", "/w", "--provider", "elevenlabs",
             "--require-events", "--no-cache"]
        )
        assert opts.transcribe_provider == "elevenlabs"
        assert opts.require_events is True and opts.no_transcript_cache is True

    def test_rank_flags_are_translated(self):
        opts = self._opts(
            ["rank", "--work-dir", "/w", "--provider", "gemini",
             "--max-clips", "3", "--instructions", "funny bits", "--no-cache"]
        )
        assert opts.rank_provider == "gemini" and opts.max_clips == 3
        assert opts.instructions == "funny bits" and opts.no_rank_cache is True

    def test_absent_flags_stay_unset(self):
        # Each subparser defines only its own flags; a field the current
        # subcommand lacks must default rather than raise.
        opts = self._opts(["export", "--work-dir", "/w"])
        assert opts.sample_rate is None and opts.crf is None
        assert opts.centred is False and opts.no_silence is False

    def test_cut_and_reframe_and_render_flags(self):
        assert self._opts(["cut", "--work-dir", "/w", "--no-silence"]).no_silence is True
        assert self._opts(["reframe", "--work-dir", "/w", "--centred"]).centred is True
        assert self._opts(["render", "--work-dir", "/w", "--crf", "23"]).crf == 23


class TestDispatch:
    def test_calls_run_phase_with_the_parsed_arguments(self, monkeypatch, capsys):
        seen = {}

        def fake_run_phase(phase, work_dir, *, source=None, options=None):
            seen.update(phase=phase, work_dir=work_dir, source=source, options=options)
            return {"phase": phase, "ok": True}

        monkeypatch.setattr(cli, "run_phase", fake_run_phase)
        assert cli.main(["ingest", "v.mp4", "--work-dir", "/w"]) == 0

        assert seen["phase"] == "ingest"
        assert seen["work_dir"] == "/w"
        assert seen["source"] == "v.mp4"
        assert json.loads(capsys.readouterr().out)["ok"] is True

    def test_non_ingest_phases_pass_no_source(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            cli, "run_phase",
            lambda phase, wd, *, source=None, options=None: seen.update(source=source) or {},
        )
        cli.main(["rank", "--work-dir", "/w"])
        assert seen["source"] is None

    def test_domain_errors_become_exit_codes_not_tracebacks(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ValidationError("something specific went wrong")

        monkeypatch.setattr(cli, "run_phase", boom)
        assert cli.main(["rank", "--work-dir", "/w"]) == ValidationError("x").exit_code
        assert "something specific went wrong" in capsys.readouterr().err

    def test_real_validation_error_surfaces_from_the_pipeline(self, tmp_path, capsys):
        # End to end through the real run_phase: an unsupported scheme is caught.
        code = cli.main(["ingest", "file:///etc/passwd", "--work-dir", str(tmp_path / "w")])
        assert code == 1
        assert "unsupported source scheme" in capsys.readouterr().err


class TestPendingPhases:
    def test_all_seven_phases_are_implemented(self):
        assert cli._PENDING_PHASES == ()
        assert len(PHASES) == 7

    def test_a_pending_phase_would_still_report_clearly(self, monkeypatch, capsys):
        # The mechanism stays covered so re-adding a stub phase behaves.
        monkeypatch.setattr(cli, "_PENDING_PHASES", ("future",))
        assert cli.main(["future"]) == 2
        assert "not implemented yet" in capsys.readouterr().err


class TestWebCommand:
    def test_starts_the_server_with_the_parsed_options(self, monkeypatch):
        seen = {}

        def fake_serve(*, host, port, runs_dir, open_browser):
            seen.update(host=host, port=port, runs_dir=runs_dir, open_browser=open_browser)
            return 0

        from clipper_pro.web import server
        monkeypatch.setattr(server, "serve", fake_serve)

        assert cli.main(["web", "--port", "9001", "--no-browser"]) == 0
        assert seen == {
            "host": "127.0.0.1", "port": 9001, "runs_dir": None, "open_browser": False,
        }
