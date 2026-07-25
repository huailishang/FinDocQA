"""Route evidence bundles to the appropriate solver."""

from __future__ import annotations

from typing import Mapping

from contracts import EvidenceBundle, QuestionLabel, Solver, SolverResult


class RoutedSolver:
    name = "routed"

    def __init__(
        self,
        solvers: Mapping[str, Solver],
        default_solver: Solver,
        *,
        composite_solver: Solver | None = None,
        enable_composite_routes: bool = False,
    ) -> None:
        self.solvers = solvers
        self.default_solver = default_solver
        self.composite_solver = composite_solver
        self.enable_composite_routes = bool(enable_composite_routes)

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        labels = set(bundle.classification.labels)

        if (
            self.enable_composite_routes
            and self.composite_solver is not None
            and QuestionLabel.CALCULATION in labels
            and (QuestionLabel.MULTI_OPTION in labels or QuestionLabel.CROSS_DOC in labels)
        ):
            return self.composite_solver.solve(bundle)

        # Multi-select has no partial credit, so it gets the most conservative
        # option-by-option solver even when the question is also cross-doc or numeric.
        if QuestionLabel.MULTI_OPTION in labels and "multi_choice" in self.solvers:
            return self.solvers["multi_choice"].solve(bundle)
        if QuestionLabel.CALCULATION in labels and "calculation" in self.solvers:
            return self.solvers["calculation"].solve(bundle)
        if QuestionLabel.CROSS_DOC in labels and "cross_doc" in self.solvers:
            return self.solvers["cross_doc"].solve(bundle)
        return self.default_solver.solve(bundle)
