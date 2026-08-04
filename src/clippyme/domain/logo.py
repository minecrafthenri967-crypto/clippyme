"""Logo / watermark overlay — burns a user-supplied PNG onto a clip.

Mirrors the hook overlay approach (single ffmpeg overlay pass) but the image is
provided by the user instead of rendered from text. Used as the topmost compose
layer so the brand mark stays visible on every kept frame.

Pure ffmpeg; the only Python-side work is computing the overlay placement
expression from a position preset + margin. The geometry helper is host-unit
tested (no ffmpeg needed) via `logo_overlay_xy`.
"""
import logging
import os
import subprocess

from clippyme.domain.encode import ffmpeg_timeout, x264_intermediate_crf, x264_video_args

logger = logging.getLogger(__name__)

# Anchor presets → (x_expr, y_expr) using ffmpeg overlay variables. `M` is the
# margin in pixels (substituted before building the filter). main_* = base video
# size, overlay_* = scaled logo size, so we never need to know the logo's exact
# pixel dimensions up front.
_POSITIONS = {
    "top-left": ("{M}", "{M}"),
    "top-right": ("main_w-overlay_w-{M}", "{M}"),
    "top-center": ("(main_w-overlay_w)/2", "{M}"),
    "bottom-left": ("{M}", "main_h-overlay_h-{M}"),
    "bottom-right": ("main_w-overlay_w-{M}", "main_h-overlay_h-{M}"),
    "bottom-center": ("(main_w-overlay_w)/2", "main_h-overlay_h-{M}"),
    "center": ("(main_w-overlay_w)/2", "(main_h-overlay_h)/2"),
}

DEFAULT_POSITION = "top-right"


def parse_logo_position_xy(position) -> tuple[float, float] | None:
    """Return ``position`` as an (x, y) 0..1 fraction pair, or ``None`` for a
    keyword preset.

    The logo editor lets the user drag the logo anywhere on a 9:16 preview
    instead of picking a corner preset; it stores that as ``{"x": .., "y": ..}``
    (fraction of the space the overlay has to move in — 0 flush against the
    start edge, 1 flush against the end edge, matching ffmpeg's
    ``(main_w-overlay_w)*x`` normalized placement). Named presets keep working
    unchanged for old recipes/history.
    """
    if not isinstance(position, dict):
        return None
    try:
        x = float(position["x"])
        y = float(position["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return x, y


def logo_overlay_xy(position, margin_px: int) -> tuple[str, str]:
    """Return the (x, y) ffmpeg overlay expressions for a position.

    ``position`` is either a preset keyword (falls back to the default corner
    for an unknown one) or a ``{"x": .., "y": ..}`` free-placement fraction
    from the drag editor, in which case ``margin_px`` is ignored — a dragged
    position already accounts for its own distance from the edge. Pure string
    math → host-unit-testable without ffmpeg or a real video.
    """
    xy = parse_logo_position_xy(position)
    if xy is not None:
        x, y = xy
        return f"(main_w-overlay_w)*{x:.4f}", f"(main_h-overlay_h)*{y:.4f}"
    x_tpl, y_tpl = _POSITIONS.get(position, _POSITIONS[DEFAULT_POSITION])
    m = max(0, int(margin_px))
    return x_tpl.format(M=m), y_tpl.format(M=m)


def logo_filter_chain(video_width, scale=0.18, opacity=1.0, margin=0.04,
                      position=DEFAULT_POSITION):
    """(scale/alpha filter chain, x_expr, y_expr) for overlaying the logo.

    Shared by the standalone logo pass and the single-pass hook+logo encode so
    the size/opacity clamps and geometry can't drift between them. Pure →
    host-unit-testable.
    """
    scale = min(0.5, max(0.05, float(scale)))
    opacity = min(1.0, max(0.0, float(opacity)))
    logo_w = max(1, int(video_width * scale))
    margin_px = int(video_width * max(0.0, float(margin)))
    x_expr, y_expr = logo_overlay_xy(position, margin_px)
    chain = f"scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}"
    return chain, x_expr, y_expr


def add_logo_to_video(
    video_path: str,
    logo_path: str,
    output_path: str,
    position: str = DEFAULT_POSITION,
    scale: float = 0.18,
    opacity: float = 1.0,
    margin: float = 0.04,
) -> bool:
    """Overlay a logo PNG onto a video.

    position: a _POSITIONS keyword (default top-right), or a free-placement
              {"x": .., "y": ..} fraction from the drag editor
    scale:    logo width as a fraction of the video width (0.05–0.5)
    opacity:  0.0–1.0 alpha multiplier
    margin:   gap from the frame edge as a fraction of the video width
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")
    if not os.path.exists(logo_path):
        raise FileNotFoundError(f"Logo {logo_path} not found")

    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
               "-of", "csv=s=x:p=0", video_path]
        dims = subprocess.check_output(cmd, timeout=30).decode().strip().split("\n")[0].split("x")
        video_width, video_height = int(dims[0]), int(dims[1])
    except Exception as exc:
        # The 1080×1920 assumption is wrong for 1:1 / 16:9 jobs — a silently
        # misplaced watermark with no trace is worse than a loud fallback.
        logger.warning("Logo ffprobe failed on %s (%s) — assuming 1080x1920",
                       os.path.basename(video_path), exc)
        video_width, video_height = 1080, 1920

    # Scale the logo to the target width (height auto, aspect preserved), force
    # an alpha channel, then apply the opacity multiplier before overlaying.
    logo_chain, x_expr, y_expr = logo_filter_chain(video_width, scale, opacity, margin, position)
    filter_complex = f"[1:v]{logo_chain}[lg];[0:v][lg]overlay={x_expr}:{y_expr}"

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", logo_path,
        "-filter_complex", filter_complex,
        "-c:a", "copy",
        # Shared near-visually-lossless encode (CRF 18 / medium). Logo is the
        # last compose layer → +faststart for progressive playback. encode.py.
        *x264_video_args(crf=x264_intermediate_crf()),
        output_path,
    ]
    try:
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=ffmpeg_timeout())
        logger.info("✅ Logo overlaid → %s", os.path.basename(output_path))
        return True
    except subprocess.TimeoutExpired:
        logger.error("❌ Logo ffmpeg timed out after %ss", ffmpeg_timeout())
        raise
    except subprocess.CalledProcessError as e:
        logger.error("❌ Logo ffmpeg error: %s", e.stderr.decode() if e.stderr else "unknown")
        raise
