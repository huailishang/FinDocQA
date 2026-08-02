"""Fail-closed assembly of explicit C3 inputs; this module never executes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from calculation.compiler import SafeFormulaCompiler
from calculation.contracts import (
    BoundVariable,
    DeterministicExecutionGateInput,
    ExecutionGateFact,
    FormulaProgram,
    SemanticBindingCandidate,
    SemanticBindingRequest,
    SemanticBindingStatus,
)
from calculation.material import FormulaEvidenceGate, MaterialFormulaExtractor, SemanticVariableBinder
from contracts import EvidenceCandidate


@dataclass(frozen=True)
class C3InputAssemblyInput:
    """Caller-supplied inputs for one already-selected evidence candidate."""

    candidate: EvidenceCandidate
    semantic_requests: Mapping[str, SemanticBindingRequest]
    semantic_candidates: Mapping[str, Sequence[SemanticBindingCandidate]]
    question_formula_match: ExecutionGateFact


@dataclass(frozen=True)
class C3InputAssemblyResult:
    """Pure assembly output for the C3-F adapter; ``program`` is optional."""

    program: FormulaProgram | None
    bindings: Mapping[str, BoundVariable] = field(default_factory=dict)
    gate_input: DeterministicExecutionGateInput | None = None
    reasons: Sequence[str] = field(default_factory=tuple)


class C3InputAssembler:
    """Assemble only explicit material, semantic, and question-match facts."""

    def __init__(self) -> None:
        self._extractor = MaterialFormulaExtractor()
        self._formula_gate = FormulaEvidenceGate()
        self._semantic_binder = SemanticVariableBinder()
        self._compiler = SafeFormulaCompiler()

    @staticmethod
    def _gate_input(
        formula_evidence: ExecutionGateFact,
        semantic_binding: ExecutionGateFact,
        question_formula_match: ExecutionGateFact,
    ) -> DeterministicExecutionGateInput:
        return DeterministicExecutionGateInput(
            formula_evidence=formula_evidence,
            semantic_binding=semantic_binding,
            question_formula_match=question_formula_match,
        )

    def assemble(self, assembly_input: C3InputAssemblyInput) -> C3InputAssemblyResult:
        formulas = self._extractor.extract_from_candidate(assembly_input.candidate)
        if len(formulas) != 1:
            reason = "formula_not_found" if not formulas else "multiple_material_formulas"
            formula_fact = ExecutionGateFact(False, (reason,))
            semantic_fact = ExecutionGateFact(False, ("formula_program_missing",))
            return C3InputAssemblyResult(
                program=None,
                gate_input=self._gate_input(
                    formula_fact, semantic_fact, assembly_input.question_formula_match
                ),
                reasons=(reason, "formula_program_missing"),
            )

        evidence = formulas[0]
        try:
            referenced = SafeFormulaCompiler.referenced_symbols(evidence.normalized_expression)
        except ValueError as exc:
            reason = str(exc)
            formula_fact = ExecutionGateFact(False, (reason,))
            semantic_fact = ExecutionGateFact(False, ("formula_program_missing",))
            return C3InputAssemblyResult(
                program=None,
                gate_input=self._gate_input(
                    formula_fact, semantic_fact, assembly_input.question_formula_match
                ),
                reasons=(reason, "formula_program_missing"),
            )

        reasons: list[str] = []
        bindings: dict[str, BoundVariable] = {}
        request_names = set(assembly_input.semantic_requests)
        referenced_names = set(referenced)
        for name in sorted(referenced_names - request_names):
            reasons.append(f"semantic_request_missing:{name}")
        for name in sorted(request_names - referenced_names):
            reasons.append(f"semantic_request_extraneous:{name}")
        for name in sorted(set(assembly_input.semantic_candidates) - referenced_names):
            reasons.append(f"semantic_candidates_extraneous:{name}")

        for name in referenced:
            request = assembly_input.semantic_requests.get(name)
            if request is None:
                continue
            if request.name != name:
                reasons.append(f"semantic_request_name_mismatch:{name}")
                continue
            result = self._semantic_binder.bind(
                request,
                tuple(assembly_input.semantic_candidates.get(name, ())),
            )
            if result.status is not SemanticBindingStatus.BOUND or result.bound is None:
                reasons.append(f"semantic_binding_{result.status.value}:{name}")
                reasons.extend(str(reason) for reason in result.reasons)
                continue
            bindings[name] = result.bound

        semantic_ready = not reasons and set(bindings) == referenced_names
        formula_gate = self._formula_gate.evaluate(evidence, bindings)
        formula_reasons = tuple(formula_gate.reasons)
        if formula_gate.status.value != "PASS":
            reasons.extend(formula_reasons)

        program: FormulaProgram | None = None
        if formula_gate.status.value == "PASS":
            try:
                program = self._compiler.compile(evidence, bindings)
            except ValueError as exc:
                reasons.append(f"formula_compile_failed:{exc}")

        formula_ready = program is not None
        if not formula_ready and not formula_reasons and not any(
            reason.startswith("formula_compile_failed:") for reason in reasons
        ):
            reasons.append("formula_program_missing")
        if program is None:
            semantic_ready = False
            if "formula_program_missing" not in reasons:
                reasons.append("formula_program_missing")

        formula_fact = ExecutionGateFact(
            formula_ready,
            () if formula_ready else tuple(dict.fromkeys(formula_reasons or tuple(reasons))),
        )
        semantic_fact = ExecutionGateFact(
            semantic_ready,
            () if semantic_ready else tuple(dict.fromkeys(reasons)),
        )
        return C3InputAssemblyResult(
            program=program,
            bindings=dict(bindings),
            gate_input=self._gate_input(
                formula_fact, semantic_fact, assembly_input.question_formula_match
            ),
            reasons=tuple(dict.fromkeys(reasons)),
        )
