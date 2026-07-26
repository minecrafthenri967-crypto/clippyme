"""Environment-driven settings, resolved once and passed down explicitly.

Every knob has a working default, so a fresh checkout runs without a ``.env``.
Values are read through the clamping helpers below rather than ``int(os.environ
[...])`` so a typo in the environment degrades to the default instead of
crashing a long job halfway through.

No module-level mutable state: call :func:`Settings.from_env` once at the entry
point and thread the result through. That keeps tests from having to monkeypatch
globals and makes a per-job override (different ranker, different sample rate)
a matter of constructing a second ``Settings``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from clipper_pro.errors import ConfigError

__all__ = ["Settings", "env_choice", "env_flag", "env_int"]

# Ranking providers understood by clipper_pro.rank. DeepSeek-V3 is the default
# per the pipeline design; the Gemini adapter exists so a host that already has
# Gemini credentials (as ClippyMe does) can run phase 3 without a second key.
RANKERS = ("deepseek", "gemini")

_DEFAULT_FFMPEG_TIMEOUT = 1800


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read an int env var, clamped to ``[minimum, maximum]``.

    Unset, blank, or unparseable values yield ``default``. A parseable value
    outside the range is clamped rather than rejected — the caller asked for
    "as much as possible" and a hard failure here would be worse than honouring
    the intent at the boundary.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var. ``1/true/yes/on`` are true, ``0/false/no/off`` false."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def env_choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    """Read an enum-ish env var, falling back to ``default`` when unrecognised."""
    raw = (os.getenv(name) or "").strip().lower()
    return raw if raw in allowed else default


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one pipeline run."""

    # -- phase 1: ingest ---------------------------------------------------
    asr_sample_rate: int = 16_000
    asr_channels: int = 1
    flac_compression: int = 8
    ffmpeg_timeout: int = _DEFAULT_FFMPEG_TIMEOUT

    # -- phase 3: rank -----------------------------------------------------
    ranker: str = "deepseek"
    rank_cache: bool = True

    # -- phase 7: export ---------------------------------------------------
    export_crf: int = 18

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from ``CLIPPER_PRO_*`` environment variables."""
        return cls(
            # 16 kHz is what Nova-3 and Whisper resample to internally, so
            # anything higher is bytes paid for and then discarded. The range
            # still permits 8 kHz (telephony sources) and 48 kHz (when a
            # downstream consumer genuinely needs full-band audio).
            asr_sample_rate=env_int(
                "CLIPPER_PRO_ASR_SAMPLE_RATE", 16_000, minimum=8_000, maximum=48_000
            ),
            asr_channels=env_int(
                "CLIPPER_PRO_ASR_CHANNELS", 1, minimum=1, maximum=2
            ),
            # FLAC compression is lossless at every level — level 8 costs a
            # little CPU during extraction and buys a smaller upload, which is
            # the actual bottleneck when posting an hour of audio to Deepgram.
            flac_compression=env_int(
                "CLIPPER_PRO_FLAC_COMPRESSION", 8, minimum=0, maximum=12
            ),
            ffmpeg_timeout=env_int(
                "CLIPPER_PRO_FFMPEG_TIMEOUT",
                _DEFAULT_FFMPEG_TIMEOUT,
                minimum=30,
                maximum=24 * 3600,
            ),
            ranker=env_choice("CLIPPER_PRO_RANKER", "deepseek", RANKERS),
            rank_cache=env_flag("CLIPPER_PRO_RANK_CACHE", True),
            export_crf=env_int("CLIPPER_PRO_EXPORT_CRF", 18, minimum=0, maximum=51),
        )

    def with_overrides(self, **kwargs: object) -> Settings:
        """Return a copy with the named fields replaced (CLI flags beat env)."""
        unknown = set(kwargs) - {f for f in self.__dataclass_fields__}
        if unknown:
            raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})
