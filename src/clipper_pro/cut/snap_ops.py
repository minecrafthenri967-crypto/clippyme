"""Pure adapters between phase-3 candidates and the host repository's snapper.

The three-stage cascade itself is **not reimplemented here**.
``clippyme.pipeline.cut_ops`` already owns it — word snapping, sentence
expansion with duration and neighbour clamping, waveform-silence refinement,
plus the ordering subtlety that "adjacent in the list" is not "adjacent in
time". That module is pure and host-tested; re-deriving its arithmetic would
mean maintaining two versions of the one calculation that decides whether a cut
sounds edited or chopped.

What this module does is translate. ``cut_ops`` speaks the ClippyMe clip dict
(``{"start", "end", …}``, mutated in place) and returns ``SnapEvent`` objects;
phase 4 speaks immutable :class:`~clipper_pro.types.Candidate`. Everything below
is that conversion, plus recording where each edge came from so phase 7's report
can show what the snapping actually did.
"""

from __future__ import annotations

import math

from clipper_pro.types import Candidate, Word

__all__ = [
    "candidates_to_clips",
    "clips_to_candidates",
    "describe_movement",
    "validate_snapped",
    "words_to_dicts",
]


def words_to_dicts(words: list[Word]) -> list[dict]:
    """Render words in the shape ``cut_ops`` expects.

    ``cut_ops.sentence_boundaries`` reads the ``word`` key to find terminal
    punctuation, which is why phase 2 insists on ``smart_format``: without
    punctuation the sentence stage finds no boundaries and the cascade silently
    degrades to word-level snapping only.
    """
    return [{"word": w.text, "start": w.start, "end": w.end} for w in words]


def candidates_to_clips(candidates: list[Candidate]) -> list[dict]:
    """Render candidates as the mutable clip dicts ``cut_ops`` snaps in place."""
    return [{"start": c.start, "end": c.end} for c in candidates]


def clips_to_candidates(
    clips: list[dict], originals: list[Candidate]
) -> list[Candidate]:
    """Rebuild candidates from snapped clip dicts, preserving score and prose.

    ``snapped_from`` is set only on candidates whose edges actually moved, so a
    populated value always means "the cascade changed this" rather than "phase 4
    ran". Candidates are returned in time order because everything downstream —
    rendering, the report, an editor scrubbing the source — reads along the
    timeline.
    """
    if len(clips) != len(originals):
        raise ValueError(
            f"snapped {len(clips)} clips for {len(originals)} candidates — "
            f"the snapper must not add or drop entries"
        )

    rebuilt: list[Candidate] = []
    for clip, original in zip(clips, originals, strict=True):
        start = _finite(clip.get("start"), original.start)
        end = _finite(clip.get("end"), original.end)
        # The snapper guarantees a non-inverted range, but it is cheap to refuse
        # a degenerate one here rather than let it reach the renderer.
        if end <= start:
            start, end = original.start, original.end
        moved = (start, end) != (original.start, original.end)
        rebuilt.append(
            Candidate(
                start=start,
                end=end,
                title=original.title,
                reason=original.reason,
                scores=original.scores,
                snapped_from=(original.start, original.end) if moved else None,
            )
        )
    rebuilt.sort(key=lambda c: c.start)
    return rebuilt


def _finite(value: object, fallback: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def describe_movement(candidate: Candidate) -> str:
    """One line describing how far a candidate's edges moved, for logs/reports."""
    if candidate.snapped_from is None:
        return "edges unchanged"
    old_start, old_end = candidate.snapped_from
    return (
        f"start {old_start:.2f}→{candidate.start:.2f}s "
        f"({candidate.start - old_start:+.2f}), "
        f"end {old_end:.2f}→{candidate.end:.2f}s "
        f"({candidate.end - old_end:+.2f})"
    )


def validate_snapped(
    candidates: list[Candidate], *, source_duration: float = 0.0
) -> list[str]:
    """Return problems that would make the snapped set unrenderable.

    Checked rather than assumed because the cascade composes three independent
    adjustments; a bad interaction between them should surface here, next to the
    conversion, instead of as a confusing ffmpeg error several phases later.
    """
    problems: list[str] = []
    ordered = sorted(candidates, key=lambda c: c.start)
    for index, candidate in enumerate(ordered):
        label = f"clip[{index}] {candidate.title or '<untitled>'}"
        if candidate.start < 0:
            problems.append(f"{label}: negative start {candidate.start:.2f}")
        if source_duration > 0 and candidate.end > source_duration + 0.01:
            problems.append(
                f"{label}: ends at {candidate.end:.2f}s past the "
                f"{source_duration:.2f}s source"
            )
        if index + 1 < len(ordered):
            following = ordered[index + 1]
            if candidate.end > following.start + 0.01:
                problems.append(
                    f"{label}: overlaps the next clip by "
                    f"{candidate.end - following.start:.2f}s"
                )
    return problems
