"""Adapters over the host repository's transcription backends.

These are deliberately thin. ``clippyme.pipeline.deepgram_transcribe`` and
``elevenlabs_transcribe`` already carry retry/backoff on the retryable status
codes, file-size guards against the provider caps, content-type detection,
pooled sessions and diarization handling — several hundred lines each, hardened
against the ways a paid hour-long upload actually fails. Writing fresh HTTP
clients here would mean re-earning all of that, so instead we call theirs and
normalise the result.

Both modules import ``requests`` at their top, so the imports are deferred into
the call: it keeps ``clipper_pro.transcribe`` importable — and its pure
normalisation tested — on a host without those wheels.
"""

from __future__ import annotations

import os

from clipper_pro.errors import ConfigError, ToolFailureError
from clipper_pro.transcribe.base import ProviderSpec, provider_spec

__all__ = ["effective_model", "require_api_key", "transcribe_raw"]

#: Env var each provider reads for its model, mirroring the host modules.
_MODEL_ENV = {"deepgram": "DEEPGRAM_MODEL", "elevenlabs": "ELEVENLABS_MODEL"}


def effective_model(spec: ProviderSpec) -> str:
    """The model the provider will actually use, for logging and the cache key.

    Resolved the same way the underlying module resolves it, so a cache entry
    can never be attributed to the wrong model.
    """
    env = _MODEL_ENV.get(spec.name)
    raw = (os.getenv(env) or "").strip() if env else ""
    return raw or spec.default_model


def require_api_key(spec: ProviderSpec) -> str:
    """Return the provider's credential or raise a ConfigError naming the var."""
    key = (os.getenv(spec.api_key_env) or "").strip()
    if not key:
        raise ConfigError(
            f"{spec.api_key_env} is not set — phase 2 needs a {spec.name} "
            f"credential (or select another provider with CLIPPER_PRO_TRANSCRIBER)"
        )
    return key


def transcribe_raw(audio_path: str, provider: str) -> dict:
    """Transcribe ``audio_path`` and return the host repository's transcript dict.

    Raises :class:`ConfigError` when the credential is missing and
    :class:`ToolFailureError` when the provider itself fails, so callers only
    ever handle the clipper-pro hierarchy.
    """
    spec = provider_spec(provider)
    require_api_key(spec)

    if spec.name == "deepgram":
        try:
            from clippyme.pipeline.deepgram_transcribe import (
                DeepgramError,
                transcribe_with_deepgram,
            )
        except ImportError as exc:  # pragma: no cover - needs requests absent
            raise ToolFailureError(
                "the deepgram backend needs the 'requests' runtime "
                "(pip install -e '.[host-tests]')"
            ) from exc
        try:
            return transcribe_with_deepgram(audio_path)
        except DeepgramError as exc:
            raise ToolFailureError(f"deepgram transcription failed: {exc}") from exc

    try:
        from clippyme.pipeline.elevenlabs_transcribe import (
            ElevenLabsError,
            transcribe_with_elevenlabs,
        )
    except ImportError as exc:  # pragma: no cover - needs requests absent
        raise ToolFailureError(
            "the elevenlabs backend needs the 'requests' runtime "
            "(pip install -e '.[host-tests]')"
        ) from exc
    try:
        return transcribe_with_elevenlabs(audio_path)
    except ElevenLabsError as exc:
        raise ToolFailureError(f"elevenlabs transcription failed: {exc}") from exc
