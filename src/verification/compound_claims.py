"""Deterministic routing and safety guards for compound option claims.

The module is intentionally independent from retrieval and table parsing.  It
classifies an option from its text and returns the minimum scopes that a direct
or derived evidence path must close.  Raw table rows are never allowed to
certify an aggregate, growth, policy-state or cross-entity claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence


COMPOUND_CLAIM_TYPES = frozenset(
    {
        "policy_execution_state",
        "yoy_growth",
        "numeric_comparison",
        "numeric_sum_comparison",
        "cross_entity_comparison",
        "cross_document_all",
    }
)

_ENTITY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "宁德时代": ("宁德时代", "catl"),
    "比亚迪": ("比亚迪", "byd"),
    "美的集团": ("美的集团", "美的", "midea"),
    "中国移动": ("中国移动", "china mobile"),
}

_METRIC_ALIASES: Mapping[str, tuple[str, ...]] = {
    "parent_attributable_net_profit": (
        "归母净利润",
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
        "归属于母公司所有者的净利润",
        "净利润",
    ),
    "operating_revenue": ("营业收入", "营业总收入", "营收"),
    "operating_cash_flow": (
        "经营活动产生的现金流量净额",
        "经营活动现金流净额",
        "经营现金流",
    ),
    "cash_dividend": ("现金分红", "现金股利", "派发现金红利"),
    "share_repurchase": ("股份回购", "回购股份", "股份回购金额"),
    "research_and_development": ("研发投入", "研发费用"),
}

_PARENT_SCOPE_TOKENS = (
    "归母",
    "归属于上市公司股东",
    "归属于母公司股东",
    "归属于母公司所有者",
    "合并口径",
    "上市公司",
    "母公司",
)
_SUBSIDIARY_SCOPE_TOKENS = (
    "子公司",
    "全资子公司",
    "控股子公司",
    "公司类型=子公司",
)

_SUM_TOKENS = ("总金额", "合计", "之和", "加总", "合计金额")
_GROWTH_TOKENS = ("增速", "同比", "增长率", "增长", "降幅", "下降趋势")
_ALL_TOKENS = ("均", "双方", "两家公司", "两份文档", "分别", "各自", "每个")
_CROSS_ENTITY_COMPARATORS = ("快于", "慢于", "高于", "低于", "大于", "小于")
_NUMERIC_COMPARATORS = ("超过", "少于", "不低于", "不高于", "至少", "至多")
_POLICY_STATE_TOKENS = ("实施", "执行", "完成", "已派发", "已分派", "实际派发")
_POLICY_SUBJECT_TOKENS = ("分红政策", "利润分配", "现金分红", "派发现金")

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")


@dataclass(frozen=True)
class OptionClaimRoute:
    claim_type: str
    compound: bool
    entities: tuple[str, ...]
    periods: tuple[str, ...]
    metric: str
    comparator: str
    threshold_percent: float | None
    required_document_count: int
    required_atoms: tuple[str, ...]
    route_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(value: Any) -> str:
    return "".join(str(value or "").lower().replace("％", "%").split())


def extract_entities(text: str) -> tuple[str, ...]:
    compact = _compact(text)
    positioned: list[tuple[int, str]] = []
    for canonical, aliases in _ENTITY_ALIASES.items():
        positions = [
            compact.find(_compact(alias))
            for alias in aliases
            if _compact(alias) and compact.find(_compact(alias)) >= 0
        ]
        if positions:
            positioned.append((min(positions), canonical))
    positioned.sort(key=lambda item: (item[0], item[1]))
    return tuple(canonical for _, canonical in positioned)


def extract_periods(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_YEAR_RE.findall(str(text or ""))))


def extract_metric(text: str) -> str:
    compact = _compact(text)
    for metric, aliases in _METRIC_ALIASES.items():
        if any(_compact(alias) in compact for alias in aliases):
            return metric
    return ""


def _threshold_percent(text: str) -> float | None:
    match = _PERCENT_RE.search(str(text or ""))
    if match:
        return float(match.group(1))
    compact = _compact(text)
    if "双位数" in compact:
        return 10.0
    if "一成半" in compact:
        return 15.0
    if "一成" in compact:
        return 10.0
    return None


def _comparator(text: str) -> str:
    compact = _compact(text)
    if "降幅" in compact and "超过" in compact:
        return "<"
    if any(token in compact for token in ("快于", "高于", "大于", "超过", "不低于", "至少")):
        return ">"
    if any(token in compact for token in ("慢于", "低于", "小于", "少于", "不高于", "至多")):
        return "<"
    return ""


def route_option_claim(
    option_text: str,
    question_doc_ids: Sequence[str] = (),
) -> OptionClaimRoute:
    text = str(option_text or "")
    compact = _compact(text)
    entities = extract_entities(text)
    periods = extract_periods(text)
    metric = extract_metric(text)
    comparator = _comparator(text)
    threshold = _threshold_percent(text)
    reasons: list[str] = []

    is_policy = (
        any(_compact(token) in compact for token in _POLICY_STATE_TOKENS)
        and any(_compact(token) in compact for token in _POLICY_SUBJECT_TOKENS)
    )
    has_sum = any(_compact(token) in compact for token in _SUM_TOKENS)
    has_growth = any(_compact(token) in compact for token in _GROWTH_TOKENS)
    has_all = any(_compact(token) in compact for token in _ALL_TOKENS)
    has_cross_comparison = any(
        _compact(token) in compact for token in _CROSS_ENTITY_COMPARATORS
    )
    has_numeric_comparison = any(
        _compact(token) in compact for token in _NUMERIC_COMPARATORS
    )
    has_explicit_numeric_threshold = bool(
        threshold is not None or re.search(r"\d+(?:\.\d+)?", text)
    )

    if is_policy:
        claim_type = "policy_execution_state"
        reasons.append("policy_state_terms")
    elif has_sum:
        claim_type = "numeric_sum_comparison"
        reasons.append("sum_or_aggregate_terms")
    elif has_cross_comparison and len(entities) >= 2:
        claim_type = "cross_entity_comparison"
        reasons.append("cross_entity_comparator")
    elif has_all and (has_growth or len(question_doc_ids) >= 2):
        claim_type = "cross_document_all"
        reasons.append("universal_cross_document_quantifier")
    elif has_growth:
        claim_type = "yoy_growth"
        reasons.append("growth_or_decline_terms")
    elif has_numeric_comparison and has_explicit_numeric_threshold:
        claim_type = "numeric_comparison"
        reasons.append("numeric_comparator_with_explicit_threshold")
    else:
        claim_type = "direct_fact"

    compound = claim_type in COMPOUND_CLAIM_TYPES
    required_atoms: list[str] = ["canonical_sources", "local_window_or_source_facts"]
    if entities:
        required_atoms.append("entity_scope")
    if periods or claim_type in {"yoy_growth", "cross_entity_comparison", "cross_document_all"}:
        required_atoms.append("period_scope")
    if metric:
        required_atoms.append("metric_scope")
    if comparator or claim_type in {
        "yoy_growth",
        "numeric_comparison",
        "numeric_sum_comparison",
        "cross_entity_comparison",
    }:
        required_atoms.append("comparator_scope")
    if compound:
        required_atoms.extend(("derived_formula", "derived_inputs"))

    required_document_count = 1
    if claim_type in {"cross_document_all", "cross_entity_comparison"}:
        required_document_count = max(2, len(entities), len(question_doc_ids))

    return OptionClaimRoute(
        claim_type=claim_type,
        compound=compound,
        entities=entities,
        periods=periods,
        metric=metric,
        comparator=comparator,
        threshold_percent=threshold,
        required_document_count=required_document_count,
        required_atoms=tuple(dict.fromkeys(required_atoms)),
        route_reasons=tuple(reasons),
    )


def entity_scope_guard(option_text: str, row_text: str) -> tuple[bool, tuple[str, ...]]:
    option_compact = _compact(option_text)
    row_compact = _compact(row_text)
    required_entities = extract_entities(option_text)
    reasons: list[str] = []

    for entity in required_entities:
        aliases = _ENTITY_ALIASES.get(entity, (entity,))
        if not any(_compact(alias) in row_compact for alias in aliases):
            reasons.append(f"missing_entity:{entity}")

    parent_scope_required = any(_compact(token) in option_compact for token in _PARENT_SCOPE_TOKENS)
    subsidiary_row = any(_compact(token) in row_compact for token in _SUBSIDIARY_SCOPE_TOKENS)
    if parent_scope_required and subsidiary_row:
        reasons.append("parent_subsidiary_scope_mismatch")

    # A brand substring inside a separately named subsidiary is not sufficient
    # for a parent-company attributable claim.
    if parent_scope_required and "有限公司" in row_text and not any(
        token in row_text for token in ("上市公司", "母公司", "合并")
    ):
        for entity in required_entities:
            if entity in row_text and row_text.strip().startswith(entity) is False:
                reasons.append("brand_substring_in_subsidiary_entity")
                break

    return not reasons, tuple(dict.fromkeys(reasons))


def raw_table_certification_guard(
    option_text: str,
    row_text: str,
    question_doc_ids: Sequence[str] = (),
) -> dict[str, Any]:
    route = route_option_claim(option_text, question_doc_ids)
    reasons: list[str] = []
    if route.compound:
        reasons.append("compound_claim_requires_derivation")
    entity_ok, entity_reasons = entity_scope_guard(option_text, row_text)
    if not entity_ok:
        reasons.append("entity_scope_mismatch_or_incomplete")
        reasons.extend(entity_reasons)

    row_periods = extract_periods(row_text)
    if route.claim_type in {"yoy_growth", "cross_entity_comparison", "cross_document_all"}:
        explicit_growth = bool(re.search(r"[-+]?\d+(?:\.\d+)?\s*[%％]", row_text))
        if len(row_periods) < 2 and not explicit_growth:
            reasons.append("derived_period_or_growth_inputs_incomplete")
    if route.claim_type in {"cross_entity_comparison", "cross_document_all"}:
        row_entities = extract_entities(row_text)
        if len(set(row_entities)) < 2:
            reasons.append("cross_entity_inputs_incomplete")

    return {
        "allowed": not reasons and route.claim_type == "direct_fact",
        "claim_type": route.claim_type,
        "compound": route.compound,
        "route": route.to_dict(),
        "reasons": list(dict.fromkeys(reasons)),
    }
