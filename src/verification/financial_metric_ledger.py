"""Production financial-report metric ledger.

The ledger converts structured annual-report rows into source-local, auditable
facts.  It is deliberately independent from QIDs and answer labels.  Every fact
keeps entity, statement/attribution scope, period, unit propagation and exact
MinerU row lineage so derived claims can fail closed when operands do not align.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from evidence.structured_tables import StructuredTableRow, load_structured_table_rows
from verification.derived_option_evidence import SourceFact


@dataclass(frozen=True)
class DocumentFinancialMeta:
    entity_name: str
    entity_scope: str
    statement_scope: str
    attribution_scope: str
    currency: str
    default_amount_unit: str
    default_scale_multiplier: Decimal


_DOC_META_RULES: tuple[tuple[str, DocumentFinancialMeta], ...] = (
    (
        "annual_byd_",
        DocumentFinancialMeta(
            "比亚迪", "listed_group", "consolidated",
            "listed_company_shareholders_attributable", "CNY", "元", Decimal("1"),
        ),
    ),
    (
        "annual_catl_",
        DocumentFinancialMeta(
            "宁德时代", "listed_group", "consolidated",
            "listed_company_shareholders_attributable", "CNY", "千元", Decimal("1000"),
        ),
    ),
    (
        "annual_midea_",
        DocumentFinancialMeta(
            "美的集团", "listed_group", "consolidated",
            "listed_company_shareholders_attributable", "CNY", "千元", Decimal("1000"),
        ),
    ),
    (
        "annual_chinamobile_",
        DocumentFinancialMeta(
            "中国移动", "listed_group", "consolidated",
            "parent_attributable", "CNY", "百万元", Decimal("1000000"),
        ),
    ),
    (
        "annual_cscec_",
        DocumentFinancialMeta(
            "中国建筑", "listed_group", "consolidated",
            "listed_company_shareholders_attributable", "CNY", "千元", Decimal("1000"),
        ),
    ),
)

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_KV_RE = re.compile(r"(?:^|\|\s*)([^|=]+?)=([^|]+)")
_NUMBER_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_STRICT_NUMBER_RE = re.compile(r"^\s*([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?:[%％]|元|亿元|万元|千元|百万元)?\s*$")
_PERCENT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*[%％]")


FINANCIAL_METRIC_REGISTRY: tuple[str, ...] = (
    "operating_revenue",
    "total_operating_revenue",
    "parent_attributable_net_profit",
    "operating_cash_flow_net",
    "financing_cash_flow_net",
    "rd_investment",
    "rd_expense",
    "rd_investment_ratio",
    "rd_expense_ratio",
    "cash_dividend_amount",
    "cash_dividend_per_share",
    "cash_dividend_per_10_shares",
    "cash_dividend_profit_ratio",
    "share_repurchase_amount",
    "dividend_plus_repurchase_amount",
    "new_contract_amount",
    "overseas_revenue",
    "overseas_revenue_ratio",
    "total_assets",
    "total_liabilities",
    "bonus_shares_per_10",
    "capitalization_shares_per_10",
)


@dataclass(frozen=True)
class FinancialFact:
    entity_name: str
    entity_scope: str
    statement_scope: str
    attribution_scope: str
    document_id: str
    document_year: str
    metric: str
    period: str
    comparison_period: str
    raw_value: str
    normalized_value: float | None
    raw_unit: str
    normalized_unit: str
    scale_multiplier: float
    fact_state: str
    per_share_basis: str
    source_page: int
    source_table: int
    source_row: int
    canonical_source: str
    local_window: str
    rejection_reasons: tuple[str, ...] = ()
    precision_rank: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_source_fact(self) -> SourceFact:
        scope = " ".join(
            token for token in (
                self.entity_name,
                self.entity_scope,
                self.statement_scope,
                self.attribution_scope,
            ) if token
        )
        return SourceFact(
            doc_id=self.document_id,
            entity_scope=scope,
            period_scope=self.period,
            metric=self.metric,
            value=self.normalized_value,
            unit=self.normalized_unit,
            canonical_source=self.canonical_source,
            local_window=self.local_window,
            fact_state=self.fact_state,
            metadata={
                **dict(self.metadata or {}),
                "comparison_period": self.comparison_period,
                "raw_value": self.raw_value,
                "raw_unit": self.raw_unit,
                "scale_multiplier": self.scale_multiplier,
                "statement_scope": self.statement_scope,
                "attribution_scope": self.attribution_scope,
                "per_share_basis": self.per_share_basis,
                "source_page": self.source_page,
                "source_table": self.source_table,
                "source_row": self.source_row,
                "precision_rank": self.precision_rank,
            },
        )


def document_meta(doc_id: str) -> DocumentFinancialMeta:
    lowered = str(doc_id or "").lower()
    for token, meta in _DOC_META_RULES:
        if token in lowered:
            return meta
    return DocumentFinancialMeta(
        "", "unknown_scope", "unknown_scope", "unknown_scope",
        "CNY", "unknown", Decimal("1"),
    )


def document_year(doc_id: str) -> str:
    matches = _YEAR_RE.findall(str(doc_id or ""))
    return matches[-1] if matches else ""


def _prior_year(year: str) -> str:
    return str(int(year) - 1) if str(year).isdigit() else "prior_period"


def _compact(value: Any) -> str:
    return "".join(str(value or "").replace("％", "%").split())


def _row_values(row: StructuredTableRow) -> dict[str, str]:
    return {
        str(key).strip(): str(value).strip()
        for key, value in _KV_RE.findall(row.normalized_row_text)
    }


def _decimal(value: Any) -> Decimal | None:
    # MinerU sometimes inserts spaces after thousands separators, for example
    # ``40, 254,346, 000.00``.  Normalize only comma-group spacing so separate
    # numbers in one cell are not concatenated.
    text = re.sub(r",\s+", ",", str(value or "").strip())
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _strict_decimal(value: Any) -> Decimal | None:
    match = _STRICT_NUMBER_RE.fullmatch(str(value or ""))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _last_decimal(value: Any) -> Decimal | None:
    matches = list(_NUMBER_RE.finditer(str(value or "")))
    if not matches:
        return None
    try:
        return Decimal(matches[-1].group(0).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _percent(value: Any) -> Decimal | None:
    text = str(value or "").replace("％", "%")
    match = _PERCENT_RE.search(text)
    if not match:
        return None
    return _decimal(match.group(0).replace("%", ""))


def _amount_unit(row: StructuredTableRow, meta: DocumentFinancialMeta) -> tuple[str, Decimal]:
    context = " ".join((row.table_caption, row.table_footnote, row.normalized_row_text))
    compact = _compact(context)
    if "人民币百万元" in compact or "单位：百万元" in compact or "单位:百万元" in compact:
        return "百万元", Decimal("1000000")
    if "单位：千元" in compact or "单位:千元" in compact or "（千元）" in compact or "(千元)" in compact:
        return "千元", Decimal("1000")
    if "单位：万元" in compact or "单位:万元" in compact or "（万元）" in compact or "(万元)" in compact:
        return "万元", Decimal("10000")
    if "单位：亿元" in compact or "单位:亿元" in compact or "（亿元）" in compact or "(亿元)" in compact:
        return "亿元", Decimal("100000000")
    if "（元）" in compact or "(元)" in compact:
        return "元", Decimal("1")
    return meta.default_amount_unit, meta.default_scale_multiplier


def _metric_scope(metric: str, meta: DocumentFinancialMeta) -> tuple[str, str]:
    if metric == "parent_attributable_net_profit":
        return "consolidated", meta.attribution_scope
    if metric in {
        "operating_revenue", "total_operating_revenue", "operating_cash_flow_net",
        "financing_cash_flow_net", "rd_investment", "rd_expense",
        "rd_investment_ratio", "rd_expense_ratio", "cash_dividend_amount",
        "share_repurchase_amount", "dividend_plus_repurchase_amount",
        "cash_dividend_profit_ratio", "overseas_revenue", "overseas_revenue_ratio",
        "total_assets", "total_liabilities", "new_contract_amount",
    }:
        return "consolidated", "not_applicable"
    return meta.statement_scope, "not_applicable"


def _fact(
    row: StructuredTableRow,
    *,
    metric: str,
    period: str,
    comparison_period: str = "",
    raw_value: Any,
    normalized_value: Decimal | None,
    raw_unit: str,
    normalized_unit: str,
    scale_multiplier: Decimal = Decimal("1"),
    fact_state: str = "reported",
    per_share_basis: str = "not_applicable",
    precision_rank: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> FinancialFact:
    meta = document_meta(row.doc_id)
    statement_scope, attribution_scope = _metric_scope(metric, meta)
    return FinancialFact(
        entity_name=meta.entity_name,
        entity_scope=meta.entity_scope,
        statement_scope=statement_scope,
        attribution_scope=attribution_scope,
        document_id=row.doc_id,
        document_year=document_year(row.doc_id),
        metric=metric,
        period=period,
        comparison_period=comparison_period,
        raw_value=str(raw_value or ""),
        normalized_value=float(normalized_value) if normalized_value is not None else None,
        raw_unit=raw_unit,
        normalized_unit=normalized_unit,
        scale_multiplier=float(scale_multiplier),
        fact_state=fact_state,
        per_share_basis=per_share_basis,
        source_page=row.page_idx,
        source_table=row.table_index,
        source_row=row.row_index,
        canonical_source=row.canonical_source,
        local_window=row.normalized_row_text,
        rejection_reasons=(),
        precision_rank=precision_rank,
        metadata=dict(metadata or {}),
    )


def _summary_metric(text: str) -> tuple[str, str] | None:
    compact = _compact(text)
    if "公司类型=子公司" in compact or "主要子公司" in compact:
        return None
    if "归属于上市公司股东的净利润" in compact:
        return "parent_attributable_net_profit", "listed_company_shareholders_attributable"
    if "归属于母公司股东的净利润" in compact or "归属于母公司所有者的净利润" in compact:
        return "parent_attributable_net_profit", "parent_attributable"
    if "经营活动产生的现金流量净额" in compact:
        return "operating_cash_flow_net", "not_applicable"
    if "筹资活动产生的现金流量净额" in compact:
        return "financing_cash_flow_net", "not_applicable"
    first_label = compact.split("|", 1)[0]
    if "营业总收入" in first_label or "column_1=一、营业总收入" in compact:
        return "total_operating_revenue", "not_applicable"
    if (
        "column_1=营业收入" in compact
        or "科目=营业收入" in compact
        or first_label == "营业收入"
        or "营业收入（" in compact
    ) and not any(token in compact for token in ("营业收入合计", "占营业收入", "分地区", "分行业")):
        return "operating_revenue", "not_applicable"
    return None


def _summary_row_rank(row: StructuredTableRow, metric: str) -> int:
    text = _compact(row.normalized_row_text)
    rank = 0
    if row.page_idx <= 15:
        rank += 100
    if row.table_caption and any(token in row.table_caption for token in ("主要会计数据", "主要财务指标")):
        rank += 60
    if "column_2=" in text and "column_3=" in text:
        rank += 40
    # Annual summary rows normally carry a YoY percentage. Quarterly rows may
    # use the same generic column names but do not carry an annual growth cell.
    if _PERCENT_RE.search(row.normalized_row_text):
        rank += 40
    else:
        rank -= 20
    if "第一季度" in text or "第二季度" in text:
        rank -= 200
    if metric == "operating_revenue" and "营业收入（" in text:
        rank += 30
    if metric == "parent_attributable_net_profit" and row.page_idx <= 15:
        rank += 30
    return rank


def _extract_summary_facts(row: StructuredTableRow) -> list[FinancialFact]:
    metric_info = _summary_metric(row.normalized_row_text)
    if metric_info is None:
        return []
    metric, _ = metric_info
    values = _row_values(row)
    current_raw = values.get("column_2") or values.get("本期数")
    prior_raw = values.get("column_3") or values.get("上年同期数")
    current = _decimal(current_raw)
    prior = _decimal(prior_raw)
    if current is None or prior is None:
        return []
    meta = document_meta(row.doc_id)
    raw_unit, multiplier = _amount_unit(row, meta)
    year = document_year(row.doc_id)
    prior_year = _prior_year(year)
    rank = _summary_row_rank(row, metric)
    current_fact = _fact(
        row,
        metric=metric,
        period=year or "current_period",
        comparison_period=prior_year,
        raw_value=current_raw,
        normalized_value=current * multiplier,
        raw_unit=raw_unit,
        normalized_unit=meta.currency,
        scale_multiplier=multiplier,
        precision_rank=rank,
        metadata={"column_role": "current", "paired_prior_raw": prior_raw},
    )
    prior_fact = _fact(
        row,
        metric=metric,
        period=prior_year,
        comparison_period=year or "current_period",
        raw_value=prior_raw,
        normalized_value=prior * multiplier,
        raw_unit=raw_unit,
        normalized_unit=meta.currency,
        scale_multiplier=multiplier,
        precision_rank=rank - 1,
        metadata={"column_role": "prior", "paired_current_raw": current_raw},
    )
    return [current_fact, prior_fact]


def _ratio_metric(text: str) -> str:
    compact = _compact(text)
    if "研发费用占营业收入" in compact:
        return "rd_expense_ratio"
    if "研发投入占营业收入" in compact or "研发投入总额占营业收入" in compact:
        return "rd_investment_ratio"
    return ""


def _extract_total_operating_revenue_facts(row: StructuredTableRow) -> list[FinancialFact]:
    compact = _compact(row.normalized_row_text)
    if "营业总收入" not in compact:
        return []
    values = _row_values(row)
    current_raw = values.get("column_2")
    prior_raw = values.get("column_3")
    # Some consolidated income statements put current/prior values in columns
    # four and five after item numbering and note columns.
    if _strict_decimal(current_raw) is None and _strict_decimal(values.get("column_4")) is not None:
        current_raw = values.get("column_4")
        prior_raw = values.get("column_5")
    current = _strict_decimal(current_raw)
    prior = _strict_decimal(prior_raw)
    if current is None:
        return []
    meta = document_meta(row.doc_id)
    unit, multiplier = _amount_unit(row, meta)
    year = document_year(row.doc_id)
    result = [_fact(
        row, metric="total_operating_revenue", period=year, comparison_period=_prior_year(year),
        raw_value=current_raw, normalized_value=current * multiplier, raw_unit=unit,
        normalized_unit=meta.currency, scale_multiplier=multiplier, precision_rank=130,
    )]
    if prior is not None:
        result.append(_fact(
            row, metric="total_operating_revenue", period=_prior_year(year), comparison_period=year,
            raw_value=prior_raw, normalized_value=prior * multiplier, raw_unit=unit,
            normalized_unit=meta.currency, scale_multiplier=multiplier, precision_rank=129,
        ))
    return result


def _extract_ratio_facts(row: StructuredTableRow) -> list[FinancialFact]:
    metric = _ratio_metric(row.normalized_row_text)
    if not metric:
        return []
    values = _row_values(row)
    current_raw = values.get("column_2")
    prior_raw = values.get("column_3")
    current = _percent(current_raw)
    prior = _percent(prior_raw)
    year = document_year(row.doc_id)
    result: list[FinancialFact] = []
    if current is not None:
        result.append(_fact(
            row, metric=metric, period=year, comparison_period=_prior_year(year),
            raw_value=current_raw, normalized_value=current, raw_unit="%",
            normalized_unit="%", precision_rank=150 if row.page_idx <= 80 else 100,
            metadata={"column_role": "current"},
        ))
    if prior is not None:
        result.append(_fact(
            row, metric=metric, period=_prior_year(year), comparison_period=year,
            raw_value=prior_raw, normalized_value=prior, raw_unit="%",
            normalized_unit="%", precision_rank=149 if row.page_idx <= 80 else 99,
            metadata={"column_role": "prior"},
        ))
    return result


def _policy_stage(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("未实施", "不实施", "尚未实施")):
        return "not_executed"
    # Bind policy ratios and proposed distributions to the proposal clause even
    # when the same paragraph also mentions an already-paid interim dividend.
    if "董事会建议" in compact:
        return "board_recommendation"
    if any(token in compact for token in ("预案", "拟以", "建议派发", "拟每")):
        return "proposal"
    if any(token in compact for token in ("股东大会审议通过", "股东会审议通过", "已经审议通过")):
        return "shareholder_approved"
    if any(token in compact for token in ("已实施完毕", "实施完毕", "已派发", "已分派", "已完成")):
        return "executed"
    return "unknown"


def _extract_dividend_facts(row: StructuredTableRow) -> list[FinancialFact]:
    text = row.normalized_row_text
    compact = _compact(text)
    values = _row_values(row)
    raw = values.get("column_2")
    value = _strict_decimal(raw)
    year = document_year(row.doc_id)
    state = _policy_stage(text)
    result: list[FinancialFact] = []
    if any(token in compact for token in ("每10股派息数", "每10股派息金额", "每10股派发现金红利")) and value is not None:
        result.append(_fact(
            row, metric="cash_dividend_per_10_shares", period=year,
            raw_value=raw, normalized_value=value, raw_unit="CNY/10 shares",
            normalized_unit="CNY/10 shares", fact_state=state,
            per_share_basis="per_10_shares", precision_rank=180,
        ))
        result.append(_fact(
            row, metric="cash_dividend_per_share", period=year,
            raw_value=raw, normalized_value=value / Decimal("10"), raw_unit="CNY/10 shares",
            normalized_unit="CNY/share", fact_state=state,
            per_share_basis="per_share_derived_from_per_10", precision_rank=170,
            metadata={"formula": "per_10_shares / 10"},
        ))
    if "每10股送红股数" in compact and value is not None:
        result.append(_fact(
            row, metric="bonus_shares_per_10", period=year,
            raw_value=raw, normalized_value=value, raw_unit="shares/10 shares",
            normalized_unit="shares/10 shares", fact_state=state,
            per_share_basis="per_10_shares", precision_rank=180,
        ))
    if "每10股转增数" in compact and value is not None:
        result.append(_fact(
            row, metric="capitalization_shares_per_10", period=year,
            raw_value=raw, normalized_value=value, raw_unit="shares/10 shares",
            normalized_unit="shares/10 shares", fact_state=state,
            per_share_basis="per_10_shares", precision_rank=180,
        ))
    meta = document_meta(row.doc_id)
    amount_unit, amount_multiplier = _amount_unit(row, meta)
    if "现金分红金额" in compact and "以其他方式" not in compact and "总额" not in compact and value is not None:
        result.append(_fact(
            row, metric="cash_dividend_amount", period=year,
            raw_value=raw, normalized_value=value * amount_multiplier, raw_unit=amount_unit,
            normalized_unit=meta.currency, scale_multiplier=amount_multiplier,
            fact_state=state, precision_rank=180,
        ))
    if "以其他方式" in compact and "回购股份" in compact and value is not None:
        result.append(_fact(
            row, metric="share_repurchase_amount", period=year,
            raw_value=raw, normalized_value=value * amount_multiplier, raw_unit=amount_unit,
            normalized_unit=meta.currency, scale_multiplier=amount_multiplier,
            fact_state=state, precision_rank=180,
        ))
    if "现金分红总额" in compact and "占" not in compact and value is not None:
        result.append(_fact(
            row, metric="dividend_plus_repurchase_amount", period=year,
            raw_value=raw, normalized_value=value * amount_multiplier, raw_unit=amount_unit,
            normalized_unit=meta.currency, scale_multiplier=amount_multiplier,
            fact_state=state, precision_rank=175,
        ))
    if "每10股派发现金红利" in compact:
        match = re.search(r"每10股派发现金红利(?:人民币)?\s*([\d.]+)元", compact)
        if match:
            per10 = Decimal(match.group(1))
            result.append(_fact(
                row, metric="cash_dividend_per_10_shares", period=year,
                raw_value=match.group(1), normalized_value=per10,
                raw_unit="CNY/10 shares", normalized_unit="CNY/10 shares",
                fact_state=state, per_share_basis="per_10_shares", precision_rank=160,
            ))
    ratio_matches = [Decimal(value) for value in re.findall(r"(?:净利润的|占[^。；]{0,35}?净利润[^。；]{0,10}?)(\d+(?:\.\d+)?)%", compact)]
    for ratio in ratio_matches:
        result.append(_fact(
            row, metric="cash_dividend_profit_ratio", period=year,
            raw_value=str(ratio), normalized_value=ratio, raw_unit="%",
            normalized_unit="%", fact_state=state, precision_rank=160,
            metadata={"policy_stage": state},
        ))
    return result


def _extract_rd_amount_facts(row: StructuredTableRow) -> list[FinancialFact]:
    compact = _compact(row.normalized_row_text)
    values = _row_values(row)
    metric = ""
    paired_periods = False
    if "本期费用化研发投入" in compact or "研发费用金额" in compact:
        metric = "rd_expense"
    elif compact.startswith("column_1=研发费用|"):
        # Management-analysis tables expose a clean current/prior R&D expense
        # row.  Do not use similarly named tax-note or valuation rows and do not
        # infer from malformed consolidated-statement column shifts.
        current_raw = values.get("column_2")
        prior_raw = values.get("column_3")
        if _strict_decimal(current_raw) is not None and _strict_decimal(prior_raw) is not None:
            metric = "rd_expense"
            paired_periods = True
    elif "研发投入合计" in compact:
        metric = "rd_investment"
    if not metric:
        return []

    raw = values.get("column_2")
    value = _strict_decimal(raw)
    if value is None and len(values) == 1:
        value = _last_decimal(next(iter(values.values()), ""))
        raw = str(value) if value is not None else ""
    if value is None:
        return []

    meta = document_meta(row.doc_id)
    raw_unit, multiplier = _amount_unit(row, meta)
    year = document_year(row.doc_id)
    rank = 165 if paired_periods and row.page_idx <= 100 else 145 if row.page_idx <= 100 else 95
    result = [_fact(
        row, metric=metric, period=year, comparison_period=_prior_year(year) if paired_periods else "",
        raw_value=raw, normalized_value=value * multiplier, raw_unit=raw_unit,
        normalized_unit=meta.currency, scale_multiplier=multiplier, precision_rank=rank,
        metadata={"column_role": "current"} if paired_periods else {},
    )]
    if paired_periods:
        prior_raw = values.get("column_3")
        prior_value = _strict_decimal(prior_raw)
        if prior_value is not None:
            result.append(_fact(
                row, metric=metric, period=_prior_year(year), comparison_period=year,
                raw_value=prior_raw, normalized_value=prior_value * multiplier,
                raw_unit=raw_unit, normalized_unit=meta.currency,
                scale_multiplier=multiplier, precision_rank=rank - 1,
                metadata={"column_role": "prior"},
            ))
    return result


def _extract_dividend_history_row(
    row: StructuredTableRow,
    *,
    verified_dividend_table: bool = False,
) -> list[FinancialFact]:
    if not verified_dividend_table:
        return []
    values = _row_values(row)
    period_raw = values.get("column_1", "")
    if not re.fullmatch(r"(?:19|20)\d{2}年?", period_raw.strip()):
        return []
    period = _YEAR_RE.search(period_raw).group(1) if _YEAR_RE.search(period_raw) else document_year(row.doc_id)
    result: list[FinancialFact] = []
    field_map = (
        ("column_2", "bonus_shares_per_10", "shares/10 shares", "per_10_shares"),
        ("column_3", "cash_dividend_per_10_shares", "CNY/10 shares", "per_10_shares"),
        ("column_4", "capitalization_shares_per_10", "shares/10 shares", "per_10_shares"),
    )
    for key, metric, unit, basis in field_map:
        value = _strict_decimal(values.get(key))
        if value is None:
            continue
        result.append(_fact(
            row, metric=metric, period=period, raw_value=values.get(key),
            normalized_value=value, raw_unit=unit, normalized_unit=unit,
            per_share_basis=basis, precision_rank=230, fact_state="proposal",
        ))
        if metric == "cash_dividend_per_10_shares":
            result.append(_fact(
                row, metric="cash_dividend_per_share", period=period, raw_value=values.get(key),
                normalized_value=value / Decimal("10"), raw_unit=unit, normalized_unit="CNY/share",
                per_share_basis="per_share_derived_from_per_10", precision_rank=220,
                fact_state="proposal", metadata={"formula": "per_10_shares / 10"},
            ))
    meta = document_meta(row.doc_id)
    for key, metric in (("column_5", "cash_dividend_amount"), ("column_6", "parent_attributable_net_profit")):
        value = _strict_decimal(values.get(key))
        if value is None:
            continue
        result.append(_fact(
            row, metric=metric, period=period, raw_value=values.get(key),
            normalized_value=value * meta.default_scale_multiplier, raw_unit=meta.default_amount_unit,
            normalized_unit=meta.currency, scale_multiplier=meta.default_scale_multiplier,
            precision_rank=225, fact_state="proposal" if metric == "cash_dividend_amount" else "reported",
        ))
    ratio = _strict_decimal(values.get("column_7"))
    if ratio is not None:
        result.append(_fact(
            row, metric="cash_dividend_profit_ratio", period=period, raw_value=values.get("column_7"),
            normalized_value=ratio, raw_unit="%", normalized_unit="%",
            precision_rank=230, fact_state="proposal",
        ))
    return result


def _extract_balance_sheet_facts(row: StructuredTableRow) -> list[FinancialFact]:
    compact = _compact(row.normalized_row_text)
    metric = ""
    if compact.startswith("column_1=资产总计") or compact.startswith("科目=资产总计"):
        metric = "total_assets"
    elif compact.startswith("column_1=负债合计") or compact.startswith("科目=负债合计"):
        metric = "total_liabilities"
    if not metric:
        return []
    values = _row_values(row)
    raw = values.get("column_2") or values.get("本期数")
    value = _strict_decimal(raw)
    if value is None:
        return []
    meta = document_meta(row.doc_id)
    unit, multiplier = _amount_unit(row, meta)
    return [_fact(
        row, metric=metric, period=document_year(row.doc_id), raw_value=raw,
        normalized_value=value * multiplier, raw_unit=unit, normalized_unit=meta.currency,
        scale_multiplier=multiplier, precision_rank=100,
    )]


def _extract_overseas_facts(row: StructuredTableRow) -> list[FinancialFact]:
    text = row.normalized_row_text
    compact = _compact(text)
    if not (compact.startswith("境外|") or "项目=境外" in compact or "分地区=境外" in compact):
        return []
    values = _row_values(row)
    raw = values.get("营业收入")
    value = _decimal(raw)
    if value is None:
        return []
    meta = document_meta(row.doc_id)
    raw_unit, multiplier = _amount_unit(row, meta)
    return [_fact(
        row, metric="overseas_revenue", period=document_year(row.doc_id),
        raw_value=raw, normalized_value=value * multiplier, raw_unit=raw_unit,
        normalized_unit=meta.currency, scale_multiplier=multiplier,
        precision_rank=120,
    )]


def _extract_new_contract_facts(row: StructuredTableRow) -> list[FinancialFact]:
    text = row.normalized_row_text
    compact = _compact(text)
    if "期内累计新签合同额" not in compact:
        return []
    values = _row_values(row)
    candidates = [values.get(key) for key in ("column_3", "column_2", "新签合同额")]
    raw = next((value for value in candidates if _decimal(value) is not None), None)
    value = _decimal(raw)
    if value is None:
        return []
    # CSCEC business-data tables express the total in 亿元.
    return [_fact(
        row, metric="new_contract_amount", period=document_year(row.doc_id),
        raw_value=raw, normalized_value=value * Decimal("100000000"), raw_unit="亿元",
        normalized_unit="CNY", scale_multiplier=Decimal("100000000"), precision_rank=150,
    )]


def _extract_dividend_matrix_facts(row: StructuredTableRow) -> list[FinancialFact]:
    """Extract one annual dividend-history row with explicit column roles."""
    values = _row_values(row)
    period_match = _YEAR_RE.search(values.get("column_1", ""))
    if not period_match:
        return []
    context = _compact(" ".join((row.table_caption, row.table_footnote, row.normalized_row_text)))
    if not all(token in context for token in ("column_3=", "column_5=", "column_6=", "column_7=")):
        return []
    if not any(token in context for token in ("分红年度", "每10股", "现金分红的数额", "归属于上市公司普通股股东")):
        return []
    period = period_match.group(1)
    meta = document_meta(row.doc_id)
    result: list[FinancialFact] = []
    fields = (
        ("column_2", "bonus_shares_per_10", "shares/10 shares", "shares/10 shares", "per_10_shares"),
        ("column_3", "cash_dividend_per_10_shares", "CNY/10 shares", "CNY/10 shares", "per_10_shares"),
        ("column_4", "capitalization_shares_per_10", "shares/10 shares", "shares/10 shares", "per_10_shares"),
    )
    for column, metric, raw_unit, normalized_unit, basis in fields:
        raw = values.get(column)
        value = _decimal(raw)
        if value is None:
            continue
        result.append(_fact(
            row, metric=metric, period=period, raw_value=raw,
            normalized_value=value, raw_unit=raw_unit,
            normalized_unit=normalized_unit, fact_state="historical_reported",
            per_share_basis=basis, precision_rank=210,
            metadata={"column_role": column, "matrix_period": period},
        ))
        if metric == "cash_dividend_per_10_shares":
            result.append(_fact(
                row, metric="cash_dividend_per_share", period=period, raw_value=raw,
                normalized_value=value / Decimal("10"), raw_unit=raw_unit,
                normalized_unit="CNY/share", fact_state="historical_reported",
                per_share_basis="per_share_derived_from_per_10", precision_rank=205,
                metadata={"column_role": column, "matrix_period": period, "formula": "per_10 / 10"},
            ))
    amount_raw = values.get("column_5")
    amount = _decimal(amount_raw)
    if amount is not None:
        result.append(_fact(
            row, metric="cash_dividend_amount", period=period, raw_value=amount_raw,
            normalized_value=amount * meta.default_scale_multiplier,
            raw_unit=meta.default_amount_unit, normalized_unit=meta.currency,
            scale_multiplier=meta.default_scale_multiplier,
            fact_state="historical_reported", precision_rank=210,
            metadata={"column_role": "column_5", "matrix_period": period},
        ))
    profit_raw = values.get("column_6")
    profit = _decimal(profit_raw)
    if profit is not None:
        result.append(_fact(
            row, metric="parent_attributable_net_profit", period=period,
            raw_value=profit_raw, normalized_value=profit * meta.default_scale_multiplier,
            raw_unit=meta.default_amount_unit, normalized_unit=meta.currency,
            scale_multiplier=meta.default_scale_multiplier, fact_state="reported",
            precision_rank=205,
            metadata={"column_role": "column_6", "matrix_period": period},
        ))
    ratio_raw = values.get("column_7")
    ratio = _decimal(ratio_raw)
    if ratio is not None:
        result.append(_fact(
            row, metric="cash_dividend_profit_ratio", period=period,
            raw_value=ratio_raw, normalized_value=ratio, raw_unit="%",
            normalized_unit="%", fact_state="historical_reported",
            precision_rank=210,
            metadata={"column_role": "column_7", "matrix_period": period},
        ))
    return result


def extract_financial_facts(rows: Sequence[StructuredTableRow]) -> tuple[FinancialFact, ...]:
    facts: list[FinancialFact] = []
    dividend_history_tables = {
        (row.document_id if hasattr(row, "document_id") else row.doc_id, row.page_idx, row.table_index)
        for row in rows
        if "分红年度" in _compact(row.normalized_row_text)
        and "每10股" in _compact(row.normalized_row_text)
        and "现金分红" in _compact(row.normalized_row_text)
        and "净利润" in _compact(row.normalized_row_text)
    }
    for row in rows:
        table_key = (row.doc_id, row.page_idx, row.table_index)
        facts.extend(_extract_summary_facts(row))
        facts.extend(_extract_total_operating_revenue_facts(row))
        facts.extend(_extract_ratio_facts(row))
        facts.extend(_extract_dividend_facts(row))
        facts.extend(_extract_dividend_history_row(
            row,
            verified_dividend_table=table_key in dividend_history_tables,
        ))
        facts.extend(_extract_rd_amount_facts(row))
        facts.extend(_extract_balance_sheet_facts(row))
        facts.extend(_extract_overseas_facts(row))
        facts.extend(_extract_new_contract_facts(row))
    # Deduplicate exact source/metric/period/value records while retaining the
    # highest precision rank from repeated table extraction.
    dedup: dict[tuple[Any, ...], FinancialFact] = {}
    for fact in facts:
        key = (
            fact.document_id, fact.metric, fact.period, fact.normalized_value,
            fact.normalized_unit, fact.canonical_source,
        )
        current = dedup.get(key)
        if current is None or fact.precision_rank > current.precision_rank:
            dedup[key] = fact
    return tuple(sorted(
        dedup.values(),
        key=lambda fact: (
            fact.document_id, fact.metric, fact.period,
            -fact.precision_rank, fact.source_page, fact.source_table, fact.source_row,
        ),
    ))


@lru_cache(maxsize=128)
def load_document_financial_facts(
    structured_root: str,
    domain: str,
    doc_id: str,
) -> tuple[FinancialFact, ...]:
    rows = load_structured_table_rows(Path(structured_root), domain, doc_id)
    return extract_financial_facts(rows)


class FinancialMetricLedger:
    def __init__(self, facts: Iterable[FinancialFact] = ()) -> None:
        self._facts = tuple(facts)

    @classmethod
    def from_documents(
        cls,
        structured_root: str | Path,
        domain: str,
        doc_ids: Sequence[str],
    ) -> "FinancialMetricLedger":
        facts = [
            fact
            for doc_id in doc_ids
            for fact in load_document_financial_facts(str(Path(structured_root)), domain, str(doc_id))
        ]
        return cls(facts)

    @property
    def facts(self) -> tuple[FinancialFact, ...]:
        return self._facts

    def select(
        self,
        *,
        metric: str,
        entity_name: str = "",
        period: str = "",
        document_id: str = "",
        fact_states: Sequence[str] = (),
        normalized_unit: str = "",
        statement_scope: str = "",
        attribution_scope: str = "",
    ) -> tuple[FinancialFact, ...]:
        rows = [
            fact for fact in self._facts
            if fact.metric == metric
            and (not entity_name or fact.entity_name == entity_name)
            and (not period or fact.period == period)
            and (not document_id or fact.document_id == document_id)
            and (not fact_states or fact.fact_state in set(fact_states))
            and (not normalized_unit or fact.normalized_unit == normalized_unit)
            and (not statement_scope or fact.statement_scope == statement_scope)
            and (not attribution_scope or fact.attribution_scope == attribution_scope)
            and not fact.rejection_reasons
            and fact.normalized_value is not None
        ]
        return tuple(sorted(
            rows,
            key=lambda fact: (
                -fact.precision_rank,
                fact.source_page,
                fact.source_table,
                fact.source_row,
            ),
        ))

    def best(self, **kwargs: Any) -> FinancialFact | None:
        rows = self.select(**kwargs)
        if not rows:
            return None
        top = rows[0]
        tied = [
            row for row in rows
            if row.precision_rank == top.precision_rank
            and row.normalized_value != top.normalized_value
        ]
        if tied:
            return None
        return top

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_count": len(self._facts),
            "facts": [fact.to_dict() for fact in self._facts],
        }
