"""Phase 5 — predictive reframing and speaker tracking.

Produces a crop trajectory per clip, consumed by phase 6's filtergraph. This
phase decides *where the camera looks*; it renders nothing::

    >>> from clipper_pro.reframe import run_reframe
    >>> plan = run_reframe(candidates, transcript, media, "/work/run-1")  # doctest: +SKIP
    >>> len(plan[0].keyframes)                                            # doctest: +SKIP
    1440

Three ideas, in the order they matter
-------------------------------------
1. **Anticipation, from lookahead rather than prediction.** The camera begins
   moving ~200 ms before the new speaker is audible, so it reads as anticipation;
   a camera that starts on the first phoneme always looks surprised. Because
   phase 5 runs offline, the diarized word timings already say when each speaker
   starts — so the lead is a shift, not a guess. See
   :mod:`clipper_pro.reframe.speaker_ops`.
2. **MAR resolves position, not speech.** Diarization knows *when* someone
   talks, so mouth-aspect-ratio variance only has to answer *which face* that
   is — a much easier question than identifying speech from video. Variance, not
   instantaneous opening: a mouth held open mid-laugh is wide but silent. See
   :mod:`clipper_pro.reframe.detect`.
3. **Savitzky-Golay smoothing.** A local polynomial fit keeps the shape of an
   intentional fast pan while erasing per-frame jitter, where a moving average
   flattens both and turns a snap into a drift. See
   :mod:`clipper_pro.reframe.camera_ops`.

Layering
--------
Everything decidable without pixels — turn segmentation, the lead shift, crop
geometry, smoothing — is stdlib/numpy and host-tested. Only
:mod:`clipper_pro.reframe.detect` needs cv2 + MediaPipe, so only it is confined
to the Docker integration suite. ``run_reframe`` takes a ``locate`` callable, so
the whole orchestration is testable on a host by injecting known positions.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable

from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError
from clipper_pro.reframe.camera_ops import (
    DEFAULT_ASPECT,
    build_keyframes,
    crop_window,
    smooth_keyframes,
    smoothing_window,
)
from clipper_pro.reframe.plan import CameraPlan, probe_geometry
from clipper_pro.reframe.speaker_ops import apply_lead, segment_at, speaker_segments
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import CameraKeyframe, Candidate, SourceMedia, SpeakerSegment

__all__ = [
    "DEFAULT_ASPECT",
    "REFRAME_FILENAME",
    "CameraPlan",
    "apply_lead",
    "build_keyframes",
    "crop_window",
    "run_reframe",
    "segment_at",
    "smooth_keyframes",
    "smoothing_window",
    "speaker_segments",
]

REFRAME_FILENAME = "reframe.json"

#: Signature of a speaker locator: (video path, segments) -> {label: centre_x}.
Locator = Callable[[str, list[SpeakerSegment]], dict[int | None, float]]


def _default_locator(video_path: str, segments: list[SpeakerSegment]):
    """Use the cv2/MediaPipe locator, degrading to centred crops without it.

    The guard has to wrap the *call*, not the import: ``detect`` imports only
    ``clipper_pro.types`` at module scope and pulls cv2/MediaPipe in call-scoped,
    so importing it succeeds on a host that cannot run it. An unresolved position
    is a documented degradation (``run_reframe`` centres the crop), never a
    reason to fail phase 5.
    """
    try:
        from clipper_pro.reframe.detect import locate_speakers

        return locate_speakers(video_path, segments)
    except ImportError:
        print(
            "   ⚠️  cv2/MediaPipe unavailable — every clip gets a centred crop "
            "(run in the backend image for speaker tracking)",
            file=sys.stderr,
        )
        return {}


def run_reframe(
    candidates: list[Candidate],
    transcript: TranscriptResult,
    media: SourceMedia,
    work_dir: str,
    *,
    settings: Settings | None = None,
    locate: Locator | None = None,
    source_width: int | None = None,
    source_height: int | None = None,
    fps: float | None = None,
) -> list[CameraPlan]:
    """Build a smoothed crop trajectory for every candidate.

    ``locate`` defaults to the cv2 speaker locator; pass one explicitly to test
    the orchestration, or to supply positions from another source. Dimensions and
    fps are probed from the source when not given.
    """
    settings = settings or Settings.from_env()
    if not candidates:
        raise ToolFailureError("phase 5 has no clips to reframe; run phase 4 first")

    width, height, source_fps = _probe_geometry(
        media.path, source_width, source_height, fps
    )

    segments = apply_lead(
        speaker_segments(transcript.words, gap_tolerance=settings.speaker_gap_tolerance),
        lead=settings.camera_lead,
        min_hold=settings.camera_min_hold,
    )
    positions = (locate or _default_locator)(media.path, segments)
    if not positions:
        print(
            "   ⚠️  no speaker positions resolved — falling back to centred crops",
            file=sys.stderr,
        )

    plans: list[CameraPlan] = []
    for index, candidate in enumerate(candidates):
        keyframes = build_keyframes(
            segments,
            positions,
            start=candidate.start,
            end=candidate.end,
            fps=source_fps,
            source_width=width,
            source_height=height,
        )
        keyframes = smooth_keyframes(
            keyframes, fps=source_fps, source_width=width,
            seconds=settings.camera_smooth_seconds,
        )
        moves = _count_moves(keyframes)
        print(
            f"   🎥 clip {index + 1}: {len(keyframes)} keyframes, "
            f"{moves} camera move{'' if moves == 1 else 's'}",
            file=sys.stderr,
        )
        plans.append(
            CameraPlan(
                clip_index=index,
                start=candidate.start,
                end=candidate.end,
                source_width=width,
                source_height=height,
                fps=source_fps,
                keyframes=keyframes,
            )
        )

    _write_plans(work_dir, plans, segments=segments, positions=positions)
    return plans


def _count_moves(keyframes: list[CameraKeyframe]) -> int:
    """Distinct speaker changes across a clip — what a viewer perceives as a cut."""
    moves = 0
    previous = None
    for keyframe in keyframes:
        if previous is not None and keyframe.speaker != previous:
            moves += 1
        previous = keyframe.speaker
    return moves


def _probe_geometry(
    video_path: str, width: int | None, height: int | None, fps: float | None
) -> tuple[int, int, float]:
    """Resolve source dimensions and frame rate, preferring explicit values."""
    if width and height and fps:
        return width, height, fps
    probed_w, probed_h, probed_fps = probe_geometry(video_path)
    return width or probed_w, height or probed_h, fps or probed_fps


def _write_plans(
    work_dir: str,
    plans: list[CameraPlan],
    *,
    segments: list[SpeakerSegment],
    positions: dict[int | None, float],
) -> str:
    """Persist the camera plans as the phase's durable artifact (atomic)."""
    analysis_dir = os.path.join(work_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    path = os.path.join(analysis_dir, REFRAME_FILENAME)
    payload = {
        # The shifted turn timeline and the resolved positions are recorded so a
        # camera cue that looks wrong can be traced to the turn that caused it.
        "speaker_segments": [s.to_dict() for s in segments],
        "speaker_positions": {str(k): v for k, v in positions.items()},
        "clips": [p.to_dict() for p in plans],
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
