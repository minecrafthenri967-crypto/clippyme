"""The local web UI — a private front end for the pipeline.

Runs on ``localhost`` only and drives the same
:func:`clipper_pro.pipeline.run_phase` the CLI uses, so the two front ends
cannot disagree about what a phase does.

Start it with ``clipper-pro web``. Submodules:

* :mod:`~clipper_pro.web.runs` — run state, naming, safe path resolution (pure)
* :mod:`~clipper_pro.web.worker` — background execution with log capture
* :mod:`~clipper_pro.web.app` — the FastAPI routes
* :mod:`~clipper_pro.web.server` — uvicorn entry point
"""

from __future__ import annotations

__all__ = ["create_app", "serve"]


def create_app(runs_root: str | None = None):
    """Build the FastAPI app (imported lazily so ``clipper_pro`` needs no web deps)."""
    from clipper_pro.web.app import create_app as _create_app

    return _create_app(runs_root)


def serve(**kwargs):
    """Start the local server. See :func:`clipper_pro.web.server.serve`."""
    from clipper_pro.web.server import serve as _serve

    return _serve(**kwargs)
