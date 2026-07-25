"""Deterministic CRAG-style grading for local evidence hits."""
from __future__ import annotations

from typing import Any, Mapping

from evidence_completion.contracts import (
    EvidenceGrade,
    EvidenceRequest,
    GradedEvidenceHit,
)
from verification.claim_fact_binding import (
    normalize_attribution_scope,
    normalize_statement_scope,
    normalize_unit,
    per_share_basis_from_unit,
    unit_family,
    units_compatible,
)

_CRITICAL = {
    "declared_doc_match", "entity_match", "metric_match", "period_match",
    "statement_scope_match", "attribution_scope_match",
}


def _match(expected: str, actual: str, *, optional: bool = False) -> str:
    expected = str(expected or "").strip()
    actual = str(actual or "").strip()
    if not expected:
        return "not_required"
    if not actual or actual in {"unknown", "unknown_scope"}:
        return "missing" if not optional else "unknown"
    if expected == actual:
        return "match"
    attribution_family = {"parent_attributable", "listed_company_shareholders_attributable"}
    if expected in attribution_family and actual in attribution_family:
        return "match"
    return "conflict"


def grade_evidence_hit(
    request: EvidenceRequest,
    hit: Mapping[str, Any],
    candidate_fact: Mapping[str, Any] | None,
) -> GradedEvidenceHit:
    fact = dict(candidate_fact or {})
    metadata = dict(fact.get("metadata") or {})
    actual_unit = str(fact.get("unit") or fact.get("normalized_unit") or "")
    actual_family = unit_family(actual_unit)
    expected_family = str(request.expected_unit_family or "")
    unit_match = (
        "not_required" if not request.unit_compatibility_required
        else "missing" if not actual_unit
        else "match" if units_compatible(
            request.unit_expectation,
            actual_unit,
            expected_family=expected_family,
        )
        else "conflict"
    )
    family_match = (
        "not_required" if not request.unit_compatibility_required or not expected_family
        else "missing" if actual_family == "unknown"
        else "match" if actual_family == expected_family
        else "conflict"
    )
    actual_statement = normalize_statement_scope(fact.get("statement_scope"))
    actual_attribution = normalize_attribution_scope(fact.get("attribution_scope"))
    actual_basis = per_share_basis_from_unit(actual_unit, metadata)
    expected_basis = str(request.per_share_basis_expectation or "").strip()
    basis_match = (
        "not_required"
        if expected_basis in {"", "not_applicable"}
        else "unknown"
        if not actual_unit and not str(metadata.get("per_share_basis") or "").strip()
        else _match(expected_basis, actual_basis, optional=True)
    )
    dimensions = {
        "declared_doc_match": "match" if str(hit.get("doc_id") or "") in set(request.allowed_doc_ids) else "conflict",
        "entity_match": _match(request.entity, str(fact.get("entity") or fact.get("entity_name") or "")),
        "metric_match": _match(request.metric, str(fact.get("metric") or "")),
        "period_match": _match(request.period, str(fact.get("period") or "")),
        "comparison_period_match": _match(
            request.comparison_period,
            str(fact.get("comparison_period") or metadata.get("comparison_period") or ""),
            optional=True,
        ),
        "unit_match": unit_match,
        "unit_family_match": family_match,
        "statement_scope_match": _match(
            normalize_statement_scope(request.statement_scope), actual_statement, optional=True
        ),
        "attribution_scope_match": _match(
            normalize_attribution_scope(request.attribution_scope), actual_attribution, optional=True
        ),
        "policy_stage_match": _match(request.policy_stage_expectation, str(fact.get("fact_state") or ""), optional=True),
        "per_share_basis_match": basis_match,
        "source_lineage_present": "match" if hit.get("source") and hit.get("local_window") else "missing",
    }
    conflicts = [name for name, state in dimensions.items() if state == "conflict"]
    missing = [name for name, state in dimensions.items() if state in {"missing", "unknown"}]
    critical_conflict = any(name in _CRITICAL for name in conflicts)
    if candidate_fact is None or critical_conflict:
        grade = EvidenceGrade.INCORRECT
    elif conflicts:
        grade = EvidenceGrade.INCORRECT
    elif missing:
        grade = EvidenceGrade.AMBIGUOUS
    else:
        grade = EvidenceGrade.CORRECT
    reasons = tuple(
        [f"conflict:{name}" for name in conflicts]
        + [f"missing:{name}" for name in missing]
        + (["all_required_bindings_match"] if not conflicts and not missing else [])
    )
    return GradedEvidenceHit(
        hit=dict(hit), grade=grade, dimensions=dimensions, reasons=reasons
    )
