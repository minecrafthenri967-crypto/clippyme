"""Phase 4 — semantic edge snapping.

Takes phase 3's candidates and returns the same clips with edges that survive
being listened to::

    >>> from clipper_pro.cut import run_cut
    >>> snapped = run_cut(candidates, transcript, media, "/work/run-1")  # doctest: +SKIP
    >>> snapped[0].snapped_from                                          # doctest: +SKIP
    (12.0, 47.2)

The three-stage cascade
-----------------------
This is the phase that separates a clip that sounds edited from one that sounds
chopped. Each stage only runs where the previous one left the edge unsatisfying:

1. **Word alignment** — pull the model's timestamp onto the nearest word
   boundary. An LLM returns round numbers; a cut at 42.0 s lands mid-syllable.
2. **Sentence expansion** — walk the start back to a sentence onset and the end
   forward to terminal punctuation, clamped by the duration cap and by the
   time-adjacent neighbours so an extension never eats the next clip. This is
   what stops a clip opening on "…and that's why I quit."
3. **Waveform nudging** — slide the boundary into an actual trough using ffmpeg
   ``silencedetect``. A sentence boundary in a transcript is a grammatical
   claim; silence is an acoustic fact, and only the second guarantees the cut is
   inaudible.

The arithmetic is the host repository's ``clippyme.pipeline.cut_ops`` — pure,
host-tested, and already carrying the subtleties (neighbour bounds computed in
time order rather than list order, edges left raw when no boundary is within
budget). This package converts types and reports movement; it does not
re-derive the maths. See :mod:`clipper_pro.cut.snap_ops`.
"""

from __future__ import annotations

import json
import os
import sys

from clipper_pro.config import Settings
from clipper_pro.cut.snap_ops import (
    candidates_to_clips,
    clips_to_candidates,
    describe_movement,
    validate_snapped,
    words_to_dicts,
)
from clipper_pro.errors import ToolFailureError
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import Candidate, SourceMedia

__all__ = [
    "CUTS_FILENAME",
    "candidates_to_clips",
    "clips_to_candidates",
    "describe_movement",
    "detect_silences",
    "run_cut",
    "validate_snapped",
    "words_to_dicts",
]

CUTS_FILENAME = "cuts.json"


def detect_silences(audio_path: str, *, noise_db: float = -30.0) -> list[tuple[float, float]]:
    """Silence intervals in ``audio_path``, or ``[]`` when they can't be measured.

    Runs against phase 1's mono FLAC rather than the source video: the analysis
    audio is a fraction of the bytes and ``silencedetect`` decodes audio only
    anyway. Delegates to the host repository's wrapper, which never raises — a
    missing ffmpeg yields ``[]`` and stage 3 is simply skipped, leaving the
    transcript-derived edges intact.
    """
    if not audio_path or not os.path.isfile(audio_path):
        return []
    try:
        from clippyme.pipeline.media_probe import detect_silences as _detect
    except ImportError:  # pragma: no cover - stdlib-only module, always present
        return []
    return _detect(audio_path, noise_db=noise_db)


def run_cut(
    candidates: list[Candidate],
    transcript: TranscriptResult,
    media: SourceMedia,
    work_dir: str,
    *,
    settings: Settings | None = None,
    use_silence: bool | None = None,
) -> list[Candidate]:
    """Snap every candidate's edges and persist ``analysis/cuts.json``."""
    from clippyme.pipeline.cut_ops import snap_clips_to_transcript

    settings = settings or Settings.from_env()
    silence_enabled = settings.snap_silence if use_silence is None else use_silence

    if not candidates:
        raise ToolFailureError("phase 4 has no candidates to snap; run phase 3 first")
    if not transcript.words:
        raise ToolFailureError(
            "phase 4 needs word-level timings from phase 2; the transcript is empty"
        )

    silences = detect_silences(media.audio_path) if silence_enabled else []
    if silence_enabled and not silences:
        print(
            "   ⚠️  no silences measured — keeping transcript-derived edges "
            "(stage 3 skipped)",
            file=sys.stderr,
        )

    # source_duration comes from the transcript rather than the container: it is
    # the timeline every other phase works against, and a container whose
    # duration overruns its audio would otherwise license an edge past the last
    # word.
    source_duration = max(transcript.duration, media.duration or 0.0)

    clips = candidates_to_clips(candidates)
    events = snap_clips_to_transcript(
        clips,
        words_to_dicts(transcript.words),
        source_duration=source_duration,
        silences=silences or None,
    )
    snapped = clips_to_candidates(clips, candidates)

    problems = validate_snapped(snapped, source_duration=source_duration)
    if problems:
        # A bad interaction between the three stages must not reach the renderer.
        raise ToolFailureError(
            "snapped clips are not renderable: " + "; ".join(problems)
        )

    paths = {event.index: event.path for event in events}
    for index, candidate in enumerate(snapped):
        if candidate.snapped_from is not None:
            print(
                f"   ✂️  clip {index + 1}: {describe_movement(candidate)}"
                f" [{paths.get(index, 'snap')}]",
                file=sys.stderr,
            )

    _write_cuts(work_dir, snapped, events=events, silences=len(silences))
    return snapped


def _write_cuts(
    work_dir: str, candidates: list[Candidate], *, events: list, silences: int
) -> str:
    """Persist the snapped clips as the phase's durable artifact (atomic)."""
    analysis_dir = os.path.join(work_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    path = os.path.join(analysis_dir, CUTS_FILENAME)
    payload = {
        "silences_detected": silences,
        "clips_moved": len(events),
        # Which stages fired per clip ("words+sentence+silence"), so a suspicious
        # edge can be traced to the stage that placed it.
        "snap_paths": {str(e.index): e.path for e in events},
        "clips": [c.to_dict() for c in candidates],
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
