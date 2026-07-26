"""Command-line entry point: ``python -m clipper_pro`` / ``clipper-pro``.

One subcommand per phase, so a run can be driven step by step and inspected
between steps — which is how an agent is expected to use this. Every command
prints a single JSON object on stdout (progress and warnings go to stderr), so
the output pipes into ``jq`` or is parsed by a calling harness without scraping
log lines.

A phase reads what the previous one recorded in the workspace manifest, so the
steps compose without the caller having to thread paths between them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING, Any

from clipper_pro import __version__
from clipper_pro.config import RANKERS as RANKERS_CHOICES
from clipper_pro.config import TRANSCRIBERS, Settings
from clipper_pro.errors import ClipperProError, ValidationError
from clipper_pro.ingest import describe_saving, run_ingest, spec_from_settings
from clipper_pro.ingest.audio_ops import estimate_flac_bytes
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import Candidate, SourceMedia
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import load as workspace_load
from clipper_pro.workspace import record_artifact

if TYPE_CHECKING:  # heavy phase modules stay lazily imported at runtime
    from clipper_pro.reframe.plan import CameraPlan

__all__ = ["build_parser", "main"]

_PENDING_PHASES = ("export",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipper-pro",
        description="Turn long-form video into publish-ready vertical clips.",
    )
    parser.add_argument("--version", action="version", version=f"clipper-pro {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest",
        help="phase 1: download the source and extract mono 16 kHz FLAC audio",
    )
    ingest.add_argument("source", help="an https video URL or a local file path")
    ingest.add_argument(
        "--work-dir", required=True, help="run workspace directory (created if absent)"
    )
    ingest.add_argument(
        "--sample-rate", type=int, default=None,
        help="analysis audio sample rate in Hz (default 16000)",
    )
    ingest.add_argument("--cookies", default=None, help="cookies file for the downloader")
    ingest.add_argument(
        "--overwrite", action="store_true",
        help="re-extract even if this run already produced the audio",
    )

    transcribe = sub.add_parser(
        "transcribe",
        help="phase 2: word-level transcription with diarization and audio events",
    )
    transcribe.add_argument("--work-dir", required=True, help="run workspace directory")
    transcribe.add_argument(
        "--provider", choices=TRANSCRIBERS, default=None,
        help="transcription provider (default deepgram; elevenlabs also tags audio events)",
    )
    transcribe.add_argument(
        "--require-events", action="store_true",
        help="fail rather than continue if the provider cannot tag audio events",
    )
    transcribe.add_argument(
        "--no-cache", action="store_true",
        help="ignore any cached transcript for this audio and re-transcribe",
    )

    rank = sub.add_parser(
        "rank", help="phase 3: score moments against the 5-axis virality rubric"
    )
    rank.add_argument("--work-dir", required=True, help="run workspace directory")
    rank.add_argument(
        "--provider", choices=RANKERS_CHOICES, default=None,
        help="ranking provider (default deepseek)",
    )
    rank.add_argument(
        "--max-clips", type=int, default=None, help="cap on returned clips (default 10)"
    )
    rank.add_argument(
        "--instructions", default=None,
        help="free-text preferences to guide selection (treated as untrusted)",
    )
    rank.add_argument(
        "--no-cache", action="store_true",
        help="ignore any cached ranking for this prompt and re-ask the model",
    )

    cut = sub.add_parser(
        "cut", help="phase 4: snap clip edges to words, sentences and silence"
    )
    cut.add_argument("--work-dir", required=True, help="run workspace directory")
    cut.add_argument(
        "--no-silence", action="store_true",
        help="skip the waveform stage and keep transcript-derived edges",
    )

    reframe = sub.add_parser(
        "reframe", help="phase 5: build a 9:16 crop trajectory per clip"
    )
    reframe.add_argument("--work-dir", required=True, help="run workspace directory")
    reframe.add_argument(
        "--centred", action="store_true",
        help="skip speaker tracking and centre every crop (no cv2 needed)",
    )

    render = sub.add_parser(
        "render", help="phase 6: render each clip in a single ffmpeg pass"
    )
    render.add_argument("--work-dir", required=True, help="run workspace directory")
    render.add_argument(
        "--crf", type=int, default=None,
        help="x264 CRF (default 18 — a CapCut-import intermediate, not delivery)",
    )

    for name in _PENDING_PHASES:
        sub.add_parser(name, help=f"phase {name} (not implemented yet)")

    return parser


def _cmd_ingest(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings.from_env().with_overrides(asr_sample_rate=args.sample_rate)
    workspace_init(args.work_dir)

    media = run_ingest(
        args.source,
        args.work_dir,
        settings=settings,
        cookies_file=args.cookies,
        overwrite=args.overwrite,
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
    record_artifact(args.work_dir, "ingest", payload)
    return payload


def _media_from_workspace(work_dir: str) -> SourceMedia:
    """Recover phase 1's source record so later phases need no repeated paths."""
    artifacts = workspace_load(work_dir).get("artifacts") or {}
    ingest = artifacts.get("ingest")
    if not isinstance(ingest, dict) or not isinstance(ingest.get("source"), dict):
        raise ValidationError(
            f"no ingest artifact in {work_dir} — run 'clipper-pro ingest' first"
        )
    return SourceMedia.from_dict(ingest["source"])


def _cmd_transcribe(args: argparse.Namespace) -> dict[str, Any]:
    from clipper_pro.transcribe import run_transcribe  # defers the requests import

    settings = Settings.from_env()
    media = _media_from_workspace(args.work_dir)

    result = run_transcribe(
        media,
        args.work_dir,
        settings=settings,
        provider=args.provider,
        use_cache=False if args.no_cache else None,
        require_events=args.require_events,
    )

    # The full word list belongs in the artifact file, not in a terminal; the
    # summary is what a human or an agent needs to decide whether to continue.
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
        "transcript_path": f"{args.work_dir}/analysis/transcript.json",
    }
    record_artifact(args.work_dir, "transcribe", payload)
    return payload


def _transcript_from_workspace(work_dir: str) -> TranscriptResult:
    """Recover phase 2's transcript artifact so phase 3 needs no repeated paths."""
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


def _cmd_rank(args: argparse.Namespace) -> dict[str, Any]:
    from clipper_pro.rank import run_rank  # defers the provider imports

    settings = Settings.from_env()
    transcript = _transcript_from_workspace(args.work_dir)

    candidates = run_rank(
        transcript,
        args.work_dir,
        settings=settings,
        provider=args.provider,
        instructions=args.instructions,
        use_cache=False if args.no_cache else None,
        max_clips=args.max_clips,
    )

    payload = {
        "phase": "rank",
        "provider": args.provider or settings.ranker,
        "clips": len(candidates),
        # The per-axis scores live in the artifact; the summary is what a human
        # needs to judge whether the selection is worth rendering.
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
        "candidates_path": os.path.join(args.work_dir, "analysis", "candidates.json"),
    }
    record_artifact(args.work_dir, "rank", payload)
    return payload


def _candidates_from_workspace(work_dir: str) -> list[Candidate]:
    """Recover phase 3's candidates, or phase 4's if it has already run."""
    path = os.path.join(work_dir, "analysis", "candidates.json")
    if not os.path.isfile(path):
        raise ValidationError(f"no candidates at {path} — run 'clipper-pro rank' first")
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"candidates at {path} is not valid JSON: {exc}") from exc
    return [Candidate.from_dict(c) for c in payload.get("clips") or []]


def _cmd_cut(args: argparse.Namespace) -> dict[str, Any]:
    from clipper_pro.cut import run_cut

    settings = Settings.from_env()
    media = _media_from_workspace(args.work_dir)
    transcript = _transcript_from_workspace(args.work_dir)
    candidates = _candidates_from_workspace(args.work_dir)

    snapped = run_cut(
        candidates,
        transcript,
        media,
        args.work_dir,
        settings=settings,
        use_silence=False if args.no_silence else None,
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
        "cuts_path": os.path.join(args.work_dir, "analysis", "cuts.json"),
    }
    record_artifact(args.work_dir, "cut", payload)
    return payload


def _cmd_reframe(args: argparse.Namespace) -> dict[str, Any]:
    from clipper_pro.reframe import run_reframe

    settings = Settings.from_env()
    media = _media_from_workspace(args.work_dir)
    transcript = _transcript_from_workspace(args.work_dir)
    # Prefer phase 4's snapped edges; fall back to phase 3's if cut hasn't run.
    candidates = _snapped_or_ranked(args.work_dir)

    plans = run_reframe(
        candidates, transcript, media, args.work_dir,
        settings=settings,
        locate=(lambda _p, _s: {}) if args.centred else None,
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
        "reframe_path": os.path.join(args.work_dir, "analysis", "reframe.json"),
    }
    record_artifact(args.work_dir, "reframe", payload)
    return payload


def _snapped_or_ranked(work_dir: str) -> list[Candidate]:
    """Phase 4's cuts when present, else phase 3's candidates."""
    cuts = os.path.join(work_dir, "analysis", "cuts.json")
    if os.path.isfile(cuts):
        try:
            with open(cuts) as fh:
                return [Candidate.from_dict(c) for c in json.load(fh).get("clips") or []]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValidationError(f"cuts at {cuts} is unreadable: {exc}") from exc
    return _candidates_from_workspace(work_dir)


def _plans_from_workspace(work_dir: str) -> list[CameraPlan]:
    """Recover phase 5's camera plans."""
    from clipper_pro.reframe.plan import CameraPlan

    path = os.path.join(work_dir, "analysis", "reframe.json")
    if not os.path.isfile(path):
        raise ValidationError(f"no camera plans at {path} — run 'clipper-pro reframe' first")
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"reframe at {path} is not valid JSON: {exc}") from exc
    return [CameraPlan.from_dict(p) for p in payload.get("clips") or []]


def _cmd_render(args: argparse.Namespace) -> dict[str, Any]:
    from clipper_pro.render import run_render

    settings = Settings.from_env().with_overrides(export_crf=args.crf)
    media = _media_from_workspace(args.work_dir)
    plans = _plans_from_workspace(args.work_dir)

    outputs = run_render(
        plans, media, args.work_dir,
        settings=settings, candidates=_snapped_or_ranked(args.work_dir),
    )

    payload = {
        "phase": "render",
        "clips": len(outputs),
        "crf": settings.export_crf,
        "size": f"{settings.output_width}x{settings.output_height}",
        "outputs": outputs,
        "renders_path": os.path.join(args.work_dir, "analysis", "renders.json"),
    }
    record_artifact(args.work_dir, "render", payload)
    return payload


_HANDLERS = {
    "ingest": _cmd_ingest,
    "transcribe": _cmd_transcribe,
    "rank": _cmd_rank,
    "cut": _cmd_cut,
    "reframe": _cmd_reframe,
    "render": _cmd_render,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    handler = _HANDLERS.get(args.command)
    if handler is None:
        print(
            f"phase '{args.command}' is not implemented yet — "
            f"implemented phases: {', '.join(sorted(_HANDLERS))}",
            file=sys.stderr,
        )
        return 2

    try:
        result = handler(args)
    except ClipperProError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return exc.exit_code

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
