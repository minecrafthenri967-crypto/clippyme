"""Provider-independent ranking contract.

The ranker is an interface, not a vendor. Every provider reduces to one
operation — take a prompt, return the model's text — because all of phase 3's
actual judgement (the rubric, the validation, the dedupe) lives in
:mod:`clipper_pro.rank.rubric_ops` and is shared. Adding a provider means
implementing ``complete`` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RANKERS", "RankerSpec", "ranker_spec"]


@dataclass(frozen=True)
class RankerSpec:
    """Identity and credentials of a ranking provider."""

    name: str
    api_key_env: str
    default_model: str
    model_env: str
    #: Human-readable note used in error messages when the key is missing.
    signup_hint: str


RANKERS: dict[str, RankerSpec] = {
    "deepseek": RankerSpec(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        model_env="DEEPSEEK_MODEL",
        signup_hint="https://platform.deepseek.com",
    ),
    "gemini": RankerSpec(
        name="gemini",
        # Reuses the host repository's existing credential, so a deployment that
        # already runs ClippyMe needs no second key to rank.
        api_key_env="GEMINI_API_KEY",
        default_model="gemini-3.5-flash",
        model_env="GEMINI_MODEL",
        signup_hint="https://aistudio.google.com/apikey",
    ),
}


def ranker_spec(name: str) -> RankerSpec:
    """Look up a ranking provider by name, raising for an unknown one."""
    from clipper_pro.errors import ValidationError  # local: avoids a cycle

    spec = RANKERS.get((name or "").strip().lower())
    if spec is None:
        raise ValidationError(
            f"unknown ranking provider {name!r} — "
            f"expected one of {', '.join(sorted(RANKERS))}"
        )
    return spec
