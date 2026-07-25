"""Side-effect-free recovery policy for BB-P0-12 failure records.

The router returns a recommendation only.  It is intentionally incapable of
performing retrieval, provider retry, deterministic calculation, or answer
mutation so integration review can inspect the decision contract first.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

from verification.failure_taxonomy import FailureClass, FailureRecord, classify_failure_signal


class RecoveryAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    CORRECTIVE_RETRIEVAL = "CORRECTIVE_RETRIEVAL"
    REBIND_LINEAGE = "REBIND_LINEAGE"
    REVERIFY_CLAIM = "REVERIFY_CLAIM"
    RECOMPUTE_DETERMINISTIC = "RECOMPUTE_DETERMINISTIC"
    REPARSE_VISIBLE_OUTPUT = "REPARSE_VISIBLE_OUTPUT"
    STOP_PROVIDER_ERROR = "STOP_PROVIDER_ERROR"
    STOP_CONTRACT_FAILURE = "STOP_CONTRACT_FAILURE"
    STOP_BUDGET = "STOP_BUDGET"
    STOP_INTEGRITY = "STOP_INTEGRITY"
    ESCALATE_EVALUATOR = "ESCALATE_EVALUATOR"


_ACTION_BY_FAILURE: dict[FailureClass, RecoveryAction] = {
    FailureClass.MISSING_EVIDENCE: RecoveryAction.CORRECTIVE_RETRIEVAL,
    FailureClass.LINEAGE_LOST: RecoveryAction.REBIND_LINEAGE,
    FailureClass.BINDING_FAILED: RecoveryAction.REVERIFY_CLAIM,
    FailureClass.CALCULATION_BINDING_FAILED: RecoveryAction.RECOMPUTE_DETERMINISTIC,
    FailureClass.MODEL_OUTPUT_INVALID: RecoveryAction.REPARSE_VISIBLE_OUTPUT,
    FailureClass.EMPTY_VISIBLE_OUTPUT: RecoveryAction.REPARSE_VISIBLE_OUTPUT,
    FailureClass.PROVIDER_ERROR: RecoveryAction.STOP_PROVIDER_ERROR,
    FailureClass.ANSWER_CONTRACT_FAILED: RecoveryAction.STOP_CONTRACT_FAILURE,
    FailureClass.BUDGET_BLOCKED: RecoveryAction.STOP_BUDGET,
    FailureClass.RUNTIME_INTEGRITY_FAILED: RecoveryAction.STOP_INTEGRITY,
    FailureClass.UNKNOWN_FAILURE: RecoveryAction.ESCALATE_EVALUATOR,
}


@dataclass(frozen=True)
class RecoveryDecision:
    failure_class: FailureClass | None
    action: RecoveryAction
    fail_closed: bool
    provider_retry_allowed: bool
    corrective_retrieval_allowed: bool
    execution_authorized: bool
    evaluator_review_required: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failure_class"] = self.failure_class.value if self.failure_class is not None else None
        payload["action"] = self.action.value
        return payload


def route_recovery(failure: FailureRecord | FailureClass | str | None) -> RecoveryDecision:
    """Return the deterministic shadow action for one classified failure."""
    if failure is None:
        return RecoveryDecision(
            failure_class=None,
            action=RecoveryAction.NO_ACTION,
            fail_closed=False,
            provider_retry_allowed=False,
            corrective_retrieval_allowed=False,
            execution_authorized=False,
            evaluator_review_required=False,
            reason="no failure supplied; shadow router recommends no action",
        )

    if isinstance(failure, FailureRecord):
        failure_class = failure.failure_class
        reason = failure.reason
    elif isinstance(failure, FailureClass):
        failure_class = failure
        reason = f"classified as {failure_class.value}"
    else:
        try:
            failure_class = FailureClass(str(failure))
        except ValueError:
            failure_class = FailureClass.UNKNOWN_FAILURE
        reason = f"classified as {failure_class.value}"

    action = _ACTION_BY_FAILURE[failure_class]
    fail_closed = action in {
        RecoveryAction.STOP_PROVIDER_ERROR,
        RecoveryAction.STOP_CONTRACT_FAILURE,
        RecoveryAction.STOP_BUDGET,
        RecoveryAction.STOP_INTEGRITY,
        RecoveryAction.ESCALATE_EVALUATOR,
    }
    evaluator_review_required = fail_closed or failure_class in {
        FailureClass.LINEAGE_LOST,
        FailureClass.BINDING_FAILED,
        FailureClass.CALCULATION_BINDING_FAILED,
        FailureClass.EMPTY_VISIBLE_OUTPUT,
        FailureClass.MODEL_OUTPUT_INVALID,
    }
    return RecoveryDecision(
        failure_class=failure_class,
        action=action,
        fail_closed=fail_closed,
        provider_retry_allowed=False,
        corrective_retrieval_allowed=action is RecoveryAction.CORRECTIVE_RETRIEVAL,
        execution_authorized=False,
        evaluator_review_required=evaluator_review_required,
        reason=reason,
    )


def classify_and_route(signal: Mapping[str, Any]) -> tuple[FailureRecord, RecoveryDecision]:
    failure = classify_failure_signal(signal)
    return failure, route_recovery(failure)


def policy_matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for failure_class in FailureClass:
        decision = route_recovery(failure_class)
        rows.append(decision.to_dict())
    return rows


__all__ = [
    "RecoveryAction",
    "RecoveryDecision",
    "route_recovery",
    "classify_and_route",
    "policy_matrix",
]
