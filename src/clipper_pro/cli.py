"""Command-line entry point: ``python -m clipper_pro``.

One subcommand per phase, so a run can be driven step by step and inspected
between steps — which is how an agent is expected to use this. Every command
prints a single JSON object on stdout (progress and warnings go to stderr), so
the output pipes into ``jq`` or is parsed by a calling harness without
scraping log lines.

Only ``ingest`` is wired up so far; the remaining phases are registered here so
``--help`` reflects the real shape of the pipeline rather than hiding it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from clipper_pro import __version__
from clipper_pro.config import Settings
from clipper_pro.errors import ClipperProError
from clipper_pro.ingest import describe_saving, run_ingest, spec_from_settings
from clipper_pro.ingest.audio_ops import estimate_flac_bytes
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import record_artifact

__all__ = ["build_parser", "main"]

_PENDING_PHASES = ("transcribe", "rank", "cut", "reframe", "render", "export")


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
    ingest.add_argument(
        "--cookies", default=None, help="cookies file for the downloader",
    )
    ingest.add_argument(
        "--overwrite", action="store_true",
        help="re-extract even if this run already produced the audio",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in _PENDING_PHASES:
        print(
            f"phase '{args.command}' is not implemented yet — "
            f"'ingest' is the current entry point",
            file=sys.stderr,
        )
        return 2

    try:
        result = _cmd_ingest(args)
    except ClipperProError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return exc.exit_code

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
