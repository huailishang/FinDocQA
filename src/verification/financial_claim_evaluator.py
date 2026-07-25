"""AST-driven deterministic evaluator for common financial-report claims."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Sequence

from contracts import Question
from verification.derived_option_evidence import (
    CONTRADICTED,
    SUPPORTED,
    UNRESOLVED,
    DerivedOptionEvidence,
    SourceFact,
)
from verification.claim_fact_binding import fact_mapping
from verification.financial_claim_ast import FinancialClaimSpec


_VALUE_METRICS = {
    "operating_revenue", "total_operating_revenue",
    "parent_attributable_net_profit", "operating_cash_flow_net",
    "financing_cash_flow_net", "rd_investment", "rd_expense",
    "cash_dividend_amount", "cash_dividend_profit_ratio", "cash_dividend_per_share",
    "cash_dividend_per_10_shares", "overseas_revenue_ratio",
    "overseas_revenue", "new_contract_amount",
    "dividend_plus_repurchase_amount",
}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _status(value: bool | None) -> str:
    return SUPPORTED if value is True else CONTRADICTED if value is False else UNRESOLVED


def _source_fact(fact: Any) -> SourceFact:
    if isinstance(fact, SourceFact):
        return fact
    method = getattr(fact, "to_source_fact", None)
    if callable(method):
        return method()
    raise TypeError("financial fact does not support source conversion")


def _precision_tolerance(target: Decimal, spec: FinancialClaimSpec) -> Decimal:
    precision = spec.value_precision if spec.value_precision is not None else 0
    if spec.value_unit == "%":
        return Decimal("0.5") * (Decimal("10") ** Decimal(-precision))
    if spec.value_unit in {"CNY/share", "CNY/10 shares"}:
        return Decimal("0.5") * (Decimal("10") ** Decimal(-precision))
    if spec.value_unit == "CNY":
        return max(Decimal("1"), abs(target) * Decimal("0.0001"))
    return max(Decimal("0.000001"), abs(target) * Decimal("0.001"))


def _compare(left: Decimal, right: Decimal, relation: str, spec: FinancialClaimSpec) -> bool | None:
    if relation in {"gt", "multiplier_gt", "ratio_gt", "yoy_gt"}:
        return left > right
    if relation in {"lt", "multiplier_lt", "ratio_lt", "yoy_lt"}:
        return left < right
    if relation == "gte":
        return left >= right
    if relation == "lte":
        return left <= right
    if relation == "eq":
        if spec.value_unit == "CNY" and abs(right) >= Decimal("100000000"):
            return abs(left - right) <= max(Decimal("1"), abs(right) * Decimal("0.0005"))
        return left == right
    if relation == "approx_eq":
        return abs(left - right) <= _precision_tolerance(right, spec)
    return None


def _make(
    question: Question,
    label: str,
    spec: FinancialClaimSpec,
    facts: Sequence[Any],
    *,
    left: Decimal | None,
    right: Decimal | None,
    formula: str,
    result: bool | None,
    conflicts: Sequence[str] = (),
) -> DerivedOptionEvidence:
    source_facts = tuple(_source_fact(fact) for fact in facts)
    failures = list(conflicts)
    if not source_facts:
        failures.append("source_facts_missing")
    if left is None or right is None:
        failures.append("ast_operand_missing")
    status = _status(result)
    trusted = bool(status in {SUPPORTED, CONTRADICTED} and not failures and source_facts)
    return DerivedOptionEvidence(
        qid=question.qid,
        option_label=label,
        claim_type="financial_semantic_ast_claim",
        source_facts=source_facts,
        formula_or_aggregation=formula,
        variables={
            "left": str(left) if left is not None else None,
            "right": str(right) if right is not None else None,
            "relation": spec.relation,
            "multiplier": spec.multiplier,
        },
        units={f"fact_{index}": fact.unit for index, fact in enumerate(source_facts)},
        entity_scope=tuple(dict.fromkeys(fact.entity_scope for fact in source_facts)),
        period_scope=tuple(dict.fromkeys(fact.period_scope for fact in source_facts)),
        document_scope=tuple(dict.fromkeys(fact.doc_id for fact in source_facts)),
        result=result,
        status=status,
        canonical_sources=tuple(dict.fromkeys(fact.canonical_source for fact in source_facts)),
        conflicts=tuple(sorted(set(failures))),
        trusted_for_option_gate=trusted,
        diagnostics={
            "evidence_tier": 1,
            "production_capability": "financial_reports:corpus_lineage_corrective_retrieval_v2",
            "claim_ast_schema_version": spec.schema_version,
            "claim_ast": spec.to_dict(),
            "comparison_formula_derived_from_ast": True,
            "no_default_comparator_fallback": True,
        },
    )


def _all_components(
    question: Question,
    label: str,
    spec: FinancialClaimSpec,
    components: Sequence[DerivedOptionEvidence],
    formula: str,
) -> DerivedOptionEvidence:
    statuses = [component.status for component in components]
    result = (
        True if components and all(status == SUPPORTED for status in statuses)
        else False if any(status == CONTRADICTED for status in statuses)
        else None
    )
    source_facts = tuple(
        fact for component in components for fact in component.source_facts
    )
    conflicts = tuple(
        conflict for component in components for conflict in component.conflicts
    )
    return DerivedOptionEvidence(
        qid=question.qid,
        option_label=label,
        claim_type="financial_semantic_ast_all",
        source_facts=source_facts,
        formula_or_aggregation=formula,
        variables={"component_statuses": statuses, "relation": spec.relation},
        units={f"fact_{index}": fact.unit for index, fact in enumerate(source_facts)},
        entity_scope=tuple(dict.fromkeys(fact.entity_scope for fact in source_facts)),
        period_scope=tuple(dict.fromkeys(fact.period_scope for fact in source_facts)),
        document_scope=tuple(dict.fromkeys(fact.doc_id for fact in source_facts)),
        result=result,
        status=_status(result),
        canonical_sources=tuple(dict.fromkeys(fact.canonical_source for fact in source_facts)),
        conflicts=tuple(sorted(set(conflicts))),
        trusted_for_option_gate=bool(
            result is not None
            and components
            and all(component.trusted_for_option_gate for component in components)
        ),
        diagnostics={
            "evidence_tier": 1,
            "production_capability": "financial_reports:corpus_lineage_corrective_retrieval_v2",
            "claim_ast_schema_version": spec.schema_version,
            "claim_ast": spec.to_dict(),
            "comparison_formula_derived_from_ast": True,
            "no_default_comparator_fallback": True,
            "components": [component.to_dict() for component in components],
        },
    )


def _fact_value(context: Any, entity: str, metric: str, period: str) -> tuple[Decimal | None, tuple[Any, ...], str]:
    if metric == "rd_expense_ratio":
        doc_id = context.doc(entity, period)
        hit = context.narrative(doc_id, ("研发费用", "占营业收入比重")) if doc_id else None
        if hit:
            source, window = hit
            match = re.search(r"占营业收入比重为\s*([-+]?\d+(?:\.\d+)?)\s*[%％]", window)
            if match:
                value = Decimal(match.group(1))
                fact = SourceFact(
                    doc_id=doc_id,
                    entity_scope=f"{entity} / consolidated / not_applicable",
                    period_scope=period,
                    metric=metric,
                    value=str(value),
                    unit="%",
                    canonical_source=source,
                    local_window=window,
                    fact_state="reported",
                    metadata={"precision_rank": 100, "direct_disclosure": True},
                )
                return value, (fact,), "directly reported rd expense ratio"
        value, facts, formula = context.rd_ratio(entity, period, expense=True)
        return _decimal(value), tuple(facts), formula
    if metric == "rd_investment_ratio":
        value, facts, formula = context.rd_ratio(entity, period, expense=False)
        return _decimal(value), tuple(facts), formula
    if metric == "overseas_revenue_ratio":
        direct = context.fact(entity, metric, period)
        if direct:
            return _decimal(direct.normalized_value), (direct,), "reported overseas revenue ratio"
        doc_id = context.doc(entity, period)
        hit = context.narrative(doc_id, ("境外", "营业收入")) if doc_id else None
        if hit:
            source, window = hit
            match = re.search(r"占(?:本期)?营业收入\s*([-+]?\d+(?:\.\d+)?)\s*[%％]", window)
            if match:
                value = Decimal(match.group(1))
                fact = SourceFact(
                    doc_id=doc_id,
                    entity_scope=f"{entity} / consolidated / not_applicable",
                    period_scope=period,
                    metric=metric,
                    value=str(value),
                    unit="%",
                    canonical_source=source,
                    local_window=window,
                    fact_state="reported",
                    metadata={"precision_rank": 100, "direct_disclosure": True},
                )
                return value, (fact,), "directly reported overseas revenue ratio"
        overseas = context.fact(entity, "overseas_revenue", period)
        revenue = context.fact(entity, "operating_revenue", period)
        overseas_value = _decimal(overseas.normalized_value) if overseas else None
        revenue_value = _decimal(revenue.normalized_value) if revenue else None
        if overseas_value is not None and revenue_value not in {None, Decimal("0")}:
            return overseas_value / revenue_value * Decimal("100"), tuple(
                fact for fact in (overseas, revenue) if fact is not None
            ), "overseas_revenue / operating_revenue * 100"
    fact = context.fact(entity, metric, period)
    return (_decimal(fact.normalized_value) if fact else None, (fact,) if fact else (), metric)


def evaluate_financial_claim_spec(
    question: Question,
    label: str,
    spec: FinancialClaimSpec,
    context: Any,
) -> DerivedOptionEvidence | None:
    """Evaluate supported AST shapes; return ``None`` for policy/compound fallback."""
    if not spec.complete:
        return None
    relation = spec.relation
    entities = spec.entity_refs
    if relation == "policy_state_is":
        return None
    if not entities:
        return None

    if (
        relation in {"gt", "lt", "gte", "lte"}
        and len(entities) == 1
        and spec.value is None
        and spec.comparison_period
    ):
        current, current_facts, _ = _fact_value(
            context, entities[0], spec.metric, spec.current_period
        )
        comparison, comparison_facts, _ = _fact_value(
            context, entities[0], spec.metric, spec.comparison_period
        )
        result = _compare(current, comparison, relation, spec) if current is not None and comparison is not None else None
        return _make(
            question, label, spec, (*current_facts, *comparison_facts),
            left=current, right=comparison,
            formula=f"{entities[0]}.{spec.metric}[{spec.current_period}] {relation} {entities[0]}.{spec.metric}[{spec.comparison_period}]",
            result=result,
        )

    if relation in {"gt", "lt", "gte", "lte"} and len(entities) >= 2 and spec.value is None:
        left, left_facts, _ = _fact_value(context, entities[0], spec.metric, spec.current_period)
        right, right_facts, _ = _fact_value(context, entities[1], spec.metric, spec.current_period)
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec, (*left_facts, *right_facts),
            left=left, right=right,
            formula=f"{entities[0]}.{spec.metric} {relation} {entities[1]}.{spec.metric}",
            result=result,
        )

    if relation in {"multiplier_gt", "multiplier_lt"} and len(entities) >= 2:
        left, left_facts, _ = _fact_value(context, entities[0], spec.metric, spec.current_period)
        right, right_facts, _ = _fact_value(context, entities[1], spec.metric, spec.current_period)
        multiplier = _decimal(spec.multiplier)
        bound = right * multiplier if right is not None and multiplier is not None else None
        result = _compare(left, bound, relation, spec) if left is not None and bound is not None else None
        return _make(
            question, label, spec, (*left_facts, *right_facts),
            left=left, right=bound,
            formula=f"{entities[0]}.{spec.metric} {relation} {spec.multiplier} * {entities[1]}.{spec.metric}",
            result=result,
        )

    if relation in {"ratio_gt", "ratio_lt"} and spec.comparator_metric:
        left, left_facts, _ = _fact_value(context, entities[0], spec.metric, spec.current_period)
        right, right_facts, _ = _fact_value(context, entities[0], spec.comparator_metric, spec.current_period)
        multiplier = _decimal(spec.multiplier)
        bound = right * multiplier if right is not None and multiplier is not None else None
        result = _compare(left, bound, relation, spec) if left is not None and bound is not None else None
        return _make(
            question, label, spec, (*left_facts, *right_facts),
            left=left, right=bound,
            formula=f"{spec.metric} {relation} {spec.multiplier} * {spec.comparator_metric}",
            result=result,
        )

    if relation in {"yoy_gt", "yoy_lt"} and len(entities) >= 2:
        components: list[DerivedOptionEvidence] = []
        for entity in entities:
            current, current_facts, _ = _fact_value(
                context, entity, spec.metric, spec.current_period
            )
            comparison, comparison_facts, _ = _fact_value(
                context, entity, spec.metric, spec.comparison_period
            )
            result = _compare(current, comparison, relation, spec) if current is not None and comparison is not None else None
            components.append(_make(
                question, label, spec, (*current_facts, *comparison_facts),
                left=current, right=comparison,
                formula=f"{entity}.{spec.metric}[{spec.current_period}] {relation} {entity}.{spec.metric}[{spec.comparison_period}]",
                result=result,
            ))
        return _all_components(
            question, label, spec, components,
            f"all entities satisfy {spec.metric} {relation} across {spec.current_period}/{spec.comparison_period}",
        )

    if relation in {"yoy_gt", "yoy_lt"}:
        entity = entities[0]
        current, current_facts, _ = _fact_value(context, entity, spec.metric, spec.current_period)
        comparison, comparison_facts, _ = _fact_value(context, entity, spec.metric, spec.comparison_period)
        facts = (*current_facts, *comparison_facts)
        if spec.value is not None:
            growth, growth_facts = context.growth(entity, spec.metric, spec.current_period)
            left = _decimal(growth)
            right = _decimal(spec.value)
            result = _compare(left, right, relation, spec) if left is not None and right is not None else None
            return _make(
                question, label, spec, growth_facts,
                left=left, right=right,
                formula=f"yoy({entity}.{spec.metric},{spec.current_period}/{spec.comparison_period}) {relation} {spec.value}{spec.value_unit}",
                result=result,
            )
        left = current
        right = comparison
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec, facts,
            left=left, right=right,
            formula=f"{entity}.{spec.metric}[{spec.current_period}] {relation} {entity}.{spec.metric}[{spec.comparison_period}]",
            result=result,
        )

    if relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"} and spec.metric in (_VALUE_METRICS | {"rd_expense_ratio", "rd_investment_ratio"}):
        left, facts, source_formula = _fact_value(context, entities[0], spec.metric, spec.current_period)
        right = _decimal(spec.value)
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec, facts,
            left=left, right=right,
            formula=f"{source_formula}: {spec.metric} {relation} {spec.value}{spec.value_unit}",
            result=result,
        )
    return None


def _source_fact_entity(fact: SourceFact) -> str:
    return str(fact_mapping(fact).get("entity") or "").strip()


def _select_source_facts(
    facts: Sequence[SourceFact],
    *,
    entity: str,
    metric: str,
    period: str,
) -> tuple[SourceFact, ...]:
    return tuple(
        fact for fact in facts
        if fact.metric == metric
        and (not entity or _source_fact_entity(fact) == entity)
        and (not period or fact.period_scope == period or fact.period_scope.startswith(period))
    )


def _best_source_fact(
    facts: Sequence[SourceFact],
    *,
    entity: str,
    metric: str,
    period: str,
) -> SourceFact | None:
    candidates = _select_source_facts(facts, entity=entity, metric=metric, period=period)
    safe_candidates = tuple(
        fact for fact in candidates
        if not isinstance((fact.metadata or {}).get("claim_fact_binding"), dict)
        or (fact.metadata or {}).get("claim_fact_binding", {}).get("safe_for_formula") is True
    )
    if not safe_candidates:
        return None

    def rank(fact: SourceFact) -> tuple[int, int, int, str]:
        metadata = dict(fact.metadata or {})
        return (
            1 if metadata.get("direct_disclosure") is True else 0,
            int(metadata.get("precision_rank") or 0),
            1 if fact.canonical_source and fact.local_window else 0,
            fact.canonical_source,
        )

    return max(safe_candidates, key=rank)


def evaluate_financial_claim_from_source_facts(
    question: Question,
    label: str,
    spec: FinancialClaimSpec,
    facts: Sequence[SourceFact],
) -> DerivedOptionEvidence | None:
    """Re-evaluate one AST exclusively from merged typed SourceFacts."""
    if not spec.complete or not spec.entity_refs:
        return None
    relation = spec.relation
    entities = spec.entity_refs

    if relation == "policy_state_is":
        policy_metrics = {spec.metric}
        if spec.metric == "cash_dividend_policy":
            policy_metrics.update({
                "cash_dividend_profit_ratio", "cash_dividend_amount",
                "cash_dividend_per_share", "cash_dividend_per_10_shares",
            })
        candidates = tuple(
            fact for fact in facts
            if fact.metric in policy_metrics
            and _source_fact_entity(fact) == entities[0]
            and (
                not spec.current_period
                or fact.period_scope == spec.current_period
                or fact.period_scope.startswith(spec.current_period)
            )
        )
        acceptable = {
            "proposal": {"proposal", "board_recommendation", "not_executed"},
            "board_recommendation": {"board_recommendation", "proposal"},
            "approved": {"approved"},
            "executed": {"executed"},
            "historical_series": {"historical_series", "executed"},
            "historical_cumulative": {"historical_cumulative", "executed"},
        }.get(spec.policy_stage, {spec.policy_stage})
        result = None if not candidates else any(fact.fact_state in acceptable for fact in candidates)
        return _make(
            question, label, spec, candidates,
            left=Decimal("1") if result else Decimal("0") if result is False else None,
            right=Decimal("1"),
            formula=f"policy_state({spec.metric}) in {sorted(acceptable)}",
            result=result,
        )

    if relation in {"multiplier_gt", "multiplier_lt"} and len(entities) >= 2:
        left_fact = _best_source_fact(facts, entity=entities[0], metric=spec.metric, period=spec.current_period)
        right_fact = _best_source_fact(facts, entity=entities[1], metric=spec.metric, period=spec.current_period)
        left = _decimal(left_fact.value) if left_fact else None
        right_base = _decimal(right_fact.value) if right_fact else None
        multiplier = _decimal(spec.multiplier)
        right = right_base * multiplier if right_base is not None and multiplier is not None else None
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec,
            tuple(fact for fact in (left_fact, right_fact) if fact),
            left=left, right=right,
            formula=f"{entities[0]}.{spec.metric} {relation} {spec.multiplier} * {entities[1]}.{spec.metric}",
            result=result,
        )

    if relation in {"ratio_gt", "ratio_lt"} and spec.comparator_metric:
        left_fact = _best_source_fact(facts, entity=entities[0], metric=spec.metric, period=spec.current_period)
        right_fact = _best_source_fact(facts, entity=entities[0], metric=spec.comparator_metric, period=spec.current_period)
        left = _decimal(left_fact.value) if left_fact else None
        right_base = _decimal(right_fact.value) if right_fact else None
        multiplier = _decimal(spec.multiplier)
        right = right_base * multiplier if right_base is not None and multiplier is not None else None
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec,
            tuple(fact for fact in (left_fact, right_fact) if fact),
            left=left, right=right,
            formula=f"{spec.metric} {relation} {spec.multiplier} * {spec.comparator_metric}",
            result=result,
        )

    if relation in {"yoy_gt", "yoy_lt"} and len(entities) >= 2:
        components: list[DerivedOptionEvidence] = []
        for entity in entities:
            current = _best_source_fact(facts, entity=entity, metric=spec.metric, period=spec.current_period)
            prior = _best_source_fact(facts, entity=entity, metric=spec.metric, period=spec.comparison_period)
            left = _decimal(current.value) if current else None
            right = _decimal(prior.value) if prior else None
            result = _compare(left, right, relation, spec) if left is not None and right is not None else None
            components.append(_make(
                question, label, spec,
                tuple(fact for fact in (current, prior) if fact),
                left=left, right=right,
                formula=f"{entity}.{spec.metric}[{spec.current_period}] {relation} {entity}.{spec.metric}[{spec.comparison_period}]",
                result=result,
            ))
        return _all_components(
            question, label, spec, components,
            f"all entities satisfy {spec.metric} {relation}",
        )

    if relation in {"yoy_gt", "yoy_lt"}:
        current = _best_source_fact(facts, entity=entities[0], metric=spec.metric, period=spec.current_period)
        prior = _best_source_fact(facts, entity=entities[0], metric=spec.metric, period=spec.comparison_period)
        current_value = _decimal(current.value) if current else None
        prior_value = _decimal(prior.value) if prior else None
        selected = tuple(fact for fact in (current, prior) if fact)
        if spec.value is not None:
            growth = None
            if current_value is not None and prior_value not in {None, Decimal("0")}:
                growth = (current_value - prior_value) / abs(prior_value) * Decimal("100")
            target = _decimal(spec.value)
            result = _compare(growth, target, relation, spec) if growth is not None and target is not None else None
            return _make(
                question, label, spec, selected,
                left=growth, right=target,
                formula=f"yoy({spec.metric}) {relation} {spec.value}{spec.value_unit}",
                result=result,
            )
        result = _compare(current_value, prior_value, relation, spec) if current_value is not None and prior_value is not None else None
        return _make(
            question, label, spec, selected,
            left=current_value, right=prior_value,
            formula=f"{spec.metric}[{spec.current_period}] {relation} {spec.metric}[{spec.comparison_period}]",
            result=result,
        )

    if (
        relation in {"gt", "lt", "gte", "lte"}
        and len(entities) == 1
        and spec.value is None
        and spec.comparison_period
    ):
        current_fact = _best_source_fact(
            facts, entity=entities[0], metric=spec.metric, period=spec.current_period
        )
        prior_fact = _best_source_fact(
            facts, entity=entities[0], metric=spec.metric, period=spec.comparison_period
        )
        left = _decimal(current_fact.value) if current_fact else None
        right = _decimal(prior_fact.value) if prior_fact else None
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec,
            tuple(fact for fact in (current_fact, prior_fact) if fact),
            left=left, right=right,
            formula=f"{entities[0]}.{spec.metric}[{spec.current_period}] {relation} {entities[0]}.{spec.metric}[{spec.comparison_period}]",
            result=result,
        )

    if relation in {"gt", "lt", "gte", "lte"} and len(entities) >= 2 and spec.value is None:
        left_fact = _best_source_fact(facts, entity=entities[0], metric=spec.metric, period=spec.current_period)
        right_fact = _best_source_fact(facts, entity=entities[1], metric=spec.metric, period=spec.current_period)
        left = _decimal(left_fact.value) if left_fact else None
        right = _decimal(right_fact.value) if right_fact else None
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec,
            tuple(fact for fact in (left_fact, right_fact) if fact),
            left=left, right=right,
            formula=f"{entities[0]}.{spec.metric} {relation} {entities[1]}.{spec.metric}",
            result=result,
        )

    derived_ratio_components = {
        "rd_expense_ratio": ("rd_expense", "operating_revenue"),
        "rd_investment_ratio": ("rd_investment", "operating_revenue"),
        "overseas_revenue_ratio": ("overseas_revenue", "operating_revenue"),
        "cash_dividend_profit_ratio": ("cash_dividend_amount", "parent_attributable_net_profit"),
    }
    if relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"} and spec.metric in derived_ratio_components:
        direct_fact = _best_source_fact(
            facts, entity=entities[0], metric=spec.metric, period=spec.current_period
        )
        if direct_fact is not None:
            direct_value = _decimal(direct_fact.value)
            target = _decimal(spec.value)
            result = _compare(direct_value, target, relation, spec) if direct_value is not None and target is not None else None
            return _make(
                question, label, spec, (direct_fact,),
                left=direct_value, right=target,
                formula=f"direct_reported({spec.metric}) {relation} {spec.value}{spec.value_unit}",
                result=result,
            )
        numerator_metric, denominator_metric = derived_ratio_components[spec.metric]
        numerator = _best_source_fact(
            facts, entity=entities[0], metric=numerator_metric, period=spec.current_period
        )
        denominator = _best_source_fact(
            facts, entity=entities[0], metric=denominator_metric, period=spec.current_period
        )
        numerator_value = _decimal(numerator.value) if numerator else None
        denominator_value = _decimal(denominator.value) if denominator else None
        ratio = None
        if numerator_value is not None and denominator_value not in {None, Decimal("0")}:
            ratio = numerator_value / denominator_value * Decimal("100")
        target = _decimal(spec.value)
        result = _compare(ratio, target, relation, spec) if ratio is not None and target is not None else None
        return _make(
            question, label, spec,
            tuple(fact for fact in (numerator, denominator) if fact),
            left=ratio, right=target,
            formula=f"100 * {numerator_metric} / {denominator_metric} {relation} {spec.value}{spec.value_unit}",
            result=result,
        )

    if relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"}:
        fact = _best_source_fact(
            facts, entity=entities[0], metric=spec.metric, period=spec.current_period
        )
        left = _decimal(fact.value) if fact else None
        right = _decimal(spec.value)
        result = _compare(left, right, relation, spec) if left is not None and right is not None else None
        return _make(
            question, label, spec, (fact,) if fact else (),
            left=left, right=right,
            formula=f"typed_fact({spec.metric}) {relation} {spec.value}{spec.value_unit}",
            result=result,
        )
    return None
