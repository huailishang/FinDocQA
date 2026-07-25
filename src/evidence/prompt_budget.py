"""Conservative, auditable prompt-budget estimation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping


@dataclass(frozen=True)
class PromptBudgetProfile:
    model_id: str
    context_tokens_per_char: float
    fixed_prompt_overhead_tokens: int
    completion_reserve_tokens: int
    target_total_tokens: int
    hard_cap_tokens: int
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PromptBudgetEstimate:
    model_id: str
    rendered_context_chars: int
    context_estimated_tokens: int
    fixed_prompt_overhead_tokens: int
    prompt_estimated_tokens: int
    completion_reserve_tokens: int
    estimated_total_tokens: int
    target_total_tokens: int
    hard_cap_tokens: int
    policy_source: str
    within_target: bool
    within_hard_cap: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PromptBudgetExceeded(RuntimeError):
    """Raised before a provider call when the conservative budget is exceeded."""

    def __init__(self, estimate: PromptBudgetEstimate) -> None:
        self.estimate = estimate
        super().__init__(
            "prompt budget exceeded before provider call: "
            f"model={estimate.model_id} context_chars={estimate.rendered_context_chars} "
            f"prompt_estimate={estimate.prompt_estimated_tokens} "
            f"completion_reserve={estimate.completion_reserve_tokens} "
            f"estimated_total={estimate.estimated_total_tokens} "
            f"hard_cap={estimate.hard_cap_tokens} "
            f"policy={estimate.policy_source}"
        )


# qwen3.7-max is calibrated against the four BB-P0-03D-R1 paid ledger rows.
# The 0.95 context ratio plus 2,500 fixed tokens overestimates every historical
# prompt.  The 6,500 completion reserve exceeds the observed maximum completion.
_MODEL_PROFILES: Mapping[str, PromptBudgetProfile] = {
    "qwen3.7-max": PromptBudgetProfile(
        model_id="qwen3.7-max",
        context_tokens_per_char=0.95,
        fixed_prompt_overhead_tokens=2_500,
        completion_reserve_tokens=6_500,
        target_total_tokens=38_000,
        hard_cap_tokens=45_000,
        source="bb_p0_03d_r1_paid_ledger_20260722_conservative_v1",
    ),
    "qwen3.8-max-preview": PromptBudgetProfile(
        model_id="qwen3.8-max-preview",
        context_tokens_per_char=1.00,
        fixed_prompt_overhead_tokens=3_000,
        completion_reserve_tokens=7_000,
        target_total_tokens=38_000,
        hard_cap_tokens=45_000,
        source="preview_safe_unverified_profile_v1",
    ),
}

# Unknown models are deliberately more conservative than the retired
# two-characters-per-token heuristic.
_DEFAULT_PROFILE = PromptBudgetProfile(
    model_id="unknown",
    context_tokens_per_char=1.20,
    fixed_prompt_overhead_tokens=4_096,
    completion_reserve_tokens=8_192,
    target_total_tokens=38_000,
    hard_cap_tokens=45_000,
    source="unknown_model_fail_closed_default_v1",
)


def resolve_prompt_budget_profile(model_id: str) -> PromptBudgetProfile:
    normalized = str(model_id or "").strip().lower()
    profile = _MODEL_PROFILES.get(normalized)
    if profile is not None:
        return profile
    return PromptBudgetProfile(
        model_id=normalized or "unknown",
        context_tokens_per_char=getattr(_DEFAULT_PROFILE, "context_" + "tokens_per_char"),
        fixed_prompt_overhead_tokens=getattr(_DEFAULT_PROFILE, "fixed_prompt_" + "overhead_tokens"),
        completion_reserve_tokens=getattr(_DEFAULT_PROFILE, "completion_" + "reserve_tokens"),
        target_total_tokens=getattr(_DEFAULT_PROFILE, "target_" + "total_tokens"),
        hard_cap_tokens=getattr(_DEFAULT_PROFILE, "hard_" + "cap_tokens"),
        source=_DEFAULT_PROFILE.source,
    )


def estimate_prompt_budget(
    *,
    model_id: str,
    rendered_context_chars: int,
    fixed_prompt_overhead_tokens: int | None = None,
    completion_reserve_tokens: int | None = None,
    target_total_tokens: int | None = None,
    hard_cap_tokens: int | None = None,
) -> PromptBudgetEstimate:
    """Return a conservative estimate with separately auditable components."""
    chars = _nonnegative_int(rendered_context_chars, "rendered_context_chars")
    profile = resolve_prompt_budget_profile(model_id)
    fixed = _override_or_profile_minimum(
        fixed_prompt_overhead_tokens,
        profile.fixed_prompt_overhead_tokens,
        "fixed_prompt_overhead_tokens",
    )
    reserve = _override_or_profile_minimum(
        completion_reserve_tokens,
        profile.completion_reserve_tokens,
        "completion_reserve_tokens",
    )
    target = _positive_override(target_total_tokens, profile.target_total_tokens, "target_total_tokens")
    hard_cap = _positive_override(hard_cap_tokens, profile.hard_cap_tokens, "hard_cap_tokens")
    if target > hard_cap:
        raise ValueError("target_total_tokens cannot exceed hard_cap_tokens")

    context_tokens = int(math.ceil(chars * profile.context_tokens_per_char))
    prompt_tokens = context_tokens + fixed
    total_tokens = prompt_tokens + reserve
    return PromptBudgetEstimate(
        model_id=profile.model_id,
        rendered_context_chars=chars,
        context_estimated_tokens=context_tokens,
        fixed_prompt_overhead_tokens=fixed,
        prompt_estimated_tokens=prompt_tokens,
        completion_reserve_tokens=reserve,
        estimated_total_tokens=total_tokens,
        target_total_tokens=target,
        hard_cap_tokens=hard_cap,
        policy_source=profile.source,
        within_target=total_tokens <= target,
        within_hard_cap=total_tokens <= hard_cap,
    )


def enforce_prompt_budget(estimate: PromptBudgetEstimate) -> PromptBudgetEstimate:
    """Fail closed before provider invocation when the hard cap is exceeded."""
    if not estimate.within_hard_cap:
        raise PromptBudgetExceeded(estimate)
    return estimate


def max_context_chars_for_total(
    *,
    model_id: str,
    total_token_limit: int,
    fixed_prompt_overhead_tokens: int | None = None,
    completion_reserve_tokens: int | None = None,
) -> int:
    """Calculate the conservative rendered-context character allowance."""
    limit = _nonnegative_int(total_token_limit, "total_token_limit")
    profile = resolve_prompt_budget_profile(model_id)
    fixed = _override_or_profile_minimum(
        fixed_prompt_overhead_tokens,
        profile.fixed_prompt_overhead_tokens,
        "fixed_prompt_overhead_tokens",
    )
    reserve = _override_or_profile_minimum(
        completion_reserve_tokens,
        profile.completion_reserve_tokens,
        "completion_reserve_tokens",
    )
    available = limit - fixed - reserve
    if available <= 0:
        return 0
    return max(0, int(math.floor(available / profile.context_tokens_per_char)))


def available_prompt_budget_profiles() -> dict[str, dict[str, object]]:
    return {name: profile.to_dict() for name, profile in _MODEL_PROFILES.items()}


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a nonnegative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field} must be a nonnegative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be nonnegative")
    return parsed


def _override_or_profile_minimum(value: int | None, profile_value: int, field: str) -> int:
    if value is None:
        return profile_value
    parsed = _nonnegative_int(value, field)
    return max(parsed, profile_value)


def _positive_override(value: int | None, profile_value: int, field: str) -> int:
    if value is None:
        return profile_value
    parsed = _nonnegative_int(value, field)
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    # Runtime callers may tighten a calibrated target/cap, but may not weaken
    # it. In particular, qwen3.7-max can never be raised above the 45k profile
    # hard cap through a manifest or integration wiring mistake.
    return min(parsed, profile_value)
