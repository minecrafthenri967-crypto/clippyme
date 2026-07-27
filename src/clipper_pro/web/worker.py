"""Background execution of a full run for the web UI.

The seven phases take minutes, so a browser cannot wait on one request. The
worker runs them on a thread, updating the :class:`~clipper_pro.web.runs.RunRecord`
as it goes; the browser polls that record.

Log capture works by teeing ``sys.stderr`` for the duration of a run, because
that is where the phases already write their progress ("✂️ clip 1: start
327.06→326.69s"). Reproducing those messages through a second logging path would
mean maintaining two descriptions of the same events. The tee keeps writing to
the real stderr, so a terminal running the server still shows everything.

That swap is process-global, which is safe here only because the registry admits
one active run at a time; a line another thread logs mid-run is copied into that
run's log, which is harmless noise rather than lost output.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from clipper_pro.errors import ClipperProError
from clipper_pro.pipeline import PHASES, PhaseOptions, run_phase
from clipper_pro.web.runs import RunRecord, RunRegistry

__all__ = ["RunWorker", "execute_run"]

#: Injection point for tests: the real orchestrator by default.
PhaseRunner = Callable[..., dict[str, Any]]


class _LogTee:
    """A writable stream that forwards to ``original`` and into ``sink``."""

    def __init__(self, original: Any, sink: Callable[[str], None]) -> None:
        self._original = original
        self._sink = sink
        self._pending = ""

    def write(self, text: str) -> int:
        # Phases print whole lines, but a partial write must not become a log
        # entry of its own — buffer until a newline arrives.
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            with contextlib.suppress(Exception):
                self._sink(line)
        with contextlib.suppress(Exception):
            self._original.write(text)
        return len(text)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self._original.flush()

    def isatty(self) -> bool:
        # Some libraries probe this before emitting colour; answer honestly for
        # the underlying stream rather than raising AttributeError.
        return bool(getattr(self._original, "isatty", lambda: False)())


def execute_run(
    registry: RunRegistry,
    record: RunRecord,
    options: PhaseOptions,
    *,
    phase_runner: PhaseRunner | None = None,
    phases: tuple[str, ...] = PHASES,
) -> None:
    """Run every phase in order, recording progress on ``record``.

    Never raises: a phase failure is recorded on the run and the sequence stops,
    because every later phase depends on the one before it. The browser learns
    what happened by polling, so an exception escaping here would strand the UI
    with a run stuck at "running" forever.

    ``phase_runner`` defaults to :func:`~clipper_pro.pipeline.run_phase`, resolved
    here rather than in the signature: a default argument is bound once at import,
    which would silently ignore anyone substituting the module attribute.
    """
    phase_runner = phase_runner or run_phase
    with registry.lock:
        record.status = "running"
        record.error = ""

    tee = _LogTee(sys.stderr, lambda line: _append(registry, record, line))
    failed = False

    original_stderr = sys.stderr
    sys.stderr = tee  # type: ignore[assignment]
    try:
        for name in phases:
            progress = record.phase(name)
            started = time.time()
            with registry.lock:
                if progress is not None:
                    progress.status = "running"

            try:
                payload = phase_runner(
                    name,
                    record.work_dir,
                    source=record.source if name == "ingest" else None,
                    options=options,
                )
            except ClipperProError as exc:
                _fail(registry, record, progress, name, exc.detail, started)
                failed = True
                break
            except Exception as exc:  # noqa: BLE001 - a worker thread must not die silently
                _fail(
                    registry, record, progress, name,
                    f"unexpected {type(exc).__name__}: {exc}", started,
                )
                failed = True
                break

            with registry.lock:
                record.results[name] = payload
                if progress is not None:
                    progress.status = "done"
                    progress.seconds = time.time() - started
                    progress.detail = _summarise(name, payload)
    finally:
        sys.stderr = original_stderr
        with registry.lock:
            record.status = "failed" if failed else "done"
            record.finished_at = time.time()


def _append(registry: RunRegistry, record: RunRecord, line: str) -> None:
    with registry.lock:
        record.append_log(line)


def _fail(
    registry: RunRegistry,
    record: RunRecord,
    progress: Any,
    name: str,
    detail: str,
    started: float,
) -> None:
    with registry.lock:
        if progress is not None:
            progress.status = "failed"
            progress.detail = detail
            progress.seconds = time.time() - started
        record.error = f"{name}: {detail}"
        record.append_log(f"ERROR in {name}: {detail}")


def _summarise(phase: str, payload: dict[str, Any]) -> str:
    """A short line per finished phase, for the progress list in the UI."""
    if phase == "ingest":
        return str(payload.get("saving") or "")
    if phase == "transcribe":
        words, speakers = payload.get("words", 0), payload.get("speakers") or []
        return f"{words} words, {len(speakers)} speaker(s)"
    if phase in ("rank", "cut", "reframe", "render"):
        count = payload.get("clips", 0)
        return f"{count} clip{'' if count == 1 else 's'}"
    if phase == "export":
        return f"{payload.get('rendered', 0)} rendered"
    return ""


class RunWorker:
    """Starts runs on background threads, one at a time."""

    def __init__(self, registry: RunRegistry) -> None:
        self._registry = registry
        self._thread: threading.Thread | None = None

    def start(self, record: RunRecord, options: PhaseOptions) -> None:
        """Begin ``record`` on a daemon thread.

        Daemon so that quitting the server with Ctrl-C does not hang waiting for
        an hour-long render; the workspace on disk is the durable record, and a
        half-finished run can simply be started again.
        """
        thread = threading.Thread(
            target=execute_run,
            args=(self._registry, record, options),
            name=f"clipper-pro-run-{record.id}",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the current run — used by tests, not by request handling."""
        if self._thread is not None:
            self._thread.join(timeout)
