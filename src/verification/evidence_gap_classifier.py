"""Classify financial claim gaps before any retrieval is attempted."""
from __future__ import annotations

from typing import Iterable, Sequence

from evidence_completion.contracts import EvidenceGap, GapClass
from verification.financial_claim_ast import FinancialClaimSpec

_SEMANTIC_ATOMS = {
    "entity", "metric", "relation", "comparator", "multiplier",
    "statement_scope", "attribution_scope",
}
_EVIDENCE_ATOMS = {
    "current_value", "comparison_value", "current_period", "comparison_period",
    "unit", "policy_stage",
}
_LINEAGE_ATOMS = {"canonical_source", "local_window", "source_lineage"}
_CONTRACT_ATOMS = {"option_contract", "answer_contract"}

_FAILURE_TO_ATOM = {
    "entity_unparsed": "entity",
    "metric_unparsed": "metric",
    "comparator_unparsed": "relation",
    "multiplier_unparsed": "multiplier",
    "comparison_entity_unparsed": "entity",
    "comparison_metric_unparsed": "metric",
    "comparison_value_unparsed": "current_value",
    "current_period_unparsed": "current_period",
    "comparison_period_unparsed": "comparison_period",
    "policy_stage_unparsed": "policy_stage",
    "statement_scope_unparsed": "statement_scope",
    "attribution_scope_unparsed": "attribution_scope",
    "unit_unparsed": "unit",
    "declared_document_binding_missing": "source_lineage",
}


def _make(atom: str, source: str, reason: str) -> EvidenceGap:
    if atom in _SEMANTIC_ATOMS:
        return EvidenceGap(atom, GapClass.SEMANTIC, reason, False, source)
    if atom in _EVIDENCE_ATOMS:
        return EvidenceGap(atom, GapClass.EVIDENCE, reason, True, source)
    if atom in _LINEAGE_ATOMS:
        return EvidenceGap(atom, GapClass.LINEAGE, reason, True, source)
    if atom in _CONTRACT_ATOMS:
        return EvidenceGap(atom, GapClass.CONTRACT, reason, False, source)
    if "conflict" in atom:
        return EvidenceGap(atom, GapClass.CONFLICT, reason, False, source)
    return EvidenceGap(atom, GapClass.EVIDENCE, reason, True, source)


def classify_financial_gaps(
    claim_spec: FinancialClaimSpec,
    missing_atoms: Sequence[str],
    conflicting_atoms: Sequence[str] = (),
) -> tuple[EvidenceGap, ...]:
    gaps: list[EvidenceGap] = []
    seen: set[tuple[str, str]] = set()
    for atom in missing_atoms:
        atom = str(atom)
        if atom == "current_period" and not claim_spec.current_period:
            gap = EvidenceGap(atom, GapClass.SEMANTIC, "claim_period_not_parsed", False, "claim_ast")
        elif atom == "comparison_period" and not claim_spec.comparison_period:
            gap = EvidenceGap(atom, GapClass.SEMANTIC, "comparison_period_not_parsed", False, "claim_ast")
        elif (
            atom == "unit"
            and not claim_spec.value_unit
            and claim_spec.value is not None
            and claim_spec.relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"}
        ):
            gap = EvidenceGap(atom, GapClass.SEMANTIC, "claim_unit_not_parsed", False, "claim_ast")
        elif atom == "policy_stage" and not claim_spec.policy_stage:
            gap = EvidenceGap(atom, GapClass.SEMANTIC, "policy_stage_not_parsed", False, "claim_ast")
        else:
            gap = _make(atom, "sufficiency", f"missing_atom:{atom}")
        key = (gap.atom, gap.gap_class.value)
        if key not in seen:
            seen.add(key)
            gaps.append(gap)
    for failure in claim_spec.parse_failures:
        atom = _FAILURE_TO_ATOM.get(str(failure), str(failure))
        if any(existing.atom == atom for existing in gaps):
            continue
        gap = _make(atom, "claim_ast", f"parse_failure:{failure}")
        key = (gap.atom, gap.gap_class.value)
        if key not in seen:
            seen.add(key)
            gaps.append(gap)
    for conflict in conflicting_atoms:
        gap = EvidenceGap(str(conflict), GapClass.CONFLICT, f"conflict:{conflict}", False, "evidence")
        key = (gap.atom, gap.gap_class.value)
        if key not in seen:
            seen.add(key)
            gaps.append(gap)
    return tuple(gaps)


def retrievable_atoms(gaps: Iterable[EvidenceGap]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(gap.atom for gap in gaps if gap.retrievable))
