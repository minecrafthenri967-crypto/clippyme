"""Neutral data schemas shared across layers.

``ViralClip`` / ``ViralClipsResponse`` are the contract between the Gemini
viral-detection prompt (pipeline) and the rest of the app. They live here — in
a layer that depends on neither ``api`` nor ``pipeline`` — so the pipeline no
longer has to import from ``clippyme.api`` (which inverted the intended
dependency direction). ``clippyme.api.schemas`` re-exports them for backward
compatibility.
"""
from pydantic import BaseModel, Field, field_validator, model_validator

# Bounds on the "peak moment" window Gemini marks inside each clip (the
# strongest 1-3s — punchline, big reaction, payoff line — used to build a
# cold-open teaser before the clip plays from its real start).
#
# Shorter than MIN is a degenerate/typo value; longer than MAX is not a
# "moment" at all but a second clip, which means the model misread the task.
# Either way the window is CLEARED rather than used or clamped: a guessed
# teaser that opens on the wrong footage is worse than no teaser, and the
# clip itself is still perfectly good without one.
#
# The prompt asks for 1-3s; these bounds are deliberately wider so a
# near-miss survives instead of being thrown away (same reasoning as
# ViralClip's own 10-75s duration band vs. the 15-60s the prompt requests).
MIN_PEAK_DURATION = 0.8
MAX_PEAK_DURATION = 10.0


def _coerce_timestamp_value(v):
    """Normalize a Gemini timestamp to float seconds.

    Gemini 2.5-flash occasionally emits a timestamp as a *dotted* or
    *colon-separated* time string instead of float seconds. Seen:

    * ``"25.17.724"`` → 25 min 17.724 s (MM.SS.mmm)
    * ``"1.25.17.724"`` → 1 h 25 min 17.724 s (HH.MM.SS.mmm)
    * ``"25:17.724"`` / ``"1:25:17"`` → HH:MM:SS

    Shared by every schema with a Gemini-sourced timestamp field (not just
    ``ViralClip``) so each one is self-defending wherever it's used, not only
    via ``gemini_parser.validate_and_dedupe``. Numeric values and single-dot
    float strings pass through unchanged.
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return v
    # Colon-separated (HH:MM:SS or MM:SS, optional decimal seconds)
    if ":" in s:
        try:
            parts = s.split(":")
            if len(parts) == 2:
                return float(parts[0]) * 60.0 + float(parts[1])
            if len(parts) == 3:
                return (
                    float(parts[0]) * 3600.0
                    + float(parts[1]) * 60.0
                    + float(parts[2])
                )
        except ValueError:
            return v
        return v
    # Dotted. One dot = ordinary float; 2+ = MM.SS[.ms] / HH.MM.SS[.ms]
    if s.count(".") <= 1:
        return v
    parts = s.split(".")
    try:
        if len(parts) == 3:
            mm, ss, ms = parts
            return float(mm) * 60.0 + float(ss) + float(f"0.{ms}")
        if len(parts) == 4:
            hh, mm, ss, ms = parts
            return (
                float(hh) * 3600.0
                + float(mm) * 60.0
                + float(ss)
                + float(f"0.{ms}")
            )
    except ValueError:
        return v
    return v


class ViralClip(BaseModel):
    """A single viral clip candidate emitted by Gemini and validated
    before it's handed to the reframing pipeline.

    Duration bounds are deliberately a bit wider than the user-facing
    15-60s target (10-75s) so we don't throw away near-misses that the
    Smart Cut post-processing can still rescue.
    """
    start: float = Field(..., ge=0)
    end: float = Field(..., gt=0)
    viral_score: int = Field(..., ge=1, le=100)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v):
        return _coerce_timestamp_value(v)
    viral_reason: str = Field(..., min_length=20)
    video_description_for_tiktok: str = ""
    video_description_for_instagram: str = ""
    # YouTube Shorts enforces 100 chars; we leave a tiny cushion (10 chars)
    # because Gemini sometimes appends a stray period or ellipsis that we
    # trim during normalization anyway.
    video_title_for_youtube_short: str = Field("", max_length=110)
    viral_hook_text: str = Field("", max_length=160)
    # The strongest moment INSIDE this clip, in the same absolute source
    # seconds as start/end (not clip-relative). Optional by design — see
    # _coerce_peak_timestamp / _validate_peak_window below for why a bad or
    # missing value must never cost us the clip.
    peak_start: float | None = None
    peak_end: float | None = None

    @field_validator("peak_start", "peak_end", mode="before")
    @classmethod
    def _coerce_peak_timestamp(cls, v):
        """Coerce like start/end, but NEVER raise.

        start/end are load-bearing, so a malformed value there rightly kills
        the clip. The peak is a bonus: letting an unparseable value raise
        would reject an otherwise-perfect clip over an optional field, so
        anything non-numeric collapses to None instead.
        """
        if v is None:
            return None
        try:
            return float(_coerce_timestamp_value(v))
        except (TypeError, ValueError):
            return None

    @model_validator(mode="after")
    def _validate_peak_window(self):
        """Clear the peak window unless it is a sane moment inside the clip.

        Runs after start/end are known, and CLEARS rather than raises for the
        same reason as above. Note this deliberately does not clamp a
        too-long window down to size: the point of the peak is that Gemini
        identified a specific moment, and a window we had to reshape is no
        longer that moment.
        """
        start, end = self.peak_start, self.peak_end
        if start is None or end is None:
            self.peak_start = self.peak_end = None
            return self
        duration = end - start
        if (
            end <= start
            or start < self.start
            or end > self.end
            or duration < MIN_PEAK_DURATION
            or duration > MAX_PEAK_DURATION
        ):
            self.peak_start = self.peak_end = None
        return self

    @field_validator(
        "viral_reason",
        "video_description_for_tiktok",
        "video_description_for_instagram",
        "video_title_for_youtube_short",
        "viral_hook_text",
    )
    @classmethod
    def _normalize_whitespace(cls, v: str) -> str:
        """Collapse whitespace runs and strip.

        Gemini occasionally emits multiline values with stray \\n or \\t
        that break downstream rendering (ASS lines, drawtext, UI cards).
        Normalize once at the edge so the rest of the pipeline sees clean
        single-line text for every string field.
        """
        if not isinstance(v, str):
            return v
        return " ".join(v.split()).strip()

    @field_validator("end")
    @classmethod
    def _duration_in_range(cls, v: float, info) -> float:
        start = info.data.get("start", 0.0) or 0.0
        if v <= start:
            raise ValueError(f"end ({v}) must be strictly greater than start ({start})")
        duration = v - start
        if duration < 10 or duration > 75:
            raise ValueError(
                f"clip duration {duration:.2f}s outside allowed range [10, 75]"
            )
        return v


class ViralClipsResponse(BaseModel):
    """Top-level response shape from the Gemini viral-moment prompt."""
    shorts: list[ViralClip] = Field(..., min_length=0, max_length=20)


class PlayerMention(BaseModel):
    """One detected athlete-name mention, emitted by the player-image
    overlay's Gemini call (``domain.player_detect``)."""
    player_name: str = Field(..., min_length=1, max_length=120)
    timestamp: float = Field(..., ge=0)
    confidence: float = Field(..., ge=0, le=1)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v):
        return _coerce_timestamp_value(v)

    @field_validator("player_name")
    @classmethod
    def _normalize_whitespace(cls, v: str) -> str:
        return " ".join(v.split()).strip()


class PlayerMentionsResponse(BaseModel):
    """Top-level response shape from the player-name-detection prompt."""
    mentions: list[PlayerMention] = Field(default_factory=list, max_length=20)
