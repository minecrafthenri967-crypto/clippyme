"""Locate each diarized speaker on screen — the cv2/MediaPipe half of phase 5.

Requires the heavy CV runtime (cv2 + MediaPipe), so it is **not** importable on a
dev host and is exercised only by the Docker integration suite. Every decision
that can be made without pixels lives in ``speaker_ops`` / ``camera_ops``, which
are host-tested; this module answers one question and nothing else:

    given that phase 2 says speaker 1 was talking from 12.5s to 17.8s,
    which face on screen is speaker 1?

How MAR is used
---------------
Not as an absolute measure. A mouth held open — mid-laugh, chewing, a resting
open jaw — reads as wide on any single frame, so instantaneous MAR cannot
distinguish speech. What distinguishes it is *variance over time*: a talking
mouth oscillates, a still one does not. So for each speaker's turn we sample
frames, compute MAR per detected face, and attribute the turn to the face whose
MAR varies most across it.

That is the cross-check the pipeline design asks for, arrived at from the other
direction: diarization already knows *when*, so MAR only has to resolve *where*,
which is a far easier question than identifying speech from video alone.
"""

from __future__ import annotations

from clipper_pro.types import SpeakerSegment

__all__ = ["DEFAULT_SAMPLES_PER_SEGMENT", "locate_speakers"]

#: Frames sampled per speaker turn. Enough to measure mouth variance; small
#: enough that a ten-minute source costs seconds rather than minutes, since a
#: face's screen position does not move much within one turn.
DEFAULT_SAMPLES_PER_SEGMENT = 12

#: A turn shorter than this is skipped — too few frames to measure variance, and
#: its speaker will be located from their other turns anyway.
_MIN_SEGMENT_SECONDS = 0.5

#: Minimum MAR samples a face-track needs before its variance is trusted. Two
#: points always describe a straight line, so variance below this is noise.
_MIN_TRACK_SAMPLES = 3


def _face_box(face: object) -> tuple[float, float, float, float] | None:
    """Normalise one detector result to ``(x, y, w, h)``, or None if unusable.

    ``detect_face_candidates`` yields ``{"box": [x, y, w, h], "score": …}``; a
    plain 4-sequence is accepted too so a caller can inject a simpler detector.
    Anything else is not a face this module can measure, which is a "no
    position" outcome rather than an error — see ``locate_speakers``.
    """
    raw = face.get("box") if isinstance(face, dict) else face
    try:
        x, y, w, h = (float(value) for value in tuple(raw)[:4])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def locate_speakers(
    video_path: str,
    segments: list[SpeakerSegment],
    *,
    samples_per_segment: int = DEFAULT_SAMPLES_PER_SEGMENT,
) -> dict[int | None, float]:
    """Map each speaker label to their horizontal centre in source pixels.

    Returns only the labels it could resolve; callers fall back to the frame
    centre for the rest (see ``camera_ops.build_keyframes``). Never raises for
    detector trouble — an undetected face means "no position", which degrades to
    a centred crop rather than failing a render.
    """
    import cv2  # heavy import, deliberately call-scoped

    from clippyme.pipeline.reframe_detect import (
        compute_mouth_aspect_ratio,
        detect_face_candidates,
    )

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return {}

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        if fps <= 0:
            return {}
        # label -> list of (mar_variance, center_x) contributions
        evidence: dict[int | None, list[tuple[float, float]]] = {}

        for segment in segments:
            if segment.duration < _MIN_SEGMENT_SECONDS:
                continue
            best = _best_face_for_segment(
                capture,
                cv2,
                compute_mouth_aspect_ratio,
                detect_face_candidates,
                segment,
                fps,
                samples_per_segment,
            )
            if best is not None:
                evidence.setdefault(segment.speaker, []).append(best)

        positions: dict[int | None, float] = {}
        for label, samples in evidence.items():
            # Weight each turn's answer by how confidently it was made, so one
            # ambiguous turn cannot outvote several clear ones.
            total_weight = sum(weight for weight, _ in samples)
            if total_weight <= 0:
                continue
            positions[label] = sum(w * x for w, x in samples) / total_weight
        return positions
    finally:
        capture.release()


def _best_face_for_segment(
    capture, cv2, mar_fn, detect_fn, segment: SpeakerSegment, fps: float, samples: int
) -> tuple[float, float] | None:
    """Return ``(mar_variance, center_x)`` for the most-moving mouth in a turn."""
    step = segment.duration / max(1, samples)
    # box-key -> (mar samples, x-centre samples)
    tracks: dict[tuple[int, int], tuple[list[float], list[float]]] = {}

    for index in range(samples):
        timestamp = segment.start + index * step
        capture.set(cv2.CAP_PROP_POS_FRAMES, round(timestamp * fps))
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        try:
            faces = detect_fn(frame) or []
        except Exception:  # noqa: BLE001, S112 — see module docstring
            continue
        for face in faces:
            box = _face_box(face)
            if box is None:
                continue
            x, y, w, h = box
            try:
                mar = mar_fn(frame, (x, y, w, h))
            except Exception:  # noqa: BLE001 — detector trouble means "no position"
                mar = None
            if mar is None:
                continue
            # Bucket by coarse position so the same face across frames lands in
            # one track without needing full identity association.
            key = (int(x + w / 2) // 64, int(y + h / 2) // 64)
            mars, centers = tracks.setdefault(key, ([], []))
            mars.append(float(mar))
            centers.append(float(x + w / 2))

    best: tuple[float, float] | None = None
    for mars, centers in tracks.values():
        if len(mars) < _MIN_TRACK_SAMPLES:
            continue
        mean = sum(mars) / len(mars)
        variance = sum((m - mean) ** 2 for m in mars) / len(mars)
        center = sum(centers) / len(centers)
        if best is None or variance > best[0]:
            best = (variance, center)
    return best
