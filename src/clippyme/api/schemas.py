"""Pydantic request schemas for the ClippyMe FastAPI app."""
from __future__ import annotations

import ipaddress
import math
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from clippyme.domain.job_results import ALLOWED_LANGUAGES, GEMINI_MODEL_RE, MAX_INSTRUCTIONS_LEN
from clippyme.netutil import resolve_host_addresses
from clippyme.schemas import ViralClip, ViralClipsResponse  # noqa: F401


class GamingFacecamBox(BaseModel):
    """A facecam region drawn by the user over their own screenshot of the
    stream, as 0..1 fractions of the screenshot's width/height — see the
    dashboard's facecam box picker. Only meaningful with reframe_mode ==
    'gaming'; ``None`` (the field's default everywhere it's used) means "run
    the usual auto-detection scan" instead."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _box_stays_inside_the_frame(self):
        eps = 1e-6
        if self.x + self.w > 1.0 + eps:
            raise ValueError("x + w must not exceed 1.0")
        if self.y + self.h > 1.0 + eps:
            raise ValueError("y + h must not exceed 1.0")
        return self


class GamingGameplayBox(GamingFacecamBox):
    """The region of the source frame that IS the game, drawn the same way as
    the facecam box. ``None`` keeps the legacy crop — horizontally centred over
    the full frame height — which silently includes a taskbar, chat panel or
    webcam strip whenever the streamer's layout has one baked in. Unlike the
    facecam there is no detector fallback: "where is the game" has no visual
    signature to detect, so it is drawn or defaulted, never guessed."""


#: Bounds for the facecam/gameplay split, mirroring
#: ``reframe_ops.gaming_facecam_fraction``'s own clamp so an out-of-range value
#: is a 400 here instead of being silently clamped inside the renderer.
GAMING_SPLIT_MIN = 0.15
GAMING_SPLIT_MAX = 0.75


def _reject_internal_host(host: str) -> None:
    """Reject literal or DNS-resolved non-public hosts without unbounded DNS I/O.

    DNS failures and timeouts remain best-effort at this early API boundary; the
    downloader performs the authoritative rebinding-aware check immediately
    before the network request.
    """
    if not host:
        raise ValueError("url has no host")
    try:
        candidates = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            candidates = set(resolve_host_addresses(host, timeout=5.0))
        except (OSError, TimeoutError, UnicodeError):
            return
    for ip in candidates:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("url points to a non-public address")


def validate_public_url(value: str) -> str:
    """Enforce an HTTP(S) URL with a non-internal host."""
    raw = (value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("url must use http or https")
    _reject_internal_host((parsed.hostname or "").lower())
    return raw


def _validate_language(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized not in ALLOWED_LANGUAGES:
        raise ValueError(f"unsupported language: {value!r}")
    return normalized


def _validate_timezone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("timezone must not be blank")
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {normalized!r}") from exc
    except ImportError:  # pragma: no cover - Python 3.11 always has zoneinfo
        pass
    return normalized


class ProcessRequest(BaseModel):
    url: str = Field(..., max_length=2048)
    instructions: Optional[str] = Field(None, max_length=MAX_INSTRUCTIONS_LEN)
    reframe_mode: Optional[str] = Field(None, pattern=r"^(auto|disabled|subject|object|gaming)$")
    aspect: Optional[str] = Field(None, pattern=r"^(9:16|1:1|16:9)$")
    language: Optional[str] = Field(None, max_length=16)
    no_zoom: Optional[bool] = False
    skip_analysis: Optional[bool] = False
    model: Optional[str] = Field(
        None, max_length=72, pattern=r"^gemini-[A-Za-z0-9.\-]{1,64}$"
    )
    # Only used with reframe_mode == 'gaming'. None (default) runs the usual
    # detection scan; a drawn box pins the facecam there directly instead —
    # facecam placement varies per streamer/game and the detector can miss or
    # mis-locate it, so a user who knows their own layout can draw it rather
    # than relying on a guess.
    gaming_facecam_box: Optional[GamingFacecamBox] = None
    gaming_gameplay_box: Optional[GamingGameplayBox] = None
    gaming_split: Optional[float] = Field(
        default=None, ge=GAMING_SPLIT_MIN, le=GAMING_SPLIT_MAX)
    # Which named Zernio account this job's clips are earmarked for (see
    # storage.config_store's profile namespace) — persisted alongside the job
    # (job_artifacts.save_job_campaign) so an external approval bot running
    # for ONE campaign (its own ZERNIO_PROFILE) can filter GET /api/history to
    # only its own jobs instead of every bot posting every clip.
    zernio_profile: str = Field("default", pattern=r"^[a-z0-9_-]{1,32}$")
    # The Create-tab layer recipe this job was submitted with, remembered so
    # every later consumer (the dashboard's auto-compose, a publish, an
    # external approval bot) burns the layers the user actually configured
    # instead of inventing its own. See validate_compose_recipe.
    compose: Optional[dict] = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if value == "https://upload.invalid/local":
            return value
        return validate_public_url(value)

    @field_validator("language")
    @classmethod
    def _bound_language(cls, value: Optional[str]) -> Optional[str]:
        return _validate_language(value)

    @field_validator("compose")
    @classmethod
    def _bound_compose_recipe(cls, value):
        return validate_compose_recipe(value)


class BatchRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=20)
    instructions: Optional[str] = Field(None, max_length=MAX_INSTRUCTIONS_LEN)
    reframe_mode: Optional[str] = Field(None, pattern=r"^(auto|disabled|subject|object|gaming)$")
    aspect: Optional[str] = Field(None, pattern=r"^(9:16|1:1|16:9)$")
    language: Optional[str] = Field(None, max_length=16)
    no_zoom: Optional[bool] = False
    skip_analysis: Optional[bool] = False
    model: Optional[str] = Field(
        None, max_length=72, pattern=r"^gemini-[A-Za-z0-9.\-]{1,64}$"
    )
    gaming_facecam_box: Optional[GamingFacecamBox] = None
    gaming_gameplay_box: Optional[GamingGameplayBox] = None
    gaming_split: Optional[float] = Field(
        default=None, ge=GAMING_SPLIT_MIN, le=GAMING_SPLIT_MAX)
    zernio_profile: str = Field("default", pattern=r"^[a-z0-9_-]{1,32}$")
    compose: Optional[dict] = None

    @field_validator("urls")
    @classmethod
    def _validate_urls(cls, values: List[str]) -> List[str]:
        cleaned = [validate_public_url(url) for url in values if (url or "").strip()]
        if not cleaned:
            raise ValueError("at least one non-blank URL is required")
        return cleaned

    @field_validator("language")
    @classmethod
    def _bound_language(cls, value: Optional[str]) -> Optional[str]:
        return _validate_language(value)

    @field_validator("compose")
    @classmethod
    def _bound_compose_recipe(cls, value):
        return validate_compose_recipe(value)


_ALLOWED_CONFIG_KEYS = frozenset({
    "GEMINI_API_KEY", "GEMINI_MODEL", "YOUTUBE_COOKIES", "HF_TOKEN",
    "HUGGINGFACE_TOKEN", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY",
    "TRANSCRIPTION_PROVIDER", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
    "CLIPPYME_MAX_DOWNLOAD_HEIGHT",
})


class ConfigUpdateRequest(BaseModel):
    keys: Dict[str, str]

    @field_validator("keys")
    @classmethod
    def _validate_keys(cls, values: Dict[str, str]) -> Dict[str, str]:
        unknown = set(values) - _ALLOWED_CONFIG_KEYS
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        for name, value in values.items():
            if len(value) > 4096:
                raise ValueError(f"config value for {name!r} too long (max 4096)")
        provider = values.get("TRANSCRIPTION_PROVIDER")
        if provider not in (None, "", "deepgram", "elevenlabs", "whisper"):
            raise ValueError("TRANSCRIPTION_PROVIDER must be deepgram, elevenlabs or whisper")
        model = values.get("GEMINI_MODEL")
        if model and not GEMINI_MODEL_RE.fullmatch(model):
            raise ValueError("GEMINI_MODEL is not a valid Gemini model id")
        return values


class ReframeRequest(BaseModel):
    reframe_mode: Optional[str] = Field(None, pattern=r"^(auto|disabled|subject|object|gaming)$")
    gaming_facecam_box: Optional[GamingFacecamBox] = None
    gaming_gameplay_box: Optional[GamingGameplayBox] = None
    gaming_split: Optional[float] = Field(
        default=None, ge=GAMING_SPLIT_MIN, le=GAMING_SPLIT_MAX)


_OVERLAY_MAX_KEYS = 40
_OVERLAY_MAX_STR = 1000
_OVERLAY_MAX_ABS_NUM = 100_000


def _validate_overlay_scalar(key, item):
    """One scalar overlay-param value. Raises on anything out of bounds."""
    if isinstance(item, str):
        if len(item) > _OVERLAY_MAX_STR:
            raise ValueError(f"value for {key!r} too long (max {_OVERLAY_MAX_STR})")
    elif isinstance(item, bool) or item is None:
        return
    elif isinstance(item, (int, float)):
        if not math.isfinite(item) or abs(item) > _OVERLAY_MAX_ABS_NUM:
            raise ValueError(f"value for {key!r} out of range")
    else:
        raise ValueError(f"value for {key!r} must be a scalar")


def _validate_point_value(key, item):
    """An {"x": float, "y": float} 0..1 placement pair — nothing else.

    The ONLY nested shape any overlay-param block is allowed to carry. The
    free-drag logo editor stores `logo_params["position"]` like this (see
    logo.parse_logo_position_xy), so a blanket scalars-only rule 400s every
    submit/compose that has the logo enabled at a dragged position — the logo
    feature becomes unusable, not merely unpersisted. Kept deliberately narrow
    (exactly two numeric keys, range-checked) rather than "any flat object",
    so it cannot become a hole for the nested junk the scalars-only rule
    exists to keep out of tiktok_settings / platformSpecificData.
    """
    if set(item) != {"x", "y"}:
        raise ValueError(f"value for {key!r} must be an {{x, y}} pair")
    for axis in ("x", "y"):
        axis_value = item[axis]
        if isinstance(axis_value, bool) or not isinstance(axis_value, (int, float)):
            raise ValueError(f"value for {key!r}.{axis} must be a number")
        if not math.isfinite(axis_value) or not (0.0 <= axis_value <= 1.0):
            raise ValueError(f"value for {key!r}.{axis} must be a 0..1 fraction")


def _validate_overlay_params(value, point_keys=frozenset()):
    """Bound one overlay-param block: a flat object of scalars.

    ``point_keys`` names the keys (if any) that may instead hold an {x, y}
    placement pair. It is opt-in per call site — the blocks that never carry
    a point (tiktok_settings, platformSpecificData, …) keep the strict
    scalars-only rule they were given deliberately.
    """
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("must be an object")
    if len(value) > _OVERLAY_MAX_KEYS:
        raise ValueError(f"too many keys (max {_OVERLAY_MAX_KEYS})")
    for key, item in value.items():
        if isinstance(item, dict) and key in point_keys:
            _validate_point_value(key, item)
            continue
        _validate_overlay_scalar(key, item)
    return value


# Overlay-param keys that legitimately hold an {x, y} pair rather than a scalar.
_OVERLAY_POINT_KEYS = frozenset({"position"})


_DROP_MAX_RANGES = 500
_DROP_MAX_SECONDS = 100_000


def _validate_drop_ranges(value):
    if value is None:
        return value
    if not isinstance(value, list):
        raise ValueError("drop_ranges must be a list")
    if len(value) > _DROP_MAX_RANGES:
        raise ValueError(f"too many drop_ranges (max {_DROP_MAX_RANGES})")
    for item in value:
        if isinstance(item, dict):
            numbers = (item.get("start"), item.get("end"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            numbers = (item[0], item[1])
        else:
            raise ValueError("each drop range must be [start, end] or {start, end}")
        for number in numbers:
            if not isinstance(number, (int, float)) or isinstance(number, bool):
                raise ValueError("drop range bounds must be numbers")
            if not math.isfinite(number) or abs(number) > _DROP_MAX_SECONDS:
                raise ValueError("drop range bound out of range")
    return value


_ALLOWED_TOGGLES = frozenset({
    "smartcut", "hook", "subtitles", "logo", "grade", "banner", "player_image",
    "teaser",
})


def _validate_toggles(value):
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("toggles must be an object")
    extra = set(value) - _ALLOWED_TOGGLES
    if extra:
        raise ValueError(f"unknown toggles: {sorted(extra)}")
    for key, enabled in value.items():
        if not isinstance(enabled, bool):
            raise ValueError(f"toggle {key!r} must be boolean")
    return value


# Keys a submitted job may carry as its remembered compose recipe. Mirrors the
# kwargs compose_layers already takes, so a consumer can hand the stored recipe
# straight to POST /api/compose or /api/publish without re-deriving anything.
_COMPOSE_RECIPE_KEYS = frozenset({
    "toggles", "hook_params", "subtitle_params", "logo_params",
    "grade_params", "banner_params", "player_image_params", "teaser_params",
})


def validate_compose_recipe(value):
    """Validate the optional per-job compose recipe (the Create-tab settings).

    Persisted with the job (``job_artifacts.save_job_campaign``) and surfaced
    by ``GET /api/history`` so every consumer burns the layers the user
    actually configured. Without it each consumer invented its own recipe —
    the Discord approval bot in particular composed from its own env vars, so
    a hook configured with a background at Create time reached Discord (and
    then the platform) with none, at whatever position the bot's own env said.
    """
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("compose must be an object")
    extra = set(value) - _COMPOSE_RECIPE_KEYS
    if extra:
        raise ValueError(f"unknown compose keys: {sorted(extra)}")
    if "toggles" in value:
        _validate_toggles(value["toggles"])
    for key, item in value.items():
        if key == "toggles":
            continue
        if item is None:
            continue
        if not isinstance(item, dict):
            raise ValueError(f"compose[{key!r}] must be an object")
        _validate_overlay_params(item, point_keys=_OVERLAY_POINT_KEYS)
    return value


def validate_publish_platforms(value: List[dict]) -> List[dict]:
    """Validate the small, flat Zernio target objects forwarded downstream."""
    allowed = {"tiktok", "instagram", "youtube"}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each platform must be an object")
        platform = item.get("platform")
        if platform not in allowed:
            raise ValueError(f"platform must be one of {sorted(allowed)}")
        account_id = item.get("accountId")
        if not isinstance(account_id, str) or not account_id or len(account_id) > 256:
            raise ValueError("accountId must be a non-empty string (max 256)")
        extra = set(item) - {"platform", "accountId", "platformSpecificData"}
        if extra:
            raise ValueError(f"unexpected platform keys: {sorted(extra)}")
        if "platformSpecificData" in item:
            _validate_overlay_params(item["platformSpecificData"])
    return value


class ComposeRequest(BaseModel):
    toggles: dict = Field(default_factory=dict)
    hook_params: dict = Field(default_factory=dict)
    subtitle_params: dict = Field(default_factory=dict)
    logo_params: dict = Field(default_factory=dict)
    grade_params: dict = Field(default_factory=dict)
    banner_params: dict = Field(default_factory=dict)
    player_image_params: dict = Field(default_factory=dict)
    teaser_params: dict = Field(default_factory=dict)
    drop_ranges: list = Field(default_factory=list)

    @field_validator("toggles")
    @classmethod
    def _bound_toggles(cls, value):
        return _validate_toggles(value)

    @field_validator(
        "hook_params", "subtitle_params", "logo_params", "grade_params", "banner_params",
        "player_image_params", "teaser_params",
    )
    @classmethod
    def _bound_overlay(cls, value):
        return _validate_overlay_params(value, point_keys=_OVERLAY_POINT_KEYS)

    @field_validator("drop_ranges")
    @classmethod
    def _bound_drops(cls, value):
        return _validate_drop_ranges(value)


class EditAIRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=1000)
    model: Optional[str] = Field(
        None, max_length=64, pattern=r"^gemini-[A-Za-z0-9.\-]{1,64}$"
    )


class PublishRequest(BaseModel):
    title: str = Field("", max_length=500)
    caption: str = Field("", max_length=2200)
    platforms: List[dict] = Field(..., min_length=1, max_length=14)
    schedule_mode: str = Field("now", pattern=r"^(now|auto|manual)$")
    scheduled_for: Optional[str] = Field(None, max_length=64)
    start_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field("Europe/Rome", max_length=64)
    tiktok_settings: Optional[dict] = None
    compose_first: bool = False
    toggles: Optional[dict] = None
    hook_params: Optional[dict] = None
    subtitle_params: Optional[dict] = None
    logo_params: Optional[dict] = None
    grade_params: Optional[dict] = None
    banner_params: Optional[dict] = None
    player_image_params: Optional[dict] = None
    teaser_params: Optional[dict] = None
    drop_ranges: Optional[list] = None
    delete_after_publish: bool = True
    # Which named Zernio account this publish uses (see ZernioProfileCreateRequest
    # below). "default" reproduces pre-profile behavior for single-campaign installs.
    zernio_profile: str = Field("default", pattern=r"^[a-z0-9_-]{1,32}$")

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        return _validate_timezone(value)  # type: ignore[return-value]

    @field_validator("scheduled_for")
    @classmethod
    def _validate_scheduled_for(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValueError("scheduled_for must be an ISO 8601 timestamp") from exc
        return value

    @field_validator("toggles")
    @classmethod
    def _bound_toggles(cls, value):
        return _validate_toggles(value)

    @field_validator(
        "hook_params", "subtitle_params", "logo_params", "grade_params", "banner_params",
        "player_image_params", "teaser_params",
    )
    @classmethod
    def _bound_overlay(cls, value):
        return _validate_overlay_params(value, point_keys=_OVERLAY_POINT_KEYS)

    @field_validator("drop_ranges")
    @classmethod
    def _bound_drops(cls, value):
        return None if value is None else _validate_drop_ranges(value)

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, value: List[dict]) -> List[dict]:
        return validate_publish_platforms(value)

    @field_validator("tiktok_settings")
    @classmethod
    def _bound_tiktok_settings(cls, value):
        return _validate_overlay_params(value)


class LiveMonitorStopRequest(BaseModel):
    monitor_id: Optional[str] = Field(
        None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$"
    )


class LiveMonitorPublishingRequest(BaseModel):
    # Strict prevents strings such as 'false' from being coerced to True.
    enabled: bool = Field(strict=True)


class LiveMonitorStartRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=256)
    platform: str = Field("kick", pattern=r"^(kick|twitch|youtube)$")
    mode: str = Field("live", pattern=r"^(live|vod)$")
    platforms: List[dict] = Field(..., min_length=1, max_length=14)
    segment_seconds: int = Field(1800, ge=60, le=3600)
    prelive_skip_seconds: int = Field(1800, ge=0, le=7200)
    min_gap_seconds: int = Field(900, ge=0, le=86400)
    poll_interval: Optional[int] = Field(None, ge=30, le=3600)
    loop: bool = False
    caption_template: str = Field("", max_length=2200)
    title_template: str = Field("", max_length=500)
    instructions: Optional[str] = Field(None, max_length=MAX_INSTRUCTIONS_LEN)
    timezone: Optional[str] = Field(None, max_length=64)
    banner: Optional[dict] = None
    compose: Optional[dict] = None
    catchup: str = Field("backfill", pattern=r"^(backfill|live_only)$")
    delete_after_publish: bool = True
    max_clips: int = Field(5, ge=1, le=50)
    # Start with auto-publishing already paused, so an external approval step
    # (e.g. a Discord gatekeeper calling /api/publish) decides what goes live.
    # Omitted → keep whatever the restored snapshot had, which is how a restart
    # of a deliberately paused monitor stays paused.
    publishing_enabled: Optional[bool] = None
    # Which named Zernio account this monitor auto-publishes through. An
    # identity field (like platform/channel) — not runtime-patchable, see
    # live_monitor._UPDATABLE_CONFIG_FIELDS.
    zernio_profile: str = Field("default", pattern=r"^[a-z0-9_-]{1,32}$")
    # Free-text display label (e.g. a campaign/client name) so a dashboard
    # running several monitors across campaigns can tell them apart without
    # decoding platform+channel — purely cosmetic, so it IS runtime-patchable
    # (see live_monitor._UPDATABLE_CONFIG_FIELDS).
    label: str = Field("", max_length=80)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone(value)

    @field_validator("banner")
    @classmethod
    def _bound_banner(cls, value):
        return _validate_overlay_params(value)

    @field_validator("compose")
    @classmethod
    def _bound_compose(cls, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("compose must be an object")
        if len(value) > 8:
            raise ValueError("compose has too many keys")
        for key, item in value.items():
            if isinstance(item, dict):
                _validate_overlay_params(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                if not math.isfinite(item):
                    raise ValueError(f"compose[{key!r}] must be finite")
            elif not isinstance(item, (str, int, float, bool)) and item is not None:
                raise ValueError(f"compose[{key!r}] must be a scalar or object")
        return value

    @field_validator("slug")
    @classmethod
    def _clean_slug(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("channel must not be blank or contain whitespace")
        return normalized

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, value: List[dict]) -> List[dict]:
        return validate_publish_platforms(value)


class LiveMonitorProbeRequest(BaseModel):
    """Body for POST /api/live-monitor/probe.

    A connectivity + detection check: runs the exact per-platform strategy
    code a real monitor uses (channel reachable? live now? what does the feed
    currently hold?) without starting a monitor or spending any transcription/
    ranking budget — the "does it even see this channel" question, answerable
    before committing to a real run.
    """

    platform: str = Field("kick", pattern=r"^(kick|twitch|youtube)$")
    channel: str = Field(..., min_length=1, max_length=256)
    mode: str = Field("live", pattern=r"^(live|vod)$")


class ZernioConfigRequest(BaseModel):
    api_key: Optional[str] = Field(None, max_length=512)
    accounts: Optional[dict] = None
    timezone: Optional[str] = Field(None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone(value)

    @field_validator("accounts")
    @classmethod
    def _validate_accounts(cls, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("accounts must be an object")
        if len(value) > 16:
            raise ValueError("too many account entries")
        allowed = {"tiktok", "instagram", "youtube"}
        for platform, account_id in value.items():
            if platform not in allowed:
                raise ValueError(f"unknown account platform: {platform!r}")
            if account_id is not None and (
                not isinstance(account_id, str) or len(account_id) > 256
            ):
                raise ValueError(
                    f"account id for {platform!r} must be a string <= 256 chars"
                )
        return value


class ZernioProfileCreateRequest(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9_-]{1,32}$")
    label: Optional[str] = Field(None, max_length=64)

    @field_validator("id")
    @classmethod
    def _reject_default(cls, value: str) -> str:
        if value == "default":
            raise ValueError("'default' already exists and cannot be recreated")
        return value


class ZernioProfileRenameRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)


class StreamLayoutRequest(BaseModel):
    """A saved per-streamer layout — upserted by id via
    POST /api/config/stream-layouts.

    Every geometry field is independently optional so a streamer who only
    needs, say, the gameplay region pinned is not forced to draw the rest;
    omitted fields fall back to the pipeline's own defaults.
    """

    id: str = Field(..., pattern=r"^[a-z0-9_-]{1,40}$")
    label: str = Field(..., min_length=1, max_length=60)
    #: Regions of the SOURCE frame (drawn over a screenshot of the stream).
    facecam: Optional[GamingFacecamBox] = None
    gameplay: Optional[GamingGameplayBox] = None
    #: Positions on the DELIVERED 9:16 frame.
    split: Optional[float] = Field(default=None, ge=GAMING_SPLIT_MIN, le=GAMING_SPLIT_MAX)
    hook_y: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    subtitle_y: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class CaptionPresetRequest(BaseModel):
    """A saved caption template (e.g. one per seller in a multi-account
    clipping campaign) — upserted by id via POST /api/config/caption-presets."""

    id: str = Field(..., pattern=r"^[a-z0-9_-]{1,40}$")
    label: str = Field(..., min_length=1, max_length=60)
    text: str = Field("", max_length=2200)
