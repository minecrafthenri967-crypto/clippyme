"""SQLite cache for ranking responses, keyed by provider, model and prompt hash.

Iterating on phases 4-7 means re-running the pipeline over the same source many
times; without this, each pass re-bills a long-context ranking call. The key is
the hash of the exact prompt, so any change to the rubric, the transcript, the
duration bounds or the user's preferences produces a miss — a stale answer can
never be served for a question that changed.

SQLite rather than one file per entry: a single file is easy to delete or ship
with a run, and the table gives cheap introspection ("what did the model say
last Tuesday") without parsing a directory of JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any

__all__ = ["CACHE_FILENAME", "cache_key", "connect", "load", "store"]

CACHE_FILENAME = "rank_cache.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rankings (
    key        TEXT PRIMARY KEY,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    response   TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


def cache_key(prompt: str, provider: str, model: str) -> str:
    """Stable key for one (prompt, provider, model) combination."""
    digest = hashlib.sha256(
        f"{provider}:{model}:{prompt}".encode()
    ).hexdigest()
    return digest[:40]


def connect(cache_dir: str) -> sqlite3.Connection:
    """Open (creating if needed) the ranking cache under ``cache_dir``."""
    os.makedirs(cache_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(cache_dir, CACHE_FILENAME))
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def load(cache_dir: str, key: str) -> dict[str, Any] | None:
    """Return the cached parsed response for ``key``, or ``None`` on any miss.

    A damaged cache counts as a miss rather than an error: paying for the call
    again is strictly better than failing a run over a corrupt row.
    """
    try:
        conn = connect(cache_dir)
    except (OSError, sqlite3.Error):
        return None
    try:
        row = conn.execute(
            "SELECT response FROM rankings WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()

    if not row:
        return None
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def store(
    cache_dir: str, key: str, payload: dict[str, Any], *, provider: str, model: str
) -> None:
    """Persist a parsed response. A cache write failure never fails the run."""
    import time

    try:
        conn = connect(cache_dir)
    except (OSError, sqlite3.Error):
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rankings "
            "(key, provider, model, response, created_at) VALUES (?, ?, ?, ?, ?)",
            (key, provider, model, json.dumps(payload), time.time()),
        )
        conn.commit()
    except (sqlite3.Error, TypeError, ValueError):
        return
    finally:
        conn.close()
