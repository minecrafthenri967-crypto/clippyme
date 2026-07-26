"""Settings resolution: defaults, clamping, and tolerance of bad environments."""

from dataclasses import FrozenInstanceError

import pytest

from clipper_pro.config import Settings, env_choice, env_flag, env_int
from clipper_pro.errors import ConfigError


class TestEnvInt:
    def test_unset_and_blank_yield_the_default(self, monkeypatch):
        monkeypatch.delenv("X_INT", raising=False)
        assert env_int("X_INT", 7, minimum=0, maximum=10) == 7
        monkeypatch.setenv("X_INT", "   ")
        assert env_int("X_INT", 7, minimum=0, maximum=10) == 7

    def test_reads_a_valid_value(self, monkeypatch):
        monkeypatch.setenv("X_INT", "5")
        assert env_int("X_INT", 7, minimum=0, maximum=10) == 5

    def test_garbage_falls_back_instead_of_raising(self, monkeypatch):
        # A typo in the environment must not kill a job an hour in.
        monkeypatch.setenv("X_INT", "sixteen")
        assert env_int("X_INT", 7, minimum=0, maximum=10) == 7

    @pytest.mark.parametrize("raw,expected", [("999", 10), ("-999", 0)])
    def test_out_of_range_is_clamped(self, monkeypatch, raw, expected):
        monkeypatch.setenv("X_INT", raw)
        assert env_int("X_INT", 7, minimum=0, maximum=10) == expected


class TestEnvFlag:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_spellings(self, monkeypatch, raw):
        monkeypatch.setenv("X_FLAG", raw)
        assert env_flag("X_FLAG") is True

    @pytest.mark.parametrize("raw", ["0", "false", "No", "off"])
    def test_falsy_spellings(self, monkeypatch, raw):
        monkeypatch.setenv("X_FLAG", raw)
        assert env_flag("X_FLAG", default=True) is False

    @pytest.mark.parametrize("raw", ["", "maybe"])
    def test_unset_or_unrecognised_uses_the_default(self, monkeypatch, raw):
        monkeypatch.setenv("X_FLAG", raw)
        assert env_flag("X_FLAG", default=True) is True


class TestEnvChoice:
    def test_accepts_an_allowed_value_case_insensitively(self, monkeypatch):
        monkeypatch.setenv("X_CH", "GEMINI")
        assert env_choice("X_CH", "deepseek", ("deepseek", "gemini")) == "gemini"

    def test_unknown_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("X_CH", "llama")
        assert env_choice("X_CH", "deepseek", ("deepseek", "gemini")) == "deepseek"


class TestSettings:
    def test_defaults_match_the_pipeline_targets(self, monkeypatch):
        for name in (
            "CLIPPER_PRO_ASR_SAMPLE_RATE", "CLIPPER_PRO_ASR_CHANNELS",
            "CLIPPER_PRO_FLAC_COMPRESSION", "CLIPPER_PRO_RANKER",
            "CLIPPER_PRO_EXPORT_CRF",
        ):
            monkeypatch.delenv(name, raising=False)
        settings = Settings.from_env()
        assert settings.asr_sample_rate == 16_000
        assert settings.asr_channels == 1
        assert settings.flac_compression == 8
        assert settings.ranker == "deepseek"
        # CRF 18 is the CapCut-import target: an intermediate, not a delivery file.
        assert settings.export_crf == 18

    def test_reads_overrides_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_PRO_ASR_SAMPLE_RATE", "48000")
        monkeypatch.setenv("CLIPPER_PRO_RANKER", "gemini")
        settings = Settings.from_env()
        assert settings.asr_sample_rate == 48_000
        assert settings.ranker == "gemini"

    def test_absurd_sample_rate_is_clamped_to_a_usable_one(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_PRO_ASR_SAMPLE_RATE", "1000000")
        assert Settings.from_env().asr_sample_rate == 48_000

    def test_settings_are_immutable(self):
        # Settings are threaded through a whole run; a phase must not be able to
        # mutate them out from under a later one.
        with pytest.raises(FrozenInstanceError):
            Settings().asr_sample_rate = 8_000  # type: ignore[misc]

    def test_with_overrides_replaces_named_fields(self):
        assert Settings().with_overrides(asr_sample_rate=8_000).asr_sample_rate == 8_000

    def test_with_overrides_ignores_none_so_unset_cli_flags_do_not_win(self):
        # argparse hands us None for a flag the user did not pass; that must
        # leave the environment-resolved value alone.
        base = Settings(asr_sample_rate=48_000)
        assert base.with_overrides(asr_sample_rate=None).asr_sample_rate == 48_000

    def test_unknown_override_is_rejected(self):
        with pytest.raises(ConfigError, match="unknown setting"):
            Settings().with_overrides(sample_rate=8_000)
