"""Phase 6 — single-pass render engine.

Turns each phase-5 camera plan into a finished vertical clip with one ffmpeg
invocation::

    >>> from clipper_pro.render import run_render
    >>> paths = run_render(plans, media, "/work/run-1")   # doctest: +SKIP

One decode, one encode
----------------------
The naive pipeline renders a reframe, re-opens it to burn subtitles, then
re-opens *that* to add overlays. libx264 is lossy, so each of those generations
discards detail; by the third the result is visibly soft. Composing the whole
chain into a single filtergraph bounds quality loss to one generation regardless
of how many layers the recipe carries. Layer order inside the graph is not free
— the crop is established first so any later overlay is positioned against final
geometry, and scaling happens last so nothing is resampled twice.

GPU is an optimisation, not a requirement: the CPU path is the tested default,
because CI and most self-hosted deployments have no GPU. The single-pass win comes
from the filtergraph, not from device memory.

The keyframe-count problem and its answer live in
:mod:`clipper_pro.render.filtergraph_ops` — a per-frame crop expression would be
a megabyte of command line, so the trajectory is simplified to a handful of
anchors and interpolated by ffmpeg.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError
from clipper_pro.ingest.audio import require_binary
from clipper_pro.reframe.plan import CameraPlan
from clipper_pro.render.filtergraph_ops import (
    DEFAULT_OUTPUT_HEIGHT,
    DEFAULT_OUTPUT_WIDTH,
    build_filtergraph,
    crop_x_expression,
    simplify_trajectory,
)
from clipper_pro.types import Candidate, SourceMedia

__all__ = [
    "DEFAULT_OUTPUT_HEIGHT",
    "DEFAULT_OUTPUT_WIDTH",
    "RENDER_FILENAME",
    "build_filtergraph",
    "build_render_command",
    "clip_output_name",
    "crop_x_expression",
    "run_render",
    "simplify_trajectory",
]

RENDER_FILENAME = "renders.json"


def clip_output_name(index: int, title: str = "") -> str:
    """Deterministic ``clip_NN_title.mp4`` name, safe on every filesystem.

    Deterministic so a re-render overwrites its own output instead of littering
    the directory, and index-prefixed so the files sort in timeline order — an
    editor importing them into CapCut gets them in the order they occur.
    """
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in title).strip("_")
    safe = safe[:50].strip("_")
    suffix = f"_{safe}" if safe else ""
    return f"clip_{index + 1:02d}{suffix}.mp4"


def build_render_command(
    source_path: str,
    plan: CameraPlan,
    output_path: str,
    *,
    crf: int = 18,
    output_width: int = DEFAULT_OUTPUT_WIDTH,
    output_height: int = DEFAULT_OUTPUT_HEIGHT,
) -> tuple[list[str], int]:
    """Build the single-pass ffmpeg argv; return ``(argv, anchor_count)``.

    ``-ss`` sits *after* ``-i`` here, unlike the phase-1 audio extraction: output
    seeking is frame-accurate, and a clip whose first frame is half a GOP early
    would desync from the camera path the expression assumes. The cost is
    decoding up to the start, which is acceptable once per clip.
    """
    graph, anchors = build_filtergraph(
        plan.keyframes,
        start=plan.start,
        output_width=output_width,
        output_height=output_height,
    )

    from clippyme.domain.encode import x264_video_args

    argv = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", source_path,
        "-ss", f"{plan.start:.3f}",
        "-t", f"{plan.duration:.3f}",
        "-filter:v", graph,
        *x264_video_args(crf=crf),
        # Re-encode audio rather than copying: a stream copy would keep the
        # source's GOP-aligned packet boundaries and drift against the
        # frame-accurate video trim.
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        output_path,
    ]
    return argv, anchors


def run_render(
    plans: list[CameraPlan],
    media: SourceMedia,
    work_dir: str,
    *,
    settings: Settings | None = None,
    candidates: list[Candidate] | None = None,
    timeout: int | None = None,
) -> list[str]:
    """Render every plan to ``renders/`` and return the output paths."""
    settings = settings or Settings.from_env()
    if not plans:
        raise ToolFailureError("phase 6 has no camera plans to render; run phase 5 first")
    if not os.path.isfile(media.path):
        raise ToolFailureError(f"source video not found: {media.path}")

    # Fail before creating directories if ffmpeg is absent.
    require_binary("ffmpeg")

    renders_dir = os.path.join(work_dir, "renders")
    os.makedirs(renders_dir, exist_ok=True)

    outputs: list[str] = []
    records: list[dict] = []
    try:
        for plan in plans:
            title = ""
            if candidates and plan.clip_index < len(candidates):
                title = candidates[plan.clip_index].title
            output_path = os.path.join(renders_dir, clip_output_name(plan.clip_index, title))

            argv, anchors = build_render_command(
                media.path, plan, output_path,
                crf=settings.export_crf,
                output_width=settings.output_width,
                output_height=settings.output_height,
            )
            print(
                f"   🎬 clip {plan.clip_index + 1}: {plan.duration:.1f}s, "
                f"{len(plan.keyframes)} keyframes → {anchors} anchors, one pass",
                file=sys.stderr,
            )
            _run_ffmpeg(argv, timeout=timeout or settings.ffmpeg_timeout)

            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise ToolFailureError(
                    f"ffmpeg reported success but produced no video at {output_path}"
                )
            outputs.append(output_path)
            records.append({
                "clip_index": plan.clip_index,
                "path": output_path,
                "duration": round(plan.duration, 3),
                "keyframes": len(plan.keyframes),
                "anchors": anchors,
                "bytes": os.path.getsize(output_path),
            })
    finally:
        # Persist whatever actually finished, even on a mid-loop failure.
        # Without this, a crash on clip N left the manifest from an earlier,
        # unrelated run untouched — export would then report that stale run's
        # clip count instead of the truth of what is actually on disk now.
        _write_renders(work_dir, records, crf=settings.export_crf,
                       size=f"{settings.output_width}x{settings.output_height}")

    return outputs


def _run_ffmpeg(argv: list[str], *, timeout: int) -> None:
    binary = require_binary(argv[0])
    try:
        proc = subprocess.run(
            [binary, *argv[1:]], capture_output=True, text=True,
            check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFailureError(f"ffmpeg timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr or "").strip().splitlines()[-6:])
        raise ToolFailureError(f"ffmpeg exited with {proc.returncode}: {tail}")


def _write_renders(work_dir: str, records: list[dict], *, crf: int, size: str) -> str:
    """Persist the render manifest as the phase's durable artifact (atomic)."""
    analysis_dir = os.path.join(work_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    path = os.path.join(analysis_dir, RENDER_FILENAME)
    payload = {"crf": crf, "output_size": size, "clips": records}
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
