"""Compose-on-download pipeline (Smart Cut → Hook → Subtitles).

Extracted from app.py compose_clip endpoint. This module owns the layer
composition logic; the FastAPI endpoint stays a thin wrapper that handles
validation, path resolution and HTTP error mapping.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess

from clippyme.domain.clip_locks import clip_lock
from clippyme.domain.errors import ValidationError

logger = logging.getLogger(__name__)

from clippyme.domain import job_artifacts
from clippyme.domain.smartcut import analyze_silences, remap_time_through_kept_segments, smart_cut
from clippyme.domain.subtitles import generate_ass_karaoke, generate_srt, burn_subtitles


_SIZE_MAP = {"S": 0.8, "M": 1.0, "L": 1.3}

# Persisted brand logo (uploaded via /api/config/logo). Overridable for tests.
LOGO_PATH = os.environ.get("CLIPPYME_LOGO_PATH") or os.path.join("data", "logo.png")
# Logo size presets → width as a fraction of the video width.
_LOGO_SIZE_MAP = {"S": 0.12, "M": 0.18, "L": 0.26}
# Player-image size presets → width fraction. Wider range than the logo
# watermark — this is a featured "flash" reveal, not a persistent brand mark.
_PLAYER_IMAGE_SIZE_MAP = {"S": 0.35, "M": 0.55, "L": 0.75}


async def _apply_logo(
    current_input: str,
    job_dir: str,
    clip_index: int,
    logo_params: dict,
    intermediate_files: list,
) -> str:
    from clippyme.domain.logo import add_logo_to_video, DEFAULT_POSITION

    logo_output = os.path.join(job_dir, f"composed_logo_{clip_index}.mp4")
    intermediate_files.append(logo_output)
    lp = logo_params or {}
    position = lp.get("position", DEFAULT_POSITION)
    size = lp.get("size")
    scale = lp.get("scale", _LOGO_SIZE_MAP.get(size, 0.18))
    opacity = lp.get("opacity", 1.0)
    margin = lp.get("margin", 0.04)
    await asyncio.to_thread(
        add_logo_to_video,
        current_input,
        LOGO_PATH,
        logo_output,
        position,
        scale,
        opacity,
        margin,
    )
    return logo_output


async def _apply_grade(
    current_input: str,
    job_dir: str,
    clip_index: int,
    grade_params: dict,
    intermediate_files: list,
) -> str:
    """Apply an optional colour grade. Runs FIRST (before subtitles) so overlay
    colours are not shifted by the grade. Silently keeps the input if the
    preset is none/unknown or ffmpeg fails."""
    from clippyme.domain.grade import apply_grade_async, DEFAULT_GRADE

    preset = (grade_params or {}).get("preset", DEFAULT_GRADE)
    grade_output = os.path.join(job_dir, f"composed_grade_{clip_index}.mp4")
    ok = await apply_grade_async(current_input, grade_output, preset)
    if not ok:
        return current_input
    intermediate_files.append(grade_output)
    return grade_output


def _probe_qa(path: str) -> tuple:
    """(duration_seconds | None, has_audio, size_bytes | None) via ffprobe.
    Best-effort: any failure returns (None, True, size) so QA never blocks."""
    size = os.path.getsize(path) if os.path.exists(path) else None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
        )
        info = json.loads(proc.stdout or b"{}")
        dur = info.get("format", {}).get("duration")
        dur = float(dur) if dur is not None else None
        has_audio = any(
            s.get("codec_type") == "audio" for s in info.get("streams", [])
        )
        return dur, has_audio, size
    except Exception:
        return None, True, size


async def _self_eval(
    composed_path: str, clip_info: dict, smartcut_applied: bool, clip_index: int,
) -> None:
    """video-use step 7 / superpowers verification: probe the rendered output and
    log any QA issues. Never raises — a QA miss surfaces a warning, not a 500."""
    from clippyme.domain.clip_qa import evaluate_clip_qa

    try:
        dur, has_audio, size = await asyncio.to_thread(_probe_qa, composed_path)
        try:
            expected = float(clip_info.get("end", 0)) - float(clip_info.get("start", 0))
        except (TypeError, ValueError):
            expected = None
        report = evaluate_clip_qa(
            actual_duration=dur,
            expected_duration=expected if expected and expected > 0 else None,
            has_audio=has_audio,
            size_bytes=size,
            smartcut_applied=smartcut_applied,
        )
        if report["ok"]:
            logger.info("self_eval: clip_index=%d ✓ output looks sane", clip_index)
        else:
            logger.warning(
                "self_eval: clip_index=%d ⚠️ QA issues: %s",
                clip_index, "; ".join(report["issues"]),
            )
    except Exception as e:  # pragma: no cover — QA must never break compose
        logger.debug("self_eval skipped (probe error): %s", e)


async def _apply_smartcut(
    current_input: str,
    base_clip: str,
    metadata: dict,
    clip_info: dict,
    intermediate_files: list,
    drop_ranges=None,
) -> str:
    # Smart Cut caching is delegated entirely to smart_cut() itself, which
    # writes a plan-hashed output (`{base}_smartcut_{hash}.mp4`) and validates
    # cache hits against the source mtime (plus a legacy bare-`_smartcut.mp4`
    # back-compat candidate). The previous fixed-name sidecar shortcut here
    # built `{base}_smartcut.mp4` — a name smart_cut NEVER produces under the
    # current Subtitles→Smart Cut ordering (current_input is `composed_sub_N.mp4`)
    # — so it could only ever match stale artifacts that smart_cut already
    # handles itself. Removed: always delegate to smart_cut's own correct cache.
    transcript = metadata.get("transcript", {})
    sc_output, _ = await asyncio.to_thread(
        smart_cut,
        current_input,
        transcript,
        clip_info.get("start", 0),
        clip_info.get("end", 0),
        transcript.get("language"),
        drop_ranges,
    )
    # Track the smart-cut artefact so _cleanup_intermediates can remove
    # it at the end of compose_layers (unless it ends up as the final
    # composed_clip_*.mp4, in which case the cleanup helper preserves it).
    if sc_output and sc_output != current_input:
        intermediate_files.append(sc_output)
    return sc_output or current_input


async def _apply_hook(
    current_input: str,
    job_dir: str,
    clip_index: int,
    hook_params: dict,
    intermediate_files: list,
    logo_params: dict = None,
    reframe_mode: str = None,
    teaser_offset: float = 0.0,
) -> str:
    """Hook overlay pass. When ``logo_params`` is given the brand logo is
    composited in the SAME encode (hook below, logo topmost — identical
    z-order to the sequential Hook → Logo passes, one generation cheaper).

    The hook is visible for the first 4s of the clip only, EXCEPT when
    ``reframe_mode`` is the literal 'disabled' (letterbox) — full clip then.

    ``teaser_offset`` extends that window by the length of a prepended
    cold-open teaser. Without it the teaser would eat into the 4s and the
    clip's real opening would get whatever was left — the hook has to cover
    the teaser AND still give the clip's own start its full four seconds."""
    from clippyme.domain.hooks import add_hook_to_video

    hook_duration = None if reframe_mode == "disabled" else 4 + max(0.0, teaser_offset)

    hook_output = os.path.join(job_dir, f"composed_hook_{clip_index}.mp4")
    intermediate_files.append(hook_output)
    position = hook_params.get("position", "top")
    font_scale = _SIZE_MAP.get(hook_params.get("size", "M"), 1.0)
    offset_y = hook_params.get("offset_y", 0)
    # Instagram-Stories-style text customisation. Only forward keys the user
    # actually set so create_hook_image's defaults fill the rest.
    _style_keys = ("text_color", "bg_enabled", "bg_color", "bg_opacity",
                   "corner_radius", "outline_color", "outline_width", "font", "shadow",
                   "animate")
    style = {k: hook_params[k] for k in _style_keys if k in hook_params}
    logo = None
    if logo_params is not None:
        lp = logo_params or {}
        logo = {
            "path": LOGO_PATH,
            "position": lp.get("position"),
            "scale": lp.get("scale", _LOGO_SIZE_MAP.get(lp.get("size"), 0.18)),
            "opacity": lp.get("opacity", 1.0),
            "margin": lp.get("margin", 0.04),
        }
        from clippyme.domain.logo import DEFAULT_POSITION
        logo["position"] = logo["position"] or DEFAULT_POSITION
    await asyncio.to_thread(
        add_hook_to_video,
        current_input,
        hook_params["text"],
        hook_output,
        position,
        font_scale,
        offset_y,
        style or None,
        logo,
        hook_duration,
    )
    return hook_output


async def _apply_banner(
    current_input: str,
    job_dir: str,
    clip_index: int,
    banner_params: dict,
    clip_info: dict,
    intermediate_files: list,
) -> str:
    """Attribution-banner pass — the TOPMOST compose layer (after Logo).

    Burns the platform-logo + channel-handle pill onto the clip. ``mode`` is
    'attach' by default for a ``reframe_mode='disabled'`` (letterbox) clip so
    the pill hugs the video band's bottom edge; otherwise it rides the safe-zone
    ``y_pct``. Silently keeps the input when the params don't resolve to a real
    banner (defensive — the caller already gated on ``enabled``)."""
    from clippyme.domain.banner import add_banner_to_video, banner_text

    bp = dict(banner_params or {})
    if not banner_text(bp.get("platform"), bp.get("handle")):
        return current_input
    if not bp.get("mode") and (clip_info or {}).get("reframe_mode") == "disabled":
        bp["mode"] = "attach"

    banner_output = os.path.join(job_dir, f"composed_banner_{clip_index}.mp4")
    intermediate_files.append(banner_output)
    await asyncio.to_thread(add_banner_to_video, current_input, bp, banner_output)
    return banner_output


async def _apply_teaser(
    current_input: str,
    job_dir: str,
    clip_index: int,
    teaser_params: dict,
    clip_info: dict,
    metadata: dict,
    drop_ranges,
    smartcut_rendered: bool,
    intermediate_files: list,
) -> tuple:
    """Prepend the clip's peak moment as a cold-open teaser.

    Returns ``(path, offset_seconds)``. The offset is how much LATER every
    subsequent timestamp on this clip now falls, and it is load-bearing —
    the hook's visible window and the player-image overlay both shift by it.
    Returning it explicitly (rather than having the caller re-derive the
    window) keeps a single source of truth for how long the teaser actually
    turned out to be.

    ``(current_input, 0.0)`` means "no teaser" and is a completely normal
    outcome: no peak, the peak was cut away by Smart Cut, or the render
    failed. Never raises — same defensive posture as ``_apply_banner``.
    """
    from clippyme.domain.teaser import (
        DEFAULT_PUNCH_DURATION,
        DEFAULT_PUNCH_ZOOM,
        DEFAULT_TEASER_FADE,
        DEFAULT_TEASER_MAX_DURATION,
        DEFAULT_TEASER_TRANSITION,
        prepend_teaser,
        resolve_teaser_window,
    )

    try:
        params = teaser_params or {}
        duration, has_audio, _ = await asyncio.to_thread(_probe_qa, current_input)
        if not duration:
            logger.info("teaser: could not probe duration (clip_index=%d) — skipping",
                        clip_index)
            return current_input, 0.0

        # Smart Cut shortened the clip, so the peak's clip-relative time means
        # nothing until it is remapped onto the rendered timeline. Only pass
        # the kept spans when a render ACTUALLY happened (the toggle alone is
        # not enough — smart_cut can decline to render).
        kept_segments = None
        if smartcut_rendered:
            transcript = metadata.get("transcript") or {}
            kept_segments, _ = analyze_silences(
                transcript, clip_info.get("start", 0), clip_info.get("end", 0),
                transcript.get("language"), drop_ranges,
            )

        window = resolve_teaser_window(
            clip_info, video_duration=duration, kept_segments=kept_segments,
            max_duration=params.get("max_duration", DEFAULT_TEASER_MAX_DURATION),
        )
        if window is None:
            logger.info(
                "teaser: no usable peak window for clip_index=%d — skipping "
                "(no peak, cut away by Smart Cut, or too close to the clip start)",
                clip_index,
            )
            return current_input, 0.0

        start, end = window
        teaser_output = os.path.join(job_dir, f"composed_teaser_{clip_index}.mp4")
        intermediate_files.append(teaser_output)
        await asyncio.to_thread(
            prepend_teaser, current_input, teaser_output,
            start=start, end=end,
            fade=params.get("fade", DEFAULT_TEASER_FADE),
            has_audio=has_audio,
            transition=params.get("transition", DEFAULT_TEASER_TRANSITION),
            punch=params.get("punch", DEFAULT_PUNCH_ZOOM),
            punch_duration=params.get("punch_duration", DEFAULT_PUNCH_DURATION),
        )
        return teaser_output, end - start
    except Exception:
        logger.warning("teaser: failed for clip_index=%d — skipping the layer",
                       clip_index, exc_info=True)
        return current_input, 0.0


async def _apply_player_image(
    current_input: str,
    job_dir: str,
    clip_index: int,
    player_image_params: dict,
    clip_info: dict,
    metadata: dict,
    metadata_path: str,
    drop_ranges,
    smartcut_rendered: bool,
    intermediate_files: list,
    teaser_offset: float = 0.0,
) -> str:
    """Detect (once, cached) → match (every call, cheap) → remap through
    Smart Cut if it actually rendered → shift past any cold-open teaser →
    burn a timed photo overlay.

    Never raises — any failure at any stage falls back to returning
    ``current_input`` unchanged (skip the layer), same defensive posture as
    ``_apply_banner``. The one exception carved out of that blanket safety
    net is the detection call itself: a transient failure there must NOT be
    cached as "no mentions", or a flaky network blip would permanently hide
    the overlay for that clip — see the nested try/except below.
    """
    from clippyme.domain.player_detect import detect_player_mentions
    from clippyme.domain.player_image import (
        DEFAULT_PLAYER_IMAGE_DURATION,
        DEFAULT_PLAYER_IMAGE_POSITION,
        PLAYER_IMAGES_DIR,
        add_player_image_to_video,
        list_player_images,
        match_player_image,
    )
    from clippyme.storage.config_store import load_persistent_config

    try:
        transcript = metadata.get("transcript") or {}
        clip_start = clip_info.get("start", 0)
        clip_end = clip_info.get("end", 0)

        # Detect once per clip, ever — cached into clip_info["player_mentions"]
        # so re-compose/re-preview/re-publish never re-bills Gemini.
        if "player_mentions" not in clip_info:
            cfg = load_persistent_config() or {}
            api_key = os.environ.get("GEMINI_API_KEY") or cfg.get("GEMINI_API_KEY")
            model = cfg.get("GEMINI_MODEL") or "gemini-3.5-flash"
            try:
                mentions = await asyncio.to_thread(
                    detect_player_mentions, api_key=api_key, model=model,
                    transcript=transcript, clip_start=clip_start, clip_end=clip_end,
                )
            except Exception:
                logger.warning(
                    "player_image: detection failed for clip_index=%d — NOT caching, "
                    "will retry on next compose", clip_index, exc_info=True,
                )
                return current_input
            if metadata_path:
                await asyncio.to_thread(
                    job_artifacts.set_clip_field, metadata_path, clip_index,
                    "player_mentions", mentions,
                )
            clip_info["player_mentions"] = mentions

        mentions = clip_info.get("player_mentions") or []
        if not mentions:
            return current_input

        library = list_player_images()
        if not library:
            return current_input

        # Re-matched every call (cheap, no Gemini) — uploading a photo AFTER
        # detection still lights up the overlay on the next re-compose.
        match = None
        for mention in sorted(mentions, key=lambda m: -m.get("confidence", 0)):
            found = match_player_image(mention.get("player_name", ""), library)
            if found:
                match = (found, mention["timestamp"])
                break
        if not match:
            logger.info(
                "player_image: no library match for any detected mention (clip_index=%d)",
                clip_index,
            )
            return current_input
        matched_name, raw_timestamp = match

        if smartcut_rendered:
            language = transcript.get("language")
            merged, _ = analyze_silences(transcript, clip_start, clip_end, language, drop_ranges)
            mapped_t = remap_time_through_kept_segments(raw_timestamp, merged)
            if mapped_t is None:
                logger.info(
                    "player_image: detected moment was cut away by Smart Cut — "
                    "skipping (clip_index=%d)", clip_index,
                )
                return current_input
        else:
            mapped_t = raw_timestamp

        # A prepended teaser pushed the whole clip later by exactly its own
        # length; without this the photo would flash during the teaser instead
        # of on the name that triggered it.
        mapped_t += max(0.0, teaser_offset)

        dur, _, _ = await asyncio.to_thread(_probe_qa, current_input)
        video_duration = dur if dur else max(0.0, clip_end - clip_start)
        start_t = max(0.0, min(mapped_t, max(0.0, video_duration - 0.1)))

        pip = player_image_params or {}
        image_path = os.path.join(PLAYER_IMAGES_DIR, f"{matched_name}.png")
        pi_output = os.path.join(job_dir, f"composed_player_image_{clip_index}.mp4")
        intermediate_files.append(pi_output)
        scale = pip.get("scale", _PLAYER_IMAGE_SIZE_MAP.get(pip.get("size"), 0.55))
        await asyncio.to_thread(
            add_player_image_to_video, current_input, image_path, pi_output,
            start=start_t, duration=pip.get("duration", DEFAULT_PLAYER_IMAGE_DURATION),
            position=pip.get("position", DEFAULT_PLAYER_IMAGE_POSITION),
            scale=scale, opacity=pip.get("opacity", 1.0),
            margin=pip.get("margin", 0.04),
        )
        return pi_output
    except Exception:
        logger.warning(
            "player_image: layer failed for clip_index=%d — skipping (never fails compose)",
            clip_index, exc_info=True,
        )
        return current_input


async def _apply_subtitles(
    current_input: str,
    job_dir: str,
    clip_index: int,
    metadata: dict,
    clip_info: dict,
    subtitle_params: dict,
    intermediate_files: list,
    pre_vf: str = None,
) -> str:
    sub_output = os.path.join(job_dir, f"composed_sub_{clip_index}.mp4")
    intermediate_files.append(sub_output)
    transcript = metadata.get("transcript", {})
    clip_start = clip_info.get("start", 0)
    clip_end = clip_info.get("end", 0)
    sub_mode = subtitle_params.get("mode", "karaoke")
    sub_offset_y = subtitle_params.get("offset_y", 0)

    if sub_mode == "karaoke":
        ass_path = os.path.join(job_dir, f"composed_subs_{clip_index}.ass")
        intermediate_files.append(ass_path)
        success = await asyncio.to_thread(
            lambda: generate_ass_karaoke(
                transcript,
                clip_start,
                clip_end,
                ass_path,
                preset=subtitle_params.get("preset", "classic_white"),
                mode=subtitle_params.get("display_mode", "word_group"),
                words_per_group=subtitle_params.get("words_per_group", 3),
                # Default None → honour the preset's own casing (mrbeast_box /
                # minimal_clean are lower-case presets). Only an explicit
                # frontend value overrides it.
                uppercase=subtitle_params.get("uppercase"),
                font_color=subtitle_params.get("font_color"),
                highlight_color=subtitle_params.get("highlight_color"),
                outline_width=subtitle_params.get("outline_width"),
                font_name=subtitle_params.get("font"),
                font_size=subtitle_params.get("font_size"),
                position=subtitle_params.get("position", "bottom"),
                offset_y=sub_offset_y,
                outline_color=subtitle_params.get("outline_color"),
                align=subtitle_params.get("align", "center"),
            ),
        )
        if not success:
            raise ValidationError("No words found for this clip range.")
        await asyncio.to_thread(
            lambda: burn_subtitles(
                current_input,
                ass_path,
                sub_output,
                2,
                16,
                "Verdana",
                "#FFFFFF",
                "#000000",
                2,
                "#000000",
                0.0,
                sub_offset_y,
                pre_vf=pre_vf,
            ),
        )
    else:
        srt_path = os.path.join(job_dir, f"composed_subs_{clip_index}.srt")
        intermediate_files.append(srt_path)
        success = await asyncio.to_thread(
            generate_srt, transcript, clip_start, clip_end, srt_path
        )
        if not success:
            raise ValidationError("No words found for this clip range.")
        await asyncio.to_thread(
            lambda: burn_subtitles(
                current_input,
                srt_path,
                sub_output,
                alignment=subtitle_params.get("position", "bottom"),
                fontsize=subtitle_params.get("font_size", 16),
                font_name=subtitle_params.get("font", "Verdana"),
                font_color=subtitle_params.get("font_color", "#FFFFFF"),
                border_color=subtitle_params.get("border_color", "#000000"),
                border_width=subtitle_params.get("border_width", 2),
                bg_color=subtitle_params.get("bg_color", "#000000"),
                bg_opacity=subtitle_params.get("bg_opacity", 0.0),
                offset_y=sub_offset_y,
                h_align=subtitle_params.get("align", "center"),
                pre_vf=pre_vf,
            ),
        )
    return sub_output


def _cleanup_intermediates(files: list, keep_path: str) -> None:
    """Best-effort removal of intermediate files.

    Never raises — cleanup failures must NOT mask the original composition
    error. ``keep_path`` is the final artifact path; any intermediate
    matching it is preserved.
    """
    keep_abs = os.path.abspath(keep_path) if keep_path else None
    for temp_file in files:
        if not temp_file:
            continue
        if not os.path.exists(temp_file):
            continue
        if keep_abs and os.path.abspath(temp_file) == keep_abs:
            continue
        try:
            os.remove(temp_file)
        except OSError:
            pass


async def compose_layers(
    *,
    base_clip: str,
    job_dir: str,
    clip_index: int,
    metadata: dict,
    clip_info: dict,
    toggles: dict,
    hook_params: dict,
    subtitle_params: dict,
    logo_params: dict = None,
    grade_params: dict = None,
    banner_params: dict = None,
    player_image_params: dict = None,
    teaser_params: dict = None,
    drop_ranges=None,
    metadata_path: str = None,
) -> str:
    """Run the active layer pipeline. Returns the final composed filename (basename).

    Cleans up intermediate files on BOTH success and failure. If any layer
    raises (ffmpeg crash, bad params, HTTPException) we still remove every
    partial file we created before re-raising the original error.

    Serialised per (job_dir, clip_index): every intermediate filename is
    deterministic by clip index, so two overlapping composes for the same clip
    (Download racing Publish's compose_first) would delete/overwrite each
    other's in-flight files. Different clips compose in parallel as before.

    ``metadata_path`` (the resolved clip's own metadata file, i.e.
    ``resolved.metadata_path``) is only required when the ``player_image``
    layer is active — it's how a first-ever detection result gets cached
    onto the clip's metadata entry so a later compose never re-bills Gemini.
    """
    async with clip_lock(job_dir, clip_index):
        return await _compose_layers_impl(
            base_clip=base_clip, job_dir=job_dir, clip_index=clip_index,
            metadata=metadata, clip_info=clip_info, toggles=toggles,
            hook_params=hook_params, subtitle_params=subtitle_params,
            logo_params=logo_params, grade_params=grade_params,
            banner_params=banner_params, player_image_params=player_image_params,
            teaser_params=teaser_params,
            drop_ranges=drop_ranges, metadata_path=metadata_path,
        )


async def _compose_layers_impl(
    *,
    base_clip: str,
    job_dir: str,
    clip_index: int,
    metadata: dict,
    clip_info: dict,
    toggles: dict,
    hook_params: dict,
    subtitle_params: dict,
    logo_params: dict = None,
    grade_params: dict = None,
    banner_params: dict = None,
    player_image_params: dict = None,
    teaser_params: dict = None,
    drop_ranges=None,
    metadata_path: str = None,
) -> str:
    active = {k: v for k, v in toggles.items() if v}
    # The banner can be enabled via its own params.enabled (frontend convention)
    # without a toggles entry — fold it in so the no-active short-circuit and the
    # downstream active.get('banner') check both see it.
    if (banner_params or {}).get("enabled"):
        active["banner"] = True
    logger.info(
        "compose_layers: clip_index=%d active=%s hook_text_len=%d subtitle_mode=%s",
        clip_index, list(active.keys()),
        len((hook_params or {}).get("text", "") or ""),
        (subtitle_params or {}).get("mode", "karaoke"),
    )
    if not active:
        logger.info("compose_layers: no active toggles → returning base clip unmodified")
        return os.path.basename(base_clip)

    current_input = base_clip
    intermediate_files: list = []
    from clippyme.domain.clip_resolve import composed_clip_basename
    composed_filename = composed_clip_basename(clip_info, clip_index)
    composed_path = os.path.join(job_dir, composed_filename)
    # Always wipe a stale composed file from a previous compose pass so
    # we never accidentally upload yesterday's version when the user has
    # changed toggles in the meantime.
    if os.path.exists(composed_path):
        try:
            os.remove(composed_path)
        except OSError:
            pass

    layers_applied: list[str] = []

    try:
        # --- Ordering matters ---
        # Earlier revisions ran Smart Cut FIRST, then burned subtitles on
        # the resulting shorter clip. The subtitle timestamps are derived
        # from the original transcript using absolute ``clip_start`` and
        # ``clip_end`` seconds, so the subs expected a clip of length
        # (clip_end - clip_start). After Smart Cut removed silences and
        # filler words, the clip was strictly shorter than that, so the
        # subs accumulated drift relative to the audio — very visible on
        # fast speakers, where Smart Cut removes many micro-gaps and the
        # error snowballs over the clip.
        #
        # Correct order: GRADE → SUBTITLES → SMART CUT → HOOK → LOGO.
        #
        # Grade runs FIRST so the colour transform applies only to the source
        # frames — burning it before subtitles/hook/logo means those overlays
        # keep their exact authored colours instead of being tinted too.
        #
        # Step 1 burns the subs into the raw frames with perfect timing
        # (since the base clip still has the original length). Step 2
        # re-encodes to remove silence segments; because the subs are
        # already pixels at that point, they travel with the frames and
        # stay locked to the audio, no drift regardless of speech speed.
        # Step 3 overlays the static Hook on top of everything so it
        # remains visible for every surviving frame.
        # Grade + Subtitles fusion: when BOTH are active, the grade chain rides
        # as pre_vf on the subtitle burn — inside one filtergraph the colour
        # transform still applies to the source pixels BEFORE the glyphs are
        # composited, so the Grade→Subtitles semantics are identical, one
        # encode generation cheaper. Grade-only keeps its own pass.
        merged_grade_vf = None
        if active.get("grade") and active.get("subtitles"):
            from clippyme.domain.grade import DEFAULT_GRADE, build_grade_filter
            merged_grade_vf = build_grade_filter(
                (grade_params or {}).get("preset", DEFAULT_GRADE)) or None

        if active.get("grade") and not merged_grade_vf:
            current_input = await _apply_grade(
                current_input, job_dir, clip_index, grade_params, intermediate_files,
            )
            layers_applied.append("grade")
            logger.info("compose_layers: ✓ grade → %s", os.path.basename(current_input))

        if active.get("subtitles"):
            current_input = await _apply_subtitles(
                current_input,
                job_dir,
                clip_index,
                metadata,
                clip_info,
                subtitle_params,
                intermediate_files,
                pre_vf=merged_grade_vf,
            )
            if merged_grade_vf:
                layers_applied.append("grade")
            layers_applied.append("subtitles")
            logger.info(
                "compose_layers: ✓ %ssubtitles → %s",
                "grade+" if merged_grade_vf else "", os.path.basename(current_input),
            )

        smartcut_rendered = False
        if active.get("smartcut"):
            _pre_smartcut_input = current_input
            current_input = await _apply_smartcut(
                current_input, base_clip, metadata, clip_info, intermediate_files,
                drop_ranges,
            )
            # smart_cut() can internally decide NOT to render (too little
            # time_saved, <2 segments) and return the input unchanged even
            # though the toggle is on — the player-image layer's Smart-Cut
            # remap must know whether a render actually happened, not just
            # whether the toggle was set.
            smartcut_rendered = (current_input != _pre_smartcut_input)
            layers_applied.append("smartcut")
            logger.info("compose_layers: ✓ smartcut → %s", os.path.basename(current_input))

        # Cold-open teaser, AFTER Smart Cut and BEFORE the hook.
        #
        # After Smart Cut, because Smart Cut is transcript-driven on
        # clip-relative times — prepending footage first would desync it, and
        # it would also chew on the duplicated teaser footage.
        #
        # Before the hook, because the teaser is the first thing anyone sees:
        # the hook text belongs ON it. That is the whole point of the format
        # (peak footage + hook copy together in the first two seconds).
        #
        # This is the only layer that changes the clip's DURATION, so it
        # returns the offset every later timed layer has to shift by.
        teaser_offset = 0.0
        if active.get("teaser"):
            current_input, teaser_offset = await _apply_teaser(
                current_input, job_dir, clip_index, teaser_params, clip_info, metadata,
                drop_ranges, smartcut_rendered, intermediate_files,
            )
            if teaser_offset:
                layers_applied.append("teaser")
                logger.info("compose_layers: ✓ teaser (+%.2fs) → %s",
                            teaser_offset, os.path.basename(current_input))

        # Hook last: it's a static overlay that should appear on every
        # kept frame, regardless of how many silences Smart Cut removed.
        hook_text = (hook_params or {}).get("text", "")
        if isinstance(hook_text, str):
            hook_text = hook_text.strip()
        hook_active = bool(active.get("hook"))
        if hook_active and not hook_text:
            logger.warning(
                "compose_layers: hook toggle ON but text is empty — "
                "skipping hook layer. Ensure PublishModal / ResultCard "
                "sends a non-empty hook_params.text.",
            )
            hook_active = False
        logo_active = bool(active.get("logo"))
        if logo_active and not os.path.exists(LOGO_PATH):
            logger.warning(
                "compose_layers: logo toggle ON but no logo uploaded at %s "
                "— skipping logo layer.", LOGO_PATH,
            )
            logo_active = False

        if hook_active and logo_active:
            # Hook + Logo fusion: both are static overlays applied after Smart
            # Cut, so they composite in ONE encode (hook below, logo topmost —
            # the exact z-order of the sequential passes), one generation
            # cheaper on a fully-toggled clip.
            hp_clean = {**hook_params, "text": hook_text}
            current_input = await _apply_hook(
                current_input, job_dir, clip_index, hp_clean, intermediate_files,
                logo_params=logo_params or {},
                reframe_mode=(clip_info or {}).get("reframe_mode"),
                teaser_offset=teaser_offset,
            )
            layers_applied += ["hook", "logo"]
            logger.info("compose_layers: ✓ hook+logo → %s", os.path.basename(current_input))
        elif hook_active:
            hp_clean = {**hook_params, "text": hook_text}
            current_input = await _apply_hook(
                current_input, job_dir, clip_index, hp_clean, intermediate_files,
                reframe_mode=(clip_info or {}).get("reframe_mode"),
                teaser_offset=teaser_offset,
            )
            layers_applied.append("hook")
            logger.info("compose_layers: ✓ hook → %s", os.path.basename(current_input))
        elif logo_active:
            # Logo absolutely last: a static brand mark that must sit on top of
            # subtitles AND hook, on every kept frame.
            current_input = await _apply_logo(
                current_input, job_dir, clip_index, logo_params, intermediate_files
            )
            layers_applied.append("logo")
            logger.info("compose_layers: ✓ logo → %s", os.path.basename(current_input))

        # Player-image: campaign content (the athlete photo flash) sits above
        # the brand hook/logo but strictly below the attribution banner, which
        # must stay the topmost layer regardless of what else is active.
        player_image_active = bool(active.get("player_image"))
        if player_image_active:
            current_input = await _apply_player_image(
                current_input, job_dir, clip_index, player_image_params,
                clip_info, metadata, metadata_path, drop_ranges, smartcut_rendered,
                intermediate_files, teaser_offset,
            )
            layers_applied.append("player_image")
            logger.info("compose_layers: ✓ player_image → %s", os.path.basename(current_input))

        # Banner absolutely last (topmost) — the attribution pill sits on top of
        # everything, including the logo. Enable via toggles['banner'] or an
        # explicit banner_params.enabled (frontend sends the latter). Separate
        # pass on purpose: fusing a third overlay into the hook+logo filtergraph
        # is not trivial, and correctness > one saved encode generation.
        from clippyme.domain.banner import banner_text
        bp = banner_params or {}
        banner_active = bool(active.get("banner") or bp.get("enabled"))
        if banner_active and not banner_text(bp.get("platform"), bp.get("handle")):
            logger.warning(
                "compose_layers: banner enabled but platform/handle didn't "
                "resolve to a valid banner — skipping banner layer.")
            banner_active = False
        if banner_active:
            current_input = await _apply_banner(
                current_input, job_dir, clip_index, bp, clip_info, intermediate_files,
            )
            layers_applied.append("banner")
            logger.info("compose_layers: ✓ banner → %s", os.path.basename(current_input))

        if os.path.abspath(current_input) != os.path.abspath(composed_path):
            shutil.copy2(current_input, composed_path)

        logger.info(
            "compose_layers: ✅ final = %s (applied=%s)",
            os.path.basename(composed_path), layers_applied or ["<none>"],
        )
        # video-use step 7 / superpowers verification — probe the rendered
        # output and log any QA issues before handing it back. Soft check.
        await _self_eval(
            composed_path, clip_info, "smartcut" in layers_applied, clip_index,
        )
        _cleanup_intermediates(intermediate_files, composed_path)
        return composed_filename
    except Exception:
        # Failure path: remove any partial composed output AND every
        # intermediate we created. Then re-raise the original exception
        # so the endpoint layer can map it to an HTTP error.
        _cleanup_intermediates(intermediate_files, "")
        if os.path.exists(composed_path):
            try:
                os.remove(composed_path)
            except OSError:
                pass
        raise
