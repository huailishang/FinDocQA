"""Strict attribution gate for CRAG recoveries.

A retrieval recovery is counted only when the evidence returned by the current
CRAG run is itself sufficient to close the missing fact.  A downstream verifier
may reconfirm the verdict, but facts fetched from a separate full-context ledger
cannot retroactively turn merely relevant CRAG context into a CRAG recovery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


PASS = "PASS"
FAIL = "FAIL"


@dataclass(frozen=True)
class EvidenceSufficiencyDecision:
    quality: str
    missing_requirements: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strict_fact_audit_pass(audit: Mapping[str, Any]) -> bool:
    """Require a retrieved/rebound source to pass the actual strict fact gate."""
    strict = dict(audit.get("strict_fact_audit") or audit)
    return bool(
        strict.get("fact_gate_pass")
        and strict.get("semantic_binding_pass")
        and strict.get("metric_value_unit_role_entailment")
        and strict.get("source_local_provenance", True)
    )


def canonical_rebinding_strict_pass(audits: Sequence[Mapping[str, Any]]) -> bool:
    """A source match/rebound alone is insufficient; every decisive fact must pass."""
    rows = [dict(row) for row in audits]
    return bool(rows) and all(bool(row.get("rebound")) and strict_fact_audit_pass(row) for row in rows)


def evidence_sufficiency_quality(
    *,
    retrieval_relevance_quality: str,
    initial_missing_fields: Sequence[str],
    crag_bound_missing_fields: Sequence[str],
    required_key_coverage: Mapping[str, bool],
    strict_fact_audits: Sequence[Mapping[str, Any]] = (),
    canonical_rebinding_required: bool = False,
    absence_claim: bool = False,
    absence_proof_valid: bool = False,
    wrong_entity: bool = False,
    wrong_document: bool = False,
    wrong_field: bool = False,
    wrong_period: bool = False,
) -> EvidenceSufficiencyDecision:
    """Grade evidence sufficiency after retrieval relevance has been established."""
    missing: list[str] = []
    reasons: list[str] = []
    if str(retrieval_relevance_quality).upper() != "CORRECT":
        missing.append("retrieval_relevance_quality")
    if wrong_entity or wrong_document or wrong_field or wrong_period:
        missing.append("binding_integrity")
        reasons.append("retrieved evidence binds a wrong entity/document/field/period")

    initial = {str(value) for value in initial_missing_fields if str(value)}
    bound = {str(value) for value in crag_bound_missing_fields if str(value)}
    if not initial:
        missing.append("initial_missing_fields")
    if initial and not (initial & bound):
        missing.append("crag_bound_missing_field")
        reasons.append("CRAG evidence did not itself bind any originally missing field")

    for name, covered in required_key_coverage.items():
        if not bool(covered):
            missing.append(str(name))

    if canonical_rebinding_required and not canonical_rebinding_strict_pass(strict_fact_audits):
        missing.append("canonical_rebinding_strict_fact_gate")
        reasons.append("rebound source failed strict fact audit")

    if absence_claim and not absence_proof_valid:
        missing.append("scope_absence_proof")
        reasons.append("absence/specific-value-presence claim lacks complete-scope negative proof")

    if missing:
        return EvidenceSufficiencyDecision(FAIL, tuple(dict.fromkeys(missing)), tuple(reasons))
    return EvidenceSufficiencyDecision(PASS, (), ("CRAG evidence itself closes all decisive evidence requirements",))


def strict_crag_recovery_decision(
    *,
    initial_status: str,
    initial_missing_fields: Sequence[str],
    rewritten_query_executed: bool,
    directed_child_hit: bool,
    parent_triggered_by_child_hit: bool,
    evidence_sufficiency: Mapping[str, Any],
    crag_bound_missing_fields: Sequence[str],
    final_status: str,
) -> dict[str, Any]:
    """Return strict recovery attribution without consulting full-context facts."""
    checks = {
        "initial_unresolved": str(initial_status).upper() == "UNRESOLVED",
        "initial_missing_fields_nonempty": bool(initial_missing_fields),
        "rewritten_query_executed": bool(rewritten_query_executed),
        "directed_child_hit": bool(directed_child_hit),
        "parent_triggered_by_child_hit": bool(parent_triggered_by_child_hit),
        "evidence_sufficiency_pass": str(evidence_sufficiency.get("quality") or "").upper() == PASS,
        "crag_bound_original_missing_field": bool(set(map(str, initial_missing_fields)) & set(map(str, crag_bound_missing_fields))),
        "final_status_decisive": str(final_status).lower() in {"supported", "contradicted"},
    }
    success = all(checks.values())
    return {
        "strict_crag_recovery_success": success,
        "checks": checks,
        "attribution": "STRICT_CRAG_RECOVERY_SUCCESS" if success else "DOMAIN_VERIFIER_RECONFIRM_WITH_CRAG_RELEVANT_CONTEXT",
    }


def structured_false_match_proof(
    *,
    qid: str,
    label: str,
    required_binding: Mapping[str, Any],
    old_binding: Mapping[str, Any],
    source_lineage: Sequence[Mapping[str, Any]],
    allowed_dimensions: Sequence[str],
) -> dict[str, Any]:
    """Prove a false match only from explicit binding field differences.

    Free-form reason strings are intentionally ignored.
    """
    mismatches: list[str] = []
    structured: dict[str, Any] = {}
    for dimension in allowed_dimensions:
        required_value = required_binding.get(dimension)
        old_value = old_binding.get(dimension)
        if required_value is None or required_value == "" or required_value == [] or required_value == {}:
            continue
        if old_value is None or old_value == "" or old_value == [] or old_value == {}:
            continue
        if required_value != old_value:
            mismatches.append(str(dimension))
            structured[str(dimension)] = {
                "required": required_value,
                "old": old_value,
            }
    proven = bool(mismatches and source_lineage)
    return {
        "qid": qid,
        "label": label,
        "required_binding": dict(required_binding),
        "old_binding": dict(old_binding),
        "mismatched_dimension": mismatches,
        "structured_proof": structured,
        "source_lineage": [dict(row) for row in source_lineage],
        "proven_false_match": proven,
        "classification": "PROVEN_FALSE_MATCH" if proven else "REBINDING_IMPROVEMENT_NOT_PROVEN_FALSE_MATCH",
    }


__all__ = [
    "PASS",
    "FAIL",
    "EvidenceSufficiencyDecision",
    "strict_fact_audit_pass",
    "canonical_rebinding_strict_pass",
    "evidence_sufficiency_quality",
    "strict_crag_recovery_decision",
    "structured_false_match_proof",
]
