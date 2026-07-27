"""Run bookkeeping for the local web UI — state, naming, and safe path resolution.

Stdlib-only and host-tested: everything the UI needs to *decide* lives here, so
the FastAPI layer beside it only has to move JSON.

Two decisions worth stating up front.

**Runs default to the home directory, not the working directory.** On WSL a
checkout typically sits under ``/mnt/c/...``, and rendering there is where
ffmpeg's faststart rewrite trips over a Windows file lock ("Unable to re-open
output file for shifting data"). Writing hundreds of megabytes of video across
that boundary is also far slower. The UI therefore puts runs under
``~/clipper-pro-runs`` unless told otherwise, which sidesteps the problem for
anyone who never thinks about it.

**Clips are addressed by index, never by a name from the client.** The browser
asks for clip 3 of run X; this module maps that to a path and then verifies the
result really sits inside that run's ``renders`` directory. A filename taken from
the URL would be a path-traversal read of the user's disk, which is not a risk
worth carrying for a convenience the UI does not need.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from clipper_pro.pipeline import PHASE_LABELS, PHASES

__all__ = [
    "MAX_LOG_LINES",
    "PhaseProgress",
    "RunRecord",
    "RunRegistry",
    "clip_path",
    "default_runs_root",
    "load_report",
    "new_run_id",
    "slugify",
    "workdir_for",
]

#: Ring-buffer size for the live log. A long run emits a few dozen lines; this
#: is generous while still bounding memory for a server left running for days.
MAX_LOG_LINES = 400

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(text: str, *, max_length: int = 40) -> str:
    """Filesystem-safe fragment of ``text``, or ``"run"`` when nothing survives."""
    cleaned = _UNSAFE.sub("-", str(text or "")).strip("-._")
    cleaned = cleaned[:max_length].strip("-._")
    return cleaned or "run"


def new_run_id() -> str:
    """Sortable, unique run id: ``20260727-104500-a1b2c3``.

    Time-prefixed so a directory listing reads chronologically, with a random
    tail so two runs started in the same second cannot collide.
    """
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def default_runs_root() -> str:
    """Where runs live unless the caller says otherwise (see module docstring)."""
    return os.path.join(os.path.expanduser("~"), "clipper-pro-runs")


def workdir_for(root: str, run_id: str, source: str = "") -> str:
    """Per-run workspace directory, named ``<run-id>-<source-slug>``."""
    hint = slugify(os.path.basename(source.rstrip("/")) or source) if source else ""
    name = f"{run_id}-{hint}" if hint and hint != "run" else run_id
    return os.path.join(root, name)


@dataclass
class PhaseProgress:
    """One phase's state within a run."""

    name: str
    label: str
    status: str = "pending"  # pending | running | done | failed | skipped
    detail: str = ""
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "seconds": round(self.seconds, 1),
        }


@dataclass
class RunRecord:
    """Everything the UI knows about one pipeline run.

    Mutated by the worker thread and read by request handlers, so all access goes
    through :class:`RunRegistry`, which owns the lock.
    """

    id: str
    source: str
    work_dir: str
    status: str = "queued"  # queued | running | done | failed
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    phases: list[PhaseProgress] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, run_id: str, source: str, work_dir: str) -> RunRecord:
        return cls(
            id=run_id, source=source, work_dir=work_dir,
            phases=[
                PhaseProgress(name=p, label=PHASE_LABELS.get(p, p)) for p in PHASES
            ],
        )

    @property
    def completed_phases(self) -> int:
        return sum(1 for p in self.phases if p.status == "done")

    @property
    def progress(self) -> float:
        """Fraction of phases finished, 0.0–1.0."""
        return self.completed_phases / len(self.phases) if self.phases else 0.0

    def phase(self, name: str) -> PhaseProgress | None:
        return next((p for p in self.phases if p.name == name), None)

    def append_log(self, line: str) -> None:
        """Record a log line, discarding the oldest once the buffer is full."""
        text = line.rstrip()
        if not text:
            return
        self.log.append(text)
        if len(self.log) > MAX_LOG_LINES:
            del self.log[: len(self.log) - MAX_LOG_LINES]

    def to_dict(self, *, include_log: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "work_dir": self.work_dir,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "progress": round(self.progress, 3),
            "completed_phases": self.completed_phases,
            "total_phases": len(self.phases),
            "phases": [p.to_dict() for p in self.phases],
            "results": self.results,
        }
        if include_log:
            payload["log"] = list(self.log)
        return payload


class RunRegistry:
    """Thread-safe store of runs, newest first.

    A private tool renders one video at a time — ffmpeg would saturate the CPU
    anyway — so the registry also enforces that only one run is active, which
    keeps progress reporting and the captured log unambiguous.
    """

    def __init__(self, runs_root: str | None = None) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._order: list[str] = []
        self.runs_root = runs_root or default_runs_root()

    def create(self, source: str) -> RunRecord:
        with self._lock:
            run_id = new_run_id()
            record = RunRecord.create(
                run_id, source, workdir_for(self.runs_root, run_id, source)
            )
            self._runs[run_id] = record
            self._order.insert(0, run_id)
            return record

    def rehydrate(self) -> int:
        """Re-adopt finished runs already on disk; returns how many were found.

        The registry is in memory, so without this a restart would look like the
        user's clips had vanished — they are still in ``runs_root``, but nothing
        would point at them. Only completed phases are reconstructed, from the
        workspace manifest each phase writes; a run interrupted by the restart
        comes back as failed rather than as one that will never finish.
        """
        if not os.path.isdir(self.runs_root):
            return 0

        found = 0
        for name in sorted(os.listdir(self.runs_root), reverse=True):
            work_dir = os.path.join(self.runs_root, name)
            manifest = os.path.join(work_dir, "workspace.json")
            if not os.path.isfile(manifest):
                continue
            # Directory names are "<run-id>-<slug>"; the id is the first three
            # dash-separated parts (date-time-random).
            parts = name.split("-")
            run_id = "-".join(parts[:3]) if len(parts) >= 3 else name
            with self._lock:
                if run_id in self._runs:
                    continue

            record = self._adopt(run_id, work_dir, manifest)
            if record is None:
                continue
            with self._lock:
                self._runs[run_id] = record
                self._order.append(run_id)
            found += 1

        with self._lock:
            # Newest first, matching the ordering `create` maintains.
            self._order.sort(reverse=True)
        return found

    def _adopt(self, run_id: str, work_dir: str, manifest_path: str) -> RunRecord | None:
        try:
            with open(manifest_path) as fh:
                artifacts = (json.load(fh) or {}).get("artifacts") or {}
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(artifacts, dict) or not artifacts:
            return None

        source = ""
        ingest = artifacts.get("ingest")
        if isinstance(ingest, dict) and isinstance(ingest.get("source"), dict):
            source = ingest["source"].get("url") or ingest["source"].get("path") or ""

        record = RunRecord.create(run_id, source, work_dir)
        for phase in record.phases:
            payload = artifacts.get(phase.name)
            if isinstance(payload, dict):
                phase.status = "done"
        record.results = {k: v for k, v in artifacts.items() if isinstance(v, dict)}
        complete = all(p.status == "done" for p in record.phases)
        record.status = "done" if complete else "failed"
        if not complete:
            record.error = "interrupted — the server restarted before this run finished"
        with contextlib.suppress(OSError):
            record.created_at = os.path.getmtime(manifest_path)
            record.finished_at = record.created_at
        return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, limit: int = 25) -> list[RunRecord]:
        with self._lock:
            return [self._runs[i] for i in self._order[:limit]]

    def active(self) -> RunRecord | None:
        """The run currently executing, if any."""
        with self._lock:
            return next(
                (r for r in self._runs.values() if r.status in ("queued", "running")),
                None,
            )

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            for key, value in fields.items():
                setattr(record, key, value)

    def snapshot(self, run_id: str, *, include_log: bool = True) -> dict[str, Any] | None:
        """A consistent copy for a response, taken under the lock."""
        with self._lock:
            record = self._runs.get(run_id)
            return record.to_dict(include_log=include_log) if record else None

    @property
    def lock(self) -> threading.RLock:
        return self._lock


# --- reading a finished run's output ---------------------------------------


def load_report(work_dir: str) -> dict[str, Any] | None:
    """Phase 7's draft report, or ``None`` when it has not been written."""
    path = os.path.join(work_dir, "reports", "draft.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def clip_path(work_dir: str, index: int) -> str | None:
    """Absolute path of the report's ``index``-th clip, if it is safe to serve.

    Returns ``None`` for an unknown index, a clip that was never rendered, or —
    the case that matters — a path that resolves outside the run's ``renders``
    directory. The report is written by this pipeline, but it is still a file on
    disk that could have been edited, so its paths are treated as claims to
    verify rather than facts.
    """
    report = load_report(work_dir)
    if not report:
        return None
    clips = report.get("clips")
    if not isinstance(clips, list) or not 0 <= index < len(clips):
        return None
    entry = clips[index]
    if not isinstance(entry, dict):
        return None
    raw = entry.get("file")
    if not raw:
        return None

    renders_root = os.path.realpath(os.path.join(work_dir, "renders"))
    resolved = os.path.realpath(os.path.join(work_dir, str(raw)))
    if os.path.commonpath([renders_root, resolved]) != renders_root:
        return None
    return resolved if os.path.isfile(resolved) else None
