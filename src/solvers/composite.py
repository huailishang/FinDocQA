"""Controlled, default-off composite solver integration for Workstream C."""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Mapping, Optional

from composite import CompositeType, classify_composite
from contracts import EvidenceBundle, QuestionLabel, Solver, SolverResult


class CompositeDelegatingSolver:
    """Coordinate existing solvers for overlapping capabilities.

    This class performs no extraction itself.  It preserves the accepted live
    solvers and adds deterministic orchestration, evidence coverage checks and
    explicit fallback metadata.  It is only reachable when RoutedSolver's
    enable_composite_routes flag is true.
    """

    name = "composite"

    def __init__(
        self,
        solvers: Mapping[str, Solver],
        *,
        fallback_on_incomplete_cross_calc: bool = True,
    ) -> None:
        self.solvers = solvers
        self.fallback_on_incomplete_cross_calc = bool(fallback_on_incomplete_cross_calc)

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        labels = set(bundle.classification.labels)
        profile = classify_composite(
            is_multi_select=QuestionLabel.MULTI_OPTION in labels,
            needs_calculation=QuestionLabel.CALCULATION in labels,
            is_cross_doc=QuestionLabel.CROSS_DOC in labels,
        )
        if profile.composite_type is CompositeType.MULTI_CHOICE_PLUS_CALCULATION:
            return self._solve_multi_calc(bundle, profile.composite_type.value)
        if profile.composite_type is CompositeType.CROSS_DOC_PLUS_CALCULATION:
            return self._solve_cross_calc(bundle, profile.composite_type.value)
        return self._delegate("calculation", bundle, profile.composite_type.value)

    def _solve_multi_calc(self, bundle: EvidenceBundle, composite_type: str) -> SolverResult:
        calc = self._delegate("calculation", bundle, composite_type)
        complete = bool(calc.metadata.get("computation_complete"))
        if complete:
            return self._annotate(calc, composite_type, "calculation", False)
        fallback = self._delegate("multi_choice", bundle, composite_type)
        return self._annotate(
            fallback, composite_type, "multi_choice", True,
            primary_solver="calculation",
            primary_answer=calc.answer,
            primary_computation_complete=complete,
            primary_error=calc.metadata.get("calc_error"),
        )

    def _solve_cross_calc(self, bundle: EvidenceBundle, composite_type: str) -> SolverResult:
        required = sorted({str(doc_id) for doc_id in bundle.question.doc_ids})
        present = sorted({str(candidate.doc_id) for candidate in bundle.candidates})
        missing = [doc_id for doc_id in required if doc_id not in present]
        if missing:
            fallback = self._delegate("cross_doc", bundle, composite_type)
            return self._annotate(
                fallback, composite_type, "cross_doc", True,
                required_doc_ids=required,
                present_doc_ids=present,
                missing_doc_ids=missing,
                cross_doc_complete=False,
            )
        calc = self._delegate("calculation", bundle, composite_type)
        complete = bool(calc.metadata.get("computation_complete"))
        consistency = self._check_option_consistency(bundle, calc) if complete else None
        if complete and consistency is False:
            fallback = self._delegate("cross_doc", bundle, composite_type)
            return self._annotate(
                fallback, composite_type, "cross_doc", True,
                required_doc_ids=required,
                present_doc_ids=present,
                missing_doc_ids=[],
                cross_doc_complete=True,
                computation_complete=False,
                calculation_incomplete=True,
                option_consistency=False,
                original_calculation_answer_or_guess=calc.answer,
                original_calculation_answer_source=calc.metadata.get("answer_source"),
                primary_solver="calculation",
                primary_answer=calc.answer,
                primary_computation_complete=True,
                fallback_solver="cross_doc",
                fallback_answer=fallback.answer,
                answer_source=fallback.metadata.get("answer_source") or "cross_doc_fallback",
                risk_label="option_inconsistent",
            )
        if complete or not self.fallback_on_incomplete_cross_calc:
            return self._annotate(
                calc, composite_type, "calculation", False,
                required_doc_ids=required,
                present_doc_ids=present,
                missing_doc_ids=[],
                cross_doc_complete=True,
                calculation_incomplete=not complete,
                option_consistency=consistency,
            )

        fallback = self._delegate("cross_doc", bundle, composite_type)
        fallback_answer_source = fallback.metadata.get("answer_source") or "cross_doc_fallback"
        failure_reason = calc.metadata.get("calc_error") or "computation_complete=false"
        return self._annotate(
            fallback, composite_type, "cross_doc", True,
            required_doc_ids=required,
            present_doc_ids=present,
            missing_doc_ids=[],
            cross_doc_complete=True,
            computation_complete=False,
            calculation_incomplete=True,
            original_calculation_failure_reason=failure_reason,
            original_calculation_answer_or_guess=calc.answer,
            original_calculation_answer_source=calc.metadata.get("answer_source"),
            primary_solver="calculation",
            primary_answer=calc.answer,
            primary_computation_complete=False,
            primary_error=failure_reason,
            fallback_solver="cross_doc",
            fallback_answer=fallback.answer,
            answer_source=fallback_answer_source,
            risk_label="calculation_incomplete",
        )

    @staticmethod
    def _check_option_consistency(
        bundle: EvidenceBundle, result: SolverResult,
    ) -> Optional[bool]:
        """Validate computed named amounts against the selected option when possible.

        Returns True when at least one named amount is checked and all checked
        values agree, False on a mismatch, and None when the option text does
        not expose comparable named amounts.
        """
        computed = result.metadata.get("computed_values")
        option_text = str(bundle.question.options.get(result.answer, ""))
        if not isinstance(computed, Mapping) or not computed or not option_text:
            return None

        checked = 0
        for label, raw_value in computed.items():
            label_text = str(label).strip()
            if not label_text or label_text not in option_text:
                continue
            tail = option_text.split(label_text, 1)[1]
            match = re.search(r"[（(]\s*([0-9]+(?:\.[0-9]+)?)\s*(万)?", tail)
            if match is None:
                continue
            expected = float(match.group(1)) * (10000.0 if match.group(2) else 1.0)
            try:
                actual = float(raw_value)
            except (TypeError, ValueError):
                return False
            checked += 1
            tolerance = max(1.0, abs(expected) * 1e-6)
            if abs(actual - expected) > tolerance:
                return False
        return True if checked else None

    def _delegate(self, key: str, bundle: EvidenceBundle, composite_type: str) -> SolverResult:
        solver = self.solvers.get(key)
        if solver is None:
            raise KeyError(f"missing composite delegate: {key} for {composite_type}")
        return solver.solve(bundle)

    @staticmethod
    def _annotate(
        result: SolverResult,
        composite_type: str,
        selected_delegate: str,
        fallback_used: bool,
        **extra: object,
    ) -> SolverResult:
        metadata = dict(result.metadata)
        metadata.update({
            "composite_route_enabled": True,
            "composite_type": composite_type,
            "composite_selected_delegate": selected_delegate,
            "composite_fallback_used": fallback_used,
            **extra,
        })
        return replace(result, solver=f"composite:{selected_delegate}", metadata=metadata)
