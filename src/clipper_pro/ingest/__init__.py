"""Phase 1 — smart ingestion and audio extraction.

Takes a URL or a local path and returns a :class:`~clipper_pro.types.SourceMedia`
whose ``audio_path`` points at a mono 16 kHz FLAC. Nothing downstream reads the
video again until phase 6 renders it.

    >>> from clipper_pro.ingest import run_ingest
    >>> media = run_ingest("https://youtu.be/...", "/work/run-1")   # doctest: +SKIP
    >>> media.audio_path                                            # doctest: +SKIP
    '/work/run-1/audio/audio_My_Talk.flac'

Module layout follows the repository's purity rule: ``*_ops`` modules are
stdlib-only and host-tested, the others shell out.
"""

from __future__ import annotations

import os
from dataclasses import replace

from clipper_pro.config import Settings
from clipper_pro.ingest.audio import (
    describe_saving,
    extract_analysis_audio,
    probe_audio,
)
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
from clipper_pro.ingest.source import acquire_source
from clipper_pro.ingest.source_ops import classify_source, resolve_local_source
from clipper_pro.types import SourceMedia

__all__ = [
    "AudioSpec",
    "AudioStreamInfo",
    "acquire_source",
    "asr_output_name",
    "build_extract_command",
    "build_probe_command",
    "classify_source",
    "describe_saving",
    "estimate_flac_bytes",
    "extract_analysis_audio",
    "format_size_saving",
    "parse_audio_stream",
    "probe_audio",
    "resolve_local_source",
    "run_ingest",
    "spec_from_settings",
    "verify_extraction",
]


def spec_from_settings(settings: Settings) -> AudioSpec:
    """Build the extraction target from resolved configuration."""
    return AudioSpec(
        sample_rate=settings.asr_sample_rate,
        channels=settings.asr_channels,
        codec="flac",
        compression_level=settings.flac_compression,
    )


def run_ingest(
    reference: str,
    work_dir: str,
    *,
    settings: Settings | None = None,
    cookies_file: str | None = None,
    overwrite: bool = False,
) -> SourceMedia:
    """Run phase 1 end to end: acquire the source, then extract analysis audio.

    ``work_dir`` gets a ``sources/`` subdirectory for downloads and an ``audio/``
    one for extracted FLAC, matching the workspace layout in
    :mod:`clipper_pro.workspace`. A local source is read in place, so only the
    audio directory is written to.
    """
    settings = settings or Settings.from_env()
    spec = spec_from_settings(settings)

    media = acquire_source(
        reference,
        os.path.join(work_dir, "sources"),
        cookies_file=cookies_file,
    )
    audio_path = extract_analysis_audio(
        media.path,
        os.path.join(work_dir, "audio"),
        spec,
        timeout=settings.ffmpeg_timeout,
        overwrite=overwrite,
    )
    return replace(media, audio_path=audio_path)
