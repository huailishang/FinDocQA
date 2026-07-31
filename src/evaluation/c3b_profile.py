"""C3-B Formula Context Recovery reliability profile."""
from __future__ import annotations

from evaluation.profiles import ReliabilityProfile

C3B_MODULE_ID = "c3_formula_context_recovery"

C3B_REQUIRED_INVARIANTS = (
    "ambiguity_must_not_execute",
    "unresolved_dependency_must_not_pass",
    "cross_document_recovery_forbidden",
    "unrelated_evidence_must_not_change_decision",
    "incomplete_lineage_must_not_execute",
)


def build_c3b_reliability_profile() -> ReliabilityProfile:
    """Return the first HIGH-risk profile used to validate the generic core."""

    return ReliabilityProfile(
        module_id=C3B_MODULE_ID,
        risk_level="HIGH",
        failure_modes=(
            "ambiguous_formula_accepted",
            "unresolved_dependency_accepted",
            "cross_document_recovery",
            "physical_neighbor_as_binding",
            "incomplete_lineage_accepted",
        ),
        required_invariants=C3B_REQUIRED_INVARIANTS,
        test_techniques=(
            "decision_table",
            "combinatorial_2way",
            "selected_3way",
            "property_based",
            "metamorphic",
            "mutation_for_critical_gates",
        ),
        gate_policy={
            "missing_required_invariant": "FAIL",
        },
    )
