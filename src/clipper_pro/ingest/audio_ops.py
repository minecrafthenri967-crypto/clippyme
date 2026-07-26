"""Pure helpers for audio extraction — argv construction, probe parsing, sizing.

Everything here is stdlib-only and side-effect free, so the whole of phase 1's
decision-making is covered by the fast host suite. :mod:`clipper_pro.ingest.audio`
holds the three lines that actually run ffmpeg.

Why the extraction shape is what it is
--------------------------------------
The pipeline separates audio from video as its very first act, and every later
analysis phase reads only the audio. The target is **mono 16 kHz FLAC**:

* *16 kHz* — Nova-3 and Whisper both resample to 16 kHz internally. Sending
  48 kHz means paying to upload samples the model immediately discards.
* *mono* — ASR models mix to mono anyway; a stereo podcast track is 2× the
  bytes for zero additional recognised words.
* *FLAC* — lossless, so unlike an MP3/Opus intermediate it costs no accuracy,
  while still compressing speech to roughly half of raw PCM. That matters
  because upload time, not inference time, dominates the wall clock on a
  long video, and a mid-upload network error on an hour-long file is the most
  common way a batch job dies.

A 60-minute video that arrives as a 900 MB MP4 leaves this phase as a ~60 MB
FLAC — small enough to stay far under provider file-size caps and to retry
cheaply.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from clipper_pro.errors import ValidationError

__all__ = [
    "PCM_BYTES_PER_SAMPLE",
    "SPEECH_FLAC_RATIO",
    "AudioSpec",
    "AudioStreamInfo",
    "asr_output_name",
    "build_extract_command",
    "build_probe_command",
    "estimate_flac_bytes",
    "format_size_saving",
    "parse_audio_stream",
    "verify_extraction",
]

#: FLAC is fixed at 16-bit for our extraction (``s16`` sample format).
PCM_BYTES_PER_SAMPLE = 2

#: Measured compression ratio of FLAC over 16-bit PCM for spoken-word audio.
#: Speech has long low-energy stretches and strong sample-to-sample correlation,
#: so it compresses far better than music (~0.7). Used only for *estimates*
#: shown to the user before a long upload — never for allocation.
SPEECH_FLAC_RATIO = 0.55

_ALLOWED_CODECS = ("flac", "wav", "pcm_s16le")


@dataclass(frozen=True)
class AudioSpec:
    """The target format for extracted analysis audio."""

    sample_rate: int = 16_000
    channels: int = 1
    codec: str = "flac"
    compression_level: int = 8

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValidationError(f"sample_rate must be positive, got {self.sample_rate}")
        if self.channels not in (1, 2):
            raise ValidationError(f"channels must be 1 or 2, got {self.channels}")
        if self.codec not in _ALLOWED_CODECS:
            raise ValidationError(
                f"codec must be one of {', '.join(_ALLOWED_CODECS)}, got {self.codec!r}"
            )
        if not 0 <= self.compression_level <= 12:
            raise ValidationError(
                f"compression_level must be within 0–12, got {self.compression_level}"
            )

    @property
    def suffix(self) -> str:
        """File extension for this spec, dot included."""
        return ".flac" if self.codec == "flac" else ".wav"

    @property
    def bytes_per_second(self) -> int:
        """Uncompressed byte rate — the ceiling an estimate works down from."""
        return self.sample_rate * self.channels * PCM_BYTES_PER_SAMPLE


@dataclass(frozen=True)
class AudioStreamInfo:
    """The subset of an ffprobe audio stream that phase 1 checks."""

    codec_name: str
    sample_rate: int
    channels: int
    duration: float


def asr_output_name(source_path: str, spec: AudioSpec, *, prefix: str = "audio") -> str:
    """Deterministic output filename for a source's analysis audio.

    Deterministic rather than timestamped so re-running phase 1 over an existing
    workspace overwrites its own artifact instead of littering the directory
    with one FLAC per attempt — and so a resumed run can find the audio a
    previous run already extracted.
    """
    stem = os.path.splitext(os.path.basename(source_path))[0]
    # A source basename can be almost anything (yt-dlp keeps the video title).
    # Only characters that are safe on every filesystem survive.
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in stem).strip("_")
    safe = safe[:60] or "source"
    return f"{prefix}_{safe}{spec.suffix}"


def build_extract_command(
    source_path: str,
    destination: str,
    spec: AudioSpec,
    *,
    start: float | None = None,
    duration: float | None = None,
) -> list[str]:
    """Build the ffmpeg argv that extracts analysis audio from ``source_path``.

    ``start``/``duration`` cut a slice instead of the whole file — used when a
    later phase wants the audio for one candidate rather than the full source.
    ``-ss`` is placed *before* ``-i`` so ffmpeg seeks by index rather than
    decoding and discarding everything up to the mark; with a re-encode on the
    output side that stays sample-accurate.
    """
    if start is not None and start < 0:
        raise ValidationError(f"start must be >= 0, got {start}")
    if duration is not None and duration <= 0:
        raise ValidationError(f"duration must be positive, got {duration}")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", source_path]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]

    # Take exactly the first audio stream and nothing else: multi-language
    # uploads carry several, and letting ffmpeg guess produces a different
    # track between runs. -vn/-sn/-dn drop video, subtitles and data; stripping
    # metadata keeps the artifact free of source tags we would only have to
    # scrub later.
    cmd += [
        "-map", "0:a:0",
        "-vn", "-sn", "-dn",
        "-map_metadata", "-1",
        "-ac", str(spec.channels),
        "-ar", str(spec.sample_rate),
    ]

    if spec.codec == "flac":
        cmd += ["-c:a", "flac", "-compression_level", str(spec.compression_level)]
    else:
        cmd += ["-c:a", "pcm_s16le"]

    cmd.append(destination)
    return cmd


def build_probe_command(path: str) -> list[str]:
    """Build the ffprobe argv that reports the first audio stream as JSON."""
    return [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
    ]


def parse_audio_stream(probe: dict[str, Any] | None) -> AudioStreamInfo | None:
    """Parse :func:`build_probe_command` output into :class:`AudioStreamInfo`.

    Returns ``None`` when there is no audio stream or the payload is unusable —
    a silent screen recording is a legitimate input to *detect*, not a crash.
    Duration falls back to the container's, since some codecs omit the
    per-stream value.
    """
    if not isinstance(probe, dict):
        return None
    streams = probe.get("streams")
    if not isinstance(streams, list) or not streams:
        return None
    stream = streams[0]
    if not isinstance(stream, dict):
        return None

    def _float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    duration = _float(stream.get("duration"))
    if duration <= 0:
        fmt = probe.get("format")
        duration = _float(fmt.get("duration")) if isinstance(fmt, dict) else 0.0

    return AudioStreamInfo(
        codec_name=str(stream.get("codec_name") or ""),
        sample_rate=_int(stream.get("sample_rate")),
        channels=_int(stream.get("channels")),
        duration=duration,
    )


def verify_extraction(info: AudioStreamInfo | None, spec: AudioSpec) -> list[str]:
    """Return human-readable mismatches between a probed artifact and ``spec``.

    An empty list means the extraction landed exactly on target. This is checked
    rather than assumed because a silent ffmpeg filter-graph fallback (an
    unsupported channel layout, a resampler refusing a rate) produces a file
    that exists and plays but is not what the ASR provider was promised — the
    kind of defect that otherwise surfaces as mysteriously bad transcripts.
    """
    if info is None:
        return ["no audio stream found in the extracted file"]

    problems: list[str] = []
    expected_codec = "flac" if spec.codec == "flac" else "pcm_s16le"
    if info.codec_name != expected_codec:
        problems.append(f"codec is {info.codec_name or 'unknown'}, expected {expected_codec}")
    if info.sample_rate != spec.sample_rate:
        problems.append(
            f"sample rate is {info.sample_rate or 'unknown'} Hz, expected {spec.sample_rate} Hz"
        )
    if info.channels != spec.channels:
        problems.append(
            f"channel count is {info.channels or 'unknown'}, expected {spec.channels}"
        )
    if info.duration <= 0:
        problems.append("extracted audio has zero duration")
    return problems


def estimate_flac_bytes(duration: float, spec: AudioSpec) -> int:
    """Estimate the extracted file's size, for reporting before a long upload.

    Uses :data:`SPEECH_FLAC_RATIO` against the uncompressed rate. Approximate by
    construction — actual FLAC output varies with content — so it is only ever
    shown to a human, never used to pre-allocate or to decide a code path.
    """
    if duration <= 0:
        return 0
    raw = duration * spec.bytes_per_second
    ratio = SPEECH_FLAC_RATIO if spec.codec == "flac" else 1.0
    return int(raw * ratio)


def format_size_saving(source_bytes: int, audio_bytes: int) -> str:
    """One-line summary of what the extraction saved, for logs and the report."""
    if source_bytes <= 0 or audio_bytes <= 0:
        return "size saving unknown"
    pct = 100.0 * (1.0 - audio_bytes / source_bytes)
    if pct <= 0:
        return (
            f"{_mb(audio_bytes)} audio from {_mb(source_bytes)} source (no saving)"
        )
    return (
        f"{_mb(audio_bytes)} audio from {_mb(source_bytes)} source "
        f"({pct:.1f}% smaller to upload)"
    )


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"
