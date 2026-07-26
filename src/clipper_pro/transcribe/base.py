"""Provider-independent transcription contract.

Phase 2 has to satisfy two requirements that no single vendor currently covers:
word-level timing with diarization (which phases 4 and 5 depend on) *and*
non-speech audio events (which phase 3 wants as emotional-payoff markers).
Deepgram Nova-3 delivers the first and not the second; ElevenLabs Scribe
delivers both. Rather than hard-code one and quietly drop a documented
requirement, the provider is named by :class:`ProviderSpec` and declares what
it can actually do, so a caller that needs events can ask for them and be told
plainly when the selected provider cannot supply any.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clipper_pro.types import AudioEvent, Word

__all__ = ["PROVIDERS", "ProviderSpec", "TranscriptResult", "provider_spec"]


@dataclass(frozen=True)
class ProviderSpec:
    """What a transcription provider is and what it can produce."""

    name: str
    #: Whether the provider tags non-speech events (laughter, applause, music).
    supports_audio_events: bool
    #: Whether the provider returns per-word speaker labels.
    supports_diarization: bool
    #: Environment variable holding the credential, for a useful error message.
    api_key_env: str
    default_model: str


PROVIDERS: dict[str, ProviderSpec] = {
    "deepgram": ProviderSpec(
        name="deepgram",
        # Nova-3 has no audio-event tagging. Phase 3 still runs without events;
        # it just loses the "this landed with a live audience" signal.
        supports_audio_events=False,
        supports_diarization=True,
        api_key_env="DEEPGRAM_API_KEY",
        default_model="nova-3",
    ),
    "elevenlabs": ProviderSpec(
        name="elevenlabs",
        supports_audio_events=True,
        supports_diarization=True,
        api_key_env="ELEVENLABS_API_KEY",
        default_model="scribe_v1",
    ),
}


def provider_spec(name: str) -> ProviderSpec:
    """Look up a provider by name, raising for an unknown one."""
    from clipper_pro.errors import ValidationError  # local: avoids a cycle

    spec = PROVIDERS.get((name or "").strip().lower())
    if spec is None:
        raise ValidationError(
            f"unknown transcription provider {name!r} — "
            f"expected one of {', '.join(sorted(PROVIDERS))}"
        )
    return spec


@dataclass(frozen=True)
class TranscriptResult:
    """Everything phase 2 hands downstream.

    ``words`` drives phases 4 and 5; ``events`` feeds phase 3's ranking prompt;
    ``text`` is the reading copy with events woven in at their timestamps.
    """

    words: list[Word] = field(default_factory=list)
    events: list[AudioEvent] = field(default_factory=list)
    language: str = ""
    text: str = ""
    provider: str = ""
    model: str = ""

    @property
    def duration(self) -> float:
        """End of the last word, or 0.0 for an empty transcript."""
        return max((w.end for w in self.words), default=0.0)

    @property
    def speakers(self) -> set[int]:
        """Distinct diarization labels present in the word stream."""
        return {w.speaker for w in self.words if w.speaker is not None}

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "text": self.text,
            "duration": round(self.duration, 3),
            "speakers": sorted(self.speakers),
            "words": [w.to_dict() for w in self.words],
            "events": [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict) -> TranscriptResult:
        return cls(
            words=[Word.from_dict(w) for w in data.get("words") or []],
            events=[AudioEvent.from_dict(e) for e in data.get("events") or []],
            language=str(data.get("language", "")),
            text=str(data.get("text", "")),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
        )
