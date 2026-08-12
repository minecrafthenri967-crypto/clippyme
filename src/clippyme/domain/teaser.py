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

# How the teaser hands over to the clip's real start:
#   "punch" — the tail zooms in fast, then hard-cuts. The editing idiom for
#             "that was a flash-forward, now we rewind"; reads as energy
#             rather than as an error, which a bare cut can.
#   "fade"  — the tail fades to black instead. Calmer, and the fallback when
#             the punch cannot be built (see build_teaser_filter).
#   "none"  — hard cut, no video treatment.
# The AUDIO fade is applied for "punch" and "fade" alike: it is not decoration,
# it is what stops the cut from landing mid-word as a click.
TEASER_TRANSITIONS = ("punch", "fade", "none")
DEFAULT_TEASER_TRANSITION = "punch"
# Zoom reached at the moment of the cut. 1.12 (a 12% push) read as too subtle
# in practice — barely distinguishable from noise at normal playback speed.
# 1.35 is a real lunge, still short of turning the last frames into a soft
# blur of whatever pixels the crop left (the same real-pixel budget reframe
# worries about).
DEFAULT_PUNCH_ZOOM = 1.35
# The push happens only at the very end; before it the teaser plays untouched.
# Widened alongside the stronger zoom so the motion has room to read as a
# push rather than a single-frame jump.
DEFAULT_PUNCH_DURATION = 0.35


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


def build_punch_zoom_expr(duration: float, zoom: float, punch_duration: float) -> str:
    """ffmpeg expression for the zoom factor over the teaser's own timeline.

    Flat at 1.0 until ``duration - punch_duration``, then accelerating (the
    ramp is squared) up to ``zoom`` at the cut. Squared rather than linear
    because a linear push reads as a slow creep; the snap is what sells "we
    are jumping back now".

    Commas are backslash-escaped because the result is embedded in a
    ``-filter_complex`` string, where a bare comma separates filters.
    """
    punch_duration = max(1e-3, min(punch_duration, duration))
    ramp_start = max(0.0, duration - punch_duration)
    amount = max(0.0, zoom - 1.0)
    progress = f"max(0\\,min(1\\,(t-{ramp_start:.3f})/{punch_duration:.3f}))"
    return f"1+{amount:.4f}*pow({progress}\\,2)"


def build_teaser_filter(
    start: float,
    end: float,
    *,
    fade: float = DEFAULT_TEASER_FADE,
    has_audio: bool = True,
    transition: str = DEFAULT_TEASER_TRANSITION,
    punch: float = DEFAULT_PUNCH_ZOOM,
    punch_duration: float = DEFAULT_PUNCH_DURATION,
    width: int = None,
    height: int = None,
) -> str:
    """The ``-filter_complex`` graph that prepends ``[start, end)`` to the clip.

    One pass, one encode generation: the teaser segment and the untouched body
    are trimmed out of the SAME input and concatenated, rather than writing a
    temp file per half.

    The body branch is deliberately the whole input — the clip still plays in
    full after the teaser, so the moment is shown twice by design.

    ``transition`` picks the video treatment on the teaser's tail (see
    TEASER_TRANSITIONS). The zoom punch needs ``width``/``height``: it scales
    the frame up and crops back to size, and ``crop`` cannot express "the
    dimensions I had before the scale" — inside crop, ``iw`` is already the
    scaled width. Without usable dimensions it degrades to the fade rather
    than emitting a graph that would fail at render time.
    """
    duration = max(0.0, end - start)
    # Never fade more than half the teaser, or the moment is mostly a fade.
    fade = max(0.0, min(fade, duration / 2.0))
    fade_start = duration - fade

    if transition not in TEASER_TRANSITIONS:
        transition = DEFAULT_TEASER_TRANSITION
    if transition == "punch" and not (width and height):
        logger.info("teaser: no frame dimensions for the zoom punch — using the fade")
        transition = "fade"

    video = f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS"
    if transition == "punch":
        zoom = build_punch_zoom_expr(duration, punch, punch_duration)
        # ceil() guards the crop: at zoom 1.0 the scaled frame is exactly the
        # crop size, and a float undershoot of even one pixel makes crop abort
        # the whole render with "Invalid too big or non positive size".
        video += (
            f",scale=w='ceil(iw*({zoom}))':h='ceil(ih*({zoom}))':eval=frame"
            f",crop={int(width)}:{int(height)}"
        )
    elif transition == "fade" and fade > 0:
        video += f",fade=t=out:st={fade_start:.3f}:d={fade:.3f}"
    parts = [f"{video}[tv]", "[0:v]setpts=PTS-STARTPTS[bv]"]

    if not has_audio:
        parts.append("[tv][bv]concat=n=2:v=1:a=0[outv]")
        return ";".join(parts)

    audio = f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
    # The audio fade rides BOTH video treatments: the punch is a visual cue,
    # it does nothing about the sound cutting off mid-word.
    if fade > 0 and transition != "none":
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
    transition: str = DEFAULT_TEASER_TRANSITION,
    punch: float = DEFAULT_PUNCH_ZOOM,
    punch_duration: float = DEFAULT_PUNCH_DURATION,
) -> bool:
    """Render ``video_path`` with its ``[start, end)`` moment prepended.

    ``start``/``end`` are seconds on ``video_path``'s OWN timeline — the caller
    (``compose._apply_teaser``) owns rebasing and Smart-Cut remapping.

    Audio is re-encoded because the concat filter produces a new stream; there
    is no ``-c:a copy`` path here.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")

    width = height = None
    if transition == "punch":
        # Only the punch needs these, and a probe failure must cost the punch
        # (build_teaser_filter falls back to the fade), never the teaser.
        try:
            from clippyme.pipeline.media_probe import probe_dimensions

            width, height = probe_dimensions(video_path)
        except Exception:
            logger.warning("teaser: could not probe dimensions for the zoom punch",
                           exc_info=True)

    filter_complex = build_teaser_filter(
        start, end, fade=fade, has_audio=has_audio, transition=transition,
        punch=punch, punch_duration=punch_duration, width=width, height=height,
    )
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
