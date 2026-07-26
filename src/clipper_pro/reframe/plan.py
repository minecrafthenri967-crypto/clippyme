"""The camera plan — phase 5's output contract, consumed by phase 6."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from clipper_pro.errors import ValidationError
from clipper_pro.types import CameraKeyframe

__all__ = ["CameraPlan", "probe_geometry"]


@dataclass(frozen=True)
class CameraPlan:
    """A clip's crop trajectory plus the geometry it was computed against.

    Source dimensions and fps travel with the keyframes because phase 6 needs
    them to build its filtergraph, and a plan that outlived the source's metadata
    would otherwise be uninterpretable.
    """

    clip_index: int
    start: float
    end: float
    source_width: int
    source_height: int
    fps: float
    keyframes: list[CameraKeyframe] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValidationError(
                f"plan {self.clip_index}: end ({self.end}) must exceed start ({self.start})"
            )
        if self.fps <= 0:
            raise ValidationError(f"plan {self.clip_index}: fps must be positive")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_index": self.clip_index,
            "start": self.start,
            "end": self.end,
            "duration": round(self.duration, 3),
            "source_width": self.source_width,
            "source_height": self.source_height,
            "fps": self.fps,
            "keyframes": [kf.to_dict() for kf in self.keyframes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraPlan:
        return cls(
            clip_index=int(data["clip_index"]),
            start=float(data["start"]),
            end=float(data["end"]),
            source_width=int(data["source_width"]),
            source_height=int(data["source_height"]),
            fps=float(data["fps"]),
            keyframes=[CameraKeyframe.from_dict(k) for k in data.get("keyframes") or []],
        )


def probe_geometry(video_path: str) -> tuple[int, int, float]:
    """Source ``(width, height, fps)``, falling back to 1920x1080 @ 30.

    Dimensions come from the host repository's ``probe_dimensions``; the frame
    rate needs its own ffprobe call since no helper there returns it. Never
    raises — an unprobeable source gets the defaults, because guessing 1080p/30
    for a downloaded video is far better than failing the phase.
    """
    width, height = 1920, 1080
    try:
        from clippyme.pipeline.media_probe import parse_frame_rate, probe_dimensions

        width, height = probe_dimensions(video_path, default=(1920, 1080))
    except (ImportError, OSError, ValueError):
        return width, height, 30.0

    fps = 30.0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "json", video_path],
            capture_output=True, text=True, timeout=60, check=False,
        )
        streams = json.loads(result.stdout or "{}").get("streams") or []
        if streams:
            parsed = parse_frame_rate(str(streams[0].get("r_frame_rate") or ""))
            if parsed > 0:
                fps = parsed
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return width, height, fps
