"""Phase 2 — high-precision transcription.

Reads the phase-1 FLAC (never the video) and returns word-level timings plus
non-speech audio events::

    >>> from clipper_pro.transcribe import run_transcribe
    >>> result = run_transcribe(media, "/work/run-1")     # doctest: +SKIP
    >>> len(result.words), len(result.events)             # doctest: +SKIP
    (8214, 37)

What the phase guarantees
-------------------------
* **Word-level timing** — non-negotiable. Phase 4 snaps clip edges to word
  boundaries and phase 5 aligns the camera to speech onsets; segment-level
  output cannot drive either.
* **``smart_format``** — punctuation and casing, on by default in both
  backends. Phase 4's sentence expansion keys off terminal punctuation, so an
  unpunctuated transcript silently degrades edge snapping to word-level only.
* **Diarization** — per-word speaker labels, used by phase 5 to cross-check the
  MAR-derived active speaker.
* **Audio events** — laughter/applause, *when the provider supports them*.
  Deepgram Nova-3 does not tag them; ElevenLabs Scribe does. See
  :mod:`clipper_pro.transcribe.base` for why this is a declared capability
  rather than an assumption, and pass ``require_events=True`` to make a missing
  capability an error instead of a silent downgrade.

Results are cached by audio content hash, so re-running phases 3-7 during
development costs nothing.
"""

from __future__ import annotations

import json
import os
import sys

from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError, ValidationError
from clipper_pro.transcribe import cache
from clipper_pro.transcribe.base import (
    PROVIDERS,
    ProviderSpec,
    TranscriptResult,
    provider_spec,
)
from clipper_pro.transcribe.normalize_ops import (
    normalize_event_kind,
    parse_audio_events,
    parse_words,
    weave_text,
)
from clipper_pro.transcribe.providers import (
    effective_model,
    require_api_key,
    transcribe_raw,
)
from clipper_pro.types import SourceMedia

__all__ = [
    "PROVIDERS",
    "ProviderSpec",
    "TranscriptResult",
    "effective_model",
    "normalize_event_kind",
    "parse_audio_events",
    "parse_words",
    "provider_spec",
    "require_api_key",
    "run_transcribe",
    "transcribe_raw",
    "weave_text",
]

TRANSCRIPT_FILENAME = "transcript.json"


def normalize_transcript(
    raw: dict, *, provider: str = "", model: str = ""
) -> tuple[TranscriptResult, int]:
    """Convert a host-repository transcript dict into a :class:`TranscriptResult`.

    Returns the result plus the number of malformed entries that were dropped.
    """
    words, dropped_words = parse_words(raw)
    events, dropped_events = parse_audio_events(raw)
    # Prefer our woven text over the provider's flat transcript so the events
    # sit at their timestamps for phase 3; fall back when there is nothing to
    # weave (no words parsed) so the reading copy is never empty for no reason.
    text = weave_text(words, events) or str(raw.get("text") or "")
    return (
        TranscriptResult(
            words=words,
            events=events,
            language=str(raw.get("language") or ""),
            text=text,
            provider=provider,
            model=model,
        ),
        dropped_words + dropped_events,
    )


def run_transcribe(
    media: SourceMedia,
    work_dir: str,
    *,
    settings: Settings | None = None,
    provider: str | None = None,
    use_cache: bool | None = None,
    require_events: bool = False,
) -> TranscriptResult:
    """Transcribe ``media.audio_path`` and persist the result under ``work_dir``.

    ``require_events=True`` refuses a provider that cannot tag audio events,
    rather than returning an empty event list that looks like "this video had no
    laughter" when it actually means "nobody was listening for any".
    """
    settings = settings or Settings.from_env()
    spec = provider_spec(provider or settings.transcriber)
    caching = settings.transcript_cache if use_cache is None else use_cache

    if require_events and not spec.supports_audio_events:
        supporting = sorted(n for n, s in PROVIDERS.items() if s.supports_audio_events)
        raise ValidationError(
            f"provider {spec.name!r} does not tag audio events; "
            f"use one of {', '.join(supporting)} or drop require_events"
        )

    audio_path = media.audio_path
    if not audio_path or not os.path.isfile(audio_path):
        raise ValidationError(
            f"phase 2 needs the extracted audio from phase 1; "
            f"{audio_path or '<unset>'} is not a file"
        )

    model = effective_model(spec)
    cache_dir = os.path.join(work_dir, "analysis", "cache")
    key = cache.cache_key(audio_path, spec.name, model) if caching else ""

    result: TranscriptResult | None = None
    if caching:
        cached = cache.load(cache_dir, key)
        if cached is not None:
            result = TranscriptResult.from_dict(cached)
            print(
                f"   ↩︎  transcript cache hit ({spec.name}/{model}, "
                f"{len(result.words)} words)",
                file=sys.stderr,
            )

    if result is None:
        if not spec.supports_audio_events:
            print(
                f"   ⚠️  {spec.name} does not tag audio events — phase 3 will "
                f"rank on text alone. Set CLIPPER_PRO_TRANSCRIBER=elevenlabs "
                f"for laughter/applause markers.",
                file=sys.stderr,
            )
        raw = transcribe_raw(audio_path, spec.name)
        result, dropped = normalize_transcript(raw, provider=spec.name, model=model)
        if not result.words:
            raise ToolFailureError(
                f"{spec.name} returned no word-level timings for {audio_path} — "
                f"phases 4 and 5 cannot run without them"
            )
        if dropped:
            print(
                f"   ⚠️  dropped {dropped} malformed transcript entr"
                f"{'y' if dropped == 1 else 'ies'}",
                file=sys.stderr,
            )
        if caching:
            cache.store(cache_dir, key, result.to_dict())

    _write_transcript(work_dir, result)
    return result


def _write_transcript(work_dir: str, result: TranscriptResult) -> str:
    """Persist the transcript as the phase's durable artifact (atomic write)."""
    analysis_dir = os.path.join(work_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    path = os.path.join(analysis_dir, TRANSCRIPT_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(result.to_dict(), fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
