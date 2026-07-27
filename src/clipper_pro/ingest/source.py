"""Source acquisition — download a remote video or adopt a local one.

Remote downloads delegate to ``clippyme.pipeline.download``, which owns the
platform allow-list, the DNS-rebinding re-check, cookie handling and the
player-client retry chain. That module imports ``yt_dlp`` at its top, so the
import here is deferred into the function: it keeps ``clipper_pro.ingest``
importable — and its pure logic testable — on a host that has no yt-dlp wheel.
"""

from __future__ import annotations

import os

from clipper_pro.errors import IngestError
from clipper_pro.ingest.audio import probe_audio
from clipper_pro.ingest.source_ops import (
    SOURCE_REMOTE,
    classify_source,
    resolve_local_source,
)
from clipper_pro.types import SourceMedia

__all__ = ["acquire_source"]


def acquire_source(
    reference: str,
    work_dir: str,
    *,
    cookies_file: str | None = None,
) -> SourceMedia:
    """Resolve ``reference`` to a local video file and describe it.

    Remote references are downloaded into ``work_dir``; local ones are adopted
    in place and never copied or modified — source media is immutable, and every
    later phase writes derived artifacts elsewhere.

    The returned :class:`~clipper_pro.types.SourceMedia` has an empty
    ``audio_path``; :func:`clipper_pro.ingest.run_ingest` fills it in.
    """
    if classify_source(reference) == SOURCE_REMOTE:
        return _download_remote(reference, work_dir, cookies_file=cookies_file)

    path = resolve_local_source(reference)
    return SourceMedia(
        path=path,
        duration=_duration_of(path),
        title=os.path.splitext(os.path.basename(path))[0],
    )


def _download_remote(url: str, work_dir: str, *, cookies_file: str | None) -> SourceMedia:
    try:
        from clippyme.pipeline.download import (
            download_youtube_video,
            validate_supported_source_url,
        )
    except ImportError as exc:  # pragma: no cover - requires yt-dlp absent
        raise IngestError(
            "remote downloads need the yt-dlp runtime "
            "(pip install -e '.[host-tests]' or run in the backend image)"
        ) from exc

    # Raises ValueError for anything outside the supported-platform allow-list;
    # surfaced as an IngestError so a caller only handles our hierarchy.
    try:
        safe_url = validate_supported_source_url(url)
    except ValueError as exc:
        raise IngestError(str(exc)) from exc

    os.makedirs(work_dir, exist_ok=True)
    path, title = download_youtube_video(safe_url, work_dir, cookies_file)
    if not path or not os.path.isfile(path):
        raise IngestError(f"download did not produce a file for {safe_url}")

    return SourceMedia(
        path=path,
        duration=_duration_of(path),
        # The downloader already sanitised the video title; fall back to the
        # basename only if it handed back an empty one.
        title=title or os.path.splitext(os.path.basename(path))[0],
        url=safe_url,
    )


def _duration_of(path: str) -> float:
    """Container duration in seconds, or 0.0 when it cannot be determined.

    Reads the audio stream's view of the duration, which is the timeline every
    later phase actually works against.
    """
    info = probe_audio(path)
    return info.duration if info else 0.0
