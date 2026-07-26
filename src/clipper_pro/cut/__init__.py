"""Phase 4 — semantic edge snapping.

Contract
--------
``run_cut(candidates, words, audio_path) -> list[Candidate]``

Returns candidates whose ``start``/``end`` sit on defensible boundaries, with
the pre-snap values preserved in ``snapped_from``.

The three-stage cascade
-----------------------
This is the phase that separates a clip that sounds edited from one that sounds
chopped. Each stage runs only if the previous one left the edge unsatisfying:

1. **Word alignment** — pull the raw LLM-proposed timestamp onto the nearest
   word boundary. An LLM returns round numbers; a cut at 42.0s lands mid-syllable.
2. **Sentence expansion** — walk the start back to a sentence onset and the end
   forward to a terminal punctuation mark, subject to the maximum clip length.
   This is what stops a clip opening on "...and that's why I quit."
3. **Waveform nudging** — with ffmpeg ``silencedetect``, slide the boundary into
   an actual trough in the audio. Sentence boundaries in a transcript are a
   grammatical claim; silence is an acoustic fact, and only the second one
   guarantees the cut is inaudible.

The host repository's ``clippyme.pipeline.cut_ops`` already implements this
cascade and is the porting source — reuse it rather than re-deriving the maths.
"""

from __future__ import annotations

__all__ = ["run_cut"]


def run_cut(*args: object, **kwargs: object) -> None:
    """Not implemented yet — phase 4 of the build."""
    raise NotImplementedError(
        "phase 4 (edge snapping) is not implemented yet; "
        "phase 1 (clipper_pro.ingest) is the current entry point"
    )
