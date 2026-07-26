"""Pure report assembly: artifacts in, draft report and Markdown out.

What the report is for
----------------------
An editor with eleven rendered clips has to decide which three to post, in about
a minute, without scrubbing each one. A bare score cannot support that decision —
"7.4" says nothing about *why*, and two clips scoring 7.4 can be worth very
different things. So every row carries the five rubric axes, the ranker's stated
reason, and what phase 4's snapping moved. A clip that scored well on hook but
poorly on completeness is a different editorial call from the reverse, and the
report should make that visible at a glance.

Stdlib-only, so the whole document — including the Markdown — is host-tested
without rendering a frame.
"""

from __future__ import annotations

from typing import Any

from clipper_pro.types import Candidate, RubricScores

__all__ = ["build_report", "format_timecode", "render_markdown"]


def format_timecode(seconds: float) -> str:
    """``M:SS`` (or ``H:MM:SS``) — where to scrub to in the source.

    The point of the report is finding the moment in the original, and nobody
    locates 1487.2 seconds by eye.
    """
    seconds = max(0.0, seconds)
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_report(
    candidates: list[Candidate],
    renders: list[dict[str, Any]],
    *,
    source_title: str = "",
    source_path: str = "",
    ranker: str = "",
    crf: int = 18,
    output_size: str = "",
) -> dict[str, Any]:
    """Assemble the draft report.

    Clips are listed by descending score, not in timeline order — this is the
    one artifact whose job is ranking, so the best clip belongs at the top. Its
    ``timecode`` field carries the source position for anyone who wants the
    timeline instead.

    ``renders`` are the phase-6 records; a candidate without one is still
    reported, marked unrendered, rather than silently dropped — a missing render
    is exactly what the reader needs to know about.
    """
    by_index = {r.get("clip_index"): r for r in renders if isinstance(r, dict)}

    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        record = by_index.get(index) or {}
        rows.append({
            "clip": index + 1,
            "title": candidate.title or f"Clip {index + 1}",
            "start": round(candidate.start, 3),
            "end": round(candidate.end, 3),
            "duration": round(candidate.duration, 3),
            "timecode": format_timecode(candidate.start),
            "score": candidate.scores.total,
            "axes": {axis: getattr(candidate.scores, axis) for axis in RubricScores.WEIGHTS},
            "reason": candidate.reason,
            "snapped_from": list(candidate.snapped_from) if candidate.snapped_from else None,
            "file": record.get("path"),
            "bytes": record.get("bytes"),
            "rendered": bool(record.get("path")),
        })

    rows.sort(key=lambda r: (-r["score"], r["start"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "source": {"title": source_title, "path": source_path},
        "ranker": ranker,
        "weights": dict(RubricScores.WEIGHTS),
        "export": {
            "crf": crf,
            "size": output_size,
            # Stated explicitly because it is the single most likely thing to be
            # misread: these are intermediates for a human pass in CapCut, not
            # delivery files. Whatever compression the destination platform
            # applies lands on CapCut's export, not on this one.
            "profile": "CapCut import intermediate — not a delivery encode",
        },
        "clips": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the report as Markdown for a human to skim.

    A table rather than prose: the decision is comparative, and a reader
    scanning a column of hook scores can rank eleven clips far faster than by
    reading eleven paragraphs.
    """
    source = report.get("source") or {}
    export = report.get("export") or {}
    clips = report.get("clips") or []

    lines: list[str] = []
    title = source.get("title") or "Untitled source"
    lines.append(f"# Clip draft — {title}")
    lines.append("")
    rendered = sum(1 for c in clips if c.get("rendered"))
    lines.append(
        f"{len(clips)} clip{'' if len(clips) == 1 else 's'} "
        f"({rendered} rendered) · ranked by {report.get('ranker') or 'unknown'} · "
        f"CRF {export.get('crf', '?')} {export.get('size') or ''}".strip()
    )
    lines.append("")
    lines.append(f"> {export.get('profile', '')}")
    lines.append("")

    weights = report.get("weights") or {}
    axis_names = list(weights)
    header = ["#", "At", "Len", "Score", *(a[:4].title() for a in axis_names), "Title"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for clip in clips:
        axes = clip.get("axes") or {}
        cells = [
            str(clip.get("rank", "")),
            clip.get("timecode", ""),
            f"{clip.get('duration', 0):.0f}s",
            f"**{clip.get('score', 0):.2f}**",
            *(f"{axes.get(a, 0):.0f}" for a in axis_names),
            clip.get("title", ""),
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## Why these moments")
    lines.append("")
    for clip in clips:
        lines.append(f"### {clip.get('rank')}. {clip.get('title')} — {clip.get('score', 0):.2f}")
        lines.append("")
        lines.append(
            f"`{clip.get('start', 0):.2f}s – {clip.get('end', 0):.2f}s` "
            f"({clip.get('duration', 0):.1f}s, source {clip.get('timecode')})"
        )
        lines.append("")
        if clip.get("reason"):
            lines.append(clip["reason"])
            lines.append("")
        moved = clip.get("snapped_from")
        if moved:
            lines.append(
                f"*Edges snapped from {moved[0]:.2f}–{moved[1]:.2f}s "
                f"to land on a word, sentence or silence boundary.*"
            )
            lines.append("")
        if clip.get("file"):
            lines.append(f"→ `{clip['file']}`")
        else:
            lines.append("→ **not rendered**")
        lines.append("")

    weight_note = ", ".join(f"{a} {w:.0%}" for a, w in weights.items())
    lines.append(f"---\n\nScore weights: {weight_note}.")
    lines.append("")
    return "\n".join(lines)
