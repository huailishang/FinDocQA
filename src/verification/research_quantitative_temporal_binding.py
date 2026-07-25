"""Research quantitative and temporal-status proposition binding.

The binder is QID-agnostic.  It distinguishes direct quantitative statements
from derived calculations and treats realized, forecast and target states as
semantically different even when year/metric/value tokens happen to match.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Sequence


GROWTH_TYPES = ("LEVEL", "YOY_GROWTH", "CAGR", "INCREMENT", "SHARE", "RATIO", "DIFFERENCE")
STATUS_STATES = ("REALIZED", "HISTORICAL_ACTUAL", "FORECAST", "EXPECTED", "TARGET", "PLAN", "CONDITIONAL", "UNKNOWN")
TEMPORAL_RELATIONS = ("AT_YEAR", "BY_YEAR", "AS_OF_DATE", "FROM_TO", "NEXT_N_YEARS", "FORECAST_FOR_YEAR", "UNKNOWN")


METRICS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("居民", "可支配收入", "增速"), "disposable_income_growth"),
    (("冰雪装备", "市场规模"), "ice_snow_equipment_market_size"),
    (("数据中心", "半导体", "加速", "市场规模"), "data_center_semiconductor_acceleration_market_size"),
    (("服务零售", "商品零售"), "service_vs_goods_retail_growth"),
    (("韩国", "寿险", "银保", "保费", "贡献"), "korea_bancassurance_contribution"),
    (("韩国", "寿险", "银保", "复合增速"), "korea_bancassurance_cagr"),
    (("手续费", "佣金", "净收入", "增速"), "bank_fee_income_growth"),
    (("上市险企", "归母净利润"), "listed_insurer_net_profit"),
    (("居民", "收入", "人均名义", "gdp", "剪刀差"), "resident_income_vs_nominal_gdp_growth_spread"),
)

UNIT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*万亿元", "万亿元"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*亿美元", "亿美元"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*亿欧元", "亿欧元"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*亿元", "亿元"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*万元", "万元"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*元", "元"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*%", "%"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*倍", "倍"),
    (r"(?<!\d)(\d+(?:\.\d+)?)\s*pcts?", "pcts"),
)


@dataclass(frozen=True)
class QuantitativeProposition:
    text: str
    subject_entity: tuple[str, ...]
    metric: str
    metric_terms: tuple[str, ...]
    values: tuple[str, ...]
    units: tuple[str, ...]
    periods: tuple[str, ...]
    comparator: str
    trend_direction: str
    growth_type: str
    status_state: str
    forecast_horizon: str
    source_statement_type: str
    temporal_relation: str
    transition_direction: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compact(value: Any) -> str:
    return re.sub(r"[\s，。；：、（）()《》\[\]【】‘’“”\"']+", "", str(value or "")).replace("％", "%").lower()


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _metric(text: str) -> tuple[str, tuple[str, ...]]:
    body = compact(text)
    for terms, name in METRICS:
        if all(compact(term) in body for term in terms):
            return name, terms
    return "unresolved", ()


def _values_units(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    rows: list[tuple[int, str, str]] = []
    normalized = text.replace("％", "%")
    for pattern, unit in UNIT_PATTERNS:
        for match in re.finditer(pattern, normalized, re.I):
            rows.append((match.start(), match.group(1), unit))
    rows.sort(key=lambda row: row[0])
    return tuple(row[1] for row in rows), tuple(row[2] for row in rows)


def _periods(text: str) -> tuple[str, ...]:
    values = re.findall(r"(?<!\d)((?:19|20)\d{2})\s*年?", text)
    return tuple(dict.fromkeys(values))


def _growth_type(text: str) -> str:
    body = compact(text)
    if "cagr" in body or "复合增速" in body or "复合增长率" in body:
        return "CAGR"
    if any(token in body for token in ("同比", "增速", "增长率", "同比增长")):
        return "YOY_GROWTH"
    if any(token in body for token in ("增量", "新增", "增加额")):
        return "INCREMENT"
    if any(token in body for token in ("占比", "份额", "市占率")):
        return "SHARE"
    if any(token in body for token in ("比率", "比例", "杠杆")):
        return "RATIO"
    if any(token in body for token in ("差额", "剪刀差", "差值")):
        return "DIFFERENCE"
    return "LEVEL"


def _status_state(text: str) -> str:
    body = compact(text)
    if any(token in body for token in ("预计", "预测")):
        return "FORECAST"
    if any(token in body for token in ("有望", "预期")):
        return "EXPECTED"
    if any(token in body for token in ("目标", "力争")):
        return "TARGET"
    if any(token in body for token in ("规划", "计划")) and not any(token in body for token in ("计划期", "五年规划")):
        return "PLAN"
    if any(token in body for token in ("若", "如果", "假设", "情景下", "条件下")):
        return "CONDITIONAL"
    if any(token in body for token in ("将达到", "将达", "将增至", "将提升至", "未来达到")):
        return "FORECAST"
    if any(token in body for token in ("已达", "已达到", "实现", "录得", "达到", "降至", "升至", "为")):
        return "REALIZED"
    if re.search(r"(?:19|20)\d{2}\s*年", text):
        return "HISTORICAL_ACTUAL"
    return "UNKNOWN"


def _statement_type(status: str) -> str:
    if status in {"FORECAST", "EXPECTED"}:
        return "FORECAST_STATEMENT"
    if status in {"TARGET", "PLAN"}:
        return "TARGET_OR_PLAN_STATEMENT"
    if status == "CONDITIONAL":
        return "CONDITIONAL_STATEMENT"
    if status in {"REALIZED", "HISTORICAL_ACTUAL"}:
        return "ACTUAL_STATEMENT"
    return "UNKNOWN_STATEMENT"


def _temporal_relation(text: str, status: str, periods: Sequence[str]) -> tuple[str, str]:
    body = compact(text)
    horizon = periods[-1] if periods and status in {"FORECAST", "EXPECTED", "TARGET", "PLAN"} else ""
    if re.search(r"截至\s*(?:19|20)\d{2}", text):
        return "AS_OF_DATE", horizon
    if re.search(r"(?:19|20)\d{2}\s*[-—至]\s*(?:19|20)\d{2}", text):
        return "FROM_TO", horizon
    if re.search(r"未来\s*\d+\s*年", text):
        return "NEXT_N_YEARS", horizon
    if periods and status in {"FORECAST", "EXPECTED", "TARGET", "PLAN"}:
        return "FORECAST_FOR_YEAR", horizon
    if periods and any(token in body for token in ("到", "截至", "届时")):
        return "BY_YEAR", horizon
    if periods:
        return "AT_YEAR", horizon
    return "UNKNOWN", horizon


def _transition_direction(text: str) -> str:
    body = compact(text)
    if "由负转正" in body:
        return "NEG_TO_POS"
    if "由正转负" in body:
        return "POS_TO_NEG"
    if "转正" in body:
        return "TO_POS"
    if "转负" in body:
        return "TO_NEG"
    return "UNKNOWN"


def parse_proposition(text: str) -> QuantitativeProposition:
    metric, terms = _metric(text)
    values, units = _values_units(text)
    periods = _periods(text)
    status = _status_state(text)
    temporal, horizon = _temporal_relation(text, status, periods)
    body = compact(text)
    comparator = "GT" if any(t in body for t in ("超过", "高于", "大于")) else "LT" if any(t in body for t in ("低于", "少于", "小于")) else "GE" if "至少" in body else "LE" if "至多" in body else "EQ"
    trend = "DOWN" if any(t in body for t in ("下降", "降至", "下滑", "回落", "收窄")) else "UP" if any(t in body for t in ("上升", "升至", "增长", "提升", "扩大")) else "FLAT" if any(t in body for t in ("持平", "不变")) else "UNKNOWN"
    subjects = tuple(term for term in terms if term not in {"增速", "同比", "市场规模", "净利润"})
    return QuantitativeProposition(
        text=text, subject_entity=subjects, metric=metric, metric_terms=terms,
        values=values, units=units, periods=periods, comparator=comparator,
        trend_direction=trend, growth_type=_growth_type(text), status_state=status,
        forecast_horizon=horizon, source_statement_type=_statement_type(status), temporal_relation=temporal,
        transition_direction=_transition_direction(text),
    )


def _fragments(text: str, claim: QuantitativeProposition) -> list[str]:
    normalized = text.replace("\r", "\n")
    pieces = [piece.strip() for piece in re.split(r"(?<=[。！？；])|\n+", normalized) if piece.strip()]
    scored: list[tuple[int, int, str]] = []
    claim_periods = set(claim.periods)
    for index, piece in enumerate(pieces):
        body = compact(piece)
        metric_hits = sum(1 for term in claim.metric_terms if compact(term) in body)
        period_hits = sum(1 for period in claim_periods if period in body)
        value_hits = 0
        for value, unit in zip(claim.values, claim.units):
            if re.search(rf"(?<!\d){re.escape(value)}\s*{re.escape(unit)}", piece):
                value_hits += 1
        if metric_hits == 0 and value_hits == 0:
            continue
        score = metric_hits * 10 + period_hits * 5 + value_hits * 8
        if claim.growth_type != "LEVEL" and any(token in body for token in ("增速", "同比", "增长率", "cagr", "复合增速")):
            score += 4
        scored.append((score, -index, piece))
    return [row[2] for row in sorted(scored, reverse=True)]


def _status_relation(claim: str, source: str) -> str:
    if claim == source:
        return "MATCH"
    actual = {"REALIZED", "HISTORICAL_ACTUAL"}
    forward = {"FORECAST", "EXPECTED", "TARGET", "PLAN"}
    if claim in actual and source in forward:
        return "TEMPORAL_STATUS_CONFLICT"
    if claim in forward and source in actual:
        return "TEMPORAL_STATUS_CONFLICT"
    if claim in {"FORECAST", "EXPECTED"} and source in {"TARGET", "PLAN"}:
        return "STATUS_MISMATCH"
    if claim in {"TARGET", "PLAN"} and source in {"FORECAST", "EXPECTED"}:
        return "STATUS_MISMATCH"
    if claim == "UNKNOWN" or source == "UNKNOWN":
        return "UNKNOWN"
    return "COMPATIBLE"


def _numeric_relation(claim: QuantitativeProposition, source: QuantitativeProposition) -> dict[str, Any]:
    if not claim.values:
        return {"applicable": False, "match": True, "reason": "claim_has_no_numeric_value"}
    if not source.values:
        return {"applicable": True, "match": False, "reason": "source_has_no_numeric_value"}
    claim_pairs = list(zip(claim.values, claim.units))
    source_pairs = list(zip(source.values, source.units))
    pair_rows = []
    for c_value, c_unit in claim_pairs:
        c_num = _decimal(c_value)
        best = None
        for s_value, s_unit in source_pairs:
            s_num = _decimal(s_value)
            if c_num is None or s_num is None:
                continue
            unit_match = c_unit == s_unit
            delta = abs(c_num - s_num)
            row = {"claim_value": c_value, "claim_unit": c_unit, "source_value": s_value, "source_unit": s_unit, "unit_match": unit_match, "numeric_equal": delta <= Decimal("0.0001"), "delta": str(delta)}
            if best is None or (unit_match, -delta) > (best[0], -best[1]):
                best = (unit_match, delta, row)
        if best:
            pair_rows.append(best[2])
    match = bool(pair_rows) and all(row["unit_match"] and row["numeric_equal"] for row in pair_rows)
    return {"applicable": True, "match": match, "pairs": pair_rows, "reason": "match" if match else "NUMERIC_VALUE_OR_UNIT_CONFLICT"}


def bind_claim_to_texts(claim_text: str, source_texts: Sequence[str]) -> dict[str, Any]:
    claim = parse_proposition(claim_text)
    candidates: list[dict[str, Any]] = []
    for source_index, text in enumerate(source_texts):
        for fragment in _fragments(text, claim)[:8]:
            source = parse_proposition(fragment)
            metric_match = claim.metric != "unresolved" and source.metric == claim.metric
            period_match = not claim.periods or all(period in fragment for period in claim.periods)
            growth_match = claim.growth_type == source.growth_type or (claim.growth_type == "YOY_GROWTH" and source.growth_type == "YOY_GROWTH")
            numeric = _numeric_relation(claim, source)
            status_relation = _status_relation(claim.status_state, source.status_state)
            trend_match = claim.trend_direction == "UNKNOWN" or source.trend_direction == "UNKNOWN" or claim.trend_direction == source.trend_direction
            transition_match = claim.transition_direction == "UNKNOWN" or source.transition_direction == "UNKNOWN" or claim.transition_direction == source.transition_direction
            score = int(metric_match)*20 + int(period_match)*8 + int(numeric.get("match"))*12 + int(growth_match)*5 + int(status_relation in {"MATCH","COMPATIBLE","UNKNOWN"})*4 + int(trend_match)*2 + int(transition_match)*4
            candidates.append({
                "source_index": source_index, "fragment": fragment,
                "claim": claim.to_dict(), "source": source.to_dict(),
                "metric_match": metric_match, "period_match": period_match,
                "growth_type_match": growth_match, "trend_direction_match": trend_match, "transition_direction_match": transition_match,
                "numeric_relation": numeric, "status_relation": status_relation, "score": score,
            })
    if not candidates:
        growth_mode = "DERIVED_GROWTH_REQUIRED" if claim.growth_type in {"YOY_GROWTH", "CAGR"} else "NOT_GROWTH_CLAIM"
        return {"status": "UNRESOLVED", "reason": "NO_RELEVANT_SOURCE_FRAGMENT", "claim": claim.to_dict(), "binding_pass": False, "best": None, "growth_mode": growth_mode, "status_relation": "UNKNOWN"}
    best = max(candidates, key=lambda row: row["score"])
    source = best["source"]
    direct_growth = (
        claim.growth_type in {"YOY_GROWTH","CAGR"}
        and best["metric_match"] and best["period_match"] and best["numeric_relation"].get("match")
        and source["growth_type"] == claim.growth_type
    )
    derived_required = claim.growth_type in {"YOY_GROWTH","CAGR"} and not direct_growth
    growth_mode = "DIRECT_GROWTH_STATEMENT" if direct_growth else "DERIVED_GROWTH_REQUIRED" if derived_required else "NOT_GROWTH_CLAIM"

    decisive_scope = best["metric_match"] and best["period_match"]
    numeric = best["numeric_relation"]
    status_relation = best["status_relation"]
    transition_conflict = (
        claim.transition_direction != "UNKNOWN"
        and source["transition_direction"] != "UNKNOWN"
        and claim.transition_direction != source["transition_direction"]
    )
    if decisive_scope and transition_conflict:
        status = "CONTRADICTED"
        reason = "TRANSITION_DIRECTION_CONFLICT"
    elif decisive_scope and numeric.get("applicable") and not numeric.get("match") and numeric.get("pairs"):
        status = "CONTRADICTED"
        reason = "NUMERIC_VALUE_OR_UNIT_CONFLICT"
    elif decisive_scope and numeric.get("match") and status_relation == "TEMPORAL_STATUS_CONFLICT":
        status = "CONTRADICTED"
        reason = "TEMPORAL_STATUS_CONFLICT"
    elif decisive_scope and numeric.get("match") and best["growth_type_match"] and best["trend_direction_match"] and best["transition_direction_match"] and status_relation in {"MATCH","COMPATIBLE","UNKNOWN"}:
        status = "SUPPORTED"
        reason = growth_mode if growth_mode != "NOT_GROWTH_CLAIM" else "QUANTITATIVE_TEMPORAL_DIRECT_BINDING"
    else:
        status = "UNRESOLVED"
        reason = "INCOMPLETE_QUANTITATIVE_TEMPORAL_BINDING"
    binding_pass = status in {"SUPPORTED","CONTRADICTED"}
    return {
        "status": status, "reason": reason, "claim": claim.to_dict(), "binding_pass": binding_pass,
        "growth_mode": growth_mode, "status_relation": status_relation, "best": best,
        "candidate_count": len(candidates),
    }
