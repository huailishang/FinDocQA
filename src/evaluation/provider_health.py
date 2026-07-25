"""Run-level Provider health signals and fail-closed circuit breaker.

This module intentionally observes only transport/runtime/visible-output health.
Answer correctness and semantic contract failures never trip the Provider breaker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


HEALTHY_COMPLETED = "healthy_completed"
SINGLE_QID_SEMANTIC_INVALID = "single_qid_semantic_invalid"
SINGLE_PROVIDER_ERROR = "single_provider_error"
EMPTY_VISIBLE_OUTPUT_STREAK = "empty_visible_output_streak"
PROVIDER_DEGRADATION_STREAK = "provider_degradation_streak"
QUOTA_EXHAUSTION = "quota_exhaustion"


@dataclass(frozen=True)
class ProviderHealthDecision:
    qid: str
    signal: str
    stop_run: bool
    stop_reason: str | None
    provider_degradation_streak: int
    empty_visible_output_streak: int
    provider_error_streak: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _usage(result: Mapping[str, Any]) -> Mapping[str, Any]:
    value = result.get("usage")
    return value if isinstance(value, Mapping) else {}


def is_quota_or_budget_exhaustion(result: Mapping[str, Any]) -> bool:
    error = _text(result.get("error")).lower()
    failure = _text(result.get("failure_class")).lower()
    markers = (
        "llmproviderbudgetexhausted",
        "quota_exhaust",
        "quota exhausted",
        "budget_exhaust",
        "budget exhausted",
        "token_budget_blocked",
    )
    return any(marker in error or marker in failure for marker in markers)


def is_empty_visible_output(result: Mapping[str, Any]) -> bool:
    provider_status = _text(result.get("provider_status")).upper()
    visible = _text(result.get("visible_content"))
    completion = int(_usage(result).get("completion_tokens", 0) or 0)
    failure = _text(result.get("failure_class")).upper()
    return bool(
        provider_status == "COMPLETED"
        and not visible
        and completion == 0
        and failure == "EMPTY_VISIBLE_OUTPUT"
    )


def is_provider_error(result: Mapping[str, Any]) -> bool:
    if is_quota_or_budget_exhaustion(result):
        return False
    provider_status = _text(result.get("provider_status")).upper()
    failure = _text(result.get("failure_class")).upper()
    return provider_status not in {"", "COMPLETED"} or failure == "PROVIDER_ERROR"


def visible_output_healthy(result: Mapping[str, Any]) -> bool:
    usage = _usage(result)
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or 0)
    return bool(
        _text(result.get("provider_status")).upper() == "COMPLETED"
        and _text(result.get("visible_content"))
        and completion > 0
        and prompt + completion == total
        and not is_quota_or_budget_exhaustion(result)
    )


class ProviderHealthGate:
    """Stateful run-level breaker with no retry/fallback behavior."""

    def __init__(self, *, empty_threshold: int = 2, provider_error_threshold: int = 2) -> None:
        if empty_threshold < 1 or provider_error_threshold < 1:
            raise ValueError("provider health thresholds must be positive")
        self.empty_threshold = int(empty_threshold)
        self.provider_error_threshold = int(provider_error_threshold)
        self.empty_visible_output_streak = 0
        self.provider_error_streak = 0
        self.provider_degradation_streak = 0
        self.stopped = False
        self.stop_reason: str | None = None

    def observe(self, result: Mapping[str, Any]) -> ProviderHealthDecision:
        qid = _text(result.get("qid"))
        if self.stopped:
            return ProviderHealthDecision(
                qid=qid,
                signal=PROVIDER_DEGRADATION_STREAK,
                stop_run=True,
                stop_reason=self.stop_reason,
                provider_degradation_streak=self.provider_degradation_streak,
                empty_visible_output_streak=self.empty_visible_output_streak,
                provider_error_streak=self.provider_error_streak,
            )

        if is_quota_or_budget_exhaustion(result):
            self.provider_degradation_streak += 1
            self.empty_visible_output_streak = 0
            self.provider_error_streak = 0
            self.stopped = True
            self.stop_reason = QUOTA_EXHAUSTION
            signal = QUOTA_EXHAUSTION
        elif is_empty_visible_output(result):
            self.empty_visible_output_streak += 1
            self.provider_error_streak = 0
            self.provider_degradation_streak += 1
            signal = EMPTY_VISIBLE_OUTPUT_STREAK
            if self.empty_visible_output_streak >= self.empty_threshold:
                self.stopped = True
                self.stop_reason = EMPTY_VISIBLE_OUTPUT_STREAK
        elif is_provider_error(result):
            self.provider_error_streak += 1
            self.empty_visible_output_streak = 0
            self.provider_degradation_streak += 1
            signal = SINGLE_PROVIDER_ERROR
            if self.provider_error_streak >= self.provider_error_threshold:
                self.stopped = True
                self.stop_reason = "provider_error_streak"
        else:
            # A semantically invalid answer with healthy visible Provider output is
            # not Provider degradation and must not trigger the run breaker.
            signal = HEALTHY_COMPLETED if not result.get("failure_class") else SINGLE_QID_SEMANTIC_INVALID
            self.empty_visible_output_streak = 0
            self.provider_error_streak = 0
            self.provider_degradation_streak = 0

        return ProviderHealthDecision(
            qid=qid,
            signal=signal,
            stop_run=self.stopped,
            stop_reason=self.stop_reason,
            provider_degradation_streak=self.provider_degradation_streak,
            empty_visible_output_streak=self.empty_visible_output_streak,
            provider_error_streak=self.provider_error_streak,
        )
