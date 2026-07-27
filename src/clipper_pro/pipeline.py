"""Phase orchestration shared by every front end.

One function — :func:`run_phase` — drives one phase of the pipeline: it resolves
settings, loads whatever the previous phases recorded in the workspace, calls the
phase, records the result, and returns the summary payload.

This lives here rather than in :mod:`clipper_pro.cli` because there is now more
than one way to drive a run (the CLI and the local web UI). Two front ends each
carrying their own copy of "load the transcript, then call run_rank, then record
the artifact" would be two implementations to keep in step, and they would drift
the first time a phase gained an argument. Front ends translate *input* — argv,
an HTTP body — into :class:`PhaseOptions` and then get out of the way.

Heavy phase modules are imported inside each branch, not at module top, so
importing this module stays cheap and a missing optional runtime only surfaces
when the phase that needs it actually runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from clipper_pro.config import Settings
from clipper_pro.errors import ValidationError
from clipper_pro.ingest import describe_saving, run_ingest, spec_from_settings
from clipper_pro.ingest.audio_ops import estimate_flac_bytes
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import Candidate, SourceMedia
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import load as workspace_load
from clipper_pro.workspace import record_artifact

if TYPE_CHECKING:  # heavy phase modules stay lazily imported at runtime
    from clipper_pro.reframe.plan import CameraPlan

__all__ = [
    "PHASES",
    "PhaseOptions",
    "media_from_workspace",
    "renders_from_workspace",
    "run_phase",
    "snapped_or_ranked",
    "transcript_from_workspace",
]

#: The pipeline in order. A front end can iterate this to run everything.
PHASES: tuple[str, ...] = (
    "ingest",
    "transcribe",
    "rank",
    "cut",
    "reframe",
    "render",
    "export",
)

#: Human-readable one-liners, so a UI need not hard-code its own copy.
PHASE_LABELS: dict[str, str] = {
    "ingest": "Download & extract audio",
    "transcribe": "Transcribe speech",
    "rank": "Score viral moments",
    "cut": "Snap clip edges",
    "reframe": "Plan 9:16 framing",
    "render": "Render clips",
    "export": "Write draft report",
}


@dataclass(frozen=True)
class PhaseOptions:
    """Per-run overrides, mapped 1:1 onto the CLI flags.

    Every field defaults to "use the configured behaviour", so a caller only sets
    what it actually wants to change. ``None`` means "leave it to
    :class:`~clipper_pro.config.Settings`"; a ``False`` boolean means "do not opt
    out of the default".
    """

    # phase 1
    sample_rate: int | None = None
    cookies: str | None = None
    overwrite: bool = False
    # phase 2
    transcribe_provider: str | None = None
    require_events: bool = False
    no_transcript_cache: bool = False
    # phase 3
    rank_provider: str | None = None
    max_clips: int | None = None
    instructions: str | None = None
    no_rank_cache: bool = False
    # phase 4
    no_silence: bool = False
    # phase 5
    centred: bool = False
    # phase 6
    crf: int | None = None


# --- workspace loaders ------------------------------------------------------
# Each phase reads what the previous one recorded, so a caller never threads
# paths between steps.


def media_from_workspace(work_dir: str) -> SourceMedia:
    """Recover phase 1's source record."""
    artifacts = workspace_load(work_dir).get("artifacts") or {}
    ingest = artifacts.get("ingest")
    if not isinstance(ingest, dict) or not isinstance(ingest.get("source"), dict):
        raise ValidationError(
            f"no ingest artifact in {work_dir} — run 'clipper-pro ingest' first"
        )
    return SourceMedia.from_dict(ingest["source"])


def transcript_from_workspace(work_dir: str) -> TranscriptResult:
    """Recover phase 2's transcript artifact."""
    path = os.path.join(work_dir, "analysis", "transcript.json")
    if not os.path.isfile(path):
        raise ValidationError(
            f"no transcript at {path} — run 'clipper-pro transcribe' first"
        )
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"transcript at {path} is not valid JSON: {exc}") from exc
    return TranscriptResult.from_dict(payload)


def candidates_from_workspace(work_dir: str) -> list[Candidate]:
    """Recover phase 3's candidates."""
    path = os.path.join(work_dir, "analysis", "candidates.json")
    if not os.path.isfile(path):
        raise ValidationError(f"no candidates at {path} — run 'clipper-pro rank' first")
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"candidates at {path} is not valid JSON: {exc}") from exc
    return [Candidate.from_dict(c) for c in payload.get("clips") or []]


def snapped_or_ranked(work_dir: str) -> list[Candidate]:
    """Phase 4's snapped cuts when present, else phase 3's raw candidates."""
    cuts = os.path.join(work_dir, "analysis", "cuts.json")
    if os.path.isfile(cuts):
        try:
            with open(cuts) as fh:
                return [Candidate.from_dict(c) for c in json.load(fh).get("clips") or []]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValidationError(f"cuts at {cuts} is unreadable: {exc}") from exc
    return candidates_from_workspace(work_dir)


def plans_from_workspace(work_dir: str) -> list[CameraPlan]:
    """Recover phase 5's camera plans."""
    from clipper_pro.reframe.plan import CameraPlan

    path = os.path.join(work_dir, "analysis", "reframe.json")
    if not os.path.isfile(path):
        raise ValidationError(
            f"no camera plans at {path} — run 'clipper-pro reframe' first"
        )
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"reframe at {path} is not valid JSON: {exc}") from exc
    return [CameraPlan.from_dict(p) for p in payload.get("clips") or []]


def renders_from_workspace(work_dir: str) -> list[dict[str, Any]]:
    """Phase 6's render records, or an empty list when it has not run.

    Absence is not an error here: phase 7 reports an unrendered clip rather than
    refusing to produce a report at all.
    """
    path = os.path.join(work_dir, "analysis", "renders.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as fh:
            return json.load(fh).get("clips") or []
    except (json.JSONDecodeError, AttributeError):
        return []


# --- the phases -------------------------------------------------------------


def run_phase(
    phase: str,
    work_dir: str,
    *,
    source: str | None = None,
    options: PhaseOptions | None = None,
) -> dict[str, Any]:
    """Run one phase and return its summary payload.

    ``source`` is required for ``ingest`` and ignored by every other phase.
    Raises :class:`~clipper_pro.errors.ClipperProError` subclasses on failure, so
    a caller handles one exception hierarchy however it presents errors.
    """
    if phase not in PHASES:
        raise ValidationError(
            f"unknown phase {phase!r} — expected one of {', '.join(PHASES)}"
        )
    options = options or PhaseOptions()
    runner = _RUNNERS[phase]
    return runner(work_dir, source, options)


def _ingest(work_dir: str, source: str | None, opts: PhaseOptions) -> dict[str, Any]:
    if not source:
        raise ValidationError("ingest needs a source URL or file path")
    settings = Settings.from_env().with_overrides(asr_sample_rate=opts.sample_rate)
    workspace_init(work_dir)

    media = run_ingest(
        source, work_dir,
        settings=settings, cookies_file=opts.cookies, overwrite=opts.overwrite,
    )

    spec = spec_from_settings(settings)
    payload = {
        "phase": "ingest",
        "source": media.to_dict(),
        "audio": {
            "path": media.audio_path,
            "sample_rate": spec.sample_rate,
            "channels": spec.channels,
            "codec": spec.codec,
            "estimated_bytes": estimate_flac_bytes(media.duration, spec),
        },
        "saving": describe_saving(media.path, media.audio_path),
    }
    record_artifact(work_dir, "ingest", payload)
    return payload


def _transcribe(work_dir: str, _source: str | None, opts: PhaseOptions) -> dict[str, Any]:
    from clipper_pro.transcribe import run_transcribe  # defers the requests import

    settings = Settings.from_env()
    media = media_from_workspace(work_dir)

    result = run_transcribe(
        media, work_dir,
        settings=settings,
        provider=opts.transcribe_provider,
        use_cache=False if opts.no_transcript_cache else None,
        require_events=opts.require_events,
    )

    # The full word list belongs in the artifact file, not in a summary; this is
    # what a human or an agent needs to decide whether to continue.
    payload = {
        "phase": "transcribe",
        "provider": result.provider,
        "model": result.model,
        "language": result.language,
        "words": len(result.words),
        "events": len(result.events),
        "event_kinds": sorted({e.kind for e in result.events}),
        "speakers": sorted(result.speakers),
        "duration": round(result.duration, 3),
        "transcript_path": os.path.join(work_dir, "analysis", "transcript.json"),
    }
    record_artifact(work_dir, "transcribe", payload)
    return payload


def _rank(work_dir: str, _source: str | None, opts: PhaseOptions) -> dict[str, Any]:
    from clipper_pro.rank import run_rank  # defers the provider imports

    settings = Settings.from_env()
    transcript = transcript_from_workspace(work_dir)

    candidates = run_rank(
        transcript, work_dir,
        settings=settings,
        provider=opts.rank_provider,
        instructions=opts.instructions,
        use_cache=False if opts.no_rank_cache else None,
        max_clips=opts.max_clips,
    )

    payload = {
        "phase": "rank",
        "provider": opts.rank_provider or settings.ranker,
        "clips": len(candidates),
        # Per-axis scores live in the artifact; this is the shape a human needs
        # to judge whether the selection is worth rendering.
        "ranked": [
            {
                "start": round(c.start, 2),
                "end": round(c.end, 2),
                "duration": round(c.duration, 2),
                "score": c.scores.total,
                "title": c.title,
            }
            for c in candidates
        ],
        "candidates_path": os.path.join(work_dir, "analysis", "candidates.json"),
    }
    record_artifact(work_dir, "rank", payload)
    return payload


def _cut(work_dir: str, _source: str | None, opts: PhaseOptions) -> dict[str, Any]:
    from clipper_pro.cut import run_cut

    settings = Settings.from_env()
    snapped = run_cut(
        candidates_from_workspace(work_dir),
        transcript_from_workspace(work_dir),
        media_from_workspace(work_dir),
        work_dir,
        settings=settings,
        use_silence=False if opts.no_silence else None,
    )

    payload = {
        "phase": "cut",
        "clips": len(snapped),
        "moved": sum(1 for c in snapped if c.snapped_from is not None),
        "snapped": [
            {
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "duration": round(c.duration, 3),
                "moved_from": list(c.snapped_from) if c.snapped_from else None,
                "title": c.title,
            }
            for c in snapped
        ],
        "cuts_path": os.path.join(work_dir, "analysis", "cuts.json"),
    }
    record_artifact(work_dir, "cut", payload)
    return payload


def _reframe(work_dir: str, _source: str | None, opts: PhaseOptions) -> dict[str, Any]:
    from clipper_pro.reframe import run_reframe

    settings = Settings.from_env()
    plans = run_reframe(
        # Prefer phase 4's snapped edges; fall back to phase 3's if cut skipped.
        snapped_or_ranked(work_dir),
        transcript_from_workspace(work_dir),
        media_from_workspace(work_dir),
        work_dir,
        settings=settings,
        locate=(lambda _p, _s: {}) if opts.centred else None,
    )

    payload = {
        "phase": "reframe",
        "clips": len(plans),
        "crops": [
            {
                "clip": p.clip_index,
                "keyframes": len(p.keyframes),
                "source": f"{p.source_width}x{p.source_height}@{p.fps:g}",
                "crop": (
                    f"{p.keyframes[0].width:g}x{p.keyframes[0].height:g}"
                    if p.keyframes else None
                ),
                "speakers": sorted(
                    {kf.speaker for kf in p.keyframes if kf.speaker is not None}
                ),
            }
            for p in plans
        ],
        "reframe_path": os.path.join(work_dir, "analysis", "reframe.json"),
    }
    record_artifact(work_dir, "reframe", payload)
    return payload


def _render(work_dir: str, _source: str | None, opts: PhaseOptions) -> dict[str, Any]:
    from clipper_pro.render import run_render

    settings = Settings.from_env().with_overrides(export_crf=opts.crf)
    outputs = run_render(
        plans_from_workspace(work_dir),
        media_from_workspace(work_dir),
        work_dir,
        settings=settings,
        candidates=snapped_or_ranked(work_dir),
    )

    payload = {
        "phase": "render",
        "clips": len(outputs),
        "crf": settings.export_crf,
        "size": f"{settings.output_width}x{settings.output_height}",
        "outputs": outputs,
        "renders_path": os.path.join(work_dir, "analysis", "renders.json"),
    }
    record_artifact(work_dir, "render", payload)
    return payload


def _export(work_dir: str, _source: str | None, _opts: PhaseOptions) -> dict[str, Any]:
    from clipper_pro.export import run_export

    settings = Settings.from_env()
    candidates = snapped_or_ranked(work_dir)
    renders = renders_from_workspace(work_dir)
    artifacts = workspace_load(work_dir).get("artifacts") or {}
    ranker = (artifacts.get("rank") or {}).get("provider", "")

    report_path = run_export(
        candidates, renders, media_from_workspace(work_dir), work_dir,
        settings=settings, ranker=ranker,
    )

    payload = {
        "phase": "export",
        "clips": len(candidates),
        "rendered": sum(1 for r in renders if r.get("path")),
        "report_json": report_path,
        "report_markdown": os.path.join(work_dir, "reports", "draft.md"),
        "renders_dir": os.path.join(work_dir, "renders"),
    }
    record_artifact(work_dir, "export", payload)
    return payload


_RUNNERS = {
    "ingest": _ingest,
    "transcribe": _transcribe,
    "rank": _rank,
    "cut": _cut,
    "reframe": _reframe,
    "render": _render,
    "export": _export,
}
