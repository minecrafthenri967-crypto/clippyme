"""CLI surface: argument wiring, phase chaining through the manifest, exit codes."""

import json

import pytest

from clipper_pro import cli
from clipper_pro.errors import ValidationError
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import record_artifact


class TestParser:
    def test_every_phase_is_listed(self, capsys):
        # --help must reflect the real shape of the pipeline, including the
        # phases that are not built yet.
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--help"])
        out = capsys.readouterr().out
        for phase in ("ingest", "transcribe", "rank", "cut", "reframe", "render", "export"):
            assert phase in out

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


class TestUnimplementedPhases:
    def test_all_seven_phases_are_implemented(self):
        # Every phase of the pipeline now has a handler; nothing is pending.
        assert cli._PENDING_PHASES == ()
        assert set(cli._HANDLERS) == {
            "ingest", "transcribe", "rank", "cut", "reframe", "render", "export",
        }

    def test_a_pending_phase_would_still_report_clearly(self, capsys, monkeypatch):
        # The mechanism stays covered so re-adding a stub phase behaves.
        monkeypatch.setattr(cli, "_PENDING_PHASES", ("future",))
        monkeypatch.setattr(cli, "_HANDLERS", dict(cli._HANDLERS))
        assert cli.main(["future"]) == 2
        assert "not implemented yet" in capsys.readouterr().err


class TestPhaseChaining:
    def test_transcribe_reads_the_ingest_artifact(self, tmp_path, monkeypatch):
        root = str(tmp_path / "run")
        workspace_init(root)
        audio = tmp_path / "run" / "audio" / "a.flac"
        audio.write_bytes(b"AUDIO")
        record_artifact(root, "ingest", {
            "phase": "ingest",
            "source": {"path": "/v/a.mp4", "duration": 10.0, "audio_path": str(audio)},
        })

        captured = {}

        def fake_run_transcribe(media, work_dir, **kwargs):
            from clipper_pro.transcribe.base import TranscriptResult
            from clipper_pro.types import Word
            captured["audio_path"] = media.audio_path
            captured["kwargs"] = kwargs
            return TranscriptResult(
                words=[Word("hi", 0.0, 0.5, speaker=0)],
                language="en", provider="deepgram", model="nova-3",
            )

        import clipper_pro.transcribe as phase2
        monkeypatch.setattr(phase2, "run_transcribe", fake_run_transcribe)

        assert cli.main(["transcribe", "--work-dir", root]) == 0
        # The phase found its input without the caller re-supplying any path.
        assert captured["audio_path"] == str(audio)

    def test_transcribe_without_ingest_is_a_clear_error(self, tmp_path, capsys):
        root = str(tmp_path / "run")
        workspace_init(root)
        assert cli.main(["transcribe", "--work-dir", root]) == ValidationError("x").exit_code
        assert "run 'clipper-pro ingest' first" in capsys.readouterr().err

    def test_media_from_workspace_rejects_a_malformed_artifact(self, tmp_path):
        root = str(tmp_path / "run")
        workspace_init(root)
        record_artifact(root, "ingest", {"phase": "ingest"})  # no source record
        with pytest.raises(ValidationError, match="no ingest artifact"):
            cli._media_from_workspace(root)


class TestIngestCommand:
    def test_prints_json_and_records_the_artifact(self, tmp_path, monkeypatch, capsys):
        from clipper_pro.types import SourceMedia

        root = str(tmp_path / "run")
        audio = tmp_path / "a.flac"
        audio.write_bytes(b"\x00" * 100)
        source = tmp_path / "v.mp4"
        source.write_bytes(b"\x00" * 1000)

        monkeypatch.setattr(
            cli, "run_ingest",
            lambda *a, **k: SourceMedia(
                path=str(source), duration=20.0, title="v", audio_path=str(audio)
            ),
        )

        assert cli.main(["ingest", str(source), "--work-dir", root]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["phase"] == "ingest"
        assert payload["audio"]["sample_rate"] == 16_000

        from clipper_pro.workspace import load
        assert load(root)["artifacts"]["ingest"]["phase"] == "ingest"

    def test_domain_errors_become_exit_codes_not_tracebacks(self, tmp_path, capsys):
        root = str(tmp_path / "run")
        code = cli.main(["ingest", "file:///etc/passwd", "--work-dir", root])
        assert code == 1
        assert "unsupported source scheme" in capsys.readouterr().err
