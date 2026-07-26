"""Phase 2 — high-precision transcription (Deepgram Nova-3).

Contract
--------
``run_transcribe(media: SourceMedia, work_dir) -> tuple[list[Word], list[AudioEvent]]``

Reads ``media.audio_path`` (never the video), writes
``analysis/transcript.json``, returns word-level timings plus non-speech events.

Design notes carried from the pipeline plan
-------------------------------------------
* ``smart_format=true`` — restores punctuation and casing. Phase 4's sentence
  expansion keys off terminal punctuation, so an unpunctuated transcript
  silently degrades edge snapping to word-level only.
* ``diarize=true`` — per-word speaker labels. Phase 5 uses them to sanity-check
  the MAR-derived active speaker; disagreement between the two is a strong
  signal to hold the camera rather than cut to a wrong face.
* **Audio events** (laughter, applause) are a hard requirement, not a nicety:
  they mark where a moment *landed with a live audience*, which is invisible to
  any analysis of the transcript text. Phase 3 consumes them as emotional-payoff
  markers.

Word-level timing is non-negotiable — segment-level output cannot drive phase 4.
"""

from __future__ import annotations

__all__ = ["run_transcribe"]


def run_transcribe(*args: object, **kwargs: object) -> None:
    """Not implemented yet — phase 2 of the build."""
    raise NotImplementedError(
        "phase 2 (transcription) is not implemented yet; "
        "phase 1 (clipper_pro.ingest) is the current entry point"
    )
