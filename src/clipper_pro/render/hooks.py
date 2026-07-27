"""Writing a clip's hook ASS file — the I/O half of the text-hook feature.

The document itself is built by :mod:`clipper_pro.render.hooks_ops`; this module
decides where it goes and turns a failure into a decision rather than a crash.
"""

from __future__ import annotations

import os
import sys

from clipper_pro.render.hooks_ops import HookSettings, build_hook_ass

__all__ = ["HOOKS_DIRNAME", "write_clip_hook"]

HOOKS_DIRNAME = "hooks"


def write_clip_hook(
    text: str,
    work_dir: str,
    clip_index: int,
    *,
    start: float,
    end: float,
    settings: HookSettings,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
) -> str | None:
    """Write ``hooks/clip_NN.ass`` for one clip; return its path or ``None``.

    ``None`` means "render this clip without a hook". A clip whose ranker
    returned no hook text is the ordinary case, not an error — and an overlay
    failing is never worth failing an otherwise complete render over.
    """
    if not settings.enabled:
        return None

    document = build_hook_ass(
        text,
        settings=settings,
        # Source time, not clip-relative: phase 6 seeks with `-ss` after `-i`,
        # so the filter graph still sees each frame's original timestamp. The
        # same rule the captions writer follows, for the same reason.
        start=start,
        end=end,
        play_res_x=play_res_x,
        play_res_y=play_res_y,
    )
    if not document:
        print(
            f"   ⚠️  clip {clip_index + 1}: no hook text — rendering without an overlay",
            file=sys.stderr,
        )
        return None

    hooks_dir = os.path.join(work_dir, HOOKS_DIRNAME)
    os.makedirs(hooks_dir, exist_ok=True)
    path = os.path.join(hooks_dir, f"clip_{clip_index + 1:02d}.ass")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(document)
        os.replace(tmp, path)
    except OSError as exc:
        print(
            f"   ⚠️  clip {clip_index + 1}: hook file could not be written ({exc}) — "
            f"rendering without an overlay",
            file=sys.stderr,
        )
        return None
    return path
