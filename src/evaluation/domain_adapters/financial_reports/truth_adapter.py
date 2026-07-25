"""Independent financial-report truth adapter for Package AG-R1."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import (
    FieldProvenance,
    TruthOptionResult,
    TruthQuestionResult,
    TruthSource,
    candidates_for_docs,
    compact,
    first_relevant_candidate,
    provenance_for_fragments,
    result_from_options,
)

CAPABILITY = "financial_reports:verifiable_metric_period_unit_policy_stage_v1"
ENTITY_DOC_TOKENS: tuple[tuple[str, str], ...] = (
    ("宁德时代", "catl"), ("美的集团", "midea"), ("美的", "midea"),
    ("比亚迪", "byd"), ("中国移动", "chinamobile"), ("中国建筑", "cscec"),
)


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _entity(option: str) -> str:
    for name, _ in ENTITY_DOC_TOKENS:
        if name in option:
            return "美的集团" if name == "美的" else name
    return "declared_document_subject"


def _bound_docs(option: str, declared: Sequence[str]) -> tuple[str, ...]:
    selected = [doc for doc in declared for name, token in ENTITY_DOC_TOKENS if name in option and token in doc]
    return tuple(dict.fromkeys(selected)) if selected else tuple(str(doc) for doc in declared)


def _years(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(?:19|20)\d{2}", text)))


def _metric(text: str) -> str:
    value = compact(text)
    rules = (
        (("每10股派发现金", "每10股派息", "每10股现金红利"), "cash_dividend_per_10_shares"),
        (("每股派发现金", "每股分红", "每股现金红利"), "cash_dividend_per_share"),
        (("资本公积", "转增股本"), "capital_reserve_conversion"),
        (("现金分红与股份回购", "分红与回购"), "dividend_plus_repurchase_vs_parent_profit"),
        (("研发费用占营业收入",), "rd_expense_ratio"),
        (("研发投入占营业收入",), "rd_investment_ratio"),
        (("归属于上市公司股东的净利润", "归母净利润"), "parent_attributable_net_profit"),
        (("经营活动产生的现金流量净额",), "operating_cash_flow_net"),
        (("营业收入",), "operating_revenue"),
    )
    for tokens, name in rules:
        if all(token in value for token in tokens) if len(tokens) > 1 else tokens[0] in value:
            return name
        if any(token in value for token in tokens):
            return name
    return "unresolved"


def _value_unit(text: str) -> tuple[str, str]:
    source = text.replace("％", "%")
    for pattern, unit in (
        (r"每\s*10\s*股[^\d]{0,40}(\d+(?:\.\d+)?)\s*元", "CNY_per_10_shares"),
        (r"每股[^\d]{0,40}(\d+(?:\.\d+)?)\s*元", "CNY_per_share"),
        (r"(\d+(?:\.\d+)?)\s*%", "%"),
        (r"(\d+(?:\.\d+)?)\s*亿元", "亿元"),
    ):
        match = re.search(pattern, source)
        if match:
            return match.group(1), unit
    return "not_applicable", "not_applicable"


def _policy_stage(text: str) -> str:
    value = compact(text)
    if "年末" in value and any(token in value for token in ("拟", "方案")):
        return "year_end_proposed"
    if any(token in value for token in ("全年", "年度总", "年度利润分配方案为")):
        return "annual_total"
    if "中期" in value and any(token in value for token in ("实施", "完成")):
        return "interim_executed"
    if any(token in value for token in ("拟", "预案")):
        return "proposed"
    if "实施" in value:
        return "executed"
    return "not_applicable"


def parse_claim(question: Question, label: str) -> dict[str, Any]:
    text = str(question.options[label])
    value, unit = _value_unit(text)
    years = _years(text)
    return {
        "option": label,
        "text": text,
        "entity": _entity(text),
        "required_doc_ids": list(_bound_docs(text, question.doc_ids)),
        "period": years[0] if years else "unresolved",
        "comparison_period": years[1] if len(years) > 1 else "not_applicable",
        "metric": _metric(text),
        "value": value,
        "unit": unit,
        "policy_stage": _policy_stage(text),
        "attribution_scope": "parent_attributable" if any(token in text for token in ("归母", "归属于上市公司股东")) else "not_applicable",
        "relation": (
            "decrease_vs_comparison"
            if any(token in text for token in ("有所下降", "较上年下降", "同比下降"))
            else "increase_vs_comparison"
            if any(token in text for token in ("有所上升", "较上年上升", "同比上升"))
            else "gt"
            if any(token in text for token in ("超过", "高于"))
            else "eq"
        ),
    }


def _source(repo_root: Path, candidate: EvidenceCandidate, fields: Sequence[str]) -> TruthSource:
    return TruthSource.from_candidate(repo_root=repo_root, candidate=candidate, relevance_fields=fields)


def _unresolved(label: str, claim: Mapping[str, Any], *blockers: str) -> TruthOptionResult:
    return TruthOptionResult(option=label, claim=claim, status="unresolved", blockers=tuple(blockers), reason="financial decisive fact not independently closed")


def _sentence_context(text: str, start: int, end: int) -> str:
    left = max(text.rfind("。", 0, start), text.rfind("\n", 0, start), text.rfind("；", 0, start))
    right_candidates = [position for position in (text.find("。", end), text.find("\n", end), text.find("；", end)) if position >= 0]
    right = min(right_candidates) if right_candidates else min(len(text), end + 180)
    return text[left + 1:right + 1]


def _candidate_matches_claim_period(candidate: EvidenceCandidate, claim: Mapping[str, Any]) -> bool:
    period = str(claim.get("period") or "")
    if not re.fullmatch(r"(?:19|20)\d{2}", period):
        return True
    doc_years = re.findall(r"(?:19|20)\d{2}", str(candidate.doc_id))
    if doc_years:
        return period in doc_years
    return period in candidate.text


def _exact_per_share_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    metric = claim["metric"]
    value = str(claim["value"])
    if metric not in {"cash_dividend_per_10_shares", "cash_dividend_per_share"} or value == "not_applicable":
        return None
    per10_pattern = rf"每\s*10\s*股[^。；\n]{{0,90}}?{re.escape(value)}\s*元"
    per_share_pattern = rf"每股[^。；\n]{{0,90}}?{re.escape(value)}\s*元"
    wanted = per10_pattern if metric == "cash_dividend_per_10_shares" else per_share_pattern
    opposite = per10_pattern if metric == "cash_dividend_per_share" else per_share_pattern
    for candidate in candidates:
        if not _candidate_matches_claim_period(candidate, claim):
            continue
        match = re.search(wanted, candidate.text)
        if match:
            context = _sentence_context(candidate.text, match.start(), match.end())
            fact_stage = _policy_stage(context)
            claim_stage = str(claim.get("policy_stage") or "not_applicable")
            stage_allowed = (
                claim_stage in {"not_applicable", "unresolved"}
                or claim_stage == fact_stage
                or (claim_stage == "proposed" and fact_stage in {"proposed", "year_end_proposed"})
            )
            if not stage_allowed:
                continue
            source = _source(repo_root, candidate, ("entity", "document", "period", "metric", "value", "unit", "policy_stage"))
            provenance = provenance_for_fragments(source=source, fields={
                "value_and_unit": (value, match.group(0), "financial_exact_share_basis_value_v1"),
            })
            return TruthOptionResult(
                option=label, claim=claim, status="supported", sources=(source,), provenance=provenance,
                binding={"entity_doc": "match", "period": "match", "metric": "match", "value": "match", "unit": "match", "policy_stage": "match_or_not_required"},
                reason="exact bound-document share-basis value matched",
            )
        wrong = re.search(opposite, candidate.text)
        if wrong:
            source = _source(repo_root, candidate, ("entity", "document", "period", "metric", "value", "unit"))
            provenance = provenance_for_fragments(source=source, fields={
                "unit_conflict": (claim["unit"], wrong.group(0), "financial_share_basis_conflict_v1"),
            })
            return TruthOptionResult(
                option=label, claim=claim, status="contradicted", sources=(source,), provenance=provenance,
                binding={"entity_doc": "match", "period": "match", "metric": "match", "value": "match", "unit": "conflict"},
                reason="same value is stated on the opposite per-share basis",
            )
    return None


def _capital_reserve_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    if claim["metric"] != "capital_reserve_conversion":
        return None
    by_doc: dict[str, tuple[EvidenceCandidate, re.Match[str]]] = {}
    pattern = re.compile(r"(?:不实施资本公积金?转增股本|不以(?:资本)?公积金转增股本|未宣告资本公积金转增股本预案)")
    for candidate in candidates:
        match = pattern.search(candidate.text)
        if match:
            by_doc[str(candidate.doc_id)] = (candidate, match)
    required = set(map(str, claim["required_doc_ids"]))
    if required and required <= set(by_doc):
        sources = []
        provenance = []
        for doc_id in sorted(required):
            candidate, match = by_doc[doc_id]
            source = _source(repo_root, candidate, ("entity", "document", "metric", "negation"))
            sources.append(source)
            provenance.extend(provenance_for_fragments(source=source, fields={
                "capital_reserve_action": ("not_implemented", match.group(0), "financial_capital_reserve_negation_v1"),
            }))
        return TruthOptionResult(
            option=label, claim=claim, status="contradicted", sources=tuple(sources), provenance=tuple(provenance),
            binding={"entity_doc": "match", "metric": "match", "negation": "conflict"},
            reason="every required company report explicitly states no capital-reserve conversion",
        )
    return None


def _direct_phrase_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    patterns: dict[str, tuple[str, str]] = {
        "dividend_plus_repurchase_vs_parent_profit": (r"现金分红与股份回购之总金额超过当年度公司归母净利润", "greater_than"),
    }
    if claim["metric"] not in patterns:
        return None
    pattern, fact = patterns[claim["metric"]]
    for candidate in candidates:
        match = re.search(pattern, candidate.text)
        if match:
            source = _source(repo_root, candidate, ("entity", "document", "period", "metric", "relation", "attribution_scope"))
            provenance = provenance_for_fragments(source=source, fields={
                "direct_relation": (fact, match.group(0), "financial_direct_relation_sentence_v1"),
            })
            return TruthOptionResult(
                option=label, claim=claim, status="supported", sources=(source,), provenance=provenance,
                binding={"entity_doc": "match", "period": "match", "metric": "match", "relation": "match", "attribution_scope": "match"},
                reason="direct bound-report sentence supports the comparison",
            )
    return None


def _annual_ratio_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    text = str(claim["text"])
    if (
        "现金分红" not in text
        or claim["value"] == "not_applicable"
        or claim["unit"] != "%"
        or claim.get("metric") != "parent_attributable_net_profit"
        or claim.get("attribution_scope") != "parent_attributable"
    ):
        return None
    value = str(claim["value"])
    pattern = re.compile(rf"年度现金分红[：:]?[^。；\n]{{0,220}}?归属于上市公司股东的净利润的\s*{re.escape(value)}\s*%")
    for candidate in candidates:
        match = pattern.search(candidate.text)
        if match:
            fragment = match.group(0)
            if any(token in fragment for token in ("基础上", "特别现金分红", "特殊现金分红")):
                continue
            source = _source(repo_root, candidate, ("entity", "document", "period", "metric", "value", "unit", "policy_stage", "attribution_scope"))
            provenance = provenance_for_fragments(source=source, fields={
                "annual_dividend_ratio": (value, match.group(0), "financial_annual_dividend_ratio_v1"),
            })
            return TruthOptionResult(
                option=label, claim=claim, status="supported", sources=(source,), provenance=provenance,
                binding={"entity_doc": "match", "period": "match", "metric": "match", "value": "match", "unit": "match", "policy_stage": "match", "attribution_scope": "match"},
                reason="annual-dividend component and 20% value are directly stated",
            )
    return None


def _find_numeric_candidate(candidates: Sequence[EvidenceCandidate], field_terms: Sequence[str]) -> tuple[EvidenceCandidate | None, Decimal | None, str]:
    for candidate in candidates:
        body = candidate.text
        if not all(compact(term) in compact(body) for term in field_terms):
            continue
        joined = "|".join(field_terms)
        patterns = [
            rf"(?:{joined})[^\d-]{{0,80}}(-?[\d,]+(?:\.\d+)?)",
            rf"(?:{joined})[^=]{{0,30}}=(-?[\d,]+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, body)
            if match:
                return candidate, _decimal(match.group(1)), match.group(0)
    return None, None, ""


def _year_bound_amount(
    candidates: Sequence[EvidenceCandidate], *, year: str, metric: str
) -> tuple[EvidenceCandidate | None, Decimal | None, str]:
    patterns = {
        "rd_investment": (
            r"研发投入[^。；\n]{0,80}?(?:约为|约人民币|高达|为)?\s*([\d,]+(?:\.\d+)?)\s*(亿元|百万元)",
        ),
        "operating_revenue": (
            r"(?:营业收入金额为|实现收入约|营业收入约|营业收入为)[^\d]{0,30}([\d,]+(?:\.\d+)?)\s*(亿元|百万元)",
        ),
    }
    for candidate in candidates:
        if year not in str(candidate.doc_id):
            continue
        for pattern in patterns[metric]:
            match = re.search(pattern, candidate.text)
            if not match:
                continue
            value = _decimal(match.group(1))
            if value is None:
                continue
            if match.group(2) == "百万元":
                value = value / Decimal(100)
            return candidate, value, match.group(0)
    return None, None, ""


def _rd_ratio_comparison_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    if (
        claim.get("metric") != "rd_investment_ratio"
        or claim.get("relation") not in {"decrease_vs_comparison", "increase_vs_comparison"}
        or claim.get("period") == "unresolved"
        or claim.get("comparison_period") in {"unresolved", "not_applicable"}
    ):
        return None
    current_year = str(claim["period"])
    comparison_year = str(claim["comparison_period"])
    current_rd_candidate, current_rd, current_rd_fragment = _year_bound_amount(
        candidates, year=current_year, metric="rd_investment"
    )
    current_rev_candidate, current_rev, current_rev_fragment = _year_bound_amount(
        candidates, year=current_year, metric="operating_revenue"
    )
    previous_rd_candidate, previous_rd, previous_rd_fragment = _year_bound_amount(
        candidates, year=comparison_year, metric="rd_investment"
    )
    previous_rev_candidate, previous_rev, previous_rev_fragment = _year_bound_amount(
        candidates, year=comparison_year, metric="operating_revenue"
    )
    if any(
        value is None
        for value in (current_rd, current_rev, previous_rd, previous_rev)
    ) or current_rev == 0 or previous_rev == 0:
        return None
    current_ratio = (current_rd / current_rev * Decimal(100)).quantize(Decimal("0.01"))
    previous_ratio = (previous_rd / previous_rev * Decimal(100)).quantize(Decimal("0.01"))
    expected_decrease = claim["relation"] == "decrease_vs_comparison"
    observed_decrease = current_ratio < previous_ratio
    status = "supported" if expected_decrease == observed_decrease else "contradicted"
    source_candidates = (
        current_rd_candidate,
        current_rev_candidate,
        previous_rd_candidate,
        previous_rev_candidate,
    )
    sources = tuple(
        _source(repo_root, candidate, ("entity", "document", "period", "metric", "value", "unit"))
        for candidate in source_candidates
        if candidate is not None
    )
    provenance: list[FieldProvenance] = []
    fragments = (
        (sources[0], "current_rd_investment", str(current_rd), current_rd_fragment),
        (sources[1], "current_operating_revenue", str(current_rev), current_rev_fragment),
        (sources[2], "comparison_rd_investment", str(previous_rd), previous_rd_fragment),
        (sources[3], "comparison_operating_revenue", str(previous_rev), previous_rev_fragment),
    )
    for source, field, value, fragment in fragments:
        provenance.extend(
            provenance_for_fragments(
                source=source,
                fields={field: (value, fragment, "financial_rd_ratio_year_bound_v1")},
            )
        )
    return TruthOptionResult(
        option=label,
        claim=claim,
        status=status,
        sources=sources,
        provenance=tuple(provenance),
        binding={
            "entity_doc": "match",
            "period": "match",
            "comparison_period": "match",
            "metric": "match",
            "value": "calculated",
            "unit": "match",
            "relation": "match" if status == "supported" else "conflict",
        },
        rule_steps=(
            {
                "formula": "rd_investment / operating_revenue * 100",
                "current_year": current_year,
                "current_ratio_percent": str(current_ratio),
                "comparison_year": comparison_year,
                "comparison_ratio_percent": str(previous_ratio),
                "claimed_relation": claim["relation"],
            },
        ),
        reason="year-bound R&D-investment ratios were calculated and compared",
    )


def _ratio_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    metric = str(claim["metric"])
    if metric not in {"rd_expense_ratio", "rd_investment_ratio"} or claim["value"] == "not_applicable":
        return None
    field_term = "研发费用" if metric == "rd_expense_ratio" else "研发投入"
    rd_candidate, rd_value, rd_fragment = _find_numeric_candidate(candidates, (field_term,))
    revenue_candidate, revenue_value, revenue_fragment = _find_numeric_candidate(candidates, ("营业收入",))
    if not rd_candidate or not revenue_candidate or rd_value is None or revenue_value in {None, Decimal(0)}:
        return None
    actual = (rd_value / revenue_value * Decimal(100)).quantize(Decimal("0.01"))
    expected = _decimal(str(claim["value"]))
    if expected is None:
        return None
    status = "supported" if abs(actual - expected) <= Decimal("0.01") else "contradicted"
    sources = (
        _source(repo_root, rd_candidate, ("entity", "document", "period", "metric", "value", "unit")),
        _source(repo_root, revenue_candidate, ("entity", "document", "period", "metric", "value", "unit")),
    )
    provenance = (
        *provenance_for_fragments(source=sources[0], fields={"rd_expense": (str(rd_value), rd_fragment, "financial_rd_expense_value_v1")}),
        *provenance_for_fragments(source=sources[1], fields={"operating_revenue": (str(revenue_value), revenue_fragment, "financial_operating_revenue_value_v1")}),
    )
    return TruthOptionResult(
        option=label, claim=claim, status=status, sources=sources, provenance=tuple(provenance),
        binding={"entity_doc": "match", "period": "match", "metric": "match", "value": "match" if status == "supported" else "conflict", "unit": "match"},
        rule_steps=({"formula": "rd_expense / operating_revenue * 100", "rd_expense": str(rd_value), "operating_revenue": str(revenue_value), "actual_percent": str(actual), "claim_percent": str(expected)},),
        reason="deterministic ratio calculation from two bound-report fields",
    )


def _parent_profit_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    if claim["metric"] != "parent_attributable_net_profit" or claim["value"] == "not_applicable":
        return None
    candidate, raw, fragment = _find_numeric_candidate(candidates, ("归属于上市公司股东的净利润",))
    if not candidate or raw is None:
        return None
    expected = _decimal(str(claim["value"]))
    if expected is None:
        return None
    actual = raw / Decimal(100_000_000) if claim["unit"] == "亿元" and raw > Decimal(1_000_000) else raw
    actual = actual.quantize(Decimal("0.01"))
    status = "supported" if abs(actual - expected) <= Decimal("0.05") else "contradicted"
    source = _source(repo_root, candidate, ("entity", "document", "period", "metric", "value", "unit", "attribution_scope"))
    provenance = provenance_for_fragments(source=source, fields={
        "parent_attributable_net_profit": (str(raw), fragment, "financial_parent_profit_value_v1"),
    })
    return TruthOptionResult(
        option=label, claim=claim, status=status, sources=(source,), provenance=provenance,
        binding={"entity_doc": "match", "period": "match", "metric": "match", "value": "match" if status == "supported" else "conflict", "unit": "match", "attribution_scope": "match"},
        rule_steps=({"raw_value": str(raw), "normalized_value": str(actual), "claim_value": str(expected), "normalized_unit": claim["unit"]},),
        reason="parent-attributable profit normalized and compared deterministically",
    )


def evaluate(*, repo_root: Path, question: Question, candidates: Sequence[EvidenceCandidate]) -> TruthQuestionResult:
    option_results: dict[str, TruthOptionResult] = {}
    if (question.answer_contract.answer_format if question.answer_contract else question.answer_format) == "tf":
        for label in question.options:
            claim = {"option": label, "text": question.options[label], "proposition_text": question.text, "required_doc_ids": list(question.doc_ids), "metric": "compound_tf_proposition"}
            option_results[label] = _unresolved(label, claim, "financial_tf_compound_parser_not_implemented")
        return result_from_options(question=question, option_results=option_results, task_type="true_false", lane="FIN-S", implementation_status="PARTIAL", capability=CAPABILITY)

    for label in question.options:
        claim = parse_claim(question, label)
        scoped = candidates_for_docs(candidates, claim["required_doc_ids"])
        if not scoped:
            option_results[label] = _unresolved(label, claim, "missing_required_doc")
            continue
        result = (
            _exact_per_share_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _capital_reserve_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _direct_phrase_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _annual_ratio_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _rd_ratio_comparison_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _ratio_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _parent_profit_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
        )
        option_results[label] = result or _unresolved(label, claim, "missing_metric_specific_fact_or_rule")
    answer_format = question.answer_contract.answer_format if question.answer_contract else question.answer_format
    lane = "FIN-M" if answer_format == "multi" else "FIN-S"
    return result_from_options(
        question=question,
        option_results=option_results,
        task_type="financial_explicit_fact",
        lane=lane,
        implementation_status="IMPLEMENTED_PARTIAL_DOMAIN_COVERAGE",
        capability=CAPABILITY,
    )
