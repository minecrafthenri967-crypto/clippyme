"""Pure speaker-timeline maths: who holds the floor, and when the camera moves.

Prediction without guessing
---------------------------
The pipeline design asks the camera to begin moving ~200 ms *before* the new
speaker is audible, so the result reads as anticipation rather than reaction — a
camera that starts panning on the first phoneme always looks like it was
surprised, and that lag is the clearest tell that a clip was auto-framed.

A live system would have to predict that onset from lip movement. Phase 5 runs
**offline**, after phase 2, so it does not have to: the diarized word timings say
exactly when each speaker starts. Shifting every camera transition earlier by the
lead time buys the same anticipation from lookahead instead of from a guess, which
is both more accurate and far simpler to reason about. MAR then answers the
separate question of *where on screen* each labelled speaker is
(:mod:`clipper_pro.reframe.detect`).

Stdlib-only and host-tested, so the whole decision — every camera cue's timing —
is verified without cv2.
"""

from __future__ import annotations

from clipper_pro.errors import ValidationError
from clipper_pro.types import SpeakerSegment, Word

__all__ = [
    "DEFAULT_GAP_TOLERANCE",
    "DEFAULT_LEAD",
    "DEFAULT_MIN_HOLD",
    "apply_lead",
    "segment_at",
    "speaker_segments",
]

#: A pause longer than this ends a speaker's turn even without a label change.
#: Below it, the natural gaps between words would shred one turn into dozens of
#: segments and make the camera twitch on every breath.
DEFAULT_GAP_TOLERANCE = 0.8

#: How far ahead of audible speech the camera begins moving.
DEFAULT_LEAD = 0.2

#: Minimum time the camera stays on a speaker before it may leave. A rapid
#: two-way exchange would otherwise pan faster than a viewer can follow — worse
#: than simply being a beat late.
DEFAULT_MIN_HOLD = 1.0


def speaker_segments(
    words: list[Word], *, gap_tolerance: float = DEFAULT_GAP_TOLERANCE
) -> list[SpeakerSegment]:
    """Collapse diarized words into contiguous turns.

    A new segment starts when the speaker label changes or when the pause since
    the previous word exceeds ``gap_tolerance``. Words are assumed time-ordered
    (phase 2 guarantees it); an unlabelled transcript yields one segment per
    speech run with ``speaker=None``, which is the correct answer for a
    single-camera monologue.
    """
    if gap_tolerance < 0:
        raise ValidationError(f"gap_tolerance must be >= 0, got {gap_tolerance}")

    segments: list[SpeakerSegment] = []
    current_speaker: int | None = None
    start: float | None = None
    end = 0.0

    for word in words:
        if start is None:
            current_speaker, start, end = word.speaker, word.start, word.end
            continue
        same_speaker = word.speaker == current_speaker
        within_gap = (word.start - end) <= gap_tolerance
        if same_speaker and within_gap:
            end = max(end, word.end)
            continue
        segments.append(_segment(start, end, current_speaker))
        current_speaker, start, end = word.speaker, word.start, word.end

    if start is not None:
        segments.append(_segment(start, end, current_speaker))
    return segments


def _segment(start: float, end: float, speaker: int | None) -> SpeakerSegment:
    # A turn of a single zero-width word is legal upstream but not a usable
    # range; give it a frame's worth of width so the camera has something to do.
    return SpeakerSegment(start, max(end, start + 0.04), speaker)


def apply_lead(
    segments: list[SpeakerSegment],
    *,
    lead: float = DEFAULT_LEAD,
    min_hold: float = DEFAULT_MIN_HOLD,
) -> list[SpeakerSegment]:
    """Move every camera transition earlier by ``lead``, and close the gaps.

    The returned timeline is **contiguous**: each segment ends where the next
    begins, so the camera always points somewhere and phase 6 never has to
    invent a position for a hole. The first segment keeps its start (there is no
    earlier transition to anticipate) and the last keeps its end.

    Two clamps keep the shift honest:

    * a transition never moves earlier than ``min_hold`` after the segment it is
      leaving began, so a fast exchange cannot make the camera jump before it
      has settled;
    * a transition never moves backwards past the previous transition, which
      would reorder the timeline.
    """
    if lead < 0:
        raise ValidationError(f"lead must be >= 0, got {lead}")
    if min_hold < 0:
        raise ValidationError(f"min_hold must be >= 0, got {min_hold}")
    if not segments:
        return []

    starts = [segments[0].start]
    for index in range(1, len(segments)):
        previous_start = starts[index - 1]
        wanted = segments[index].start - lead
        floor = previous_start + min_hold
        # Never before the previous cue, and never so early that the previous
        # speaker got less than min_hold of screen time.
        starts.append(max(wanted, floor, previous_start))

    shifted: list[SpeakerSegment] = []
    for index, segment in enumerate(segments):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(segments) else segment.end
        # A clamp can push a cue past the segment it belongs to; such a turn has
        # been squeezed out entirely and is dropped rather than inverted.
        if end <= start:
            continue
        shifted.append(SpeakerSegment(start, end, segment.speaker))
    return shifted


def segment_at(segments: list[SpeakerSegment], time: float) -> SpeakerSegment | None:
    """The segment covering ``time``, or ``None`` when nothing does.

    Used to sample the timeline per frame. Linear scan on purpose: a clip holds
    tens of segments, and a bisect would trade readability for nothing.
    """
    for segment in segments:
        if segment.start <= time < segment.end:
            return segment
    if segments and time >= segments[-1].end:
        return segments[-1]
    return None
