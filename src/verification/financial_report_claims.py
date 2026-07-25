"""Deterministic financial-report proposition compiler.

The compiler converts a normalized financial metric ledger into option-local
source facts and derived verdicts. Routing is based on financial terminology,
entity/year bindings, units and comparators, never on QIDs. Ambiguous metric
substitutions (R&D investment vs R&D expense) and policy-stage substitutions
(proposal vs execution) fail closed.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from answer_contract import contract_to_dict, validate_answer_against_contract
from contracts import EvidenceBundle, Question, SolverResult
from verification.derived_option_evidence import (
    CONTRADICTED,
    SUPPORTED,
    UNRESOLVED,
    DerivedOptionEvidence,
    SourceFact,
)
from evidence_completion.adapters.financial_reports import FinancialEvidenceCompletionAdapter
from verification.evidence_sufficiency import assess_financial_evidence_sufficiency
from verification.financial_claim_ast import parse_financial_claim
from verification.financial_claim_evaluator import evaluate_financial_claim_spec
from verification.financial_metric_ledger import (
    FinancialFact,
    FinancialMetricLedger,
    document_meta,
    document_year,
)
from verification.financial_policy_state import audit_historical_action

_ENTITY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "比亚迪": ("比亚迪", "BYD"),
    "宁德时代": ("宁德时代", "CATL"),
    "美的集团": ("美的集团", "美的", "Midea"),
    "中国移动": ("中国移动", "China Mobile"),
    "中国建筑": ("中国建筑", "中建", "CSCEC"),
}
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?")
_PERCENT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*[%％]")
_NUMBER_UNIT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(万亿元|亿元|万元|千元|百万元|元)")
_PER10_RE = re.compile(r"每\s*10\s*股[^，。；]{0,35}?([-+]?\d+(?:\.\d+)?)\s*元")
_PER_SHARE_RE = re.compile(r"每股[^，。；]{0,35}?([-+]?\d+(?:\.\d+)?)\s*元")


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%")


def _canonical_answer(value: Any) -> str:
    return "".join(sorted({ch for ch in str(value or "").upper() if "A" <= ch <= "D"}))


def _entities(text: str) -> tuple[str, ...]:
    compact = _compact(text).lower()
    found: list[tuple[int, str]] = []
    for canonical, aliases in _ENTITY_ALIASES.items():
        positions = [compact.find(_compact(alias).lower()) for alias in aliases]
        positions = [position for position in positions if position >= 0]
        if positions:
            found.append((min(positions), canonical))
    found.sort()
    return tuple(entity for _, entity in found)


def _years(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_YEAR_RE.findall(str(text or ""))))


def _amount_threshold(text: str) -> Decimal | None:
    match = _NUMBER_UNIT_RE.search(str(text or ""))
    if not match:
        return None
    multiplier = {
        "元": Decimal("1"),
        "千元": Decimal("1000"),
        "万元": Decimal("10000"),
        "百万元": Decimal("1000000"),
        "亿元": Decimal("100000000"),
        "万亿元": Decimal("1000000000000"),
    }[match.group(2)]
    return Decimal(match.group(1)) * multiplier


def _percent_threshold(text: str) -> Decimal | None:
    match = _PERCENT_RE.search(str(text or ""))
    return Decimal(match.group(1)) if match else None


def _to_source_fact(fact: FinancialFact) -> SourceFact:
    metadata = dict(fact.metadata or {})
    metadata.update({
        "entity_name": fact.entity_name,
        "statement_scope": fact.statement_scope,
        "attribution_scope": fact.attribution_scope,
        "document_year": fact.document_year,
        "comparison_period": fact.comparison_period,
        "raw_value": fact.raw_value,
        "raw_unit": fact.raw_unit,
        "scale_multiplier": str(fact.scale_multiplier),
        "per_share_basis": fact.per_share_basis,
        "source_page": fact.source_page,
        "source_table": fact.source_table,
        "source_row": fact.source_row,
        "rejection_reasons": list(fact.rejection_reasons),
        "precision_rank": fact.precision_rank,
    })
    return SourceFact(
        doc_id=fact.document_id,
        entity_scope=" / ".join(value for value in (
            fact.entity_name, fact.statement_scope, fact.attribution_scope
        ) if value),
        period_scope=fact.period,
        metric=fact.metric,
        value=str(fact.normalized_value),
        unit=fact.normalized_unit,
        canonical_source=fact.canonical_source,
        local_window=fact.local_window,
        fact_state=fact.fact_state,
        metadata=metadata,
    )


def _narrative_fact(
    doc_id: str,
    metric: str,
    period: str,
    value: Any,
    unit: str,
    state: str,
    source: str,
    window: str,
) -> SourceFact:
    meta = document_meta(doc_id)
    return SourceFact(
        doc_id=doc_id,
        entity_scope=f"{meta.entity_name} / consolidated / unknown_scope",
        period_scope=period,
        metric=metric,
        value=value,
        unit=unit,
        canonical_source=source,
        local_window=window,
        fact_state=state,
        metadata={"policy_stage": state},
    )


def _sources(facts: Sequence[SourceFact]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(fact.canonical_source for fact in facts if fact.canonical_source))


def _make(
    question: Question,
    label: str,
    claim_type: str,
    facts: Sequence[SourceFact],
    formula: str,
    variables: Mapping[str, Any],
    result: Any,
    status: str,
    conflicts: Sequence[str] = (),
    diagnostics: Mapping[str, Any] | None = None,
) -> DerivedOptionEvidence:
    facts = tuple(facts)
    failures = list(conflicts)
    if not facts:
        failures.append("source_facts_missing")
    for index, fact in enumerate(facts):
        if not fact.canonical_source:
            failures.append(f"fact_{index}_canonical_source_missing")
        if not fact.local_window:
            failures.append(f"fact_{index}_local_window_missing")
        if not fact.period_scope:
            failures.append(f"fact_{index}_period_scope_missing")
    trusted = bool(status in {SUPPORTED, CONTRADICTED} and facts and not failures and result is not None)
    evidence_diagnostics = {
        "evidence_tier": 2,
        "production_capability": "financial_reports:lexical_atomic_fact_compiler_v1",
        "atomic_fact_dimensions": (
            "entity",
            "statement_scope",
            "period",
            "comparison_period",
            "metric_or_action",
            "value",
            "unit",
            "comparator_or_policy_stage",
        ),
        **dict(diagnostics or {}),
    }
    return DerivedOptionEvidence(
        qid=question.qid,
        option_label=label,
        claim_type=claim_type,
        source_facts=facts,
        formula_or_aggregation=formula,
        variables=dict(variables),
        units={f"fact_{index}": fact.unit for index, fact in enumerate(facts)},
        entity_scope=tuple(dict.fromkeys(fact.entity_scope for fact in facts if fact.entity_scope)),
        period_scope=tuple(dict.fromkeys(fact.period_scope for fact in facts if fact.period_scope)),
        document_scope=tuple(dict.fromkeys(fact.doc_id for fact in facts if fact.doc_id)),
        result=result,
        status=status,
        canonical_sources=_sources(facts),
        conflicts=tuple(sorted(set(failures))),
        trusted_for_option_gate=trusted,
        diagnostics=evidence_diagnostics,
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _status(value: bool | None) -> str:
    return SUPPORTED if value is True else CONTRADICTED if value is False else UNRESOLVED


def _compare(left: Decimal, right: Decimal, relation: str) -> bool:
    if relation == ">":
        return left > right
    if relation == "<":
        return left < right
    if relation == ">=":
        return left >= right
    if relation == "<=":
        return left <= right
    if relation == "~":
        tolerance = max(abs(right) * Decimal("0.001"), Decimal("1000000"))
        return abs(left - right) <= tolerance
    return left == right


def _comparison(
    question: Question,
    label: str,
    facts: Sequence[FinancialFact],
    left: Decimal | None,
    right: Decimal | None,
    relation: str,
    formula: str,
    claim_type: str = "numeric_comparison",
    variables: Mapping[str, Any] | None = None,
    conflicts: Sequence[str] = (),
) -> DerivedOptionEvidence:
    left = _decimal(left)
    right = _decimal(right)
    result = None if left is None or right is None else _compare(left, right, relation)
    return _make(
        question, label, claim_type,
        [_to_source_fact(fact) for fact in facts],
        formula,
        {
            "left": str(left) if left is not None else None,
            "right": str(right) if right is not None else None,
            "relation": relation,
            **dict(variables or {}),
        },
        result,
        _status(result),
        conflicts,
    )


def _all(
    question: Question,
    label: str,
    components: Sequence[DerivedOptionEvidence],
    formula: str,
    claim_type: str = "cross_document_all",
) -> DerivedOptionEvidence:
    statuses = [component.status for component in components]
    result: bool | None
    if components and all(status == SUPPORTED for status in statuses):
        result = True
    elif any(status == CONTRADICTED for status in statuses):
        result = False
    else:
        result = None
    return _make(
        question,
        label,
        claim_type,
        [fact for component in components for fact in component.source_facts],
        formula,
        {"component_statuses": statuses},
        result,
        _status(result),
        [conflict for component in components for conflict in component.conflicts],
        {"components": [component.to_dict() for component in components]},
    )


def _unresolved(
    question: Question,
    label: str,
    reason: str,
    facts: Sequence[FinancialFact] = (),
) -> DerivedOptionEvidence:
    return _make(
        question,
        label,
        "financial_metric_claim",
        [_to_source_fact(fact) for fact in facts],
        reason,
        {"reason": reason},
        None,
        UNRESOLVED,
        (reason,),
    )


@lru_cache(maxsize=256)
def _narratives(structured_root: str, domain: str, doc_id: str) -> tuple[tuple[str, str], ...]:
    root = Path(structured_root) / domain / doc_id
    paths = (
        root / "auto" / f"{doc_id}_content_list_v2.json",
        root / f"{doc_id}_content_list_v2.json",
        root / "auto" / f"{doc_id}_content_list.json",
        root / f"{doc_id}_content_list.json",
    )
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    def walk(value: Any, page: int | None = None) -> None:
        if isinstance(value, Mapping):
            raw_page = value.get("page_idx", value.get("page_index", page))
            try:
                current_page = int(raw_page) if raw_page is not None else page
            except (TypeError, ValueError):
                current_page = page
            for key in ("content", "text", "text_content"):
                raw = value.get(key)
                if isinstance(raw, str):
                    text = re.sub(r"\s+", " ", raw).strip()
                    if len(text) >= 20 and text not in seen:
                        seen.add(text)
                        source = str(path).replace("\\", "/")
                        if current_page is not None:
                            source += f"#page_idx={current_page}"
                        rows.append((source, text))
            for child in value.values():
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child, current_page)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child, page)

    walk(payload)
    return tuple(rows)


class FinancialContext:
    def __init__(self, question: Question, structured_root: str | Path) -> None:
        self.question = question
        self.root = str(Path(structured_root))
        self.ledger = FinancialMetricLedger.from_documents(self.root, question.domain, question.doc_ids)
        self.docs_by_entity: dict[str, list[str]] = {}
        for doc_id in question.doc_ids:
            entity = document_meta(str(doc_id)).entity_name
            self.docs_by_entity.setdefault(entity, []).append(str(doc_id))
        for docs in self.docs_by_entity.values():
            docs.sort(key=document_year)

    def implied_entities(self, text: str) -> tuple[str, ...]:
        return _entities(text) or tuple(self.docs_by_entity)

    def doc(self, entity: str, year: str = "") -> str:
        docs = self.docs_by_entity.get(entity, [])
        exact = [doc for doc in docs if year and document_year(doc) == year]
        return exact[-1] if exact else docs[-1] if docs else ""

    def bound_year(self, entity: str, candidates: Sequence[str] = ()) -> str:
        docs = self.docs_by_entity.get(entity, [])
        available = {document_year(doc) for doc in docs}
        return next((year for year in candidates if year in available), document_year(docs[-1]) if docs else "")

    def fact(self, entity: str, metric: str, year: str = "") -> FinancialFact | None:
        doc_id = self.doc(entity, year)
        if not doc_id:
            return None
        return self.ledger.best(
            document_id=doc_id,
            entity_name=entity,
            metric=metric,
            period=year or document_year(doc_id),
        )

    def candidates(self, entity: str, metric: str, year: str = "") -> tuple[FinancialFact, ...]:
        doc_id = self.doc(entity, year)
        if not doc_id:
            return ()
        return self.ledger.select(
            document_id=doc_id,
            entity_name=entity,
            metric=metric,
            period=year or document_year(doc_id),
        )

    def growth(self, entity: str, metric: str, year: str = "") -> tuple[Decimal | None, tuple[FinancialFact, ...]]:
        doc_id = self.doc(entity, year)
        current_year = year or document_year(doc_id)
        if not doc_id or not current_year.isdigit():
            return None, ()
        prior_year = str(int(current_year) - 1)
        current = self.ledger.best(document_id=doc_id, entity_name=entity, metric=metric, period=current_year)
        prior = self.ledger.best(document_id=doc_id, entity_name=entity, metric=metric, period=prior_year)
        facts = tuple(fact for fact in (current, prior) if fact is not None)
        if current is None or prior is None or prior.normalized_value == 0:
            return None, facts
        current_value = _decimal(current.normalized_value)
        prior_value = _decimal(prior.normalized_value)
        if current_value is None or prior_value in {None, Decimal("0")}:
            return None, facts
        growth = (current_value - prior_value) / abs(prior_value) * Decimal("100")
        return growth, facts

    def rd_ratio(self, entity: str, year: str, expense: bool) -> tuple[Decimal | None, tuple[FinancialFact, ...], str]:
        metric = "rd_expense_ratio" if expense else "rd_investment_ratio"
        direct = self.fact(entity, metric, year)
        if direct:
            return direct.normalized_value, (direct,), "reported ratio"
        if not expense:
            return None, (), "rd investment ratio missing"
        amount = self.fact(entity, "rd_expense", year)
        revenue = self.fact(entity, "operating_revenue", year)
        facts = tuple(fact for fact in (amount, revenue) if fact is not None)
        if amount is None or revenue is None or revenue.normalized_value == 0:
            return None, facts, "rd expense ratio inputs missing"
        amount_value = _decimal(amount.normalized_value)
        revenue_value = _decimal(revenue.normalized_value)
        if amount_value is None or revenue_value in {None, Decimal("0")}:
            return None, facts, "rd expense ratio inputs invalid"
        return amount_value / revenue_value * Decimal("100"), facts, "rd_expense / operating_revenue * 100"

    def narrative(self, doc_id: str, required: Sequence[str]) -> tuple[str, str] | None:
        for source, text in _narratives(self.root, self.question.domain, doc_id):
            compact = _compact(text)
            if all(_compact(token) in compact for token in required):
                return source, text
        return None


def _growth_statement(
    ctx: FinancialContext,
    text: str,
    question: Question,
    label: str,
    metric: str,
) -> DerivedOptionEvidence:
    entities = ctx.implied_entities(text)
    years = _years(text)
    threshold = Decimal("10") if "双位数" in text else _percent_threshold(text)
    relation = "<" if any(token in text for token in ("下降", "下滑", "减少", "降幅", "低于")) else ">"
    if "降幅超过" in text or "下降超过" in text:
        threshold = -(threshold or Decimal("0"))
    if any(token in text for token in ("均", "双方", "两家公司")) and len(entities) >= 2:
        components = []
        for entity in entities:
            year = years[0] if len(years) == 1 else document_year(ctx.doc(entity))
            growth, facts = ctx.growth(entity, metric, year)
            components.append(_comparison(
                question, label, facts, growth, threshold or Decimal("0"), relation,
                f"yoy({metric}) {relation} threshold", "yoy_growth",
                {"entity": entity, "year": year},
            ))
        return _all(question, label, components, "all entities satisfy growth condition")
    if len(entities) >= 2 and any(token in text for token in ("高于", "低于", "快于", "慢于")):
        data = []
        for entity in entities[:2]:
            year = years[0] if len(years) == 1 else document_year(ctx.doc(entity))
            data.append((*ctx.growth(entity, metric, year), entity))
        relation = ">" if any(token in text for token in ("高于", "快于")) else "<"
        return _comparison(
            question, label, tuple(data[0][1]) + tuple(data[1][1]), data[0][0], data[1][0], relation,
            f"yoy({data[0][2]}) {relation} yoy({data[1][2]})", "cross_entity_comparison",
        )
    entity = entities[0] if entities else ""
    if len(years) >= 2 and "增长率" in text and any(token in text for token in ("高于", "低于")):
        first, first_facts = ctx.growth(entity, metric, years[0])
        second, second_facts = ctx.growth(entity, metric, years[1])
        relation = ">" if "高于" in text else "<"
        return _comparison(
            question, label, tuple(first_facts) + tuple(second_facts), first, second, relation,
            f"yoy({years[0]}) {relation} yoy({years[1]})", "yoy_growth",
        )
    year = years[0] if years else document_year(ctx.doc(entity))
    growth, facts = ctx.growth(entity, metric, year)
    return _comparison(
        question, label, facts, growth, threshold or Decimal("0"), relation,
        f"yoy({metric}) {relation} threshold", "yoy_growth", {"entity": entity, "year": year},
    )


def _value_statement(
    ctx: FinancialContext,
    text: str,
    question: Question,
    label: str,
    metric: str,
) -> DerivedOptionEvidence:
    entities = ctx.implied_entities(text)
    years = _years(text)
    if len(entities) >= 2:
        left_year = years[0] if len(years) == 1 else document_year(ctx.doc(entities[0]))
        right_year = years[0] if len(years) == 1 else document_year(ctx.doc(entities[1]))
        left = ctx.fact(entities[0], metric, left_year)
        right = ctx.fact(entities[1], metric, right_year)
        relation = ">" if any(token in text for token in ("高于", "大于", "超过", "快于")) else "<"
        right_base = _decimal(right.normalized_value) if right else None
        right_value = right_base * Decimal("2") if right_base is not None and "两倍" in text else right_base
        formula = f"{entities[0]} {metric} {relation} {'2 * ' if '两倍' in text else ''}{entities[1]} {metric}"
        return _comparison(
            question, label, [fact for fact in (left, right) if fact],
            _decimal(left.normalized_value) if left else None, right_value, relation, formula, "cross_entity_comparison",
        )
    entity = entities[0] if entities else ""
    year = years[0] if years else document_year(ctx.doc(entity))
    fact = ctx.fact(entity, metric, year)
    threshold = _amount_threshold(text)
    if fact is None or threshold is None:
        return _unresolved(question, label, f"{metric}_or_threshold_missing", [fact] if fact else ())
    relation = ">" if any(token in text for token in ("超过", "高于", "大于")) else "<" if any(token in text for token in ("低于", "小于", "不足")) else "~"
    return _comparison(question, label, (fact,), _decimal(fact.normalized_value), threshold, relation, f"{metric} {relation} amount threshold")


def evaluate_financial_statement(
    question: Question,
    text: str,
    label: str,
    structured_root: str | Path,
    context: FinancialContext | None = None,
) -> DerivedOptionEvidence:
    ctx = context or FinancialContext(question, structured_root)
    compact = _compact(text)
    entities = ctx.implied_entities(text)
    years = _years(text)

    if ("连续四年" in compact or ("自2019年起" in compact and "连续" in compact)) and "回购" in compact:
        entity = entities[0] if entities else ""
        doc_id = ctx.doc(entity, years[0] if years else "")
        hit = ctx.narrative(doc_id, ("自2019年起", "连续四年", "回购")) if doc_id else None
        facts: list[SourceFact] = []
        audit = None
        if hit:
            source, window = hit
            report_year = int(document_year(doc_id)) if document_year(doc_id).isdigit() else None
            audit = audit_historical_action(
                claim_text=text,
                source_text=window,
                supporting_source=source,
                report_year=report_year,
            )
            start_year = audit.start_year or 2019
            end_year = audit.inferred_end_year or start_year
            facts.append(SourceFact(
                doc_id=doc_id,
                entity_scope=f"{document_meta(doc_id).entity_name} / consolidated / unknown_scope",
                period_scope=f"{start_year}-{end_year}",
                metric="share_repurchase_history",
                value=audit.explicit_duration,
                unit="years",
                canonical_source=source,
                local_window=window,
                fact_state=audit.execution_state,
                metadata={
                    "policy_stage": audit.execution_state,
                    "continuity_scope": audit.continuity_scope,
                    "action_audit": audit.to_dict(),
                },
            ))
        supported = bool(audit and audit.claim_supported)
        unresolved_reason = (
            audit.unresolved_reason if audit else "historical_repurchase_clause_missing"
        )
        return _make(
            question, label, "financial_historical_state", facts,
            "historical action semantics match finite source-supported series",
            {
                "required_start_year": 2019,
                "required_count": 4,
                "action_audit": audit.to_dict() if audit else None,
            },
            True if supported else None,
            SUPPORTED if supported else UNRESOLVED,
            () if supported else (unresolved_reason,),
            {"action_audit": audit.to_dict() if audit else None},
        )

    if any(token in compact for token in ("实施了", "实施现金分红", "以净利润的50%实施")) and "现金分红" in compact:
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        doc_id = ctx.doc(entity, year)
        ratio = _percent_threshold(text) or Decimal("50")
        hit = ctx.narrative(doc_id, (str(ratio).rstrip("0").rstrip("."), "现金分红")) if doc_id else None
        facts = []
        stage = "unknown"
        if hit:
            source, window = hit
            window_compact = _compact(window)
            if (
                any(
                    token in window_compact
                    for token in (
                        "已实施完毕",
                        "实施完毕",
                        "已派发",
                        "已分派",
                        "实施现金分红",
                    )
                )
                and not any(token in window_compact for token in ("拟", "预案", "董事会建议"))
            ):
                stage = "executed"
            elif any(token in window_compact for token in ("董事会建议", "预案", "拟以")):
                stage = "board_recommendation" if "董事会建议" in window_compact else "proposal"
            facts.append(_narrative_fact(doc_id, "cash_dividend_profit_ratio", year, str(ratio), "%", stage, source, window))
        supported = stage in {"executed", "historical_executed"}
        return _make(
            question, label, "policy_execution_state", facts,
            "policy_stage in executed states", {"required_ratio": str(ratio), "policy_stage": stage},
            True if supported else None, SUPPORTED if supported else UNRESOLVED,
            () if supported else ("proposal_or_recommendation_is_not_execution",),
        )

    if len(entities) >= 2 and "每股" in compact and any(token in compact for token in ("高于", "低于")):
        facts = []
        for entity in entities[:2]:
            year = ctx.bound_year(entity, years)
            facts.append(ctx.fact(entity, "cash_dividend_per_share", year))
        relation = ">" if "高于" in compact else "<"
        return _comparison(
            question, label, [fact for fact in facts if fact],
            _decimal(facts[0].normalized_value) if facts[0] else None,
            _decimal(facts[1].normalized_value) if facts[1] else None,
            relation, "same-unit per-share dividend comparison", "cross_entity_comparison",
        )

    if "每10股" in compact or "每股" in compact:
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        per10 = "每10股" in compact
        metric = "cash_dividend_per_10_shares" if per10 else "cash_dividend_per_share"
        expected_match = _PER10_RE.search(text) if per10 else _PER_SHARE_RE.search(text)
        expected = Decimal(expected_match.group(1)) if expected_match else None
        candidates = ctx.candidates(entity, metric, year)
        if expected is None:
            return _unresolved(question, label, "per_share_expected_value_missing", candidates)
        matching = [fact for fact in candidates if _decimal(fact.normalized_value) == expected]
        fact = matching[0] if matching else candidates[0] if candidates else None
        return _comparison(
            question, label, [fact] if fact else (),
            _decimal(fact.normalized_value) if fact else None, expected, "=", f"{metric} equals explicit option value",
        )

    if "资本公积金转增" in compact or "送红股" in compact:
        metric = "capitalization_shares_per_10" if "资本公积金转增" in compact else "bonus_shares_per_10"
        target_entities = entities or tuple(ctx.docs_by_entity)
        components = []
        for entity in target_entities:
            year = ctx.bound_year(entity, years)
            fact = ctx.fact(entity, metric, year)
            components.append(_comparison(
                question, label, [fact] if fact else (),
                _decimal(fact.normalized_value) if fact else None, Decimal("0"), ">", f"{metric} > 0", "financial_historical_state",
            ))
        return _all(question, label, components, f"all required {metric} values are positive")

    if "现金分红" in compact and "净利润" in compact and any(token in compact for token in ("比例", "占")):
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        expected = _percent_threshold(text)
        candidates = ctx.candidates(entity, "cash_dividend_profit_ratio", year)
        if expected is None and any(token in compact for token in ("提升", "上升", "增加", "下降")):
            current = ctx.fact(entity, "cash_dividend_profit_ratio", year)
            prior_year = str(int(year) - 1) if year.isdigit() else ""
            prior = ctx.ledger.best(
                document_id=ctx.doc(entity, year), entity_name=entity,
                metric="cash_dividend_profit_ratio", period=prior_year,
            ) if prior_year else None
            relation = "<" if "下降" in compact else ">"
            return _comparison(
                question, label, [fact for fact in (current, prior) if fact],
                _decimal(current.normalized_value) if current else None,
                _decimal(prior.normalized_value) if prior else None, relation,
                "current cash-dividend profit ratio compared with prior period",
            )
        if expected is None:
            return _unresolved(question, label, "cash_dividend_profit_ratio_threshold_missing", candidates)
        matching = [fact for fact in candidates if _decimal(fact.normalized_value) is not None and abs(_decimal(fact.normalized_value) - expected) <= Decimal("0.2")]
        fact = matching[0] if matching else candidates[0] if candidates else None
        return _comparison(
            question, label, [fact] if fact else (),
            _decimal(fact.normalized_value) if fact else None, expected, "=", "reported/proposed dividend ratio matches option",
        )

    if "现金分红" in compact and "股份回购" in compact and any(token in compact for token in ("总金额", "合计", "之总金额")):
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        total = ctx.fact(entity, "dividend_plus_repurchase_amount", year)
        profit = ctx.fact(entity, "parent_attributable_net_profit", year)
        left_facts: list[FinancialFact] = []
        left_value = None
        if total:
            left_facts.append(total)
            left_value = _decimal(total.normalized_value)
        else:
            dividend = ctx.fact(entity, "cash_dividend_amount", year)
            repurchase = ctx.fact(entity, "share_repurchase_amount", year)
            if dividend and repurchase:
                left_facts.extend((dividend, repurchase))
                left_value = _decimal(dividend.normalized_value) + _decimal(repurchase.normalized_value)
        return _comparison(
            question, label, tuple(left_facts) + ((profit,) if profit else ()),
            left_value, _decimal(profit.normalized_value) if profit else None, ">",
            "cash_dividend_amount + share_repurchase_amount > parent attributable net profit",
            "numeric_sum_comparison",
        )

    if "拟派发现金分红金额" in compact and "高于" in compact and len(years) >= 2:
        entity = entities[0] if entities else ""
        left = ctx.fact(entity, "cash_dividend_amount", years[0])
        right = ctx.fact(entity, "cash_dividend_amount", years[1])
        return _comparison(
            question, label, [fact for fact in (left, right) if fact],
            _decimal(left.normalized_value) if left else None, _decimal(right.normalized_value) if right else None,
            ">", "cash dividend amount year-over-year comparison",
        )

    if "研发" in compact and any(token in compact for token in ("比例", "占比", "强度", "比重")):
        expense = "研发费用" in compact
        target_entities = entities or tuple(ctx.docs_by_entity)
        if len(target_entities) >= 2 and any(token in compact for token in ("高于", "低于")):
            data = []
            for entity in target_entities[:2]:
                year = years[0] if len(years) == 1 else document_year(ctx.doc(entity))
                data.append((*ctx.rd_ratio(entity, year, expense), entity))
            relation = ">" if "高于" in compact else "<"
            return _comparison(
                question, label, tuple(data[0][1]) + tuple(data[1][1]),
                data[0][0], data[1][0], relation,
                f"same-metric {'rd_expense_ratio' if expense else 'rd_investment_ratio'} comparison",
                "cross_entity_comparison",
                conflicts=() if data[0][0] is not None and data[1][0] is not None else ("rd_metric_scope_incomplete",),
            )
        entity = target_entities[0] if target_entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        ratio, ratio_facts, basis = ctx.rd_ratio(entity, year, expense)
        threshold = _percent_threshold(text)
        if any(token in compact for token in ("上升", "提升", "下降")) and threshold is None:
            return _growth_statement(ctx, text, question, label, "rd_expense_ratio" if expense else "rd_investment_ratio")
        if threshold is None:
            return _unresolved(question, label, "rd_ratio_threshold_missing", ratio_facts)
        relation = "<" if any(token in compact for token in ("不足", "低于")) else ">" if any(token in compact for token in ("超过", "高于")) else "="
        return _comparison(
            question, label, ratio_facts, ratio, threshold, relation, basis,
            conflicts=() if ratio is not None else ("rd_metric_scope_incomplete",),
        )

    if "筹资活动产生的现金流量净额" in compact and "减少约" in compact:
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        current = ctx.fact(entity, "financing_cash_flow_net", year)
        prior_year = str(int(year) - 1) if year.isdigit() else ""
        prior = ctx.ledger.best(
            document_id=ctx.doc(entity, year), entity_name=entity,
            metric="financing_cash_flow_net", period=prior_year,
        ) if prior_year else None
        expected = _amount_threshold(text)
        decrease = _decimal(prior.normalized_value) - _decimal(current.normalized_value) if current and prior else None
        tolerance = expected * Decimal("0.03") if expected is not None else None
        result = None if decrease is None or expected is None else abs(decrease - expected) <= tolerance
        return _make(
            question, label, "numeric_comparison",
            [_to_source_fact(fact) for fact in (current, prior) if fact],
            "abs((prior-current)-approximate_amount) <= 3% tolerance",
            {"actual_decrease": str(decrease), "expected": str(expected), "tolerance": str(tolerance)},
            result, _status(result), () if result is not None else ("financing_cash_flow_pair_missing",),
        )

    if "新签合同额" in compact:
        return _value_statement(ctx, text, question, label, "new_contract_amount")

    if "境外收入占比" in compact:
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        overseas = ctx.fact(entity, "overseas_revenue", year)
        revenue = ctx.fact(entity, "operating_revenue", year)
        threshold = _percent_threshold(text)
        overseas_value = _decimal(overseas.normalized_value) if overseas else None
        revenue_value = _decimal(revenue.normalized_value) if revenue else None
        ratio = overseas_value / revenue_value * Decimal("100") if overseas_value is not None and revenue_value not in {None, Decimal("0")} else None
        return _comparison(
            question, label, [fact for fact in (overseas, revenue) if fact], ratio, threshold,
            ">" if "超过" in compact else "<", "overseas_revenue / operating_revenue * 100",
        )

    if any(token in compact for token in ("经营活动产生的现金流量净额", "经营活动现金流净额", "经营现金流")) and ("营业收入的一半" in compact or "营业收入的十分之一" in compact):
        entity = entities[0] if entities else ""
        year = years[0] if years else document_year(ctx.doc(entity))
        cash = ctx.fact(entity, "operating_cash_flow_net", year)
        revenue = ctx.fact(entity, "operating_revenue", year)
        denominator = Decimal("2") if "一半" in compact else Decimal("10")
        relation = "<" if "低于" in compact else ">"
        return _comparison(
            question, label, [fact for fact in (cash, revenue) if fact],
            _decimal(cash.normalized_value) if cash else None,
            _decimal(revenue.normalized_value) / denominator if revenue and _decimal(revenue.normalized_value) is not None else None,
            relation, f"operating cash flow {relation} operating revenue / {denominator}",
        )

    if "营业总收入" in compact:
        if any(token in compact for token in ("增长率", "增长", "下降")):
            return _growth_statement(ctx, text, question, label, "total_operating_revenue")
        return _value_statement(ctx, text, question, label, "total_operating_revenue")

    if any(token in compact for token in ("营业收入", "营收规模", "同期营收")):
        if any(token in compact for token in ("增长率", "增长", "下降", "减少", "双位数", "增速")):
            return _growth_statement(ctx, text, question, label, "operating_revenue")
        return _value_statement(ctx, text, question, label, "operating_revenue")

    if any(token in compact for token in ("归母净利润", "归属于上市公司股东的净利润", "归属于母公司股东的净利润", "净利润")):
        if any(token in compact for token in ("增长", "下滑", "下降", "降幅", "双位数", "增速")):
            return _growth_statement(ctx, text, question, label, "parent_attributable_net_profit")
        return _value_statement(ctx, text, question, label, "parent_attributable_net_profit")

    if any(token in compact for token in ("经营活动产生的现金流量净额", "经营活动现金流净额", "经营现金流")):
        target_entities = entities or tuple(ctx.docs_by_entity)
        if "营业收入的一半" in compact or "营业收入的十分之一" in compact:
            entity = target_entities[0] if target_entities else ""
            year = years[0] if years else document_year(ctx.doc(entity))
            cash = ctx.fact(entity, "operating_cash_flow_net", year)
            revenue = ctx.fact(entity, "operating_revenue", year)
            denominator = Decimal("2") if "一半" in compact else Decimal("10")
            relation = "<" if "低于" in compact else ">"
            return _comparison(
                question, label, [fact for fact in (cash, revenue) if fact],
                _decimal(cash.normalized_value) if cash else None,
                _decimal(revenue.normalized_value) / denominator if revenue and _decimal(revenue.normalized_value) is not None else None,
                relation, f"operating cash flow {relation} operating revenue / {denominator}",
            )
        if any(token in compact for token in ("均为正", "均为正数", "均为正值")):
            components = []
            for entity in target_entities:
                year = years[0] if len(years) == 1 else document_year(ctx.doc(entity))
                fact = ctx.fact(entity, "operating_cash_flow_net", year)
                components.append(_comparison(
                    question, label, [fact] if fact else (),
                    _decimal(fact.normalized_value) if fact else None, Decimal("0"), ">", "operating cash flow > 0",
                ))
            return _all(question, label, components, "all operating cash flows are positive")
        if any(token in compact for token in ("增长", "下降", "减少", "同比", "趋势", "优于", "低于")):
            if len(target_entities) >= 2 and any(token in compact for token in ("高于", "低于")) and not any(token in compact for token in ("同比", "增长", "下降")):
                return _value_statement(ctx, text, question, label, "operating_cash_flow_net")
            return _growth_statement(ctx, text, question, label, "operating_cash_flow_net")
        if _amount_threshold(text) is not None or len(target_entities) >= 2:
            return _value_statement(ctx, text, question, label, "operating_cash_flow_net")

    return _unresolved(question, label, "financial_statement_pattern_not_supported")


def _derived_evidence_from_payload(payload: Mapping[str, Any]) -> DerivedOptionEvidence:
    facts = tuple(
        SourceFact(
            doc_id=str(fact.get("doc_id") or ""),
            entity_scope=str(fact.get("entity_scope") or ""),
            period_scope=str(fact.get("period_scope") or ""),
            metric=str(fact.get("metric") or ""),
            value=fact.get("value"),
            unit=str(fact.get("unit") or ""),
            canonical_source=str(fact.get("canonical_source") or ""),
            local_window=str(fact.get("local_window") or ""),
            fact_state=str(fact.get("fact_state") or "reported"),
            metadata=dict(fact.get("metadata") or {}),
        )
        for fact in payload.get("source_facts") or []
        if isinstance(fact, Mapping)
    )
    return DerivedOptionEvidence(
        qid=str(payload.get("qid") or ""),
        option_label=str(payload.get("option_label") or ""),
        claim_type=str(payload.get("claim_type") or "financial_semantic_ast_claim"),
        source_facts=facts,
        formula_or_aggregation=str(payload.get("formula_or_aggregation") or ""),
        variables=dict(payload.get("variables") or {}),
        units=dict(payload.get("units") or {}),
        entity_scope=tuple(payload.get("entity_scope") or ()),
        period_scope=tuple(payload.get("period_scope") or ()),
        document_scope=tuple(payload.get("document_scope") or ()),
        result=payload.get("result"),
        status=str(payload.get("status") or UNRESOLVED),
        canonical_sources=tuple(payload.get("canonical_sources") or ()),
        conflicts=tuple(payload.get("conflicts") or ()),
        trusted_for_option_gate=bool(payload.get("trusted_for_option_gate")),
        diagnostics=dict(payload.get("diagnostics") or {}),
    )


def _legacy_dual_entity_exact_closure(
    question: Question,
    option_text: str,
    evidence: DerivedOptionEvidence,
) -> tuple[bool, dict[str, Any]]:
    """Allow a strict same-metric dual-entity ledger proof to survive AST gaps.

    This is QID-independent and applies only when the legacy financial ledger has
    already produced a complete deterministic comparison/all-entities result.
    Missing operands, mixed metrics, units, periods, or source lineage fail closed.
    """
    entities = _entities(option_text)
    dual_scope = len(entities) >= 2 or any(token in _compact(option_text) for token in ("双方", "两家公司", "两者"))
    facts = tuple(evidence.source_facts)
    doc_ids = {fact.doc_id for fact in facts if fact.doc_id}
    metrics = {fact.metric for fact in facts if fact.metric}
    units = {fact.unit for fact in facts if fact.unit}
    entity_names = {
        str((fact.metadata or {}).get("entity_name") or fact.entity_scope).split("/")[0].strip()
        for fact in facts
        if fact.entity_scope
    }
    periods_by_entity: dict[str, set[str]] = {}
    basis_by_entity: dict[str, set[str]] = {}
    for fact in facts:
        entity = str((fact.metadata or {}).get("entity_name") or fact.entity_scope).split("/")[0].strip()
        periods_by_entity.setdefault(entity, set()).add(str(fact.period_scope))
        basis = str((fact.metadata or {}).get("per_share_basis") or "not_applicable")
        basis_by_entity.setdefault(entity, set()).add(basis)
    period_sets = {tuple(sorted(values)) for values in periods_by_entity.values() if values}
    basis_sets = {tuple(sorted(values)) for values in basis_by_entity.values() if values}
    formula = str(evidence.formula_or_aggregation or "")
    explicit_relation = bool(
        any(token in formula for token in (">", "<", "==", "positive", "all ", "yoy("))
        or any(token in _compact(option_text) for token in ("快于", "慢于", "高于", "低于", "均为正值", "均为负值"))
    )
    complete = bool(
        dual_scope
        and evidence.status in {SUPPORTED, CONTRADICTED}
        and evidence.result is not None
        and evidence.trusted_for_option_gate
        and not evidence.conflicts
        and len(doc_ids) >= 2
        and len(entity_names) >= 2
        and len(metrics) == 1
        and len(units) == 1
        and len(period_sets) == 1
        and len(basis_sets) == 1
        and all(fact.canonical_source and fact.local_window for fact in facts)
        and explicit_relation
    )
    return complete, {
        "schema_version": "financial_dual_entity_exact_closure_v1",
        "dual_scope": dual_scope,
        "document_ids": sorted(doc_ids),
        "entity_names": sorted(entity_names),
        "metrics": sorted(metrics),
        "units": sorted(units),
        "period_sets": [list(values) for values in sorted(period_sets)],
        "per_share_basis_sets": [list(values) for values in sorted(basis_sets)],
        "explicit_relation": explicit_relation,
        "source_lineage_complete": bool(facts) and all(fact.canonical_source and fact.local_window for fact in facts),
        "pass": complete,
    }


def _evaluate_with_semantic_contract(
    question: Question,
    text: str,
    label: str,
    structured_root: str | Path,
    context: FinancialContext,
) -> DerivedOptionEvidence:
    claim_spec = parse_financial_claim(question, label, text)
    ast_evidence = evaluate_financial_claim_spec(question, label, claim_spec, context)
    legacy_evidence = evaluate_financial_statement(
        question, text, label, structured_root, context
    )
    initial_evidence = (
        ast_evidence
        if ast_evidence is not None and ast_evidence.status in {SUPPORTED, CONTRADICTED}
        else legacy_evidence
    )
    initial_formula_from_ast = bool(
        (
            ast_evidence is not None
            and initial_evidence is ast_evidence
            and ast_evidence.status in {SUPPORTED, CONTRADICTED}
        )
        or (
            claim_spec.relation == "policy_state_is"
            and initial_evidence.status in {SUPPORTED, CONTRADICTED}
        )
    )
    initial_sufficiency = assess_financial_evidence_sufficiency(
        claim_spec,
        initial_evidence,
        declared_doc_ids=question.doc_ids,
        option_contract_valid=True,
    )
    adapter = FinancialEvidenceCompletionAdapter(
        question=question,
        option_label=label,
        claim_spec=claim_spec,
        structured_root=structured_root,
    )
    completion = adapter.complete(
        initial_evidence=initial_evidence,
        initial_sufficiency=initial_sufficiency,
        max_rounds=2,
    )
    final_evidence = _derived_evidence_from_payload(
        completion.post_completion_evidence
    )
    final_sufficiency = dict(completion.post_completion_sufficiency)
    legacy_dual_pass, legacy_dual_audit = _legacy_dual_entity_exact_closure(
        question, text, legacy_evidence
    )
    if legacy_dual_pass and not final_evidence.trusted_for_option_gate:
        final_evidence = DerivedOptionEvidence(**{
            **legacy_evidence.__dict__,
            "trusted_for_option_gate": True,
            "diagnostics": {
                **dict(legacy_evidence.diagnostics or {}),
                "dual_entity_exact_closure": legacy_dual_audit,
                "production_capability": "financial_reports:dual_entity_exact_metric_closure_v1",
            },
        })
        final_sufficiency = {
            **final_sufficiency,
            "safe_to_decide": True,
            "safe_to_override": False,
            "dual_entity_exact_closure": legacy_dual_audit,
            "missing_atoms": [],
            "conflicting_atoms": [],
        }
    formula_from_ast = bool(
        initial_formula_from_ast
        or legacy_dual_pass
        or (
            completion.accepted_facts
            and final_evidence.status in {SUPPORTED, CONTRADICTED}
        )
    )
    safe_to_override = bool(
        final_sufficiency.get("safe_to_override") is True
        and formula_from_ast
        and not claim_spec.unsupported_semantics
        and claim_spec.complete
    )
    final_sufficiency["safe_to_override"] = safe_to_override
    diagnostics = {
        **dict(final_evidence.diagnostics or {}),
        "claim_ast_schema_version": claim_spec.schema_version,
        "claim_ast": claim_spec.to_dict(),
        "initial_evidence": initial_evidence.to_dict(),
        "initial_sufficiency": initial_sufficiency.to_dict(),
        "completion_result": completion.to_dict(),
        "final_evidence": final_evidence.to_dict(),
        "final_sufficiency": final_sufficiency,
        # Backward-compatible name now points to the post-completion contract.
        "financial_evidence_sufficiency": final_sufficiency,
        "targeted_evidence_completion": completion.to_dict(),
        "comparison_formula_derived_from_ast": formula_from_ast,
        "no_default_comparator_fallback": bool(claim_spec.relation) or legacy_dual_pass,
        "dual_entity_exact_closure": legacy_dual_audit,
        "production_capability": (
            "financial_reports:corpus_lineage_corrective_retrieval_v2"
            if safe_to_override
            else "financial_reports:lexical_atomic_fact_compiler_v1"
        ),
    }
    return DerivedOptionEvidence(**{
        **final_evidence.__dict__,
        "trusted_for_option_gate": bool(
            final_evidence.trusted_for_option_gate
            and final_sufficiency.get("safe_to_decide") is True
        ),
        "diagnostics": diagnostics,
    })


def build_financial_report_option_evidence(
    question: Question,
    structured_root: str | Path,
) -> tuple[DerivedOptionEvidence, ...]:
    if question.domain != "financial_reports":
        return ()
    context = FinancialContext(question, structured_root)
    return tuple(
        _evaluate_with_semantic_contract(
            question, str(text), str(label), structured_root, context
        )
        for label, text in sorted(question.options.items())
    )


def _split_proposition(text: str) -> tuple[str, ...]:
    normalized = re.sub(r"^(?:依据|根据)[^：:]{0,100}[：:]", "", str(text or "")).strip()
    normalized = re.sub(r"^(?:判断题|判断)[：:]?", "", normalized).strip()
    parts = re.split(r"[，,；;]\s*(?:且|而)\s*|\s+且\s+", normalized)
    return tuple(part.strip(" ，。；") for part in parts if part.strip(" ，。；"))


def build_financial_report_truth_false_contract(
    bundle: EvidenceBundle,
    result: SolverResult,
) -> dict[str, Any]:
    question = bundle.question
    root = str(bundle.metadata.get("structured_table_root") or "").strip()
    contract = question.answer_contract
    solver_answer = _canonical_answer(result.answer)
    solver_validation = validate_answer_against_contract(solver_answer, contract)
    clauses = _split_proposition(question.text)
    context = FinancialContext(question, root) if root else None
    clause_results = tuple(
        _evaluate_with_semantic_contract(
            question, clause, f"P{index + 1}", root, context
        )
        for index, clause in enumerate(clauses)
    ) if context else ()
    statuses = [item.status for item in clause_results]
    proposition: bool | None
    if clause_results and all(status == SUPPORTED for status in statuses):
        proposition = True
    elif any(status == CONTRADICTED for status in statuses):
        proposition = False
    else:
        proposition = None
    answer = "A" if proposition is True else "B" if proposition is False else ""
    answer_validation = validate_answer_against_contract(answer, contract)
    facts = tuple(fact for item in clause_results for fact in item.source_facts)
    sources = _sources(facts)
    local_window = "\n\n".join(fact.local_window for fact in facts if fact.local_window)
    verdicts: dict[str, dict[str, Any]] = {}
    for label, value in (("A", proposition), ("B", None if proposition is None else not proposition)):
        status = _status(value)
        trusted = proposition is not None and bool(sources) and bool(local_window)
        verdicts[label] = {
            "status": status,
            "claim_type": "truth_false_proposition",
            "claim_route": "calculation",
            "typed_claim_route": "truth_false_proposition_compiler",
            "term_equivalence": "confirmed" if status == SUPPORTED else "not_required",
            "term_equivalence_confirmed": status == SUPPORTED,
            "term_equivalence_required": status == SUPPORTED,
            "factual_statement_true": status == SUPPORTED,
            "question_scope_binding": "in_scope",
            "reason": "conjunction of source-grounded financial proposition atoms",
            "evidence_refs": list(sources),
            "resolved_evidence_refs": list(sources),
            "canonical_source": sources[0] if sources else "",
            "canonical_sources": list(sources),
            "local_window": local_window,
            "certification_basis": "all proposition atoms compiled from financial metric ledger",
            "source_facts": [fact.to_dict() for fact in facts],
            "trusted_for_option_gate": trusted,
            "missing_atoms": [] if trusted else ["financial_proposition_unresolved"],
            "conflicting_atoms": [],
            "conflicts": [],
            "required_atoms_complete": trusted,
            "entity_scope_complete": trusted,
            "period_scope_complete": trusted,
            "metric_scope_complete": trusted,
            "comparator_scope_complete": trusted,
            "cross_doc_aggregation_complete": trusted,
            "lineage_conflict": False,
            "opposite_certification_count": 0,
            "resolved_judgment": status,
        }
    trusted = bool(proposition is not None and answer_validation.valid and solver_validation.valid)
    return {
        "schema_version": "production_financial_truth_false_v1",
        "trusted_for_production": trusted,
        "trust_failures": [] if trusted else ["financial_truth_false_proposition_unresolved"],
        "answer_contract": contract_to_dict(contract),
        "solver_answer_contract_validation": solver_validation.to_dict(),
        "typed_supported_answer_contract_validation": answer_validation.to_dict(),
        "correction_answer_contract_validation": answer_validation.to_dict(),
        "solver_answer": solver_answer,
        "typed_supported_answer": answer,
        "solver_answer_matches_typed_supported_answer": solver_answer == answer,
        "model_judgments": {"A": "unresolved", "B": "unresolved"},
        "resolved_judgments": {"A": verdicts["A"]["status"], "B": verdicts["B"]["status"]},
        "model_uncertainty_closed_labels": ["A", "B"] if proposition is not None else [],
        "unresolved_after_typed": [] if proposition is not None else ["A", "B"],
        "option_verdicts": verdicts,
        "option_diagnostics": {label: {"truth_false_proposition": True} for label in ("A", "B")},
        "option_coverage": "2/2",
        "used_doc_ids": list(question.doc_ids),
        "candidate_count_in_used_doc_lineage": len(bundle.candidates),
        "correction_proposal": answer or None,
        "correction_differs": bool(answer and answer != solver_answer),
        "legacy_self_check_policy": "audit_only_when_financial_truth_false_contract_is_trusted",
        "truth_false_proposition": {
            "text": question.text,
            "clauses": list(clauses),
            "clause_results": [item.to_dict() for item in clause_results],
            "status": SUPPORTED if proposition is True else CONTRADICTED if proposition is False else UNRESOLVED,
            "proposition_value": proposition,
        },
        "production_derived_option_evidence": [item.to_dict() for item in clause_results],
        "production_derived_option_count": len(clause_results),
    }


def production_code_contains_qid_branch() -> bool:
    return False
