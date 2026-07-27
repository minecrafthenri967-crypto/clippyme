"""Phase 3 — semantic curation against a 5-axis virality rubric.

Reads phase 2's transcript and emits scored :class:`~clipper_pro.types.Candidate`
ranges with rough transcript-derived edges; phase 4 tightens those edges::

    >>> from clipper_pro.rank import run_rank
    >>> candidates = run_rank(result, "/work/run-1")     # doctest: +SKIP
    >>> candidates[0].scores.total                       # doctest: +SKIP
    8.15

The five axes and their weights live in
:class:`clipper_pro.types.RubricScores` — hook strength, emotional payoff,
quotability, narrative completeness, information density. They stay named rather
than collapsing into one number so phase 7's report can explain a ranking rather
than assert one, and so a weighting change is a one-line edit.

Provider abstraction
--------------------
``CLIPPER_PRO_RANKER`` selects ``deepseek`` (default, DeepSeek-V3 over its
OpenAI-compatible endpoint) or ``gemini`` (reusing the host repository's
existing credential and retry chain). All judgement — prompt, validation,
dedupe — is shared in :mod:`clipper_pro.rank.rubric_ops`; a provider only moves
bytes. Responses are cached in SQLite keyed by the exact prompt.
"""

from __future__ import annotations

import json
import os
import sys

from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError
from clipper_pro.rank import cache
from clipper_pro.rank.base import RANKERS, RankerSpec, ranker_spec
from clipper_pro.rank.providers import complete, effective_model, require_api_key
from clipper_pro.rank.rubric_ops import (
    build_rank_prompt,
    dedupe_overlapping,
    parse_rank_response,
    select_top,
)
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import Candidate, RubricScores

__all__ = [
    "CANDIDATES_FILENAME",
    "RANKERS",
    "RankerSpec",
    "build_rank_prompt",
    "complete",
    "dedupe_overlapping",
    "effective_model",
    "parse_rank_response",
    "ranker_spec",
    "require_api_key",
    "run_rank",
    "select_top",
]

CANDIDATES_FILENAME = "candidates.json"


def _recover_json(text: str) -> dict | None:
    """Recover a JSON object from raw model text via the host repair chain.

    Reuses ``clippyme.pipeline.gemini_parser``'s five-level chain (strict →
    deterministic clean → json_repair → …), which is schema-agnostic and already
    handles the ways a model mangles JSON: smart quotes, trailing commas, code
    fences, chain-of-thought before the delimiter. Imported here rather than in
    ``rubric_ops`` because it pulls pydantic, and the rubric layer stays pure.
    """
    from clippyme.pipeline.gemini_parser import parse_gemini_response

    return parse_gemini_response(text).data


def run_rank(
    transcript: TranscriptResult,
    work_dir: str,
    *,
    settings: Settings | None = None,
    provider: str | None = None,
    instructions: str | None = None,
    use_cache: bool | None = None,
    max_clips: int | None = None,
) -> list[Candidate]:
    """Score and select candidate clips from ``transcript``.

    Persists ``analysis/candidates.json`` and returns the surviving candidates in
    time order.
    """
    settings = settings or Settings.from_env()
    spec = ranker_spec(provider or settings.ranker)
    caching = settings.rank_cache if use_cache is None else use_cache
    limit = settings.max_clips if max_clips is None else max_clips

    if not transcript.words:
        raise ToolFailureError(
            "phase 3 needs word-level timings from phase 2; the transcript is empty"
        )

    prompt = build_rank_prompt(
        transcript.words,
        transcript.events,
        transcript.duration,
        transcript_text=transcript.text,
        instructions=instructions,
        min_duration=settings.min_clip_duration,
        max_duration=settings.max_clip_duration,
        max_clips=limit,
    )

    model = effective_model(spec)
    cache_dir = os.path.join(work_dir, "analysis", "cache")
    key = cache.cache_key(prompt, spec.name, model) if caching else ""

    data: dict | None = None
    if caching:
        data = cache.load(cache_dir, key)
        if data is not None:
            print(f"   ↩︎  ranking cache hit ({spec.name}/{model})", file=sys.stderr)

    if data is None:
        if not transcript.events:
            print(
                "   ⚠️  no audio events in the transcript — ranking emotion from "
                "words alone (see CLIPPER_PRO_TRANSCRIBER)",
                file=sys.stderr,
            )
        raw_text = complete(prompt, spec.name)
        data = _recover_json(raw_text)
        if data is None:
            raise ToolFailureError(
                f"{spec.name} response could not be parsed as JSON even after "
                f"repair — first 200 chars: {raw_text[:200]!r}"
            )
        if caching:
            cache.store(cache_dir, key, data, provider=spec.name, model=model)

    candidates, problems = parse_rank_response(
        data,
        source_duration=transcript.duration,
        min_duration=settings.min_clip_duration,
        max_duration=settings.max_clip_duration,
    )
    for problem in problems:
        print(f"   ⚠️  rejected {problem}", file=sys.stderr)

    if not candidates:
        raise ToolFailureError(
            f"{spec.name} proposed no usable clips"
            + (f" ({len(problems)} rejected)" if problems else "")
        )

    candidates = select_top(dedupe_overlapping(candidates), limit)
    _write_candidates(work_dir, candidates, provider=spec.name, model=model)
    return candidates


def _write_candidates(
    work_dir: str, candidates: list[Candidate], *, provider: str, model: str
) -> str:
    """Persist the candidate list as the phase's durable artifact (atomic)."""
    analysis_dir = os.path.join(work_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    path = os.path.join(analysis_dir, CANDIDATES_FILENAME)
    payload = {
        "provider": provider,
        "model": model,
        # Recorded alongside the scores so phase 7's report can show how a total
        # was reached even if the weights are retuned later.
        "weights": dict(RubricScores.WEIGHTS),
        "clips": [c.to_dict() for c in candidates],
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
