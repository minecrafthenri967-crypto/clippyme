"""Persistence for the watch state — the file that makes the watcher durable.

A watcher whose memory lived in the process would re-adopt its whole backlog
after every restart, or worse, re-process it. This module is the disk half of
:mod:`clipper_pro.watch.state_ops`: it loads the record of what has already been
handled and writes it back after each video, atomically, so a crash between two
videos costs at most the one in flight.

Writes follow the repository's atomic-write rule (tmp + ``os.replace``, mode
0o600). The state names channels and videos a user watches, which is not a
secret but is not the world's business either.
"""

from __future__ import annotations

import json
import os
from typing import Any

from clipper_pro.errors import ValidationError
from clipper_pro.watch.state_ops import SCHEMA_VERSION, init_state

__all__ = ["STATE_NAME", "default_state_path", "load", "save"]

STATE_NAME = "watch-state.json"


def default_state_path(runs_root: str) -> str:
    """Where the watch state lives: beside the runs it produced."""
    return os.path.join(runs_root, STATE_NAME)


def load(path: str) -> dict[str, Any]:
    """Read the state at ``path``, or a fresh one when it does not exist yet.

    A missing file is the normal first-run case, not an error. A corrupt one *is*
    an error: silently starting over would re-process every video in every feed,
    which is exactly the surprise bill this package tries to avoid.
    """
    if not os.path.isfile(path):
        return init_state()
    try:
        with open(path) as fh:
            state: dict[str, Any] = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"watch state {path} is not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ValidationError(f"watch state {path} is not an object")

    version = state.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValidationError(
            f"watch state at {path} uses schema version {version}, "
            f"this build reads version {SCHEMA_VERSION}"
        )
    state.setdefault("channels", {})
    return state


def save(path: str, state: dict[str, Any]) -> str:
    """Write the state atomically and return its path."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path
