"""FastAPI application for the local web UI.

Deliberately small: routes validate input, call into
:mod:`clipper_pro.web.runs` / :mod:`clipper_pro.web.worker`, and return JSON.

Why this is safe to run on a laptop
-----------------------------------
The server starts downloads and spawns ffmpeg, so it is only ever bound to
loopback (see :mod:`clipper_pro.web.server`). Loopback alone is not quite enough,
though: any website open in the same browser can POST to ``localhost`` and would
otherwise be able to make this machine download and transcode a video of the
attacker's choosing. Two cheap guards close that:

* a cross-origin ``Origin`` header is rejected outright, and
* state-changing routes take a JSON body, which a plain cross-site form cannot
  send without a CORS preflight that this app never answers.

No CORS middleware is installed, on purpose — there is no second origin that
should be talking to this.
"""

from __future__ import annotations

import os
import shutil
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from pydantic import BaseModel, Field, field_validator

from clipper_pro import __version__
from clipper_pro.config import (
    CAPTION_POSITIONS,
    CAPTION_PRESETS,
    HOOK_POSITIONS,
    HOOK_STYLES,
    RANKERS,
    TRANSCRIBERS,
)
from clipper_pro.errors import ClipperProError
from clipper_pro.pipeline import PhaseOptions
from clipper_pro.web.runs import RunRegistry, clip_path, load_report
from clipper_pro.web.worker import RunWorker

__all__ = ["create_app"]

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class StartRunRequest(BaseModel):
    """Body for ``POST /api/runs``."""

    source: str = Field(min_length=1, max_length=2048)
    transcribe_provider: str | None = None
    rank_provider: str | None = None
    max_clips: int | None = Field(default=None, ge=1, le=50)
    instructions: str | None = Field(default=None, max_length=2000)
    centred: bool = True
    require_events: bool = False
    no_silence: bool = False
    crf: int | None = Field(default=None, ge=0, le=51)
    captions: bool = False
    caption_preset: str | None = None
    caption_words_per_group: int | None = Field(default=None, ge=1, le=12)
    caption_position: str | None = None
    hooks: bool = False
    hook_style: str | None = None
    hook_position: str | None = None
    hook_font_size: int | None = Field(default=None, ge=20, le=300)

    @field_validator("caption_preset")
    @classmethod
    def _known_preset(cls, value: str | None) -> str | None:
        if value is not None and value not in CAPTION_PRESETS:
            raise ValueError(f"expected one of {', '.join(CAPTION_PRESETS)}")
        return value

    @field_validator("caption_position")
    @classmethod
    def _known_position(cls, value: str | None) -> str | None:
        if value is not None and value not in CAPTION_POSITIONS:
            raise ValueError(f"expected one of {', '.join(CAPTION_POSITIONS)}")
        return value

    @field_validator("hook_style")
    @classmethod
    def _known_hook_style(cls, value: str | None) -> str | None:
        if value is not None and value not in HOOK_STYLES:
            raise ValueError(f"expected one of {', '.join(HOOK_STYLES)}")
        return value

    @field_validator("hook_position")
    @classmethod
    def _known_hook_position(cls, value: str | None) -> str | None:
        if value is not None and value not in HOOK_POSITIONS:
            raise ValueError(f"expected one of {', '.join(HOOK_POSITIONS)}")
        return value

    def to_options(self) -> PhaseOptions:
        return PhaseOptions(
            transcribe_provider=self.transcribe_provider,
            require_events=self.require_events,
            rank_provider=self.rank_provider,
            max_clips=self.max_clips,
            instructions=self.instructions,
            no_silence=self.no_silence,
            centred=self.centred,
            crf=self.crf,
            captions=self.captions,
            caption_preset=self.caption_preset,
            caption_words_per_group=self.caption_words_per_group,
            caption_position=self.caption_position,
            hooks=self.hooks,
            hook_style=self.hook_style,
            hook_position=self.hook_position,
            hook_font_size=self.hook_font_size,
        )


def create_app(runs_root: str | None = None) -> FastAPI:
    """Build the app. ``runs_root`` overrides where run workspaces are written."""
    app = FastAPI(title="AI-Clipper Pro", version=__version__, docs_url=None, redoc_url=None)
    registry = RunRegistry(runs_root)
    # Adopt runs already on disk, so restarting the server does not look like
    # the user's clips disappeared.
    registry.rehydrate()
    worker = RunWorker(registry)

    app.state.registry = registry
    app.state.worker = worker

    @app.middleware("http")
    async def reject_cross_origin(request: Request, call_next):
        """Refuse requests a different website told the browser to send."""
        origin = request.headers.get("origin")
        if origin:
            host = urlparse(origin).hostname
            if host not in ("localhost", "127.0.0.1", "::1"):
                return JSONResponse(
                    {"detail": "cross-origin requests are not accepted"}, status_code=403
                )
        return await call_next(request)

    @app.exception_handler(ClipperProError)
    async def _domain_error(_request: Request, exc: ClipperProError) -> JSONResponse:
        # Map the pipeline's own errors to 400 — they mean "your input or your
        # workspace is wrong", never "the server broke".
        return JSONResponse({"detail": exc.detail}, status_code=400)

    # The next three read files, so they are defined sync: Starlette runs a
    # sync handler in a threadpool, where blocking IO belongs. Declaring them
    # async would stall the event loop for every other request.
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        path = os.path.join(_STATIC_DIR, "index.html")
        if not os.path.isfile(path):  # pragma: no cover - packaging guard
            raise HTTPException(500, "UI asset missing from the installation")
        with open(path, encoding="utf-8") as fh:
            return HTMLResponse(fh.read())

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """What the UI needs to warn about *before* a run wastes ten minutes."""
        return {
            "version": __version__,
            "runs_root": registry.runs_root,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "keys": {
                "deepgram": bool(os.getenv("DEEPGRAM_API_KEY", "").strip()),
                "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY", "").strip()),
                "deepseek": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
                "gemini": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            },
            "transcribers": list(TRANSCRIBERS),
            "rankers": list(RANKERS),
            "caption_presets": list(CAPTION_PRESETS),
            "caption_positions": list(CAPTION_POSITIONS),
            "hook_styles": list(HOOK_STYLES),
            "hook_positions": list(HOOK_POSITIONS),
            "busy": registry.active() is not None,
        }

    @app.post("/api/runs", status_code=201)
    async def start_run(body: StartRunRequest) -> dict[str, Any]:
        # One at a time: two concurrent ffmpeg renders would each run at half
        # speed, and the captured log could not be attributed to either run.
        if registry.active() is not None:
            raise HTTPException(409, "a run is already in progress")

        source = body.source.strip()
        # Check the obvious mistakes now rather than accepting the run and
        # failing a second later: a typo belongs next to the input field, not
        # in a failed run's log. Raises ClipperProError, mapped to 400 above.
        _validate_source(source)

        record = registry.create(source)
        worker.start(record, body.to_options())
        return record.to_dict()

    @app.get("/api/runs")
    async def list_runs() -> dict[str, Any]:
        return {
            "runs": [r.to_dict(include_log=False) for r in registry.list()],
            "runs_root": registry.runs_root,
        }

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        snapshot = registry.snapshot(run_id)
        if snapshot is None:
            raise HTTPException(404, "no such run")
        return snapshot

    @app.get("/api/runs/{run_id}/report")
    def get_report(run_id: str) -> dict[str, Any]:
        record = _require_run(registry, run_id)
        report = load_report(record.work_dir)
        if report is None:
            raise HTTPException(404, "this run has no report yet")
        return report

    @app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
    def get_report_markdown(run_id: str) -> PlainTextResponse:
        record = _require_run(registry, run_id)
        path = os.path.join(record.work_dir, "reports", "draft.md")
        if not os.path.isfile(path):
            raise HTTPException(404, "this run has no report yet")
        with open(path, encoding="utf-8") as fh:
            return PlainTextResponse(fh.read())

    @app.get("/api/runs/{run_id}/clips/{index}/video")
    async def get_clip(run_id: str, index: int, download: bool = False) -> FileResponse:
        record = _require_run(registry, run_id)
        # Resolved from the report by index and confirmed to sit inside this
        # run's renders directory — the client never names a file.
        path = clip_path(record.work_dir, index)
        if path is None:
            raise HTTPException(404, "no such clip")

        # Inline by default: passing `filename` makes Starlette send
        # `Content-Disposition: attachment`, which tells the browser to save the
        # file instead of playing it — and the page previews clips in a <video>
        # element. The download link asks for the attachment explicitly, which
        # is also how the file gets its real name rather than "video".
        if download:
            return FileResponse(
                path, media_type="video/mp4", filename=os.path.basename(path)
            )
        return FileResponse(path, media_type="video/mp4")

    return app


def _require_run(registry: RunRegistry, run_id: str):
    record = registry.get(run_id)
    if record is None:
        raise HTTPException(404, "no such run")
    return record


def _validate_source(source: str) -> None:
    """Reject a source that cannot possibly work, before a run is created.

    Only the cheap, local checks: an unusable scheme, or a local path that is
    missing or is not a video. Whether a remote host is an *allowed* platform
    stays with the downloader, which owns that policy — this is about catching
    typos, not about duplicating the allow-list.
    """
    from clipper_pro.ingest.source_ops import (
        SOURCE_REMOTE,
        classify_source,
        resolve_local_source,
    )

    if classify_source(source) != SOURCE_REMOTE:
        resolve_local_source(source)
