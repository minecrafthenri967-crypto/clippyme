"""Pure conversion from a ClippyMe transcript dict to phase-2 types.

Both of the host repository's cloud backends return the same dict shape::

    {"text": str,
     "segments": [{"text", "start", "end", "speaker"?,
                   "words": [{"word", "start", "end", "probability", "speaker"?}]}],
     "language": str,
     "audio_events": [{"text", "start", "end"}]}   # ElevenLabs only

This module flattens that into ``list[Word]`` + ``list[AudioEvent]``.

**Sanitise, don't reject.** Transcription is the most expensive step in the
pipeline; a single malformed word out of forty thousand must not throw away a
paid hour of ASR. So a reversed timestamp is repaired, an unparseable entry is
dropped, and the rest of the transcript survives. The count of what was dropped
is returned alongside, so a caller can log it instead of the corruption passing
unnoticed.
"""

from __future__ import annotations

import math
import re

from clipper_pro.types import AudioEvent, Word

__all__ = [
    "EVENT_MIN_DURATION",
    "normalize_event_kind",
    "parse_audio_events",
    "parse_words",
    "weave_text",
]

#: Instantaneous events (start == end) are padded to this many seconds. The
#: marker's *position* is what phase 3 uses; discarding a real laugh because the
#: provider gave it zero width would lose signal for a formality.
EVENT_MIN_DURATION = 0.01

#: "(laughter)" / "[APPLAUSE]" / " Music " → "laughter" / "applause" / "music".
_EVENT_WRAPPER_RE = re.compile(r"^[\s(\[{<]+|[\s)\]}>]+$")
_WHITESPACE_RE = re.compile(r"\s+")


def _as_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # NaN/inf poison every downstream comparison rather than raising anywhere
    # visible; treat them as unparseable at the boundary.
    return result if math.isfinite(result) else None


def _as_speaker(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def normalize_event_kind(text: str) -> str:
    """Reduce a provider's event token to a bare lowercase kind.

    Scribe emits these wrapped and inconsistently cased — ``(laughter)``,
    ``[APPLAUSE]``. Normalising here means phase 3's prompt and any per-kind
    weighting see one spelling per kind.
    """
    cleaned = _EVENT_WRAPPER_RE.sub("", str(text or ""))
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip().lower()
    return cleaned


def parse_words(transcript: dict | None) -> tuple[list[Word], int]:
    """Flatten a transcript's segments into a sorted word list.

    Returns ``(words, dropped)``. Words are sorted by start time: phase 4's
    boundary search assumes monotonic input, and utterance-based segmentation
    can interleave slightly when two speakers overlap.
    """
    if not isinstance(transcript, dict):
        return [], 0

    words: list[Word] = []
    dropped = 0
    for segment in transcript.get("segments") or []:
        if not isinstance(segment, dict):
            dropped += 1
            continue
        # A segment may carry its own speaker label while its words do not.
        segment_speaker = _as_speaker(segment.get("speaker"))
        for raw in segment.get("words") or []:
            if not isinstance(raw, dict):
                dropped += 1
                continue
            text = str(raw.get("word") or raw.get("text") or "")
            start = _as_float(raw.get("start"))
            end = _as_float(raw.get("end"))
            if not text or start is None or end is None or start < 0:
                dropped += 1
                continue
            # Repair rather than discard: a reversed pair still tells us where
            # the word is, and a zero-width word is legal downstream.
            end = max(end, start)
            speaker = _as_speaker(raw.get("speaker"))
            confidence = _as_float(raw.get("probability"))
            if confidence is None:
                confidence = _as_float(raw.get("confidence"))
            words.append(
                Word(
                    text=text,
                    start=start,
                    end=end,
                    speaker=speaker if speaker is not None else segment_speaker,
                    confidence=1.0 if confidence is None else confidence,
                )
            )

    words.sort(key=lambda w: (w.start, w.end))
    return words, dropped


def parse_audio_events(transcript: dict | None) -> tuple[list[AudioEvent], int]:
    """Extract non-speech events from a transcript's ClippyMe extension.

    Returns ``(events, dropped)``. A provider without event tagging simply has
    no ``audio_events`` key, which yields an empty list — not an error, since
    phase 3 degrades to text-only ranking.
    """
    if not isinstance(transcript, dict):
        return [], 0

    events: list[AudioEvent] = []
    dropped = 0
    for raw in transcript.get("audio_events") or []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        kind = normalize_event_kind(raw.get("text") or raw.get("kind") or "")
        start = _as_float(raw.get("start"))
        end = _as_float(raw.get("end"))
        if not kind or start is None or end is None or start < 0:
            dropped += 1
            continue
        if end <= start:
            end = start + EVENT_MIN_DURATION
        confidence = _as_float(raw.get("confidence"))
        events.append(
            AudioEvent(
                kind=kind,
                start=start,
                end=end,
                confidence=1.0 if confidence is None else confidence,
            )
        )

    events.sort(key=lambda e: (e.start, e.end))
    return events, dropped


def weave_text(words: list[Word], events: list[AudioEvent]) -> str:
    """Render a reading copy with events interleaved at their timestamps.

    Phase 3's prompt reads this rather than the bare words, so the ranker sees
    ``…and then I quit (laughter) which turned out…`` — the payoff is in the
    text at the position where it happened, not in a separate list the model has
    to correlate by hand. The word stream itself stays spoken-only, because
    phase 4 snaps cuts to it and must never land on a token nobody said.
    """
    tokens: list[tuple[float, int, str]] = []
    # The middle element orders an event before a word at the same timestamp,
    # keeping the output stable regardless of sort implementation.
    tokens += [(w.start, 1, w.text) for w in words]
    tokens += [(e.start, 0, f"({e.kind})") for e in events]
    tokens.sort(key=lambda t: (t[0], t[1]))
    return " ".join(text for _, _, text in tokens).strip()
