"""Data contracts passed between pipeline phases.

These are the only types phases share. Keeping them here — rather than letting
each phase invent its own dict shape — is what makes a phase independently
runnable: phase 4 needs a list of :class:`Word` and a list of
:class:`Candidate`, and does not care whether they came from Deepgram and
DeepSeek or from a fixture file.

Every type round-trips through plain JSON via ``to_dict`` / ``from_dict`` so a
run can be checkpointed to disk between phases and resumed (or inspected by an
agent) without a pickle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from clipper_pro.errors import ValidationError

__all__ = [
    "AudioEvent",
    "Candidate",
    "RubricScores",
    "SourceMedia",
    "Word",
]


def _require_range(start: float, end: float, label: str) -> None:
    if start < 0:
        raise ValidationError(f"{label}: start must be >= 0, got {start}")
    if end <= start:
        raise ValidationError(f"{label}: end ({end}) must be greater than start ({start})")


@dataclass(frozen=True)
class SourceMedia:
    """A downloaded or local source video plus the audio extracted from it.

    ``audio_path`` is the mono FLAC produced by phase 1. It is separate from
    ``path`` on purpose: every analysis phase reads the small audio file, and
    only the render phase goes back to the full-resolution video.
    """

    path: str
    duration: float
    title: str = ""
    url: str = ""
    audio_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceMedia:
        return cls(
            path=str(data["path"]),
            duration=float(data.get("duration", 0.0)),
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            audio_path=str(data.get("audio_path", "")),
        )


@dataclass(frozen=True)
class Word:
    """One transcript word with its aligned timing.

    ``start``/``end`` are absolute seconds into the source. Phase 4 snaps clip
    edges to these boundaries, which is why word-level timing (not segment-level)
    is a hard requirement on the transcription provider.
    """

    text: str
    start: float
    end: float
    speaker: int | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValidationError(
                f"word {self.text!r}: end ({self.end}) precedes start ({self.start})"
            )

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Word:
        return cls(
            text=str(data.get("text", data.get("word", ""))),
            start=float(data["start"]),
            end=float(data["end"]),
            speaker=None if data.get("speaker") is None else int(data["speaker"]),
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass(frozen=True)
class AudioEvent:
    """A non-speech audio event (laughter, applause) with its span.

    These are the "emotional payoff" markers: a laugh at 14:32 says the moment
    landed with a live audience, which no amount of reading the transcript text
    would reveal. Phase 3 feeds them to the ranker alongside the words.
    """

    kind: str
    start: float
    end: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValidationError("audio event needs a non-empty kind")
        _require_range(self.start, self.end, f"audio event {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioEvent:
        return cls(
            kind=str(data["kind"]),
            start=float(data["start"]),
            end=float(data["end"]),
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass(frozen=True)
class RubricScores:
    """The 5-axis virality rubric, each axis scored 0–10 by the ranker.

    Kept as five named axes rather than one opaque number so the draft report
    can explain *why* a clip ranked where it did — and so a weighting change is
    a one-line edit here instead of a re-prompt.
    """

    hook: float = 0.0
    emotion: float = 0.0
    quotability: float = 0.0
    completeness: float = 0.0
    density: float = 0.0

    #: Relative weight per axis. Hook strength dominates because a short-form
    #: viewer decides within ~2 seconds; narrative completeness is weighted next
    #: because an unresolved clip reads as clickbait and suppresses retention.
    WEIGHTS: ClassVar[dict[str, float]] = {
        "hook": 0.30,
        "emotion": 0.20,
        "quotability": 0.20,
        "completeness": 0.20,
        "density": 0.10,
    }

    def __post_init__(self) -> None:
        for axis in self.WEIGHTS:
            value = getattr(self, axis)
            if not 0.0 <= value <= 10.0:
                raise ValidationError(
                    f"rubric axis {axis!r} must be within 0–10, got {value}"
                )

    @property
    def total(self) -> float:
        """Weighted 0–10 virality score."""
        return round(
            sum(getattr(self, axis) * weight for axis, weight in self.WEIGHTS.items()), 3
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total"] = self.total
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricScores:
        # ``total`` is derived, so it is accepted and ignored on the way back in.
        return cls(**{axis: float(data.get(axis, 0.0)) for axis in cls.WEIGHTS})


@dataclass(frozen=True)
class Candidate:
    """A proposed clip: a time range, why it was picked, and how it scored.

    Phase 3 emits these with rough transcript-derived edges; phase 4 replaces
    ``start``/``end`` with semantically snapped ones and records the movement in
    ``snapped_from`` so the report can show what the snapping actually did.
    """

    start: float
    end: float
    title: str = ""
    reason: str = ""
    scores: RubricScores = field(default_factory=RubricScores)
    snapped_from: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        _require_range(self.start, self.end, f"candidate {self.title or '<untitled>'}")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "duration": round(self.duration, 3),
            "title": self.title,
            "reason": self.reason,
            "scores": self.scores.to_dict(),
            "snapped_from": list(self.snapped_from) if self.snapped_from else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        snapped = data.get("snapped_from")
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            title=str(data.get("title", "")),
            reason=str(data.get("reason", "")),
            scores=RubricScores.from_dict(data.get("scores") or {}),
            snapped_from=(float(snapped[0]), float(snapped[1])) if snapped else None,
        )
