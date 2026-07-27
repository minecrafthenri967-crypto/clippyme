"""Pure helpers for resolving what the user handed us as a source.

Deliberately does *not* re-implement URL validation. The host repository already
owns a hardened allow-list plus a DNS-rebinding guard in
``clippyme.pipeline.download``; duplicating it here would mean two policies to
keep in sync and one of them silently going stale. This module only answers the
structural question — "is this a remote URL or a file on disk?" — and leaves
the security decision to the single place that owns it.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from clipper_pro.errors import ValidationError

__all__ = ["SOURCE_LOCAL", "SOURCE_REMOTE", "classify_source", "resolve_local_source"]

SOURCE_LOCAL = "local"
SOURCE_REMOTE = "remote"

#: Containers the pipeline will accept from disk. yt-dlp downloads are not
#: filtered by extension — the downloader controls its own output format.
_VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"})


def classify_source(reference: str) -> str:
    """Return :data:`SOURCE_REMOTE` or :data:`SOURCE_LOCAL` for a source string.

    Anything carrying an ``http``/``https`` scheme is remote and must go through
    the downloader (and therefore through the host's URL allow-list). Everything
    else is treated as a path. A scheme we do not handle — ``file://``,
    ``ftp://``, ``data:`` — is rejected outright rather than being coerced into
    a path, since coercion is how a "path" ends up being fetched.
    """
    raw = (reference or "").strip()
    if not raw:
        raise ValidationError("source reference is empty")

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme in ("http", "https"):
        return SOURCE_REMOTE
    # A bare Windows path ("C:\\videos\\a.mp4") parses with scheme "c"; a single
    # letter is never a real URL scheme, so treat it as the path it is.
    if scheme and len(scheme) > 1:
        raise ValidationError(
            f"unsupported source scheme {scheme!r} — pass an https URL or a local file path"
        )
    return SOURCE_LOCAL


def resolve_local_source(reference: str) -> str:
    """Validate and normalise a local source path.

    Returns the absolute path. Raises :class:`ValidationError` when the file is
    missing, is a directory, or carries an extension the pipeline does not read.
    """
    path = os.path.abspath(os.path.expanduser((reference or "").strip()))
    if not os.path.exists(path):
        raise ValidationError(f"source file not found: {path}")
    if not os.path.isfile(path):
        raise ValidationError(f"source is not a file: {path}")
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in _VIDEO_SUFFIXES:
        raise ValidationError(
            f"unsupported source container {suffix or '<none>'} — "
            f"expected one of {', '.join(sorted(_VIDEO_SUFFIXES))}"
        )
    return path
