"""Cold-open teaser: replay the clip's strongest ~2s BEFORE it plays from its
real start ("show the payoff, then rewind").

The source moment is the ``peak_start``/``peak_end`` window Gemini marks on
each clip (see ``clippyme.schemas.ViralClip``). This module turns that window
into one extra ffmpeg pass that prepends the moment to the clip.

Split like ``player_image.py``: the window maths and the filter graph are pure
(host-tested), the ffmpeg invocation is the only impure part.

⚠️ This is the ONLY compose layer that changes the clip's DURATION, so every
later layer that places something at a timestamp shifts by exactly the teaser
length. ``compose._compose_layers_impl`` threads that offset into the hook's
visible window and the player-image overlay — a new timed layer added after
the teaser must do the same or it will fire at the wrong moment.
"""
import logging
import os
import subprocess

from clippyme.domain.encode import ffmpeg_timeout, x264_intermediate_crf, x264_video_args

logger = logging.getLogger(__name__)

# The prompt asks Gemini for a 1-3s peak, so 3.0 lets a compliant answer
# through untouched and only trims an out-of-spec one. A longer teaser stops
# being a teaser: it spends the attention it was supposed to buy.
DEFAULT_TEASER_MAX_DURATION = 3.0
# Matches schemas.MIN_PEAK_DURATION — below this nothing registers as a moment,
# it just reads as a stutter before the clip starts.
MIN_TEASER_DURATION = 0.8
# A peak that begins within the first second of the clip would make the teaser
# replay footage the viewer is about to see anyway. The prompt already forbids
# it; this is the defence for when the model ignores that.
MIN_TEASER_LEAD = 1.0
# Short fade on the teaser's tail so the jump back to the clip's start reads as
# a deliberate edit rather than a glitch. Applied to audio too — with sound on,
# a hard mid-word audio cut is the most jarring part of the transition.
DEFAULT_TEASER_FADE = 0.12


def resolve_teaser_window(
    clip_info: dict,
    *,
    video_duration: float,
    kept_segments=None,
    max_duration: float = DEFAULT_TEASER_MAX_DURATION,
    min_duration: float = MIN_TEASER_DURATION,
    min_lead: float = MIN_TEASER_LEAD,
):
    """Resolve the peak into a ``(start, end)`` window on the CURRENT timeline.

    ``clip_info`` carries ``peak_start``/``peak_end`` in ABSOLUTE source
    seconds (same space as ``start``/``end``), so they are first rebased to
    clip-relative time.

    ``kept_segments`` is ``smartcut_ops.analyze_silences()``'s kept-span list
    when Smart Cut actually rendered, and ``None`` when it did not. Smart Cut
    shortens the clip, so a pre-Smart-Cut timestamp means nothing in the
    rendered timeline until it is remapped.

    Returns ``None`` — meaning "no teaser for this clip" — whenever the moment
    cannot be placed honestly: no peak, the peak was cut away by Smart Cut,
    it sits at the very top of the clip, or it is too short to register.
    Skipping is always correct here; a teaser built from a guessed window
    opens the video on the wrong footage, which is worse than no teaser.
    """
    from clippyme.domain.smartcut_ops import remap_time_through_kept_segments

    peak_start = clip_info.get("peak_start")
    peak_end = clip_info.get("peak_end")
    if peak_start is None or peak_end is None:
        return None
    try:
        clip_start = float(clip_info.get("start") or 0.0)
        start = float(peak_start) - clip_start
        end = float(peak_end) - clip_start
    except (TypeError, ValueError):
        return None

    if kept_segments is not None:
        start = remap_time_through_kept_segments(start, kept_segments)
        end = remap_time_through_kept_segments(end, kept_segments)
        # Either end landing in a removed span means Smart Cut ate part of the
        # moment. A partially-surviving peak is not the moment any more.
        if start is None or end is None:
            return None

    start = max(0.0, start)
    end = min(float(video_duration), end)
    if start < min_lead:
        return None
    if end - start < min_duration:
        return None
    if end - start > max_duration:
        end = start + max_duration
    return (start, end)


def build_teaser_filter(
    start: float,
    end: float,
    *,
    fade: float = DEFAULT_TEASER_FADE,
    has_audio: bool = True,
) -> str:
    """The ``-filter_complex`` graph that prepends ``[start, end)`` to the clip.

    One pass, one encode generation: the teaser segment and the untouched body
    are trimmed out of the SAME input and concatenated, rather than writing a
    temp file per half.

    The body branch is deliberately the whole input — the clip still plays in
    full after the teaser, so the moment is shown twice by design.
    """
    duration = max(0.0, end - start)
    # Never fade more than half the teaser, or the moment is mostly a fade.
    fade = max(0.0, min(fade, duration / 2.0))
    fade_start = duration - fade

    video = f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS"
    if fade > 0:
        video += f",fade=t=out:st={fade_start:.3f}:d={fade:.3f}"
    parts = [f"{video}[tv]", "[0:v]setpts=PTS-STARTPTS[bv]"]

    if not has_audio:
        parts.append("[tv][bv]concat=n=2:v=1:a=0[outv]")
        return ";".join(parts)

    audio = f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
    if fade > 0:
        audio += f",afade=t=out:st={fade_start:.3f}:d={fade:.3f}"
    parts.append(f"{audio}[ta]")
    parts.append("[0:a]asetpts=PTS-STARTPTS[ba]")
    parts.append("[tv][ta][bv][ba]concat=n=2:v=1:a=1[outv][outa]")
    return ";".join(parts)


def prepend_teaser(
    video_path: str,
    output_path: str,
    *,
    start: float,
    end: float,
    fade: float = DEFAULT_TEASER_FADE,
    has_audio: bool = True,
) -> bool:
    """Render ``video_path`` with its ``[start, end)`` moment prepended.

    ``start``/``end`` are seconds on ``video_path``'s OWN timeline — the caller
    (``compose._apply_teaser``) owns rebasing and Smart-Cut remapping.

    Audio is re-encoded because the concat filter produces a new stream; there
    is no ``-c:a copy`` path here.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")

    filter_complex = build_teaser_filter(start, end, fade=fade, has_audio=has_audio)
    cmd = ["ffmpeg", "-y", "-i", video_path, "-filter_complex", filter_complex,
           "-map", "[outv]"]
    if has_audio:
        cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    cmd += [*x264_video_args(crf=x264_intermediate_crf()), output_path]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=ffmpeg_timeout())
        logger.info("✅ Teaser prepended (%.2fs) → %s",
                    end - start, os.path.basename(output_path))
        return True
    except subprocess.TimeoutExpired:
        logger.error("❌ Teaser ffmpeg timed out after %ss", ffmpeg_timeout())
        raise
    except subprocess.CalledProcessError as e:
        logger.error("❌ Teaser ffmpeg error: %s", e.stderr.decode() if e.stderr else "unknown")
        raise
