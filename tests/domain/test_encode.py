"""Host tests for the shared libx264 encode-settings helper."""
import pytest

from clippyme.domain.encode import x264_crf, x264_preset, x264_video_args


def test_defaults_are_near_visually_lossless(monkeypatch):
    monkeypatch.delenv("CLIPPYME_X264_CRF", raising=False)
    monkeypatch.delenv("CLIPPYME_X264_PRESET", raising=False)
    assert x264_crf() == 18
    assert x264_preset() == "medium"


def test_crf_env_override_clamped(monkeypatch):
    monkeypatch.setenv("CLIPPYME_X264_CRF", "14")
    assert x264_crf() == 14
    # Out of range / garbage → default 18.
    monkeypatch.setenv("CLIPPYME_X264_CRF", "99")
    assert x264_crf() == 18
    monkeypatch.setenv("CLIPPYME_X264_CRF", "-3")
    assert x264_crf() == 18
    monkeypatch.setenv("CLIPPYME_X264_CRF", "notanint")
    assert x264_crf() == 18


def test_preset_env_override_validated(monkeypatch):
    monkeypatch.setenv("CLIPPYME_X264_PRESET", "slow")
    assert x264_preset() == "slow"
    # Unknown preset → default medium.
    monkeypatch.setenv("CLIPPYME_X264_PRESET", "warpspeed")
    assert x264_preset() == "medium"


def test_video_args_shape(monkeypatch):
    monkeypatch.delenv("CLIPPYME_X264_CRF", raising=False)
    monkeypatch.delenv("CLIPPYME_X264_PRESET", raising=False)
    args = x264_video_args()
    assert args == [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]


def test_video_args_overrides_and_toggles():
    # Explicit overrides win over env/default.
    args = x264_video_args(crf=16, preset="slower")
    assert "-crf" in args and args[args.index("-crf") + 1] == "16"
    assert args[args.index("-preset") + 1] == "slower"

    # faststart=False drops the moov-atom flag (intermediate files).
    assert "-movflags" not in x264_video_args(faststart=False)

    # pix_fmt=None omits the pixel-format flag.
    assert "-pix_fmt" not in x264_video_args(pix_fmt=None)


def test_video_args_is_a_fresh_list_each_call():
    a = x264_video_args()
    a.append("MUTATED")
    assert "MUTATED" not in x264_video_args()


def test_ffmpeg_timeout_default_and_env(monkeypatch):
    from clippyme.domain.encode import ffmpeg_timeout

    monkeypatch.delenv("CLIPPYME_FFMPEG_TIMEOUT", raising=False)
    assert ffmpeg_timeout() == 600

    monkeypatch.setenv("CLIPPYME_FFMPEG_TIMEOUT", "120")
    assert ffmpeg_timeout() == 120

    # Invalid / non-positive values fall back to the default.
    for bad in ("0", "-5", "abc", ""):
        monkeypatch.setenv("CLIPPYME_FFMPEG_TIMEOUT", bad)
        assert ffmpeg_timeout() == 600


def test_intermediate_crf_default_is_better_than_delivery(monkeypatch):
    from clippyme.domain.encode import x264_crf, x264_intermediate_crf

    monkeypatch.delenv("CLIPPYME_X264_INTERMEDIATE_CRF", raising=False)
    monkeypatch.delenv("CLIPPYME_X264_CRF", raising=False)
    assert x264_intermediate_crf() == 14
    # Lower CRF == higher quality. A delivered clip stacks ~5 generations, so
    # the ones feeding the next pass must not throw away more than the last.
    assert x264_intermediate_crf() < x264_crf()


def test_intermediate_crf_env_override(monkeypatch):
    from clippyme.domain.encode import x264_intermediate_crf

    monkeypatch.delenv("CLIPPYME_X264_CRF", raising=False)
    monkeypatch.setenv("CLIPPYME_X264_INTERMEDIATE_CRF", "10")
    assert x264_intermediate_crf() == 10


def test_intermediate_crf_never_worse_than_delivery(monkeypatch):
    from clippyme.domain.encode import x264_intermediate_crf

    # An intermediate configured *coarser* than delivery would degrade the
    # chain below its own output quality — nonsense, so it clamps to delivery.
    monkeypatch.setenv("CLIPPYME_X264_CRF", "20")
    monkeypatch.setenv("CLIPPYME_X264_INTERMEDIATE_CRF", "30")
    assert x264_intermediate_crf() == 20


def test_intermediate_crf_bad_values_fall_back_to_default(monkeypatch):
    from clippyme.domain.encode import x264_intermediate_crf

    monkeypatch.delenv("CLIPPYME_X264_CRF", raising=False)
    for bad in ("abc", "", "-1", "99"):
        monkeypatch.setenv("CLIPPYME_X264_INTERMEDIATE_CRF", bad)
        assert x264_intermediate_crf() == 14
