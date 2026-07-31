import pytest

from evaluation.contracts import (
    EvaluationCase,
    EvaluationObservation,
    EvaluationResult,
    GateStatus,
    MetricKind,
    MetricResult,
)
from contracts import EvidenceCandidate
from evaluation.adapters.layered import adapt_layer_result
from evaluation.gates import ReliabilityGate
from evaluation.layers import RetrievalGold, evaluate_retrieval
from evaluation.profiles import ReliabilityProfile


def test_evaluation_case_and_observation_are_module_agnostic_and_serializable() -> None:
    case = EvaluationCase(
        case_id="case-1",
        module_id="retrieval",
        input={"query": "营业收入"},
        expected={"doc_ids": ["d1"]},
        oracle_ref="gold://retrieval/case-1",
        tags=("gold",),
        risk_tags=("miss",),
        slice="financial_reports",
        provenance={"source": "local_gold"},
    )
    observation = EvaluationObservation(
        module_id="retrieval",
        case_id="case-1",
        output={"doc_ids": ["d1"]},
        status="COMPLETED",
        trace=("retrieve", "rank"),
        lineage={"corpus": "canonical-v1"},
        latency_ms=12.5,
        token_usage={"total_tokens": 0},
        cost=0.0,
        runtime={"mode": "offline"},
    )

    assert case.to_dict()["module_id"] == "retrieval"
    assert case.to_dict()["tags"] == ["gold"]
    assert observation.to_dict()["runtime"] == {"mode": "offline"}
    assert observation.to_dict()["latency_ms"] == 12.5


def test_metric_result_threshold_supports_pass_and_fail() -> None:
    passed = MetricResult.threshold_metric(
        "recall_at_10",
        value=0.92,
        threshold=0.90,
    )
    failed = MetricResult.threshold_metric(
        "recall_at_10",
        value=0.82,
        threshold=0.90,
    )

    assert passed.passed is True
    assert failed.passed is False
    assert passed.to_dict()["comparison"] == ">="


def test_critical_invariant_violation_forces_gate_fail() -> None:
    profile = ReliabilityProfile(
        module_id="formula_recovery",
        risk_level="HIGH",
        failure_modes=("ambiguous_formula_accepted",),
        required_invariants=("ambiguity_must_not_execute",),
        test_techniques=("decision_table", "mutation"),
    )
    result = EvaluationResult(
        case_id="ambiguous-1",
        module_id="formula_recovery",
        metrics=(
            MetricResult.invariant(
                "ambiguity_must_not_execute",
                passed=False,
            ),
        ),
        violations=("ambiguity_must_not_execute",),
    )

    decision = ReliabilityGate().evaluate([result], profile)

    assert decision.status is GateStatus.FAIL
    assert "critical_invariant_failed:ambiguity_must_not_execute" in decision.reasons


def test_non_critical_metric_failure_can_be_review() -> None:
    profile = ReliabilityProfile(
        module_id="retrieval",
        risk_level="MEDIUM",
        required_metrics=("recall_at_10",),
        test_techniques=("gold", "hard_negative"),
        gate_policy={"metric_failure": "REVIEW"},
    )
    result = EvaluationResult(
        case_id="retrieval-1",
        module_id="retrieval",
        metrics=(
            MetricResult.threshold_metric(
                "recall_at_10",
                value=0.82,
                threshold=0.90,
            ),
        ),
    )

    decision = ReliabilityGate().evaluate([result], profile)

    assert decision.status is GateStatus.REVIEW
    assert "metric_failed:recall_at_10" in decision.reasons


def test_all_required_checks_satisfied_gate_passes() -> None:
    profile = ReliabilityProfile(
        module_id="parser",
        risk_level="MEDIUM",
        required_metrics=("page_recall",),
        required_invariants=("lineage_preserved",),
        test_techniques=("gold", "metamorphic"),
    )
    result = EvaluationResult(
        case_id="parser-1",
        module_id="parser",
        metrics=(
            MetricResult.threshold_metric("page_recall", value=1.0, threshold=0.95),
            MetricResult.invariant("lineage_preserved", passed=True),
        ),
    )

    decision = ReliabilityGate().evaluate([result], profile)

    assert decision.status is GateStatus.PASS
    assert decision.reasons == ()


def test_profiles_can_choose_different_test_techniques_without_gate_branching() -> None:
    parser = ReliabilityProfile(
        module_id="parser",
        risk_level="MEDIUM",
        test_techniques=("gold", "metamorphic"),
    )
    retrieval = ReliabilityProfile(
        module_id="retrieval",
        risk_level="MEDIUM",
        test_techniques=("gold", "hard_negative"),
    )
    recovery = ReliabilityProfile(
        module_id="recovery",
        risk_level="HIGH",
        test_techniques=("stateful", "property_based"),
    )
    formula = ReliabilityProfile(
        module_id="formula_recovery",
        risk_level="HIGH",
        test_techniques=("decision_table", "combinatorial_2way", "mutation"),
    )

    assert parser.test_techniques != retrieval.test_techniques
    assert recovery.test_techniques != formula.test_techniques

    gate = ReliabilityGate()
    for profile in (parser, retrieval, recovery, formula):
        decision = gate.evaluate(
            [EvaluationResult(case_id=f"{profile.module_id}-1", module_id=profile.module_id)],
            profile,
        )
        assert decision.status is GateStatus.PASS


def test_missing_required_invariant_fails_closed() -> None:
    profile = ReliabilityProfile(
        module_id="verification",
        risk_level="HIGH",
        required_invariants=("false_accept_must_be_zero",),
    )

    decision = ReliabilityGate().evaluate(
        [EvaluationResult(case_id="v-1", module_id="verification")],
        profile,
    )

    assert decision.status is GateStatus.FAIL
    assert "required_invariant_missing:false_accept_must_be_zero" in decision.reasons


def test_existing_e2_result_can_map_to_evaluation_result() -> None:
    retrieval_result = evaluate_retrieval(
        [
            EvidenceCandidate(
                domain="financial_reports",
                doc_id="d1",
                source="canonical://financial_reports/d1/page/1",
                text="营业收入 100 亿元",
                metadata={"page_number": 1},
            )
        ],
        RetrievalGold(
            required_doc_ids=("d1",),
            required_pages={"d1": (1,)},
        ),
        k=5,
    )

    mapped = adapt_layer_result(
        module_id="retrieval",
        case_id="retrieval-e2-1",
        layer_result=retrieval_result,
        thresholds={
            "document_recall_at_k": 1.0,
            "page_recall_at_k": 1.0,
        },
    )

    assert mapped.module_id == "retrieval"
    assert mapped.metric("document_recall_at_k").passed is True
    assert mapped.metric("page_recall_at_k").passed is True
    assert mapped.diagnostics["source_result_type"] == "RetrievalQualityResult"


def test_gate_decision_and_profile_serialization_are_stable() -> None:
    profile = ReliabilityProfile(
        module_id="parser",
        risk_level="MEDIUM",
        required_metrics=("page_recall",),
        test_techniques=("gold",),
        gate_policy={"metric_failure": "REVIEW"},
    )
    result = EvaluationResult(
        case_id="p-1",
        module_id="parser",
        metrics=(MetricResult.threshold_metric("page_recall", value=1.0, threshold=0.95),),
    )
    decision = ReliabilityGate().evaluate([result], profile)

    assert profile.to_dict()["risk_level"] == "MEDIUM"
    assert profile.to_dict()["test_techniques"] == ["gold"]
    assert decision.to_dict() == {
        "module_id": "parser",
        "status": "PASS",
        "reasons": [],
        "evaluated_cases": 1,
    }


def test_gate_fails_closed_on_module_mismatch() -> None:
    profile = ReliabilityProfile(module_id="parser", risk_level="LOW")
    decision = ReliabilityGate().evaluate(
        [EvaluationResult(case_id="r-1", module_id="retrieval")],
        profile,
    )

    assert decision.status is GateStatus.FAIL
    assert any(reason.startswith("module_mismatch:") for reason in decision.reasons)


def test_profile_cannot_downgrade_missing_required_invariant() -> None:
    try:
        ReliabilityProfile(
            module_id="verification",
            risk_level="HIGH",
            required_invariants=("false_accept_must_be_zero",),
            gate_policy={"missing_required_invariant": "REVIEW"},
        )
    except ValueError as exc:
        assert "missing_required_invariant must be FAIL" in str(exc)
    else:
        raise AssertionError("unsafe invariant policy should be rejected")


def test_required_invariant_is_checked_for_every_case() -> None:
    profile = ReliabilityProfile(
        module_id="verification",
        risk_level="HIGH",
        required_invariants=("false_accept_must_be_zero",),
    )
    case_a = EvaluationResult(
        case_id="a",
        module_id="verification",
        metrics=(MetricResult.invariant("false_accept_must_be_zero", passed=True),),
    )
    case_b = EvaluationResult(case_id="b", module_id="verification")

    decision = ReliabilityGate().evaluate([case_a, case_b], profile)

    assert decision.status is GateStatus.FAIL
    assert "required_invariant_missing:b:false_accept_must_be_zero" in decision.reasons


def test_required_invariant_passes_only_when_every_case_has_it() -> None:
    profile = ReliabilityProfile(
        module_id="verification",
        risk_level="HIGH",
        required_invariants=("false_accept_must_be_zero",),
    )
    results = [
        EvaluationResult(
            case_id=case_id,
            module_id="verification",
            metrics=(MetricResult.invariant("false_accept_must_be_zero", passed=True),),
        )
        for case_id in ("a", "b")
    ]

    assert ReliabilityGate().evaluate(results, profile).status is GateStatus.PASS


def test_one_failed_required_invariant_fails_even_if_other_case_passes() -> None:
    profile = ReliabilityProfile(
        module_id="verification",
        risk_level="HIGH",
        required_invariants=("false_accept_must_be_zero",),
    )
    results = [
        EvaluationResult(
            case_id="a",
            module_id="verification",
            metrics=(MetricResult.invariant("false_accept_must_be_zero", passed=False),),
        ),
        EvaluationResult(
            case_id="b",
            module_id="verification",
            metrics=(MetricResult.invariant("false_accept_must_be_zero", passed=True),),
        ),
    ]

    decision = ReliabilityGate().evaluate(results, profile)

    assert decision.status is GateStatus.FAIL
    assert "critical_invariant_failed:a:false_accept_must_be_zero" in decision.reasons


def test_required_metric_is_checked_for_every_case() -> None:
    profile = ReliabilityProfile(
        module_id="retrieval",
        risk_level="MEDIUM",
        required_metrics=("recall_at_10",),
        gate_policy={"missing_required_metric": "REVIEW"},
    )
    results = [
        EvaluationResult(
            case_id="a",
            module_id="retrieval",
            metrics=(MetricResult.threshold_metric("recall_at_10", value=1.0, threshold=0.9),),
        ),
        EvaluationResult(case_id="b", module_id="retrieval"),
    ]

    decision = ReliabilityGate().evaluate(results, profile)

    assert decision.status is GateStatus.REVIEW
    assert "required_metric_missing:b:recall_at_10" in decision.reasons


def test_threshold_metric_rejects_inconsistent_explicit_passed_true() -> None:
    with pytest.raises(ValueError, match="passed is inconsistent"):
        MetricResult(
            metric_name="recall_at_10",
            kind=MetricKind.METRIC,
            value=0.1,
            threshold=0.9,
            comparison=">=",
            passed=True,
        )


def test_threshold_metric_rejects_inconsistent_explicit_passed_false() -> None:
    with pytest.raises(ValueError, match="passed is inconsistent"):
        MetricResult(
            metric_name="recall_at_10",
            kind=MetricKind.METRIC,
            value=1.0,
            threshold=0.9,
            comparison=">=",
            passed=False,
        )


def test_plain_metric_cannot_satisfy_required_invariant_with_same_name() -> None:
    profile = ReliabilityProfile(
        module_id="verification",
        risk_level="HIGH",
        required_invariants=("false_accept_must_be_zero",),
    )
    result = EvaluationResult(
        case_id="a",
        module_id="verification",
        metrics=(
            MetricResult.threshold_metric(
                "false_accept_must_be_zero",
                value=1.0,
                threshold=1.0,
            ),
        ),
    )

    decision = ReliabilityGate().evaluate([result], profile)

    assert decision.status is GateStatus.FAIL
    assert "required_invariant_missing:a:false_accept_must_be_zero" in decision.reasons


def test_invariant_rejects_value_and_passed_contradiction() -> None:
    with pytest.raises(ValueError, match="invariant value and passed must agree"):
        MetricResult(
            metric_name="false_accept_must_be_zero",
            kind=MetricKind.INVARIANT,
            value=False,
            passed=True,
            comparison="==",
        )


def test_metric_kind_is_explicit_for_factory_methods() -> None:
    metric = MetricResult.threshold_metric("recall", value=1.0, threshold=0.9)
    invariant = MetricResult.invariant("lineage_preserved", passed=True)

    assert metric.kind is MetricKind.METRIC
    assert invariant.kind is MetricKind.INVARIANT
    assert metric.to_dict()["kind"] == "METRIC"
    assert invariant.to_dict()["kind"] == "INVARIANT"


def test_additional_critical_metric_with_passed_true_can_pass() -> None:
    result = EvaluationResult(
        case_id="critical-true",
        module_id="generic",
        metrics=(
            MetricResult(
                metric_name="runtime_safety_check",
                value="ok",
                passed=True,
                severity="CRITICAL",
            ),
        ),
    )

    decision = ReliabilityGate().evaluate(
        [result],
        ReliabilityProfile(module_id="generic", risk_level="HIGH"),
    )

    assert decision.status is GateStatus.PASS


def test_additional_critical_metric_with_passed_false_fails() -> None:
    result = EvaluationResult(
        case_id="critical-false",
        module_id="generic",
        metrics=(
            MetricResult(
                metric_name="runtime_safety_check",
                value="bad",
                passed=False,
                severity="CRITICAL",
            ),
        ),
    )

    decision = ReliabilityGate().evaluate(
        [result],
        ReliabilityProfile(module_id="generic", risk_level="HIGH"),
    )

    assert decision.status is GateStatus.FAIL
    assert "critical_metric_failed:critical-false:runtime_safety_check" in decision.reasons


def test_additional_critical_metric_with_unknown_passed_fails_closed() -> None:
    result = EvaluationResult(
        case_id="critical-unknown",
        module_id="generic",
        metrics=(
            MetricResult(
                metric_name="runtime_safety_check",
                value="unknown",
                passed=None,
                severity="CRITICAL",
            ),
        ),
    )

    decision = ReliabilityGate().evaluate(
        [result],
        ReliabilityProfile(module_id="generic", risk_level="HIGH"),
    )

    assert decision.status is GateStatus.FAIL
    assert "critical_metric_failed:critical-unknown:runtime_safety_check" in decision.reasons


def test_empty_evaluation_results_fail_closed() -> None:
    decision = ReliabilityGate().evaluate(
        [],
        ReliabilityProfile(module_id="generic", risk_level="LOW"),
    )

    assert decision.status is GateStatus.FAIL
    assert decision.reasons == ("evaluation_results_missing",)


@pytest.mark.parametrize(
    ("case_status", "expected_status"),
    [
        (GateStatus.FAIL, GateStatus.FAIL),
        (GateStatus.REVIEW, GateStatus.REVIEW),
    ],
)
def test_case_gate_status_is_preserved_by_reliability_gate(
    case_status: GateStatus,
    expected_status: GateStatus,
) -> None:
    result = EvaluationResult(
        case_id="case-status",
        module_id="generic",
        gate_status=case_status,
    )

    decision = ReliabilityGate().evaluate(
        [result],
        ReliabilityProfile(module_id="generic", risk_level="LOW"),
    )

    assert decision.status is expected_status
