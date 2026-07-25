"""Bounded, shadow-only recovery planning for BB-P0-14C.

P14A decides the primary failure.  This module translates that primary failure
into one bounded recovery recommendation while preserving secondary failures
for audit.  It never executes retrieval, rebind, re-verification, recompute,
provider retry, answer mutation, or any other recovery side effect.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from verification.composite_failure_arbitrator import CompositeFailureDecision
from verification.failure_taxonomy import FailureClass, FailureRecord
from verification.recovery_policy import RecoveryAction, route_recovery


_TERMINAL_FAILURES: frozenset[FailureClass] = frozenset({
    FailureClass.PROVIDER_ERROR,
    FailureClass.ANSWER_CONTRACT_FAILED,
    FailureClass.BUDGET_BLOCKED,
    FailureClass.RUNTIME_INTEGRITY_FAILED,
    FailureClass.UNKNOWN_FAILURE,
})

_MAX_STEPS_BY_FAILURE: dict[FailureClass, int] = {
    FailureClass.MISSING_EVIDENCE: 1,
    FailureClass.LINEAGE_LOST: 1,
    FailureClass.BINDING_FAILED: 1,
    FailureClass.CALCULATION_BINDING_FAILED: 1,
    FailureClass.MODEL_OUTPUT_INVALID: 1,
    FailureClass.EMPTY_VISIBLE_OUTPUT: 1,
    FailureClass.PROVIDER_ERROR: 0,
    FailureClass.ANSWER_CONTRACT_FAILED: 0,
    FailureClass.BUDGET_BLOCKED: 0,
    FailureClass.RUNTIME_INTEGRITY_FAILED: 0,
    FailureClass.UNKNOWN_FAILURE: 0,
}


@dataclass(frozen=True)
class BoundedRecoveryPlan:
    primary_failure: FailureRecord
    secondary_failures: tuple[FailureRecord, ...]
    recommended_action: RecoveryAction
    max_recovery_steps: int
    requires_evaluator: bool
    provider_retry_allowed: bool
    corrective_retrieval_allowed: bool
    execution_authorized: bool
    terminal_stop: bool
    stop_reason: str
    plan_reason: str
    provider_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["primary_failure"] = self.primary_failure.to_dict()
        payload["secondary_failures"] = [item.to_dict() for item in self.secondary_failures]
        payload["recommended_action"] = self.recommended_action.value
        return payload


def _normalize_inputs(
    decision: CompositeFailureDecision | None,
    primary_failure: FailureRecord | None,
    secondary_failures: Sequence[FailureRecord],
) -> tuple[FailureRecord, tuple[FailureRecord, ...], bool, bool]:
    if decision is not None and primary_failure is not None:
        raise ValueError("supply either CompositeFailureDecision or primary_failure, not both")

    if decision is not None:
        return (
            decision.primary_failure,
            tuple(decision.secondary_failures),
            bool(decision.terminal_stop),
            bool(decision.evaluator_escalation_required),
        )

    if primary_failure is None:
        raise ValueError("primary_failure is required when CompositeFailureDecision is not supplied")

    return primary_failure, tuple(secondary_failures), False, False


def build_bounded_recovery_plan(
    decision: CompositeFailureDecision | None = None,
    *,
    primary_failure: FailureRecord | None = None,
    secondary_failures: Sequence[FailureRecord] = (),
    context: Mapping[str, Any] | None = None,
) -> BoundedRecoveryPlan:
    """Build one deterministic, primary-driven recovery plan.

    ``context`` is accepted for the stable P14C contract but cannot authorize
    side effects or override the P14A primary failure.  Recovery action is
    derived exclusively from ``primary_failure``.
    """
    del context  # Explicitly non-authoritative in P14C.

    primary, secondaries, composite_terminal, composite_escalation = _normalize_inputs(
        decision,
        primary_failure,
        secondary_failures,
    )
    failure_class = primary.failure_class
    shadow_decision = route_recovery(primary)

    max_steps = _MAX_STEPS_BY_FAILURE[failure_class]
    terminal_stop = bool(composite_terminal or failure_class in _TERMINAL_FAILURES)
    corrective_retrieval_allowed = (
        failure_class is FailureClass.MISSING_EVIDENCE
        and shadow_decision.action is RecoveryAction.CORRECTIVE_RETRIEVAL
        and not terminal_stop
    )
    requires_evaluator = bool(
        composite_escalation
        or shadow_decision.evaluator_review_required
        or failure_class in {
            FailureClass.MODEL_OUTPUT_INVALID,
            FailureClass.EMPTY_VISIBLE_OUTPUT,
            FailureClass.UNKNOWN_FAILURE,
        }
    )

    if terminal_stop:
        stop_reason = (
            f"primary failure {failure_class.value} requires fail-closed stop; "
            "secondary failures cannot authorize alternate recovery"
        )
    else:
        stop_reason = ""

    secondary_names = ",".join(item.failure_class.value for item in secondaries) or "NONE"
    plan_reason = (
        f"primary-driven plan: {failure_class.value} -> {shadow_decision.action.value}; "
        f"secondary failures preserved for trace only: {secondary_names}; "
        "execution remains unauthorized"
    )

    return BoundedRecoveryPlan(
        primary_failure=primary,
        secondary_failures=secondaries,
        recommended_action=shadow_decision.action,
        max_recovery_steps=max_steps,
        requires_evaluator=requires_evaluator,
        provider_retry_allowed=False,
        corrective_retrieval_allowed=corrective_retrieval_allowed,
        execution_authorized=False,
        terminal_stop=terminal_stop,
        stop_reason=stop_reason,
        plan_reason=plan_reason,
        provider_calls=0,
    )


__all__ = [
    "BoundedRecoveryPlan",
    "build_bounded_recovery_plan",
]
