"""Player-image library + overlay — burns an athlete's photo onto a clip at
the moment their name is mentioned (e.g. a trading-card pull on a live
sports-card-break stream).

Two halves, one file (mirrors the size of `logo.py`, which this overlay is
structurally closest to — a user-supplied image, not text rendered by us):

- **Library** (`list_player_images`, `_normalize_name`, `match_player_image`):
  pure, host-testable name matching against the uploaded collection in
  `PLAYER_IMAGES_DIR` (one PNG per player, uploaded via
  `POST /api/config/player-images`).
- **Render** (`player_image_filter_chain`, `add_player_image_to_video`): a
  single, timed ffmpeg overlay pass, exactly like `logo.py`'s watermark but
  bounded to a `between(t, start, start+duration)` window instead of the
  whole clip (reuses `hooks._enable_suffix`).
"""
import logging
import os
import re
import subprocess
import unicodedata

from clippyme.domain.encode import ffmpeg_timeout, x264_intermediate_crf, x264_video_args
from clippyme.domain.hooks import _enable_suffix
from clippyme.domain.logo import logo_overlay_xy

logger = logging.getLogger(__name__)

PLAYER_IMAGES_DIR = os.environ.get("CLIPPYME_PLAYER_IMAGES_DIR") or os.path.join("data", "player_images")
_PLAYER_IMAGE_EXT = ".png"
# Deliberately more permissive than subtitles._FONT_NAME_RE (ASCII-only, no
# path role) — a player name is never interpolated into an ASS/drawtext
# directive, only used as a path component and a plain string-match target.
# Real athlete names routinely contain periods ("Jr."), apostrophes
# ("De'Aaron"), and accented letters ("Acuña"). \w is unicode-aware in
# Python 3 `str` patterns; no path separator is in the allow-list and the
# name may not START with punctuation, so `..`/`/` can never appear.
_PLAYER_NAME_RE = re.compile(r"^\w[\w .'\-]{0,79}$")


def list_player_images() -> list[str]:
    """Basenames (no extension) of every uploaded player image."""
    if not os.path.isdir(PLAYER_IMAGES_DIR):
        return []
    names = []
    for fn in os.listdir(PLAYER_IMAGES_DIR):
        if fn.lower().endswith(_PLAYER_IMAGE_EXT):
            names.append(os.path.splitext(fn)[0])
    return sorted(names)


def _normalize_name(s: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace.

    Pure — used to compare a Gemini-detected player name against the image
    library's stored (unnormalized) names regardless of case/accents/
    punctuation differences (e.g. "De'Aaron Fox" vs "de aaron fox").
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return " ".join(s.lower().split())


def match_player_image(player_name: str, available_names: list[str] | None = None) -> str | None:
    """Exact match (after normalization) between `player_name` and the
    library. Returns the ORIGINAL stored name (needed to rebuild the file
    path), or None. Never raises."""
    if not player_name:
        return None
    names = list_player_images() if available_names is None else available_names
    target = _normalize_name(player_name)
    if not target:
        return None
    for name in names:
        if _normalize_name(name) == target:
            return name
    return None


# ---------------------------------------------------------------------------
# Render — a single, timed ffmpeg overlay pass (mirrors logo.py's watermark,
# bounded to a between(t, start, start+duration) window instead of the whole
# clip). Positioning reuses logo.logo_overlay_xy's preset table; timing
# reuses hooks._enable_suffix generalized to an arbitrary start.
# ---------------------------------------------------------------------------

DEFAULT_PLAYER_IMAGE_POSITION = "center"
DEFAULT_PLAYER_IMAGE_DURATION = 2.5  # seconds the photo stays on screen
_DURATION_MIN, _DURATION_MAX = 0.5, 8.0
# Wider than logo.py's [0.05, 0.5] — this is a featured "flash" reveal for
# the campaign content, not a small persistent watermark.
_SCALE_MIN, _SCALE_MAX = 0.10, 0.90


def player_image_filter_chain(video_width, scale=0.55, opacity=1.0, margin=0.04,
                              position=DEFAULT_PLAYER_IMAGE_POSITION):
    """(scale/alpha filter chain, x_expr, y_expr) for overlaying the player
    photo. Same shape as logo.logo_filter_chain, own (wider) scale clamp.
    Pure → host-unit-testable."""
    scale = min(_SCALE_MAX, max(_SCALE_MIN, float(scale)))
    opacity = min(1.0, max(0.0, float(opacity)))
    img_w = max(1, int(video_width * scale))
    margin_px = int(video_width * max(0.0, float(margin)))
    x_expr, y_expr = logo_overlay_xy(position, margin_px)
    chain = f"scale={img_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}"
    return chain, x_expr, y_expr


def add_player_image_to_video(
    video_path: str,
    image_path: str,
    output_path: str,
    *,
    start: float,
    duration: float = DEFAULT_PLAYER_IMAGE_DURATION,
    position: str = DEFAULT_PLAYER_IMAGE_POSITION,
    scale: float = 0.55,
    opacity: float = 1.0,
    margin: float = 0.04,
) -> bool:
    """Overlay a player photo onto a video for a timed window
    ``[start, start+duration)``.

    ``start``/``duration`` are output-timeline seconds — the caller
    (``compose._apply_player_image``) is responsible for remapping a
    pre-Smart-Cut timestamp and clamping against the actual clip duration;
    this function only clamps ``duration`` to a sane display window.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Player image {image_path} not found")

    from clippyme.pipeline.media_probe import probe_dimensions

    video_width, _ = probe_dimensions(video_path)

    start = max(0.0, float(start))
    duration = min(_DURATION_MAX, max(_DURATION_MIN, float(duration)))
    enable_end = start + duration

    chain, x_expr, y_expr = player_image_filter_chain(video_width, scale, opacity, margin, position)
    suffix = _enable_suffix(enable_end, start)
    filter_complex = f"[1:v]{chain}[pi];[0:v][pi]overlay={x_expr}:{y_expr}{suffix}"

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", image_path,
        "-filter_complex", filter_complex,
        "-c:a", "copy",
        *x264_video_args(crf=x264_intermediate_crf()),
        output_path,
    ]
    try:
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=ffmpeg_timeout())
        logger.info("✅ Player image overlaid → %s", os.path.basename(output_path))
        return True
    except subprocess.TimeoutExpired:
        logger.error("❌ Player image ffmpeg timed out after %ss", ffmpeg_timeout())
        raise
    except subprocess.CalledProcessError as e:
        logger.error("❌ Player image ffmpeg error: %s", e.stderr.decode() if e.stderr else "unknown")
        raise
