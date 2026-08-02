from __future__ import annotations

from decimal import Decimal

import pytest

from calculation import (
    BoundVariable,
    DeterministicExecutionGateInput,
    ExecutionGateFact,
    FormulaProgram,
    FormulaSourceRef,
    FormulaStep,
)
from contracts import ClassificationResult, EvidenceBundle, Question
from solvers.calculation import CalculationSolver


class FakeLLMClient:
    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, *args, **kwargs):
        self.call_count += 1
        raise AssertionError("the gated adapter must not call an LLM")


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        question=Question(
            qid="c3-gate-adapter",
            domain="test",
            text="计算 a + b",
            options={},
            answer_format="freeform",
            doc_ids=["doc-a"],
        ),
        classification=ClassificationResult(labels=[]),
        candidates=(),
        prompt_context="",
        estimated_tokens=0,
    )


def _program() -> FormulaProgram:
    ref = FormulaSourceRef("doc-a", 1, "doc://a/page/1", "formula")
    return FormulaProgram(
        formula_id="adapter-demo",
        steps=(FormulaStep("result", "add", ("a", "b")),),
        output_ref="result",
        source_refs=(ref,),
    )


def _bindings() -> dict[str, BoundVariable]:
    ref = FormulaSourceRef("doc-a", 1, "doc://a/page/1", "variables")
    return {
        "a": BoundVariable("a", Decimal("2"), source_ref=ref),
        "b": BoundVariable("b", Decimal("3"), source_ref=ref),
    }


def _gates(**changes: ExecutionGateFact) -> DeterministicExecutionGateInput:
    values = {
        "formula_evidence": ExecutionGateFact(True),
        "semantic_binding": ExecutionGateFact(True),
        "question_formula_match": ExecutionGateFact(True),
    }
    values.update(changes)
    return DeterministicExecutionGateInput(**values)


def _solver_without_legacy(monkeypatch) -> tuple[CalculationSolver, FakeLLMClient]:
    client = FakeLLMClient()
    solver = CalculationSolver(llm_client=client)
    monkeypatch.setattr(
        solver,
        "solve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy solve invoked")),
    )
    return solver, client


def test_explicit_gated_adapter_returns_decimal_trace_and_lineage(monkeypatch) -> None:
    solver, client = _solver_without_legacy(monkeypatch)

    result = solver.solve_deterministic_gated(_bundle(), _program(), _bindings(), _gates())

    assert result.answer == "5"
    assert result.confidence == 1.0
    assert result.metadata["answer_source"] == "c3_deterministic_gate"
    assert result.metadata["computation_status"] == "completed"
    assert result.metadata["result_trace"]
    assert result.metadata["source_lineage"] == [ref.to_dict() for ref in _program().source_refs]
    assert result.metadata["provider_call_count"] == 0
    assert result.metadata["legacy_execution_invoked"] is False
    assert client.call_count == 0


@pytest.mark.parametrize(
    ("name", "fact"),
    [
        ("formula_evidence", ExecutionGateFact(False, ("formula_review",))),
        ("semantic_binding", ExecutionGateFact(None, ("binding_missing",))),
        ("question_formula_match", ExecutionGateFact(False, ("question_mismatch",))),
    ],
)
def test_each_refused_gate_is_blocked_without_legacy_execution(monkeypatch, name, fact) -> None:
    solver, client = _solver_without_legacy(monkeypatch)

    result = solver.solve_deterministic_gated(
        _bundle(), _program(), _bindings(), _gates(**{name: fact})
    )

    assert result.answer == ""
    assert result.confidence == 0.0
    assert result.metadata["answer_source"] == "c3_deterministic_execution_not_ready"
    assert result.metadata["computation_status"] == "blocked"
    assert result.metadata["error"] == "deterministic_execution_not_ready"
    assert name in result.metadata["audit_reasons"]
    assert result.metadata["legacy_execution_invoked"] is False
    assert client.call_count == 0


def test_multiple_refused_gates_preserve_all_audit_reasons(monkeypatch) -> None:
    solver, client = _solver_without_legacy(monkeypatch)

    result = solver.solve_deterministic_gated(
        _bundle(),
        _program(),
        _bindings(),
        _gates(
            formula_evidence=ExecutionGateFact(False, ("formula_review",)),
            semantic_binding=ExecutionGateFact(False, ("binding_ambiguous",)),
        ),
    )

    assert result.answer == ""
    assert result.metadata["audit_reasons"][:2] == ["formula_evidence", "semantic_binding"]
    assert "formula_review" in result.metadata["audit_reasons"]
    assert "binding_ambiguous" in result.metadata["audit_reasons"]
    assert result.metadata["source_lineage"] == [ref.to_dict() for ref in _program().source_refs]
    assert client.call_count == 0
