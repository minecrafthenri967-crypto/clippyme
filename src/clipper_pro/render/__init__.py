"""Phase 6 — single-pass render engine.

Contract
--------
``run_render(candidate, keyframes, source_path, out_dir) -> str``

One ffmpeg invocation per clip, producing the finished vertical video.

Why one pass
------------
The naive pipeline renders a reframe, then re-opens it to burn subtitles, then
re-opens *that* to add overlays. libx264 is lossy, so each of those generations
discards detail; by the fourth the result is visibly soft and blocky. Composing
the whole chain — 9:16 crop with the phase-5 trajectory, colour grade, ASS
subtitles, overlays — into a **single filtergraph** means exactly one decode and
one encode, so quality is bounded by a single generation regardless of how many
layers the recipe carries.

Layer order within the graph is not free: the grade runs before overlays so
burned-in elements keep their authored colour, and subtitles are applied against
the final crop geometry so their positioning cannot drift.

GPU is an optimisation, not a requirement. Where NVDEC/NVENC are present the
frames stay in device memory across the graph; the CPU path must produce
byte-comparable framing and remain the tested default, since CI and most
self-hosted deployments have no GPU.
"""

from __future__ import annotations

__all__ = ["run_render"]


def run_render(*args: object, **kwargs: object) -> None:
    """Not implemented yet — phase 6 of the build."""
    raise NotImplementedError(
        "phase 6 (render engine) is not implemented yet; "
        "phase 1 (clipper_pro.ingest) is the current entry point"
    )
