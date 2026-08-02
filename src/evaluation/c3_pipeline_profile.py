"""Offline reliability profile and real-path harness for ExplicitC3Pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from calculation import (
    C3InputAssemblyInput,
    ExecutionGateFact,
    FormulaSourceRef,
    SemanticBindingCandidate,
    SemanticBindingRequest,
)
from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    SolverResult,
)
from evaluation.contracts import EvaluationResult, MetricResult
from evaluation.oracles.c3_pipeline import (
    C3PipelineDecisionRow,
    C3PipelineFactors,
    C3PipelineObservation,
    evaluate_c3_pipeline_factors,
    evaluate_c3_pipeline_invariants,
)
from evaluation.profiles import ReliabilityProfile
from solvers.c3_deterministic import ExplicitC3Pipeline
from solvers.calculation import CalculationSolver


C3_PIPELINE_MODULE_ID = "explicit_c3_pipeline"

C3_PIPELINE_REQUIRED_INVARIANTS = (
    "candidate_scope_exactly_once_must_hold",
    "ambiguous_or_incomplete_assembly_must_not_execute",
    "non_pass_question_match_must_not_execute",
    "blocked_path_must_not_call_legacy_or_provider",
    "successful_result_must_preserve_trace_and_lineage",
    "unrelated_bundle_evidence_must_not_change_decision",
    "independent_oracle_decision_must_match",
)


def build_c3_pipeline_reliability_profile() -> ReliabilityProfile:
    """Return the CRITICAL evaluation profile for the explicit C3 chain."""

    return ReliabilityProfile(
        module_id=C3_PIPELINE_MODULE_ID,
        risk_level="CRITICAL",
        failure_modes=(
            "candidate_out_of_scope_executed",
            "ambiguous_or_incomplete_assembly_executed",
            "non_pass_question_match_executed",
            "blocked_path_called_legacy_or_provider",
            "successful_result_lost_trace_or_lineage",
            "unrelated_bundle_evidence_changed_decision",
            "real_pipeline_disagreed_with_independent_oracle",
        ),
        required_invariants=C3_PIPELINE_REQUIRED_INVARIANTS,
        test_techniques=(
            "explicit_decision_table",
            "constrained_pairwise_covering_set",
            "selected_3way_high_risk_cases",
            "metamorphic_unrelated_evidence",
            "mutation_style_erroneous_allow_sentinel",
        ),
        gate_policy={"missing_required_invariant": "FAIL"},
    )


class _OfflineProviderSentinel:
    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        raise AssertionError("offline C3 reliability harness must not call a provider")


class _InstrumentedCalculationSolver(CalculationSolver):
    def __init__(self, provider: _OfflineProviderSentinel) -> None:
        super().__init__(llm_client=provider)
        self.legacy_call_count = 0

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        self.legacy_call_count += 1
        return SolverResult(
            qid=bundle.question.qid,
            answer="LEGACY_PATH_INVOKED",
            solver=self.name,
            confidence=0.0,
            metadata={"provider_call_count": self.llm_client.call_count},
        )


@dataclass(frozen=True)
class C3PipelineRun:
    case_id: str
    factors: C3PipelineFactors
    result: SolverResult
    observation: C3PipelineObservation
    evaluation: EvaluationResult


def _target_candidate(formula_state: str) -> EvidenceCandidate:
    text = "result = a + b"
    if formula_state == "multiple":
        text = "result = a + b\nother = a - b"
    return EvidenceCandidate(
        doc_id="doc-a",
        text=text,
        source="doc://a/page/1",
        domain="test",
        metadata={"page_number": 1},
    )


def _unrelated_candidate() -> EvidenceCandidate:
    return EvidenceCandidate(
        doc_id="doc-b",
        text="unrelated_metric = 999",
        source="doc://b/page/7",
        domain="test",
        metadata={"page_number": 7},
    )


def _request(name: str) -> SemanticBindingRequest:
    return SemanticBindingRequest(name, name, "entity", "2024", "ratio", "doc-a")


def _value(name: str, value: str) -> SemanticBindingCandidate:
    return SemanticBindingCandidate(
        value=value,
        metric=name,
        entity="entity",
        period="2024",
        unit="ratio",
        document_id="doc-a",
        source_ref=FormulaSourceRef("doc-a", 1, "doc://a/page/1", name),
    )


def _question_match_fact(state: str) -> ExecutionGateFact:
    if state == "pass":
        return ExecutionGateFact(True)
    if state == "fail":
        return ExecutionGateFact(False, ("question_formula_match_failed",))
    if state == "missing":
        return ExecutionGateFact(None, ("question_formula_match_missing",))
    raise ValueError(f"unsupported question_formula_match: {state}")


def build_c3_pipeline_case_inputs(
    factors: C3PipelineFactors,
) -> tuple[EvidenceBundle, C3InputAssemblyInput]:
    """Translate one oracle row into explicit caller inputs for the real pipeline."""

    target = _target_candidate(factors.material_formula_state)
    bundle_candidates: list[EvidenceCandidate] = []
    if factors.candidate_membership == "exactly_one":
        bundle_candidates.append(target)
    elif factors.candidate_membership == "duplicate_equal":
        bundle_candidates.extend((target, target))
    elif factors.candidate_membership != "zero":
        raise ValueError(f"unsupported candidate_membership: {factors.candidate_membership}")

    if factors.unrelated_bundle_candidate == "present":
        unrelated = _unrelated_candidate()
        if unrelated == target:
            raise ValueError("unrelated candidate must remain non-equal to the target")
        bundle_candidates.append(unrelated)
    elif factors.unrelated_bundle_candidate != "absent":
        raise ValueError(
            f"unsupported unrelated_bundle_candidate: {factors.unrelated_bundle_candidate}"
        )

    semantic_candidates: dict[str, Sequence[SemanticBindingCandidate]] = {
        "a": (_value("a", "2"),),
        "b": (_value("b", "3"),),
    }
    if factors.semantic_binding_state == "missing":
        semantic_candidates = {"a": (_value("a", "2"),), "b": ()}
    elif factors.semantic_binding_state == "ambiguous":
        semantic_candidates = {
            "a": (_value("a", "2"), _value("a", "4")),
            "b": (_value("b", "3"),),
        }
    elif factors.semantic_binding_state != "complete":
        raise ValueError(
            f"unsupported semantic_binding_state: {factors.semantic_binding_state}"
        )

    bundle = EvidenceBundle(
        question=Question(
            qid=f"c3-reliability-{factors.candidate_membership}",
            domain="test",
            text="计算 a + b",
            options={},
            answer_format="freeform",
            doc_ids=["doc-a"],
        ),
        classification=ClassificationResult(labels=[]),
        candidates=tuple(bundle_candidates),
        prompt_context="",
        estimated_tokens=0,
    )
    assembly_input = C3InputAssemblyInput(
        candidate=target,
        semantic_requests={"a": _request("a"), "b": _request("b")},
        semantic_candidates=semantic_candidates,
        question_formula_match=_question_match_fact(factors.question_formula_match),
    )
    return bundle, assembly_input


def adapt_c3_pipeline_observation(
    result: SolverResult,
    *,
    legacy_call_count: int,
    provider_call_count: int,
) -> C3PipelineObservation:
    """Normalize behavior without consulting production decision diagnostics."""

    metadata = dict(result.metadata or {})
    trace = tuple(metadata.get("result_trace") or ())
    lineage = tuple(metadata.get("source_lineage") or ())
    answer = str(result.answer or "")
    return C3PipelineObservation(
        executed=bool(answer.strip()),
        answer=answer,
        trace=trace,
        lineage=lineage,
        legacy_call_count=legacy_call_count,
        provider_call_count=provider_call_count,
    )


def adapt_c3_pipeline_evaluation(
    *,
    case_id: str,
    factors: C3PipelineFactors,
    observation: C3PipelineObservation,
    baseline_observation: C3PipelineObservation | None = None,
) -> EvaluationResult:
    """Convert an independent expectation/observation comparison for the generic gate."""

    expected = evaluate_c3_pipeline_factors(factors)
    invariants = evaluate_c3_pipeline_invariants(
        factors,
        observation,
        baseline_observation=baseline_observation,
    )
    metrics = tuple(
        MetricResult.invariant(
            name,
            passed=passed,
            details={
                "expected_execute": expected.should_execute,
                "observed_execute": observation.executed,
            },
        )
        for name, passed in invariants.items()
    )
    return EvaluationResult(
        case_id=case_id,
        module_id=C3_PIPELINE_MODULE_ID,
        metrics=metrics,
        violations=tuple(name for name, passed in invariants.items() if not passed),
        diagnostics={
            "factors": {
                "candidate_membership": factors.candidate_membership,
                "material_formula_state": factors.material_formula_state,
                "semantic_binding_state": factors.semantic_binding_state,
                "question_formula_match": factors.question_formula_match,
                "unrelated_bundle_candidate": factors.unrelated_bundle_candidate,
            },
            "expected_execute": expected.should_execute,
            "observed_execute": observation.executed,
            "legacy_call_count": observation.legacy_call_count,
            "provider_call_count": observation.provider_call_count,
        },
    )


def run_c3_pipeline_case(
    row: C3PipelineDecisionRow,
    *,
    baseline_observation: C3PipelineObservation | None = None,
) -> C3PipelineRun:
    """Drive the real ExplicitC3Pipeline for one bounded oracle row."""

    provider = _OfflineProviderSentinel()
    solver = _InstrumentedCalculationSolver(provider)
    pipeline = ExplicitC3Pipeline(solver=solver)
    bundle, assembly_input = build_c3_pipeline_case_inputs(row.factors)
    result = pipeline.solve(bundle, assembly_input)
    observation = adapt_c3_pipeline_observation(
        result,
        legacy_call_count=solver.legacy_call_count,
        provider_call_count=provider.call_count,
    )
    evaluation = adapt_c3_pipeline_evaluation(
        case_id=row.case_id,
        factors=row.factors,
        observation=observation,
        baseline_observation=baseline_observation,
    )
    return C3PipelineRun(
        case_id=row.case_id,
        factors=row.factors,
        result=result,
        observation=observation,
        evaluation=evaluation,
    )


def run_c3_pipeline_rows(
    rows: Sequence[C3PipelineDecisionRow],
) -> tuple[C3PipelineRun, ...]:
    return tuple(run_c3_pipeline_case(row) for row in rows)


def build_erroneous_allow_sentinel() -> EvaluationResult:
    """Forge one unsafe allow observation to prove the generic gate fails closed."""

    factors = C3PipelineFactors("zero", "one", "complete", "pass", "absent")
    forged = C3PipelineObservation(
        executed=True,
        answer="FORGED_ALLOW",
        trace=({"step": "forged"},),
        lineage=({"source": "forged"},),
        legacy_call_count=0,
        provider_call_count=0,
    )
    return adapt_c3_pipeline_evaluation(
        case_id="mutation_sentinel_erroneous_allow",
        factors=factors,
        observation=forged,
    )


__all__ = [
    "C3PipelineRun",
    "C3_PIPELINE_MODULE_ID",
    "C3_PIPELINE_REQUIRED_INVARIANTS",
    "adapt_c3_pipeline_evaluation",
    "adapt_c3_pipeline_observation",
    "build_c3_pipeline_case_inputs",
    "build_c3_pipeline_reliability_profile",
    "build_erroneous_allow_sentinel",
    "run_c3_pipeline_case",
    "run_c3_pipeline_rows",
]
