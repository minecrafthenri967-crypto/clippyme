"""Pure camera maths: crop geometry, trajectory sampling, Savitzky-Golay smoothing.

The 9:16 window is the tallest rectangle that fits the source, so no upscaling is
ever needed — the crop only ever discards pixels, which is why a 1080p source
still yields a full-height vertical clip.

Smoothing goes through the host repository's ``reframe_ops.smooth_and_clamp``
(pure numpy, no scipy). Savitzky-Golay rather than a moving average because it
fits a local polynomial: it preserves the shape of an intentional fast pan while
removing per-frame jitter, where a mean filter flattens both equally and turns a
deliberate snap to a new speaker into a slow drift.
"""

from __future__ import annotations

from clipper_pro.errors import ValidationError
from clipper_pro.reframe.speaker_ops import segment_at
from clipper_pro.types import CameraKeyframe, SpeakerSegment

__all__ = [
    "DEFAULT_ASPECT",
    "DEFAULT_POLYORDER",
    "DEFAULT_SMOOTH_SECONDS",
    "build_keyframes",
    "crop_window",
    "smooth_keyframes",
    "smoothing_window",
]

#: 9:16 — the vertical short-form frame.
DEFAULT_ASPECT = 9.0 / 16.0

#: Seconds of trajectory the smoother looks across. Roughly a third of a second
#: is long enough to erase detector jitter and short enough that a real pan
#: survives it.
DEFAULT_SMOOTH_SECONDS = 0.35

#: Cubic. High enough to follow an ease-in/ease-out pan, low enough not to
#: re-introduce the wobble the filter is there to remove.
DEFAULT_POLYORDER = 3


def crop_window(
    center_x: float,
    source_width: int,
    source_height: int,
    *,
    aspect: float = DEFAULT_ASPECT,
) -> tuple[float, float, float, float]:
    """The ``(x, y, w, h)`` crop centred as near ``center_x`` as bounds allow.

    Full source height is kept and the width derived from ``aspect``, so the
    result never upscales. When the requested centre would put the window off
    frame it is clamped, which is deliberate: sliding a pan to a stop at the edge
    looks intentional, whereas letting it run off produces black bars.
    """
    if source_width <= 0 or source_height <= 0:
        raise ValidationError(
            f"source dimensions must be positive, got {source_width}x{source_height}"
        )
    if aspect <= 0:
        raise ValidationError(f"aspect must be positive, got {aspect}")

    width = min(float(source_width), source_height * aspect)
    height = min(float(source_height), width / aspect)
    x = center_x - width / 2.0
    x = max(0.0, min(x, source_width - width))
    y = max(0.0, (source_height - height) / 2.0)
    return x, y, width, height


def smoothing_window(fps: float, seconds: float = DEFAULT_SMOOTH_SECONDS) -> int:
    """Odd frame count spanning ``seconds`` at ``fps`` — the smoother's window.

    Expressed in seconds rather than frames so the same visual smoothness holds
    at 24, 30 and 60 fps instead of getting three different results.
    """
    if fps <= 0:
        raise ValidationError(f"fps must be positive, got {fps}")
    frames = round(fps * max(seconds, 0.0))
    if frames < 3:
        return 3
    return frames if frames % 2 == 1 else frames + 1


def build_keyframes(
    segments: list[SpeakerSegment],
    positions: dict[int | None, float],
    *,
    start: float,
    end: float,
    fps: float,
    source_width: int,
    source_height: int,
    aspect: float = DEFAULT_ASPECT,
) -> list[CameraKeyframe]:
    """Sample the speaker timeline into one keyframe per frame over ``[start, end)``.

    ``positions`` maps a diarization label to that speaker's horizontal centre in
    source pixels. A label with no known position falls back to the frame centre
    rather than being skipped: an unlocated speaker should leave the camera in a
    neutral place, not somewhere arbitrary.
    """
    if fps <= 0:
        raise ValidationError(f"fps must be positive, got {fps}")
    if end <= start:
        raise ValidationError(f"end ({end}) must be greater than start ({start})")

    default_x = source_width / 2.0
    frame_count = max(1, round((end - start) * fps))

    keyframes: list[CameraKeyframe] = []
    for index in range(frame_count):
        time = start + index / fps
        segment = segment_at(segments, time)
        speaker = segment.speaker if segment else None
        center_x = positions.get(speaker, default_x)
        x, y, width, height = crop_window(
            center_x, source_width, source_height, aspect=aspect
        )
        keyframes.append(
            CameraKeyframe(
                time=time, x=x, y=y, width=width, height=height, speaker=speaker
            )
        )
    return keyframes


def smooth_keyframes(
    keyframes: list[CameraKeyframe],
    *,
    fps: float,
    source_width: int,
    seconds: float = DEFAULT_SMOOTH_SECONDS,
    polyorder: int = DEFAULT_POLYORDER,
) -> list[CameraKeyframe]:
    """Low-pass the horizontal trajectory, leaving every other field alone.

    Only ``x`` moves, because the window keeps full source height and so has no
    vertical trajectory to smooth. The result is clamped back into frame, since a
    polynomial fit can overshoot past the ends of a fast pan.
    """
    if len(keyframes) < 3:
        return list(keyframes)

    from clippyme.pipeline.reframe_ops import smooth_and_clamp

    width = keyframes[0].width
    smoothed = smooth_and_clamp(
        [kf.x for kf in keyframes],
        smoothing_window(fps, seconds),
        polyorder,
        0.0,
        max(0.0, source_width - width),
    )
    return [
        CameraKeyframe(
            time=kf.time,
            x=float(new_x),
            y=kf.y,
            width=kf.width,
            height=kf.height,
            speaker=kf.speaker,
        )
        for kf, new_x in zip(keyframes, smoothed, strict=True)
    ]
