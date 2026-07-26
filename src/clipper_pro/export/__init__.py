"""Phase 7 — CapCut bridge and draft report.

Contract
--------
``run_export(candidates, render_paths, out_dir) -> str``

Writes ``reports/draft.json`` (plus a readable Markdown twin) and leaves the
clips where an editor can import them.

Output profile
--------------
Clips are exported at CRF 18 — x264's practical "visually lossless" point.
These files are an *intermediate*, not a deliverable: they are going into CapCut
for a final human pass, and whatever compression the destination platform
applies will be applied to CapCut's export, not to this one. Shipping a
delivery-grade CRF here would mean stacking two lossy generations for no benefit.

The report
----------
Per clip: the five rubric axis scores and the weighted total, the ranker's
stated reason, and what phase 4's snapping moved (``snapped_from`` → final
edges). The point is an editor deciding *which three of eleven clips to post* in
a minute, without scrubbing each one — a bare score would not support that
decision, so the axes and the reasoning are what make the report worth reading.
"""

from __future__ import annotations

__all__ = ["run_export"]


def run_export(*args: object, **kwargs: object) -> None:
    """Not implemented yet — phase 7 of the build."""
    raise NotImplementedError(
        "phase 7 (export) is not implemented yet; "
        "phase 1 (clipper_pro.ingest) is the current entry point"
    )
