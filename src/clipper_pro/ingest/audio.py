"""Audio extraction — the thin I/O half of phase 1.

All argv construction, probe parsing and verification logic lives next door in
:mod:`clipper_pro.ingest.audio_ops`; this module only runs the subprocesses and
writes the provenance sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

from clipper_pro import SCHEMA_VERSION
from clipper_pro.errors import IngestError, MissingBinaryError, ToolFailureError
from clipper_pro.ingest.audio_ops import (
    AudioSpec,
    AudioStreamInfo,
    asr_output_name,
    build_extract_command,
    build_probe_command,
    format_size_saving,
    parse_audio_stream,
    verify_extraction,
)

__all__ = ["extract_analysis_audio", "probe_audio", "require_binary", "write_sidecar"]


def require_binary(name: str) -> str:
    """Resolve an external binary on PATH or raise :class:`MissingBinaryError`."""
    path = shutil.which(name)
    if path is None:
        raise MissingBinaryError(name)
    return path


def _run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a tool, converting every failure mode into a ToolFailureError."""
    binary = require_binary(cmd[0])
    try:
        proc = subprocess.run(
            [binary, *cmd[1:]],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFailureError(
            f"{cmd[0]} timed out after {timeout}s — the source may be corrupt "
            f"or the file far larger than expected"
        ) from exc
    if proc.returncode != 0:
        # ffmpeg's useful diagnosis is always in the last few stderr lines; the
        # preceding banner noise would bury it in a job log.
        tail = " | ".join((proc.stderr or "").strip().splitlines()[-6:])
        raise ToolFailureError(f"{cmd[0]} exited with {proc.returncode}: {tail}")
    return proc


def probe_audio(path: str, *, timeout: int = 120) -> AudioStreamInfo | None:
    """ffprobe ``path`` and return its first audio stream, or ``None`` if absent."""
    proc = _run(build_probe_command(path), timeout=timeout)
    try:
        payload: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolFailureError(f"ffprobe produced invalid JSON for {path}: {exc}") from exc
    return parse_audio_stream(payload)


def extract_analysis_audio(
    source_path: str,
    output_dir: str,
    spec: AudioSpec | None = None,
    *,
    timeout: int = 1800,
    overwrite: bool = False,
    verify: bool = True,
) -> str:
    """Extract mono 16 kHz FLAC analysis audio from ``source_path``.

    Returns the path to the extracted file. Raises :class:`IngestError` if the
    source has no audio, or :class:`ToolFailureError` if ffmpeg fails.

    Unlike a best-effort helper that returns ``None`` and lets the caller hand
    the original video to the transcriber, this raises: silently falling back
    would upload the full video and quietly undo the entire point of the phase,
    turning a cheap loud failure into an expensive quiet one.

    Set ``overwrite=False`` (the default) to reuse an artifact a previous run
    already produced, which makes phase 1 resumable.
    """
    spec = spec or AudioSpec()
    if not os.path.isfile(source_path):
        raise IngestError(f"source file not found: {source_path}")

    os.makedirs(output_dir, exist_ok=True)
    destination = os.path.join(output_dir, asr_output_name(source_path, spec))

    if not overwrite and os.path.isfile(destination) and os.path.getsize(destination) > 0:
        return destination

    # Fail before spending minutes on a decode that cannot produce audio.
    if probe_audio(source_path) is None:
        raise IngestError(
            f"{source_path} has no audio stream — nothing to transcribe. "
            f"A silent screen recording cannot drive this pipeline."
        )

    cmd = build_extract_command(source_path, destination, spec)
    _run(cmd, timeout=timeout)

    if not os.path.isfile(destination) or os.path.getsize(destination) == 0:
        raise ToolFailureError(
            f"ffmpeg reported success but produced no audio at {destination}"
        )

    if verify:
        problems = verify_extraction(probe_audio(destination), spec)
        if problems:
            raise ToolFailureError(
                "extracted audio does not match the requested spec: "
                + "; ".join(problems)
            )

    write_sidecar(
        destination,
        command="ingest.extract_analysis_audio",
        inputs=[source_path],
        parameters={
            "sample_rate": spec.sample_rate,
            "channels": spec.channels,
            "codec": spec.codec,
            "compression_level": spec.compression_level,
        },
        tool_commands=[cmd],
    )
    return destination


def describe_saving(source_path: str, audio_path: str) -> str:
    """One-line "N MB audio from M MB source" summary, safe on missing files."""
    def _size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    return format_size_saving(_size(source_path), _size(audio_path))


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sidecar(
    output: str,
    *,
    command: str,
    inputs: list[str],
    parameters: dict[str, Any],
    tool_commands: list[list[str]] | None = None,
) -> str:
    """Write ``<output>.provenance.json`` recording how ``output`` was produced.

    Mirrors the video-edit-cli sidecar contract so a clipper-pro workspace can
    be handed to that CLI (and to an agent reading its plans) without a
    translation step. Source media is never modified; this is the only record
    of the transformation, so it carries hashes of both ends plus the exact
    argv, making any artifact reproducible from the sidecar alone.
    """
    record = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "created_at": datetime.now(UTC).isoformat(),
        "inputs": [
            {"path": p, "sha256": _sha256_file(p)} for p in inputs if os.path.isfile(p)
        ],
        "parameters": parameters,
        "tool_commands": tool_commands or [],
        "output": {"path": output, "sha256": _sha256_file(output)},
    }
    sidecar = output + ".provenance.json"
    # Atomic: a crash mid-write must not leave a half-parsed sidecar that makes
    # a good artifact look corrupt.
    tmp = sidecar + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, sidecar)
    return sidecar
