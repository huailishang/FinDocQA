from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class FreetokenModelProfile:
    name: str
    model: str
    token_budget: int
    usage_file: str


DEFAULT_PLUS_MODEL = "qwen3.7-plus"
DEFAULT_MAX_MODEL = "qwen3.7-max"


def resolve_freetoken_profile(name: str, *, usage_dir: Path | None = None) -> FreetokenModelProfile:
    """Resolve explicit Plus/Max cost profiles for the shared FREETOKEN route.

    Endpoint credentials stay in the existing project environment. This helper
    only separates model identity, token budget, and usage ledger.
    """
    normalized = str(name or "plus").strip().lower()
    usage_dir = usage_dir or Path("output")
    if normalized == "plus":
        model = os.getenv("FREETOKEN_PLUS_MODEL", DEFAULT_PLUS_MODEL)
        budget = int(os.getenv("FREETOKEN_PLUS_TOKEN_BUDGET", os.getenv("FREETOKEN_TOKEN_BUDGET", "2000000")))
        usage = os.getenv("FREETOKEN_PLUS_USAGE_FILE", os.getenv("FREETOKEN_USAGE_FILE", str(usage_dir / "freetoken_qwen37_plus_usage.json")))
        return FreetokenModelProfile("plus", model, budget, usage)
    if normalized == "max":
        model = os.getenv("FREETOKEN_MAX_MODEL", DEFAULT_MAX_MODEL)
        budget = int(os.getenv("FREETOKEN_MAX_TOKEN_BUDGET", "300000"))
        usage = os.getenv("FREETOKEN_MAX_USAGE_FILE", str(usage_dir / "freetoken_qwen37_max_usage.json"))
        return FreetokenModelProfile("max", model, budget, usage)
    raise ValueError(f"unknown FREETOKEN profile: {name!r}; expected plus or max")


def profile_env_overrides(profile: FreetokenModelProfile) -> dict[str, str]:
    """Return process-local overrides consumed by the existing LLM client."""
    return {
        "FREETOKEN_MODEL": profile.model,
        "FREETOKEN_TOKEN_BUDGET": str(profile.token_budget),
        "FREETOKEN_USAGE_FILE": profile.usage_file,
    }
