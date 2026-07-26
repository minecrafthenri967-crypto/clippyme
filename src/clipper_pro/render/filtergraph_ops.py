"""Pure filtergraph construction: trajectory → crop expression → one ffmpeg pass.

The keyframe-count problem
--------------------------
Phase 5 emits one keyframe per frame — several hundred for a 15-second clip, tens
of thousands for a long one. None of the obvious ways to feed that to ffmpeg work:
a per-frame ``crop`` expression would be a megabyte-long command line, and
``sendcmd`` issues *discrete* commands, so the camera would step between positions
instead of gliding.

The fix is to notice that a smoothed trajectory is mostly straight. Ramer-Douglas-
Peucker reduces it to the few anchor points where it actually changes direction —
typically under a dozen for a clip with two or three camera moves — and those
anchors become a piecewise-**linear** ``crop=x=`` expression in ``t``. ffmpeg then
interpolates between them itself, giving continuous motion from a short
expression. A static camera collapses to a single constant, which is both cheaper
and clearer in a log.

Why one pass
------------
Rendering a reframe, re-opening it to burn subtitles, then re-opening *that* for
overlays runs libx264 three times, and each generation throws away detail; by the
third the result is visibly soft. Composing the whole chain into a single
filtergraph means exactly one decode and one encode, so quality is bounded by one
generation no matter how many layers the recipe carries.

Stdlib-only and host-tested: the entire graph, including the expression, is
verified without running ffmpeg.
"""

from __future__ import annotations

from itertools import pairwise

from clipper_pro.errors import ValidationError
from clipper_pro.types import CameraKeyframe

__all__ = [
    "DEFAULT_OUTPUT_HEIGHT",
    "DEFAULT_OUTPUT_WIDTH",
    "DEFAULT_TOLERANCE_PX",
    "MAX_EXPRESSION_CHARS",
    "build_filtergraph",
    "crop_x_expression",
    "simplify_trajectory",
]

#: 1080x1920 — the standard vertical delivery frame.
DEFAULT_OUTPUT_WIDTH = 1080
DEFAULT_OUTPUT_HEIGHT = 1920

#: Anchors closer than this to the straight line between their neighbours are
#: dropped. Two pixels of crop error is invisible after scaling, and buys an
#: order-of-magnitude shorter expression.
DEFAULT_TOLERANCE_PX = 2.0

#: Ceiling on the generated expression. Beyond this the tolerance is raised and
#: the trajectory re-simplified, so a pathological camera path degrades to a
#: slightly coarser pan rather than producing a command line ffmpeg would reject.
MAX_EXPRESSION_CHARS = 8000


def simplify_trajectory(
    keyframes: list[CameraKeyframe], *, tolerance: float = DEFAULT_TOLERANCE_PX
) -> list[tuple[float, float]]:
    """Reduce ``keyframes`` to the ``(time, x)`` anchors that define the path.

    Ramer-Douglas-Peucker: recursively keep the point furthest from the chord
    between the current endpoints until every dropped point lies within
    ``tolerance`` of the retained polyline. The result reproduces the original
    trajectory to within that tolerance under linear interpolation, which is
    exactly what ffmpeg will do between anchors.
    """
    if tolerance < 0:
        raise ValidationError(f"tolerance must be >= 0, got {tolerance}")
    points = [(kf.time, kf.x) for kf in keyframes]
    if len(points) <= 2:
        return points

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    # Iterative rather than recursive: a 60-fps hour-long clip would otherwise
    # risk the interpreter's recursion limit on a pathological path.
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        worst_index, worst_distance = -1, -1.0
        for index in range(start + 1, end):
            distance = _perpendicular_distance(points[index], points[start], points[end])
            if distance > worst_distance:
                worst_index, worst_distance = index, distance
        if worst_distance > tolerance:
            keep[worst_index] = True
            stack.append((start, worst_index))
            stack.append((worst_index, end))

    return [point for point, kept in zip(points, keep, strict=True) if kept]


def _perpendicular_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Vertical (x-axis) deviation of ``point`` from the chord ``start``→``end``.

    Vertical rather than true perpendicular distance: the two axes here are
    seconds and pixels, so a Euclidean distance would compare unlike units and
    make the tolerance meaningless. What matters is how far off the crop would be
    at that instant, which is precisely the vertical gap.
    """
    (t, x), (t0, x0), (t1, x1) = point, start, end
    if t1 == t0:
        return abs(x - x0)
    interpolated = x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return abs(x - interpolated)


def crop_x_expression(
    anchors: list[tuple[float, float]], *, time_offset: float = 0.0
) -> str:
    """A piecewise-linear ffmpeg expression in ``t`` through ``anchors``.

    ``time_offset`` is subtracted from every anchor time, because the render
    seeks into the source and ffmpeg's ``t`` restarts at zero for the trimmed
    output. Getting this wrong would slide the whole camera path by the clip's
    start time.

    A single anchor (or a path that never moves) yields a bare constant — no
    reason to make ffmpeg evaluate a conditional every frame for a static crop.
    """
    if not anchors:
        raise ValidationError("cannot build a crop expression from no anchors")

    shifted = [(max(0.0, t - time_offset), x) for t, x in anchors]
    if len(shifted) == 1 or all(abs(x - shifted[0][1]) < 1e-6 for _t, x in shifted):
        return f"{shifted[0][1]:.3f}"

    # Built from the last segment backwards so each `if` wraps the remainder,
    # ending with the final anchor's value as the fallback for t past the end.
    expression = f"{shifted[-1][1]:.3f}"
    for (t0, x0), (t1, x1) in reversed(list(pairwise(shifted))):
        span = t1 - t0
        if span <= 0:
            continue
        slope = (x1 - x0) / span
        ramp = f"{x0:.3f}+({slope:.4f})*(t-{t0:.3f})"
        expression = f"if(lt(t,{t1:.3f}),{ramp},{expression})"
    return expression


def build_filtergraph(
    keyframes: list[CameraKeyframe],
    *,
    start: float,
    output_width: int = DEFAULT_OUTPUT_WIDTH,
    output_height: int = DEFAULT_OUTPUT_HEIGHT,
    tolerance: float = DEFAULT_TOLERANCE_PX,
) -> tuple[str, int]:
    """Build the ``crop → scale → setsar`` chain; return ``(graph, anchor_count)``.

    The crop takes the geometry phase 5 chose (constant width/height, moving x);
    scale lifts it to the delivery frame; ``setsar=1`` prevents a non-square
    pixel aspect from surviving into the output, which some players honour and
    others ignore — the difference shows up as a subtly stretched clip.
    """
    if not keyframes:
        raise ValidationError("cannot build a filtergraph without keyframes")
    if output_width <= 0 or output_height <= 0:
        raise ValidationError(
            f"output dimensions must be positive, got {output_width}x{output_height}"
        )

    anchors = simplify_trajectory(keyframes, tolerance=tolerance)
    expression = crop_x_expression(anchors, time_offset=start)

    # Coarsen rather than emit a command line ffmpeg would reject. The ceiling is
    # the trajectory's own travel: at that tolerance every intermediate point is
    # within the chord between the endpoints, so the path collapses to two
    # anchors and the loop is guaranteed to terminate. A magic constant here
    # could be smaller than the path's deviation and loop forever short of the
    # limit.
    xs = [kf.x for kf in keyframes]
    ceiling = max(1.0, max(xs) - min(xs))
    while len(expression) > MAX_EXPRESSION_CHARS and tolerance < ceiling:
        tolerance = min(tolerance * 4, ceiling)
        anchors = simplify_trajectory(keyframes, tolerance=tolerance)
        expression = crop_x_expression(anchors, time_offset=start)

    width = keyframes[0].width
    height = keyframes[0].height
    y = keyframes[0].y
    graph = (
        f"crop=w={width:.0f}:h={height:.0f}:x='{expression}':y={y:.0f}"
        f",scale={output_width}:{output_height}:flags=lanczos"
        f",setsar=1"
    )
    return graph, len(anchors)
