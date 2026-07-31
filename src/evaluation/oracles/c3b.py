"""C3-B safety oracles and bounded Decision Table V1.

This module interprets C3-B outcomes for offline evaluation only.  It does not
participate in FormulaContextRecovery and never changes business results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping

from calculation.contracts import FormulaGateStatus
from calculation.material import LocalContextVariableBinder
from calculation.recovery import FormulaRecoveryResult

_AMBIGUITY_PREFIXES = (
    "canonical_formula_not_unique:",
    "formula_block_not_unique",
    "formula_page_not_unique",
    "linked_table_ambiguous",
    "adjacent_page_continuation_not_unique",
)
_UNRESOLVED_PREFIXES = (
    "missing_variable_binding:",
    "linked_table_missing",
    "linked_table_not_found",
    "linked_table_ambiguous",
    "adjacent_page_continuation_not_found",
    "adjacent_page_continuation_not_unique",
    "unsupported_explicit_footnote_linkage",
)
_LINEAGE_PREFIXES = (
    "recovery_lineage_missing:",
    "source_lineage_missing",
)

@dataclass(frozen=True)
class C3BSafetyExpectation:
    """Evaluation-only dangerous facts, independent of production diagnostics."""

    ambiguity_expected: bool = False
    unresolved_dependency_expected: bool = False
    cross_document_expected: bool = False
    lineage_complete_expected: bool = True


@dataclass(frozen=True)
class C3BEvidenceFingerprint:
    status: str
    ready_for_execution: bool
    recovered_source_refs: tuple[tuple[str, int | None, str, str], ...]
    variable_binding_identity: tuple[tuple[str, str], ...]


def c3b_evidence_fingerprint(result: FormulaRecoveryResult) -> C3BEvidenceFingerprint:
    """Return stable source/binding identity without snapshotting free-form text."""

    expression = result.recovered_evidence.normalized_expression
    variables = {
        token
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression)
        if token not in {"min", "max", "abs", "round"}
    }
    formula_blocks = {
        str(result.recovered_evidence.metadata.get("formula_id") or ""),
    }
    relevant_refs = (
        ref
        for ref in result.recovered_source_refs
        if ref.block_id in formula_blocks
        or any(re.search(rf"\b{re.escape(name)}\b", ref.excerpt) for name in variables)
    )
    refs = tuple(
        sorted(
            (ref.doc_id, ref.page_number, ref.source, ref.block_id)
            for ref in relevant_refs
        )
    )
    resolved_bindings = LocalContextVariableBinder().bind(result.recovered_evidence)
    bindings = tuple(
        sorted(
            (
                str(name),
                "|".join(
                    (
                        str(binding.value),
                        str(binding.unit),
                        str(binding.source_ref.doc_id if binding.source_ref else ""),
                        str(binding.source_ref.page_number if binding.source_ref else ""),
                        str(binding.source_ref.block_id if binding.source_ref else ""),
                    )
                ),
            )
            for name, binding in resolved_bindings.items()
        )
    )
    return C3BEvidenceFingerprint(
        status=result.status.value,
        ready_for_execution=result.ready_for_execution,
        recovered_source_refs=refs,
        variable_binding_identity=bindings,
    )


def _has_reason(result: FormulaRecoveryResult, prefixes: tuple[str, ...]) -> bool:
    return any(
        any(str(reason).startswith(prefix) for prefix in prefixes)
        for reason in result.reasons
    )


def _same_decision(left: FormulaRecoveryResult, right: FormulaRecoveryResult) -> bool:
    return c3b_evidence_fingerprint(left) == c3b_evidence_fingerprint(right)


def evaluate_c3b_invariants(
    result: FormulaRecoveryResult,
    *,
    baseline_result: FormulaRecoveryResult | None = None,
    expectation: C3BSafetyExpectation | None = None,
) -> dict[str, bool]:
    """Evaluate C3-B critical safety invariants from an immutable recovery result."""

    diagnostic_ambiguous = _has_reason(result, _AMBIGUITY_PREFIXES)
    diagnostic_unresolved = _has_reason(result, _UNRESOLVED_PREFIXES)
    diagnostic_cross_document = (
        "cross_document_recovery_forbidden" in result.reasons
        or len({ref.doc_id for ref in result.recovered_source_refs if ref.doc_id}) > 1
    )
    diagnostic_lineage_missing = _has_reason(result, _LINEAGE_PREFIXES)
    ambiguous = expectation.ambiguity_expected if expectation else diagnostic_ambiguous
    unresolved = (
        expectation.unresolved_dependency_expected if expectation else diagnostic_unresolved
    )
    cross_document = (
        expectation.cross_document_expected if expectation else diagnostic_cross_document
    )
    lineage_missing = (
        not expectation.lineage_complete_expected if expectation else diagnostic_lineage_missing
    )

    return {
        "ambiguity_must_not_execute": not (ambiguous and result.ready_for_execution),
        "unresolved_dependency_must_not_pass": not (
            unresolved
            and (result.status is FormulaGateStatus.PASS or result.ready_for_execution)
        ),
        "cross_document_recovery_forbidden": not (
            cross_document
            and (result.status is FormulaGateStatus.PASS or result.ready_for_execution)
        ),
        "unrelated_evidence_must_not_change_decision": (
            True if baseline_result is None else _same_decision(baseline_result, result)
        ),
        "incomplete_lineage_must_not_execute": not (
            lineage_missing and result.ready_for_execution
        ),
    }


@dataclass(frozen=True)
class C3BDecisionInput:
    formula_count: str
    formula_identity: str
    lineage: str
    variable_binding: str
    cross_document: str
    linked_table: str
    continuation: str

    def __post_init__(self) -> None:
        allowed = {
            "formula_count": {"0", "1", "2+"},
            "formula_identity": {"unique", "ambiguous"},
            "lineage": {"complete", "missing"},
            "variable_binding": {"complete", "incomplete"},
            "cross_document": {"no", "yes"},
            "linked_table": {"none", "unique", "ambiguous"},
            "continuation": {"none", "unique", "ambiguous"},
        }
        for name, values in allowed.items():
            value = str(getattr(self, name))
            if value not in values:
                raise ValueError(f"unsupported {name}: {value}")
        if self.formula_identity == "ambiguous" and self.formula_count != "2+":
            raise ValueError("ambiguous formula identity requires formula_count=2+")


@dataclass(frozen=True)
class C3BDecisionExpectation:
    status: FormulaGateStatus
    ready_for_execution: bool
    invariant_results: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class C3BDecisionRow:
    case_id: str
    inputs: C3BDecisionInput
    expected: C3BDecisionExpectation


_ALL_SAFE_INVARIANTS = {
    "ambiguity_must_not_execute": True,
    "unresolved_dependency_must_not_pass": True,
    "cross_document_recovery_forbidden": True,
    "unrelated_evidence_must_not_change_decision": True,
    "incomplete_lineage_must_not_execute": True,
}


def evaluate_c3b_decision(inputs: C3BDecisionInput) -> C3BDecisionExpectation:
    """Small deterministic oracle for the first C3-B input-space model."""

    if inputs.cross_document == "yes":
        status = FormulaGateStatus.FAIL
    elif inputs.formula_identity == "ambiguous":
        status = FormulaGateStatus.REVIEW
    elif inputs.lineage == "missing":
        status = FormulaGateStatus.REVIEW
    elif inputs.variable_binding == "incomplete":
        status = FormulaGateStatus.REVIEW
    elif inputs.linked_table == "ambiguous":
        status = FormulaGateStatus.REVIEW
    elif inputs.continuation == "ambiguous":
        status = FormulaGateStatus.REVIEW
    else:
        status = FormulaGateStatus.PASS

    return C3BDecisionExpectation(
        status=status,
        ready_for_execution=status is FormulaGateStatus.PASS,
        invariant_results=dict(_ALL_SAFE_INVARIANTS),
    )


C3B_DECISION_TABLE_V1 = (
    C3BDecisionRow(
        "zero_formula_block_context",
        C3BDecisionInput("0", "unique", "complete", "complete", "no", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.PASS, True, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "single_formula_safe",
        C3BDecisionInput("1", "unique", "complete", "complete", "no", "unique", "unique"),
        C3BDecisionExpectation(FormulaGateStatus.PASS, True, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "duplicate_formula_ambiguous",
        C3BDecisionInput("2+", "ambiguous", "complete", "complete", "no", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "lineage_missing",
        C3BDecisionInput("1", "unique", "missing", "complete", "no", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "binding_incomplete",
        C3BDecisionInput("1", "unique", "complete", "incomplete", "no", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "cross_document",
        C3BDecisionInput("1", "unique", "complete", "complete", "yes", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.FAIL, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "linked_table_ambiguous",
        C3BDecisionInput("1", "unique", "complete", "complete", "no", "ambiguous", "none"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "continuation_ambiguous",
        C3BDecisionInput("1", "unique", "complete", "complete", "no", "none", "ambiguous"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
)


# Greedy pairwise covering array over the valid V1 input space.  It covers all
# valid factor-value pairs in 12 rows instead of the 288-row constrained product.
C3B_PAIRWISE_CASES_V1 = (
    C3BDecisionInput("0", "unique", "complete", "complete", "no", "none", "none"),
    C3BDecisionInput("1", "unique", "missing", "incomplete", "yes", "unique", "unique"),
    C3BDecisionInput("2+", "ambiguous", "complete", "complete", "yes", "ambiguous", "ambiguous"),
    C3BDecisionInput("2+", "ambiguous", "missing", "incomplete", "no", "none", "none"),
    C3BDecisionInput("0", "unique", "missing", "incomplete", "no", "ambiguous", "ambiguous"),
    C3BDecisionInput("2+", "ambiguous", "complete", "complete", "no", "unique", "unique"),
    C3BDecisionInput("1", "unique", "complete", "complete", "no", "none", "ambiguous"),
    C3BDecisionInput("0", "unique", "complete", "incomplete", "yes", "none", "unique"),
    C3BDecisionInput("1", "unique", "missing", "complete", "yes", "ambiguous", "none"),
    C3BDecisionInput("0", "unique", "complete", "complete", "no", "unique", "none"),
    C3BDecisionInput("2+", "unique", "complete", "complete", "no", "unique", "ambiguous"),
    C3BDecisionInput("0", "unique", "complete", "complete", "no", "ambiguous", "unique"),
)


C3B_SELECTED_3WAY_CASES_V1 = (
    C3BDecisionRow(
        "identity_binding_lineage",
        C3BDecisionInput("2+", "ambiguous", "missing", "incomplete", "no", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "identity_table_continuation",
        C3BDecisionInput("2+", "ambiguous", "complete", "complete", "no", "ambiguous", "ambiguous"),
        C3BDecisionExpectation(FormulaGateStatus.REVIEW, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
    C3BDecisionRow(
        "crossdoc_binding_lineage",
        C3BDecisionInput("1", "unique", "missing", "incomplete", "yes", "none", "none"),
        C3BDecisionExpectation(FormulaGateStatus.FAIL, False, dict(_ALL_SAFE_INVARIANTS)),
    ),
)
