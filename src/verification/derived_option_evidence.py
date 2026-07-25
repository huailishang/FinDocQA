"""Deterministic option evidence derived from authoritative source facts.

The objects in this module do not guess answers. They record source-local facts,
explicit formulas or state transitions, and a fail-closed option-level result.
They can be merged into production typed evidence only when every required
scope, unit, source, and calculation check succeeds.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from answer_contract import contract_from_mapping, validate_answer_against_contract


SUPPORTED = "supported"
CONTRADICTED = "contradicted"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class SourceFact:
    doc_id: str
    entity_scope: str
    period_scope: str
    metric: str
    value: float | str | None
    unit: str
    canonical_source: str
    local_window: str
    fact_state: str = "reported"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DerivedOptionEvidence:
    qid: str
    option_label: str
    claim_type: str
    source_facts: Sequence[SourceFact]
    formula_or_aggregation: str
    variables: Mapping[str, Any]
    units: Mapping[str, str]
    entity_scope: Sequence[str]
    period_scope: Sequence[str]
    document_scope: Sequence[str]
    result: Any
    status: str
    canonical_sources: Sequence[str]
    conflicts: Sequence[str] = field(default_factory=tuple)
    trusted_for_option_gate: bool = False
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_facts"] = [fact.to_dict() for fact in self.source_facts]
        payload["typed_claim_route"] = "derived_option_evidence"
        payload["required_atoms_complete"] = bool(
            self.trusted_for_option_gate and not self.conflicts
        )
        payload["entity_scope_complete"] = bool(self.entity_scope)
        payload["period_scope_complete"] = bool(self.period_scope)
        payload["metric_scope_complete"] = bool(
            self.source_facts and all(fact.metric for fact in self.source_facts)
        )
        payload["comparator_scope_complete"] = bool(
            self.formula_or_aggregation and self.result is not None
        )
        return payload


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _canonical_sources(facts: Sequence[SourceFact]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(fact.canonical_source for fact in facts if fact.canonical_source))


def _base_conflicts(facts: Sequence[SourceFact]) -> list[str]:
    conflicts: list[str] = []
    if not facts:
        conflicts.append("source_facts_missing")
    for index, fact in enumerate(facts):
        if not fact.canonical_source:
            conflicts.append(f"fact_{index}_canonical_source_missing")
        if not fact.local_window:
            conflicts.append(f"fact_{index}_local_window_missing")
        if not fact.doc_id:
            conflicts.append(f"fact_{index}_doc_id_missing")
        if not fact.entity_scope:
            conflicts.append(f"fact_{index}_entity_scope_missing")
        if not fact.period_scope:
            conflicts.append(f"fact_{index}_period_scope_missing")
    return conflicts


def policy_execution_state(
    *,
    qid: str,
    option_label: str,
    facts: Sequence[SourceFact],
    required_period: str,
    required_ratio: float,
) -> DerivedOptionEvidence:
    """Resolve proposal/approval/execution state without conflating them."""
    conflicts = _base_conflicts(facts)
    stage_rank = {"proposal": 1, "approved": 2, "executed": 3}
    matching: list[SourceFact] = []
    for fact in facts:
        ratio = _decimal(fact.value)
        if (
            fact.period_scope == required_period
            and fact.metric == "cash_dividend_profit_ratio"
            and ratio is not None
            and ratio == _decimal(required_ratio)
        ):
            matching.append(fact)
    if not matching:
        conflicts.append("matching_policy_fact_missing")
        highest_stage = "unknown"
    else:
        highest_stage = max(
            (fact.fact_state for fact in matching),
            key=lambda value: stage_rank.get(value, 0),
        )
    executed = highest_stage == "executed"
    explicitly_not_executed = any(fact.fact_state == "not_executed" for fact in matching)
    # A proposal or approval is not evidence that execution occurred, but it is
    # also not enough to prove the opposite.  Keep that state unresolved.
    status = (
        SUPPORTED
        if executed
        else CONTRADICTED
        if explicitly_not_executed
        else UNRESOLVED
    )
    trusted = bool(matching) and all(
        fact.canonical_source and fact.local_window for fact in matching
    )
    return DerivedOptionEvidence(
        qid=qid,
        option_label=option_label,
        claim_type="policy_execution_state",
        source_facts=tuple(matching or facts),
        formula_or_aggregation="max(policy_stage_rank) == executed",
        variables={
            "required_ratio": required_ratio,
            "required_period": required_period,
            "highest_policy_stage": highest_stage,
        },
        units={"required_ratio": "%"},
        entity_scope=tuple(dict.fromkeys(f.entity_scope for f in facts if f.entity_scope)),
        period_scope=(required_period,),
        document_scope=tuple(dict.fromkeys(f.doc_id for f in facts if f.doc_id)),
        result={"policy_stage": highest_stage, "executed": executed, "explicitly_not_executed": explicitly_not_executed},
        status=status,
        canonical_sources=_canonical_sources(matching or facts),
        conflicts=tuple(sorted(set(conflicts))),
        trusted_for_option_gate=trusted and status in {SUPPORTED, CONTRADICTED},
        diagnostics={"matching_fact_count": len(matching)},
    )


def numeric_sum_comparison(
    *,
    qid: str,
    option_label: str,
    left_facts: Sequence[SourceFact],
    right_fact: SourceFact | None,
    comparator: str = ">",
) -> DerivedOptionEvidence:
    """Compare a sum with one authoritative amount after scope/unit checks."""
    facts = list(left_facts) + ([right_fact] if right_fact else [])
    conflicts = _base_conflicts(facts)
    if not left_facts:
        conflicts.append("left_facts_missing")
    if right_fact is None:
        conflicts.append("right_fact_missing")
    entities = {fact.entity_scope for fact in facts if fact.entity_scope}
    periods = {fact.period_scope for fact in facts if fact.period_scope}
    units = {fact.unit for fact in facts if fact.unit}
    if len(entities) != 1:
        conflicts.append("entity_scope_mismatch")
    if len(periods) != 1:
        conflicts.append("period_scope_mismatch")
    if len(units) != 1:
        conflicts.append("unit_mismatch")
    left_values = [_decimal(fact.value) for fact in left_facts]
    right_value = _decimal(right_fact.value) if right_fact else None
    if any(value is None for value in left_values):
        conflicts.append("left_numeric_value_missing")
    if right_value is None:
        conflicts.append("right_numeric_value_missing")
    left_sum = sum((value for value in left_values if value is not None), Decimal("0"))
    comparison: bool | None = None
    if not conflicts and right_value is not None:
        if comparator == ">":
            comparison = left_sum > right_value
        elif comparator == ">=":
            comparison = left_sum >= right_value
        elif comparator == "<":
            comparison = left_sum < right_value
        elif comparator == "<=":
            comparison = left_sum <= right_value
        else:
            conflicts.append("unsupported_comparator")
    status = SUPPORTED if comparison is True else CONTRADICTED if comparison is False else UNRESOLVED
    return DerivedOptionEvidence(
        qid=qid,
        option_label=option_label,
        claim_type="numeric_sum_comparison",
        source_facts=tuple(facts),
        formula_or_aggregation=f"sum(left_values) {comparator} right_value",
        variables={
            "left_values": [float(value) for value in left_values if value is not None],
            "left_sum": float(left_sum),
            "right_value": float(right_value) if right_value is not None else None,
            "comparator": comparator,
        },
        units={"amount": next(iter(units), "")},
        entity_scope=tuple(sorted(entities)),
        period_scope=tuple(sorted(periods)),
        document_scope=tuple(dict.fromkeys(f.doc_id for f in facts if f.doc_id)),
        result=comparison,
        status=status,
        canonical_sources=_canonical_sources(facts),
        conflicts=tuple(sorted(set(conflicts))),
        trusted_for_option_gate=not conflicts and status in {SUPPORTED, CONTRADICTED},
    )


def yoy_growth(
    *,
    qid: str,
    option_label: str,
    current_fact: SourceFact | None,
    prior_fact: SourceFact | None,
    threshold_percent: float,
    relation: str = ">",
) -> DerivedOptionEvidence:
    """Compute year-over-year growth for one entity and metric."""
    facts = [fact for fact in (current_fact, prior_fact) if fact is not None]
    conflicts = _base_conflicts(facts)
    if current_fact is None:
        conflicts.append("current_fact_missing")
    if prior_fact is None:
        conflicts.append("prior_fact_missing")
    if current_fact and prior_fact:
        if current_fact.entity_scope != prior_fact.entity_scope:
            conflicts.append("entity_scope_mismatch")
        if current_fact.metric != prior_fact.metric:
            conflicts.append("metric_scope_mismatch")
        if current_fact.unit != prior_fact.unit:
            conflicts.append("unit_mismatch")
        if current_fact.doc_id != prior_fact.doc_id:
            conflicts.append("document_scope_mismatch")
    current = _decimal(current_fact.value) if current_fact else None
    prior = _decimal(prior_fact.value) if prior_fact else None
    if current is None or prior is None:
        conflicts.append("numeric_value_missing")
    if prior == 0:
        conflicts.append("prior_value_zero")
    growth: Decimal | None = None
    comparison: bool | None = None
    if not conflicts and current is not None and prior is not None:
        growth = (current - prior) / prior * Decimal("100")
        threshold = _decimal(threshold_percent)
        if relation == ">":
            comparison = growth > threshold
        elif relation == ">=":
            comparison = growth >= threshold
        elif relation == "<":
            comparison = growth < threshold
        elif relation == "<=":
            comparison = growth <= threshold
        else:
            conflicts.append("unsupported_relation")
    status = SUPPORTED if comparison is True else CONTRADICTED if comparison is False else UNRESOLVED
    entity = current_fact.entity_scope if current_fact else prior_fact.entity_scope if prior_fact else ""
    return DerivedOptionEvidence(
        qid=qid,
        option_label=option_label,
        claim_type="yoy_growth",
        source_facts=tuple(facts),
        formula_or_aggregation="(current - prior) / prior * 100",
        variables={
            "current": float(current) if current is not None else None,
            "prior": float(prior) if prior is not None else None,
            "growth_percent": float(growth) if growth is not None else None,
            "threshold_percent": threshold_percent,
            "relation": relation,
        },
        units={"current": current_fact.unit if current_fact else "", "growth": "%"},
        entity_scope=(entity,) if entity else (),
        period_scope=tuple(f.period_scope for f in facts),
        document_scope=tuple(dict.fromkeys(f.doc_id for f in facts)),
        result=comparison,
        status=status,
        canonical_sources=_canonical_sources(facts),
        conflicts=tuple(sorted(set(conflicts))),
        trusted_for_option_gate=not conflicts and status in {SUPPORTED, CONTRADICTED},
    )


def cross_document_all(
    *,
    qid: str,
    option_label: str,
    components: Sequence[DerivedOptionEvidence],
) -> DerivedOptionEvidence:
    """Require every independent document component to be supported."""
    conflicts: list[str] = []
    if not components:
        conflicts.append("components_missing")
    documents = [doc for component in components for doc in component.document_scope]
    if len(set(documents)) != len(components):
        conflicts.append("independent_document_scope_missing")
    for component in components:
        if not component.trusted_for_option_gate:
            conflicts.append(f"component_untrusted:{component.document_scope}")
    statuses = [component.status for component in components]
    if components and all(status == SUPPORTED for status in statuses):
        status = SUPPORTED
    elif any(status == CONTRADICTED for status in statuses):
        status = CONTRADICTED
    else:
        status = UNRESOLVED
    facts = [fact for component in components for fact in component.source_facts]
    return DerivedOptionEvidence(
        qid=qid,
        option_label=option_label,
        claim_type="cross_document_all",
        source_facts=tuple(facts),
        formula_or_aggregation="all(component.status == supported)",
        variables={
            "component_statuses": statuses,
            "component_results": [component.result for component in components],
        },
        units={"growth": "%"},
        entity_scope=tuple(entity for component in components for entity in component.entity_scope),
        period_scope=tuple(period for component in components for period in component.period_scope),
        document_scope=tuple(documents),
        result=status == SUPPORTED,
        status=status,
        canonical_sources=tuple(
            source for component in components for source in component.canonical_sources
        ),
        conflicts=tuple(sorted(set(conflicts))),
        trusted_for_option_gate=not conflicts and status in {SUPPORTED, CONTRADICTED},
        diagnostics={"components": [component.to_dict() for component in components]},
    )


def cross_entity_comparison(
    *,
    qid: str,
    option_label: str,
    left: DerivedOptionEvidence | None,
    right: DerivedOptionEvidence | None,
    comparator: str = ">",
) -> DerivedOptionEvidence:
    """Compare two independently derived numeric results across entities."""
    components = [item for item in (left, right) if item is not None]
    facts = [fact for item in components for fact in item.source_facts]
    conflicts = _base_conflicts(facts)
    if left is None:
        conflicts.append("left_component_missing")
    if right is None:
        conflicts.append("right_component_missing")
    if left is not None and not left.trusted_for_option_gate:
        conflicts.append("left_component_untrusted")
    if right is not None and not right.trusted_for_option_gate:
        conflicts.append("right_component_untrusted")
    left_value = _decimal((left.variables or {}).get("growth_percent")) if left else None
    right_value = _decimal((right.variables or {}).get("growth_percent")) if right else None
    if left_value is None:
        conflicts.append("left_comparison_value_missing")
    if right_value is None:
        conflicts.append("right_comparison_value_missing")
    comparison: bool | None = None
    if not conflicts and left_value is not None and right_value is not None:
        if comparator == ">":
            comparison = left_value > right_value
        elif comparator == ">=":
            comparison = left_value >= right_value
        elif comparator == "<":
            comparison = left_value < right_value
        elif comparator == "<=":
            comparison = left_value <= right_value
        else:
            conflicts.append("unsupported_comparator")
    status = SUPPORTED if comparison is True else CONTRADICTED if comparison is False else UNRESOLVED
    return DerivedOptionEvidence(
        qid=qid,
        option_label=option_label,
        claim_type="cross_entity_comparison",
        source_facts=tuple(facts),
        formula_or_aggregation=f"left_growth {comparator} right_growth",
        variables={
            "left_growth_percent": float(left_value) if left_value is not None else None,
            "right_growth_percent": float(right_value) if right_value is not None else None,
            "comparator": comparator,
        },
        units={"growth": "%"},
        entity_scope=tuple(
            entity for item in components for entity in item.entity_scope
        ),
        period_scope=tuple(
            period for item in components for period in item.period_scope
        ),
        document_scope=tuple(
            dict.fromkeys(doc for item in components for doc in item.document_scope)
        ),
        result=comparison,
        status=status,
        canonical_sources=tuple(
            source for item in components for source in item.canonical_sources
        ),
        conflicts=tuple(sorted(set(conflicts))),
        trusted_for_option_gate=not conflicts and status in {SUPPORTED, CONTRADICTED},
        diagnostics={
            "left": left.to_dict() if left else None,
            "right": right.to_dict() if right else None,
        },
    )



def _canonical_answer(value: Any) -> str:
    return "".join(sorted({ch for ch in str(value or "").upper() if "A" <= ch <= "D"}))


def _derived_claim_route_metadata(item: DerivedOptionEvidence) -> dict[str, Any]:
    """Build route metadata from the actual derived operands, not stale text routing."""
    formula = str(item.formula_or_aggregation or "")
    metrics = tuple(dict.fromkeys(fact.metric for fact in item.source_facts if fact.metric))
    if "rd_expense / operating_revenue" in formula:
        metric = "rd_expense_ratio"
    elif "overseas_revenue / operating_revenue" in formula:
        metric = "overseas_revenue_ratio"
    elif len(metrics) == 1:
        metric = metrics[0]
    else:
        metric = "+".join(metrics)
    entities = tuple(dict.fromkeys(
        str(scope).split(" / ", 1)[0].strip()
        for scope in item.entity_scope
        if str(scope).strip()
    ))
    return {
        "claim_type": item.claim_type,
        "compound": item.claim_type != "direct_fact",
        "entities": list(entities),
        "periods": list(item.period_scope),
        "metric": metric,
        "operand_metrics": list(metrics),
        "comparator": str(item.variables.get("relation") or ""),
        "threshold_percent": item.variables.get("right") if "%" in item.units.values() else None,
        "required_document_count": max(1, len(tuple(item.document_scope))),
        "required_atoms": [
            "canonical_sources",
            "source_facts",
            "entity_scope",
            "period_scope",
            "metric_scope",
            "comparator_scope",
            "derived_formula",
            "derived_inputs",
        ],
        "route_reasons": ["derived_from_source_fact_operands"],
    }


def _mapping_evidence_tier(evidence: Mapping[str, Any]) -> int:
    """Return explicit evidence strength; lower numbers are stronger."""
    candidates = [
        evidence.get("evidence_tier"),
        (evidence.get("diagnostics") or {}).get("evidence_tier")
        if isinstance(evidence.get("diagnostics"), Mapping) else None,
        (evidence.get("variables") or {}).get("evidence_tier")
        if isinstance(evidence.get("variables"), Mapping) else None,
    ]
    derived = evidence.get("derived_option_evidence")
    if isinstance(derived, Mapping):
        diagnostics = derived.get("diagnostics")
        variables = derived.get("variables")
        candidates.extend([
            diagnostics.get("evidence_tier") if isinstance(diagnostics, Mapping) else None,
            variables.get("evidence_tier") if isinstance(variables, Mapping) else None,
        ])
    for value in candidates:
        try:
            tier = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= tier <= 4:
            return tier
    # Existing generic typed evidence is structurally useful but weaker than a
    # product-bound direct clause or complete absence proof.
    return 3 if authoritative_option_verdict(evidence) else 4


def _derived_evidence_tier(item: DerivedOptionEvidence) -> int:
    diagnostics = item.diagnostics if isinstance(item.diagnostics, Mapping) else {}
    variables = item.variables if isinstance(item.variables, Mapping) else {}
    for value in (diagnostics.get("evidence_tier"), variables.get("evidence_tier")):
        try:
            tier = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= tier <= 4:
            return tier
    return 4


def _existing_sources(evidence: Mapping[str, Any]) -> list[str]:
    raw = (
        evidence.get("canonical_sources")
        or evidence.get("resolved_evidence_refs")
        or evidence.get("evidence_refs")
        or ([evidence.get("canonical_source")] if evidence.get("canonical_source") else [])
    )
    if isinstance(raw, str):
        raw = [raw]
    return list(dict.fromkeys(str(value) for value in raw or [] if str(value).strip()))


def merge_derived_option_evidence(
    typed: Mapping[str, Any],
    derived: Sequence[DerivedOptionEvidence],
) -> dict[str, Any]:
    """Merge trusted derived evidence label-locally and recompute trust.

    Only failures for the exact option closed by authoritative derived evidence
    are removed.  Unrelated option failures and all global integrity failures
    are preserved.
    """
    output = deepcopy(dict(typed or {}))
    verdicts = {
        str(label).upper(): deepcopy(dict(value or {}))
        for label, value in dict(output.get("option_verdicts") or {}).items()
    }
    diagnostics = deepcopy(dict(output.get("option_diagnostics") or {}))
    resolved = {
        str(label).upper(): str(value or "unresolved")
        for label, value in dict(output.get("resolved_judgments") or {}).items()
    }
    failures = [str(value) for value in output.get("trust_failures") or []]
    applied: list[str] = []
    conflicts: list[str] = []

    for item in derived:
        label = str(item.option_label).upper()
        payload = item.to_dict()
        diagnostics.setdefault(label, {})["derived_option_evidence"] = payload
        if not item.trusted_for_option_gate or item.status not in {SUPPORTED, CONTRADICTED}:
            continue
        existing = dict(verdicts.get(label) or {})
        existing_status = str(existing.get("status") or UNRESOLVED)
        derived_tier = _derived_evidence_tier(item)
        existing_tier = _mapping_evidence_tier(existing)
        superseded_sources: list[str] = []
        conflict_reason = ""
        reconciliation_rule_id = str(
            (item.diagnostics or {}).get("reconciliation_rule_id")
            or (item.variables or {}).get("reconciliation_rule_id")
            or ""
        )
        if existing_status in {SUPPORTED, CONTRADICTED} and existing_status != item.status:
            if authoritative_option_verdict(existing):
                if derived_tier < existing_tier:
                    superseded_sources = _existing_sources(existing)
                    conflict_reason = (
                        f"tier_{derived_tier}_derived_evidence_supersedes_"
                        f"tier_{existing_tier}_existing_evidence"
                    )
                    diagnostics.setdefault(label, {})[
                        "authoritative_lower_tier_existing_verdict_superseded"
                    ] = {
                        "existing_status": existing_status,
                        "derived_status": item.status,
                        "existing_evidence_tier": existing_tier,
                        "derived_evidence_tier": derived_tier,
                        "superseded_evidence_sources": superseded_sources,
                    }
                else:
                    conflict = f"option_{label}:derived_conflicts_with_existing_verdict"
                    conflicts.append(conflict)
                    existing["lineage_conflict"] = True
                    existing["derived_conflict"] = payload
                    existing["conflict_reason"] = (
                        f"same_or_stronger_existing_tier:{existing_tier};"
                        f"derived_tier:{derived_tier}"
                    )
                    verdicts[label] = existing
                    continue
            else:
                superseded_sources = _existing_sources(existing)
                conflict_reason = "non_authoritative_existing_verdict_overridden"
                diagnostics.setdefault(label, {})[
                    "non_authoritative_existing_verdict_overridden_by_derived"
                ] = {
                    "existing_status": existing_status,
                    "derived_status": item.status,
                    "existing_evidence_tier": existing_tier,
                    "derived_evidence_tier": derived_tier,
                    "superseded_evidence_sources": superseded_sources,
                }

        sources = list(dict.fromkeys(
            str(source) for source in item.canonical_sources if str(source).strip()
        ))
        windows = [fact.local_window for fact in item.source_facts if fact.local_window]
        insurance_audit = (item.diagnostics or {}).get("insurance_clause_audit")
        insurance_audit = insurance_audit if isinstance(insurance_audit, Mapping) else {}
        derived_claim_route = str(insurance_audit.get("claim_route") or "")
        claim_route = (
            derived_claim_route
            or ("exact_clause" if item.claim_type == "policy_execution_state" else "calculation")
        )
        verdicts[label] = {
            **existing,
            "status": item.status,
            "claim_type": item.claim_type,
            "claim_route": claim_route,
            "claim_route_metadata": _derived_claim_route_metadata(item),
            "typed_claim_route": "derived_option_evidence",
            "term_equivalence": "confirmed" if item.status == SUPPORTED else "not_required",
            "term_equivalence_confirmed": item.status == SUPPORTED,
            "term_equivalence_required": item.status == SUPPORTED,
            "factual_statement_true": item.status == SUPPORTED,
            "question_scope_binding": "in_scope",
            "reason": item.formula_or_aggregation,
            "evidence_refs": sources,
            "resolved_evidence_refs": sources,
            "canonical_source": sources[0] if sources else "",
            "canonical_sources": sources,
            "local_window": "\n\n".join(windows),
            "certification_basis": item.formula_or_aggregation,
            "source_facts": [fact.to_dict() for fact in item.source_facts],
            "derived_option_evidence": payload,
            "trusted_for_option_gate": True,
            "missing_atoms": [],
            "conflicting_atoms": [],
            "conflicts": [],
            "required_atoms_complete": payload.get("required_atoms_complete") is True,
            "entity_scope_complete": payload.get("entity_scope_complete") is True,
            "entity_scope_reasons": (
                [] if payload.get("entity_scope_complete") is True
                else ["derived_entity_scope_incomplete"]
            ),
            "period_scope_complete": payload.get("period_scope_complete") is True,
            "metric_scope_complete": payload.get("metric_scope_complete") is True,
            "comparator_scope_complete": payload.get("comparator_scope_complete") is True,
            "lineage_conflict": False,
            "opposite_certification_count": 0,
            "resolved_judgment": item.status,
            "evidence_tier": derived_tier,
            "winning_evidence_source": sources[0] if sources else "",
            "superseded_evidence_sources": superseded_sources,
            "conflict_reason": conflict_reason,
            "reconciliation_rule_id": reconciliation_rule_id,
        }
        resolved[label] = item.status
        applied.append(label)
        prefix = f"option_{label}:"
        failures = [failure for failure in failures if not failure.startswith(prefix)]

    failures.extend(conflicts)
    labels = sorted(set(verdicts) | set(resolved))
    unresolved = [
        label for label in labels
        if str((verdicts.get(label) or {}).get("status") or UNRESOLVED)
        not in {SUPPORTED, CONTRADICTED}
    ]
    failures = [
        failure for failure in failures
        if failure != "incomplete_or_unknown_model_judgments"
    ]
    for label in unresolved:
        prefix = f"option_{label}:"
        if not any(failure.startswith(prefix) for failure in failures):
            failures.append(f"option_{label}:unresolved_after_derived_evidence")
    if unresolved:
        failures.append("incomplete_or_unknown_model_judgments")

    typed_answer = _canonical_answer(
        "".join(
            label for label in labels
            if str((verdicts.get(label) or {}).get("status")) == SUPPORTED
        )
    )
    output["option_verdicts"] = verdicts
    output["option_diagnostics"] = diagnostics
    output["resolved_judgments"] = resolved
    output["derived_option_evidence_applied_labels"] = sorted(set(applied))
    output["derived_option_evidence_conflicts"] = sorted(set(conflicts))
    output["unresolved_after_typed"] = unresolved
    output["typed_supported_answer"] = typed_answer
    output["correction_proposal"] = typed_answer or None
    output["correction_differs"] = bool(
        typed_answer and typed_answer != _canonical_answer(output.get("solver_answer"))
    )

    contract = contract_from_mapping(output.get("answer_contract"))
    if contract is not None:
        validation = validate_answer_against_contract(typed_answer, contract).to_dict()
        output["typed_supported_answer_contract_validation"] = validation
        output["correction_answer_contract_validation"] = validation
    contract_ok = all(
        bool((output.get(key) or {}).get("valid") is True)
        for key in (
            "solver_answer_contract_validation",
            "typed_supported_answer_contract_validation",
            "correction_answer_contract_validation",
        )
    )
    if not contract_ok:
        failures.append("derived_typed_answer_contract_incomplete")

    full_authority = bool(labels) and not unresolved and all(
        authoritative_option_verdict(verdicts[label]) for label in labels
    )
    derived_full_replacement = bool(full_authority and set(labels) <= set(applied))
    if derived_full_replacement and contract_ok:
        # A complete ledger-derived contract supersedes model-format and stale
        # pre-merge answer-validation failures.  Source/period/unit failures are
        # never removed here; they would prevent full_authority above.
        superseded_exact = {
            "structured_parse_failed",
            "option_judgment_label_mismatch",
            "solver_answer_does_not_match_structured_judgments",
            "solver_declared_missing_option_judgments",
            "used_doc_lineage_missing",
            "no_candidates_in_used_doc_lineage",
            "truth_false_proposition_unresolved",
            "derived_typed_answer_contract_incomplete",
        }
        failures = [
            failure for failure in failures
            if failure not in superseded_exact
            and not failure.startswith("typed_supported_answer_contract_violation:")
            and not failure.startswith("correction_answer_contract_violation:")
        ]
        output["model_format_failures_superseded_by_full_derived_contract"] = True
    else:
        output["model_format_failures_superseded_by_full_derived_contract"] = False

    output["trust_failures"] = sorted(set(failures))
    output["trusted_for_production"] = bool(
        full_authority and contract_ok and not output["trust_failures"]
    )
    output["solver_answer_matches_typed_supported_answer"] = (
        _canonical_answer(output.get("solver_answer")) == typed_answer
    )
    return output

def authoritative_option_verdict(evidence: Mapping[str, Any]) -> bool:
    """Validate the complete contract required for an option-local gate."""
    status = str(evidence.get("status") or "")
    if status not in {SUPPORTED, CONTRADICTED}:
        return False
    if evidence.get("trusted_for_option_gate") is not True:
        return False
    sources = (
        evidence.get("canonical_sources")
        or evidence.get("evidence_refs")
        or ([evidence.get("canonical_source")] if evidence.get("canonical_source") else [])
    )
    if isinstance(sources, str):
        sources = [sources]
    if not sources or any(not str(source).strip() for source in sources):
        return False
    local_window = str(evidence.get("local_window") or "").strip()
    source_facts = evidence.get("source_facts") or []
    if not local_window and not source_facts:
        return False
    if evidence.get("missing_atoms"):
        return False
    if evidence.get("conflicting_atoms"):
        return False
    if evidence.get("conflicts"):
        return False
    if evidence.get("required_atoms_complete") is not True:
        return False
    if evidence.get("entity_scope_complete") is not True:
        return False
    if evidence.get("period_scope_complete") is not True:
        return False
    if evidence.get("metric_scope_complete") is not True:
        return False
    if evidence.get("comparator_scope_complete") is not True:
        return False
    if evidence.get("lineage_conflict") is True:
        return False
    if evidence.get("opposite_certification_count", 0):
        return False

    derived = evidence.get("derived_option_evidence")
    claim_type = str(
        evidence.get("claim_type")
        or (derived.get("claim_type") if isinstance(derived, Mapping) else "")
        or "direct_fact"
    )
    typed_route = str(evidence.get("typed_claim_route") or "")
    compound_types = {
        "policy_execution_state",
        "yoy_growth",
        "numeric_comparison",
        "numeric_sum_comparison",
        "cross_entity_comparison",
        "cross_document_all",
        "truth_false_proposition",
        "financial_metric_claim",
        "financial_historical_state",
    }
    if claim_type in compound_types:
        derived_route = typed_route == "derived_option_evidence"
        certified_cross_doc_route = bool(
            claim_type == "cross_document_all"
            and typed_route == "cross_doc_subclaim_aggregation"
            and evidence.get("cross_doc_aggregation_complete") is True
        )
        certified_truth_false_route = bool(
            claim_type == "truth_false_proposition"
            and typed_route == "truth_false_proposition_compiler"
            and evidence.get("cross_doc_aggregation_complete") is True
        )
        if not (derived_route or certified_cross_doc_route or certified_truth_false_route):
            return False
    if claim_type == "direct_fact" and typed_route not in {
        "typed_claim_local_window",
        "cross_doc_subclaim_aggregation",
    }:
        return False
    return True

def option_local_disqualification(
    *,
    baseline_answer: str,
    proposal_answer: str,
    option_verdicts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject only changed proposal slots proven wrong by authoritative evidence."""
    baseline = {ch for ch in str(baseline_answer or "").upper() if "A" <= ch <= "D"}
    proposal = {ch for ch in str(proposal_answer or "").upper() if "A" <= ch <= "D"}
    added = sorted(proposal - baseline)
    removed = sorted(baseline - proposal)
    decisive: list[dict[str, Any]] = []
    for label in added:
        verdict = dict(option_verdicts.get(label) or {})
        if authoritative_option_verdict(verdict) and verdict.get("status") == CONTRADICTED:
            decisive.append({
                "option_label": label,
                "change": "added",
                "authoritative_status": CONTRADICTED,
                "canonical_sources": list(verdict.get("canonical_sources") or verdict.get("evidence_refs") or [],),
            })
    for label in removed:
        verdict = dict(option_verdicts.get(label) or {})
        if authoritative_option_verdict(verdict) and verdict.get("status") == SUPPORTED:
            decisive.append({
                "option_label": label,
                "change": "removed",
                "authoritative_status": SUPPORTED,
                "canonical_sources": list(verdict.get("canonical_sources") or verdict.get("evidence_refs") or []),
            })
    return {
        "proposal_disqualified": bool(decisive),
        "baseline_preserved": bool(decisive),
        "added_labels": added,
        "removed_labels": removed,
        "decisive_changed_slots": decisive,
    }
