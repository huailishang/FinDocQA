"""Production deterministic routing from option text to derived evidence.

The router is intentionally QID-agnostic.  It derives claim type and scope from
question/option text, document ids and structured table rows, then delegates the
actual calculation/state logic to :mod:.
It fails closed whenever a required source fact cannot be selected uniquely.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from contracts import Question
from evidence.structured_tables import StructuredTableRow, load_structured_table_rows
from verification.compound_claims import (
    OptionClaimRoute,
    extract_entities,
    route_option_claim,
)
from verification.derived_option_evidence import (
    DerivedOptionEvidence,
    SourceFact,
    cross_document_all,
    cross_entity_comparison,
    numeric_sum_comparison,
    policy_execution_state,
    yoy_growth,
)
from verification.financial_metric_ledger import FinancialMetricLedger
from verification.financial_report_claims import build_financial_report_option_evidence
from verification.research_near_ready_evidence import build_research_option_evidence


_DOC_ENTITY_RULES: tuple[tuple[str, str], ...] = (
    ("catl", "宁德时代"),
    ("byd", "比亚迪"),
    ("midea", "美的集团"),
    ("chinamobile", "中国移动"),
)

_PARENT_PROFIT_TERMS = (
    "归属于上市公司股东的净利润",
    "归属于母公司股东的净利润",
    "归属于母公司所有者的净利润",
    "归母净利润",
)
_SUBSIDIARY_TERMS = (
    "公司类型=子公司",
    "全资子公司",
    "控股子公司",
    "主要子公司",
)
_POLICY_PROPOSAL_TERMS = ("预案", "拟", "董事会建议")
_POLICY_APPROVED_TERMS = ("股东会审议通过", "股东大会审议通过", "已经审议通过")
_POLICY_EXECUTED_TERMS = ("已实施完毕", "实施完毕", "已派发", "已分派", "已完成")
_POLICY_NEGATIVE_TERMS = ("未实施", "未执行", "尚未实施", "不实施")

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:年)?")
_KV_RE = re.compile(r"(?:^|\|\s*)([^|=]+?)=([^|]+)")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,\s]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*[%％]")


@dataclass(frozen=True)
class RowSelection:
    selected: StructuredTableRow | None
    rejected: tuple[Mapping[str, Any], ...]
    reason: str


def _compact(value: Any) -> str:
    return "".join(str(value or "").lower().replace("％", "%").split())


def _doc_entity(doc_id: str) -> str:
    lowered = str(doc_id or "").lower()
    for token, entity in _DOC_ENTITY_RULES:
        if token in lowered:
            return entity
    return ""


def _doc_year(doc_id: str) -> str:
    match = _YEAR_RE.search(str(doc_id or ""))
    return match.group(1) if match else ""


def _row_values(row: StructuredTableRow) -> dict[str, str]:
    return {
        str(key).strip(): str(value).strip()
        for key, value in _KV_RE.findall(row.normalized_row_text)
    }


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace(" ", "")
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None


def _column_number(row: StructuredTableRow, key: str) -> Decimal | None:
    values = _row_values(row)
    if key in values:
        return _decimal(values[key])
    return None


def _infer_unit(row: StructuredTableRow) -> tuple[str, Decimal]:
    text = row.normalized_row_text
    if "千元" in text:
        return "CNY", Decimal("1000")
    if "百万元" in text or "人民币百万元" in text:
        return "CNY", Decimal("1000000")
    if "亿元" in text:
        return "CNY", Decimal("100000000")
    return "CNY", Decimal("1")


def _entity_scope(doc_id: str) -> str:
    entity = _doc_entity(doc_id)
    return f"{entity} consolidated parent-attributable" if entity else "consolidated parent-attributable"


def _fact(
    row: StructuredTableRow,
    *,
    period: str,
    metric: str,
    value: Decimal | float | str | None,
    unit: str = "CNY",
    state: str = "reported",
    metadata: Mapping[str, Any] | None = None,
) -> SourceFact:
    return SourceFact(
        doc_id=row.doc_id,
        entity_scope=_entity_scope(row.doc_id),
        period_scope=period,
        metric=metric,
        value=str(value) if value is not None else None,
        unit=unit,
        canonical_source=row.canonical_source,
        local_window=row.normalized_row_text,
        fact_state=state,
        metadata=dict(metadata or {}),
    )


def _is_parent_profit_row(row: StructuredTableRow) -> bool:
    text = row.normalized_row_text
    if not any(term in text for term in _PARENT_PROFIT_TERMS):
        return False
    if any(term in text for term in _SUBSIDIARY_TERMS):
        return False
    if "公司名称=" in text and "净利润=" in text and not any(
        term in text for term in _PARENT_PROFIT_TERMS
    ):
        return False
    return True


def _select_parent_profit_row(rows: Sequence[StructuredTableRow]) -> RowSelection:
    candidates: list[tuple[tuple[int, int, int, int], StructuredTableRow]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        text = row.normalized_row_text
        reasons: list[str] = []
        subsidiary_row = any(term in text for term in _SUBSIDIARY_TERMS)
        if subsidiary_row and "净利润" in text:
            rejected.append({
                "canonical_source": row.canonical_source,
                "row_text": text,
                "reasons": ["subsidiary_scope"],
            })
            continue
        if not any(term in text for term in _PARENT_PROFIT_TERMS):
            continue
        values = _row_values(row)
        current = _decimal(values.get("column_2"))
        prior = _decimal(values.get("column_3"))
        if current is None or prior is None:
            reasons.append("current_prior_values_missing")
        if reasons:
            rejected.append({
                "canonical_source": row.canonical_source,
                "row_text": text,
                "reasons": reasons,
            })
            continue
        has_growth = 1 if _PERCENT_RE.search(text) else 0
        explicit_parent = 1 if any(term in text for term in _PARENT_PROFIT_TERMS[:3]) else 0
        early_summary_page = 1 if row.page_idx <= 25 else 0
        # Prefer primary summary rows, explicit parent-profit wording and a
        # reported growth column.  Deterministic row coordinates break ties.
        rank = (early_summary_page, explicit_parent, has_growth, -row.page_idx)
        candidates.append((rank, row))
    if not candidates:
        return RowSelection(None, tuple(rejected), "parent_profit_row_missing")
    candidates.sort(key=lambda item: (item[0], -item[1].table_index, -item[1].row_index), reverse=True)
    return RowSelection(candidates[0][1], tuple(rejected), "selected")


def _growth_component(
    *,
    qid: str,
    option_label: str,
    doc_id: str,
    rows: Sequence[StructuredTableRow],
    threshold_percent: float,
    relation: str,
) -> tuple[DerivedOptionEvidence, tuple[Mapping[str, Any], ...]]:
    selection = _select_parent_profit_row(rows)
    row = selection.selected
    if row is None:
        evidence = yoy_growth(
            qid=qid,
            option_label=option_label,
            current_fact=None,
            prior_fact=None,
            threshold_percent=threshold_percent,
            relation=relation,
        )
        return evidence, selection.rejected
    current_raw = _column_number(row, "column_2")
    prior_raw = _column_number(row, "column_3")
    unit, multiplier = _infer_unit(row)
    current = current_raw * multiplier if current_raw is not None else None
    prior = prior_raw * multiplier if prior_raw is not None else None
    current_year = _doc_year(doc_id)
    prior_year = str(int(current_year) - 1) if current_year.isdigit() else "prior_period"
    current_fact = _fact(
        row,
        period=current_year or "current_period",
        metric="parent_attributable_net_profit",
        value=current,
        unit=unit,
        metadata={
            "source_value": str(current_raw) if current_raw is not None else None,
            "normalization_multiplier": str(multiplier),
        },
    )
    prior_fact = _fact(
        row,
        period=prior_year,
        metric="parent_attributable_net_profit",
        value=prior,
        unit=unit,
        metadata={
            "source_value": str(prior_raw) if prior_raw is not None else None,
            "normalization_multiplier": str(multiplier),
        },
    )
    evidence = yoy_growth(
        qid=qid,
        option_label=option_label,
        current_fact=current_fact,
        prior_fact=prior_fact,
        threshold_percent=threshold_percent,
        relation=relation,
    )
    return evidence, selection.rejected


def _select_doc_ids(question: Question, option_text: str) -> tuple[str, ...]:
    entities = extract_entities(option_text)
    if not entities:
        return tuple(str(doc_id) for doc_id in question.doc_ids)
    selected = [
        str(doc_id)
        for entity in entities
        for doc_id in question.doc_ids
        if _doc_entity(str(doc_id)) == entity
    ]
    return tuple(dict.fromkeys(selected))


def _policy_evidence(
    question: Question,
    label: str,
    option_text: str,
    route: OptionClaimRoute,
    rows_by_doc: Mapping[str, Sequence[StructuredTableRow]],
) -> DerivedOptionEvidence:
    required_period = route.periods[0] if route.periods else max(
        (_doc_year(doc_id) for doc_id in question.doc_ids), default=""
    )
    required_ratio = route.threshold_percent if route.threshold_percent is not None else 0.0
    facts: list[SourceFact] = []
    for doc_id in _select_doc_ids(question, option_text):
        if required_period and _doc_year(doc_id) and _doc_year(doc_id) != required_period:
            continue
        for row in rows_by_doc.get(doc_id, ()):
            text = row.normalized_row_text
            percent_values = [Decimal(value) for value in _PERCENT_RE.findall(text)]
            if Decimal(str(required_ratio)) not in percent_values:
                continue
            if not any(token in text for token in ("利润分配", "现金分红", "分红")):
                continue
            if any(token in text for token in _POLICY_NEGATIVE_TERMS):
                stage = "not_executed"
            elif any(token in text for token in _POLICY_PROPOSAL_TERMS):
                # The same disclosure may mention an already distributed
                # interim dividend while proposing a new annual 50% policy.
                # Bind the required ratio to the proposal wording first.
                stage = "proposal"
            elif any(token in text for token in _POLICY_APPROVED_TERMS):
                stage = "approved"
            elif any(token in text for token in _POLICY_EXECUTED_TERMS):
                stage = "executed"
            else:
                continue
            facts.append(
                _fact(
                    row,
                    period=required_period or _doc_year(doc_id) or "current_period",
                    metric="cash_dividend_profit_ratio",
                    value=required_ratio,
                    unit="%",
                    state=stage,
                    metadata={"policy_stage": stage},
                )
            )
    return policy_execution_state(
        qid=question.qid,
        option_label=label,
        facts=facts,
        required_period=required_period or "current_period",
        required_ratio=required_ratio,
    )


def _find_amount_row(
    rows: Sequence[StructuredTableRow],
    *,
    include_all: Sequence[str],
    exclude_any: Sequence[str] = (),
) -> RowSelection:
    found: list[StructuredTableRow] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        text = row.normalized_row_text
        if not all(token in text for token in include_all):
            continue
        reasons: list[str] = []
        if any(token in text for token in exclude_any):
            reasons.append("excluded_semantic_variant")
        if _column_number(row, "column_2") is None:
            reasons.append("column_2_numeric_missing")
        if reasons:
            rejected.append({
                "canonical_source": row.canonical_source,
                "row_text": text,
                "reasons": reasons,
            })
            continue
        found.append(row)
    if not found:
        return RowSelection(None, tuple(rejected), "amount_row_missing")
    found.sort(key=lambda row: (row.page_idx, row.table_index, row.row_index))
    return RowSelection(found[0], tuple(rejected), "selected")


def _sum_evidence(
    question: Question,
    label: str,
    option_text: str,
    route: OptionClaimRoute,
    rows_by_doc: Mapping[str, Sequence[StructuredTableRow]],
) -> DerivedOptionEvidence:
    target_period = route.periods[0] if route.periods else ""
    doc_ids = list(_select_doc_ids(question, option_text))
    if target_period:
        matching = [doc_id for doc_id in doc_ids if _doc_year(doc_id) == target_period]
        if matching:
            doc_ids = matching
    if not target_period and doc_ids:
        doc_ids = sorted(doc_ids, key=_doc_year, reverse=True)[:1]
    if not doc_ids:
        return numeric_sum_comparison(
            qid=question.qid,
            option_label=label,
            left_facts=(),
            right_fact=None,
            comparator=route.comparator or ">",
        )
    doc_id = doc_ids[0]
    rows = rows_by_doc.get(doc_id, ())
    dividend_sel = _find_amount_row(
        rows,
        include_all=("现金分红金额",),
        exclude_any=("总额", "以其他方式", "占"),
    )
    repurchase_sel = _find_amount_row(
        rows,
        include_all=("回购股份", "现金分红金额"),
    )
    profit_sel = _select_parent_profit_row(rows)
    left_facts: list[SourceFact] = []
    for selection, metric in (
        (dividend_sel, "cash_dividend_amount"),
        (repurchase_sel, "share_repurchase_amount"),
    ):
        row = selection.selected
        if row is None:
            continue
        raw = _column_number(row, "column_2")
        unit, multiplier = _infer_unit(row)
        value = raw * multiplier if raw is not None else None
        left_facts.append(
            _fact(
                row,
                period=target_period or _doc_year(doc_id) or "current_period",
                metric=metric,
                value=value,
                unit=unit,
                metadata={"normalization_multiplier": str(multiplier)},
            )
        )
    right_fact: SourceFact | None = None
    if profit_sel.selected is not None:
        row = profit_sel.selected
        raw = _column_number(row, "column_2")
        unit, multiplier = _infer_unit(row)
        value = raw * multiplier if raw is not None else None
        right_fact = _fact(
            row,
            period=target_period or _doc_year(doc_id) or "current_period",
            metric="parent_attributable_net_profit",
            value=value,
            unit=unit,
            metadata={"normalization_multiplier": str(multiplier)},
        )
    result = numeric_sum_comparison(
        qid=question.qid,
        option_label=label,
        left_facts=left_facts,
        right_fact=right_fact,
        comparator=route.comparator or ">",
    )
    diagnostics = dict(result.diagnostics or {})
    diagnostics.update({
        "rejected_source_rows": [
            *dividend_sel.rejected,
            *repurchase_sel.rejected,
            *profit_sel.rejected,
        ],
        "selected_doc_id": doc_id,
    })
    return DerivedOptionEvidence(**{**result.__dict__, "diagnostics": diagnostics})


def _single_growth_evidence(
    question: Question,
    label: str,
    option_text: str,
    route: OptionClaimRoute,
    rows_by_doc: Mapping[str, Sequence[StructuredTableRow]],
) -> DerivedOptionEvidence:
    doc_ids = list(_select_doc_ids(question, option_text))
    target_period = route.periods[0] if route.periods else ""
    if target_period:
        period_docs = [doc_id for doc_id in doc_ids if _doc_year(doc_id) == target_period]
        if period_docs:
            doc_ids = period_docs
    if len(doc_ids) > 1 and extract_entities(option_text):
        doc_ids = doc_ids[:1]
    threshold = float(route.threshold_percent or 0.0)
    relation = route.comparator or ">"
    if "降幅" in option_text and relation == "<":
        threshold = -abs(threshold)
    if not doc_ids:
        evidence, rejected = _growth_component(
            qid=question.qid,
            option_label=label,
            doc_id="",
            rows=(),
            threshold_percent=threshold,
            relation=relation,
        )
    else:
        doc_id = doc_ids[0]
        evidence, rejected = _growth_component(
            qid=question.qid,
            option_label=label,
            doc_id=doc_id,
            rows=rows_by_doc.get(doc_id, ()),
            threshold_percent=threshold,
            relation=relation,
        )
    diagnostics = dict(evidence.diagnostics or {})
    diagnostics.update({
        "route": route.to_dict(),
        "rejected_source_rows": list(rejected),
        "selected_doc_ids": doc_ids,
    })
    return DerivedOptionEvidence(**{**evidence.__dict__, "diagnostics": diagnostics})


def _cross_document_all_evidence(
    question: Question,
    label: str,
    route: OptionClaimRoute,
    rows_by_doc: Mapping[str, Sequence[StructuredTableRow]],
) -> DerivedOptionEvidence:
    threshold = float(route.threshold_percent or 0.0)
    relation = route.comparator or ">"
    components: list[DerivedOptionEvidence] = []
    rejected: list[Mapping[str, Any]] = []
    for doc_id in question.doc_ids:
        component, component_rejected = _growth_component(
            qid=question.qid,
            option_label=label,
            doc_id=str(doc_id),
            rows=rows_by_doc.get(str(doc_id), ()),
            threshold_percent=threshold,
            relation=relation,
        )
        components.append(component)
        rejected.extend(component_rejected)
    result = cross_document_all(
        qid=question.qid,
        option_label=label,
        components=components,
    )
    diagnostics = dict(result.diagnostics or {})
    diagnostics.update({
        "route": route.to_dict(),
        "rejected_source_rows": list(rejected),
    })
    return DerivedOptionEvidence(**{**result.__dict__, "diagnostics": diagnostics})


def _cross_entity_evidence(
    question: Question,
    label: str,
    option_text: str,
    route: OptionClaimRoute,
    rows_by_doc: Mapping[str, Sequence[StructuredTableRow]],
) -> DerivedOptionEvidence:
    entities = extract_entities(option_text)
    components: list[DerivedOptionEvidence] = []
    rejected: list[Mapping[str, Any]] = []
    for entity in entities[:2]:
        doc_id = next(
            (str(value) for value in question.doc_ids if _doc_entity(str(value)) == entity),
            "",
        )
        component, component_rejected = _growth_component(
            qid=question.qid,
            option_label=label,
            doc_id=doc_id,
            rows=rows_by_doc.get(doc_id, ()),
            threshold_percent=0.0,
            relation=">",
        )
        components.append(component)
        rejected.extend(component_rejected)
    left = components[0] if len(components) >= 1 else None
    right = components[1] if len(components) >= 2 else None
    result = cross_entity_comparison(
        qid=question.qid,
        option_label=label,
        left=left,
        right=right,
        comparator=route.comparator or ">",
    )
    diagnostics = dict(result.diagnostics or {})
    diagnostics.update({
        "route": route.to_dict(),
        "entity_order": list(entities),
        "rejected_source_rows": list(rejected),
    })
    return DerivedOptionEvidence(**{**result.__dict__, "diagnostics": diagnostics})


@lru_cache(maxsize=512)
def _cached_structured_rows(
    structured_root: str,
    domain: str,
    doc_id: str,
) -> tuple[StructuredTableRow, ...]:
    return tuple(load_structured_table_rows(Path(structured_root), domain, doc_id))


def build_derived_option_evidence(
    question: Question,
    structured_root: str | Path | None,
) -> tuple[DerivedOptionEvidence, ...]:
    """Build all compound option evidence for one question.

    Direct facts are intentionally excluded; they remain the responsibility of
    the typed local-window certifier.  Failure to locate a source produces an
    unresolved derived object with auditable conflicts rather than an exception.
    """
    if not structured_root:
        return ()
    root = str(Path(structured_root))
    if question.domain == "research":
        return build_research_option_evidence(question, root)
    if question.domain == "financial_reports":
        evidence_rows = build_financial_report_option_evidence(question, root)
        enriched: list[DerivedOptionEvidence] = []
        for evidence in evidence_rows:
            option_text = str(question.options.get(evidence.option_label) or "")
            needs_parent_profit_audit = bool(
                any(fact.metric == "parent_attributable_net_profit" for fact in evidence.source_facts)
                or any(term in option_text for term in _PARENT_PROFIT_TERMS)
                or "归母净利润" in option_text
                or "净利润增速" in option_text
                or "净利润降幅" in option_text
            )
            diagnostics = dict(evidence.diagnostics or {})
            if needs_parent_profit_audit:
                rejected: list[Mapping[str, Any]] = []
                for doc_id in question.doc_ids:
                    selection = _select_parent_profit_row(
                        _cached_structured_rows(root, question.domain, str(doc_id))
                    )
                    rejected.extend(selection.rejected)
                diagnostics["rejected_source_rows"] = list(rejected)
                diagnostics["parent_profit_scope_audit"] = True
            enriched.append(DerivedOptionEvidence(**{
                **evidence.__dict__,
                "diagnostics": diagnostics,
            }))
        return tuple(enriched)
    rows_by_doc = {
        str(doc_id): _cached_structured_rows(root, question.domain, str(doc_id))
        for doc_id in question.doc_ids
    }
    results: list[DerivedOptionEvidence] = []
    for label, option_text in sorted(question.options.items()):
        option_text = str(option_text)
        route = route_option_claim(option_text, question.doc_ids)
        if not route.compound:
            continue
        parent_profit_growth = bool(
            route.metric == "parent_attributable_net_profit"
            and any(token in option_text for token in ("增速", "同比", "增长率", "增长", "降幅"))
        )
        if route.claim_type == "policy_execution_state":
            evidence = _policy_evidence(question, str(label), option_text, route, rows_by_doc)
        elif route.claim_type == "numeric_sum_comparison":
            evidence = _sum_evidence(question, str(label), option_text, route, rows_by_doc)
        elif route.claim_type == "cross_document_all" and parent_profit_growth:
            evidence = _cross_document_all_evidence(question, str(label), route, rows_by_doc)
        elif route.claim_type == "cross_entity_comparison" and parent_profit_growth:
            evidence = _cross_entity_evidence(question, str(label), option_text, route, rows_by_doc)
        elif route.claim_type == "yoy_growth" and parent_profit_growth:
            evidence = _single_growth_evidence(question, str(label), option_text, route, rows_by_doc)
        else:
            continue
        diagnostics = dict(evidence.diagnostics or {})
        diagnostics.setdefault("route", route.to_dict())
        evidence = DerivedOptionEvidence(**{**evidence.__dict__, "diagnostics": diagnostics})
        results.append(evidence)
    return tuple(results)


def production_code_contains_qid_branch() -> bool:
    """Sentinel used by Package M audits; production router has no QID branch."""
    return False
