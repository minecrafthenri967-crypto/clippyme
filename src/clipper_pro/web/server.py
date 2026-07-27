"""Start the local web UI.

Loopback by default and loudly reported otherwise: this server starts downloads
and spawns ffmpeg on the machine it runs on, so exposing it to a network is a
decision that should never happen by accident.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

__all__ = ["serve"]

#: Anything outside these is reachable from other machines.
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8720,
    runs_dir: str | None = None,
    open_browser: bool = True,
) -> int:
    """Run the UI until interrupted; returns a process exit code."""
    import uvicorn

    from clipper_pro.web.app import create_app
    from clipper_pro.web.runs import default_runs_root

    root = os.path.abspath(os.path.expanduser(runs_dir)) if runs_dir else default_runs_root()
    os.makedirs(root, exist_ok=True)

    url = f"http://{'localhost' if host in _LOOPBACK else host}:{port}"
    print(f"\n  AI-Clipper Pro — {url}", file=sys.stderr)
    print(f"  Runs are saved in: {root}", file=sys.stderr)
    if host not in _LOOPBACK:
        print(
            f"  ⚠️  Bound to {host}, not just this machine. Anyone who can reach "
            f"this port can make it download and transcode video.",
            file=sys.stderr,
        )
    print("  Press Ctrl-C to stop.\n", file=sys.stderr)

    if open_browser:
        # Deferred: the browser must not race the server to the first request.
        threading.Timer(1.0, lambda: _open(url)).start()

    try:
        uvicorn.run(create_app(root), host=host, port=port, log_level="warning")
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        print("\n  Stopped.", file=sys.stderr)
    return 0


def _open(url: str) -> None:
    """Open a browser, tolerating environments that have none (WSL, servers)."""
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001, S110 - headless box: the URL is printed above
        pass
