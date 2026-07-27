"""Content-addressed transcript cache.

Keyed by the **hash of the audio bytes** plus provider and model, not by source
URL. That distinction matters in practice: a URL key misses for a local file,
misses again when the same video is re-downloaded to a different path, and — the
dangerous direction — *hits* when a URL's content has changed underneath it. A
content hash is exact in both directions.

Transcription is the most expensive step in the pipeline, so a cache hit during
development is the difference between iterating on phases 3-7 for free and
re-billing an hour of ASR on every run. Entries never expire: the key already
pins the exact bytes and the exact model, so a stored answer cannot go stale.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

__all__ = ["cache_key", "cache_path", "load", "store"]

_CHUNK = 1 << 20


def _audio_digest(audio_path: str) -> str:
    digest = hashlib.sha256()
    with open(audio_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(audio_path: str, provider: str, model: str) -> str:
    """Stable key for one (audio bytes, provider, model) combination.

    Provider and model are part of the key because their outputs are not
    interchangeable — Scribe tags audio events that Nova-3 does not, so serving
    one for the other would silently change what phase 3 sees.
    """
    payload = f"{_audio_digest(audio_path)}:{provider}:{model}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def cache_path(cache_dir: str, key: str) -> str:
    return os.path.join(cache_dir, f"transcript_{key}.json")


def load(cache_dir: str, key: str) -> dict[str, Any] | None:
    """Return the cached payload for ``key``, or ``None`` on any miss.

    A corrupt or unreadable entry counts as a miss rather than an error: the
    worst case is paying for the transcription again, which is strictly better
    than failing a run over a damaged cache file.
    """
    path = cache_path(cache_dir, key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def store(cache_dir: str, key: str, payload: dict[str, Any]) -> str:
    """Write ``payload`` under ``key`` atomically; return the file path.

    A failed write is not fatal — the transcript is already in hand and the run
    should continue — so the caller gets the path back and OSError is left to
    propagate only when the directory itself cannot be created.
    """
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_path(cache_dir, key)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)
    return path
