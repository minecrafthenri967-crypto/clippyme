"""Ranking providers: prompt in, model text out.

Two implementations behind one function. Both defer their heavy imports into the
call so ``clipper_pro.rank`` stays importable — and its pure logic testable — on
a host without ``requests`` or ``google-genai``.

DeepSeek speaks the OpenAI chat-completions shape, so it is a small direct HTTP
client. Gemini goes through the host repository's ``generate_with_model_fallback``,
which already handles per-model retry, rate-limit classification and backoff.
"""

from __future__ import annotations

import os
import time

from clipper_pro.errors import ConfigError, ToolFailureError
from clipper_pro.rank.base import RankerSpec, ranker_spec

__all__ = ["complete", "effective_model", "require_api_key"]

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_TIMEOUT = 300.0
DEFAULT_MAX_RETRIES = 3

#: Transient upstream conditions worth a second attempt. 429 included because a
#: per-minute quota clears on its own; 4xx auth/validation errors are not here
#: because retrying them just burns the same failure three times.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def effective_model(spec: RankerSpec) -> str:
    """The model that will actually be used, for logging and the cache key."""
    raw = (os.getenv(spec.model_env) or "").strip()
    return raw or spec.default_model


def require_api_key(spec: RankerSpec) -> str:
    """Return the provider's credential or raise a ConfigError naming the var."""
    key = (os.getenv(spec.api_key_env) or "").strip()
    if not key:
        raise ConfigError(
            f"{spec.api_key_env} is not set — phase 3 needs a {spec.name} "
            f"credential ({spec.signup_hint}), or select another provider "
            f"with CLIPPER_PRO_RANKER"
        )
    return key


def complete(prompt: str, provider: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Send ``prompt`` to ``provider`` and return the model's raw text."""
    spec = ranker_spec(provider)
    api_key = require_api_key(spec)
    model = effective_model(spec)

    if spec.name == "deepseek":
        return _complete_deepseek(prompt, api_key, model, timeout)
    return _complete_gemini(prompt, api_key, model)


def _complete_deepseek(prompt: str, api_key: str, model: str, timeout: float) -> str:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - needs requests absent
        raise ToolFailureError(
            "the deepseek ranker needs the 'requests' runtime "
            "(pip install -e '.[host-tests]')"
        ) from exc

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        # Low but non-zero: ranking is a judgement call, and greedy decoding on
        # a long rubric tends to collapse onto the first plausible clip set.
        "temperature": 0.3,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = ""
    for attempt in range(DEFAULT_MAX_RETRIES):
        try:
            response = requests.post(
                DEEPSEEK_API_URL, json=body, headers=headers, timeout=timeout
            )
        except requests.RequestException as exc:
            last_error = f"request failed: {exc}"
        else:
            if response.status_code == 200:
                return _deepseek_text(response)
            # The body carries the provider's own explanation; keep it verbatim
            # so an auth or quota problem is diagnosable from the log alone.
            detail = (response.text or "")[:400]
            last_error = f"HTTP {response.status_code}: {detail}"
            if response.status_code not in _RETRYABLE_STATUS:
                break

        if attempt < DEFAULT_MAX_RETRIES - 1:
            time.sleep(2**attempt)

    raise ToolFailureError(f"deepseek ranking failed after retries — {last_error}")


def _deepseek_text(response: object) -> str:
    """Pull the assistant message out of an OpenAI-shaped response."""
    try:
        payload = response.json()  # type: ignore[attr-defined]
        text = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ToolFailureError(f"deepseek returned an unexpected body: {exc}") from exc
    if not isinstance(text, str) or not text.strip():
        raise ToolFailureError("deepseek returned an empty completion")
    return text


def _complete_gemini(prompt: str, api_key: str, model: str) -> str:
    try:
        from google import genai

        from clippyme.pipeline.gemini_request import (
            build_model_chain,
            generate_with_model_fallback,
        )
    except ImportError as exc:  # pragma: no cover - needs google-genai absent
        raise ToolFailureError(
            "the gemini ranker needs the 'google-genai' runtime "
            "(pip install -e '.[host-tests]')"
        ) from exc

    client = genai.Client(api_key=api_key)
    try:
        response, _used_model = generate_with_model_fallback(
            client, prompt, build_model_chain(model)
        )
    except Exception as exc:
        raise ToolFailureError(f"gemini ranking failed: {exc}") from exc

    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise ToolFailureError("gemini returned an empty completion")
    return text
