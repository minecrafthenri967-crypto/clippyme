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

# Transcription providers understood by clipper_pro.transcribe. Deepgram Nova-3
# is the default for word-level accuracy; ElevenLabs Scribe is the option that
# also tags audio events (see clipper_pro.transcribe.base for the capability
# table and why the difference is declared rather than assumed).
TRANSCRIBERS = ("deepgram", "elevenlabs")

# Caption style presets and positions. Duplicated from
# clipper_pro.render.captions_ops rather than imported, because config must stay
# importable without pulling the render stack in; the two are kept in step by a
# test that compares them.
CAPTION_PRESETS = (
    "hormozi_bold", "classic_white", "neon_glow",
    "mrbeast_box", "minimal_clean", "fire_impact",
)
CAPTION_POSITIONS = ("bottom", "center", "top")

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

    # -- phase 2: transcribe -----------------------------------------------
    transcriber: str = "deepgram"
    transcript_cache: bool = True

    # -- phase 3: rank -----------------------------------------------------
    ranker: str = "deepseek"
    rank_cache: bool = True
    max_clips: int = 10
    # Bounds the ranker is told to respect and phase 3 enforces. 12 s is about
    # the floor at which a hook-plus-payoff fits; 60 s is the ceiling shared by
    # Reels/Shorts/TikTok's short lane.
    min_clip_duration: float = 12.0
    max_clip_duration: float = 60.0

    # -- phase 4: cut ------------------------------------------------------
    # Stage 3 of the snapping cascade. On by default: a sentence boundary is a
    # grammatical claim, silence is an acoustic fact, and only the second makes
    # the cut inaudible. Disable to keep purely transcript-derived edges.
    snap_silence: bool = True

    # -- phase 5: reframe --------------------------------------------------
    # How far ahead of audible speech the camera starts moving. Phase 5 runs
    # offline, so this is a shift applied to known onsets, not a prediction.
    camera_lead: float = 0.2
    # Minimum screen time before the camera may leave a speaker; stops a rapid
    # exchange from panning faster than a viewer can follow.
    camera_min_hold: float = 1.0
    camera_smooth_seconds: float = 0.35
    speaker_gap_tolerance: float = 0.8

    # -- phase 6: render ---------------------------------------------------
    output_width: int = 1080
    output_height: int = 1920
    # Burned-in karaoke captions. Off by default so an existing recipe renders
    # unchanged; costs no extra API (phase 2's word timings) and no extra
    # encode (same ffmpeg pass).
    captions: bool = False
    caption_preset: str = "hormozi_bold"
    caption_words_per_group: int = 3
    caption_position: str = "bottom"
    caption_uppercase: bool = True

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
            transcriber=env_choice(
                "CLIPPER_PRO_TRANSCRIBER", "deepgram", TRANSCRIBERS
            ),
            transcript_cache=env_flag("CLIPPER_PRO_TRANSCRIPT_CACHE", True),
            ranker=env_choice("CLIPPER_PRO_RANKER", "deepseek", RANKERS),
            rank_cache=env_flag("CLIPPER_PRO_RANK_CACHE", True),
            max_clips=env_int("CLIPPER_PRO_MAX_CLIPS", 10, minimum=1, maximum=50),
            min_clip_duration=float(
                env_int("CLIPPER_PRO_MIN_CLIP_SECONDS", 12, minimum=3, maximum=120)
            ),
            max_clip_duration=float(
                env_int("CLIPPER_PRO_MAX_CLIP_SECONDS", 60, minimum=5, maximum=180)
            ),
            snap_silence=env_flag("CLIPPER_PRO_SNAP_SILENCE", True),
            # Sub-second knobs are read in milliseconds so the clamping helper
            # stays integer-only and a typo cannot produce a fractional mess.
            camera_lead=env_int(
                "CLIPPER_PRO_CAMERA_LEAD_MS", 200, minimum=0, maximum=2000
            ) / 1000.0,
            camera_min_hold=env_int(
                "CLIPPER_PRO_CAMERA_MIN_HOLD_MS", 1000, minimum=0, maximum=10_000
            ) / 1000.0,
            camera_smooth_seconds=env_int(
                "CLIPPER_PRO_CAMERA_SMOOTH_MS", 350, minimum=0, maximum=5000
            ) / 1000.0,
            speaker_gap_tolerance=env_int(
                "CLIPPER_PRO_SPEAKER_GAP_MS", 800, minimum=0, maximum=10_000
            ) / 1000.0,
            output_width=env_int(
                "CLIPPER_PRO_OUTPUT_WIDTH", 1080, minimum=160, maximum=2160
            ),
            output_height=env_int(
                "CLIPPER_PRO_OUTPUT_HEIGHT", 1920, minimum=160, maximum=3840
            ),
            captions=env_flag("CLIPPER_PRO_CAPTIONS", False),
            caption_preset=env_choice(
                "CLIPPER_PRO_CAPTION_PRESET", "hormozi_bold", CAPTION_PRESETS
            ),
            caption_words_per_group=env_int(
                "CLIPPER_PRO_CAPTION_WORDS", 3, minimum=1, maximum=12
            ),
            caption_position=env_choice(
                "CLIPPER_PRO_CAPTION_POSITION", "bottom", CAPTION_POSITIONS
            ),
            caption_uppercase=env_flag("CLIPPER_PRO_CAPTION_UPPERCASE", True),
            export_crf=env_int("CLIPPER_PRO_EXPORT_CRF", 18, minimum=0, maximum=51),
        )

    def with_overrides(self, **kwargs: object) -> Settings:
        """Return a copy with the named fields replaced (CLI flags beat env)."""
        unknown = set(kwargs) - {f for f in self.__dataclass_fields__}
        if unknown:
            raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})
