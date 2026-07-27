"""The extraction I/O layer, with ffmpeg/ffprobe faked at the subprocess seam.

These cover the decisions the pure layer cannot: when extraction is skipped,
when it refuses to continue, and what lands in the provenance sidecar.
"""

import json
import os
import subprocess

import pytest

from clipper_pro.errors import IngestError, MissingBinaryError, ToolFailureError
from clipper_pro.ingest import audio as audio_io
from clipper_pro.ingest.audio_ops import AudioSpec


def _probe_payload(codec="flac", rate=16_000, channels=1, duration=42.0):
    return {
        "streams": [
            {
                "codec_name": codec,
                "sample_rate": str(rate),
                "channels": channels,
                "duration": str(duration),
            }
        ]
    }


@pytest.fixture
def fake_tools(monkeypatch):
    """Stub ffmpeg/ffprobe. Records calls; ffmpeg materialises its output file."""
    state = {"calls": [], "probe": _probe_payload(), "ffmpeg_rc": 0, "writes": True}

    monkeypatch.setattr(audio_io, "require_binary", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, capture_output=True, text=True, check=False, timeout=None):
        tool = os.path.basename(cmd[0])
        state["calls"].append((tool, cmd))
        if tool == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, json.dumps(state["probe"]), "")
        if state["ffmpeg_rc"] == 0 and state["writes"]:
            with open(cmd[-1], "wb") as fh:
                fh.write(b"\x00" * 1024)
        return subprocess.CompletedProcess(cmd, state["ffmpeg_rc"], "", "ffmpeg said no")

    monkeypatch.setattr(audio_io.subprocess, "run", fake_run)
    return state


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"\x00" * 4096)
    return str(path)


class TestExtractAnalysisAudio:
    def test_produces_audio_and_returns_its_path(self, fake_tools, source, tmp_path):
        out = audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))
        assert out.endswith("audio_talk.flac")
        assert os.path.isfile(out)

    def test_runs_ffmpeg_with_the_spec_the_caller_asked_for(self, fake_tools, source, tmp_path):
        fake_tools["probe"] = _probe_payload(rate=8_000)
        audio_io.extract_analysis_audio(
            source, str(tmp_path / "audio"), AudioSpec(sample_rate=8_000)
        )
        ffmpeg_cmd = next(cmd for tool, cmd in fake_tools["calls"] if tool == "ffmpeg")
        assert ffmpeg_cmd[ffmpeg_cmd.index("-ar") + 1] == "8000"

    def test_creates_the_output_directory(self, fake_tools, source, tmp_path):
        target = tmp_path / "nested" / "audio"
        audio_io.extract_analysis_audio(source, str(target))
        assert target.is_dir()

    def test_missing_source_fails_before_touching_ffmpeg(self, fake_tools, tmp_path):
        with pytest.raises(IngestError, match="source file not found"):
            audio_io.extract_analysis_audio(str(tmp_path / "gone.mp4"), str(tmp_path))
        assert fake_tools["calls"] == []

    def test_silent_source_is_refused_before_a_long_decode(self, fake_tools, source, tmp_path):
        fake_tools["probe"] = {"streams": []}
        with pytest.raises(IngestError, match="no audio stream"):
            audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))
        assert not any(tool == "ffmpeg" for tool, _ in fake_tools["calls"])

    def test_ffmpeg_failure_surfaces_its_stderr(self, fake_tools, source, tmp_path):
        fake_tools["ffmpeg_rc"] = 1
        with pytest.raises(ToolFailureError, match="ffmpeg said no"):
            audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))

    def test_silent_no_op_is_caught(self, fake_tools, source, tmp_path):
        # ffmpeg exiting 0 without writing anything must not read as success.
        fake_tools["writes"] = False
        with pytest.raises(ToolFailureError, match="produced no audio"):
            audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))

    def test_off_spec_output_is_rejected_rather_than_transcribed(
        self, fake_tools, source, tmp_path, monkeypatch
    ):
        # An ffmpeg resampler fallback yields a playable file that is not what
        # the ASR provider was promised — the cause of mysteriously bad
        # transcripts if it slipped through. The source probes clean; the
        # extracted artifact comes back at 48 kHz stereo.
        probes = {"n": 0}

        def drifting_run(cmd, **kwargs):
            if os.path.basename(cmd[0]) == "ffprobe":
                probes["n"] += 1
                payload = (
                    _probe_payload()
                    if probes["n"] == 1
                    else _probe_payload(rate=48_000, channels=2)
                )
                return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
            with open(cmd[-1], "wb") as fh:
                fh.write(b"\x00" * 512)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(audio_io.subprocess, "run", drifting_run)
        with pytest.raises(ToolFailureError, match="does not match the requested spec"):
            audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))

    def test_verification_can_be_skipped(self, fake_tools, source, tmp_path):
        fake_tools["probe"] = _probe_payload(rate=48_000, channels=2)
        out = audio_io.extract_analysis_audio(source, str(tmp_path / "audio"), verify=False)
        assert os.path.isfile(out)

    def test_timeout_is_reported_as_a_tool_failure(self, fake_tools, source, tmp_path, monkeypatch):
        def timing_out(cmd, **kwargs):
            if os.path.basename(cmd[0]) == "ffprobe":
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_probe_payload()), "")
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(audio_io.subprocess, "run", timing_out)
        with pytest.raises(ToolFailureError, match="timed out"):
            audio_io.extract_analysis_audio(source, str(tmp_path / "audio"), timeout=5)

    def test_missing_ffmpeg_names_the_binary(self, monkeypatch, source, tmp_path):
        monkeypatch.setattr(audio_io.shutil, "which", lambda name: None)
        with pytest.raises(MissingBinaryError, match="ffprobe"):
            audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))


class TestResume:
    def test_existing_artifact_is_reused(self, fake_tools, source, tmp_path):
        out_dir = tmp_path / "audio"
        first = audio_io.extract_analysis_audio(source, str(out_dir))
        fake_tools["calls"].clear()

        second = audio_io.extract_analysis_audio(source, str(out_dir))
        assert second == first
        assert fake_tools["calls"] == []  # nothing re-run

    def test_overwrite_forces_a_re_extraction(self, fake_tools, source, tmp_path):
        out_dir = tmp_path / "audio"
        audio_io.extract_analysis_audio(source, str(out_dir))
        fake_tools["calls"].clear()

        audio_io.extract_analysis_audio(source, str(out_dir), overwrite=True)
        assert any(tool == "ffmpeg" for tool, _ in fake_tools["calls"])

    def test_empty_leftover_file_is_not_mistaken_for_a_result(self, fake_tools, source, tmp_path):
        out_dir = tmp_path / "audio"
        out_dir.mkdir()
        (out_dir / "audio_talk.flac").write_bytes(b"")
        audio_io.extract_analysis_audio(source, str(out_dir))
        assert any(tool == "ffmpeg" for tool, _ in fake_tools["calls"])


class TestProvenance:
    def test_sidecar_records_both_ends_and_the_exact_argv(self, fake_tools, source, tmp_path):
        out = audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))
        with open(out + ".provenance.json") as fh:
            sidecar = json.load(fh)

        assert sidecar["command"] == "ingest.extract_analysis_audio"
        assert sidecar["inputs"][0]["path"] == source
        assert len(sidecar["inputs"][0]["sha256"]) == 64
        assert sidecar["output"]["path"] == out
        assert sidecar["parameters"]["sample_rate"] == 16_000
        assert sidecar["parameters"]["channels"] == 1
        # The argv is what makes the artifact reproducible from the sidecar alone.
        assert "-ar" in sidecar["tool_commands"][0]

    def test_sidecar_leaves_no_temp_file(self, fake_tools, source, tmp_path):
        out = audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))
        assert not os.path.exists(out + ".provenance.json.tmp")


class TestProbeAudio:
    def test_returns_parsed_stream_info(self, fake_tools, source):
        info = audio_io.probe_audio(source)
        assert (info.codec_name, info.sample_rate, info.channels) == ("flac", 16_000, 1)

    def test_invalid_json_is_a_tool_failure(self, monkeypatch, source):
        monkeypatch.setattr(audio_io, "require_binary", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            audio_io.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "not json", ""),
        )
        with pytest.raises(ToolFailureError, match="invalid JSON"):
            audio_io.probe_audio(source)


class TestDescribeSaving:
    def test_summarises_the_upload_reduction(self, fake_tools, source, tmp_path):
        out = audio_io.extract_analysis_audio(source, str(tmp_path / "audio"))
        summary = audio_io.describe_saving(source, out)
        assert "MB" in summary

    def test_missing_files_do_not_raise(self, tmp_path):
        assert audio_io.describe_saving(str(tmp_path / "a"), str(tmp_path / "b")) == (
            "size saving unknown"
        )
