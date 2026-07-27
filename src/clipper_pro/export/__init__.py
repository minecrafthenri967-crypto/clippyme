"""Phase 7 — CapCut bridge and draft report.

Writes ``reports/draft.json`` and a readable ``reports/draft.md``, and leaves the
phase-6 clips where an editor can import them::

    >>> from clipper_pro.export import run_export
    >>> report_path = run_export(candidates, renders, media, "/work/run-1")  # doctest: +SKIP

Output profile
--------------
Clips stay at CRF 18, x264's practical visually-lossless point. They are an
**intermediate**, not a deliverable: they are going into CapCut for a final human
pass, and whatever compression the destination platform applies will be applied
to CapCut's export, not to this one. Shipping a delivery-grade encode here would
stack two lossy generations for no benefit.

The report exists because an editor with eleven clips has to pick three in about a
minute without scrubbing each one — so every row carries the five rubric axes, the
ranker's reason, and what phase 4's snapping moved. See
:mod:`clipper_pro.export.report_ops`.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError
from clipper_pro.export.report_ops import build_report, format_timecode, render_markdown
from clipper_pro.types import Candidate, SourceMedia

__all__ = [
    "REPORT_JSON",
    "REPORT_MARKDOWN",
    "build_report",
    "format_timecode",
    "render_markdown",
    "run_export",
]

REPORT_JSON = "draft.json"
REPORT_MARKDOWN = "draft.md"


def run_export(
    candidates: list[Candidate],
    renders: list[dict[str, Any]],
    media: SourceMedia,
    work_dir: str,
    *,
    settings: Settings | None = None,
    ranker: str = "",
) -> str:
    """Write the draft report; return the JSON path.

    Both forms are written: JSON for a calling harness or agent, Markdown for a
    human. They are generated from one assembled report so the two can never
    disagree about a score.
    """
    settings = settings or Settings.from_env()
    if not candidates:
        raise ToolFailureError("phase 7 has nothing to report; run phases 3-6 first")

    report = build_report(
        candidates,
        renders,
        source_title=media.title,
        source_path=media.path,
        ranker=ranker,
        crf=settings.export_crf,
        output_size=f"{settings.output_width}x{settings.output_height}",
    )

    reports_dir = os.path.join(work_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    json_path = _write(os.path.join(reports_dir, REPORT_JSON), json.dumps(report, indent=2) + "\n")
    _write(os.path.join(reports_dir, REPORT_MARKDOWN), render_markdown(report))

    unrendered = [c for c in report["clips"] if not c["rendered"]]
    if unrendered:
        # Reported rather than hidden: a missing render is exactly what the
        # reader needs to know before planning an edit around it.
        print(
            f"   ⚠️  {len(unrendered)} clip(s) in the report have no rendered file",
            file=sys.stderr,
        )
    best = report["clips"][0] if report["clips"] else None
    if best:
        print(
            f"   📋 {len(report['clips'])} clips ranked; top is "
            f"{best['title']!r} at {best['score']:.2f} ({best['timecode']})",
            file=sys.stderr,
        )
    return json_path


def _write(path: str, text: str) -> str:
    """Atomic write (tmp + replace) so a crash cannot leave a half report."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path
