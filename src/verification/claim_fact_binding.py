"""Deterministic ClaimSpec-to-fact binding for financial evidence.

The module is deliberately QID-independent.  It validates every source fact
against one of the semantic operand slots declared by a FinancialClaimSpec
before that fact may participate in a formula or an answer override.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from verification.financial_claim_ast import FinancialClaimSpec

SCHEMA_VERSION = "financial_claim_fact_binding_v1"

_CURRENCY_SCALE: Mapping[str, Decimal] = {
    "cny": Decimal("1"),
    "rmb": Decimal("1"),
    "人民币": Decimal("1"),
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "百万元": Decimal("1000000"),
    "亿元": Decimal("100000000"),
    "万亿元": Decimal("1000000000000"),
}

_UNIT_ALIASES: Mapping[str, str] = {
    "%": "%",
    "percent": "%",
    "percentage": "%",
    "百分比": "%",
    "cny/share": "CNY/share",
    "元/股": "CNY/share",
    "每股元": "CNY/share",
    "cny/10shares": "CNY/10 shares",
    "cny/10 shares": "CNY/10 shares",
    "元/10股": "CNY/10 shares",
    "每10股元": "CNY/10 shares",
    "count": "count",
    "次": "count",
    "个": "count",
    "years": "count",
    "year": "count",
    "年": "count",
    "policy_state": "policy_state",
    "ratio": "ratio_dimensionless",
    "ratio_dimensionless": "ratio_dimensionless",
    "倍": "ratio_dimensionless",
}

_METRIC_UNIT_FAMILY: Mapping[str, str] = {
    "operating_revenue": "currency_total",
    "total_operating_revenue": "currency_total",
    "parent_attributable_net_profit": "currency_total",
    "operating_cash_flow_net": "currency_total",
    "financing_cash_flow_net": "currency_total",
    "rd_investment": "currency_total",
    "rd_expense": "currency_total",
    "cash_dividend_amount": "currency_total",
    "overseas_revenue": "currency_total",
    "new_contract_amount": "currency_total",
    "dividend_plus_repurchase_amount": "currency_total",
    "rd_investment_ratio": "percentage",
    "rd_expense_ratio": "percentage",
    "cash_dividend_profit_ratio": "percentage",
    "overseas_revenue_ratio": "percentage",
    "cash_dividend_per_share": "currency_per_share",
    "cash_dividend_per_10_shares": "currency_per_10_shares",
    "share_repurchase_history": "count",
    "cash_dividend_policy": "policy_state",
}

_DERIVATION_COMPONENTS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "rd_expense_ratio": (("numerator", "rd_expense"), ("denominator", "operating_revenue")),
    "rd_investment_ratio": (("numerator", "rd_investment"), ("denominator", "operating_revenue")),
    "overseas_revenue_ratio": (("numerator", "overseas_revenue"), ("denominator", "operating_revenue")),
    "cash_dividend_profit_ratio": (("numerator", "cash_dividend_amount"), ("denominator", "parent_attributable_net_profit")),
}

_ATTRIBUTION_EQUIVALENCE = {
    "parent_attributable": "parent_attributable",
    "listed_company_shareholders_attributable": "parent_attributable",
    "归属于母公司股东": "parent_attributable",
    "归属于上市公司股东": "parent_attributable",
    "all_shareholders": "all_shareholders",
    "non_attributable": "non_attributable",
    "not_applicable": "not_applicable",
    "": "unknown",
    "unknown": "unknown",
}

_STATEMENT_SCOPE_ALIASES = {
    "consolidated": "consolidated",
    "合并": "consolidated",
    "合并口径": "consolidated",
    "company_only": "company_only",
    "parent_company": "company_only",
    "母公司": "company_only",
    "母公司口径": "company_only",
    "segment": "segment",
    "分部": "segment",
    "": "unknown",
    "unknown": "unknown",
}

_POLICY_EQUIVALENCE: Mapping[str, frozenset[str]] = {
    "proposal": frozenset({"proposal", "board_recommendation", "not_executed"}),
    "board_recommendation": frozenset({"board_recommendation", "proposal"}),
    "approved": frozenset({"approved"}),
    "executed": frozenset({"executed"}),
    "historical_series": frozenset({"historical_series", "executed"}),
    "historical_cumulative": frozenset({"historical_cumulative", "executed"}),
}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def normalize_unit(unit: Any) -> str:
    raw = str(unit or "").strip().replace("％", "%")
    compact = _compact(raw)
    if compact in _CURRENCY_SCALE:
        return "CNY"
    if compact in _UNIT_ALIASES:
        return _UNIT_ALIASES[compact]
    return raw


def unit_family(unit: Any) -> str:
    normalized = normalize_unit(unit)
    if normalized == "CNY":
        return "currency_total"
    if normalized == "%":
        return "percentage"
    if normalized == "CNY/share":
        return "currency_per_share"
    if normalized == "CNY/10 shares":
        return "currency_per_10_shares"
    if normalized == "count":
        return "count"
    if normalized == "policy_state":
        return "policy_state"
    if normalized == "ratio_dimensionless":
        return "ratio_dimensionless"
    return "unknown"


def metric_unit_family(metric: str) -> str:
    return _METRIC_UNIT_FAMILY.get(str(metric or ""), "unknown")


def canonical_unit_for_family(family: str) -> str:
    return {
        "currency_total": "CNY",
        "percentage": "%",
        "currency_per_share": "CNY/share",
        "currency_per_10_shares": "CNY/10 shares",
        "count": "count",
        "policy_state": "policy_state",
        "ratio_dimensionless": "ratio_dimensionless",
    }.get(str(family or ""), "")


def units_compatible(expected_unit: str, actual_unit: str, *, expected_family: str = "") -> bool:
    actual_normalized = normalize_unit(actual_unit)
    actual_family = unit_family(actual_normalized)
    expected_normalized = normalize_unit(expected_unit)
    family = expected_family or unit_family(expected_normalized)
    if family and family != "unknown" and actual_family != family:
        return False
    if expected_normalized and expected_normalized not in {"ratio_dimensionless"}:
        if unit_family(expected_normalized) in {"currency_total"}:
            return actual_family == "currency_total"
        return actual_normalized == expected_normalized
    return bool(actual_normalized)


def normalize_statement_scope(value: Any) -> str:
    compact = _compact(value)
    return _STATEMENT_SCOPE_ALIASES.get(compact, str(value or "unknown").strip() or "unknown")


def normalize_attribution_scope(value: Any) -> str:
    compact = _compact(value)
    return _ATTRIBUTION_EQUIVALENCE.get(compact, str(value or "unknown").strip() or "unknown")


def per_share_basis_from_unit(unit: Any, metadata: Mapping[str, Any] | None = None) -> str:
    metadata = dict(metadata or {})
    explicit = str(metadata.get("per_share_basis") or "").strip()
    if explicit:
        return explicit
    family = unit_family(unit)
    if family == "currency_per_share":
        return "per_share"
    if family == "currency_per_10_shares":
        return "per_10_shares"
    return "not_applicable"


def fact_identity(fact: Any) -> tuple[Any, ...]:
    data = fact_mapping(fact)
    return (
        data["entity"], data["metric"], data["period"], data["comparison_period"],
        str(data["value"]), normalize_unit(data["unit"]),
        normalize_statement_scope(data["statement_scope"]),
        normalize_attribution_scope(data["attribution_scope"]),
        data["fact_state"], data["doc_id"], data["canonical_source"],
    )


def fact_identity_hash(fact: Any) -> str:
    raw = json.dumps(fact_identity(fact), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fact_mapping(fact: Any) -> dict[str, Any]:
    if isinstance(fact, Mapping):
        raw = dict(fact)
    else:
        method = getattr(fact, "to_dict", None)
        raw = dict(method()) if callable(method) else dict(getattr(fact, "__dict__", {}) or {})
    metadata = dict(raw.get("metadata") or {})
    entity_scope = str(raw.get("entity_scope") or "")
    scope_parts = [part.strip() for part in entity_scope.split(" / ")]
    inferred_entity = scope_parts[0] if scope_parts else ""
    if " / " not in entity_scope and entity_scope:
        tokens = entity_scope.split()
        scope_markers = {
            "listed_group", "consolidated", "company_only", "parent_company",
            "segment", "not_applicable", "parent_attributable",
            "listed_company_shareholders_attributable", "all_shareholders",
        }
        marker_index = next(
            (index for index, token in enumerate(tokens) if token in scope_markers),
            len(tokens),
        )
        inferred_entity = " ".join(tokens[:marker_index]).strip()
    entity = str(
        raw.get("entity") or raw.get("entity_name") or metadata.get("entity_name")
        or metadata.get("entity") or inferred_entity
    ).strip()
    statement = str(raw.get("statement_scope") or metadata.get("statement_scope") or "").strip()
    attribution = str(raw.get("attribution_scope") or metadata.get("attribution_scope") or "").strip()
    if not statement and len(scope_parts) >= 2:
        statement = scope_parts[1]
    if not attribution and len(scope_parts) >= 3:
        attribution = scope_parts[2]
    period = str(raw.get("period") or raw.get("period_scope") or "").strip()
    comparison = str(raw.get("comparison_period") or metadata.get("comparison_period") or "").strip()
    unit = str(raw.get("unit") or raw.get("normalized_unit") or "").strip()
    return {
        "entity": entity,
        "metric": str(raw.get("metric") or "").strip(),
        "period": period,
        "comparison_period": comparison,
        "value": raw.get("value", raw.get("normalized_value")),
        "unit": unit,
        "unit_family": unit_family(unit),
        "statement_scope": normalize_statement_scope(statement),
        "attribution_scope": normalize_attribution_scope(attribution),
        "fact_state": str(raw.get("fact_state") or metadata.get("policy_stage") or "reported").strip(),
        "per_share_basis": per_share_basis_from_unit(unit, metadata),
        "doc_id": str(raw.get("doc_id") or raw.get("document_id") or "").strip(),
        "canonical_source": str(raw.get("canonical_source") or "").strip(),
        "local_window": str(raw.get("local_window") or "").strip(),
        "metadata": metadata,
        "raw": raw,
    }


@dataclass(frozen=True)
class ClaimFactSlot:
    slot_id: str
    role: str
    entity: str
    metric: str
    period: str
    comparison_period: str
    expected_unit: str
    expected_unit_family: str
    statement_scope: str
    attribution_scope: str
    policy_stage: str
    per_share_basis: str
    allowed_doc_ids: tuple[str, ...]
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimFactBinding:
    schema_version: str
    claim_id: str
    fact_identity: str
    slot_id: str
    role: str
    entity_match: str
    metric_match: str
    current_period_match: str
    comparison_period_match: str
    unit_match: str
    unit_family_match: str
    statement_scope_match: str
    attribution_scope_match: str
    policy_stage_match: str
    per_share_basis_match: str
    document_binding_match: str
    source_lineage_present: str
    binding_status: str
    binding_failures: tuple[str, ...]
    safe_for_formula: bool
    safe_for_override: bool
    normalized_fact: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slot_unit(metric: str, spec: FinancialClaimSpec, role: str) -> tuple[str, str, str]:
    family = metric_unit_family(metric)
    direct_scalar_claim = bool(
        spec.relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"}
        and spec.value is not None
        and role == "current"
        and metric == spec.metric
    )
    explicit = spec.value_unit if direct_scalar_claim and spec.value_unit else ""
    expected_unit = explicit or canonical_unit_for_family(family)
    basis = "not_applicable"
    if family == "currency_per_share":
        basis = "per_share"
    elif family == "currency_per_10_shares":
        basis = "per_10_shares"
    return expected_unit, family, basis


def claim_fact_slots(spec: FinancialClaimSpec) -> tuple[ClaimFactSlot, ...]:
    if not spec.entity_refs or not spec.metric:
        return ()
    slots: list[ClaimFactSlot] = []

    def add(role: str, entity: str, metric: str, period: str, *, required: bool = True) -> None:
        unit, family, basis = _slot_unit(metric, spec, role)
        policy = spec.policy_stage if spec.relation == "policy_state_is" else ""
        slots.append(ClaimFactSlot(
            slot_id=f"{role}:{entity}:{metric}:{period}", role=role,
            entity=entity, metric=metric, period=period, comparison_period="",
            expected_unit=unit, expected_unit_family=family,
            statement_scope=spec.statement_scope,
            attribution_scope=(
                "unknown" if spec.relation == "policy_state_is"
                else spec.attribution_scope if metric == spec.metric
                else "parent_attributable" if metric == "parent_attributable_net_profit"
                else "not_applicable"
            ),
            policy_stage=policy,
            per_share_basis=basis,
            allowed_doc_ids=tuple(spec.required_doc_ids), required=required,
        ))

    relation = spec.relation
    entities = tuple(spec.entity_refs)
    if relation in {"multiplier_gt", "multiplier_lt"} and len(entities) >= 2:
        add("current", entities[0], spec.metric, spec.current_period)
        add("peer", entities[1], spec.metric, spec.current_period)
    elif relation in {"ratio_gt", "ratio_lt"} and spec.comparator_metric:
        add("numerator", entities[0], spec.metric, spec.current_period)
        add("denominator", entities[0], spec.comparator_metric, spec.current_period)
    elif relation in {"yoy_gt", "yoy_lt"}:
        for index, entity in enumerate(entities):
            suffix = "" if index == 0 else f"_{index+1}"
            add(f"current{suffix}", entity, spec.metric, spec.current_period)
            add(f"prior{suffix}", entity, spec.metric, spec.comparison_period)
    elif relation in {"gt", "lt", "gte", "lte"} and len(entities) >= 2 and spec.value is None:
        add("current", entities[0], spec.metric, spec.current_period)
        add("peer", entities[1], spec.metric, spec.current_period)
    elif relation in {"gt", "lt", "gte", "lte"} and spec.comparison_period and spec.value is None:
        add("current", entities[0], spec.metric, spec.current_period)
        add("prior", entities[0], spec.metric, spec.comparison_period)
    else:
        add("current", entities[0], spec.metric, spec.current_period)

    # A ratio may be evidenced either by a directly reported ratio or by its
    # declared numerator/denominator components.  Component slots are optional
    # alternatives and never replace a direct slot unless both are present.
    for role, metric in _DERIVATION_COMPONENTS.get(spec.metric, ()):
        add(f"component_{role}", entities[0], metric, spec.current_period, required=False)
    if spec.relation == "policy_state_is" and spec.metric == "cash_dividend_policy":
        for metric in (
            "cash_dividend_profit_ratio", "cash_dividend_amount",
            "cash_dividend_per_share", "cash_dividend_per_10_shares",
        ):
            unit, family, basis = _slot_unit(metric, spec, "policy_evidence")
            slots.append(ClaimFactSlot(
                slot_id=f"policy_evidence:{entities[0]}:{metric}:{spec.current_period}",
                role="policy_evidence", entity=entities[0], metric=metric,
                period=spec.current_period, comparison_period="",
                expected_unit=unit, expected_unit_family=family,
                statement_scope=spec.statement_scope, attribution_scope="unknown",
                policy_stage=spec.policy_stage, per_share_basis=basis,
                allowed_doc_ids=tuple(spec.required_doc_ids), required=False,
            ))
    return tuple(slots)


def _state(expected: str, actual: str, *, aliases: Mapping[str, str] | None = None, optional: bool = False) -> str:
    expected = str(expected or "").strip()
    actual = str(actual or "").strip()
    if not expected or expected in {"unknown", "not_applicable"} and optional:
        return "not_required"
    if not actual or actual == "unknown":
        return "missing"
    if aliases is not None:
        expected = aliases.get(_compact(expected), expected)
        actual = aliases.get(_compact(actual), actual)
    return "match" if expected == actual else "conflict"


def _period_state(expected: str, actual: str) -> str:
    if not expected:
        return "not_required"
    if not actual:
        return "missing"
    return "match" if actual == expected or actual.startswith(expected) else "conflict"


def _policy_state(expected: str, actual: str) -> str:
    if not expected:
        return "not_required"
    if not actual:
        return "missing"
    acceptable = _POLICY_EQUIVALENCE.get(expected, frozenset({expected}))
    return "match" if actual in acceptable else "conflict"


def _bind_to_slot(spec: FinancialClaimSpec, fact: Any, slot: ClaimFactSlot) -> ClaimFactBinding:
    data = fact_mapping(fact)
    exact_unit = normalize_unit(slot.expected_unit)
    actual_unit = normalize_unit(data["unit"])
    expected_family = slot.expected_unit_family
    actual_family = data["unit_family"]
    unit_match = (
        "not_required" if not exact_unit
        else "missing" if not actual_unit
        else "match" if units_compatible(exact_unit, actual_unit, expected_family=expected_family)
        else "conflict"
    )
    family_match = (
        "not_required" if not expected_family or expected_family == "unknown"
        else "missing" if actual_family == "unknown"
        else "match" if actual_family == expected_family
        else "conflict"
    )
    statement_match = _state(
        normalize_statement_scope(slot.statement_scope), data["statement_scope"],
        aliases=_STATEMENT_SCOPE_ALIASES,
    )
    attribution_expected = normalize_attribution_scope(slot.attribution_scope)
    attribution_match = (
        "not_required"
        if attribution_expected == "unknown"
        else _state(
            attribution_expected,
            data["attribution_scope"],
            aliases=_ATTRIBUTION_EQUIVALENCE,
            optional=False,
        )
    )
    basis_match = _state(slot.per_share_basis, data["per_share_basis"], optional=slot.per_share_basis == "not_applicable")
    dimensions = {
        "entity_match": _state(slot.entity, data["entity"]),
        "metric_match": _state(slot.metric, data["metric"]),
        "current_period_match": _period_state(slot.period, data["period"]),
        "comparison_period_match": _period_state(slot.comparison_period, data["comparison_period"]),
        "unit_match": unit_match,
        "unit_family_match": family_match,
        "statement_scope_match": statement_match,
        "attribution_scope_match": attribution_match,
        "policy_stage_match": _policy_state(slot.policy_stage, data["fact_state"]),
        "per_share_basis_match": basis_match,
        "document_binding_match": (
            "not_required" if not slot.allowed_doc_ids
            else "missing" if not data["doc_id"]
            else "match" if data["doc_id"] in set(slot.allowed_doc_ids)
            else "conflict"
        ),
        "source_lineage_present": "match" if data["canonical_source"] and data["local_window"] else "missing",
    }
    conflicts = tuple(name for name, state in dimensions.items() if state == "conflict")
    missing = tuple(name for name, state in dimensions.items() if state == "missing")
    status = "conflict" if conflicts else "ambiguous" if missing else "correct"
    failures = tuple([f"conflict:{name}" for name in conflicts] + [f"missing:{name}" for name in missing])
    safe = status == "correct"
    return ClaimFactBinding(
        schema_version=SCHEMA_VERSION, claim_id=spec.claim_id,
        fact_identity=fact_identity_hash(fact), slot_id=slot.slot_id, role=slot.role,
        binding_status=status, binding_failures=failures,
        safe_for_formula=safe, safe_for_override=safe,
        normalized_fact={key: value for key, value in data.items() if key not in {"raw"}},
        **dimensions,
    )


def bind_fact_to_claim(spec: FinancialClaimSpec, fact: Any) -> ClaimFactBinding:
    slots = claim_fact_slots(spec)
    if not slots:
        data = fact_mapping(fact)
        return ClaimFactBinding(
            schema_version=SCHEMA_VERSION, claim_id=spec.claim_id,
            fact_identity=fact_identity_hash(fact), slot_id="", role="",
            entity_match="missing", metric_match="missing", current_period_match="missing",
            comparison_period_match="not_required", unit_match="missing", unit_family_match="missing",
            statement_scope_match="missing", attribution_scope_match="missing",
            policy_stage_match="not_required", per_share_basis_match="not_required",
            document_binding_match="missing", source_lineage_present="missing",
            binding_status="ambiguous", binding_failures=("missing:claim_slots",),
            safe_for_formula=False, safe_for_override=False,
            normalized_fact={key: value for key, value in data.items() if key != "raw"},
        )
    candidates = [_bind_to_slot(spec, fact, slot) for slot in slots]
    rank = {"correct": 0, "ambiguous": 1, "conflict": 2}
    candidates.sort(key=lambda item: (
        rank[item.binding_status], len(item.binding_failures),
        0 if item.role.startswith("current") else 1,
    ))
    return candidates[0]


def _cross_operand_conflicts(spec: FinancialClaimSpec, bindings: Sequence[ClaimFactBinding]) -> tuple[str, ...]:
    correct = [binding for binding in bindings if binding.binding_status == "correct"]
    by_role = {binding.role: binding for binding in correct}
    conflicts: list[str] = []
    if spec.relation in {"multiplier_gt", "multiplier_lt"}:
        pair = (by_role.get("current"), by_role.get("peer"))
        if all(pair):
            families = {item.normalized_fact.get("unit_family") for item in pair if item}
            if len(families) != 1:
                conflicts.append("cross_unit_multiplier_conflict")
    if spec.relation in {"yoy_gt", "yoy_lt"}:
        current_roles = [role for role in by_role if role.startswith("current")]
        for current_role in current_roles:
            suffix = current_role.removeprefix("current")
            prior = by_role.get("prior" + suffix)
            current = by_role.get(current_role)
            if current and prior and current.normalized_fact.get("unit_family") != prior.normalized_fact.get("unit_family"):
                conflicts.append("cross_unit_yoy_conflict")
    if spec.relation in {"ratio_gt", "ratio_lt"}:
        numerator = by_role.get("numerator")
        denominator = by_role.get("denominator")
        if numerator and denominator:
            if numerator.normalized_fact.get("unit_family") != denominator.normalized_fact.get("unit_family"):
                conflicts.append("ratio_operand_unit_contract_conflict")
    return tuple(sorted(set(conflicts)))


def assess_claim_fact_bindings(spec: FinancialClaimSpec, facts: Sequence[Any]) -> dict[str, Any]:
    slots = claim_fact_slots(spec)
    bindings = tuple(bind_fact_to_claim(spec, fact) for fact in facts)
    correct_slots = {binding.slot_id for binding in bindings if binding.binding_status == "correct"}
    required_slots = {slot.slot_id for slot in slots if slot.required}

    # A directly reported ratio is sufficient on its own.  Otherwise declared
    # derivation components may satisfy the same current slot as a pair.
    if spec.metric in _DERIVATION_COMPONENTS:
        current_slots = {slot.slot_id for slot in slots if slot.role == "current"}
        component_slots = {slot.slot_id for slot in slots if slot.role.startswith("component_")}
        if current_slots <= correct_slots or component_slots and component_slots <= correct_slots:
            required_slots -= current_slots
            required_slots -= component_slots
    if spec.relation == "policy_state_is":
        primary_slots = {slot.slot_id for slot in slots if slot.role == "current"}
        policy_evidence_slots = {slot.slot_id for slot in slots if slot.role == "policy_evidence"}
        if primary_slots <= correct_slots or bool(policy_evidence_slots & correct_slots):
            required_slots -= primary_slots
            required_slots -= policy_evidence_slots
    missing_slots = sorted(required_slots - correct_slots)
    cross_conflicts = _cross_operand_conflicts(spec, bindings)
    conflict_bindings = [binding for binding in bindings if binding.binding_status == "conflict"]
    ambiguous_bindings = [binding for binding in bindings if binding.binding_status == "ambiguous"]
    safe = bool(facts and not missing_slots and not conflict_bindings and not cross_conflicts)
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_id": spec.claim_id,
        "slots": [slot.to_dict() for slot in slots],
        "bindings": [binding.to_dict() for binding in bindings],
        "correct_binding_count": sum(binding.binding_status == "correct" for binding in bindings),
        "ambiguous_binding_count": len(ambiguous_bindings),
        "conflict_binding_count": len(conflict_bindings),
        "required_slot_ids": sorted(required_slots),
        "correct_slot_ids": sorted(correct_slots),
        "missing_slot_ids": missing_slots,
        "cross_operand_conflicts": list(cross_conflicts),
        "safe_for_formula": safe,
        "safe_for_override": safe,
    }


def unique_facts(facts: Sequence[Any]) -> tuple[Any, ...]:
    output: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for fact in facts:
        key = fact_identity(fact)
        if key in seen:
            continue
        seen.add(key)
        output.append(fact)
    return tuple(output)
