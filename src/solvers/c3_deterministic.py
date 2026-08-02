"""Explicit offline bridge from C3 input assembly to the C3-F solver adapter."""
from __future__ import annotations

from typing import Any

from calculation import C3InputAssembler, C3InputAssemblyInput
from contracts import EvidenceBundle, SolverResult
from solvers.calculation import CalculationSolver


class ExplicitC3Pipeline:
    """Run only caller-supplied C3 assembly and deterministic gated execution.

    This type intentionally does not select candidates, infer gate facts, or use
    the legacy ``CalculationSolver.solve`` route.
    """

    def __init__(
        self,
        *,
        assembler: C3InputAssembler | None = None,
        solver: CalculationSolver | None = None,
    ) -> None:
        self._assembler = assembler or C3InputAssembler()
        self._solver = solver or CalculationSolver()

    def solve(
        self,
        bundle: EvidenceBundle,
        assembly_input: C3InputAssemblyInput,
    ) -> SolverResult:
        candidate_matches = sum(
            candidate == assembly_input.candidate for candidate in bundle.candidates
        )
        if candidate_matches != 1:
            metadata: dict[str, Any] = {
                "answer_source": "c3_input_candidate_out_of_scope",
                "computation_status": "blocked",
                "assembly_reasons": [
                    f"c3_input_candidate_match_count:{candidate_matches}"
                ],
                "audit_reasons": [
                    f"c3_input_candidate_match_count:{candidate_matches}"
                ],
                "legacy_execution_invoked": False,
                "provider_call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
            return SolverResult(
                qid=bundle.question.qid,
                answer="",
                solver=CalculationSolver.name,
                raw_output="c3_input_candidate_out_of_scope",
                confidence=0.0,
                metadata=metadata,
            )
        assembly = self._assembler.assemble(assembly_input)
        if assembly.program is None or assembly.gate_input is None:
            metadata: dict[str, Any] = {
                "answer_source": "c3_input_assembly_not_ready",
                "computation_status": "blocked",
                "assembly_reasons": list(assembly.reasons),
                "audit_reasons": list(assembly.reasons),
                "legacy_execution_invoked": False,
                "provider_call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
            return SolverResult(
                qid=bundle.question.qid,
                answer="",
                solver=CalculationSolver.name,
                raw_output="c3_input_assembly_not_ready",
                confidence=0.0,
                metadata=metadata,
            )
        return self._solver.solve_deterministic_gated(
            bundle,
            assembly.program,
            assembly.bindings,
            assembly.gate_input,
        )
