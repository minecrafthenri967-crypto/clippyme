"""Run workspace: a directory layout plus a manifest of what each phase produced.

Modelled on the video-edit-cli workspace contract so a clipper-pro run directory
can be handed straight to that CLI (and to an agent reading its plans) without a
translation step. Source media is registered, never mutated; everything a phase
derives lands in its own subdirectory next to a provenance sidecar.

The manifest is the run's resume point: a phase records its artifacts here, so a
re-run can skip work that already landed on disk instead of re-downloading a
gigabyte or re-billing a transcription.
"""

from __future__ import annotations

import json
import os
from typing import Any

from clipper_pro import SCHEMA_VERSION
from clipper_pro.errors import ValidationError

__all__ = ["MANIFEST_NAME", "SUBDIRS", "init", "load", "record_artifact", "save"]

MANIFEST_NAME = "workspace.json"

#: One directory per stage of the run. Created up front so a phase never has to
#: guess whether its output location exists.
SUBDIRS = (
    "sources",   # phase 1 — downloaded media, treated as immutable
    "audio",     # phase 1 — extracted mono 16 kHz FLAC
    "analysis",  # phases 2-3 — transcripts, audio events, silences, rankings
    "plans",     # phase 4 — edit plans in the video-edit-cli JSON contract
    "renders",   # phase 6 — rendered vertical clips
    "reports",   # phase 7 — the scored draft report handed to the editor
)


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST_NAME)


def init(root: str, *, exist_ok: bool = True) -> dict[str, Any]:
    """Create the workspace directories and manifest under ``root``.

    Returns the manifest. With ``exist_ok`` (the default) an existing workspace
    is loaded rather than clobbered, which is what makes a run resumable; pass
    ``exist_ok=False`` to require a fresh directory.
    """
    path = manifest_path(root)
    if os.path.isfile(path):
        if not exist_ok:
            raise ValidationError(f"workspace already exists at {root}")
        return load(root)

    os.makedirs(root, exist_ok=True)
    for name in SUBDIRS:
        os.makedirs(os.path.join(root, name), exist_ok=True)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "root": os.path.abspath(root),
        "artifacts": {},
    }
    save(root, manifest)
    return manifest


def load(root: str) -> dict[str, Any]:
    """Read the manifest at ``root``, rejecting one this version cannot read."""
    path = manifest_path(root)
    if not os.path.isfile(path):
        raise ValidationError(f"no workspace manifest at {path}")
    try:
        with open(path) as fh:
            manifest: dict[str, Any] = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"workspace manifest {path} is not valid JSON: {exc}") from exc

    version = manifest.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValidationError(
            f"workspace at {root} uses schema version {version}, "
            f"this build reads version {SCHEMA_VERSION}"
        )
    manifest.setdefault("artifacts", {})
    return manifest


def save(root: str, manifest: dict[str, Any]) -> str:
    """Write the manifest atomically (tmp + replace) and return its path."""
    path = manifest_path(root)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def record_artifact(root: str, phase: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record ``phase``'s output in the manifest and persist it.

    Later phases read this to find what came before; a resumed run reads it to
    decide what it can skip.
    """
    if not phase:
        raise ValidationError("phase name is required to record an artifact")
    manifest = load(root)
    manifest["artifacts"][phase] = payload
    save(root, manifest)
    return manifest
