"""Phase 1 pure logic: argv construction, probe parsing, spec verification."""

import pytest

from clipper_pro.errors import ValidationError
from clipper_pro.ingest.audio_ops import (
    AudioSpec,
    AudioStreamInfo,
    asr_output_name,
    build_extract_command,
    build_probe_command,
    estimate_flac_bytes,
    format_size_saving,
    parse_audio_stream,
    verify_extraction,
)


def _arg_after(cmd, flag):
    """Return the token following ``flag`` in an argv list."""
    return cmd[cmd.index(flag) + 1]


class TestAudioSpec:
    def test_defaults_are_the_asr_target(self):
        spec = AudioSpec()
        assert (spec.sample_rate, spec.channels, spec.codec) == (16_000, 1, "flac")
        assert spec.suffix == ".flac"

    def test_bytes_per_second_is_uncompressed_pcm_rate(self):
        # 16 kHz * 1 channel * 2 bytes = 32 kB/s
        assert AudioSpec().bytes_per_second == 32_000
        assert AudioSpec(channels=2).bytes_per_second == 64_000

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"sample_rate": 0},
            {"sample_rate": -16_000},
            {"channels": 0},
            {"channels": 3},
            {"codec": "mp3"},
            {"compression_level": 13},
            {"compression_level": -1},
        ],
    )
    def test_rejects_out_of_range_values(self, kwargs):
        with pytest.raises(ValidationError):
            AudioSpec(**kwargs)


class TestBuildExtractCommand:
    def test_targets_mono_16k_flac(self):
        cmd = build_extract_command("in.mp4", "out.flac", AudioSpec())
        assert _arg_after(cmd, "-ac") == "1"
        assert _arg_after(cmd, "-ar") == "16000"
        assert _arg_after(cmd, "-c:a") == "flac"
        assert _arg_after(cmd, "-compression_level") == "8"
        assert cmd[-1] == "out.flac"

    def test_drops_every_non_audio_stream(self):
        cmd = build_extract_command("in.mp4", "out.flac", AudioSpec())
        # Without -vn the FLAC muxer would fail on an attached cover image, and
        # without an explicit map a multi-language upload picks a random track.
        assert {"-vn", "-sn", "-dn"} <= set(cmd)
        assert _arg_after(cmd, "-map") == "0:a:0"

    def test_strips_source_metadata(self):
        cmd = build_extract_command("in.mp4", "out.flac", AudioSpec())
        assert _arg_after(cmd, "-map_metadata") == "-1"

    def test_is_non_interactive_and_overwrites(self):
        cmd = build_extract_command("in.mp4", "out.flac", AudioSpec())
        # -nostdin matters when running under a job runner: without it ffmpeg
        # can consume the parent's stdin and hang the whole pipeline.
        assert {"-y", "-nostdin"} <= set(cmd)

    def test_seek_precedes_input_but_duration_follows_it(self):
        cmd = build_extract_command("in.mp4", "out.flac", AudioSpec(), start=12.5, duration=30)
        assert cmd.index("-ss") < cmd.index("-i") < cmd.index("-t")
        assert _arg_after(cmd, "-ss") == "12.500"
        assert _arg_after(cmd, "-t") == "30.000"

    def test_omits_slice_flags_when_not_slicing(self):
        cmd = build_extract_command("in.mp4", "out.flac", AudioSpec())
        assert "-ss" not in cmd and "-t" not in cmd

    def test_wav_spec_uses_pcm(self):
        cmd = build_extract_command("in.mp4", "out.wav", AudioSpec(codec="wav"))
        assert _arg_after(cmd, "-c:a") == "pcm_s16le"
        assert "-compression_level" not in cmd

    @pytest.mark.parametrize("kwargs", [{"start": -1.0}, {"duration": 0}, {"duration": -5}])
    def test_rejects_impossible_slices(self, kwargs):
        with pytest.raises(ValidationError):
            build_extract_command("in.mp4", "out.flac", AudioSpec(), **kwargs)

    def test_paths_are_passed_as_argv_not_shell(self):
        # A title with a space/quote must survive intact; there is no shell.
        weird = '/tmp/a video "x".mp4'
        cmd = build_extract_command(weird, "out.flac", AudioSpec())
        assert _arg_after(cmd, "-i") == weird


class TestProbeParsing:
    def test_parses_a_normal_stream(self):
        info = parse_audio_stream(
            {
                "streams": [
                    {
                        "codec_name": "flac",
                        "sample_rate": "16000",
                        "channels": 1,
                        "duration": "61.5",
                    }
                ]
            }
        )
        assert info == AudioStreamInfo("flac", 16_000, 1, 61.5)

    def test_falls_back_to_container_duration(self):
        # Some codecs omit the per-stream duration entirely.
        info = parse_audio_stream(
            {
                "streams": [{"codec_name": "aac", "sample_rate": "48000", "channels": 2}],
                "format": {"duration": "120.25"},
            }
        )
        assert info.duration == 120.25

    @pytest.mark.parametrize(
        "payload",
        [None, {}, {"streams": []}, {"streams": "nope"}, {"streams": [None]}, "junk"],
    )
    def test_missing_audio_yields_none_not_an_exception(self, payload):
        # A silent screen recording is a case to detect, not to crash on.
        assert parse_audio_stream(payload) is None

    def test_unparseable_numbers_degrade_to_zero(self):
        info = parse_audio_stream(
            {"streams": [{"codec_name": "flac", "sample_rate": "abc", "channels": None}]}
        )
        assert (info.sample_rate, info.channels, info.duration) == (0, 0, 0.0)


class TestVerifyExtraction:
    def test_matching_stream_has_no_problems(self):
        assert verify_extraction(AudioStreamInfo("flac", 16_000, 1, 30.0), AudioSpec()) == []

    def test_missing_stream_is_reported(self):
        assert verify_extraction(None, AudioSpec()) == ["no audio stream found in the extracted file"]

    def test_reports_every_mismatch_at_once(self):
        # A silent ffmpeg fallback can miss on several axes; surfacing them
        # together beats making the user re-run to find the next one.
        problems = verify_extraction(AudioStreamInfo("aac", 48_000, 2, 0.0), AudioSpec())
        assert len(problems) == 4
        joined = " ".join(problems)
        assert "codec" in joined and "sample rate" in joined
        assert "channel" in joined and "zero duration" in joined

    def test_zero_duration_is_a_failure_even_when_format_matches(self):
        problems = verify_extraction(AudioStreamInfo("flac", 16_000, 1, 0.0), AudioSpec())
        assert problems == ["extracted audio has zero duration"]


class TestOutputNaming:
    def test_derives_a_stable_name_from_the_source(self):
        assert asr_output_name("/videos/my_talk.mp4", AudioSpec()) == "audio_my_talk.flac"

    def test_is_deterministic_so_reruns_reuse_the_artifact(self):
        spec = AudioSpec()
        assert asr_output_name("/a/b.mp4", spec) == asr_output_name("/a/b.mp4", spec)

    def test_sanitises_titles_that_came_from_a_video_platform(self):
        name = asr_output_name("/dl/Why I Quit! (2024) 🎬.webm", AudioSpec())
        assert name.startswith("audio_Why_I_Quit")
        assert name.endswith(".flac")
        assert all(c.isalnum() or c in "-_." for c in name)

    def test_truncates_a_very_long_title(self):
        name = asr_output_name("/dl/" + "x" * 300 + ".mp4", AudioSpec())
        assert len(name) <= len("audio_") + 60 + len(".flac")

    def test_unusable_stem_falls_back_rather_than_producing_a_bare_suffix(self):
        assert asr_output_name("/dl/🎬🎬.mp4", AudioSpec()) == "audio_source.flac"


class TestSizeEstimates:
    def test_estimate_tracks_duration(self):
        spec = AudioSpec()
        one_minute = estimate_flac_bytes(60, spec)
        # 60s * 32 kB/s * 0.55 ≈ 1.06 MB
        assert 1_000_000 < one_minute < 1_150_000
        assert estimate_flac_bytes(120, spec) == pytest.approx(2 * one_minute, rel=0.01)

    def test_non_positive_duration_estimates_nothing(self):
        assert estimate_flac_bytes(0, AudioSpec()) == 0
        assert estimate_flac_bytes(-5, AudioSpec()) == 0

    def test_wav_spec_is_not_discounted(self):
        spec = AudioSpec(codec="wav")
        assert estimate_flac_bytes(60, spec) == 60 * spec.bytes_per_second

    def test_saving_summary_reports_a_percentage(self):
        summary = format_size_saving(1000 * 1024 * 1024, 60 * 1024 * 1024)
        assert "94.0% smaller" in summary
        assert "60.0 MB" in summary and "1000.0 MB" in summary

    @pytest.mark.parametrize("args", [(0, 100), (100, 0), (-1, -1)])
    def test_unknown_sizes_do_not_divide_by_zero(self, args):
        assert format_size_saving(*args) == "size saving unknown"

    def test_a_larger_artifact_is_reported_honestly(self):
        assert "no saving" in format_size_saving(1_000, 2_000)


def test_probe_command_requests_the_fields_verification_needs():
    cmd = build_probe_command("/tmp/a.flac")
    assert _arg_after(cmd, "-select_streams") == "a:0"
    assert _arg_after(cmd, "-of") == "json"
    entries = " ".join(cmd)
    for field in ("codec_name", "sample_rate", "channels", "duration"):
        assert field in entries
    assert cmd[-1] == "/tmp/a.flac"
