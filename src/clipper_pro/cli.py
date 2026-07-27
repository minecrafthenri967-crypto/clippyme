"""Command-line entry point: ``python -m clipper_pro`` / ``clipper-pro``.

One subcommand per phase, so a run can be driven step by step and inspected
between steps — which is how an agent is expected to use this. Every command
prints a single JSON object on stdout (progress and warnings go to stderr), so
the output pipes into ``jq`` or is parsed by a calling harness without scraping
log lines.

This module owns *argument parsing only*. The orchestration behind each phase
lives in :mod:`clipper_pro.pipeline`, shared with the local web UI so the two
front ends cannot drift apart. A phase reads what the previous one recorded in
the workspace manifest, so the steps compose without the caller threading paths
between them.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from clipper_pro import __version__
from clipper_pro.config import CAPTION_POSITIONS, CAPTION_PRESETS, TRANSCRIBERS
from clipper_pro.config import RANKERS as RANKERS_CHOICES
from clipper_pro.errors import ClipperProError
from clipper_pro.pipeline import PHASES, PhaseOptions, run_phase

__all__ = ["build_parser", "main"]

#: Phases with a parser but no implementation. Empty — all seven are built.
_PENDING_PHASES: tuple[str, ...] = ()


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
    render.add_argument(
        "--captions", action="store_true", default=None,
        help="burn in word-level karaoke captions (no extra API — uses phase 2's timings)",
    )
    render.add_argument(
        "--no-captions", dest="captions", action="store_false",
        help="render without captions even if they are enabled by configuration",
    )
    render.add_argument(
        "--caption-preset", choices=CAPTION_PRESETS, default=None,
        help="caption style (default hormozi_bold)",
    )
    render.add_argument(
        "--caption-words", type=int, default=None,
        help="words shown at once, 1-12 (default 3)",
    )
    render.add_argument(
        "--caption-position", choices=CAPTION_POSITIONS, default=None,
        help="where captions sit in frame (default bottom)",
    )

    export = sub.add_parser(
        "export", help="phase 7: write the scored draft report for CapCut import"
    )
    export.add_argument("--work-dir", required=True, help="run workspace directory")

    web = sub.add_parser(
        "web", help="serve the local web UI (localhost only) and open a browser"
    )
    web.add_argument(
        "--runs-dir", default=None,
        help="where runs are stored (default ~/clipper-pro-runs)",
    )
    web.add_argument("--port", type=int, default=8720, help="port to listen on")
    web.add_argument(
        "--host", default="127.0.0.1",
        help="interface to bind (default 127.0.0.1 — localhost only)",
    )
    web.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )

    for name in _PENDING_PHASES:
        sub.add_parser(name, help=f"phase {name} (not implemented yet)")

    return parser


def _options_from_args(args: argparse.Namespace) -> PhaseOptions:
    """Translate parsed argv into the shared per-run overrides.

    Reads with ``getattr`` defaults because each subparser defines only its own
    flags; a field the current subcommand does not have simply stays unset.
    """
    return PhaseOptions(
        sample_rate=getattr(args, "sample_rate", None),
        cookies=getattr(args, "cookies", None),
        overwrite=getattr(args, "overwrite", False),
        # Both transcribe and rank spell their provider flag "--provider"; which
        # one it means is decided by the subcommand, so the same attribute feeds
        # both fields and the phase reads only its own.
        transcribe_provider=getattr(args, "provider", None),
        require_events=getattr(args, "require_events", False),
        no_transcript_cache=getattr(args, "no_cache", False),
        rank_provider=getattr(args, "provider", None),
        max_clips=getattr(args, "max_clips", None),
        instructions=getattr(args, "instructions", None),
        no_rank_cache=getattr(args, "no_cache", False),
        no_silence=getattr(args, "no_silence", False),
        centred=getattr(args, "centred", False),
        crf=getattr(args, "crf", None),
        captions=getattr(args, "captions", None),
        caption_preset=getattr(args, "caption_preset", None),
        caption_words_per_group=getattr(args, "caption_words", None),
        caption_position=getattr(args, "caption_position", None),
    )


def _cmd_web(args: argparse.Namespace) -> int:
    """Start the local web UI. Imported lazily so the CLI needs no web runtime."""
    try:
        from clipper_pro.web.server import serve
    except ImportError as exc:
        print(
            f"error: the web UI needs fastapi + uvicorn — "
            f"install with: pip install -e '.[web]'  ({exc})",
            file=sys.stderr,
        )
        return 3
    return serve(
        host=args.host,
        port=args.port,
        runs_dir=args.runs_dir,
        open_browser=not args.no_browser,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "web":
        return _cmd_web(args)

    if args.command not in PHASES:
        print(
            f"phase '{args.command}' is not implemented yet — "
            f"implemented phases: {', '.join(PHASES)}",
            file=sys.stderr,
        )
        return 2

    try:
        result: dict[str, Any] = run_phase(
            args.command,
            args.work_dir,
            source=getattr(args, "source", None),
            options=_options_from_args(args),
        )
    except ClipperProError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return exc.exit_code

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
