"""Route evidence bundles to the appropriate solver."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from contracts import EvidenceBundle, QuestionLabel, Solver, SolverResult


_SCOPE_IDENTITY_KINDS = {
    "entity_year",
    "contract_issuer",
    "insurance_product",
    "explicit_document_reference",
    "regulatory_title_or_object",
}


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
        scope_supported_cross_doc = self._scope_supported_cross_doc(bundle)

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
        if (
            (QuestionLabel.CROSS_DOC in labels or scope_supported_cross_doc)
            and "cross_doc" in self.solvers
        ):
            return self.solvers["cross_doc"].solve(bundle)
        return self.default_solver.solve(bundle)

    @staticmethod
    def _scope_supported_cross_doc(bundle: EvidenceBundle) -> bool:
        """Use resolved identity slots to disambiguate natural-language comparisons.

        C1 intentionally avoids guessing that every use of ``比较/和/与`` means
        multiple documents.  After Document Scope has run, however, two distinct
        high-confidence identity groups are strong deterministic evidence that a
        real open question needs cross-document reasoning.
        """
        question = bundle.question
        if str(question.raw.get("_input_adapter") or "") != "canonical_question_v1":
            return False
        understanding = question.raw.get("_query_understanding")
        if not isinstance(understanding, Mapping):
            return False
        raw_traits = understanding.get("traits")
        traits = {
            str(value)
            for value in raw_traits
            if str(value)
        } if isinstance(raw_traits, Sequence) and not isinstance(raw_traits, (str, bytes)) else set()
        if not ({"comparison", "ranking"} & traits):
            return False

        groups: list[Mapping[str, Any]] = []
        for candidate in bundle.candidates:
            raw_groups = candidate.metadata.get("document_scope_coverage_groups")
            if not isinstance(raw_groups, Sequence) or isinstance(raw_groups, (str, bytes)):
                continue
            groups = [dict(item) for item in raw_groups if isinstance(item, Mapping)]
            if groups:
                break

        identities: set[str] = set()
        covered_docs: set[str] = set()
        for group in groups:
            if str(group.get("kind") or "") not in _SCOPE_IDENTITY_KINDS:
                continue
            identity = str(group.get("identity") or group.get("group_key") or "").strip()
            raw_docs = group.get("doc_ids")
            docs = {
                str(value)
                for value in raw_docs
                if str(value)
            } if isinstance(raw_docs, Sequence) and not isinstance(raw_docs, (str, bytes)) else set()
            if identity and docs:
                identities.add(identity)
                covered_docs.update(docs)
        return len(identities) >= 2 and len(covered_docs) >= 2
