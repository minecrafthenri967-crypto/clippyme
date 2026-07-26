"""Phase 5 — predictive reframing and speaker tracking.

Contract
--------
``run_reframe(candidate, source_path) -> list[CameraKeyframe]``

Produces a crop trajectory per clip, consumed by phase 6's filtergraph. This
phase decides *where the camera looks*; it renders nothing itself.

Three ideas, in the order they matter
-------------------------------------
1. **MAR (Mouth Aspect Ratio)** — identify the active speaker from lip-movement
   variance via MediaPipe FaceMesh, not from who is largest or most central.
   Variance over a short window, not instantaneous opening: a person mid-laugh
   or chewing holds a wide mouth without speaking, and only the *variation*
   separates speech from that. Cross-check against phase 2's diarization labels.
2. **Prediction** — begin the pan ~200 ms *before* speech onset, triggered by
   lip movement rather than by audio. A human operator anticipates; a camera
   that starts moving on the first phoneme always reads as reactive, and that
   lag is the tell that a clip was auto-framed.
3. **Savitzky-Golay smoothing** — fit a low-order polynomial over a sliding
   window of camera positions. Chosen over a moving average because it preserves
   the shape of an intentional fast pan while removing per-frame jitter; a mean
   filter flattens both equally and turns a snap to a new speaker into a drift.

The host repository's ``clippyme.pipeline.reframe_track`` / ``reframe_ops``
carry host-tested tracking and camera maths (including an existing MAR
implementation in ``reframe_detect``) — extend those rather than starting over.
Keep new decision logic in the pure ``_ops`` modules so it stays testable
without cv2.
"""

from __future__ import annotations

__all__ = ["run_reframe"]


def run_reframe(*args: object, **kwargs: object) -> None:
    """Not implemented yet — phase 5 of the build."""
    raise NotImplementedError(
        "phase 5 (predictive reframing) is not implemented yet; "
        "phase 1 (clipper_pro.ingest) is the current entry point"
    )
