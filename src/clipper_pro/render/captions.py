"""Writing a clip's ASS caption file — the I/O half of the caption feature.

Delegates the document itself to the host repository's karaoke generator; this
module decides where the file goes and turns a failure into a decision rather
than a crash.
"""

from __future__ import annotations

import os
import sys

from clipper_pro.render.captions_ops import (
    CaptionSettings,
    resolve_font_size,
    words_to_transcript,
)
from clipper_pro.types import Word

__all__ = ["CAPTIONS_DIRNAME", "write_clip_captions"]

CAPTIONS_DIRNAME = "captions"


def write_clip_captions(
    words: list[Word],
    work_dir: str,
    clip_index: int,
    *,
    start: float,
    end: float,
    settings: CaptionSettings,
) -> str | None:
    """Write ``captions/clip_NN.ass`` for one clip; return its path or ``None``.

    ``None`` means "render this clip without captions", and is returned rather
    than raised for the two cases where captions are genuinely impossible but
    the clip itself is fine: no words fall inside the range, or the generator
    failed. A clip with no burned-in text is a far better outcome than failing a
    render that was otherwise complete.
    """
    if not settings.enabled or not words:
        return None

    # The generator filters by range itself, but checking here means an empty
    # window is reported as such instead of producing a valid-looking ASS file
    # with no events in it.
    if not any(w.start < end and w.end > start for w in words):
        print(
            f"   ⚠️  clip {clip_index + 1}: no words in range — rendering without captions",
            file=sys.stderr,
        )
        return None

    try:
        from clippyme.domain.subtitles import generate_ass_karaoke
    except ImportError as exc:  # pragma: no cover - stdlib-only module
        print(f"   ⚠️  captions unavailable ({exc}) — rendering without them", file=sys.stderr)
        return None

    captions_dir = os.path.join(work_dir, CAPTIONS_DIRNAME)
    os.makedirs(captions_dir, exist_ok=True)
    path = os.path.join(captions_dir, f"clip_{clip_index + 1:02d}.ass")

    # Events are written in SOURCE time, not clip-relative.
    #
    # Phase 6 seeks with `-ss` *after* `-i` for frame accuracy, and with output
    # seeking the filter graph still sees each frame's original timestamp — the
    # discard happens downstream. So the `ass` filter matches events against
    # source time. Handing it a clip-relative document made captions vanish
    # exactly `start` seconds in, which looked like a styling bug and was really
    # a timebase mismatch.
    #
    # The generator rebases by subtracting its `clip_start`, so the range filter
    # is applied here and `0.0` is passed to leave the times alone.
    in_range = [w for w in words if w.end > start and w.start < end]

    try:
        generate_ass_karaoke(
            words_to_transcript(in_range),
            0.0,
            max(end, in_range[-1].end) + 1.0,
            path,
            preset=settings.preset,
            mode="word_group",
            words_per_group=settings.words_per_group,
            uppercase=settings.uppercase,
            position=settings.position,
            font_size=resolve_font_size(settings),
        )
    except Exception as exc:  # noqa: BLE001 - styling must never fail a render
        print(
            f"   ⚠️  clip {clip_index + 1}: caption generation failed ({exc}) — "
            f"rendering without them",
            file=sys.stderr,
        )
        return None

    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    return path
