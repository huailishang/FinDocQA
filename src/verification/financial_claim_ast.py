"""QID-independent semantic AST for financial-report option claims.

The parser is deliberately fail-closed. It never invents a comparator when a
relation, multiplier, period, metric, or unit cannot be recovered.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Sequence

from contracts import Question
from verification.financial_metric_ledger import document_meta, document_year

SCHEMA_VERSION = "financial_claim_spec_v1"

_ENTITY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "比亚迪": ("比亚迪", "byd"),
    "宁德时代": ("宁德时代", "catl"),
    "美的集团": ("美的集团", "美的", "midea"),
    "中国移动": ("中国移动", "china mobile"),
    "中国建筑": ("中国建筑", "中建", "cscec"),
}
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?")
_PERCENT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*[%％]")
_AMOUNT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(万亿元|亿元|百万元|万元|千元|元)")
_PER10_RE = re.compile(r"每\s*10\s*股[^，。；]{0,40}?([-+]?\d+(?:\.\d+)?)\s*元")
_PER_SHARE_RE = re.compile(r"每股[^，。；]{0,40}?([-+]?\d+(?:\.\d+)?)\s*元")
_ARABIC_MULTIPLIER_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*倍")
_CHINESE_MULTIPLIERS = {
    "一": Decimal("1"), "二": Decimal("2"), "两": Decimal("2"),
    "三": Decimal("3"), "四": Decimal("4"), "五": Decimal("5"),
    "六": Decimal("6"), "七": Decimal("7"), "八": Decimal("8"),
    "九": Decimal("9"), "十": Decimal("10"),
}
_AMOUNT_SCALE = {
    "元": Decimal("1"), "千元": Decimal("1000"), "万元": Decimal("10000"),
    "百万元": Decimal("1000000"), "亿元": Decimal("100000000"),
    "万亿元": Decimal("1000000000000"),
}


@dataclass(frozen=True)
class FinancialClaimSpec:
    schema_version: str
    claim_id: str
    option_label: str
    entity_refs: tuple[str, ...]
    metric: str
    comparator_metric: str
    statement_scope: str
    attribution_scope: str
    current_period: str
    comparison_period: str
    value: str | None
    value_unit: str
    value_precision: int | None
    relation: str
    multiplier: str | None
    approximation_mode: str
    trend_direction: str
    policy_stage: str
    required_doc_ids: tuple[str, ...]
    required_atoms: tuple[str, ...]
    parse_confidence: float
    parse_failures: tuple[str, ...]
    unsupported_semantics: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.parse_failures and not self.unsupported_semantics

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["complete"] = self.complete
        return payload


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _ordered_entities(text: str) -> tuple[str, ...]:
    compact = _compact(text)
    found: list[tuple[int, str]] = []
    for canonical, aliases in _ENTITY_ALIASES.items():
        positions = [compact.find(_compact(alias)) for alias in aliases]
        positions = [position for position in positions if position >= 0]
        if positions:
            found.append((min(positions), canonical))
    found.sort()
    return tuple(entity for _, entity in found)


def _metric(text: str) -> tuple[str, str]:
    compact = _compact(text)
    if any(token in compact for token in ("研发投入占营业收入比例", "研发投入强度", "研发投入占比")):
        return "rd_investment_ratio", ""
    if any(token in compact for token in ("研发费用占营业收入比例", "研发费用占营业收入的比例", "研发费用占营业收入比重", "研发费用率", "研发费用占比")):
        return "rd_expense_ratio", ""
    if "研发投入" in compact:
        return "rd_investment", ""
    if "研发费用" in compact:
        return "rd_expense", ""
    if any(token in compact for token in ("经营活动产生的现金流量净额", "经营活动现金流量净额", "经营活动现金流净额", "经营活动现金净流入", "经营现金流")):
        comparator = "operating_revenue" if any(token in compact for token in ("营业收入的一半", "营业收入的十分之一", "营业收入二分之一", "营业收入十分之一")) else ""
        return "operating_cash_flow_net", comparator
    if any(token in compact for token in ("筹资活动产生的现金流量净额", "筹资活动现金流净额")):
        return "financing_cash_flow_net", ""
    if any(token in compact for token in ("现金分红与股份回购之总金额", "现金分红与股份回购总金额", "分红与回购总金额")):
        return "dividend_plus_repurchase_amount", "parent_attributable_net_profit"
    if "现金分红金额" in compact or "派发现金分红金额" in compact:
        return "cash_dividend_amount", ""
    if any(token in compact for token in ("每10股派发现金", "每10股派息", "每10股现金分红")):
        return "cash_dividend_per_10_shares", ""
    if any(token in compact for token in ("每股派发现金", "每股现金分红", "每股分红金额", "每股现金")):
        return "cash_dividend_per_share", ""
    if any(token in compact for token in ("现金分红占归母净利润", "现金分红占合并报表归属于上市公司股东净利润", "现金分红比例")):
        return "cash_dividend_profit_ratio", ""
    if any(token in compact for token in ("归属于上市公司股东的净利润", "归属于母公司股东的净利润", "归属于母公司所有者的净利润", "母公司股东应占利润", "归母净利润", "净利润增速", "净利润降幅")):
        return "parent_attributable_net_profit", ""
    if "营业总收入" in compact:
        return "total_operating_revenue", ""
    if "境外收入占比" in compact or "境外收入比例" in compact:
        return "overseas_revenue_ratio", ""
    if "境外收入" in compact:
        return "overseas_revenue", ""
    if "新签合同额" in compact:
        return "new_contract_amount", ""
    if "营业收入" in compact or "营收" in compact:
        return "operating_revenue", ""
    if "股份回购" in compact or "回购计划" in compact or "回购方案" in compact:
        return "share_repurchase_history", ""
    if "现金分红" in compact or "利润分配" in compact:
        return "cash_dividend_policy", ""
    return "", ""


def _value(text: str, metric: str) -> tuple[str | None, str, int | None]:
    source = str(text or "")
    per10 = _PER10_RE.search(source)
    if per10:
        raw = per10.group(1)
        return raw, "CNY/10 shares", len(raw.partition(".")[2]) if "." in raw else 0
    per_share = _PER_SHARE_RE.search(source)
    if per_share:
        raw = per_share.group(1)
        return raw, "CNY/share", len(raw.partition(".")[2]) if "." in raw else 0
    percent = _PERCENT_RE.search(source)
    if percent:
        raw = percent.group(1)
        return raw, "%", len(raw.partition(".")[2]) if "." in raw else 0
    amount = _AMOUNT_RE.search(source)
    if amount:
        raw, unit = amount.groups()
        try:
            normalized = Decimal(raw) * _AMOUNT_SCALE[unit]
        except (InvalidOperation, KeyError):
            return None, "", None
        precision = len(raw.partition(".")[2]) if "." in raw else 0
        return format(normalized, "f"), "CNY", precision
    if metric in {"cash_dividend_policy", "share_repurchase_history"}:
        return None, "", None
    return None, "", None


def _multiplier(text: str) -> Decimal | None:
    compact = _compact(text)
    match = _ARABIC_MULTIPLIER_RE.search(compact)
    if match:
        try:
            return Decimal(match.group(1))
        except InvalidOperation:
            return None
    for token, value in sorted(_CHINESE_MULTIPLIERS.items(), key=lambda item: -len(item[0])):
        if f"{token}倍" in compact:
            return value
    if "一半" in compact or "二分之一" in compact:
        return Decimal("0.5")
    if "十分之一" in compact:
        return Decimal("0.1")
    return None


def _relation(text: str, entities: Sequence[str], value: str | None) -> tuple[str, str, str, Decimal | None]:
    compact = _compact(text)
    approximation = "precision_aware" if any(token in compact for token in ("约为", "大约", "约", "接近", "近似")) else "exact"
    trend_direction = ""
    multiplier = _multiplier(text)
    policy_tokens = ("拟", "预案", "董事会建议", "审议通过", "已实施", "实施了", "实施现金分红", "历史累计", "连续")
    comparative_tokens = ("高于", "低于", "超过", "不足", "大于", "小于", "同比", "相比", "较上年", "较上期")
    if (
        any(token in compact for token in policy_tokens)
        and any(token in compact for token in ("分红", "回购"))
        and not any(token in compact for token in comparative_tokens)
    ):
        return "policy_state_is", approximation, trend_direction, multiplier
    trend = any(token in compact for token in ("同比", "相比", "较上年", "较上期", "增长率", "增幅", "降幅", "上升", "增加", "增长", "下降", "减少", "回落", "下滑"))
    if trend:
        trend_direction = "down" if any(token in compact for token in ("下降", "减少", "回落", "下滑", "降幅")) else "up"
        if trend_direction == "down" and any(token in compact for token in ("降幅超过", "下降超过", "减少超过")):
            return "yoy_lt", approximation, trend_direction, multiplier
        if any(token in compact for token in ("低于", "不足", "小于", "慢于")):
            return "yoy_lt", approximation, trend_direction, multiplier
        if any(token in compact for token in ("高于", "超过", "大于", "快于", "至少", "不低于")):
            return "yoy_gt", approximation, trend_direction, multiplier
        return ("yoy_lt" if trend_direction == "down" else "yoy_gt"), approximation, trend_direction, multiplier
    if multiplier is not None and (len(entities) >= 2 or any(token in compact for token in ("一半", "二分之一", "十分之一"))):
        if any(token in compact for token in ("低于", "不足", "小于", "不超过", "至多")):
            return ("ratio_lt" if multiplier < 1 else "multiplier_lt"), approximation, trend_direction, multiplier
        if any(token in compact for token in ("超过", "高于", "大于", "至少", "不低于")):
            return ("ratio_gt" if multiplier < 1 else "multiplier_gt"), approximation, trend_direction, multiplier
        return "", approximation, trend_direction, multiplier
    if any(token in compact for token in ("至少", "不低于")):
        return "gte", approximation, trend_direction, multiplier
    if any(token in compact for token in ("至多", "不超过")):
        return "lte", approximation, trend_direction, multiplier
    if any(token in compact for token in ("超过", "高于", "大于", "多于")):
        return "gt", approximation, trend_direction, multiplier
    if any(token in compact for token in ("低于", "不足", "小于", "少于")):
        return "lt", approximation, trend_direction, multiplier
    if approximation == "precision_aware" and value is not None:
        return "approx_eq", approximation, trend_direction, multiplier
    if value is not None:
        return "eq", approximation, trend_direction, multiplier
    return "", approximation, trend_direction, multiplier


def _policy_stage(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("已实施", "实施了", "实施现金分红", "已派发", "已分派", "实施完毕")):
        return "executed"
    if "历史累计" in compact or "累计" in compact:
        return "historical_cumulative"
    if any(token in compact for token in ("股东大会审议通过", "股东会审议通过", "审议通过")):
        return "approved"
    if "董事会建议" in compact:
        return "board_recommendation"
    if any(token in compact for token in ("拟", "预案")):
        return "proposal"
    if "连续" in compact:
        return "historical_series"
    return ""


def _doc_bindings(question: Question, entities: Sequence[str], periods: Sequence[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for doc_id in question.doc_ids:
        meta = document_meta(str(doc_id))
        year = document_year(str(doc_id))
        entity_ok = not entities or meta.entity_name in entities
        period_ok = not periods or year in periods or any(period.isdigit() and year == str(int(period) + 1) for period in periods)
        if entity_ok and period_ok:
            selected.append(str(doc_id))
    if not selected and not entities:
        selected = [str(doc_id) for doc_id in question.doc_ids]
    return tuple(dict.fromkeys(selected))


def parse_financial_claim(question: Question, option_label: str, text: str) -> FinancialClaimSpec:
    entities = _ordered_entities(text)
    inferred = tuple(dict.fromkeys(
        document_meta(str(doc)).entity_name
        for doc in question.doc_ids
        if document_meta(str(doc)).entity_name
    ))
    compact_text = _compact(text)
    if not entities:
        if any(token in compact_text for token in ("两家公司", "双方", "均", "分别")) and len(inferred) >= 2:
            entities = inferred
        elif len(inferred) == 1:
            entities = inferred
    metric, comparator_metric = _metric(text)
    years = tuple(dict.fromkeys(_YEAR_RE.findall(str(text or ""))))
    current_period = years[0] if years else ""
    value, value_unit, value_precision = _value(text, metric)
    relation, approximation, trend_direction, multiplier = _relation(text, entities, value)
    if (
        relation == "yoy_lt"
        and trend_direction == "down"
        and value is not None
        and any(token in compact_text for token in ("降幅超过", "下降超过", "减少超过"))
    ):
        value = format(-abs(Decimal(value)), "f")
    policy_stage = _policy_stage(text)
    if not current_period and entities:
        matching = [
            document_year(str(doc))
            for doc in question.doc_ids
            if document_meta(str(doc)).entity_name in set(entities)
        ]
        matching = [year for year in matching if year]
        if len(set(matching)) == 1:
            current_period = matching[0]
    comparison_period = years[1] if len(years) > 1 else ""
    if relation in {"yoy_gt", "yoy_lt"} and not comparison_period and current_period.isdigit():
        comparison_period = str(int(current_period) - 1)

    required_atoms = ["entity", "metric", "relation", "statement_scope"]
    if relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"}:
        required_atoms.extend(("current_period", "current_value", "unit", "comparator"))
        if len(entities) >= 2 or comparison_period:
            required_atoms.append("comparison_value")
    if relation in {"multiplier_gt", "multiplier_lt", "ratio_gt", "ratio_lt"}:
        required_atoms.extend(("current_period", "current_value", "comparison_value", "unit", "multiplier", "comparator"))
    if relation in {"yoy_gt", "yoy_lt"}:
        required_atoms.extend(("current_period", "comparison_period", "current_value", "comparison_value", "unit", "comparator"))
    if relation == "policy_state_is":
        required_atoms.extend(("current_period", "policy_stage"))

    failures: list[str] = []
    unsupported: list[str] = []
    if not entities:
        failures.append("entity_unparsed")
    if not metric:
        failures.append("metric_unparsed")
    if not relation:
        failures.append("comparator_unparsed")
    if "倍" in _compact(text) and multiplier is None:
        failures.append("multiplier_unparsed")
    if relation in {"multiplier_gt", "multiplier_lt"} and len(entities) < 2:
        failures.append("comparison_entity_unparsed")
    if relation in {"ratio_gt", "ratio_lt"} and not comparator_metric:
        failures.append("comparison_metric_unparsed")
    if (
        relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"}
        and value is None
        and len(entities) < 2
        and not comparison_period
        and metric not in {"cash_dividend_policy", "share_repurchase_history"}
    ):
        failures.append("comparison_value_unparsed")
    if relation in {"yoy_gt", "yoy_lt"} and not current_period:
        failures.append("current_period_unparsed")
    if relation in {"yoy_gt", "yoy_lt"} and not comparison_period:
        failures.append("comparison_period_unparsed")
    if relation == "policy_state_is" and not policy_stage:
        failures.append("policy_stage_unparsed")
    binding_periods = () if relation == "policy_state_is" else tuple(
        p for p in (current_period, comparison_period) if p
    )
    required_docs = _doc_bindings(question, entities, binding_periods)
    if entities and not required_docs:
        failures.append("declared_document_binding_missing")
    confidence = max(0.0, round(1.0 - 0.12 * len(set(failures)) - 0.2 * len(set(unsupported)), 3))
    return FinancialClaimSpec(
        schema_version=SCHEMA_VERSION,
        claim_id=f"{option_label}:{metric or 'unknown'}:{relation or 'unknown'}",
        option_label=str(option_label), entity_refs=tuple(entities), metric=metric,
        comparator_metric=comparator_metric,
        statement_scope="consolidated" if metric else "",
        attribution_scope="parent_attributable" if metric == "parent_attributable_net_profit" else "not_applicable",
        current_period=current_period, comparison_period=comparison_period,
        value=value, value_unit=value_unit, value_precision=value_precision,
        relation=relation, multiplier=format(multiplier, "f") if multiplier is not None else None,
        approximation_mode=approximation, trend_direction=trend_direction,
        policy_stage=policy_stage, required_doc_ids=required_docs,
        required_atoms=tuple(dict.fromkeys(required_atoms)), parse_confidence=confidence,
        parse_failures=tuple(sorted(set(failures))),
        unsupported_semantics=tuple(sorted(set(unsupported))),
    )
