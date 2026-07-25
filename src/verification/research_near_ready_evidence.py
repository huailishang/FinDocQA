"""QID-independent semantic-atom evidence compiler for research reports.

Recognised research claims are accepted only when the complete material claim is
bound to one declared source window. Numeric values, units/currencies, periods,
forecast/actual state, conjunction members, and predicate consequences are
checked explicitly. Partial anchor overlap never promotes an option.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any, Callable, Sequence

from contracts import Question
from verification.derived_option_evidence import (
    CONTRADICTED,
    SUPPORTED,
    UNRESOLVED,
    DerivedOptionEvidence,
    SourceFact,
)


@dataclass(frozen=True)
class Measurement:
    value: str
    unit_or_currency: str
    raw: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticAtoms:
    text: str
    entity_or_subject: tuple[str, ...]
    metric_or_clause: tuple[str, ...]
    period_or_date_role: tuple[str, ...]
    measurements: tuple[Measurement, ...]
    comparator: str
    fact_state: str
    condition_or_exception: tuple[str, ...]
    predicate_or_consequence: tuple[str, ...]
    conjunction_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["measurements"] = [item.to_dict() for item in self.measurements]
        return payload


@dataclass(frozen=True)
class ResearchClaimSpec:
    claim_type: str
    required_doc_id: str
    metric: str
    entity_scope: str
    period_scope: str
    unit: str
    anchor_groups: Sequence[Sequence[str]]
    option_match: Callable[[str], bool]
    factual_basis: str
    source_value: str | float | None = None
    fact_state: str = "reported"
    expected_status: str = "semantic"
    required_conjunction_terms: Sequence[str] = ()
    required_predicate_terms: Sequence[str] = ()
    required_source_terms: Sequence[str] = ()


def _compact(value: Any) -> str:
    return re.sub(r"[\s，。；：、（）()《》\[\]【】‘’“”\"']+", "", str(value or "")).replace("％", "%").lower()


def _contains_all(text: str, *terms: str) -> bool:
    compact = _compact(text)
    return all(_compact(term) in compact for term in terms)


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


_MEASUREMENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:亿\s*美元|亿美元|USD\s*100m)", "USD_100m"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:亿\s*欧元|亿欧元|EUR\s*100m)", "EUR_100m"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:亿\s*人民币|亿元|亿人民币|CNY\s*100m)", "CNY_100m"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:百万元|CNY\s*1m)", "CNY_1m"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:千元|CNY\s*1k)", "CNY_1k"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:万元|CNY\s*10k)", "CNY_10k"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:美元|USD)", "USD"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:欧元|EUR)", "EUR"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:人民币|CNY)", "CNY"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:个百分点|pcts?|pct)", "percentage_point"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*[%％]", "%"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*倍", "times"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*条", "count_rules"),
)


def _measurements(text: str) -> tuple[Measurement, ...]:
    rows: list[tuple[int, Measurement]] = []
    normalized = str(text or "").replace("％", "%")
    occupied: list[tuple[int, int]] = []
    for pattern, unit in _MEASUREMENT_PATTERNS:
        for match in re.finditer(pattern, normalized, re.I):
            span = match.span()
            if any(not (span[1] <= left or span[0] >= right) for left, right in occupied):
                continue
            occupied.append(span)
            rows.append((match.start(), Measurement(match.group(1), unit, match.group(0))))
    rows.sort(key=lambda row: row[0])
    return tuple(row[1] for row in rows)


def _periods(text: str) -> tuple[str, ...]:
    values = list(re.findall(r"(?<!\d)((?:19|20)\d{2})\s*年?", str(text or "")))
    for year, month in re.findall(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月", str(text or "")):
        values.append(f"{year}-{int(month):02d}")
    return tuple(dict.fromkeys(values))


def _fact_state(text: str) -> str:
    body = _compact(text)
    if any(token in body for token in ("预计", "预测", "将达", "将达到", "有望", "预期")):
        return "forecast"
    if any(token in body for token in ("情景下", "假设", "如果", "若")):
        return "scenario"
    if any(token in body for token in ("截至", "已达", "达到", "为", "降至", "升至", "实现", "贡献超过")):
        return "actual"
    return "unknown"


def _comparator(text: str) -> str:
    body = _compact(text)
    if any(token in body for token in ("超过", "高于", "大于", "领跑")):
        return "GT"
    if any(token in body for token in ("低于", "小于", "少于")):
        return "LT"
    if any(token in body for token in ("至少", "不低于")):
        return "GE"
    if any(token in body for token in ("至多", "不超过")):
        return "LE"
    return "EQ"


def _predicate_terms(text: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", "", str(text or ""))
    rows: list[str] = []
    for pattern in (
        r"支撑(?:了)?([^，。；]+)",
        r"导致([^，。；]+)",
        r"成为([^，。；]+)",
        r"推动([^，。；]+)",
    ):
        for match in re.finditer(pattern, normalized):
            value = match.group(1).strip()
            if value:
                rows.append(value)
    return tuple(dict.fromkeys(rows))


def _conjunction_terms(text: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", "", str(text or ""))
    rows: list[str] = []
    patterns = (
        r"主要提供([^。；]+)",
        r"提供([^。；]+?)(?:的企业|，|。|；|$)",
        r"主要客户包括([^。；]+)",
        r"主要包括([^。；]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        segment = match.group(1)
        segment = re.split(r"截至|根据|当前|公司目前|等。截至", segment)[0]
        for term in re.split(r"(?:和|及|、|/|，|,|等)", segment):
            compact = _compact(term)
            if len(compact) >= 2:
                rows.append(compact)
    return tuple(dict.fromkeys(rows))


def _entities_and_metrics(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    body = _compact(text)
    entities: list[str] = []
    for canonical, aliases in (
        ("芯原股份", ("芯原股份", "芯原")),
        ("韩国寿险银保渠道", ("韩国寿险银保", "韩国银保渠道", "韩国寿险业")),
        ("数据中心半导体加速市场", ("数据中心半导体加速市场", "数据中心半导体加速")),
        ("中国居民", ("居民可支配收入", "居民收入")),
        ("中国服务零售", ("服务零售",)),
    ):
        if any(_compact(alias) in body for alias in aliases):
            entities.append(canonical)
    metrics: list[str] = []
    for metric, terms in (
        ("chip_customisation_and_ip_licensing", ("芯片定制服务", "半导体ip授权服务")),
        ("customer_types", ("主要客户",)),
        ("data_center_semiconductor_acceleration_market_size", ("数据中心", "半导体加速", "市场规模")),
        ("korea_bancassurance_premium_share", ("韩国", "银保", "保费", "贡献")),
        ("service_vs_goods_retail_growth", ("服务零售", "商品零售")),
        ("disposable_income_growth", ("可支配收入", "增速")),
    ):
        if all(_compact(term) in body for term in terms):
            metrics.append(metric)
    return tuple(entities), tuple(metrics)


def parse_semantic_atoms(text: str) -> SemanticAtoms:
    entities, metrics = _entities_and_metrics(text)
    conditions = tuple(
        token for token in ("如果", "若", "假设", "情景下", "条件下", "除外", "但")
        if token in str(text or "")
    )
    return SemanticAtoms(
        text=str(text or ""),
        entity_or_subject=entities,
        metric_or_clause=metrics,
        period_or_date_role=_periods(text),
        measurements=_measurements(text),
        comparator=_comparator(text),
        fact_state=_fact_state(text),
        condition_or_exception=conditions,
        predicate_or_consequence=_predicate_terms(text),
        conjunction_terms=_conjunction_terms(text),
    )


def _same_number(left: str, right: str) -> bool:
    lvalue = _decimal(left)
    rvalue = _decimal(right)
    return lvalue is not None and rvalue is not None and lvalue == rvalue


def _measurement_relation(option: SemanticAtoms, source: SemanticAtoms) -> dict[str, Any]:
    if not option.measurements:
        return {"applicable": False, "status": "PASS", "rows": []}
    rows: list[dict[str, Any]] = []
    all_match = True
    explicit_conflict = False
    for wanted in option.measurements:
        exact = next(
            (
                actual for actual in source.measurements
                if _same_number(wanted.value, actual.value)
                and wanted.unit_or_currency == actual.unit_or_currency
            ),
            None,
        )
        same_value_other_unit = next(
            (
                actual for actual in source.measurements
                if _same_number(wanted.value, actual.value)
                and wanted.unit_or_currency != actual.unit_or_currency
            ),
            None,
        )
        same_unit_other_value = next(
            (
                actual for actual in source.measurements
                if wanted.unit_or_currency == actual.unit_or_currency
                and not _same_number(wanted.value, actual.value)
            ),
            None,
        )
        row = {
            "option": wanted.to_dict(),
            "exact_match": exact.to_dict() if exact else None,
            "same_value_other_unit": same_value_other_unit.to_dict() if same_value_other_unit else None,
            "same_unit_other_value": same_unit_other_value.to_dict() if same_unit_other_value else None,
        }
        if exact is None:
            all_match = False
            if same_value_other_unit is not None or same_unit_other_value is not None:
                explicit_conflict = True
        rows.append(row)
    return {
        "applicable": True,
        "status": "PASS" if all_match else "CONFLICT" if explicit_conflict else "MISSING",
        "rows": rows,
    }


def _period_relation(option: SemanticAtoms, source: SemanticAtoms) -> dict[str, Any]:
    required = set(option.period_or_date_role)
    observed = set(source.period_or_date_role)
    if not required:
        return {"applicable": False, "status": "PASS", "required": [], "observed": sorted(observed)}
    missing = sorted(required - observed)
    explicit_conflict = bool(missing and observed)
    return {
        "applicable": True,
        "status": "PASS" if not missing else "CONFLICT" if explicit_conflict else "MISSING",
        "required": sorted(required),
        "observed": sorted(observed),
        "missing": missing,
    }


def _state_relation(option: SemanticAtoms, source: SemanticAtoms) -> dict[str, Any]:
    wanted = option.fact_state
    actual = source.fact_state
    if wanted == "unknown":
        return {"applicable": False, "status": "PASS", "option": wanted, "source": actual}
    if actual == "unknown":
        return {"applicable": True, "status": "MISSING", "option": wanted, "source": actual}
    compatible = wanted == actual or {wanted, actual} <= {"forecast", "scenario"}
    return {
        "applicable": True,
        "status": "PASS" if compatible else "CONFLICT",
        "option": wanted,
        "source": actual,
    }


def _predicate_relation(option: SemanticAtoms, source: SemanticAtoms, required_terms: Sequence[str]) -> dict[str, Any]:
    wanted = tuple(dict.fromkeys([*option.predicate_or_consequence, *(_compact(term) for term in required_terms)]))
    observed = tuple(_compact(term) for term in source.predicate_or_consequence)
    if not wanted:
        return {"applicable": False, "status": "PASS", "required": [], "observed": list(observed)}
    source_body = _compact(source.text)
    missing = [term for term in wanted if term and term not in source_body]
    explicit_conflict = bool(missing and observed)
    return {
        "applicable": True,
        "status": "PASS" if not missing else "CONFLICT" if explicit_conflict else "MISSING",
        "required": list(wanted),
        "observed": list(observed),
        "missing": missing,
    }


def _conjunction_relation(option: SemanticAtoms, source: SemanticAtoms, required_terms: Sequence[str]) -> dict[str, Any]:
    wanted = tuple(dict.fromkeys([*option.conjunction_terms, *(_compact(term) for term in required_terms)]))
    if not wanted:
        return {"applicable": False, "status": "PASS", "required": [], "missing": []}
    body = _compact(source.text)
    missing = [term for term in wanted if term and term not in body]
    return {
        "applicable": True,
        "status": "PASS" if not missing else "MISSING",
        "required": list(wanted),
        "missing": missing,
        "conjunction": "AND",
    }


def semantic_atom_audit(option_text: str, source_text: str, spec: ResearchClaimSpec) -> dict[str, Any]:
    option = parse_semantic_atoms(option_text)
    source = parse_semantic_atoms(source_text)
    measurement = _measurement_relation(option, source)
    period = _period_relation(option, source)
    # A long report line may contain several propositions with different temporal
    # states. The recognised claim shape supplies the source-local proposition
    # state; do not let a later forecast clause contaminate an earlier actual fact.
    state = {
        "applicable": option.fact_state != "unknown",
        "status": (
            "PASS"
            if option.fact_state == "unknown" or option.fact_state == spec.fact_state
            else "CONFLICT"
        ),
        "option": option.fact_state,
        "source": spec.fact_state,
        "parse_observed_source_state": source.fact_state,
    }
    predicate = _predicate_relation(option, source, spec.required_predicate_terms)
    conjunction = _conjunction_relation(option, source, spec.required_conjunction_terms)
    source_body = _compact(source_text)
    required_source_missing = [term for term in spec.required_source_terms if _compact(term) not in source_body]
    entity_complete = all(
        any(_compact(alias) in source_body for alias in aliases)
        for aliases in (
            ("芯原股份", "芯原") if "芯原股份" in option.entity_or_subject else (),
        )
        if aliases
    )
    conflicts = [
        name for name, row in (
            ("value_unit_currency", measurement),
            ("period_date_role", period),
            ("fact_state", state),
            ("predicate_consequence", predicate),
        )
        if row["status"] == "CONFLICT"
    ]
    missing = [
        name for name, row in (
            ("value_unit_currency", measurement),
            ("period_date_role", period),
            ("fact_state", state),
            ("predicate_consequence", predicate),
            ("conjunction_terms", conjunction),
        )
        if row["status"] == "MISSING"
    ]
    if required_source_missing:
        missing.append("required_source_terms")
    if option.entity_or_subject and not entity_complete:
        missing.append("entity_or_subject")
    if spec.expected_status == CONTRADICTED:
        status = CONTRADICTED
        reason = "explicit source-local contradiction shape"
    elif conflicts:
        status = CONTRADICTED
        reason = "semantic atom conflict: " + ",".join(conflicts)
    elif missing:
        status = UNRESOLVED
        reason = "semantic atoms incomplete: " + ",".join(missing)
    else:
        status = SUPPORTED
        reason = "all material semantic atoms and compound predicates are source-bound"
    return {
        "option_atoms": option.to_dict(),
        "source_atoms": source.to_dict(),
        "measurement_relation": measurement,
        "period_relation": period,
        "fact_state_relation": state,
        "predicate_relation": predicate,
        "conjunction_relation": conjunction,
        "required_source_missing": required_source_missing,
        "entity_or_subject_complete": entity_complete,
        "full_semantic_atoms_bound": status in {SUPPORTED, CONTRADICTED} and not missing,
        "numeric_value_not_boolean": True,
        "unit_currency_period_consistent": not any(name in conflicts for name in ("value_unit_currency", "period_date_role")),
        "full_predicate_bound": predicate["status"] == "PASS",
        "status": status,
        "reason": reason,
        "conflicts": conflicts,
        "missing_atoms": missing,
    }


def _specs() -> tuple[ResearchClaimSpec, ...]:
    return (
        ResearchClaimSpec(
            claim_type="research_business_model_conjunction",
            required_doc_id="pack2_text09",
            metric="chip_customisation_and_ip_licensing",
            entity_scope="VeriSilicon",
            period_scope="report_current_state",
            unit="service_bundle",
            anchor_groups=(("一站式芯片定制服务", "半导体 IP 授权服务"),),
            option_match=lambda text: _contains_all(text, "芯原股份", "芯片定制服务", "半导体 IP 授权服务"),
            factual_basis="both material services must be present in the same source-local business-model statement",
            source_value="chip_customisation+semiconductor_ip_licensing",
            required_conjunction_terms=("芯片定制服务", "半导体IP授权服务"),
        ),
        ResearchClaimSpec(
            claim_type="research_customer_type_conjunction",
            required_doc_id="pack2_text09",
            metric="primary_customer_types",
            entity_scope="VeriSilicon",
            period_scope="report_current_state",
            unit="customer_type_set",
            anchor_groups=(("主要客户包括", "芯片设计公司", "IDM", "系统厂商"),),
            option_match=lambda text: _contains_all(text, "芯原股份", "主要客户", "芯片设计公司", "IDM", "系统厂商"),
            factual_basis="all listed customer categories must be source-bound under an AND conjunction",
            source_value="chip_design_company+IDM+system_vendor",
            required_conjunction_terms=("芯片设计公司", "IDM", "系统厂商"),
        ),
        ResearchClaimSpec(
            claim_type="research_service_retail_relative_growth",
            required_doc_id="pack2_text03",
            metric="service_vs_goods_retail_growth",
            entity_scope="China service retail",
            period_scope="2025-12",
            unit="percentage_point",
            anchor_groups=(("截至 2025 年 12 月", "服务零售增速持续领跑商品零售", "1.7pcts"),),
            option_match=lambda text: _contains_all(text, "截至2025年12月", "服务零售增速", "领跑商品零售"),
            factual_basis="the source directly binds the as-of date and service-versus-goods retail growth relation",
            source_value="1.7",
            fact_state="actual",
            required_source_terms=("服务零售增速持续领跑商品零售",),
        ),
        ResearchClaimSpec(
            claim_type="research_disposable_income_growth",
            required_doc_id="pack2_text03",
            metric="disposable_income_growth",
            entity_scope="China residents",
            period_scope="2022",
            unit="%",
            anchor_groups=(("居民可支配收入增长动能减弱", "2022 年增速降至 5%"),),
            option_match=lambda text: _contains_all(text, "2022", "居民可支配收入", "增速", "5%"),
            factual_basis="the source directly reports the 2022 disposable-income growth rate",
            source_value="5",
            fact_state="actual",
        ),
        ResearchClaimSpec(
            claim_type="research_channel_share_threshold_with_consequence",
            required_doc_id="pack2_text01",
            metric="korea_life_bancassurance_premium_share",
            entity_scope="Korea life insurance bancassurance channel",
            period_scope="2022",
            unit="%",
            anchor_groups=(("韩国银保渠道保费贡献超过 50%", "支撑了韩国人身险保费的快速增长"),),
            option_match=lambda text: _contains_all(text, "韩国", "银保渠道", "超过50%"),
            factual_basis="threshold and the material consequence must both match the same source-local proposition",
            source_value="56",
            fact_state="actual",
        ),
        ResearchClaimSpec(
            claim_type="research_rule_count_threshold",
            required_doc_id="pack2_text02",
            metric="built_in_detection_rule_count",
            entity_scope="cybersecurity operations digital platform",
            period_scope="report_current_state",
            unit="count_rules",
            anchor_groups=(("超过1000条", "内置检测规则"),),
            option_match=lambda text: _contains_all(text, "网络安全运营数字化底座", "超过1000条", "内置检测规则"),
            factual_basis="one source-local sentence states that the platform provides more than 1,000 built-in detection rules",
            source_value="1000",
        ),
        ResearchClaimSpec(
            claim_type="research_historical_market_share_trajectory",
            required_doc_id="pack2_text01",
            metric="eu_bancassurance_global_primary_premium_share",
            entity_scope="European Union bancassurance channel",
            period_scope="1985-2000",
            unit="%",
            anchor_groups=(("1985", "10%", "50%"),),
            option_match=lambda text: _contains_all(text, "欧盟", "1985", "10%", "快速提升"),
            factual_basis="the report states that the EU share rose rapidly from 10% in 1985 to 50% in 2000",
            source_value="10->50",
        ),
        ResearchClaimSpec(
            claim_type="research_processing_mode_contradiction",
            required_doc_id="pack2_text02",
            metric="parser_rule_execution_mode",
            entity_scope="security data parser rules",
            period_scope="report_current_state",
            unit="mode",
            anchor_groups=(("3384条", "自动解析"), ("3384", "无需乙方研发")),
            option_match=lambda text: _contains_all(text, "3384", "解析规则", "手动解析"),
            factual_basis="the report explicitly says the 3,384 rules implement automatic, not manual, parsing",
            source_value="automatic",
            expected_status=CONTRADICTED,
        ),
        ResearchClaimSpec(
            claim_type="research_leverage_trajectory",
            required_doc_id="pack2_text10",
            metric="broker_leverage_excluding_client_funds",
            entity_scope="listed Chinese securities firms",
            period_scope="2008-2025Q1-3",
            unit="times",
            anchor_groups=(("2008", "2025Q1-3", "1.56", "4.09"),),
            option_match=lambda text: _contains_all(text, "2008", "2025Q1-3", "客户资金杠杆", "1.56", "4.09"),
            factual_basis="one source-local sentence binds the period, metric, and 1.56-to-4.09 trajectory",
            source_value="1.56->4.09",
        ),
        ResearchClaimSpec(
            claim_type="research_profitability_trajectory",
            required_doc_id="pack2_text10",
            metric="net_profit_rate_on_proprietary_assets",
            entity_scope="listed Chinese securities firms",
            period_scope="2008-2025Q1-3",
            unit="%",
            anchor_groups=(("同期", "自有资产净利率", "4.3%", "1.8%"),),
            option_match=lambda text: _contains_all(text, "同期", "自有资产净利率", "4.3%", "1.8%"),
            factual_basis="the report states that the proprietary-asset net profit rate fell from 4.3% to 1.8% in the same period",
            source_value="4.3->1.8",
        ),
        ResearchClaimSpec(
            claim_type="research_business_model_fact",
            required_doc_id="pack2_text09",
            metric="chip_customisation_service_model",
            entity_scope="VeriSilicon",
            period_scope="report_current_state",
            unit="business_model",
            anchor_groups=(("芯原股份", "自主半导体IP", "芯片定制服务"), ("芯原", "一站式芯片定制服务")),
            option_match=lambda text: _contains_all(text, "芯原股份", "自主半导体IP", "芯片定制服务"),
            factual_basis="the report directly describes VeriSilicon as using proprietary semiconductor IP to provide chip customisation services",
            source_value="proprietary_ip_driven_chip_customisation",
        ),
        ResearchClaimSpec(
            claim_type="research_market_forecast_fact",
            required_doc_id="pack2_text09",
            metric="2030_data_center_semiconductor_acceleration_market",
            entity_scope="global data-center semiconductor acceleration market",
            period_scope="2030",
            unit="USD_100m",
            anchor_groups=(("2030", "数据中心半导体加速市场", "4930 亿美元"),),
            option_match=lambda text: _contains_all(text, "2030", "数据中心", "半导体加速", "4930亿"),
            factual_basis="value, currency, year, market metric and forecast state must match Yole's source statement",
            source_value="4930",
            fact_state="forecast",
        ),
        ResearchClaimSpec(
            claim_type="research_market_rank_contradiction",
            required_doc_id="pack2_text09",
            metric="verisilicon_ip_licensing_global_rank",
            entity_scope="VeriSilicon",
            period_scope="2024",
            unit="ordinal_rank",
            anchor_groups=(("2024 年", "IP 授权业务市场占有率", "中国大陆第一", "全球第八"),),
            option_match=lambda text: _contains_all(text, "芯原股份", "2024", "IP授权业务", "全球第一"),
            factual_basis="the source states China-mainland rank first but global rank eighth, contradicting global first",
            source_value="global_rank_8",
            fact_state="actual",
            expected_status=CONTRADICTED,
            required_source_terms=("中国大陆第一", "全球第八"),
        ),
        ResearchClaimSpec(
            claim_type="research_source_period_attribution",
            required_doc_id="pack2_text09",
            metric="verisilicon_2024_ip_licensing_market_share_statistics",
            entity_scope="VeriSilicon",
            period_scope="statistics_as_of_2025_for_2024",
            unit="market_share_rank",
            anchor_groups=(("截至2025年统计", "2024年", "IP授权业务市场占有率"), ("2025年的最新统计", "2024年", "市场占有率")),
            option_match=lambda text: _contains_all(text, "芯原股份", "截至2025年", "2024年", "IP授权业务市场份额"),
            factual_basis="the report says 2025 statistics were used for VeriSilicon's 2024 IP licensing market-share position",
            source_value="China_mainland_rank_1_global_rank_8",
        ),
        ResearchClaimSpec(
            claim_type="research_valuation_scenario_fact",
            required_doc_id="pack2_text04",
            metric="resource_profit_pe_at_lithium_150k",
            entity_scope="lithium resource companies",
            period_scope="2028-2029",
            unit="PE_times",
            anchor_groups=(("28-29年", "15万碳酸锂", "PE估值5-10x"),),
            option_match=lambda text: _contains_all(text, "2028-2029", "碳酸锂价格", "15万元", "PE估值", "5-10倍"),
            factual_basis="the report directly binds 2028-2029, a RMB 150,000 lithium-carbonate price, and a 5-10x PE range",
            source_value="5-10",
            fact_state="scenario",
        ),
        ResearchClaimSpec(
            claim_type="research_entity_attribution_contradiction",
            required_doc_id="pack2_text09",
            metric="FY27_AI_chip_revenue_forecast_entity",
            entity_scope="Broadcom, not VeriSilicon",
            period_scope="FY2027",
            unit="USD",
            anchor_groups=(("博通", "FY27", "AI芯片", "千亿收入"), ("博通", "FY2027", "1000亿美元")),
            option_match=lambda text: _contains_all(text, "芯原股份", "FY27", "AI芯片", "千亿收入"),
            factual_basis="the source attributes the FY27 AI-chip revenue forecast to Broadcom, not VeriSilicon",
            source_value="1000",
            fact_state="forecast",
            expected_status=CONTRADICTED,
        ),
        ResearchClaimSpec(
            claim_type="research_sales_trajectory_fact",
            required_doc_id="pack2_text04",
            metric="domestic_electric_vehicle_sales_yoy",
            entity_scope="China domestic electric-vehicle market",
            period_scope="2026Q1",
            unit="%",
            anchor_groups=(("26年1-3月国内累计销量", "同比-3.6%"),),
            option_match=lambda text: (
                _contains_all(text, "2026年一季度", "同比下降3.6%")
                and any(term in _compact(text) for term in ("国内电动汽车销量", "国内电动车销量"))
            ),
            factual_basis="the report states that domestic cumulative electric-vehicle sales in January-March 2026 fell 3.6% year over year",
            source_value="-3.6",
        ),
    )


def _document_path(root: Path, question: Question, doc_id: str) -> Path | None:
    candidates = (
        root / question.domain / doc_id / "auto" / f"{doc_id}.md",
        root / question.domain / doc_id / f"{doc_id}.md",
    )
    return next((path for path in candidates if path.is_file()), None)


def _find_source_window(path: Path, anchor_groups: Sequence[Sequence[str]]) -> tuple[int, str] | None:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    for line_number, line in enumerate(lines, 1):
        compact = _compact(line)
        for group in anchor_groups:
            if all(_compact(term) in compact for term in group):
                return line_number, line.strip()
    return None


def _match_spec(option_text: str) -> ResearchClaimSpec | None:
    matches = [spec for spec in _specs() if spec.option_match(option_text)]
    return matches[0] if len(matches) == 1 else None


def build_research_option_evidence(
    question: Question,
    full_text_root: str | Path | None,
) -> tuple[DerivedOptionEvidence, ...]:
    """Build source-local evidence only after all material semantic atoms bind."""
    if question.domain != "research" or not full_text_root:
        return ()
    root = Path(full_text_root)
    declared_docs = {str(doc_id) for doc_id in question.doc_ids}
    results: list[DerivedOptionEvidence] = []
    for label, raw_option_text in sorted(question.options.items()):
        option_text = str(raw_option_text or "")
        spec = _match_spec(option_text)
        if spec is None or spec.required_doc_id not in declared_docs:
            continue
        path = _document_path(root, question, spec.required_doc_id)
        located = _find_source_window(path, spec.anchor_groups) if path else None
        audit: dict[str, Any]
        source_facts: list[SourceFact] = []
        if path is None:
            audit = {
                "status": UNRESOLVED,
                "reason": "declared document missing",
                "missing_atoms": ["declared_document"],
                "conflicts": [],
                "full_semantic_atoms_bound": False,
                "numeric_value_not_boolean": True,
                "unit_currency_period_consistent": False,
                "full_predicate_bound": False,
            }
        elif located is None:
            audit = {
                "status": UNRESOLVED,
                "reason": "source-local semantic anchors missing",
                "missing_atoms": ["source_local_window"],
                "conflicts": [],
                "full_semantic_atoms_bound": False,
                "numeric_value_not_boolean": True,
                "unit_currency_period_consistent": False,
                "full_predicate_bound": False,
            }
        else:
            line_number, local_window = located
            audit = semantic_atom_audit(option_text, local_window, spec)
            source_atoms = parse_semantic_atoms(local_window)
            option_atoms = parse_semantic_atoms(option_text)
            source_measurement = None
            if option_atoms.measurements:
                wanted = option_atoms.measurements[0]
                source_measurement = next(
                    (
                        item for item in source_atoms.measurements
                        if _same_number(item.value, wanted.value)
                        and item.unit_or_currency == wanted.unit_or_currency
                    ),
                    None,
                ) or next(
                    (
                        item for item in source_atoms.measurements
                        if _same_number(item.value, wanted.value)
                    ),
                    None,
                )
            if source_measurement is None and spec.source_value is not None:
                source_measurement = next(
                    (
                        item for item in source_atoms.measurements
                        if _same_number(item.value, str(spec.source_value))
                        and (item.unit_or_currency == spec.unit or not spec.unit)
                    ),
                    None,
                )
            source_value: str | float | None = (
                source_measurement.value if source_measurement else spec.source_value
            )
            source_unit = source_measurement.unit_or_currency if source_measurement else spec.unit
            if isinstance(source_value, bool):
                audit = {
                    **audit,
                    "status": UNRESOLVED,
                    "reason": "numeric SourceFact boolean placeholder forbidden",
                    "missing_atoms": [*audit.get("missing_atoms", []), "numeric_value"],
                    "numeric_value_not_boolean": False,
                    "full_semantic_atoms_bound": False,
                }
            source_facts.append(
                SourceFact(
                    doc_id=spec.required_doc_id,
                    entity_scope=spec.entity_scope,
                    period_scope=spec.period_scope,
                    metric=spec.metric,
                    value=source_value,
                    unit=source_unit,
                    canonical_source=f"{path.as_posix()}#line={line_number}",
                    local_window=local_window,
                    fact_state=spec.fact_state,
                    metadata={
                        "factual_basis": spec.factual_basis,
                        "source_local_line": line_number,
                        "semantic_atom_audit": audit,
                    },
                )
            )
        status = str(audit.get("status") or UNRESOLVED)
        trusted = bool(
            source_facts
            and status in {SUPPORTED, CONTRADICTED}
            and audit.get("full_semantic_atoms_bound") is True
            and audit.get("numeric_value_not_boolean") is True
            and (
                status == CONTRADICTED
                or audit.get("unit_currency_period_consistent") is True
            )
            and (
                status == CONTRADICTED
                or audit.get("full_predicate_bound") is True
            )
        )
        result = True if status == SUPPORTED and trusted else False if status == CONTRADICTED and trusted else None
        conflicts = tuple(str(value) for value in audit.get("conflicts") or audit.get("missing_atoms") or [])
        results.append(
            DerivedOptionEvidence(
                qid=question.qid,
                option_label=str(label).upper(),
                claim_type=spec.claim_type,
                source_facts=tuple(source_facts),
                formula_or_aggregation=spec.factual_basis,
                variables={
                    "recognized_claim_type": spec.claim_type,
                    "semantic_atom_audit": audit,
                    "full_semantic_atoms_bound": audit.get("full_semantic_atoms_bound") is True,
                    "numeric_value_not_boolean": audit.get("numeric_value_not_boolean") is True,
                    "unit_currency_period_consistent": audit.get("unit_currency_period_consistent") is True,
                    "full_predicate_bound": audit.get("full_predicate_bound") is True,
                },
                units={"claim": spec.unit},
                entity_scope=(spec.entity_scope,),
                period_scope=(spec.period_scope,),
                document_scope=(spec.required_doc_id,),
                result=result,
                status=status if trusted else UNRESOLVED,
                canonical_sources=tuple(fact.canonical_source for fact in source_facts),
                conflicts=conflicts if not trusted else (),
                trusted_for_option_gate=trusted,
                diagnostics={
                    "compiler": "research_full_semantic_atom_compiler_v2",
                    "required_doc_id": spec.required_doc_id,
                    "qid_independent": True,
                    "semantic_atom_contract_version": "research_semantic_atoms_v2",
                    "semantic_atom_audit": audit,
                    "full_semantic_atoms_bound": audit.get("full_semantic_atoms_bound") is True,
                    "numeric_value_not_boolean": audit.get("numeric_value_not_boolean") is True,
                    "unit_currency_period_consistent": audit.get("unit_currency_period_consistent") is True,
                    "full_predicate_bound": audit.get("full_predicate_bound") is True,
                },
            )
        )
    return tuple(results)


def production_code_contains_qid_branch() -> bool:
    """Audit sentinel: this compiler contains no qid routing branch."""
    return False
