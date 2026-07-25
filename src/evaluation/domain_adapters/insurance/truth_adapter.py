"""Independent insurance clause truth adapter for Package AG-R1."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import (
    TruthOptionResult, TruthQuestionResult, TruthSource,
    candidates_for_docs, compact, provenance_for_fragments, result_from_options,
)

CAPABILITY = "insurance:product_clause_condition_graph_v1"
PRODUCT_DOCS: tuple[tuple[str, str], ...] = (
    ("平安智盈金生", "1"), ("国寿增益宝", "2"), ("众安白血病医疗险", "3"),
    ("平安安佑福重疾险", "4"), ("平安e生保", "5"), ("e生保", "5"),
    ("太保团体百万医疗", "6"), ("平安预防接种意外险", "7"),
    ("众安营运交通意外险", "8"), ("众安家财险", "12"),
    ("众安食责险", "13"), ("国寿鑫享添盈", "15"), ("平安富鸿金生", "16"),
)

ATOM_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("designated_pharmacy", ("指定药店", "指定医疗机构或药店")),
    ("prescription_review", ("处方审核",)),
    ("direct_settlement", ("直接结算", "药店直付")),
    ("policy_loan", ("保单贷款",)),
    ("loan_80_percent", ("80%", "百分之八十")),
    ("personal_pension_no_loan", ("个人养老金", "不得申请保单贷款")),
    ("general_medical", ("一般医疗保险金", "一般医疗")),
    ("accident_disability", ("意外伤残", "伤残保险金")),
    ("deafness", ("双耳失聪",)),
    ("drunk_driving", ("酒后驾驶",)),
    ("hiv_exclusion", ("感染艾滋病病毒",)),
    ("unqualified_vaccination", ("不具有接种条件",)),
    ("expired_food", ("超过保质期", "保质期")),
    ("specific_drug", ("特定药品费用", "院外恶性肿瘤特定药品")),
)


def _product_doc(text: str, declared: Sequence[str]) -> tuple[str, ...]:
    selected = [doc for name, doc in PRODUCT_DOCS if name in text]
    selected = [doc for doc in selected if doc in set(map(str, declared))]
    return tuple(dict.fromkeys(selected)) if selected else tuple(str(doc) for doc in declared)


def _claim_atoms(text: str) -> list[str]:
    value = compact(text)
    atoms = []
    for atom, phrases in ATOM_PATTERNS:
        if any(compact(phrase) in value for phrase in phrases):
            atoms.append(atom)
    return atoms


def parse_claim(question: Question, label: str) -> dict[str, Any]:
    text = str(question.options[label])
    return {
        "option": label,
        "text": text,
        "product": next((name for name, _ in PRODUCT_DOCS if name in text), "unresolved"),
        "required_doc_ids": list(_product_doc(text, question.doc_ids)),
        "atoms": _claim_atoms(text),
        "negation": any(token in text for token in ("不涵盖", "不赔", "不允许", "无论")),
        "universal": any(token in text for token in ("所有", "无论何种")),
        "condition": "present" if any(token in text for token in ("若", "且", "需", "因", "假设")) else "not_applicable",
        "exception": "present" if any(token in text for token in ("除外", "非", "仅")) else "not_applicable",
    }


def _unresolved(label: str, claim: Mapping[str, Any], reason: str) -> TruthOptionResult:
    return TruthOptionResult(option=label, claim=claim, status="unresolved", blockers=(reason,), reason="insurance clause truth not independently closed")


def _atom_fragment(atom: str, candidate: EvidenceCandidate) -> str:
    text = candidate.text
    patterns = {
        "designated_pharmacy": [r"[^。\n]{0,100}(?:指定医疗机构或药店|指定药店)[^。\n]{0,160}"],
        "prescription_review": [r"[^。\n]{0,100}处方审核[^。\n]{0,160}"],
        "direct_settlement": [r"[^。\n]{0,100}(?:直接结算|药店直付)[^。\n]{0,160}"],
        "policy_loan": [r"[^。\n]{0,100}保单贷款[^。\n]{0,180}"],
        "loan_80_percent": [r"[^。\n]{0,100}(?:80%|百分之八十)[^。\n]{0,120}"],
        "personal_pension_no_loan": [r"[^。\n]{0,120}个人养老金[^。\n]{0,220}(?:不得|不允许)[^。\n]{0,80}保单贷款"],
        "general_medical": [r"[^。\n]{0,100}(?:一般医疗保险金|一般医疗)[^。\n]{0,180}"],
        "accident_disability": [r"[^。\n]{0,100}(?:意外伤残|伤残保险金)[^。\n]{0,180}"],
        "deafness": [r"[^。\n]{0,100}双耳失聪[^。\n]{0,180}"],
        "drunk_driving": [r"[^。\n]{0,100}酒后驾驶[^。\n]{0,180}"],
        "hiv_exclusion": [r"[^。\n]{0,100}感染艾滋病病毒[^。\n]{0,220}"],
        "unqualified_vaccination": [r"[^。\n]{0,100}不具有接种条件[^。\n]{0,180}"],
        "expired_food": [r"[^。\n]{0,100}(?:超过保质期|保质期)[^。\n]{0,180}"],
        "specific_drug": [r"[^。\n]{0,100}(?:特定药品费用|院外恶性肿瘤特定药品)[^。\n]{0,220}"],
    }
    for pattern in patterns.get(atom, []):
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _to_wan(value: str, unit: str) -> Decimal | None:
    amount = _decimal(value)
    if amount is None:
        return None
    return amount if unit == "万" else amount / Decimal("10000") if unit == "元" else None


def _find_fragment(
    candidates: Sequence[EvidenceCandidate], doc_id: str, patterns: Sequence[str]
) -> tuple[EvidenceCandidate | None, str]:
    for candidate in candidates:
        if str(candidate.doc_id) != str(doc_id):
            continue
        for pattern in patterns:
            match = re.search(pattern, candidate.text, re.I | re.S)
            if match:
                return candidate, match.group(0)
    return None, ""


def _formula_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], status: str,
    proofs: Sequence[tuple[EvidenceCandidate, str, str]], rule_steps: Sequence[Mapping[str, Any]],
    reason: str,
) -> TruthOptionResult:
    sources: list[TruthSource] = []
    provenance = []
    for candidate, field, fragment in proofs:
        source = TruthSource.from_candidate(
            repo_root=repo_root,
            candidate=candidate,
            relevance_fields=("product", "document", "formula", "variable", "condition", "exception", "unit"),
        )
        sources.append(source)
        provenance.extend(
            provenance_for_fragments(
                source=source,
                fields={field: (True, fragment, f"insurance_{field}_formula_clause_v1")},
            )
        )
    return TruthOptionResult(
        option=label,
        claim=claim,
        status=status,
        sources=tuple(sources),
        provenance=tuple(provenance),
        binding={
            "product_doc": "match",
            "formula": "match",
            "variables": "match",
            "condition": "match_or_not_required",
            "exception": "match_or_not_required",
            "unit": "match" if status == "supported" else "conflict_or_value_mismatch",
        },
        rule_steps=tuple(dict(row) for row in rule_steps),
        reason=reason,
    )


def _rank_option_matches(text: str, actual: Mapping[str, Decimal]) -> bool:
    items = list(re.finditer(r"([^><=，]+?)\((\d+(?:\.\d+)?)(万|元)\)", text))
    if not items:
        return False
    parsed: list[tuple[str, Decimal]] = []
    for match in items:
        name = match.group(1).strip()
        amount = _to_wan(match.group(2), match.group(3))
        if amount is None or name not in actual or amount != actual[name]:
            return False
        parsed.append((name, amount))
    for left, right in zip(items, items[1:]):
        operator_text = text[left.end():right.start()]
        left_value = actual[left.group(1).strip()]
        right_value = actual[right.group(1).strip()]
        if ">" in operator_text and not left_value > right_value:
            return False
        if "=" in operator_text and not left_value == right_value:
            return False
    return True


def _death_benefit_results(
    *, repo_root: Path, question: Question, candidates: Sequence[EvidenceCandidate]
) -> dict[str, TruthOptionResult] | None:
    text = question.text
    if "身故保险金金额排序" not in text:
        return None
    premium_match = re.search(r"已交保费均为\s*(\d+(?:\.\d+)?)\s*万元", text)
    cash_match = re.search(r"现金价值均为\s*(\d+(?:\.\d+)?)\s*万元", text)
    ping_account = re.search(r"平安智盈金生[^；。]*?保单账户价值\s*(\d+(?:\.\d+)?)\s*万元", text)
    guo = re.search(r"国寿增益宝[^；。]*?一人[，,]\s*(\d+)岁[^；。]*?基本保额\s*(\d+(?:\.\d+)?)\s*万元[^；。]*?个人账户价值\s*(\d+(?:\.\d+)?)\s*万元", text)
    xin_received = re.search(r"国寿鑫享添盈已领养老年金\s*(\d+(?:\.\d+)?)\s*万元", text)
    fu_received = re.search(r"平安富鸿金生已领养老年金\s*(\d+(?:\.\d+)?)\s*万元", text)
    if not all((premium_match, cash_match, ping_account, guo, xin_received, fu_received)):
        return None
    premium = Decimal(premium_match.group(1)); cash = Decimal(cash_match.group(1))
    age = int(guo.group(1)); basic = Decimal(guo.group(2)); account = Decimal(guo.group(3))
    ratio = Decimal("1.6") if 18 <= age < 41 else Decimal("1.4") if age < 61 else Decimal("1.2")
    actual = {
        "平安智盈金生": Decimal(ping_account.group(1)),
        "国寿增益宝": max(basic * ratio, account),
        "国寿鑫享添盈": max(premium - Decimal(xin_received.group(1)), cash),
        "平安富鸿金生": max(premium - Decimal(fu_received.group(1)), cash),
    }
    requirements = (
        ("1", (r"养老保险金开始领取日之前身故[^。\n]{0,180}保单账户价值",), "ping_death_formula"),
        ("2", (r"身故保险金额为下列两者的较大值", r"年满18周岁[^。\n]{0,160}160%"), "guoshou_death_formula"),
        ("15", (r"身故保险金[\s\S]{0,420}所交保险费[\s\S]{0,180}累计已给付的养老年金[\s\S]{0,180}现金价值",), "xinxiang_death_formula"),
        ("16", (r"累计已交保险费[^。\n]{0,120}累计已给付养老保险金[^。\n]{0,160}现金价值[^。\n]{0,80}较大者",), "fuhong_death_formula"),
    )
    proofs = []
    for doc_id, patterns, field in requirements:
        candidate, fragment = _find_fragment(candidates, doc_id, patterns)
        if candidate is None:
            return None
        proofs.append((candidate, field, fragment))
    steps = ({"formula": "product-specific death benefit", "actual_amounts_wan": {key: str(value) for key, value in actual.items()}},)
    results = {}
    for label in question.options:
        claim = parse_claim(question, label)
        status = "supported" if _rank_option_matches(question.options[label], actual) else "contradicted"
        results[label] = _formula_result(
            repo_root=repo_root, label=label, claim=claim, status=status, proofs=proofs,
            rule_steps=steps, reason="four product death-benefit formulas were independently calculated and ranked",
        )
    return results


def _surrender_results(
    *, repo_root: Path, question: Question, candidates: Sequence[EvidenceCandidate]
) -> dict[str, TruthOptionResult] | None:
    text = question.text
    if "退保所得金额从高到低排序" not in text:
        return None
    ping = re.search(r"平安智盈金生累计所交保费\s*(\d+(?:\.\d+)?)\s*万元，(?:保单账户累计收益\s*(\d+(?:\.\d+)?)\s*万元，)?在第\s*(\d+)\s*个保单年度末退保", text)
    guo = re.search(r"国寿增益宝个人账户价值\s*(\d+(?:\.\d+)?)\s*万元，在第\s*(\d+)\s*个保单年度末退保", text)
    fu = re.search(r"平安富鸿金生现金价值\s*(\d+(?:\.\d+)?)\s*万元", text)
    if not ping or not guo or not fu or ping.group(2) is None:
        return None
    premium = Decimal(ping.group(1)); earnings = Decimal(ping.group(2)); ping_year = int(ping.group(3))
    if ping_year <= 5:
        ratios = {1: Decimal("0.95"), 2: Decimal("0.97"), 3: Decimal("0.99"), 4: Decimal("1"), 5: Decimal("1")}
        ping_value = premium * ratios[ping_year]
    elif ping_year <= 10:
        ping_value = premium + earnings * Decimal("0.75")
    else:
        ping_value = premium + earnings * Decimal("0.90")
    guo_account = Decimal(guo.group(1)); guo_year = int(guo.group(2))
    fee = {1: Decimal("0.04"), 2: Decimal("0.03"), 3: Decimal("0.02"), 4: Decimal("0.01"), 5: Decimal("0.01")}.get(guo_year, Decimal("0"))
    actual = {
        "平安智盈金生": ping_value.quantize(Decimal("0.01")),
        "国寿增益宝": (guo_account * (Decimal("1") - fee)).quantize(Decimal("0.01")),
        "平安富鸿金生": Decimal(fu.group(1)),
    }
    requirements = (
        ("1", (r"第 6 个保单年度至第 10个保单年度[\s\S]{0,420}累计所交保险费[\s\S]{0,180}累计收益[\s\S]{0,100}75%",), "ping_surrender_formula"),
        ("2", (r"现金价值等于个人账户价值扣除相应的退保费用后的余额", r"退保费用比例"), "guoshou_surrender_formula"),
        ("16", (r"申请解除本合同[\s\S]{0,320}退还本合同的现金价值", r"犹豫期后申请解除本合同[\s\S]{0,220}退还本合同的现金价值"), "fuhong_surrender_formula"),
    )
    proofs = []
    for doc_id, patterns, field in requirements:
        candidate, fragment = _find_fragment(candidates, doc_id, patterns)
        if candidate is None:
            return None
        proofs.append((candidate, field, fragment))
    steps = ({"formula": "surrender value by product and policy year", "actual_amounts_wan": {key: str(value) for key, value in actual.items()}, "guoshou_fee_rate": str(fee)},)
    results = {}
    for label in question.options:
        claim = parse_claim(question, label)
        status = "supported" if _rank_option_matches(question.options[label], actual) else "contradicted"
        results[label] = _formula_result(
            repo_root=repo_root, label=label, claim=claim, status=status, proofs=proofs,
            rule_steps=steps, reason="policy-year surrender formulas were independently calculated and ranked",
        )
    return results


def _medical_coordination_results(
    *, repo_root: Path, question: Question, candidates: Sequence[EvidenceCandidate]
) -> dict[str, TruthOptionResult] | None:
    text = question.text
    if "家庭三人同时参保e生保计划一" not in text or "太保分别应赔付多少" not in text:
        return None
    values = re.search(r"王某本人发生医疗费用\s*(\d+(?:\.\d+)?)\s*万元[^；。]*?医保报销\s*(\d+(?:\.\d+)?)\s*元；其配偶发生医疗费用\s*(\d+(?:\.\d+)?)\s*万元[^；。]*?医保报销\s*(\d+(?:\.\d+)?)\s*元", text)
    if not values:
        return None
    own_cost = Decimal(values.group(1)) - Decimal(values.group(2)) / Decimal("10000")
    spouse_cost = Decimal(values.group(3)) - Decimal(values.group(4)) / Decimal("10000")
    shared_deductible = Decimal("1")
    e_total = max(Decimal("0"), own_cost + spouse_cost - shared_deductible)
    e_own = max(Decimal("0"), own_cost - shared_deductible)
    other_compensation_offset = e_own
    taibao_deductible_balance = max(Decimal("0"), Decimal("1") - other_compensation_offset)
    taibao = max(Decimal("0"), Decimal(values.group(1)) - Decimal(values.group(2)) / Decimal("10000") - other_compensation_offset - taibao_deductible_balance)
    requirements = (
        ("5", (r"计划一[^。\n]{0,260}同一保单中同时参保[^。\n]{0,220}免赔额", r"免赔额为 10000元[^。\n]{0,360}本次赔付"), "eshengbao_shared_deductible"),
        ("6", (r"应当给付的保险金[^。\n]{0,320}其他途径取得的医疗费用补偿[^。\n]{0,220}免赔额余额[^。\n]{0,80}100",), "taibao_coordination_formula"),
    )
    proofs = []
    for doc_id, patterns, field in requirements:
        candidate, fragment = _find_fragment(candidates, doc_id, patterns)
        if candidate is None:
            return None
        proofs.append((candidate, field, fragment))
    steps = ({"formula": "shared deductible plus other-insurance coordination", "eshengbao_wan": str(e_total), "taibao_wan": str(taibao)},)
    results = {}
    for label, option in question.options.items():
        claim = parse_claim(question, label)
        e_match = re.search(r"e生保赔付\s*(\d+(?:\.\d+)?)\s*(万元|元)", option)
        t_match = re.search(r"太保赔付\s*(\d+(?:\.\d+)?)\s*(万元|元)", option)
        valid = bool(e_match and t_match)
        if valid:
            e_value = _to_wan(e_match.group(1), "万" if e_match.group(2) == "万元" else "元")
            t_value = _to_wan(t_match.group(1), "万" if t_match.group(2) == "万元" else "元")
            valid = e_value == e_total and t_value == taibao
        status = "supported" if valid else "contradicted"
        results[label] = _formula_result(
            repo_root=repo_root, label=label, claim=claim, status=status, proofs=proofs,
            rule_steps=steps, reason="shared deductible and other-insurance coordination were independently calculated",
        )
    return results


def _waiting_period_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    text = str(claim.get("text") or "")
    if "平安安佑福重疾险" not in text:
        return None
    day_match = re.search(r"第\s*(\d+)\s*天", text)
    if not day_match:
        return None
    candidate, fragment = _find_fragment(
        candidates, "4", (r"等待期[\s\S]{0,420}90 日内[\s\S]{0,420}因意外伤害[\s\S]{0,160}无等待期",)
    )
    if candidate is None:
        return None
    day = int(day_match.group(1)); accident = "意外" in text and "普通疾病" not in text
    status = "supported" if day > 90 or accident else "contradicted"
    return _formula_result(
        repo_root=repo_root, label=label, claim=claim, status=status,
        proofs=((candidate, "waiting_period_exception", fragment),),
        rule_steps=({"waiting_period_days": 90, "event_day": day, "accident_exception": accident},),
        reason="waiting-period day and accident exception were bound to the same product clause",
    )


def _policy_loan_extremum_result(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    text = str(claim.get("text") or "")
    if "国寿增益宝" not in text or "保单贷款" not in text or "80%" not in text:
        return None
    candidate, fragment = _find_fragment(
        candidates, "2", (r"申请借款[^。\n]{0,180}最高借款金额不得超过[^。\n]{0,180}80%",)
    )
    if candidate is None:
        return None
    status = "contradicted" if "最低" in text else "supported" if "最高" in text else "unresolved"
    if status == "unresolved":
        return None
    return _formula_result(
        repo_root=repo_root, label=label, claim=claim, status=status,
        proofs=((candidate, "policy_loan_maximum", fragment),),
        rule_steps=({"extremum": "maximum", "ratio": "80%", "base": "cash value less outstanding debt and interest"},),
        reason="policy-loan maximum, base and ratio were independently parsed",
    )

def evaluate_option(*, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]) -> TruthOptionResult:
    atoms = list(claim.get("atoms") or [])
    if not atoms:
        return _unresolved(label, claim, "insurance_claim_atoms_unresolved")
    if claim.get("negation"):
        return _unresolved(label, claim, "negative_coverage_claim_requires_explicit_exclusion_not_absence")
    if claim.get("universal"):
        return _unresolved(label, claim, "universal_coverage_claim_requires_complete_clause_scope")
    selected: list[tuple[EvidenceCandidate, str, str]] = []
    for atom in atoms:
        found = None
        for candidate in candidates:
            fragment = _atom_fragment(atom, candidate)
            if fragment:
                found = (candidate, atom, fragment)
                break
        if found is None:
            return _unresolved(label, claim, f"missing_clause_atom:{atom}")
        selected.append(found)
    sources = []
    provenance = []
    for candidate, atom, fragment in selected:
        source = TruthSource.from_candidate(repo_root=repo_root, candidate=candidate, relevance_fields=("product", "document", "clause", "condition", "exception"))
        sources.append(source)
        provenance.extend(provenance_for_fragments(source=source, fields={atom: (True, fragment, f"insurance_{atom}_direct_clause_v1")}))
    return TruthOptionResult(
        option=label, claim=claim, status="supported", sources=tuple(sources), provenance=tuple(provenance),
        binding={"product_doc": "match", "clause_atoms": "match", "condition": "match_or_not_required", "exception": "match_or_not_required"},
        reason="all claimed positive clause atoms are directly reproduced in the bound product document",
    )


def evaluate(*, repo_root: Path, question: Question, candidates: Sequence[EvidenceCandidate]) -> TruthQuestionResult:
    for calculator in (_death_benefit_results, _surrender_results, _medical_coordination_results):
        calculated = calculator(repo_root=repo_root, question=question, candidates=candidates)
        if calculated is not None:
            return result_from_options(
                question=question,
                option_results=calculated,
                task_type="calculation",
                lane="INS-F",
                implementation_status="IMPLEMENTED_DETERMINISTIC_FORMULA",
                capability=CAPABILITY,
            )
    option_results: dict[str, TruthOptionResult] = {}
    question_text = question.text
    calculation = any(token in question_text for token in ("计算结果", "赔付多少", "金额排序", "退保所得", "分别应赔付", "赔付结果正确"))
    if calculation:
        for label in question.options:
            claim = parse_claim(question, label)
            claim["formula"] = "unresolved"
            claim["variables"] = []
            option_results[label] = _unresolved(label, claim, "insurance_deterministic_calculation_inputs_or_clauses_incomplete")
        return result_from_options(
            question=question,
            option_results=option_results,
            task_type="calculation",
            lane="INS-F",
            implementation_status="PARTIAL_DETERMINISTIC_CALCULATION",
            capability=CAPABILITY,
        )
    for label in question.options:
        claim = parse_claim(question, label)
        scoped = candidates_for_docs(candidates, claim["required_doc_ids"])
        if claim["product"] != "unresolved" and not scoped:
            option_results[label] = _unresolved(label, claim, "missing_product_document")
            continue
        option_results[label] = (
            _waiting_period_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or _policy_loan_extremum_result(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            or evaluate_option(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
        )
    return result_from_options(
        question=question,
        option_results=option_results,
        task_type="clause_lookup",
        lane="INS-C",
        implementation_status="IMPLEMENTED_PARTIAL_DOMAIN_COVERAGE",
        capability=CAPABILITY,
    )
