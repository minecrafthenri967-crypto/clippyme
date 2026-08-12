"""Hook impact sound: a short "whoosh"-style hit synced to the moment the hook
text becomes visible (idea 3 from the swipe-rate brainstorm — a sudden audio
cue at the exact instant a scroller's thumb is about to move on).

Synthesized entirely with ffmpeg's own audio sources (``aevalsrc``/
``anoisesrc``) rather than a shipped or user-uploaded sound file — no asset,
no licensing question, no upload step. Two layered elements make the hit:
a low thump (a short sine burst) for weight, and a bright click (a filtered
noise burst) for a crisp attack, mixed together then laid over the clip's own
audio with a brief duck so the hit actually cuts through instead of getting
buried.

Split like ``teaser.py``: filter-graph construction is pure/host-tested,
``render_hook_sfx`` is the only impure part. Video is untouched by this pass —
the ffmpeg command uses ``-c:v copy``, so unlike every other compose layer
this one costs no video re-encode generation at all.
"""
import logging
import os
import subprocess

from clippyme.domain.encode import ffmpeg_timeout

logger = logging.getLogger(__name__)

# The thump: a short low sine burst for weight/body.
DEFAULT_THUMP_FREQ = 95.0
DEFAULT_THUMP_DURATION = 0.14
# The click: a short bright noise burst layered on top for a crisp attack —
# a thump alone reads as dull/muffled, the click is what makes it feel sharp.
DEFAULT_CLICK_DURATION = 0.02
DEFAULT_CLICK_HIGHPASS = 1500.0
# Overall level of the synthesized hit relative to the mixed source, in dB.
# Negative because aevalsrc/anoisesrc are generated at full scale — this is
# the actual "how loud is the hit" knob.
DEFAULT_GAIN_DB = -6.0
# The clip's own audio is briefly pulled down so the hit is audible over it
# rather than just adding to it. 0.65 = -3.7dB, subtle enough that it reads
# as "something happened" without the original audio visibly ducking.
DEFAULT_DUCK_LEVEL = 0.65
DEFAULT_DUCK_DURATION = 0.3


def build_hook_sfx_filter(
    offset: float = 0.0,
    *,
    thump_freq: float = DEFAULT_THUMP_FREQ,
    thump_duration: float = DEFAULT_THUMP_DURATION,
    click_duration: float = DEFAULT_CLICK_DURATION,
    click_highpass: float = DEFAULT_CLICK_HIGHPASS,
    gain_db: float = DEFAULT_GAIN_DB,
    duck_level: float = DEFAULT_DUCK_LEVEL,
    duck_duration: float = DEFAULT_DUCK_DURATION,
) -> str:
    """The ``-filter_complex`` graph mixing a synthesized hit into ``[0:a]``.

    ``[1:a]`` and ``[2:a]`` are the thump/click lavfi sources the caller wires
    up as extra ``-i`` inputs (see ``render_hook_sfx``) — this function only
    builds the graph, it does not know how those inputs were generated.

    ``offset`` positions the hit via ``adelay`` (milliseconds, both channels).
    Always 0.0 in practice — the hook always becomes visible at t=0 of
    whatever timeline it is composed onto (a prepended teaser included) — but
    left as a real parameter rather than hardcoded so the graph stays testable
    and reusable if a future caller wants the hit somewhere else.
    """
    offset_ms = max(0, round(offset * 1000))
    duck_end = offset + duck_duration
    return (
        f"[1:a]afade=t=in:st=0:d=0.005,"
        f"afade=t=out:st={max(0.0, thump_duration - 0.12):.3f}:d=0.12:curve=exp[thump];"
        f"[2:a]highpass=f={click_highpass:.0f},"
        f"afade=t=out:st=0:d={click_duration:.3f}[click];"
        f"[thump][click]amix=inputs=2:duration=first:normalize=0[hit];"
        f"[hit]adelay={offset_ms}|{offset_ms},volume={gain_db:.2f}dB[sfx];"
        f"[0:a]volume={duck_level:.3f}:enable='between(t,{offset:.3f},{duck_end:.3f})'[ducked];"
        f"[ducked][sfx]amix=inputs=2:duration=first:normalize=0[outa]"
    )


def render_hook_sfx(video_path: str, output_path: str, *, offset: float = 0.0, **kwargs) -> bool:
    """Mix the synthesized hit into ``video_path``'s audio, video untouched.

    ``video_path`` MUST already have an audio stream — the caller
    (``compose._apply_hook_sfx``) is responsible for checking, since mixing a
    hit onto a silent clip means adding a track that was never there rather
    than layering onto one, a different (and here, out of scope) operation.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")

    thump_duration = kwargs.get("thump_duration", DEFAULT_THUMP_DURATION)
    click_duration = kwargs.get("click_duration", DEFAULT_CLICK_DURATION)
    thump_freq = kwargs.get("thump_freq", DEFAULT_THUMP_FREQ)

    filter_complex = build_hook_sfx_filter(offset, **kwargs)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-f", "lavfi", "-i", f"aevalsrc=exprs='sin(2*PI*{thump_freq:.2f}*t)':d={thump_duration:.3f}",
        "-f", "lavfi", "-i", f"anoisesrc=color=white:d={click_duration:.3f}:a=1",
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[outa]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=ffmpeg_timeout())
        logger.info("✅ Hook impact sound mixed → %s", os.path.basename(output_path))
        return True
    except subprocess.TimeoutExpired:
        logger.error("❌ Hook SFX ffmpeg timed out after %ss", ffmpeg_timeout())
        raise
    except subprocess.CalledProcessError as e:
        logger.error("❌ Hook SFX ffmpeg error: %s", e.stderr.decode() if e.stderr else "unknown")
        raise
