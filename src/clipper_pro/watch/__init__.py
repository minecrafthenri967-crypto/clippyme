"""Channel watcher — poll YouTube uploads feeds and run the pipeline on new ones.

This is a *driver*, not a phase: it decides when to run, then calls
:func:`clipper_pro.pipeline.run_phase` exactly the way the CLI and the web UI do.
Adding it as a fourth phase-like thing to :data:`clipper_pro.pipeline.PHASES`
would have made every front end grow a case for something that is not part of a
run at all.

The loop is deliberately dull, because it is meant to be left alone for days:

* Channels are resolved to canonical ids **once**, at startup. A handle lookup
  needs the network and yt-dlp; doing it per poll would turn a transient DNS
  failure into a dead watcher.
* One video's failure never stops the loop. It is recorded, counted, and retried
  a bounded number of times (see :mod:`clipper_pro.watch.state_ops`).
* Phases already recorded in a run's manifest are skipped. A video retried after
  a render failure does not pay for its transcription and ranking a second time
  — which is the entire reason the manifest is a resume point.
* State is written after every video, atomically, so a crash costs at most the
  one in flight.

What it does **not** do is publish. The watcher's output is a finished workspace
per video; deciding what goes out is a separate step, on purpose.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from clipper_pro.errors import ClipperProError, ValidationError, WatchError
from clipper_pro.pipeline import PHASES, PhaseOptions, run_phase
from clipper_pro.watch import feed as feed_module
from clipper_pro.watch import state as state_module
from clipper_pro.watch.state_ops import (
    CATCHUP_MODES,
    STATUS_FAILED,
    STATUS_OK,
    adopt_backlog,
    is_new_channel,
    record_result,
    run_dir_for,
    select_pending,
    summarize,
)
from clipper_pro.workspace import load as workspace_load
from clipper_pro.workspace import manifest_path

__all__ = [
    "WatchConfig",
    "poll_once",
    "process_video",
    "resolve_channels",
    "run_watch",
]

#: Default seconds between polls. YouTube's uploads feed updates within a few
#: minutes of publication; a quarter hour is well inside that and keeps a
#: day-long watch to a few hundred requests.
DEFAULT_INTERVAL = 900


@dataclass(frozen=True)
class WatchConfig:
    """Everything the loop needs that is not a per-phase override."""

    channels: tuple[str, ...]
    runs_root: str
    interval: int = DEFAULT_INTERVAL
    catchup: str = "live_only"
    max_attempts: int = 3
    once: bool = False
    #: Phases to run per video, in order. Overridable mostly for tests and for a
    #: host that wants to stop before rendering.
    phases: tuple[str, ...] = PHASES

    def __post_init__(self) -> None:
        if not self.channels:
            raise ValidationError(
                "watch needs at least one channel (--channel or CLIPPER_PRO_WATCH_CHANNELS)"
            )
        if self.catchup not in CATCHUP_MODES:
            raise ValidationError(
                f"unknown catchup mode {self.catchup!r} — "
                f"expected one of {', '.join(CATCHUP_MODES)}"
            )
        unknown = [p for p in self.phases if p not in PHASES]
        if unknown:
            raise ValidationError(f"unknown phase(s) to watch: {', '.join(unknown)}")


def _stderr(message: str) -> None:
    """Default logger. stdout stays clean for the JSON summary."""
    print(message, file=sys.stderr, flush=True)


def resolve_channels(
    channels: tuple[str, ...] | list[str], *, log: Callable[[str], None] = _stderr
) -> list[tuple[str, str]]:
    """Resolve each configured channel to ``(original, UC id)``.

    Raises on the first one that cannot be resolved: a watcher started with a
    typo in its channel list should say so immediately rather than run for a week
    watching one of the two channels it was given.
    """
    resolved: list[tuple[str, str]] = []
    for channel in channels:
        channel_id = feed_module.resolve_channel(channel)
        log(f"watching {channel} -> {channel_id}")
        resolved.append((channel, channel_id))
    return resolved


def process_video(
    video: dict[str, str],
    run_dir: str,
    *,
    phases: tuple[str, ...] = PHASES,
    options: PhaseOptions | None = None,
    log: Callable[[str], None] = _stderr,
) -> dict[str, Any]:
    """Run the pipeline for one video and return what to record about it.

    Phases whose artifact is already in the run's manifest are skipped, so a
    retry resumes rather than re-billing. Never raises for a pipeline failure —
    the loop has to survive it — but does let ``KeyboardInterrupt`` through.
    """
    video_id = video.get("id", "")
    url = video.get("url", "")
    done: set[str] = set()
    if os.path.isfile(manifest_path(run_dir)):
        done = set(workspace_load(run_dir).get("artifacts") or {})
        if done:
            log(f"  {video_id}: resuming, already done: {', '.join(sorted(done))}")

    clips = 0
    for phase in phases:
        if phase in done:
            continue
        log(f"  {video_id}: {phase}")
        try:
            payload = run_phase(
                phase, run_dir,
                source=url if phase == "ingest" else None,
                options=options,
            )
        except ClipperProError as exc:
            return {
                "video_id": video_id, "status": STATUS_FAILED,
                "detail": f"{phase}: {exc.detail}", "run_dir": run_dir, "clips": clips,
            }
        except Exception as exc:  # noqa: BLE001 - one video must not kill the loop
            return {
                "video_id": video_id, "status": STATUS_FAILED,
                "detail": f"{phase}: {type(exc).__name__}: {exc}",
                "run_dir": run_dir, "clips": clips,
            }
        if phase == "render":
            clips = int(payload.get("clips") or 0)

    return {
        "video_id": video_id, "status": STATUS_OK,
        "detail": "", "run_dir": run_dir, "clips": clips,
    }


def poll_once(
    resolved: list[tuple[str, str]],
    config: WatchConfig,
    state: dict[str, Any],
    state_path: str,
    *,
    options: PhaseOptions | None = None,
    log: Callable[[str], None] = _stderr,
) -> dict[str, Any]:
    """One pass over every channel. Returns what this cycle did.

    A channel whose feed cannot be fetched is logged and left for the next pass;
    the remaining channels are still polled.
    """
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for original, channel_id in resolved:
        try:
            items = feed_module.fetch_videos(channel_id)
        except (WatchError, ValidationError) as exc:
            log(f"{original}: {exc.detail}")
            errors.append({"channel": original, "detail": exc.detail})
            continue

        if is_new_channel(state, channel_id) and config.catchup == "live_only":
            adopted = adopt_backlog(state, channel_id, items)
            state_module.save(state_path, state)
            log(
                f"{original}: first poll, adopted {adopted} existing upload(s) "
                f"without processing (--catchup backfill to process them)"
            )
            continue

        pending = select_pending(
            state, channel_id, items, max_attempts=config.max_attempts
        )
        if not pending:
            continue
        log(f"{original}: {len(pending)} video(s) to process")

        for video in pending:
            outcome = process_video(
                video,
                run_dir_for(config.runs_root, video.get("id", "")),
                phases=config.phases, options=options, log=log,
            )
            record_result(
                state, channel_id, outcome["video_id"],
                status=outcome["status"], detail=outcome["detail"],
                run_dir=outcome["run_dir"], clips=outcome["clips"],
            )
            state_module.save(state_path, state)
            if outcome["status"] == STATUS_OK:
                log(f"  {outcome['video_id']}: done, {outcome['clips']} clip(s)")
            else:
                log(f"  {outcome['video_id']}: FAILED — {outcome['detail']}")
            processed.append({"channel": original, **outcome})

    return {
        "processed": processed,
        "ok": sum(1 for p in processed if p["status"] == STATUS_OK),
        "failed": sum(1 for p in processed if p["status"] == STATUS_FAILED),
        "channel_errors": errors,
    }


def run_watch(
    config: WatchConfig,
    *,
    options: PhaseOptions | None = None,
    log: Callable[[str], None] = _stderr,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll until interrupted (or once, with ``config.once``) and summarise.

    ``sleep`` is injected so a test can drive several cycles without waiting.
    """
    resolved = resolve_channels(config.channels, log=log)
    state_path = state_module.default_state_path(config.runs_root)
    state = state_module.load(state_path)
    log(f"state: {state_path}")

    cycles = 0
    totals = {"ok": 0, "failed": 0}
    try:
        while True:
            cycle = poll_once(
                resolved, config, state, state_path, options=options, log=log
            )
            cycles += 1
            totals["ok"] += cycle["ok"]
            totals["failed"] += cycle["failed"]
            if config.once:
                break
            sleep(config.interval)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log("interrupted, stopping")

    return {
        "cycles": cycles,
        "channels": [{"input": o, "channel_id": c} for o, c in resolved],
        "runs_root": os.path.abspath(config.runs_root),
        "state_path": state_path,
        "processed_ok": totals["ok"],
        "processed_failed": totals["failed"],
        "totals": summarize(state),
    }
