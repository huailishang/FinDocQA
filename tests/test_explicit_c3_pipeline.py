from __future__ import annotations

import pytest

from calculation import (
    C3InputAssemblyInput,
    ExecutionGateFact,
    FormulaSourceRef,
    SemanticBindingCandidate,
    SemanticBindingRequest,
)
from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question
from solvers.c3_deterministic import ExplicitC3Pipeline
from solvers.calculation import CalculationSolver


class FakeLLMClient:
    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError("explicit C3 pipeline must not call an LLM")


def _bundle(*, candidates=()) -> EvidenceBundle:
    return EvidenceBundle(
        question=Question(
            qid="explicit-c3-pipeline",
            domain="test",
            text="计算 a + b",
            options={},
            answer_format="freeform",
            doc_ids=["doc-a"],
        ),
        classification=ClassificationResult(labels=[]),
        candidates=candidates,
        prompt_context="",
        estimated_tokens=0,
    )


def _candidate(text: str = "result = a + b") -> EvidenceCandidate:
    return EvidenceCandidate(
        doc_id="doc-a",
        text=text,
        source="doc://a/page/1",
        domain="test",
        metadata={"page_number": 1},
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


def _input(**changes) -> C3InputAssemblyInput:
    values = {
        "candidate": _candidate(),
        "semantic_requests": {"a": _request("a"), "b": _request("b")},
        "semantic_candidates": {"a": (_value("a", "2"),), "b": (_value("b", "3"),)},
        "question_formula_match": ExecutionGateFact(True),
    }
    values.update(changes)
    return C3InputAssemblyInput(**values)


def _pipeline_without_legacy(monkeypatch) -> tuple[ExplicitC3Pipeline, FakeLLMClient]:
    client = FakeLLMClient()
    solver = CalculationSolver(llm_client=client)
    monkeypatch.setattr(
        solver,
        "solve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy solve invoked")),
    )
    return ExplicitC3Pipeline(solver=solver), client


def test_ready_explicit_pipeline_returns_deterministic_answer_trace_and_lineage(monkeypatch) -> None:
    pipeline, client = _pipeline_without_legacy(monkeypatch)
    candidate = _candidate()

    result = pipeline.solve(_bundle(candidates=(candidate,)), _input(candidate=candidate))

    assert result.answer == "5"
    assert result.confidence == 1.0
    assert result.metadata["answer_source"] == "c3_deterministic_gate"
    assert result.metadata["result_trace"]
    assert result.metadata["source_lineage"]
    assert result.metadata["legacy_execution_invoked"] is False
    assert result.metadata["provider_call_count"] == 0
    assert client.call_count == 0


def test_multiple_formula_assembly_refusal_is_blocked_without_legacy_execution(monkeypatch) -> None:
    pipeline, client = _pipeline_without_legacy(monkeypatch)
    candidate = _candidate("x = a + b\ny = a - b")

    result = pipeline.solve(_bundle(candidates=(candidate,)), _input(candidate=candidate))

    assert result.answer == ""
    assert result.confidence == 0.0
    assert result.metadata["answer_source"] == "c3_input_assembly_not_ready"
    assert result.metadata["computation_status"] == "blocked"
    assert "multiple_material_formulas" in result.metadata["assembly_reasons"]
    assert result.metadata["legacy_execution_invoked"] is False
    assert client.call_count == 0


def test_semantic_binding_refusal_is_blocked_without_legacy_execution(monkeypatch) -> None:
    pipeline, client = _pipeline_without_legacy(monkeypatch)
    candidate = _candidate()

    result = pipeline.solve(
        _bundle(candidates=(candidate,)),
        _input(
            candidate=candidate,
            semantic_candidates={"a": (_value("a", "2"), _value("a", "4")), "b": (_value("b", "3"),)},
        ),
    )

    assert result.answer == ""
    assert result.metadata["answer_source"] == "c3_input_assembly_not_ready"
    assert "semantic_binding_ambiguous:a" in result.metadata["assembly_reasons"]
    assert client.call_count == 0


@pytest.mark.parametrize(
    "fact",
    [
        ExecutionGateFact(False, ("question_mismatch",)),
        ExecutionGateFact(None, ("question_missing",)),
    ],
)
def test_false_or_missing_question_match_forwards_c3f_gate_refusal(monkeypatch, fact) -> None:
    pipeline, client = _pipeline_without_legacy(monkeypatch)
    candidate = _candidate()

    result = pipeline.solve(
        _bundle(candidates=(candidate,)),
        _input(candidate=candidate, question_formula_match=fact),
    )

    assert result.answer == ""
    assert result.confidence == 0.0
    assert result.metadata["answer_source"] == "c3_deterministic_execution_not_ready"
    assert result.metadata["computation_status"] == "blocked"
    assert "question_formula_match" in result.metadata["audit_reasons"]
    assert result.metadata["legacy_execution_invoked"] is False
    assert client.call_count == 0


@pytest.mark.parametrize("bundle_candidates", [(), (_candidate(), _candidate())])
def test_out_of_scope_or_duplicate_candidate_refuses_before_assembly_or_c3f(
    monkeypatch, bundle_candidates
) -> None:
    pipeline, client = _pipeline_without_legacy(monkeypatch)
    candidate = _candidate()
    monkeypatch.setattr(
        pipeline._assembler,
        "assemble",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("assembly invoked")),
    )
    monkeypatch.setattr(
        pipeline._solver,
        "solve_deterministic_gated",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("C3-F invoked")),
    )

    result = pipeline.solve(_bundle(candidates=bundle_candidates), _input(candidate=candidate))

    assert result.answer == ""
    assert result.confidence == 0.0
    assert result.metadata["answer_source"] == "c3_input_candidate_out_of_scope"
    assert result.metadata["computation_status"] == "blocked"
    assert result.metadata["legacy_execution_invoked"] is False
    assert client.call_count == 0
