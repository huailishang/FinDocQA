from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import agent.factory as factory_module
from agent.factory import PipelineFactory
from agent.workflow import EnhancedBaselineWorkflow
from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    QuestionLabel,
    SolverResult,
)
from solvers.c3_deterministic import ExplicitC3Pipeline
from solvers.c3_shadow import (
    C3QuestionFormulaMatchAuthority,
    C3ShadowInputRecord,
    C3ShadowObservation,
    C3ShadowObserver,
    C3ShadowState,
    candidate_fingerprint,
    parse_shadow_input_record,
    question_fingerprint,
)
from solvers.calculation import CalculationSolver


RULE_ID = "explicit_metric_formula_match_v1"


class FakeLLMClient:
    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError("C3 shadow must not call a provider")


class CountingPipeline:
    def __init__(self, result: SolverResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.call_count = 0

    def solve(self, bundle, assembly_input):
        self.call_count += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class StaticClassifier:
    def __init__(self, classification: ClassificationResult) -> None:
        self.classification = classification

    def classify(self, question):
        return self.classification


class StaticRetriever:
    def __init__(self, candidates) -> None:
        self.candidates = tuple(candidates)
        self.call_count = 0

    def retrieve(self, question, classification):
        self.call_count += 1
        return self.candidates


class StaticAssembler:
    def __init__(self, bundle: EvidenceBundle) -> None:
        self.bundle = bundle
        self.call_count = 0

    def assemble(self, question, classification, candidates):
        self.call_count += 1
        return replace(self.bundle, question=question, classification=classification)


class CountingMainSolver:
    name = "unchanged_main_solver"

    def __init__(self, *, answer: str = "5", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.call_count = 0

    def solve(self, bundle):
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return SolverResult(
            qid=bundle.question.qid,
            answer=self.answer,
            solver=self.name,
            confidence=0.75,
            metadata={
                "submission_answers": [self.answer],
                "provider_call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )


class CountingFallbackSolver(CountingMainSolver):
    name = "existing_fallback_solver"


class CountingWriter:
    def __init__(self) -> None:
        self.call_count = 0

    def write(self, results):
        self.call_count += 1


def _question(
    *,
    calculation: bool = True,
    answer_format: str = "freeform",
    options: dict[str, str] | None = None,
) -> Question:
    return Question(
        qid="c3-shadow-routing",
        domain="test",
        text="计算 a + b",
        options=options or {},
        answer_format=answer_format,
        doc_ids=("doc-a",),
        candidate_doc_ids=("doc-a",),
        submission_slot_count=1 if answer_format == "freeform" else None,
        raw={"calculation_fixture": calculation},
    )


def _classification(*, calculation: bool = True) -> ClassificationResult:
    return ClassificationResult(
        labels=[QuestionLabel.CALCULATION] if calculation else [QuestionLabel.FACT_LOOKUP],
        reasons={"fixture": "explicit"},
    )


def _candidate(
    *,
    text: str = "result = a + b",
    source: str = "doc://a/page_1",
    page_number: int | None = 1,
    metadata: dict[str, Any] | None = None,
) -> EvidenceCandidate:
    values: dict[str, Any] = {"block_id": "formula-block"}
    if page_number is not None:
        values["page_number"] = page_number
    values.update(metadata or {})
    return EvidenceCandidate(
        doc_id="doc-a",
        text=text,
        source=source,
        domain="test",
        metadata=values,
    )


def _source_ref(name: str) -> dict[str, Any]:
    return {
        "doc_id": "doc-a",
        "page_number": 1,
        "source": "doc://a/page_1",
        "block_id": f"value-{name}",
        "excerpt": f"{name} explicit value",
    }


def _shadow_input(
    question: Question,
    candidate: EvidenceCandidate,
    *,
    passed: bool | None = True,
    rule_id: str = RULE_ID,
) -> dict[str, Any]:
    qfp = question_fingerprint(question)
    cfp = candidate_fingerprint(candidate)
    requests = {
        name: {
            "name": name,
            "metric": name,
            "entity": "entity",
            "period": "2024",
            "unit": "ratio",
            "document_id": "doc-a",
        }
        for name in ("a", "b")
    }
    candidates = {
        "a": [
            {
                "value": "2",
                "metric": "a",
                "entity": "entity",
                "period": "2024",
                "unit": "ratio",
                "document_id": "doc-a",
                "source_ref": _source_ref("a"),
            }
        ],
        "b": [
            {
                "value": "3",
                "metric": "b",
                "entity": "entity",
                "period": "2024",
                "unit": "ratio",
                "document_id": "doc-a",
                "source_ref": _source_ref("b"),
            }
        ],
    }
    return {
        "schema_version": "c3-shadow-input/v1",
        "question_fingerprint": qfp,
        "candidate_fingerprint": cfp,
        "semantic_requests": requests,
        "semantic_candidates": candidates,
        "question_formula_match": {
            "authority_type": "deterministic_rule",
            "rule_id": rule_id,
            "passed": passed,
            "reasons": [] if passed is True else ["question_formula_match_non_pass"],
            "question_fingerprint": qfp,
            "candidate_fingerprint": cfp,
            "document_id": "doc-a",
        },
    }


def _ready_candidate(question: Question, **changes: Any) -> EvidenceCandidate:
    candidate = _candidate(**changes)
    metadata = dict(candidate.metadata)
    metadata["c3_shadow_input_v1"] = _shadow_input(question, candidate)
    return replace(candidate, metadata=metadata)


def _bundle(
    question: Question | None = None,
    *,
    candidates=(),
    verification_candidates=(),
    calculation: bool = True,
) -> EvidenceBundle:
    question = question or _question(calculation=calculation)
    return EvidenceBundle(
        question=question,
        classification=_classification(calculation=calculation),
        candidates=tuple(candidates),
        verification_candidates=tuple(verification_candidates),
        prompt_context="fixture prompt context",
        estimated_tokens=10,
    )


def _real_observer_without_legacy(monkeypatch, *, enabled: bool = True):
    client = FakeLLMClient()
    solver = CalculationSolver(llm_client=client)
    monkeypatch.setattr(
        solver,
        "solve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy CalculationSolver.solve invoked")
        ),
    )
    observer = C3ShadowObserver(
        enabled=enabled,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=ExplicitC3Pipeline(solver=solver),
    )
    return observer, client


def _workflow(bundle: EvidenceBundle, observer: C3ShadowObserver, *, main=None, fallback=None):
    main = main or CountingMainSolver()
    fallback = fallback or CountingFallbackSolver()
    writer = CountingWriter()
    workflow = EnhancedBaselineWorkflow(
        classifier=StaticClassifier(bundle.classification),
        retriever=StaticRetriever(bundle.candidates),
        assembler=StaticAssembler(bundle),
        solver=main,
        writer=writer,
        verifier=None,
        fallback_solver=fallback,
        self_check_verifier=None,
        enforce_production_integrity=False,
        fallback_enabled=True,
        c3_shadow_observer=observer,
    )
    return workflow, main, fallback, writer


def test_public_shadow_contract_is_immutable_and_bounded() -> None:
    observation = C3ShadowObserver(enabled=False).observe(_bundle())

    assert observation.state is C3ShadowState.DISABLED
    with pytest.raises(Exception):
        observation.state = C3ShadowState.ERROR  # type: ignore[misc]
    assert set(observation.to_dict()) == {
        "schema_version",
        "state",
        "reason_codes",
        "applicable",
        "pipeline_invoked",
        "candidate_count",
        "question_fingerprint",
        "candidate_fingerprint",
        "match_rule_id",
        "would_execute",
        "shadow_answer",
        "computation_status",
        "trace",
        "source_refs",
        "legacy_execution_invoked",
        "provider_call_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "error_type",
    }


@pytest.mark.parametrize(
    ("bundle", "state", "reason"),
    [
        (_bundle(), C3ShadowState.DISABLED, "c3_shadow_disabled"),
        (
            _bundle(_question(calculation=False), calculation=False),
            C3ShadowState.DISABLED,
            "c3_shadow_disabled",
        ),
    ],
)
def test_disabled_shadow_never_invokes_pipeline(bundle, state, reason) -> None:
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))
    observation = C3ShadowObserver(enabled=False, pipeline=pipeline).observe(bundle)

    assert observation.state is state
    assert reason in observation.reason_codes
    assert observation.pipeline_invoked is False
    assert pipeline.call_count == 0


def test_non_calculation_is_not_applicable_without_candidate_or_pipeline() -> None:
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))
    question = _question(calculation=False)
    observation = C3ShadowObserver(enabled=True, pipeline=pipeline).observe(
        _bundle(question, calculation=False)
    )

    assert observation.state is C3ShadowState.NOT_APPLICABLE
    assert observation.reason_codes == ("question_not_calculation",)
    assert pipeline.call_count == 0


@pytest.mark.parametrize("candidates", [(), (_candidate(), _candidate())])
def test_zero_multiple_or_duplicate_solver_visible_candidates_fail_closed(candidates) -> None:
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))
    observation = C3ShadowObserver(enabled=True, pipeline=pipeline).observe(
        _bundle(candidates=candidates)
    )

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.shadow_answer == ""
    assert pipeline.call_count == 0


def test_verification_candidate_cannot_become_execution_authority() -> None:
    question = _question()
    external = _ready_candidate(question)
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))

    observation = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    ).observe(_bundle(question, candidates=(), verification_candidates=(external,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.reason_codes == ("candidate_scope_zero",)
    assert pipeline.call_count == 0


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (_candidate(source="", page_number=1), "candidate_source_missing"),
        (_candidate(source="doc://a/no-page", page_number=None), "candidate_page_missing"),
    ],
)
def test_incomplete_candidate_lineage_blocks_before_input_parsing(candidate, reason) -> None:
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))
    observation = C3ShadowObserver(enabled=True, pipeline=pipeline).observe(
        _bundle(candidates=(candidate,))
    )

    assert observation.state is C3ShadowState.BLOCKED
    assert reason in observation.reason_codes
    assert pipeline.call_count == 0


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (lambda payload: payload.update(schema_version="unknown/v9"), "c3_shadow_input_schema_invalid"),
        (lambda payload: payload.update(question_fingerprint="stale"), "c3_shadow_input_fingerprint_invalid"),
        (
            lambda payload: payload["question_formula_match"].update(candidate_fingerprint="stale"),
            "c3_shadow_input_fingerprint_invalid",
        ),
        (
            lambda payload: payload["semantic_requests"]["a"].update(unit="unsupported"),
            "c3_shadow_semantic_records_invalid",
        ),
        (
            lambda payload: payload["semantic_candidates"].update(extra=[]),
            "c3_shadow_semantic_records_invalid",
        ),
        (
            lambda payload: payload["question_formula_match"].update(
                authority_type="model_confidence"
            ),
            "c3_shadow_question_match_record_invalid",
        ),
        (
            lambda payload: payload["question_formula_match"].update(rule_id="not-approved"),
            "c3_shadow_question_match_record_invalid",
        ),
        (
            lambda payload: payload["question_formula_match"].update(confidence=0.99),
            "c3_shadow_question_match_record_invalid",
        ),
    ],
)
def test_strict_shadow_input_rejects_malformed_stale_or_unapproved_records(
    mutate, expected_reason
) -> None:
    question = _question()
    candidate = _candidate()
    payload = _shadow_input(question, candidate)
    mutate(payload)
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))

    observation = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    ).observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert expected_reason in observation.reason_codes
    assert observation.shadow_answer == ""
    assert pipeline.call_count == 0


@pytest.mark.parametrize("passed", [False, None])
def test_explicit_false_or_missing_match_reaches_pipeline_but_cannot_execute(monkeypatch, passed) -> None:
    question = _question()
    candidate = _candidate()
    payload = _shadow_input(question, candidate, passed=passed)
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    observer, client = _real_observer_without_legacy(monkeypatch)

    observation = observer.observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.pipeline_invoked is True
    assert observation.shadow_answer == ""
    assert observation.provider_call_count == 0
    assert observation.legacy_execution_invoked is False
    assert client.call_count == 0


def test_duplicate_semantic_records_block_before_real_explicit_pipeline(monkeypatch) -> None:
    question = _question()
    candidate = _candidate()
    payload = _shadow_input(question, candidate)
    duplicate = deepcopy(payload["semantic_candidates"]["a"][0])
    duplicate["value"] = "4"
    payload["semantic_candidates"]["a"].append(duplicate)
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    observer, client = _real_observer_without_legacy(monkeypatch)

    observation = observer.observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.reason_codes == ("c3_shadow_semantic_candidate_cardinality_invalid",)
    assert observation.pipeline_invoked is False
    assert observation.shadow_answer == ""
    assert client.call_count == 0


def test_safe_explicit_record_executes_with_trace_lineage_and_zero_side_effects(monkeypatch) -> None:
    question = _question()
    candidate = _ready_candidate(question)
    observer, client = _real_observer_without_legacy(monkeypatch)

    observation = observer.observe(_bundle(question, candidates=(candidate,)))
    serialized = observation.to_dict()

    assert observation.state is C3ShadowState.EXECUTED
    assert observation.shadow_answer == "5"
    assert observation.would_execute is True
    assert observation.trace
    assert observation.source_refs
    assert all(ref["doc_id"] == "doc-a" and ref["page_number"] == 1 for ref in observation.source_refs)
    assert observation.provider_call_count == 0
    assert observation.legacy_execution_invoked is False
    assert client.call_count == 0
    assert question.text not in repr(serialized)
    assert candidate.text not in repr(serialized)
    assert "fixture prompt context" not in repr(serialized)


def test_unexpected_pipeline_error_is_contained_as_shadow_error() -> None:
    question = _question()
    candidate = _ready_candidate(question)
    pipeline = CountingPipeline(error=RuntimeError("raw evidence must not leak"))
    observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    )

    observation = observer.observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.ERROR
    assert observation.error_type == "RuntimeError"
    assert observation.shadow_answer == ""
    assert "raw evidence must not leak" not in repr(observation.to_dict())
    assert pipeline.call_count == 1


def test_final_observer_boundary_contains_unexpected_adapter_error(monkeypatch) -> None:
    observer = C3ShadowObserver(enabled=True)
    monkeypatch.setattr(
        observer,
        "_not_applicable_reason",
        lambda _bundle: (_ for _ in ()).throw(RuntimeError("adapter bug")),
    )

    observation = observer.observe(_bundle())

    assert observation.state is C3ShadowState.ERROR
    assert observation.reason_codes == ("c3_shadow_observer_error",)
    assert observation.error_type == "RuntimeError"
    assert "adapter bug" not in repr(observation.to_dict())



def test_blocked_pipeline_drops_dynamic_trace_lineage_and_reasons() -> None:
    marker = "evidence_marker_xyz"
    question = _question()
    candidate = _ready_candidate(question)
    result = SolverResult(
        qid=question.qid,
        answer="",
        solver="mutant",
        metadata={
            "answer_source": "c3_input_assembly_not_ready",
            "computation_status": "blocked",
            "assembly_reasons": [f"missing_variable_binding:{marker}"],
            "audit_reasons": [marker],
            "result_trace": [
                {
                    "step": marker,
                    "op": marker,
                    "args": [marker],
                    "resolved_args": [marker],
                    "result": marker,
                }
            ],
            "source_refs": [
                {
                    "doc_id": "doc-a",
                    "page_number": 1,
                    "source": "doc://a/page_1",
                    "block_id": marker,
                    "excerpt": marker,
                }
            ],
            "legacy_execution_invoked": False,
            "provider_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    )
    observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=CountingPipeline(result=result),
    )

    observation = observer.observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.reason_codes == ("c3_shadow_input_assembly_not_ready",)
    assert observation.trace == ()
    assert observation.source_refs == ()
    assert marker not in repr(observation.to_dict())


def test_non_numeric_completed_mutant_cannot_leak_through_executed_fields() -> None:
    marker = "evidence_marker_xyz"
    question = _question()
    candidate = _ready_candidate(question)
    result = SolverResult(
        qid=question.qid,
        answer=marker,
        solver="mutant",
        metadata={
            "answer_source": "c3_deterministic_gate",
            "computation_status": "completed",
            "result_trace": [
                {
                    "step": marker,
                    "op": "add",
                    "resolved_args": [marker, "3"],
                    "result": marker,
                }
            ],
            "source_refs": [
                {
                    "doc_id": "doc-a",
                    "page_number": 1,
                    "source": "doc://a/page_1",
                    "block_id": marker,
                }
            ],
            "legacy_execution_invoked": False,
            "provider_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    )
    observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=CountingPipeline(result=result),
    )

    observation = observer.observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.shadow_answer == ""
    assert observation.trace == ()
    assert observation.source_refs == ()
    assert marker not in repr(observation.to_dict())


def _valid_post_call_metadata() -> dict[str, Any]:
    return {
        "computation_status": "completed",
        "result_trace": [
            {"step": "#1", "op": "add", "resolved_args": ["2", "3"], "result": "5"}
        ],
        "source_refs": [_source_ref("a")],
        "provider_call_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "legacy_execution_invoked": False,
    }


def _observe_post_call_metadata(metadata: Any):
    question = _question()
    candidate = _ready_candidate(question)
    result = SolverResult(
        qid=question.qid,
        answer="5",
        solver="post_call_metadata_mutant",
        metadata=metadata,
    )
    pipeline = CountingPipeline(result=result)
    observation = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    ).observe(_bundle(question, candidates=(candidate,)))
    return question, candidate, pipeline, observation


def _assert_post_call_metadata_error(question, candidate, pipeline, observation) -> None:
    assert pipeline.call_count == 1
    assert observation.state is C3ShadowState.ERROR
    assert observation.reason_codes == ("c3_shadow_result_metadata_invalid",)
    assert observation.applicable is True
    assert observation.pipeline_invoked is True
    assert observation.candidate_count == 1
    assert observation.question_fingerprint == question_fingerprint(question)
    assert observation.candidate_fingerprint == candidate_fingerprint(candidate)
    assert observation.match_rule_id == RULE_ID
    assert observation.shadow_answer == ""
    assert observation.trace == ()
    assert observation.source_refs == ()
    assert observation.error_type == "ResultMetadataError"


@pytest.mark.parametrize(
    "field",
    ["provider_call_count", "prompt_tokens", "completion_tokens", "total_tokens"],
)
@pytest.mark.parametrize(
    "invalid_value",
    [True, "raw_count_marker_xyz", 1.0, None, {}, [], -1],
)
def test_post_call_counts_require_exact_non_negative_integers(field, invalid_value) -> None:
    metadata = _valid_post_call_metadata()
    metadata[field] = invalid_value

    question, candidate, pipeline, observation = _observe_post_call_metadata(metadata)

    _assert_post_call_metadata_error(question, candidate, pipeline, observation)
    assert "raw_count_marker_xyz" not in repr(observation.to_dict())


@pytest.mark.parametrize(
    "invalid_value", [0, 1, "raw_legacy_marker_xyz", None, {}, []]
)
def test_post_call_legacy_flag_requires_exact_boolean(invalid_value) -> None:
    metadata = _valid_post_call_metadata()
    metadata["legacy_execution_invoked"] = invalid_value

    question, candidate, pipeline, observation = _observe_post_call_metadata(metadata)

    _assert_post_call_metadata_error(question, candidate, pipeline, observation)
    assert "raw_legacy_marker_xyz" not in repr(observation.to_dict())


@pytest.mark.parametrize("field", ["result_trace", "source_refs"])
@pytest.mark.parametrize(
    "invalid_value", [None, "raw_container_marker_xyz", {}, 1, 1.0, True]
)
def test_post_call_trace_and_source_containers_fail_closed(field, invalid_value) -> None:
    metadata = _valid_post_call_metadata()
    metadata[field] = invalid_value

    question, candidate, pipeline, observation = _observe_post_call_metadata(metadata)

    _assert_post_call_metadata_error(question, candidate, pipeline, observation)
    assert "raw_container_marker_xyz" not in repr(observation.to_dict())


def test_post_call_source_lineage_fallback_container_fails_closed() -> None:
    metadata = _valid_post_call_metadata()
    metadata.pop("source_refs")
    metadata["source_lineage"] = "raw_source_lineage_marker_xyz"

    question, candidate, pipeline, observation = _observe_post_call_metadata(metadata)

    _assert_post_call_metadata_error(question, candidate, pipeline, observation)
    assert "raw_source_lineage_marker_xyz" not in repr(observation.to_dict())


def test_malformed_post_call_metadata_does_not_change_main_answer_or_fallback() -> None:
    question = _question()
    candidate = _ready_candidate(question)
    metadata = _valid_post_call_metadata()
    metadata["provider_call_count"] = "not-an-int"
    result = SolverResult(
        qid=question.qid,
        answer="5",
        solver="post_call_metadata_mutant",
        metadata=metadata,
    )
    pipeline = CountingPipeline(result=result)
    observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    )
    workflow, main, fallback, _writer = _workflow(
        _bundle(question, candidates=(candidate,)), observer
    )

    pipeline_result = workflow.process_one(question)

    assert pipeline_result.answer == "5"
    assert pipeline_result.solver_result.solver == main.name
    assert pipeline_result.fallback_used is False
    assert main.call_count == 1
    assert fallback.call_count == 0
    assert pipeline.call_count == 1
    shadow = pipeline_result.metadata["c3_shadow"]
    assert shadow["state"] == "ERROR"
    assert shadow["applicable"] is True
    assert shadow["pipeline_invoked"] is True
    assert shadow["reason_codes"] == ["c3_shadow_result_metadata_invalid"]
    assert pipeline_result.prompt_tokens == 0
    assert pipeline_result.completion_tokens == 0
    assert pipeline_result.total_tokens == 0


def test_side_effect_bearing_pipeline_result_is_not_accepted_as_executed() -> None:
    question = _question()
    candidate = _ready_candidate(question)
    result = SolverResult(
        qid=question.qid,
        answer="5",
        solver="mutant",
        metadata={
            "computation_status": "completed",
            "result_trace": [{"op": "add"}],
            "source_refs": [_source_ref("a")],
            "legacy_execution_invoked": False,
            "provider_call_count": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    )
    observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=CountingPipeline(result=result),
    )

    observation = observer.observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.ERROR
    assert observation.reason_codes == ("c3_shadow_side_effect_contract_violated",)
    assert observation.shadow_answer == ""


def test_workflow_shadow_execution_does_not_change_main_answer_or_call_count(monkeypatch) -> None:
    question = _question()
    candidate = _ready_candidate(question)
    bundle = _bundle(question, candidates=(candidate,))
    enabled, client = _real_observer_without_legacy(monkeypatch)
    disabled = C3ShadowObserver(enabled=False)

    disabled_workflow, disabled_main, disabled_fallback, disabled_writer = _workflow(
        bundle, disabled
    )
    enabled_workflow, enabled_main, enabled_fallback, enabled_writer = _workflow(bundle, enabled)
    disabled_result = disabled_workflow.process_one(question)
    enabled_result = enabled_workflow.process_one(question)

    assert disabled_result.answer == enabled_result.answer == "5"
    assert disabled_result.answer_values == enabled_result.answer_values == ("5",)
    assert disabled_result.solver_result.solver == enabled_result.solver_result.solver
    assert disabled_result.prompt_tokens == enabled_result.prompt_tokens == 0
    assert disabled_result.completion_tokens == enabled_result.completion_tokens == 0
    assert disabled_result.total_tokens == enabled_result.total_tokens == 0
    assert disabled_result.fallback_used is enabled_result.fallback_used is False
    assert disabled_result.metadata["c3_shadow"]["state"] == "DISABLED"
    assert enabled_result.metadata["c3_shadow"]["state"] == "EXECUTED"
    assert enabled_result.metadata["c3_shadow"]["shadow_answer"] == "5"
    assert enabled_result.answer == enabled_result.solver_result.answer
    assert disabled_main.call_count == enabled_main.call_count == 1
    assert disabled_fallback.call_count == enabled_fallback.call_count == 0
    assert disabled_writer.call_count == enabled_writer.call_count == 0
    assert client.call_count == 0


def test_shadow_block_or_error_never_triggers_existing_fallback() -> None:
    question = _question()
    plain_candidate = _candidate()
    blocked_observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=CountingPipeline(error=AssertionError("pipeline must not run")),
    )
    blocked_workflow, blocked_main, blocked_fallback, _ = _workflow(
        _bundle(question, candidates=(plain_candidate,)), blocked_observer
    )

    blocked_result = blocked_workflow.process_one(question)

    assert blocked_result.metadata["c3_shadow"]["state"] == "BLOCKED"
    assert blocked_main.call_count == 1
    assert blocked_fallback.call_count == 0

    ready = _ready_candidate(question)
    error_observer = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=CountingPipeline(error=RuntimeError("shadow only")),
    )
    error_workflow, error_main, error_fallback, _ = _workflow(
        _bundle(question, candidates=(ready,)), error_observer
    )

    error_result = error_workflow.process_one(question)

    assert error_result.metadata["c3_shadow"]["state"] == "ERROR"
    assert error_main.call_count == 1
    assert error_fallback.call_count == 0


def test_existing_main_error_uses_existing_fallback_and_preserves_shadow_observation() -> None:
    question = _question()
    candidate = _candidate()
    observer = C3ShadowObserver(enabled=True, approved_match_rule_ids=(RULE_ID,))
    main = CountingMainSolver(error=RuntimeError("main path failed"))
    fallback = CountingFallbackSolver(answer="7")
    workflow, main, fallback, _ = _workflow(
        _bundle(question, candidates=(candidate,)), observer, main=main, fallback=fallback
    )

    result = workflow.process_one(question)

    assert result.answer == "7"
    assert result.fallback_used is True
    assert result.metadata["c3_shadow"]["state"] == "BLOCKED"
    assert main.call_count == 1
    assert fallback.call_count == 1


def test_factory_defaults_off_and_accepts_only_explicit_allowlist(monkeypatch, tmp_path: Path) -> None:
    classification = _classification()
    bundle = _bundle(candidates=())

    def build(config):
        factory = PipelineFactory(config, tmp_path)
        monkeypatch.setattr(factory, "build_classifier", lambda: StaticClassifier(classification))
        monkeypatch.setattr(factory, "build_retriever", lambda: StaticRetriever(()))
        monkeypatch.setattr(factory, "build_assembler", lambda: StaticAssembler(bundle))
        monkeypatch.setattr(
            factory_module.OpenAICompatibleClient,
            "from_env",
            lambda _config: None,
        )
        monkeypatch.setattr(factory_module, "build_fallback_client", lambda _config: None)
        return factory.build_workflow()

    default_workflow = build({})
    enabled_workflow = build(
        {
            "pipeline": {
                "c3_shadow": {
                    "enabled": True,
                    "approved_match_rule_ids": [RULE_ID],
                }
            }
        }
    )
    malformed_workflow = build(
        {"pipeline": {"c3_shadow": {"enabled": True, "approved_match_rule_ids": RULE_ID}}}
    )

    assert default_workflow.c3_shadow_observer.enabled is False
    assert default_workflow.c3_shadow_observer.approved_match_rule_ids == frozenset()
    assert enabled_workflow.c3_shadow_observer.enabled is True
    assert enabled_workflow.c3_shadow_observer.approved_match_rule_ids == frozenset({RULE_ID})
    assert malformed_workflow.c3_shadow_observer.approved_match_rule_ids == frozenset()



@pytest.mark.parametrize(
    "raw_enabled",
    [None, False, "false", "true", 0, 1, {}, [], [True]],
)
def test_factory_shadow_activation_requires_exact_boolean_true(
    monkeypatch, tmp_path: Path, raw_enabled
) -> None:
    classification = _classification()
    bundle = _bundle(candidates=())
    factory = PipelineFactory(
        {"pipeline": {"c3_shadow": {"enabled": raw_enabled}}},
        tmp_path,
    )
    monkeypatch.setattr(factory, "build_classifier", lambda: StaticClassifier(classification))
    monkeypatch.setattr(factory, "build_retriever", lambda: StaticRetriever(()))
    monkeypatch.setattr(factory, "build_assembler", lambda: StaticAssembler(bundle))
    monkeypatch.setattr(factory_module.OpenAICompatibleClient, "from_env", lambda _config: None)
    monkeypatch.setattr(factory_module, "build_fallback_client", lambda _config: None)

    workflow = factory.build_workflow()

    assert workflow.c3_shadow_observer.enabled is False


def test_factory_allowlist_ignores_non_string_members(monkeypatch, tmp_path: Path) -> None:
    classification = _classification()
    bundle = _bundle(candidates=())
    factory = PipelineFactory(
        {
            "pipeline": {
                "c3_shadow": {
                    "enabled": True,
                    "approved_match_rule_ids": [RULE_ID, 1, None, "", "  "],
                }
            }
        },
        tmp_path,
    )
    monkeypatch.setattr(factory, "build_classifier", lambda: StaticClassifier(classification))
    monkeypatch.setattr(factory, "build_retriever", lambda: StaticRetriever(()))
    monkeypatch.setattr(factory, "build_assembler", lambda: StaticAssembler(bundle))
    monkeypatch.setattr(factory_module.OpenAICompatibleClient, "from_env", lambda _config: None)
    monkeypatch.setattr(factory_module, "build_fallback_client", lambda _config: None)

    workflow = factory.build_workflow()

    assert workflow.c3_shadow_observer.approved_match_rule_ids == frozenset({RULE_ID})


def test_matching_extraneous_formula_variable_records_block_before_pipeline() -> None:
    question = _question()
    candidate = _candidate()
    payload = _shadow_input(question, candidate)
    payload["semantic_requests"]["extra"] = {
        "name": "extra",
        "metric": "extra",
        "entity": "entity",
        "period": "2024",
        "unit": "ratio",
        "document_id": "doc-a",
    }
    payload["semantic_candidates"]["extra"] = [
        {
            "value": "9",
            "metric": "extra",
            "entity": "entity",
            "period": "2024",
            "unit": "ratio",
            "document_id": "doc-a",
            "source_ref": _source_ref("extra"),
        }
    ]
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))

    observation = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    ).observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.reason_codes == ("c3_shadow_formula_variable_records_mismatch",)
    assert observation.pipeline_invoked is False
    assert observation.shadow_answer == ""
    assert pipeline.call_count == 0


@pytest.mark.parametrize(
    "candidate_text",
    ["a = 2", "x = a + b\ny = a - b", "result = a +"],
)
def test_non_exact_formula_extraction_blocks_before_pipeline(candidate_text: str) -> None:
    question = _question()
    candidate = _candidate(text=candidate_text)
    payload = _shadow_input(question, candidate)
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))

    observation = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    ).observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.pipeline_invoked is False
    assert observation.shadow_answer == ""
    assert pipeline.call_count == 0


def test_duplicate_semantic_candidate_record_blocks_before_pipeline() -> None:
    question = _question()
    candidate = _candidate()
    payload = _shadow_input(question, candidate)
    payload["semantic_candidates"]["a"].append(
        deepcopy(payload["semantic_candidates"]["a"][0])
    )
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    pipeline = CountingPipeline(error=AssertionError("pipeline must not run"))

    observation = C3ShadowObserver(
        enabled=True,
        approved_match_rule_ids=(RULE_ID,),
        pipeline=pipeline,
    ).observe(_bundle(question, candidates=(candidate,)))

    assert observation.state is C3ShadowState.BLOCKED
    assert observation.reason_codes == ("c3_shadow_semantic_candidate_cardinality_invalid",)
    assert observation.pipeline_invoked is False
    assert pipeline.call_count == 0


def test_public_records_snapshot_nested_mutable_aliases() -> None:
    reasons_alias = ["match_ready"]
    authority = C3QuestionFormulaMatchAuthority(
        authority_type="deterministic_rule",
        rule_id=RULE_ID,
        passed=True,
        reasons=reasons_alias,
        question_fingerprint="qfp",
        candidate_fingerprint="cfp",
        document_id="doc-a",
    )
    reasons_alias.append("mutated")
    assert authority.reasons == ("match_ready",)

    question = _question()
    candidate = _candidate()
    payload = _shadow_input(question, candidate)
    record_candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    parsed = parse_shadow_input_record(
        payload,
        bundle=_bundle(question, candidates=(record_candidate,)),
        candidate=record_candidate,
        approved_rule_ids=frozenset({RULE_ID}),
    )
    requests_alias = dict(parsed.semantic_requests)
    candidates_alias = {name: list(rows) for name, rows in parsed.semantic_candidates.items()}
    record = C3ShadowInputRecord(
        schema_version=parsed.schema_version,
        question_fingerprint=parsed.question_fingerprint,
        candidate_fingerprint=parsed.candidate_fingerprint,
        semantic_requests=requests_alias,
        semantic_candidates=candidates_alias,
        question_formula_match=authority,
    )
    requests_alias.clear()
    candidates_alias["a"].clear()
    assert set(record.semantic_requests) == {"a", "b"}
    assert len(record.semantic_candidates["a"]) == 1
    with pytest.raises(TypeError):
        record.semantic_requests["extra"] = record.semantic_requests["a"]  # type: ignore[index]
    with pytest.raises(TypeError):
        record.semantic_candidates["a"] = ()  # type: ignore[index]

    trace_alias = [{"op": "add", "nested": {"operands": ["a", "b"]}}]
    source_alias = [{"doc_id": "doc-a", "page_number": 1, "source": "doc://a/page_1"}]
    reason_alias = ["static_reason"]
    observation = C3ShadowObservation(
        state=C3ShadowState.EXECUTED,
        reason_codes=reason_alias,
        trace=trace_alias,
        source_refs=source_alias,
    )
    trace_alias[0]["op"] = "mutated"
    trace_alias[0]["nested"]["operands"].append("extra")
    source_alias[0]["source"] = "mutated"
    reason_alias.append("mutated")
    assert observation.reason_codes == ("static_reason",)
    assert observation.trace[0]["op"] == "add"
    assert observation.trace[0]["nested"]["operands"] == ("a", "b")
    assert observation.source_refs[0]["source"] == "doc://a/page_1"
    with pytest.raises(TypeError):
        observation.trace[0]["op"] = "mutated"  # type: ignore[index]

    detached = observation.to_dict()
    detached["reason_codes"].append("changed")
    detached["trace"][0]["nested"]["operands"].append("changed")
    detached["source_refs"][0]["source"] = "changed"
    assert observation.reason_codes == ("static_reason",)
    assert observation.trace[0]["nested"]["operands"] == ("a", "b")
    assert observation.source_refs[0]["source"] == "doc://a/page_1"


def test_candidate_text_marker_never_leaks_from_blocked_pipeline(monkeypatch) -> None:
    marker = "evidence_marker_xyz"
    question = _question()
    candidate = _candidate(text=f"result = {marker} + a")
    payload = _shadow_input(question, candidate)
    candidate = replace(
        candidate,
        metadata={**candidate.metadata, "c3_shadow_input_v1": payload},
    )
    observer, client = _real_observer_without_legacy(monkeypatch)

    observation = observer.observe(_bundle(question, candidates=(candidate,)))
    serialized = repr(observation.to_dict())

    assert observation.state is C3ShadowState.BLOCKED
    assert marker not in serialized
    assert observation.reason_codes == ("c3_shadow_formula_variable_records_mismatch",)
    assert observation.pipeline_invoked is False
    assert client.call_count == 0


def test_default_config_has_no_active_c3_authority() -> None:
    text = Path("config/config.yaml").read_text(encoding="utf-8")
    shadow_block = text.split("c3_shadow:", 1)[1].split("\n\n", 1)[0]

    assert "enabled: false" in shadow_block
    assert "approved_match_rule_ids: []" in shadow_block
    assert "active:" not in shadow_block
    assert "authoritative:" not in shadow_block
    assert "prefer_c3" not in shadow_block
