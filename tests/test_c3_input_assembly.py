from __future__ import annotations

from calculation import (
    C3InputAssembler,
    C3InputAssemblyInput,
    ExecutionGateFact,
    FormulaSourceRef,
    SemanticBindingCandidate,
    SemanticBindingRequest,
)
from contracts import EvidenceCandidate


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


def test_fully_explicit_input_assembles_program_bindings_and_pass_gates() -> None:
    result = C3InputAssembler().assemble(_input())

    assert result.program is not None
    assert set(result.bindings) == {"a", "b"}
    assert result.bindings["a"].source_ref is not None
    assert result.gate_input is not None
    assert result.gate_input.formula_evidence.passed is True
    assert result.gate_input.semantic_binding.passed is True
    assert result.gate_input.question_formula_match == ExecutionGateFact(True)


def test_multiple_formulas_are_refused_without_arbitrary_selection() -> None:
    result = C3InputAssembler().assemble(_input(candidate=_candidate("x = a + b\ny = a - b")))

    assert result.program is None
    assert result.gate_input is not None
    assert result.gate_input.formula_evidence.passed is False
    assert "multiple_material_formulas" in result.reasons


def test_ambiguous_semantic_candidates_are_refused() -> None:
    result = C3InputAssembler().assemble(
        _input(semantic_candidates={"a": (_value("a", "2"), _value("a", "4")), "b": (_value("b", "3"),)})
    )

    assert result.program is None
    assert result.gate_input is not None
    assert result.gate_input.semantic_binding.passed is False
    assert "semantic_binding_ambiguous:a" in result.reasons


def test_missing_or_extraneous_requests_are_refused() -> None:
    result = C3InputAssembler().assemble(
        _input(semantic_requests={"a": _request("a"), "unused": _request("unused")})
    )

    assert result.program is None
    assert result.gate_input is not None
    assert result.gate_input.semantic_binding.passed is False
    assert "semantic_request_missing:b" in result.reasons
    assert "semantic_request_extraneous:unused" in result.reasons


def test_supplied_question_match_false_or_missing_is_preserved() -> None:
    for fact in (ExecutionGateFact(False, ("question_mismatch",)), ExecutionGateFact(None, ("question_missing",))):
        result = C3InputAssembler().assemble(_input(question_formula_match=fact))

        assert result.program is not None
        assert result.gate_input is not None
        assert result.gate_input.question_formula_match == fact
