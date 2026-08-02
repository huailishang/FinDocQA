from __future__ import annotations

from decimal import Decimal

from calculation import (
    BoundVariable,
    DeterministicCalculationEngine,
    DeterministicExecutionGateInput,
    ExecutionGateFact,
    FormulaProgram,
    FormulaSourceRef,
    FormulaStep,
)


def _program() -> FormulaProgram:
    ref = FormulaSourceRef("doc-a", 1, "doc://a/page/1", "formula")
    return FormulaProgram(
        formula_id="gate-demo",
        steps=(FormulaStep("result", "add", ("a", "b")),),
        output_ref="result",
        source_refs=(ref,),
    )


def _bindings() -> dict[str, BoundVariable]:
    ref = FormulaSourceRef("doc-a", 1, "doc://a/page/1", "vars")
    return {
        "a": BoundVariable("a", Decimal("2"), source_ref=ref),
        "b": BoundVariable("b", Decimal("3"), source_ref=ref),
    }


def _gate(**changes: ExecutionGateFact) -> DeterministicExecutionGateInput:
    values = {
        "formula_evidence": ExecutionGateFact(True),
        "semantic_binding": ExecutionGateFact(True),
        "question_formula_match": ExecutionGateFact(True),
    }
    values.update(changes)
    return DeterministicExecutionGateInput(**values)


def test_all_three_pass_facts_delegate_to_existing_program_execution() -> None:
    result = DeterministicCalculationEngine().execute_gated_program(_program(), _bindings(), _gate())

    assert result.ok is True
    assert result.value == Decimal("5")
    assert result.trace
    assert result.source_refs == _program().source_refs
    assert result.gate_status == "PASS"


def test_each_failed_or_missing_gate_refuses_without_execution(monkeypatch) -> None:
    engine = DeterministicCalculationEngine()

    def must_not_execute(*args, **kwargs):
        raise AssertionError("legacy execution must not run")

    monkeypatch.setattr(engine, "execute_program", must_not_execute)
    failures = {
        "formula_evidence": ExecutionGateFact(False, ("formula_review",)),
        "semantic_binding": ExecutionGateFact(None, ("binding_missing",)),
        "question_formula_match": ExecutionGateFact(False, ("question_mismatch",)),
    }
    for name, fact in failures.items():
        result = engine.execute_gated_program(_program(), _bindings(), _gate(**{name: fact}))

        assert result.ok is False
        assert result.error == "deterministic_execution_not_ready"
        assert name in result.audit_reasons
        assert result.trace == ()


def test_multiple_gate_failures_are_auditable_and_do_not_tie_break(monkeypatch) -> None:
    engine = DeterministicCalculationEngine()
    monkeypatch.setattr(engine, "execute_program", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    result = engine.execute_gated_program(
        _program(),
        _bindings(),
        _gate(
            formula_evidence=ExecutionGateFact(False, ("formula_review",)),
            semantic_binding=ExecutionGateFact(False, ("binding_ambiguous",)),
        ),
    )

    assert result.ok is False
    assert result.error == "deterministic_execution_not_ready"
    assert result.audit_reasons[:2] == ("formula_evidence", "semantic_binding")
    assert "formula_review" in result.audit_reasons
    assert "binding_ambiguous" in result.audit_reasons
