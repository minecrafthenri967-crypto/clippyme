"""Watch-state decisions: which videos a poll should process, and what to record.

Stdlib-only and host-tested — everything the watcher *decides* lives here, so the
modules beside it only have to fetch a feed, write a file, and run the phases.

Three policies are worth stating up front, because all three exist to stop an
unattended watcher from spending money by surprise.

**First sight of a channel processes nothing by default.** A YouTube uploads
feed carries roughly the last fifteen videos. Treating those as "new" the first
time a channel is configured would kick off fifteen full runs — fifteen
downloads, fifteen transcriptions, fifteen ranking calls — for someone who only
wanted to catch the *next* upload. ``live_only`` therefore adopts whatever is
already in the feed as seen and waits; ``backfill`` is the explicit opt-in that
says yes, work through the backlog.

**A failed video is retried a bounded number of times, then left alone.** Never
recording a failure would retry it on every poll forever, re-billing the API each
time. Recording it as finished on the first failure would silently drop a video
over one flaky download. Both are wrong, so attempts are counted and capped.

**A video id from the feed is never trusted as a path component.** It picks the
run directory, so it is re-validated here against the eleven-character YouTube
alphabet rather than relying on the parser upstream having done it.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from clipper_pro.errors import ValidationError

__all__ = [
    "CATCHUP_MODES",
    "SCHEMA_VERSION",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "adopt_backlog",
    "channel_entry",
    "init_state",
    "is_new_channel",
    "parse_channels",
    "record_result",
    "run_dir_for",
    "select_pending",
    "summarize",
    "validate_video_id",
]

SCHEMA_VERSION = 1

#: What to do with the videos already in the feed when a channel is first seen.
#: See the module docstring for why ``live_only`` is the default.
CATCHUP_MODES = ("live_only", "backfill")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

#: Statuses that mean "do not process this video again". A failure is absent on
#: purpose — whether to retry it depends on the attempt count, not the status.
_TERMINAL = (STATUS_OK, STATUS_SKIPPED)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_SPLIT_RE = re.compile(r"[,\s]+")


def parse_channels(raw: str | None) -> list[str]:
    """Split a comma/whitespace-separated channel list, de-duplicated in order.

    Accepts whatever :func:`clipper_pro.watch.feed.resolve_channel` accepts —
    ``@handle``, a channel URL, or a canonical ``UC…`` id — because resolution
    needs the network and this does not.
    """
    seen: set[str] = set()
    out: list[str] = []
    for part in _SPLIT_RE.split((raw or "").strip()):
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return out


def validate_video_id(video_id: str) -> str:
    """Return ``video_id`` if it is a YouTube id, else raise.

    Called before the id is used as a directory name; see the module docstring.
    """
    vid = (video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(vid):
        raise ValidationError(f"not a YouTube video id: {video_id!r}")
    return vid


def run_dir_for(runs_root: str, video_id: str) -> str:
    """Workspace directory for one video, named after its id.

    The id rather than a timestamp, so a re-run of the same video reuses the
    workspace instead of re-downloading it: the manifest already knows which
    phases landed, and that is the whole point of it being a resume point.
    """
    return os.path.abspath(os.path.join(runs_root, validate_video_id(video_id)))


def init_state() -> dict[str, Any]:
    """A fresh, empty watch state."""
    return {"schema_version": SCHEMA_VERSION, "channels": {}}


def channel_entry(state: dict[str, Any], channel_id: str) -> dict[str, Any]:
    """The per-channel record, created empty on first access."""
    if not _CHANNEL_ID_RE.fullmatch((channel_id or "").strip()):
        raise ValidationError(f"not a canonical UC channel id: {channel_id!r}")
    channels = state.setdefault("channels", {})
    entry = channels.setdefault(channel_id, {})
    entry.setdefault("videos", {})
    return entry


def is_new_channel(state: dict[str, Any], channel_id: str) -> bool:
    """True when this channel has never been polled before.

    Drives the catch-up decision, so it asks whether the channel was *recorded*,
    not whether it has any videos — a channel adopted with an empty feed has
    still been seen.
    """
    return channel_id not in (state.get("channels") or {})


def select_pending(
    state: dict[str, Any],
    channel_id: str,
    items: list[dict[str, str]],
    *,
    max_attempts: int = 3,
) -> list[dict[str, str]]:
    """Feed entries this poll should process, oldest first.

    ``items`` arrives newest-first (the feed's order); a backlog reads better
    processed chronologically, and for a single new upload the order is moot.
    Entries already finished are dropped, and a previously failed one is included
    only while it is under ``max_attempts``.
    """
    videos = (state.get("channels") or {}).get(channel_id, {}).get("videos") or {}
    pending = []
    for item in reversed(items):
        video_id = (item.get("id") or "").strip()
        if not _VIDEO_ID_RE.fullmatch(video_id):
            continue
        record = videos.get(video_id)
        if record is None:
            pending.append(item)
            continue
        if record.get("status") in _TERMINAL:
            continue
        if int(record.get("attempts") or 0) < max_attempts:
            pending.append(item)
    return pending


def adopt_backlog(
    state: dict[str, Any],
    channel_id: str,
    items: list[dict[str, str]],
    *,
    now: float | None = None,
) -> int:
    """Mark everything currently in the feed as seen without processing it.

    This is what ``live_only`` does on first sight of a channel. Returns how many
    entries were adopted.
    """
    entry = channel_entry(state, channel_id)
    stamp = time.time() if now is None else now
    entry.setdefault("first_seen_at", stamp)
    videos = entry["videos"]
    adopted = 0
    for item in items:
        video_id = (item.get("id") or "").strip()
        if not _VIDEO_ID_RE.fullmatch(video_id) or video_id in videos:
            continue
        videos[video_id] = {
            "status": STATUS_SKIPPED,
            "attempts": 0,
            "updated_at": stamp,
            "detail": "already published when this channel was first watched",
        }
        adopted += 1
    return adopted


def record_result(
    state: dict[str, Any],
    channel_id: str,
    video_id: str,
    *,
    status: str,
    detail: str = "",
    run_dir: str = "",
    clips: int = 0,
    now: float | None = None,
) -> dict[str, Any]:
    """Record one video's outcome, incrementing its attempt count.

    Mutates and returns ``state`` — the caller persists it, the same way
    :func:`clipper_pro.workspace.record_artifact` works.
    """
    if status not in (STATUS_OK, STATUS_FAILED, STATUS_SKIPPED):
        raise ValidationError(f"unknown watch status: {status!r}")
    entry = channel_entry(state, channel_id)
    stamp = time.time() if now is None else now
    entry.setdefault("first_seen_at", stamp)
    videos = entry["videos"]
    previous = videos.get(validate_video_id(video_id)) or {}
    videos[video_id] = {
        "status": status,
        "attempts": int(previous.get("attempts") or 0) + 1,
        "updated_at": stamp,
        "detail": detail,
        "run_dir": run_dir,
        "clips": clips,
    }
    return state


def summarize(state: dict[str, Any]) -> dict[str, int]:
    """Counts per status across every channel, for a status line or a summary."""
    totals = {STATUS_OK: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0}
    for entry in (state.get("channels") or {}).values():
        for record in (entry.get("videos") or {}).values():
            status = record.get("status")
            if status in totals:
                totals[status] += 1
    return totals
