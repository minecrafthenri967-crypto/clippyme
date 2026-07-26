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
import sys
from typing import Any

from clipper_pro import __version__
from clipper_pro.config import TRANSCRIBERS, Settings
from clipper_pro.errors import ClipperProError, ValidationError
from clipper_pro.ingest import describe_saving, run_ingest, spec_from_settings
from clipper_pro.ingest.audio_ops import estimate_flac_bytes
from clipper_pro.types import SourceMedia
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import load as workspace_load
from clipper_pro.workspace import record_artifact

__all__ = ["build_parser", "main"]

_PENDING_PHASES = ("rank", "cut", "reframe", "render", "export")


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


_HANDLERS = {"ingest": _cmd_ingest, "transcribe": _cmd_transcribe}


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
