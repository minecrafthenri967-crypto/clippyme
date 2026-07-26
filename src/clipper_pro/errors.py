"""Exception hierarchy for the clipper-pro pipeline.

Phase modules raise these instead of printing and returning ``None`` so a
failure carries its cause to whoever is driving the run — the CLI turns them
into a one-line stderr message plus ``exit_code``, and an embedding host (the
ClippyMe API, an agent harness) can map them however it likes.

Deliberately framework-free: no FastAPI, no click. ``exit_code`` values follow
the usual CLI convention that 1 is "your input was wrong" and 2+ distinguishes
environment problems worth retrying differently.
"""

__all__ = [
    "ClipperProError",
    "ConfigError",
    "IngestError",
    "MissingBinaryError",
    "ToolFailureError",
    "ValidationError",
]


class ClipperProError(Exception):
    """Base error. Carries the message plus a process exit code."""

    exit_code = 1

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class ValidationError(ClipperProError):
    """Caller supplied something structurally wrong (bad spec, bad range)."""

    exit_code = 1


class ConfigError(ClipperProError):
    """A required setting is missing or out of range."""

    exit_code = 1


class MissingBinaryError(ClipperProError):
    """A required external binary (ffmpeg/ffprobe) is not on PATH."""

    exit_code = 3

    def __init__(self, name: str):
        self.name = name
        super().__init__(
            f"required binary {name!r} not found on PATH — install ffmpeg "
            f"(https://ffmpeg.org/download.html) and retry"
        )


class ToolFailureError(ClipperProError):
    """An external tool ran but exited non-zero, or produced unusable output."""

    exit_code = 4


class IngestError(ClipperProError):
    """Phase 1 could not obtain usable media (download or extraction failed)."""

    exit_code = 5
