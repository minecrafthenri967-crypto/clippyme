"""Pure text-hook logic: wording, styling, and the ASS document itself.

A *hook* is the 3-8 word overlay that sits on top of a short-form clip and tells
a scrolling viewer what they are about to watch. It is not a caption: captions
transcribe what is being said and change every second, the hook says one thing
and holds.

Three properties are deliberate here, because they are what the feature was
asked for:

**The hook holds for the whole clip.** ClippyMe's own hook overlay shows for the
first four seconds and then leaves. That suits a hook whose job is only to stop
the scroll, but a viewer arriving at second nine — which on a loop is most of
them — then sees nothing at all. One event spanning the clip costs nothing extra
to render and survives the loop, so the hook is emitted with no time limit.

**The hook is never centred.** A centre-aligned overlay lands exactly where the
speaker's face does after a 9:16 reframe, so it hides the thing the clip is of.
:data:`HOOK_POSITIONS` therefore offers no centre option at all — the choice is
between the upper and lower thirds, defaulting to the upper third where it
clears both the face and any burned-in captions at the bottom.

**The border is a setting, not a fixed look.** ``boxed_light`` is the familiar
white-slab-with-dark-text treatment; ``outline`` keeps the frame visible behind
a thick contrasting stroke; ``shadow`` is the least intrusive. All three are
libass primitives, so the hook burns in during the render pass that was going to
run anyway — no image compositing, no extra dependency, no extra encode.

Stdlib-only: the whole document is built and asserted on in the host suite
without running ffmpeg.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clipper_pro.errors import ValidationError

__all__ = [
    "DEFAULT_HOOK_FONT_SIZE",
    "HOOK_FONTS",
    "HOOK_MAX_WORDS",
    "HOOK_MIN_WORDS",
    "HOOK_POSITIONS",
    "HOOK_STYLES",
    "HookSettings",
    "build_hook_ass",
    "hook_line_count",
    "normalise_hook_text",
    "wrap_hook_text",
]

#: Style treatments, each a border recipe rather than a whole design.
#:
#: ``boxed_light`` / ``boxed_dark`` use ASS ``BorderStyle=3``, where the outline
#: width becomes padding around an opaque slab — the look in the reference
#: screenshot. ``outline`` uses ``BorderStyle=1`` with a heavy stroke, which
#: keeps the footage visible behind the words. ``shadow`` is the same minus the
#: stroke, for footage that is already dark enough to carry white text.
HOOK_STYLES: tuple[str, ...] = ("boxed_light", "boxed_dark", "outline", "shadow")

#: Where the hook sits. There is deliberately no ``center``: after a 9:16 crop
#: the centre of the frame is the speaker's face, and an overlay there hides the
#: subject of the clip.
HOOK_POSITIONS: tuple[str, ...] = ("top", "upper_third", "lower_third", "bottom")

#: Faces bundled with the host repository, named by file basename because that
#: is what libass matches against when handed a ``fontsdir``.
HOOK_FONTS: tuple[str, ...] = (
    "Montserrat-ExtraBold",
    "Montserrat-Black",
    "Poppins-Black",
    "Poppins-Medium",
    "Anton-Regular",
    "Bangers-Regular",
    "NotoSerif-Bold",
)

#: The word window the ranker is asked for and this module enforces. Below three
#: words a hook is a label, not a promise; above eight it cannot be read in the
#: glance a scrolling viewer gives it.
HOOK_MIN_WORDS = 3
HOOK_MAX_WORDS = 8

#: Against a 1920-tall frame this is about 4% of frame height — large enough to
#: read on a phone held at arm's length, small enough that eight words still wrap
#: to two lines rather than four.
DEFAULT_HOOK_FONT_SIZE = 76

#: Characters per line before wrapping. Chosen against the default size so a
#: full-width line still clears the 1080-wide frame's safe margins.
DEFAULT_MAX_CHARS_PER_LINE = 22

#: Vertical placement per position: ``(ASS alignment, margin as a fraction of
#: frame height)``. 8 is top-centre, 2 is bottom-centre; the margin is measured
#: from that edge. The two "third" positions clear the middle band where the
#: reframed subject lives.
_PLACEMENT: dict[str, tuple[int, float]] = {
    "top": (8, 0.06),
    "upper_third": (8, 0.18),
    "lower_third": (2, 0.26),
    "bottom": (2, 0.08),
}

#: ``(text colour, border colour)`` as ``#RRGGBB``, plus the border geometry.
#: In ``BorderStyle=3`` the outline colour paints the slab, so ``boxed_light``
#: is dark text on a white slab.
_STYLE_SPEC: dict[str, dict[str, object]] = {
    "boxed_light": {
        "text": "#1E1E1E", "border": "#FFFFFF",
        "border_style": 3, "outline": 10, "shadow": 0,
    },
    "boxed_dark": {
        "text": "#FFFFFF", "border": "#101010",
        "border_style": 3, "outline": 10, "shadow": 0,
    },
    "outline": {
        "text": "#FFFFFF", "border": "#000000",
        "border_style": 1, "outline": 7, "shadow": 0,
    },
    "shadow": {
        "text": "#FFFFFF", "border": "#000000",
        "border_style": 1, "outline": 0, "shadow": 5,
    },
}

#: Same guard the host repository applies to font names: the value is
#: interpolated verbatim into the ASS style line, where a comma or a brace would
#: be structure rather than text.
_FONT_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class HookSettings:
    """How the hook should look. Validated on construction."""

    enabled: bool = False
    style: str = "boxed_light"
    position: str = "upper_third"
    font: str = "Montserrat-ExtraBold"
    font_size: int = DEFAULT_HOOK_FONT_SIZE
    max_words: int = HOOK_MAX_WORDS
    uppercase: bool = False
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE

    def __post_init__(self) -> None:
        if self.style not in HOOK_STYLES:
            raise ValidationError(
                f"unknown hook style {self.style!r} — expected one of "
                f"{', '.join(HOOK_STYLES)}"
            )
        if self.position not in HOOK_POSITIONS:
            raise ValidationError(
                f"unknown hook position {self.position!r} — expected one of "
                f"{', '.join(HOOK_POSITIONS)} (there is no centre position: it "
                f"would cover the speaker)"
            )
        if not _FONT_NAME_RE.match(self.font):
            raise ValidationError(f"invalid hook font name: {self.font!r}")
        if not 20 <= self.font_size <= 300:
            raise ValidationError(
                f"hook font_size must be within 20–300, got {self.font_size}"
            )
        if not HOOK_MIN_WORDS <= self.max_words <= 16:
            raise ValidationError(
                f"hook max_words must be within {HOOK_MIN_WORDS}–16, "
                f"got {self.max_words}"
            )
        if not 8 <= self.max_chars_per_line <= 60:
            raise ValidationError(
                f"max_chars_per_line must be within 8–60, "
                f"got {self.max_chars_per_line}"
            )


def normalise_hook_text(text: str, *, max_words: int = HOOK_MAX_WORDS) -> str:
    """Collapse whitespace, strip ASS structure, and cap the word count.

    Truncating rather than rejecting is the right failure mode: a ranker that
    returns a twelve-word sentence has still identified the right idea, and the
    first eight words of a hook are the ones that do the work. Returning ``""``
    for unusable input lets the caller render the clip with no overlay instead
    of failing a render that was otherwise complete.
    """
    cleaned = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    # Braces open an ASS override block, so text carrying them could inject
    # positioning or timing directives into the document.
    cleaned = cleaned.replace("{", "").replace("}", "").replace("\\", "")
    if not cleaned:
        return ""
    words = cleaned.split(" ")
    return " ".join(words[: max(1, max_words)])


def wrap_hook_text(text: str, *, max_chars_per_line: int) -> list[str]:
    """Greedily wrap ``text`` into lines of at most ``max_chars_per_line``.

    Greedy rather than balanced: a hook is two or three lines, and a balanced
    algorithm's benefit at that length is invisible next to the cost of the
    extra machinery. A single word longer than the limit gets its own line
    rather than being broken mid-word — a hyphenated fragment reads as a typo.
    """
    if max_chars_per_line <= 0:
        raise ValidationError(
            f"max_chars_per_line must be positive, got {max_chars_per_line}"
        )
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars_per_line:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def hook_line_count(text: str, settings: HookSettings) -> int:
    """How many lines ``text`` will occupy under ``settings``."""
    normalised = normalise_hook_text(text, max_words=settings.max_words)
    if not normalised:
        return 0
    return len(wrap_hook_text(normalised, max_chars_per_line=settings.max_chars_per_line))


def _ass_colour(hex_colour: str) -> str:
    """``#RRGGBB`` → ASS ``&HAABBGGRR`` (opaque). ASS orders bytes backwards."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        raise ValidationError(f"expected a #RRGGBB colour, got {hex_colour!r}")
    try:
        red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError as exc:
        raise ValidationError(f"unparseable colour {hex_colour!r}") from exc
    return f"&H00{blue:02X}{green:02X}{red:02X}"


def _ass_time(seconds: float) -> str:
    """``H:MM:SS.cc`` — the only time format the ASS event line accepts."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:  # rounding carried into the next second
        centis = 0
        whole += 1
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def build_hook_ass(
    text: str,
    *,
    settings: HookSettings,
    start: float,
    end: float,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
) -> str:
    """Build the ASS document holding one hook, or ``""`` if there is nothing to show.

    ``start``/``end`` are in the same timebase the render's filter graph sees.
    Phase 6 seeks with ``-ss`` *after* ``-i``, so frames still carry their source
    timestamps — the caller passes source time, exactly as
    :mod:`clipper_pro.render.captions` does, and for the same reason.

    The single event spans that whole range: the hook is meant to be readable
    whenever the viewer arrives, including on the second loop.
    """
    normalised = normalise_hook_text(text, max_words=settings.max_words)
    if not normalised or end <= start:
        return ""
    if settings.uppercase:
        normalised = normalised.upper()

    lines = wrap_hook_text(normalised, max_chars_per_line=settings.max_chars_per_line)
    # ASS's own line break. WrapStyle 2 below disables libass's automatic
    # wrapping so these are the only breaks in the result.
    body = r"\N".join(lines)

    spec = _STYLE_SPEC[settings.style]
    alignment, margin_ratio = _PLACEMENT[settings.position]
    margin_v = int(round(play_res_y * margin_ratio))
    # Horizontal safe margins keep the slab clear of the platform's own UI
    # (the caption rail on one side, the action buttons on the other).
    margin_h = int(round(play_res_x * 0.08))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        # 2 = no automatic wrapping; only explicit \N breaks.
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Hook,{settings.font},{settings.font_size},"
        f"{_ass_colour(str(spec['text']))},{_ass_colour(str(spec['text']))},"
        f"{_ass_colour(str(spec['border']))},{_ass_colour(str(spec['border']))},"
        f"-1,0,0,0,100,100,0,0,"
        f"{spec['border_style']},{spec['outline']},{spec['shadow']},"
        f"{alignment},{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    # Layer 1 so the hook draws above captions when both are burned in.
    event = (
        f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Hook,,0,0,0,,{body}\n"
    )
    return header + event
