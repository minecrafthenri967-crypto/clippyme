"""YouTube uploads-feed access for the watcher.

Thin adapter over :mod:`clippyme.integrations.youtube_feed`, which already owns
the parts that are easy to get subtly wrong: the ``UC…`` → ``UULF…`` uploads
playlist mapping, an exact host/path allow-list on the feed URL, a
redirect-refusing opener, and a size cap on the response. Re-deriving any of
that here would be a second implementation to keep correct.

Polling the ``UULF`` playlist rather than the channel feed **structurally
excludes Shorts** — the watcher only ever picks up long-form uploads, which is
the only thing there is any point clipping.

The import sits at module top because that module is stdlib-only; the yt-dlp
dependency it needs to resolve a handle is deferred inside its own function, and
surfaces here as a clear error rather than an import failure.
"""

from __future__ import annotations

from clipper_pro.errors import ValidationError, WatchError
from clippyme.integrations.youtube_feed import (
    feed_url,
    fetch_feed,
    parse_feed,
    resolve_channel_id,
    uploads_playlist_id,
)

__all__ = ["fetch_videos", "resolve_channel"]

#: Seconds to wait on the feed. Generous enough for a slow link, short enough
#: that one unreachable channel cannot stall a multi-channel poll for minutes.
DEFAULT_TIMEOUT = 15.0


def resolve_channel(channel: str) -> str:
    """Resolve ``@handle`` / channel URL / ``UC…`` id to a canonical ``UC…`` id.

    A ``UC…`` id needs no network and no yt-dlp; anything else is looked up, so a
    watcher configured with bare handles still starts on a host without the
    downloader installed as long as it is given ids.
    """
    try:
        return resolve_channel_id(channel)
    except ValueError as exc:
        raise ValidationError(f"cannot watch {channel!r}: {exc}") from exc
    except ImportError as exc:  # pragma: no cover - requires yt-dlp absent
        raise WatchError(
            f"resolving the channel {channel!r} needs the yt-dlp runtime — "
            f"install it, or configure the canonical UC… channel id instead ({exc})"
        ) from exc


def fetch_videos(
    channel_id: str, *, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, str]]:
    """Return ``[{id, url}]`` for the channel's recent long-form uploads.

    Newest first, as YouTube orders the feed;
    :func:`clipper_pro.watch.state_ops.select_pending` reverses it.
    """
    try:
        url = feed_url(uploads_playlist_id(channel_id))
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    try:
        xml = fetch_feed(url, timeout=timeout)
    except ValueError as exc:
        # The size cap tripped — a malformed or hostile response, not a network
        # blip, so it is worth distinguishing from the retryable case below.
        raise ValidationError(f"YouTube feed for {channel_id} was unusable: {exc}") from exc
    except (RuntimeError, OSError) as exc:
        # HTTP error, DNS failure, timeout, reset connection. Transient by
        # assumption: the caller logs it and tries again on the next poll.
        raise WatchError(f"could not fetch the feed for {channel_id}: {exc}") from exc

    return parse_feed(xml)
