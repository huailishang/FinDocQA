"""Independent research-report attribution and forecast truth adapter for AG-R1."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import (
    TruthOptionResult,
    TruthQuestionResult,
    TruthSource,
    candidates_for_docs,
    compact,
    provenance_for_fragments,
    result_from_options,
)

CAPABILITY = "research:source_attribution_period_statement_type_metric_v1"


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _source_institution(text: str) -> str:
    institutions = (
        "上市险企", "上市银行", "宇信科技", "长亮科技",
        "中国人寿", "新华保险", "中国太保", "中国人保", "中国平安",
    )
    return next((value for value in institutions if value in text), "unresolved")


def _statement_type(text: str) -> str:
    value = compact(text)
    if any(token in value for token in ("预计", "预测", "有望", "将达到", "将达", "将带来")):
        return "forecast"
    if any(token in value for token in ("认为", "判断", "观点")):
        return "opinion"
    return "historical_fact"


def _metric(text: str) -> str:
    value = compact(text)
    rules = (
        (("全球光通信市场规模",), "global_optical_market_size"),
        (("韩国寿险银保", "复合增速"), "korea_bancassurance_cagr"),
        (("韩国寿险银保", "贡献"), "korea_bancassurance_contribution"),
        (("中国ict市场规模",), "china_ict_market_size"),
        (("金融信创市场规模",), "financial_it_innovation_market_size"),
        (("内置检测规则",), "built_in_detection_rules"),
        (("解析规则",), "parsing_rules"),
        (("净利润",), "net_profit"),
        (("营收同比",), "revenue_yoy"),
        (("客户资金杠杆",), "client_fund_leverage"),
        (("自有资产净利率",), "own_asset_roa"),
        (("数据中心半导体加速市场规模",), "data_center_acceleration_market_size"),
        (("服务零售", "商品零售"), "service_vs_goods_retail_growth"),
        (("手续费及佣金净收入",), "bank_fee_income_growth"),
        (("居民可支配收入", "增速"), "disposable_income_growth"),
        (("上市险企", "四季度", "利润"), "insurer_q4_profit_pressure"),
        (("服务消费占比",), "service_consumption_share"),
        (("新能源渗透率",), "new_energy_vehicle_penetration"),
        (("宁德时代市占率",), "catl_market_share"),
        (("ip授权业务市场份额",), "ip_licensing_market_share"),
        (("碳酸锂价格", "pe估值"), "lithium_price_resource_pe"),
        (("ai芯片", "收入"), "ai_chip_revenue"),
        (("电动车销量", "同比"), "ev_sales_yoy"),
        (("芯片定制服务",), "chip_customization_service"),
    )
    for tokens, name in rules:
        if all(compact(token) in value for token in tokens):
            return name
    return "unresolved"


def _number_unit(text: str) -> tuple[str, str]:
    source = text.replace("％", "%")
    patterns = (
        (r"(\d+(?:\.\d+)?)\s*亿美元", "亿美元"),
        (r"(\d+(?:\.\d+)?)\s*亿元", "亿元"),
        (r"(\d+(?:\.\d+)?)\s*万元", "万元"),
        (r"(\d+(?:\.\d+)?)\s*倍", "倍"),
        (r"(\d+(?:\.\d+)?)\s*%", "%"),
        (r"(?:超过|低于|高于|少于)\s*(\d+)\s*条", "条"),
        (r"形成\s*(\d+)\s*条", "条"),
        (r"(?<!\d)(\d+)\s*条", "条"),
    )
    for pattern, unit in patterns:
        match = re.search(pattern, source, re.I)
        if match:
            return match.group(1), unit
    return "not_applicable", "not_applicable"


def _periods(text: str) -> list[str]:
    return re.findall(r"(?:19|20)\d{2}(?:Q[1-4](?:-[1-4])?)?|(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月", text, re.I)


def _geography(text: str) -> str:
    for token in ("全球", "韩国", "中国", "国内", "我国", "亚太", "欧洲", "香港", "台湾"):
        if token in text:
            return token
    return "not_applicable"


def parse_claim(question: Question, label: str) -> dict[str, Any]:
    text = str(question.options[label])
    value, unit = _number_unit(text)
    periods = _periods(text)
    numeric_values = re.findall(r"\d+(?:\.\d+)?", text)
    measure_values = [value for value in numeric_values if not re.fullmatch(r"(?:19|20)\d{2}", value)]
    return {
        "option": label,
        "text": text,
        "required_doc_ids": list(question.doc_ids),
        "source_institution": _source_institution(text),
        "statement_type": _statement_type(text),
        "period": periods[0] if periods else "unresolved",
        "forecast_horizon": periods[0] if periods and _statement_type(text) == "forecast" else "not_applicable",
        "geography": _geography(text),
        "metric": _metric(text),
        "value": value,
        "unit": unit,
        "numeric_values": numeric_values,
        "measure_values": measure_values,
        "relation": "gt" if any(token in text for token in ("超过", "高于")) else "lt" if any(token in text for token in ("下降", "低于")) else "eq",
        "method": "automatic" if "自动解析" in text else "manual" if "手动解析" in text else "not_applicable",
    }


def _unresolved(label: str, claim: Mapping[str, Any], reason: str) -> TruthOptionResult:
    return TruthOptionResult(
        option=label,
        claim=claim,
        status="unresolved",
        blockers=(reason,),
        reason="research claim not independently closed",
    )


def _metric_terms(metric: str) -> tuple[str, ...]:
    return {
        "global_optical_market_size": ("光通信", "市场规模"),
        "korea_bancassurance_cagr": ("韩国", "银保", "复合增速"),
        "korea_bancassurance_contribution": ("韩国", "银保", "贡献"),
        "china_ict_market_size": ("ICT", "市场规模"),
        "financial_it_innovation_market_size": ("金融信创", "市场规模"),
        "built_in_detection_rules": ("内置", "检测规则"),
        "parsing_rules": ("解析规则",),
        "net_profit": ("净利润",),
        "revenue_yoy": ("营收", "同比"),
        "client_fund_leverage": ("客户资金杠杆",),
        "own_asset_roa": ("自有资产净利率",),
        "data_center_acceleration_market_size": ("数据中心", "半导体", "市场规模"),
        "service_vs_goods_retail_growth": ("服务零售", "商品零售"),
        "bank_fee_income_growth": ("手续费及佣金净收入",),
        "disposable_income_growth": ("居民", "可支配收入", "增速"),
        "insurer_q4_profit_pressure": ("上市险企", "四季度", "利润"),
        "service_consumption_share": ("服务消费", "占比"),
        "new_energy_vehicle_penetration": ("新能源", "渗透率"),
        "catl_market_share": ("宁德时代", "市占率"),
        "ip_licensing_market_share": ("IP", "授权", "市场份额"),
        "lithium_price_resource_pe": ("碳酸锂", "PE"),
        "ai_chip_revenue": ("AI", "芯片", "收入"),
        "ev_sales_yoy": ("电动车", "销量", "同比"),
        "chip_customization_service": ("芯片", "定制服务"),
    }.get(metric, ())


def _extract_numeric_fragment(candidate: EvidenceCandidate, claim: Mapping[str, Any]) -> tuple[str, str, str]:
    terms = _metric_terms(str(claim["metric"]))
    body = compact(candidate.text)
    if terms and not all(compact(term) in body for term in terms):
        return "", "", ""
    period = str(claim["period"])
    if period != "unresolved" and compact(period) not in body:
        return "", "", ""
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*亿美元", "亿美元"),
        (r"(\d+(?:\.\d+)?)\s*亿元", "亿元"),
        (r"(\d+(?:\.\d+)?)\s*万元", "万元"),
        (r"(\d+(?:\.\d+)?)\s*倍", "倍"),
        (r"(\d+(?:\.\d+)?)\s*%", "%"),
        (r"(?:超过|低于|高于|少于)?\s*(\d+)\s*条", "条"),
        (r"形成\s*(\d+)\s*条", "条"),
    ]
    claim_unit = str(claim.get("unit") or "")
    claim_value = str(claim.get("value") or "")
    ranked: list[tuple[int, int, str, str, str]] = []
    for pattern, unit in patterns:
        for match in re.finditer(pattern, candidate.text, re.I):
            start = max(0, match.start() - 160)
            end = min(len(candidate.text), match.end() + 200)
            fragment = candidate.text[start:end]
            fragment_body = compact(fragment)
            if terms and not all(compact(term) in fragment_body for term in terms):
                continue
            actual_value = match.group(1)
            score = 0
            if claim_value not in {"", "not_applicable"} and _decimal(actual_value) == _decimal(claim_value):
                score += 12
            if claim_unit and claim_unit == unit:
                score += 4
            if period != "unresolved" and compact(period) in fragment_body:
                score += 5
            distance = abs(match.start() - candidate.text.find(period)) if period != "unresolved" else match.start()
            ranked.append((score, -distance, fragment, actual_value, unit))
    if not ranked:
        return "", "", ""
    _, _, fragment, actual_value, unit = max(ranked, key=lambda row: (row[0], row[1]))
    return fragment, actual_value, unit


def _qualitative_fragment(candidate: EvidenceCandidate, claim: Mapping[str, Any]) -> tuple[str, str]:
    terms = _metric_terms(str(claim["metric"]))
    body = compact(candidate.text)
    if not terms or not all(compact(term) in body for term in terms):
        return "", ""
    for line in [item.strip() for item in candidate.text.splitlines() if item.strip()]:
        line_body = compact(line)
        if all(compact(term) in line_body for term in terms):
            return line[:500], "present"
    return "", ""


def evaluate_option(
    *,
    repo_root: Path,
    label: str,
    claim: Mapping[str, Any],
    candidates: Sequence[EvidenceCandidate],
) -> TruthOptionResult:
    metric = str(claim["metric"])
    if metric == "unresolved":
        return _unresolved(label, claim, "research_metric_unresolved")
    claim_value = str(claim["value"])
    claim_unit = str(claim["unit"])
    measure_values = list(claim.get("measure_values") or [])
    if len(measure_values) > 1:
        return _unresolved(label, claim, "multi_value_claim_requires_structured_range_parser")
    if metric == "parsing_rules" and claim.get("method") in {"manual", "automatic"}:
        for candidate in candidates:
            fragment, _ = _qualitative_fragment(candidate, claim)
            if not fragment:
                continue
            actual_method = "automatic" if "自动" in fragment else "manual" if "手动" in fragment else "unresolved"
            if actual_method == "unresolved":
                continue
            status = "supported" if actual_method == claim["method"] else "contradicted"
            source = TruthSource.from_candidate(
                repo_root=repo_root,
                candidate=candidate,
                relevance_fields=("document", "metric", "method"),
            )
            provenance = provenance_for_fragments(
                source=source,
                fields={"method": (actual_method, fragment, "research_parsing_method_v1")},
            )
            return TruthOptionResult(
                option=label,
                claim=claim,
                status=status,
                sources=(source,),
                provenance=provenance,
                binding={"required_doc": "match", "metric": "match", "method": "match" if status == "supported" else "conflict"},
                reason="automatic/manual method directly distinguished in the same statement",
            )
        return _unresolved(label, claim, "parsing_method_statement_not_found")
    def numeric_candidate_priority(candidate: EvidenceCandidate) -> tuple[int, int, int]:
        body = compact(candidate.text)
        exact_value_unit = 0
        if claim_value not in {"", "not_applicable"}:
            exact_value_unit = int(bool(re.search(rf"(?<!\d){re.escape(claim_value)}\s*{re.escape(claim_unit)}", candidate.text)))
        institution = str(claim.get("source_institution") or "unresolved")
        institution_hit = int(institution != "unresolved" and compact(institution) in body)
        period = str(claim.get("period") or "unresolved")
        period_hit = int(period != "unresolved" and compact(period) in body)
        return exact_value_unit, institution_hit, period_hit

    for candidate in sorted(candidates, key=numeric_candidate_priority, reverse=True):
        fragment, actual_value, actual_unit = _extract_numeric_fragment(candidate, claim)
        if not fragment:
            continue
        source = TruthSource.from_candidate(
            repo_root=repo_root,
            candidate=candidate,
            relevance_fields=("source_institution", "document", "statement_type", "period", "geography", "metric", "value", "unit"),
        )
        expected = _decimal(claim_value)
        actual = _decimal(actual_value)
        if expected is None or actual is None:
            continue
        unit_match = claim_unit == actual_unit or claim_unit == "not_applicable"
        relation = str(claim.get("relation") or "eq")
        fragment_compact = compact(fragment)
        direct_relation_match = (
            (relation == "gt" and any(token + compact(claim_value) in fragment_compact for token in ("超过", "高于")))
            or (relation == "lt" and any(token + compact(claim_value) in fragment_compact for token in ("低于", "少于")))
        )
        if direct_relation_match:
            value_match = True
        elif relation == "gt":
            value_match = actual > expected
        elif relation == "lt":
            value_match = actual < expected
        else:
            value_match = abs(expected - actual) <= Decimal("0.01")
        claim_statement_type = str(claim.get("statement_type") or "historical_fact")
        actual_statement_type = _statement_type(fragment)
        statement_type_match = claim_statement_type == actual_statement_type
        claim_institution = str(claim.get("source_institution") or "unresolved")
        actual_institution = _source_institution(fragment)
        institution_match = claim_institution == "unresolved" or claim_institution == actual_institution
        status = "supported" if unit_match and value_match and statement_type_match and institution_match else "contradicted"
        provenance = provenance_for_fragments(
            source=source,
            fields={
                "metric_value": (actual_value, fragment, f"research_{metric}_period_value_v1"),
            },
        )
        return TruthOptionResult(
            option=label,
            claim=claim,
            status=status,
            sources=(source,),
            provenance=provenance,
            binding={
                "required_doc": "match",
                "source_institution": "match" if institution_match else "conflict",
                "statement_type": "match" if statement_type_match else "conflict",
                "period": "match",
                "geography": "match_or_not_required",
                "metric": "match",
                "value": "match" if value_match else "conflict",
                "unit": "match" if unit_match else "conflict",
            },
            reason="same-document institution, statement type, metric, period, value and unit were independently compared",
        )
    if metric == "parsing_rules" and claim.get("method") in {"manual", "automatic"}:
        for candidate in candidates:
            fragment, _ = _qualitative_fragment(candidate, claim)
            if not fragment:
                continue
            actual_method = "automatic" if "自动" in fragment else "manual" if "手动" in fragment else "unresolved"
            if actual_method == "unresolved":
                continue
            status = "supported" if actual_method == claim["method"] else "contradicted"
            source = TruthSource.from_candidate(
                repo_root=repo_root,
                candidate=candidate,
                relevance_fields=("document", "metric", "method"),
            )
            provenance = provenance_for_fragments(
                source=source,
                fields={"method": (actual_method, fragment, "research_parsing_method_v1")},
            )
            return TruthOptionResult(
                option=label,
                claim=claim,
                status=status,
                sources=(source,),
                provenance=provenance,
                binding={"required_doc": "match", "metric": "match", "method": "match" if status == "supported" else "conflict"},
                reason="automatic/manual method directly distinguished in the same statement",
            )
    if claim_value == "not_applicable":
        if claim.get("relation") in {"gt", "lt"} and claim.get("measure_values"):
            return _unresolved(label, claim, "relation_value_not_parsed")
        claim_text = str(claim.get("text") or "")
        directional_tokens = [token for token in ("负增长", "承压", "下降", "提高", "提升", "回升", "低位", "自动解析", "手动解析") if token in claim_text]
        for candidate in candidates:
            fragment, _ = _qualitative_fragment(candidate, claim)
            if not fragment:
                continue
            if directional_tokens and not all(token in fragment for token in directional_tokens):
                continue
            if claim.get("statement_type") == "forecast" and not any(token in fragment for token in ("预计", "预测", "有望", "将达到", "将达")):
                continue
            source = TruthSource.from_candidate(
                repo_root=repo_root,
                candidate=candidate,
                relevance_fields=("source_institution", "document", "statement_type", "period", "geography", "metric"),
            )
            provenance = provenance_for_fragments(
                source=source,
                fields={"qualitative_metric": (metric, fragment, f"research_{metric}_direct_statement_v1")},
            )
            return TruthOptionResult(
                option=label,
                claim=claim,
                status="supported",
                sources=(source,),
                provenance=provenance,
                binding={"required_doc": "match", "statement_type": "match_or_not_explicit", "period": "match_or_not_required", "geography": "match_or_not_required", "metric": "match"},
                reason="qualitative metric statement directly reproduced in a declared report",
            )
    return _unresolved(label, claim, "missing_source_attribution_or_period_value")


def evaluate(
    *,
    repo_root: Path,
    question: Question,
    candidates: Sequence[EvidenceCandidate],
) -> TruthQuestionResult:
    option_results: dict[str, TruthOptionResult] = {}
    answer_format = question.answer_contract.answer_format if question.answer_contract else question.answer_format
    if answer_format == "tf":
        for label in question.options:
            claim = {
                "option": label,
                "text": question.options[label],
                "proposition_text": question.text,
                "required_doc_ids": list(question.doc_ids),
                "metric": "compound_tf_research_proposition",
            }
            option_results[label] = _unresolved(label, claim, "research_tf_compound_parser_not_implemented")
        return result_from_options(
            question=question,
            option_results=option_results,
            task_type="true_false",
            lane="RES-T",
            implementation_status="NOT_IMPLEMENTED_COMPOUND_TF",
            capability=CAPABILITY,
        )
    for label in question.options:
        claim = parse_claim(question, label)
        scoped = candidates_for_docs(candidates, claim["required_doc_ids"])
        option_results[label] = (
            evaluate_option(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            if scoped
            else _unresolved(label, claim, "missing_required_doc")
        )
    lane = "RES-A" if any(row.claim.get("source_institution") != "unresolved" for row in option_results.values()) else "RES-X"
    return result_from_options(
        question=question,
        option_results=option_results,
        task_type="research_fact_forecast",
        lane=lane,
        implementation_status="IMPLEMENTED_PARTIAL_DOMAIN_COVERAGE",
        capability=CAPABILITY,
    )
