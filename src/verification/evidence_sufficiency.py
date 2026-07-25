"""Explicit information-sufficiency contracts for financial claims."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from verification.claim_fact_binding import assess_claim_fact_bindings
from verification.financial_claim_ast import FinancialClaimSpec

SCHEMA_VERSION = "financial_evidence_sufficiency_v1"


@dataclass(frozen=True)
class FinancialEvidenceSufficiency:
    schema_version: str
    claim_spec: Mapping[str, Any]
    required_atoms: tuple[str, ...]
    resolved_atoms: tuple[str, ...]
    missing_atoms: tuple[str, ...]
    conflicting_atoms: tuple[str, ...]
    unsupported_semantics: tuple[str, ...]
    required_doc_ids: tuple[str, ...]
    used_doc_ids: tuple[str, ...]
    source_lineage: tuple[str, ...]
    claim_fact_binding: Mapping[str, Any]
    binding_safe_for_formula: bool
    binding_safe_for_override: bool
    claim_ast_complete: bool
    declared_doc_boundary_pass: bool
    formula_complete: bool
    option_contract_valid: bool
    is_sufficient: bool
    safe_to_decide: bool
    safe_to_override: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evidence_mapping(evidence: Any) -> Mapping[str, Any]:
    if isinstance(evidence, Mapping):
        return evidence
    to_dict = getattr(evidence, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {}


def _source_facts(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = payload.get("source_facts") or []
    return tuple(item for item in raw if isinstance(item, Mapping))


def _used_doc_ids(payload: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    values = [str(item.get("doc_id") or "") for item in facts]
    values.extend(str(value) for value in payload.get("document_scope") or [])
    return tuple(dict.fromkeys(value for value in values if value))


def _lineage(payload: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    values = [str(item.get("canonical_source") or "") for item in facts]
    values.extend(str(value) for value in payload.get("canonical_sources") or [])
    return tuple(dict.fromkeys(value for value in values if value))


def _fact_periods(facts: Sequence[Mapping[str, Any]]) -> set[str]:
    periods: set[str] = set()
    for fact in facts:
        period = str(fact.get("period_scope") or "")
        if period:
            periods.add(period)
        metadata = fact.get("metadata") or {}
        if isinstance(metadata, Mapping):
            comparison = str(metadata.get("comparison_period") or "")
            if comparison:
                periods.add(comparison)
    return periods


def assess_financial_evidence_sufficiency(
    claim_spec: FinancialClaimSpec,
    evidence: Any,
    *,
    declared_doc_ids: Sequence[str],
    option_contract_valid: bool = True,
) -> FinancialEvidenceSufficiency:
    payload = _evidence_mapping(evidence)
    facts = _source_facts(payload)
    used_docs = _used_doc_ids(payload, facts)
    lineage = _lineage(payload, facts)
    formula = str(payload.get("formula_or_aggregation") or payload.get("reason") or "").strip()
    status = str(payload.get("status") or "unresolved")
    evidence_conflicts = [str(value) for value in payload.get("conflicts") or []]
    binding = assess_claim_fact_bindings(claim_spec, facts)
    bindings = [row for row in binding.get("bindings") or [] if isinstance(row, Mapping)]
    correct = [row for row in bindings if row.get("binding_status") == "correct"]
    correct_roles = {str(row.get("role") or "") for row in correct}

    resolved: set[str] = set()
    # Semantic atoms are resolved by the ClaimSpec. Evidence atoms are resolved
    # only by facts that passed the same Claim-Fact binding gate.
    if claim_spec.entity_refs:
        resolved.add("entity")
    if claim_spec.metric:
        resolved.add("metric")
    if claim_spec.relation:
        resolved.update(("relation", "comparator"))
    if claim_spec.multiplier is not None:
        resolved.add("multiplier")
    # Scope in the ClaimSpec is a semantic requirement, so its presence keeps
    # retrieval open. Whether each fact matches that scope is enforced by the
    # binding conflict/safety fields below.
    if claim_spec.statement_scope:
        resolved.add("statement_scope")
    if claim_spec.attribution_scope:
        resolved.add("attribution_scope")
    if claim_spec.current_period and any(
        role.startswith("current")
        or role in {"numerator", "current", "policy_evidence", "component_numerator", "component_denominator"}
        for role in correct_roles
    ):
        resolved.add("current_period")
    if any(role.startswith("prior") for role in correct_roles):
        resolved.add("comparison_period")
    if any(row.get("normalized_fact", {}).get("value") not in (None, "") for row in correct):
        resolved.add("current_value")
    if any(role.startswith("peer") or role.startswith("prior") or role == "denominator" for role in correct_roles):
        resolved.add("comparison_value")
    if correct and not binding.get("missing_slot_ids") and all(
        row.get("unit_match") in {"match", "not_required"}
        and row.get("unit_family_match") in {"match", "not_required"}
        for row in correct
    ):
        resolved.add("unit")
    if claim_spec.policy_stage and any(row.get("policy_stage_match") == "match" for row in correct):
        resolved.add("policy_stage")

    required_values = list(claim_spec.required_atoms)
    if facts:
        required_values.extend(("canonical_source", "local_window"))
        if correct and all(row.get("source_lineage_present") == "match" for row in correct):
            resolved.update(("canonical_source", "local_window"))
    required = tuple(dict.fromkeys(required_values))
    missing = sorted(atom for atom in required if atom not in resolved)

    binding_conflicts = [
        f"claim_fact_binding:{failure}"
        for row in bindings
        if row.get("binding_status") == "conflict"
        for failure in row.get("binding_failures") or []
    ]
    binding_conflicts.extend(str(value) for value in binding.get("cross_operand_conflicts") or [])
    conflicting = sorted(set([*evidence_conflicts, *binding_conflicts]))

    declared = set(str(value) for value in declared_doc_ids)
    required_docs = set(claim_spec.required_doc_ids)
    boundary_pass = bool(
        not used_docs
        or (
            set(used_docs) <= declared
            and (not required_docs or set(used_docs) <= required_docs)
        )
    )
    formula_complete = bool(
        formula
        and claim_spec.relation
        and "unresolved" not in formula.lower()
        and "missing" not in formula.lower()
        and status in {"supported", "contradicted"}
    )
    ast_complete = claim_spec.complete
    unsupported = tuple(claim_spec.unsupported_semantics)
    binding_safe = binding.get("safe_for_override") is True
    sufficient = bool(
        ast_complete
        and not missing
        and not conflicting
        and binding_safe
        and boundary_pass
        and formula_complete
        and option_contract_valid
        and lineage
    )
    return FinancialEvidenceSufficiency(
        schema_version=SCHEMA_VERSION,
        claim_spec=claim_spec.to_dict(),
        required_atoms=required,
        resolved_atoms=tuple(sorted(resolved)),
        missing_atoms=tuple(missing),
        conflicting_atoms=tuple(conflicting),
        unsupported_semantics=unsupported,
        required_doc_ids=tuple(claim_spec.required_doc_ids),
        used_doc_ids=used_docs,
        source_lineage=lineage,
        claim_fact_binding=binding,
        binding_safe_for_formula=binding.get("safe_for_formula") is True,
        binding_safe_for_override=binding_safe,
        claim_ast_complete=ast_complete,
        declared_doc_boundary_pass=boundary_pass,
        formula_complete=formula_complete,
        option_contract_valid=option_contract_valid,
        is_sufficient=sufficient,
        safe_to_decide=sufficient,
        safe_to_override=sufficient,
    )
