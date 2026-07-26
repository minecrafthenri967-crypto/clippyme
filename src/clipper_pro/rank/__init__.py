"""Phase 3 — semantic curation against a 5-axis virality rubric.

Contract
--------
``run_rank(words, events, settings) -> list[Candidate]``

Emits scored :class:`~clipper_pro.types.Candidate` ranges with rough transcript
edges; phase 4 tightens those edges.

Provider abstraction
--------------------
The ranker is an interface, not a vendor. ``rank.base.Ranker`` defines
``score(transcript_window) -> RubricScores``, with two implementations planned:

* ``rank.deepseek`` — DeepSeek-V3 over its OpenAI-compatible endpoint (default).
* ``rank.gemini``   — adapter reusing the host repository's existing Gemini
  prompt/parse stack, so a deployment that already holds Gemini credentials can
  run this phase without provisioning a second key.

Select with ``CLIPPER_PRO_RANKER``; see :data:`clipper_pro.config.RANKERS`.

The five axes and their weights live in
:class:`clipper_pro.types.RubricScores` — hook strength, emotional payoff,
quotability, narrative completeness, information density — kept as named axes
so the phase-7 report can explain a ranking rather than assert one.

Caching
-------
Scores are memoised in a SQLite file keyed by (model, prompt hash, window hash).
Re-running a pipeline over the same source during development is the common
case, and it should cost nothing.
"""

from __future__ import annotations

__all__ = ["run_rank"]


def run_rank(*args: object, **kwargs: object) -> None:
    """Not implemented yet — phase 3 of the build."""
    raise NotImplementedError(
        "phase 3 (ranking) is not implemented yet; "
        "phase 1 (clipper_pro.ingest) is the current entry point"
    )
