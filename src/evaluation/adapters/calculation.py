"""C3 calculation/recovery adapters into the generic Evaluation Core."""
from __future__ import annotations

from calculation.recovery import FormulaRecoveryResult
from evaluation.c3b_profile import C3B_MODULE_ID
from evaluation.contracts import EvaluationResult, MetricResult
from evaluation.oracles.c3b import C3BSafetyExpectation, evaluate_c3b_invariants


def adapt_c3b_recovery_result(
    *,
    case_id: str,
    result: FormulaRecoveryResult,
    baseline_result: FormulaRecoveryResult | None = None,
    expectation: C3BSafetyExpectation | None = None,
) -> EvaluationResult:
    """Interpret an immutable C3-B result without changing business behavior."""

    invariants = evaluate_c3b_invariants(
        result,
        baseline_result=baseline_result,
        expectation=expectation,
    )
    metrics = tuple(
        MetricResult.invariant(
            invariant_name,
            passed=passed,
            details={
                "recovery_status": result.status.value,
                "ready_for_execution": result.ready_for_execution,
            },
        )
        for invariant_name, passed in invariants.items()
    )
    return EvaluationResult(
        case_id=case_id,
        module_id=C3B_MODULE_ID,
        metrics=metrics,
        violations=tuple(name for name, passed in invariants.items() if not passed),
        diagnostics={
            "business_result_type": type(result).__name__,
            "recovery_status": result.status.value,
            "ready_for_execution": result.ready_for_execution,
            "reasons": list(result.reasons),
            "baseline_compared": baseline_result is not None,
            "independent_expectation": expectation is not None,
        },
    )
