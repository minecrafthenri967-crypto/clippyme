"""Player-name-mention detection — a small, single-shot Gemini call that
identifies when an athlete's name is spoken in a clip (e.g. a trading-card
pull on a live sports-card-break stream), so the player-image compose layer
knows where to burn in their photo.

Follows ``clip_edit_ai.py``'s shape (own small prompt-builder + parser + one
Gemini-calling entry point, importable without cv2/torch) but — unlike that
module — reuses the shared retry/model-fallback (``gemini_request``) and
5-level JSON-repair chain (``gemini_parser``) instead of hand-rolling its
own, since a detection result is cached and billed once per clip (see
``compose._apply_player_image``) and deserves the same resilience as the
big viral-clip call.

Unlike ``clip_edit_ai.suggest_drops`` (which always swallows failures into an
empty result), ``detect_player_mentions`` RAISES on any failure that means
"we couldn't tell" (missing key, network/SDK error, unparseable response) and
only returns a list (possibly empty) on a response Gemini actually produced
and we could parse — the caller needs to tell "confirmed nobody mentioned"
(cache it) apart from "detection failed" (don't cache, retry next compose).
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from clippyme.pipeline.gemini_parser import parse_gemini_response
from clippyme.pipeline.gemini_request import (
    build_model_chain,
    encode_words_toon,
    generate_with_model_fallback,
)
from clippyme.schemas import PlayerMention

logger = logging.getLogger(__name__)

# Guardrails — a 15-60s clip has nowhere near this many words; this just
# bounds a pathological transcript from handing Gemini an unbounded prompt.
MAX_CLIP_WORDS = 400
MAX_MENTIONS = 20


def _extract_clip_words(transcript: dict, clip_start: float, clip_end: float) -> list[dict]:
    """Flatten ``transcript['segments'][*]['words']`` into clip-relative
    ``{'w','s','e'}`` dicts, filtered to the clip's window — same word
    filter ``smartcut_ops.analyze_silences`` uses, but shaped for
    ``gemini_request.encode_words_toon``. Pure."""
    words = []
    for segment in (transcript or {}).get("segments", []):
        for word in segment.get("words", []):
            try:
                w_start = float(word["start"])
                w_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if w_end <= clip_start or w_start >= clip_end:
                continue
            words.append({
                "w": word.get("word", ""),
                "s": round(max(0.0, w_start - clip_start), 3),
                "e": round(max(0.0, w_end - clip_start), 3),
            })
    return words[:MAX_CLIP_WORDS]


def build_player_prompt(words: list[dict], clip_duration: float) -> str:
    """Build the Gemini prompt. Pure. Uses the ``### JSON ###`` delimiter
    convention every other call site relies on (``gemini_parser`` keys on it)."""
    return (
        "You are watching a short clip from a live sports-card-break stream "
        f"({clip_duration:.2f} seconds long). Below is the clip's word-level "
        "transcript, encoded as a TOON table (w=word, s=start second, "
        "e=end second, both relative to the clip start).\n\n"
        f"{encode_words_toon(words)}\n\n"
        "Identify every moment the speaker verbally announces a REAL, "
        "identifiable professional athlete's full name (e.g. announcing a "
        'trading card pull: "and he pulls a LeBron James!"). Ignore team '
        "names, brands, generic references, and any name you are not "
        "reasonably confident about.\n\n"
        "Rules:\n"
        "- Anchor each timestamp to the exact `s` (start) of the spoken word "
        "the name begins at — never invent a timestamp outside the clip.\n"
        "- confidence is a float from 0 to 1 reflecting how sure you are this "
        "is a real, correctly-identified athlete name.\n"
        "- If no athlete names are mentioned, return an empty mentions array.\n\n"
        "Return ONLY strict JSON in this exact shape after the delimiter, "
        "nothing else:\n\n"
        "### JSON ###\n"
        '{"mentions": [{"player_name": "<str>", "timestamp": <float>, '
        '"confidence": <float 0-1>}]}\n'
    )


def build_player_reformat_prompt(err_msg: str, broken_text: str) -> str:
    """Level-4 retry prompt — own JSON contract (``mentions``, not
    ``shorts``). Deliberately does NOT reuse
    ``gemini_request.build_reformat_prompt``, which is hardcoded to the
    viral-clips response shape."""
    return (
        "You are a JSON reformatter. The previous response below was not "
        "valid JSON and failed parsing with this error:\n\n"
        f"ERROR: {err_msg}\n\n"
        "PREVIOUS_BROKEN_OUTPUT:\n"
        f"{broken_text}\n\n"
        "Return ONLY a valid JSON object matching this exact shape:\n"
        '{"mentions": [{"player_name": "<str>", "timestamp": <float>, '
        '"confidence": <float 0-1>}]}\n\n'
        "Rules: straight double quotes only, no trailing commas, no markdown, "
        "no code fences, no prose before or after. Escape every backslash as \\\\."
    )


def validate_player_mentions(data, clip_duration: float) -> list[dict]:
    """Per-item Pydantic validate+drop against ``PlayerMention`` (mirrors
    ``gemini_parser.validate_and_dedupe``'s per-item resilience loop) — no
    IoU dedup needed for point-in-time mentions. Clamps an out-of-range
    timestamp into ``[0, clip_duration]``. Never raises; logs and skips bad
    rows."""
    raw_mentions = (data or {}).get("mentions") if isinstance(data, dict) else None
    if not isinstance(raw_mentions, list):
        return []

    mentions: list[dict] = []
    for i, raw in enumerate(raw_mentions[:MAX_MENTIONS]):
        try:
            mention = PlayerMention.model_validate(raw)
        except ValidationError as exc:
            msg = str(exc).splitlines()[0] if str(exc) else "unknown"
            logger.warning("validate_player_mentions: dropping mention #%d — %s", i, msg[:200])
            continue
        timestamp = max(0.0, min(mention.timestamp, clip_duration))
        mentions.append({
            "player_name": mention.player_name,
            "timestamp": round(timestamp, 3),
            "confidence": mention.confidence,
        })
    return mentions


def detect_player_mentions(
    *, api_key: str, model: str, transcript: dict,
    clip_start: float, clip_end: float,
) -> list[dict]:
    """Detect athlete-name mentions in one clip.

    Returns a list (possibly empty — a confirmed "nobody mentioned") on
    success. Raises on missing key / network / SDK / unparseable-response
    failures — see the module docstring for why this differs from
    ``clip_edit_ai.suggest_drops``'s always-swallow contract.
    """
    if not api_key:
        raise ValueError("Gemini API key not configured")
    clip_duration = max(0.0, clip_end - clip_start)
    words = _extract_clip_words(transcript, clip_start, clip_end)
    if not words:
        return []  # no transcript coverage for this window — confirmed nothing

    prompt = build_player_prompt(words, clip_duration)

    from google import genai

    client = genai.Client(api_key=api_key)
    models = build_model_chain(model)
    response, model_used = generate_with_model_fallback(client, prompt, models, max_attempts=3)
    text = getattr(response, "text", "") or ""

    def _retry_gemini(err_msg: str) -> str:
        retry_prompt = build_player_reformat_prompt(err_msg, text)
        retry_resp, _ = generate_with_model_fallback(
            client, retry_prompt, [model_used], max_attempts=1)
        return retry_resp.text or ""

    parse_result = parse_gemini_response(text, retry_fn=_retry_gemini)
    if parse_result.data is None:
        raise RuntimeError(f"Gemini response unparseable: {parse_result.error}")

    mentions = validate_player_mentions(parse_result.data, clip_duration)
    logger.info(
        "detect_player_mentions: model=%s clip=%.1fs → %d mention(s)",
        model_used, clip_duration, len(mentions),
    )
    return mentions
