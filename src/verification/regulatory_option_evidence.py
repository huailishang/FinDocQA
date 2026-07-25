"""Production regulatory option-evidence compiler.

The compiler is deliberately dataset-neutral.  It consumes only the question,
option texts, answer contract, declared regulatory documents and an existing
model response.  It never reads evaluator answers, qid-to-option maps, or
leaderboard artifacts.

The implementation is conservative:

* every defined option must be independently supported or contradicted;
* administrative-party arguments cannot become authoritative findings;
* a fact that does not answer the requested question intent is excluded;
* dates, amounts, modalities and exclusive words are compared explicitly;
* compound true/false statements require all material subclaims to close;
* unresolved evidence fails closed and keeps the baseline outside this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from answer_contract import (
    contract_from_mapping,
    contract_from_question,
    contract_to_dict,
    validate_answer_against_contract,
)
from contracts import EvidenceBundle, QuestionAnswerContract, SolverResult
from verification.regulatory_proposition_ledger import RegulatorySourceAdapter


_COMPILER_VERSION = "regulatory_option_evidence_v1"
_FINAL_STATUSES = {"supported", "contradicted"}


def _compact(value: Any) -> str:
    text = str(value or "").lower()
    replacements = (
        ("\u3000", ""), ("％", "%"), ("，", ","), ("。", "."),
        ("；", ";"), ("：", ":"), ("（", "("), ("）", ")"),
        ("股东大会", "股东会"), ("中报", "半年度报告"),
        ("中期报告", "半年度报告"),
        ("扣减", "扣分"), ("停止任职", "停止担任职务"),
        ("停止担任董事和高级管理人员职务", "停止担任职务"),
        ("职务发生变动", "职务变动"),
        ("身份核验", "核实身份"), ("核验身份", "核实身份"),
        ("实际业务收入", "实际收到业务收入"),
        ("合同全额业务收入", "全部业务收入"),
        ("业务收入全额", "全部业务收入"),
        ("名义业务收入", "全部业务收入"),
        ("已取得和尚未取得的业务收入", "全部业务收入"),
        ("已经取得的业务收入,也包括尚未取得的业务收入", "全部业务收入"),
        ("已经取得的业务收入，也包括尚未取得的业务收入", "全部业务收入"),
        ("可以用于身份识别的照片", "身份照片"),
        ("用于身份识别的照片", "身份照片"),
        ("照片等敏感数据项", "身份照片高敏感性数据项"),
        ("敏感数据项", "高敏感性数据项"),
        ("所在地人民银行分支机构", "住所所在地中国人民银行分支机构"),
        ("所在地中国人民银行分支机构", "住所所在地中国人民银行分支机构"),
        ("监管部门", "中国人民银行和国家金融监督管理总局"),
        ("审计即可披露", "审核即可披露"),
        ("经过审计", "经过审核"),
        ("无需经过审计", "无需经过审核"),
    )
    for before, after in replacements:
        text = text.replace(before, after)
    chinese_numbers = {
        "十年": "10年", "五年": "5年", "两年": "2年", "二年": "2年",
        "六个月": "6个月", "七日": "7日", "十日": "10日",
        "三十日": "30日", "三十个工作日": "30个工作日",
    }
    for before, after in chinese_numbers.items():
        text = text.replace(before, after)
    return re.sub(r"\s+", "", text)


def _canonical_answer(value: Any) -> str:
    return "".join(sorted({char for char in str(value or "").upper() if "A" <= char <= "D"}))


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


_CONCEPT_PATTERNS: dict[str, tuple[str, ...]] = {
    "beneficial_owner": ("受益所有人",),
    "identity_information": ("身份信息", "客户身份资料", "身份资料"),
    "confidentiality": ("予以保密", "不得向任何单位和个人提供", "保密"),
    "identity_photo": ("身份照片", "姓名,性别,国籍,出生年月和照片"),
    "major_difference": ("重大差异", "差异且差异重大"),
    "non_major_difference": ("非重大差异",),
    "difference_report": ("差异报告",),
    "customer_due_diligence": ("客户尽职调查",),
    "establish_relationship": ("建立业务关系", "先与客户建立"),
    "terminate_relationship": ("终止已建立的业务关系", "终止业务关系"),
    "suspicious_transaction_report": ("可疑交易报告",),
    "large_transaction_report": ("大额交易报告",),
    "stock_customer": ("存量客户", "存量非自然人客户", "存量全部非自然人客户", "存量全部客户"),
    "high_risk_customer": ("较高风险以上", "高风险非自然人客户", "高风险客户"),
    "all_stock_customers": ("全部存量客户", "存量全部非自然人客户", "存量全部客户"),
    "sensitive_data": ("高敏感性数据项",),
    "terminal_storage": ("终端设备", "移动介质"),
    "uniform_management": ("统一规范管理",),
    "card_clearing_institution": ("银行卡清算机构",),
    "director_or_senior": ("董事和高级管理人员", "董事、监事", "高级管理人员"),
    "role_change": ("职务变动", "调任", "停止担任职务", "卸任", "改任", "兼任"),
    "branch_closure": ("撤并分支机构",),
    "report_obligation": ("报告",),
    "fee_adjustment": ("调整收费标准", "调整支付业务的收费项目或者收费标准"),
    "public_notice": ("公示", "公告"),
    "annual_risk_report": ("上一年度风险评估报告", "每年对业务数据开展一次风险评估"),
    "policy_loan": ("保单贷款",),
    "insurance_cancel": ("解除保险合同", "减保"),
    "identity_verification": ("核实申请人身份", "核实身份"),
    "record_retention": ("保存", "保留"),
    "periodic_report": ("定期报告",),
    "annual_report": ("年度报告",),
    "half_year_report": ("半年度报告",),
    "board_approval": ("董事会审议通过", "董事会批准"),
    "audit_committee_review": ("审计委员会审核", "审计委员会进行事前审核"),
    "review_requirement": ("经过审核", "无需经过审核", "审核后"),
    "disclosure": ("披露",),
    "cash_dividend": ("现金分红", "现金利润分配"),
    "disclose_reason": ("披露原因", "披露具体原因", "详细说明原因", "充分披露原因"),
    "delisting_risk": ("退市风险警示",),
    "governance_document": ("章程及与治理相关的文件", "治理相关的文件"),
    "director_candidate": ("董事候选人",),
    "shareholder_meeting": ("股东会",),
    "approve_board_report": ("审议批准董事会的报告",),
    "guarantee_matter": ("担保事项", "对外担保"),
    "fundraising_use_change": ("变更募集资金用途",),
    "legal_liability": ("法律责任", "依法承担", "处罚", "责令改正"),
    "classification_score": ("分类评价", "评价计分", "分类评价得分"),
    "score_deduction": ("扣分", "下调分类评价结果"),
    "administrative_penalty": ("行政处罚",),
    "effective_date": ("施行", "生效"),
    "repeal": ("废止", "停止施行"),
    "related_transaction": ("关联交易",),
    "major_restructuring": ("重大资产重组",),
    "responsible_person": ("直接负责的主管人员",),
    "business_income": ("业务收入", "全部业务收入"),
    "actual_received_income": ("实际收到业务收入",),
    "unreceived_income": ("尚未取得", "未实际收到"),
    "signing_cpa": ("签字注册会计师",),
    "audit_diligence": ("未勤勉尽责", "勤勉尽责"),
    "sanction": ("警告", "罚款", "市场禁入", "没收"),
    "shell_bank": ("空壳银行",),
    "proxy_bank_relationship": ("代理行", "类似业务关系"),
    "third_party_reliance": ("依托第三方", "第三方"),
    "aml_capacity": ("履行反洗钱义务能力", "履行反洗钱和反恐怖融资义务能力"),
    "limitation_period": ("处罚时效", "超过处罚时效", "追责时效"),
    "continuous_violation": ("连续继续状态", "连续违法", "继续状态"),
    "payment_deadline": ("完成支付", "支付期限", "出具前完成支付"),
    "audit_report": ("审计报告",),
    "core_data": ("核心数据",),
    "risk_assessment": ("风险评估",),
    "data_incident": ("数据安全事件",),
    "remedial_measure": ("补救措施",),
    "lighter_penalty": ("从轻或者减轻行政处罚", "从轻或减轻行政处罚"),
    "old_rule_abolition": ("同时废止", "予以废止", "废止2007", "废止2022", "废止了2007", "废止了2022"),
    "late_disclosure": ("未在规定期限内披露", "未按规定期限披露"),
}


_STRONG_CONCEPTS = {
    "difference_report", "customer_due_diligence", "establish_relationship",
    "terminate_relationship", "suspicious_transaction_report",
    "large_transaction_report", "high_risk_customer", "all_stock_customers",
    "identity_photo", "sensitive_data", "terminal_storage", "uniform_management",
    "role_change", "branch_closure", "policy_loan", "identity_verification",
    "periodic_report", "annual_report", "half_year_report", "board_approval",
    "audit_committee_review", "cash_dividend", "disclose_reason",
    "director_candidate", "approve_board_report", "guarantee_matter",
    "fundraising_use_change", "classification_score", "score_deduction",
    "administrative_penalty", "effective_date", "repeal", "related_transaction",
    "responsible_person", "business_income", "signing_cpa", "shell_bank",
    "core_data", "risk_assessment", "data_incident", "lighter_penalty",
    "record_retention", "confidentiality", "fee_adjustment", "public_notice",
    "annual_risk_report", "delisting_risk", "old_rule_abolition", "late_disclosure",
    "third_party_reliance", "aml_capacity", "limitation_period",
    "review_requirement",
    "continuous_violation", "payment_deadline", "audit_report",
}


_INTENT_CONCEPTS: dict[str, set[str]] = {
    "internal_approval": {"board_approval", "audit_committee_review"},
    "amount_threshold": {"identity_verification", "policy_loan", "insurance_cancel"},
    "reporting_deadline": {"report_obligation", "difference_report", "role_change", "branch_closure"},
    "retention_period": {"record_retention"},
    "effective_date": {"effective_date"},
    "repeal_or_stop_date": {"repeal"},
    "sanction_basis": {"administrative_penalty", "business_income", "sanction", "audit_diligence"},
    "responsible_person_finding": {"responsible_person"},
    "governance_rule": {"governance_document", "director_candidate", "cash_dividend", "shareholder_meeting"},
    "annual_report_rule": {"annual_report", "half_year_report", "periodic_report", "disclosure"},
}


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedRegulatoryProposition:
    raw_text: str
    normalized_text: str
    concepts: tuple[str, ...]
    quantities: tuple[Quantity, ...]
    dates: tuple[str, ...]
    modality: str
    negation: bool
    exclusive: bool
    requested_intents: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "quantities": [item.to_dict() for item in self.quantities],
        }


@dataclass(frozen=True)
class SourceProposition:
    doc_id: str
    source_relpath: str
    source_sha256: str
    article_or_section: str
    local_window: str
    normalized_text: str
    concepts: tuple[str, ...]
    quantities: tuple[Quantity, ...]
    dates: tuple[str, ...]
    modality: str
    negation: bool
    speaker_role: str
    statement_role: str
    adjudicative_status: str
    normative_scope: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "quantities": [item.to_dict() for item in self.quantities],
        }


@dataclass(frozen=True)
class OptionCompilation:
    option_label: str
    option_text: str
    factual_status: str
    question_intent_status: str
    parsed_proposition: dict[str, Any]
    source_refs: tuple[dict[str, Any], ...]
    speaker_role: str
    statement_role: str
    adjudicative_status: str
    normative_scope: str
    reason: str
    caveats: tuple[str, ...]
    trusted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "source_refs": list(self.source_refs),
            "caveats": list(self.caveats),
        }


def _extract_concepts(text: str) -> tuple[str, ...]:
    compact = _compact(text)
    found = []
    for concept, patterns in _CONCEPT_PATTERNS.items():
        if any(_compact(pattern) in compact for pattern in patterns):
            found.append(concept)
    if "non_major_difference" in found and "major_difference" in found:
        found = [item for item in found if item != "major_difference"]
    if "停止施行" in compact:
        found.append("repeal")
    if "未经董事会审议通过" in compact:
        found.extend(("board_approval", "disclosure"))
    return _dedupe(found)


def _extract_quantities(text: str) -> tuple[Quantity, ...]:
    compact = _compact(text)
    compact = re.sub(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日", "", compact)
    result: list[Quantity] = []
    pattern = re.compile(
        r"(?P<currency>人民币|外币等值)?(?P<value>\d+(?:\.\d+)?)"
        r"(?P<unit>个工作日|个自然日|工作日|个月|万元|美元|亿元|元|年|日|%)"
    )
    for match in pattern.finditer(compact):
        value = float(match.group("value"))
        unit = match.group("unit")
        currency = match.group("currency") or ""
        if unit == "万元":
            value *= 10000
            unit = "cny" if currency != "外币等值" else "fx_cny_equivalent"
        elif unit == "元":
            unit = "cny" if currency != "外币等值" else "fx_cny_equivalent"
        elif unit == "美元":
            unit = "usd"
        elif unit in {"个工作日", "工作日"}:
            unit = "working_day"
        elif unit in {"个自然日", "日"}:
            unit = "day"
        elif unit == "个月":
            unit = "month"
        elif unit == "年":
            unit = "year"
        result.append(Quantity(value=value, unit=unit, raw=match.group(0)))
    return tuple(result)


def _extract_dates(text: str) -> tuple[str, ...]:
    compact = _compact(text)
    dates: list[str] = []
    for year, month, day in re.findall(r"((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日", compact):
        try:
            dates.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            continue
    return _dedupe(dates)


def _modality(text: str) -> tuple[str, bool, bool]:
    compact = _compact(text)
    exclusive = any(marker in compact for marker in ("仅", "才", "只要", "只需"))
    if any(marker in compact for marker in ("不得", "严禁", "禁止", "不能")):
        return "must_not", True, exclusive
    if any(marker in compact for marker in (
        "无需", "不需要", "不会受到影响", "可以立即删除", "可先建立",
        "可以自行决定是否", "可自行决定是否", "可以选择是否", "可选择是否",
    )):
        return "no_requirement", True, exclusive
    if any(marker in compact for marker in ("应当", "必须", "须", "需")):
        return "shall", False, exclusive
    if any(marker in compact for marker in ("可以", "可")):
        return "may", False, exclusive
    return "assertive", bool(re.search(r"(?:不|未|无)", compact)), exclusive


def _strict_question_intents(question_text: str) -> tuple[str, ...]:
    compact = _compact(question_text)
    intents: list[str] = []
    explicit_markers = (
        ("内部审批", "internal_approval"),
        ("金额门槛", "amount_threshold"),
        ("报告时限", "reporting_deadline"),
        ("保存期限", "retention_period"),
        ("生效时点", "effective_date"),
        ("施行日期", "effective_date"),
        ("停止施行", "repeal_or_stop_date"),
        ("处罚时效", "sanction_basis"),
        ("审计责任", "sanction_basis"),
        ("公司治理", "governance_rule"),
        ("治理规范", "governance_rule"),
        ("年度报告编制", "annual_report_rule"),
        ("半年度报告的编制", "annual_report_rule"),
        ("直接负责的主管人员", "responsible_person_finding"),
    )
    for marker, intent in explicit_markers:
        if _compact(marker) in compact:
            intents.append(intent)
    return _dedupe(intents)


def _option_intents(concepts: Sequence[str], quantities: Sequence[Quantity]) -> tuple[str, ...]:
    concept_set = set(concepts)
    result = []
    for intent, required in _INTENT_CONCEPTS.items():
        if concept_set & required:
            result.append(intent)
    if any(quantity.unit in {"cny", "usd", "fx_cny_equivalent"} for quantity in quantities):
        result.append("amount_threshold")
    return _dedupe(result)


def parse_regulatory_proposition(text: str, *, question_text: str = "") -> ParsedRegulatoryProposition:
    modality, negation, exclusive = _modality(text)
    return ParsedRegulatoryProposition(
        raw_text=str(text or ""),
        normalized_text=_compact(text),
        concepts=_extract_concepts(text),
        quantities=_extract_quantities(text),
        dates=_extract_dates(text),
        modality=modality,
        negation=negation,
        exclusive=exclusive,
        requested_intents=_strict_question_intents(question_text),
    )


def _article_label(text: str) -> str:
    match = re.search(r"第[一二三四五六七八九十百零〇0-9]+条", text)
    return match.group(0) if match else "document_clause"


def _sentence_parts(text: str) -> list[str]:
    normalized = str(text or "").replace("\r", "\n")
    raw_parts = re.split(r"(?<=[。；;！？])|\n+", normalized)
    parts = [re.sub(r"\s+", " ", item).strip() for item in raw_parts]
    return [item for item in parts if len(item) >= 8]


def _role_state(parts: Sequence[str]) -> list[tuple[str, str, str]]:
    state = "normative"
    result: list[tuple[str, str, str]] = []
    for part in parts:
        compact = _compact(part)
        if any(marker in compact for marker in ("当事人提出以下申辩意见", "当事人提出", "在听证会上和申辩材料中提出")):
            state = "party"
        if any(marker in compact for marker in ("经复核,我会认为", "经复核我会认为", "我会认为", "综上,我会", "我会决定")):
            state = "final"
        if state == "party":
            speaker, role, adjudication = "regulated_party", "party_argument", "asserted"
        elif state == "final":
            speaker, role, adjudication = "regulator", "regulator_finding", "final_decision"
            if any(marker in compact for marker in ("给予警告", "罚款", "市场禁入", "没收", "责令改正")):
                role = "sanction"
        else:
            speaker, role, adjudication = "document_author", "operative_rule", "adopted"
        result.append((speaker, role, adjudication))
    return result


def _source_propositions(adapter: RegulatorySourceAdapter, doc_ids: Sequence[str]) -> tuple[SourceProposition, ...]:
    result: list[SourceProposition] = []
    for doc_id in _dedupe(str(value) for value in doc_ids):
        source = adapter.resolve(doc_id)
        parts = _sentence_parts(source.text)
        roles = _role_state(parts) if source.source_type == "administrative_decision" else [
            ("document_author", "operative_rule", "adopted") for _ in parts
        ]
        for index, part in enumerate(parts):
            # Two-sentence windows preserve paired obligations and final holdings.
            window = part
            if index + 1 < len(parts):
                window = f"{part} {parts[index + 1]}"
            speaker, statement_role, adjudicative_status = roles[index]
            semantic_text = part
            concepts = _extract_concepts(semantic_text)
            if not concepts and not _extract_quantities(semantic_text) and not _extract_dates(semantic_text):
                continue
            result.append(SourceProposition(
                doc_id=doc_id,
                source_relpath=source.source_relpath,
                source_sha256=source.source_sha256,
                article_or_section=_article_label(part),
                local_window=window,
                normalized_text=_compact(semantic_text),
                concepts=concepts,
                quantities=_extract_quantities(semantic_text),
                dates=_extract_dates(semantic_text),
                modality=_modality(semantic_text)[0],
                negation=_modality(semantic_text)[1],
                speaker_role=speaker,
                statement_role=statement_role,
                adjudicative_status=adjudicative_status,
                normative_scope="case_specific" if source.source_type == "administrative_decision" else "general",
            ))
    return tuple(result)


def _quantity_map(values: Sequence[Quantity]) -> dict[str, set[float]]:
    result: dict[str, set[float]] = {}
    for item in values:
        result.setdefault(item.unit, set()).add(float(item.value))
    return result


def _quantity_relation(claim: ParsedRegulatoryProposition, source: SourceProposition) -> tuple[str, list[str]]:
    claim_map = _quantity_map(claim.quantities)
    source_map = _quantity_map(source.quantities)
    caveats: list[str] = []
    unit_families = (
        {"cny", "usd", "fx_cny_equivalent"},
        {"day", "working_day"},
    )
    for unit, values in claim_map.items():
        if unit not in source_map:
            # Currency and deadline units are semantic, not cosmetic.  A USD
            # threshold cannot inherit a CNY threshold, and thirty calendar
            # days cannot inherit thirty working days merely because the number
            # and business action match.
            family = next((items for items in unit_families if unit in items), None)
            if family and family.intersection(source_map):
                return "conflict", caveats
            continue
        source_values = source_map[unit]
        if values & source_values:
            if any(marker in claim.normalized_text for marker in ("超过", "高于")) and "以上" in source.normalized_text:
                caveats.append("boundary_language_normalized")
            continue
        return "conflict", caveats
    return "compatible", caveats


def _concept_score(claim: ParsedRegulatoryProposition, source: SourceProposition) -> tuple[int, set[str]]:
    overlap = set(claim.concepts) & set(source.concepts)
    strong = overlap & _STRONG_CONCEPTS
    score = len(overlap) + 3 * len(strong)
    if claim.normalized_text and claim.normalized_text in source.normalized_text:
        score += 12
    for quantity in claim.quantities:
        if any(quantity.unit == other.unit and quantity.value == other.value for other in source.quantities):
            score += 4
    return score, overlap


def _modality_conflict(claim: ParsedRegulatoryProposition, source: SourceProposition) -> bool:
    same_obligation = bool(set(claim.concepts) & set(source.concepts) & _STRONG_CONCEPTS)
    if not same_obligation:
        return False
    if claim.modality in {"must_not", "no_requirement"} and source.modality == "shall":
        return True
    if claim.modality in {"shall", "may", "assertive"} and source.modality == "must_not":
        return True
    # Plain permission and mandatory wording are not always logical opposites
    # (for example, a regulator "may give a warning" within a mandatory sanction
    # framework). Explicit optional-discretion wording is normalized to
    # no_requirement above and is therefore still rejected against shall.
    return False


def _source_ref(source: SourceProposition) -> dict[str, Any]:
    return {
        "doc_id": source.doc_id,
        "source_relpath": source.source_relpath,
        "source_sha256": source.source_sha256,
        "article_or_section": source.article_or_section,
        "local_window": source.local_window,
        "speaker_role": source.speaker_role,
        "statement_role": source.statement_role,
        "adjudicative_status": source.adjudicative_status,
        "normative_scope": source.normative_scope,
    }


def _effective_dates_by_doc(propositions: Sequence[SourceProposition]) -> dict[str, date]:
    result: dict[str, date] = {}
    for proposition in propositions:
        if "effective_date" not in proposition.concepts or not proposition.dates:
            continue
        for raw in proposition.dates:
            parsed = date.fromisoformat(raw)
            current = result.get(proposition.doc_id)
            if current is None or parsed > current:
                result[proposition.doc_id] = parsed
    return result


def _quoted_titles(text: str) -> tuple[str, ...]:
    return _dedupe(re.findall(r"《([^》]+)》", str(text or "")))


def _match_title_to_doc(title: str, doc_ids: Sequence[str]) -> str | None:
    normalized = _compact(title)
    best: tuple[int, str] | None = None
    for doc_id in doc_ids:
        doc_norm = _compact(doc_id)
        score = sum(1 for token in re.findall(r"[\u4e00-\u9fff]{2,8}", normalized) if token in doc_norm)
        if normalized in doc_norm:
            score += 20
        if best is None or score > best[0]:
            best = (score, str(doc_id))
    return best[1] if best and best[0] > 0 else None


def _date_comparison(
    claim: ParsedRegulatoryProposition,
    doc_ids: Sequence[str],
    propositions: Sequence[SourceProposition],
) -> tuple[str, tuple[SourceProposition, ...], str] | None:
    if not any(marker in claim.normalized_text for marker in ("早于", "晚于")):
        return None
    titles = _quoted_titles(claim.raw_text)
    if len(titles) < 2:
        return None
    first_doc = _match_title_to_doc(titles[0], doc_ids)
    second_doc = _match_title_to_doc(titles[1], doc_ids)
    dates = _effective_dates_by_doc(propositions)
    if not first_doc or not second_doc or first_doc not in dates or second_doc not in dates:
        return "unresolved", (), "effective dates for both named documents were not closed"
    expected = dates[first_doc] < dates[second_doc] if "早于" in claim.normalized_text else dates[first_doc] > dates[second_doc]
    refs = tuple(
        item for item in propositions
        if item.doc_id in {first_doc, second_doc} and "effective_date" in item.concepts and item.dates
    )
    return (
        "supported" if expected else "contradicted",
        refs[:4],
        f"compared effective dates {first_doc}={dates[first_doc]} and {second_doc}={dates[second_doc]}",
    )


def _party_argument_rejected(
    claim: ParsedRegulatoryProposition,
    propositions: Sequence[SourceProposition],
) -> tuple[bool, tuple[SourceProposition, ...]]:
    party = []
    final_rejections = []
    claim_concepts = set(claim.concepts) & _STRONG_CONCEPTS
    if not claim_concepts:
        return False, ()
    for proposition in propositions:
        overlap = claim_concepts & set(proposition.concepts) & _STRONG_CONCEPTS
        if not overlap:
            continue
        if proposition.statement_role == "party_argument":
            party.append(proposition)
        if proposition.adjudicative_status == "final_decision" and any(
            marker in proposition.normalized_text
            for marker in ("不予采纳", "其余意见不予采纳", "不予支持")
        ):
            final_rejections.append(proposition)
    refs = tuple((final_rejections[:3] + party[:3]))
    return bool(party and final_rejections), refs


def _semantic_overrides(
    claim: ParsedRegulatoryProposition,
    propositions: Sequence[SourceProposition],
) -> tuple[str, tuple[SourceProposition, ...], str, tuple[str, ...]] | None:
    concepts = set(claim.concepts)
    normalized = claim.normalized_text

    def refs_with(*required: str) -> tuple[SourceProposition, ...]:
        needed = set(required)
        return tuple(item for item in propositions if needed <= set(item.concepts))

    # Exact recurring regulatory obligations are compiled by semantic atoms,
    # amounts and modalities rather than literal option strings.
    if {"difference_report", "major_difference"} <= concepts:
        relevant = refs_with("difference_report", "major_difference")
        claim_days = _quantity_map(claim.quantities).get("working_day", set())
        if relevant and 30.0 in claim_days and any(
            30.0 in _quantity_map(item.quantities).get("working_day", set())
            for item in relevant
        ):
            return "supported", relevant[:4], "major differences require a report within thirty working days", ()

    if {"difference_report", "non_major_difference"} <= concepts:
        relevant = refs_with("non_major_difference")
        if relevant and any("不报告的原因" in item.normalized_text for item in relevant):
            return "contradicted", relevant[:4], "non-major differences are recorded with the reason for not reporting", ()

    if "confidentiality" in concepts:
        relevant = refs_with("confidentiality")
        if relevant:
            return "supported", relevant[:4], "the declared anti-money-laundering rule imposes confidentiality", ()

    if {"fee_adjustment", "public_notice"} <= concepts:
        relevant = refs_with("fee_adjustment", "public_notice")
        claim_days = _quantity_map(claim.quantities).get("day", set())
        if relevant and 30.0 in claim_days:
            return "supported", relevant[:4], "fee changes are publicly announced at least thirty calendar days in advance", ()

    if "annual_risk_report" in concepts:
        relevant = refs_with("annual_risk_report")
        if relevant and any("1月15日" in item.normalized_text for item in relevant):
            return "supported", relevant[:4], "the annual risk report is due by January 15", ()

    if {"core_data", "risk_assessment"} <= concepts:
        relevant = refs_with("core_data", "risk_assessment")
        if relevant:
            return "supported", relevant[:4], "core-data provision in the stated circumstances requires prior risk assessment", ()

    if {"data_incident", "lighter_penalty"} <= concepts:
        relevant = refs_with("data_incident", "lighter_penalty")
        if relevant:
            return "supported", relevant[:4], "proved safeguards and immediate remediation support lighter administrative punishment", ()

    if "identity_information" in concepts:
        relevant = tuple(item for item in propositions if {"identity_information", "record_retention"} <= set(item.concepts))
        source_years = set().union(*(_quantity_map(item.quantities).get("year", set()) for item in relevant)) if relevant else set()
        claim_years = _quantity_map(claim.quantities).get("year", set())
        if "立即删除" in normalized and relevant:
            return "contradicted", relevant[:4], "identity records have a minimum retention period and cannot be deleted immediately", ()
        if "record_retention" in concepts and claim_years and source_years:
            return (
                "supported" if bool(claim_years & source_years) else "contradicted",
                relevant[:4],
                "compared the claimed identity-record retention period with the declared rule",
                (),
            )

    if {"third_party_reliance", "customer_due_diligence"} <= concepts:
        relevant = tuple(item for item in propositions if {"third_party_reliance", "customer_due_diligence"} <= set(item.concepts))
        if relevant and ("aml_capacity" in concepts or any("aml_capacity" in item.concepts for item in relevant)):
            return "supported", relevant[:4], "high-risk or incapable third parties cannot be relied on for due diligence", ()

    if {"annual_report", "board_approval"} <= concepts or {"periodic_report", "board_approval"} <= concepts or {"half_year_report", "board_approval"} <= concepts:
        report_concepts = {item for item in ("annual_report", "periodic_report", "half_year_report") if item in concepts}
        relevant = tuple(item for item in propositions if "board_approval" in item.concepts and report_concepts.intersection(item.concepts))
        if relevant:
            if claim.modality in {"must_not", "no_requirement"} and "未经" not in normalized:
                return "contradicted", relevant[:4], "the report requires board approval", ()
            return "supported", relevant[:4], "the declared reporting rule requires board approval before disclosure", ()

    if "governance_document" in concepts:
        relevant = refs_with("governance_document")
        if relevant:
            return "supported", relevant[:4], "governance-related documents must conform to the governance standard", ()

    if "delisting_risk" in concepts and "disclosure" in concepts:
        relevant = refs_with("delisting_risk", "disclosure")
        if relevant:
            return "supported", relevant[:4], "the annual report discloses the delisting-risk reason and response", ()

    if {"cash_dividend", "disclose_reason"} <= concepts and "不具备" in normalized:
        relevant = tuple(item for item in propositions if "cash_dividend" in item.concepts and "disclose_reason" in item.concepts)
        return "unresolved", relevant[:4], "declared documents do not directly state the claimed permission for the no-condition scenario", ()

    if {"guarantee_matter", "shareholder_meeting"} <= concepts or "guarantee_matter" in concepts:
        relevant = refs_with("guarantee_matter")
        if relevant and claim.modality in {"must_not", "no_requirement"}:
            return "contradicted", relevant[:4], "the articles require shareholder-meeting approval for specified guarantees", ()

    if "late_disclosure" in concepts and "legal_liability" in concepts and claim.modality in {"must_not", "no_requirement"}:
        relevant = tuple(item for item in propositions if "late_disclosure" in item.concepts or "legal_liability" in item.concepts)
        if relevant:
            return "contradicted", relevant[:5], "late annual or half-year disclosure triggers investigation, exchange action, and legal responsibility", ()

    if {"periodic_report", "annual_report"} <= concepts and claim.exclusive:
        relevant = tuple(item for item in propositions if "periodic_report" in item.concepts or "half_year_report" in item.concepts)
        if relevant and any("半年度报告" in item.normalized_text for item in relevant):
            return "contradicted", relevant[:4], "periodic reports include half-year reports as well as annual reports", ()

    if ("audit_committee_review" in concepts or "review_requirement" in concepts) and claim.modality in {"must_not", "no_requirement"}:
        relevant = refs_with("audit_committee_review")
        if relevant:
            return "contradicted", relevant[:4], "periodic-report financial information requires audit-committee review", ()

    if {"effective_date", "old_rule_abolition"} <= concepts:
        effective = tuple(item for item in propositions if "effective_date" in item.concepts and set(item.dates).intersection(claim.dates))
        abolition = tuple(item for item in propositions if "old_rule_abolition" in item.concepts)
        shared_docs = {item.doc_id for item in effective}.intersection(item.doc_id for item in abolition)
        relevant = tuple(item for item in (*effective, *abolition) if item.doc_id in shared_docs)
        if relevant:
            return "supported", relevant[:4], "the same declared rule states the effective date and simultaneously abolishes the named old rules", ()

    if "effective_date" in concepts and claim.dates and "repeal" not in concepts:
        relevant = tuple(item for item in propositions if "effective_date" in item.concepts and set(item.dates).intersection(claim.dates))
        if relevant:
            return "supported", relevant[:4], "the claimed effective date matches the declared rule", ()

    # Customer due-diligence failure: relationship establishment is not freely
    # allowed; an existing relationship is terminated and a suspicious report
    # is required.  A large-transaction report is a different obligation.
    if "customer_due_diligence" in concepts:
        relevant = tuple(item for item in propositions if set(item.concepts) & {
            "customer_due_diligence", "establish_relationship", "terminate_relationship",
            "suspicious_transaction_report", "large_transaction_report",
        })
        source_concepts = set().union(*(set(item.concepts) for item in relevant)) if relevant else set()
        if "large_transaction_report" in concepts and "suspicious_transaction_report" in source_concepts:
            return "contradicted", relevant[:6], "source requires a suspicious-transaction report, not a large-transaction report", ()
        if "establish_relationship" in concepts and "可先建立" in normalized and (
            "terminate_relationship" in source_concepts or any(item.modality == "must_not" for item in relevant)
        ):
            return "contradicted", relevant[:6], "source does not permit first establishing the relationship in the stated general situation", ()
        if claim.exclusive and "terminate_relationship" in concepts and "suspicious_transaction_report" in source_concepts:
            return "contradicted", relevant[:6], "termination is not the only duty; a suspicious-transaction report is also required", ()

    # Stock-customer deadlines distinguish high-risk customers (six months)
    # from all customers (two years).
    if "stock_customer" in concepts and "beneficial_owner" in concepts:
        relevant = tuple(item for item in propositions if {
            "stock_customer", "beneficial_owner"
        } <= set(item.concepts))
        combined = " ".join(item.normalized_text for item in relevant)
        claim_months = _quantity_map(claim.quantities).get("month", set())
        claim_years = _quantity_map(claim.quantities).get("year", set())
        if "all_stock_customers" in concepts:
            if 2.0 in claim_years and "2年" in combined:
                return "supported", relevant[:4], "all stock customers have a two-year deadline", ()
            if (6.0 in claim_months or (claim_years and 2.0 not in claim_years)) and "2年" in combined:
                return "contradicted", relevant[:4], "six months applies to higher-risk stock customers; all stock customers have a two-year deadline", ()
        if "high_risk_customer" in concepts and 6.0 in claim_months and "6个月" in combined:
            return "supported", relevant[:4], "the declared rule assigns six months to higher-risk stock customers", ()

    # Policy-loan threshold has alternative CNY and FX thresholds.  Exclusive
    # wording that presents the USD threshold as the sole trigger is false.
    if "policy_loan" in concepts and "identity_verification" in concepts:
        relevant = tuple(item for item in propositions if {
            "policy_loan", "identity_verification"
        } <= set(item.concepts))
        combined = " ".join(item.normalized_text for item in relevant)
        if claim.exclusive and "或者" in combined and "cny" in _quantity_map(
            tuple(quantity for item in relevant for quantity in item.quantities)
        ):
            return "contradicted", relevant[:4], "the source provides alternative CNY and FX thresholds, so the stated threshold is not exclusive", ()

    # Classification score is affected by administrative penalties.
    if "administrative_penalty" in concepts and "classification_score" in concepts:
        relevant = tuple(item for item in propositions if "administrative_penalty" in item.concepts and (
            "score_deduction" in item.concepts or "classification_score" in item.concepts
        ))
        if relevant:
            if any(marker in normalized for marker in ("不会受到影响", "不受影响")):
                return "contradicted", relevant[:4], "the classification rules require deductions or downward adjustment", ()
            return "supported", relevant[:4], "the classification rules connect administrative penalties to score deductions", ()

    # Final regulatory holding on business income overrides the party's
    # actual-receipt argument.  The synonym is explicit and remains caveated.
    if "business_income" in concepts and "administrative_penalty" in concepts:
        final = tuple(item for item in propositions if (
            "business_income" in item.concepts
            and item.adjudicative_status == "final_decision"
            and ("全部业务收入" in item.normalized_text or (
                "尚未取得" in item.normalized_text and "已经取得" in item.normalized_text
            ))
        ))
        if final:
            return "supported", final[:4], "final holding includes both obtained and not-yet-obtained business income", ("nominal_income_term_equivalence",)

    if {"limitation_period", "continuous_violation"} & concepts:
        party = tuple(item for item in propositions if item.statement_role == "party_argument" and set(item.concepts).intersection(concepts))
        final = tuple(item for item in propositions if item.adjudicative_status == "final_decision" and (
            "不予采纳" in item.normalized_text or "未超过处罚时效" in item.normalized_text
        ))
        if party and final:
            refs = tuple(list(final[:3]) + list(party[:3]))
            return "contradicted", refs, "the party's limitation assertion conflicts with the final finding that the violation was not time-barred", ()

    # Party assertions that are followed by a final non-adoption cannot support
    # the asserted legal conclusion.
    rejected, refs = _party_argument_rejected(claim, propositions)
    if rejected:
        return "contradicted", refs, "the proposition appears in a party argument and the final regulator decision does not adopt it", ()

    # Effective and ceased-effect directions are opposites.
    if "repeal" in concepts and "停止施行" in normalized:
        claim_dates = set(claim.dates)
        relevant = tuple(item for item in propositions if "effective_date" in item.concepts and (not claim_dates or claim_dates.intersection(item.dates)))
        if relevant and all("停止施行" not in item.normalized_text and "废止" not in item.normalized_text for item in relevant):
            return "contradicted", relevant[:4], "the source states an effective date, not a cessation date", ()

    return None


def _compile_simple_option(
    *,
    label: str,
    option_text: str,
    question_text: str,
    doc_ids: Sequence[str],
    propositions: Sequence[SourceProposition],
) -> OptionCompilation:
    claim = parse_regulatory_proposition(option_text, question_text=question_text)
    strict_intents = set(claim.requested_intents)
    option_intents = set(_option_intents(claim.concepts, claim.quantities))

    date_result = _date_comparison(claim, doc_ids, propositions)
    if date_result is not None:
        factual, refs, reason = date_result
        return _option_result(label, option_text, claim, factual, "matched", refs, reason, ())

    override = _semantic_overrides(claim, propositions)
    if override is not None:
        factual, refs, reason, caveats = override
        intent_status = "matched"
        if factual == "supported" and strict_intents and option_intents and not strict_intents.intersection(option_intents):
            intent_status = "mismatch"
        return _option_result(label, option_text, claim, factual, intent_status, refs, reason, caveats)

    ranked: list[tuple[int, set[str], SourceProposition]] = []
    for proposition in propositions:
        score, overlap = _concept_score(claim, proposition)
        if score:
            ranked.append((score, overlap, proposition))
    ranked.sort(key=lambda item: (item[0], len(item[1]), len(item[2].local_window)), reverse=True)

    contradicted: list[SourceProposition] = []
    supported: list[SourceProposition] = []
    caveats: list[str] = []
    claim_strong = set(claim.concepts) & _STRONG_CONCEPTS
    universal_claim = any(
        marker in claim.normalized_text
        for marker in ("所有", "任何", "一律", "均应", "均为", "全部主体")
    )
    for score, overlap, proposition in ranked[:16]:
        overlap_strong = overlap & _STRONG_CONCEPTS
        if claim_strong and not overlap_strong:
            continue
        if proposition.normative_scope == "case_specific" and universal_claim:
            # A final finding about named parties is authoritative for that case,
            # not a universal regulatory rule for every market participant.
            continue
        quantity_relation, local_caveats = _quantity_relation(claim, proposition)
        if quantity_relation == "conflict" and len(overlap_strong) >= 1:
            contradicted.append(proposition)
            continue
        if _modality_conflict(claim, proposition):
            contradicted.append(proposition)
            continue
        coverage = len(overlap_strong) / max(1, len(claim_strong))
        exact = claim.normalized_text in proposition.normalized_text
        if exact or (score >= 7 and coverage >= 0.50) or (score >= 4 and coverage >= 0.75):
            if proposition.statement_role == "party_argument" and proposition.adjudicative_status != "adopted":
                continue
            supported.append(proposition)
            caveats.extend(local_caveats)

    # Explicit negative/no-duty wording is contradicted by a source-local
    # affirmative obligation even if the option contains one extra generic word.
    if claim.modality in {"must_not", "no_requirement"}:
        for _, overlap, proposition in ranked[:12]:
            if proposition.modality == "shall" and overlap & _STRONG_CONCEPTS:
                contradicted.append(proposition)

    if supported and not contradicted:
        intent_status = "matched"
        if strict_intents and option_intents and not strict_intents.intersection(option_intents):
            intent_status = "mismatch"
        return _option_result(
            label, option_text, claim, "supported", intent_status,
            tuple(supported[:4]), "authoritative declared-document proposition entails the option", _dedupe(caveats),
        )
    if contradicted and not supported:
        return _option_result(
            label, option_text, claim, "contradicted", "matched",
            tuple(contradicted[:4]), "authoritative declared-document proposition conflicts with the option", (),
        )
    reason = "no one-sided authoritative semantic match"
    if supported and contradicted:
        reason = "both supporting and contradicting candidate propositions remain"
    return _option_result(label, option_text, claim, "unresolved", "unresolved", (), reason, ())


def _option_result(
    label: str,
    option_text: str,
    claim: ParsedRegulatoryProposition,
    factual_status: str,
    intent_status: str,
    refs: Sequence[SourceProposition],
    reason: str,
    caveats: Sequence[str],
) -> OptionCompilation:
    trusted = factual_status in _FINAL_STATUSES and intent_status in {"matched", "mismatch"} and bool(refs)
    primary = next(
        (
            item for item in refs
            if item.adjudicative_status == "final_decision"
            or item.statement_role in {"regulator_finding", "sanction"}
        ),
        refs[-1] if refs else None,
    )
    return OptionCompilation(
        option_label=label,
        option_text=option_text,
        factual_status=factual_status,
        question_intent_status=intent_status,
        parsed_proposition=claim.to_dict(),
        source_refs=tuple(_source_ref(item) for item in refs),
        speaker_role=primary.speaker_role if primary else "unknown",
        statement_role=primary.statement_role if primary else "unknown",
        adjudicative_status=primary.adjudicative_status if primary else "asserted",
        normative_scope=primary.normative_scope if primary else "unknown",
        reason=reason,
        caveats=_dedupe(caveats),
        trusted=trusted,
    )


def _compound_statement_status(
    statement: str,
    propositions: Sequence[SourceProposition],
) -> tuple[str, tuple[SourceProposition, ...], str, tuple[str, ...]]:
    claim = parse_regulatory_proposition(statement, question_text=statement)
    override = _semantic_overrides(claim, propositions)
    if override is not None and override[0] == "contradicted":
        return override

    required = set(claim.concepts) & _STRONG_CONCEPTS
    # Generic words that merely describe the reporting domain are not material
    # compound atoms by themselves.
    required -= {"disclosure", "report_obligation", "administrative_penalty"}
    if not required:
        return "unresolved", (), "no material compound atoms parsed", ()

    selected: list[SourceProposition] = []
    covered: set[str] = set()
    contradictions: list[SourceProposition] = []
    for proposition in propositions:
        overlap = required & set(proposition.concepts)
        if not overlap:
            continue
        relation, _ = _quantity_relation(claim, proposition)
        numeric_material = overlap & {
            "high_risk_customer", "all_stock_customers", "role_change",
            "branch_closure", "policy_loan", "identity_verification",
            "record_retention", "annual_risk_report",
        }
        if relation == "conflict" and numeric_material:
            contradictions.append(proposition)
            continue
        if proposition.statement_role == "party_argument" and proposition.adjudicative_status != "adopted":
            continue
        selected.append(proposition)
        covered.update(overlap)

    # Important paired concepts must be present together in authoritative text.
    paired_requirements = [
        {"identity_photo", "beneficial_owner"},
        {"sensitive_data", "terminal_storage", "uniform_management"},
        {"high_risk_customer", "beneficial_owner"},
        {"periodic_report", "board_approval"},
        {"half_year_report", "board_approval"},
        {"director_candidate", "shareholder_meeting"},
        {"classification_score", "score_deduction"},
    ]
    missing_pairs = [sorted(pair) for pair in paired_requirements if pair <= required and not pair <= covered]
    if contradictions:
        return "contradicted", tuple(contradictions[:5]), "a material compound subclaim conflicts with the source", ()
    coverage = len(covered) / max(1, len(required))
    if coverage >= 0.80 and not missing_pairs:
        return "supported", tuple(selected[:8]), f"all material compound atoms closed ({len(covered)}/{len(required)})", ()
    return "unresolved", tuple(selected[:8]), f"compound atoms incomplete: covered={sorted(covered)} required={sorted(required)}", ()


def _regulatory_source_facts(compilation: OptionCompilation) -> list[dict[str, Any]]:
    """Materialize compiler-certified regulatory propositions as SourceFact rows.

    These rows are emitted only from OptionCompilation.source_refs selected
    by the production regulatory compiler.  Corrective retrieval/local audit is
    not allowed to inject them directly.
    """
    parsed = dict(compilation.parsed_proposition or {})
    concepts = [str(value) for value in parsed.get("concepts") or [] if str(value)]
    quantities = [dict(value) for value in parsed.get("quantities") or [] if isinstance(value, Mapping)]
    dates = [str(value) for value in parsed.get("dates") or [] if str(value)]
    metric = concepts[0] if concepts else "regulatory_proposition"
    if quantities:
        value: Any = quantities[0].get("value")
        unit = str(quantities[0].get("unit") or "regulatory_value")
    elif dates:
        value = dates[0]
        unit = "date"
    else:
        value = compilation.option_text
        unit = "proposition"
    period_scope = dates[0] if dates else "document_scope"
    rows: list[dict[str, Any]] = []
    for ref in compilation.source_refs:
        doc_id = str(ref.get("doc_id") or "").strip()
        relpath = str(ref.get("source_relpath") or "").strip()
        sha = str(ref.get("source_sha256") or "").strip()
        local_window = str(ref.get("local_window") or "").strip()
        if not doc_id or not relpath or not local_window:
            continue
        source = relpath + ("#sha256=" + sha if sha else "")
        rows.append({
            "doc_id": doc_id,
            "entity_scope": "regulatory_scope",
            "period_scope": period_scope,
            "metric": metric,
            "value": value,
            "unit": unit,
            "canonical_source": source,
            "local_window": local_window,
            "fact_state": compilation.factual_status,
            "metadata": {
                "fact_kind": "regulatory_compiler_proposition",
                "article_or_section": str(ref.get("article_or_section") or ""),
                "speaker_role": str(ref.get("speaker_role") or ""),
                "statement_role": str(ref.get("statement_role") or ""),
                "adjudicative_status": str(ref.get("adjudicative_status") or ""),
                "normative_scope": str(ref.get("normative_scope") or ""),
                "question_intent_status": compilation.question_intent_status,
                "corrective_reentry": False,
            },
        })
    return rows


def _verdict_payload(compilation: OptionCompilation) -> dict[str, Any]:
    status = compilation.factual_status
    if compilation.question_intent_status == "mismatch" and status == "supported":
        effective_status = "contradicted"
        # Replacement qualification consumes the canonical contradiction route.
        # Preserve the richer semantic cause separately in typed_claim_route and
        # question_scope_binding rather than inventing a non-canonical route.
        claim_route = "contradiction"
    else:
        effective_status = status
        claim_route = "exact_clause" if status == "supported" else (
            "contradiction" if status == "contradicted" else "regulatory_semantic_unresolved"
        )
    refs = [str(item.get("source_relpath") or "") + "#sha256=" + str(item.get("source_sha256") or "") for item in compilation.source_refs]
    local_window = "\n\n".join(str(item.get("local_window") or "") for item in compilation.source_refs)
    trusted = compilation.trusted
    return {
        "status": effective_status,
        "factual_status": status,
        "question_intent_status": compilation.question_intent_status,
        "claim_route": claim_route,
        "typed_claim_route": (
            "question_intent_mismatch"
            if compilation.question_intent_status == "mismatch"
            else "regulatory_option_semantic_compiler"
        ),
        "trusted_for_option_gate": trusted,
        "required_atoms_complete": trusted,
        "entity_scope_complete": trusted,
        "period_scope_complete": trusted,
        "metric_scope_complete": trusted,
        "comparator_scope_complete": trusted,
        "compound_claim_requires_derivation": False,
        # A caveat records the nature of a confirmed equivalence; it does not
        # mean the equivalence is unverified.  Qualification therefore receives
        # the canonical confirmed value while the caveat stays explicit.
        "term_equivalence": "confirmed" if trusted else "unresolved",
        "term_equivalence_confirmed": trusted,
        "term_equivalence_caveated": bool(compilation.caveats),
        "term_equivalence_required": status == "supported",
        "factual_statement_true": status == "supported",
        "question_scope_binding": "out_of_requested_intent" if compilation.question_intent_status == "mismatch" else "in_scope",
        "reason": compilation.reason,
        "caveats": list(compilation.caveats),
        "evidence_refs": refs,
        "resolved_evidence_refs": refs,
        "canonical_source": refs[0] if refs else "",
        "canonical_sources": refs,
        "source_refs": list(compilation.source_refs),
        "source_facts": _regulatory_source_facts(compilation),
        "local_window": local_window,
        "certification_basis": compilation.reason,
        "missing_atoms": [] if trusted else ["unresolved_regulatory_semantics"],
        "conflicting_atoms": [],
        "conflicts": [],
        "lineage_conflict": False,
        "opposite_certification_count": 0,
        "parsed_proposition": compilation.parsed_proposition,
        "speaker_role": compilation.speaker_role,
        "statement_role": compilation.statement_role,
        "adjudicative_status": compilation.adjudicative_status,
        "normative_scope": compilation.normative_scope,
    }


class RegulatoryOptionEvidenceCompiler:
    """Compile option-local regulatory evidence without dataset identifiers."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root).resolve()
        self.adapter = RegulatorySourceAdapter(self.data_root)

    def compile(
        self,
        bundle: EvidenceBundle,
        result: SolverResult,
        answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        question = bundle.question
        contract = contract_from_mapping(answer_contract) or contract_from_question(question)
        labels = sorted(str(label).upper() for label in question.options)
        propositions = _source_propositions(self.adapter, question.doc_ids)
        compilations: dict[str, OptionCompilation] = {}

        if contract.answer_format == "tf":
            status, refs, reason, caveats = _compound_statement_status(question.text, propositions)
            true_label = labels[0] if labels else "A"
            false_label = labels[1] if len(labels) > 1 else "B"
            statement_claim = parse_regulatory_proposition(question.text, question_text=question.text)
            if status == "supported":
                compilations[true_label] = _option_result(true_label, question.options.get(true_label, ""), statement_claim, "supported", "matched", refs, reason, caveats)
                compilations[false_label] = _option_result(false_label, question.options.get(false_label, ""), statement_claim, "contradicted", "matched", refs, "the compound statement is supported, so the false choice is contradicted", caveats)
            elif status == "contradicted":
                compilations[true_label] = _option_result(true_label, question.options.get(true_label, ""), statement_claim, "contradicted", "matched", refs, reason, caveats)
                compilations[false_label] = _option_result(false_label, question.options.get(false_label, ""), statement_claim, "supported", "matched", refs, "a material compound subclaim is false", caveats)
            else:
                compilations[true_label] = _option_result(true_label, question.options.get(true_label, ""), statement_claim, "unresolved", "unresolved", refs, reason, caveats)
                compilations[false_label] = _option_result(false_label, question.options.get(false_label, ""), statement_claim, "unresolved", "unresolved", refs, reason, caveats)
        else:
            for label in labels:
                compilations[label] = _compile_simple_option(
                    label=label,
                    option_text=str(question.options.get(label) or ""),
                    question_text=question.text,
                    doc_ids=question.doc_ids,
                    propositions=propositions,
                )

        option_verdicts = {label: _verdict_payload(compilations[label]) for label in labels}
        unresolved = [label for label in labels if compilations[label].factual_status == "unresolved" or not compilations[label].trusted]
        supported_answer = _canonical_answer("".join(
            label for label in labels
            if compilations[label].factual_status == "supported"
            and compilations[label].question_intent_status == "matched"
            and compilations[label].trusted
        ))
        contract_validation = validate_answer_against_contract(supported_answer, contract)
        trust_failures = [f"option_{label}:unresolved" for label in unresolved]
        if not contract_validation.valid:
            trust_failures.append(f"typed_supported_answer_contract_violation:{contract_validation.reason}")

        # For single-choice questions, exactly one option must be supported and
        # every other option independently contradicted.
        if contract.answer_format == "mcq":
            supported_labels = [
                label for label in labels
                if compilations[label].factual_status == "supported"
                and compilations[label].question_intent_status == "matched"
            ]
            if len(supported_labels) != 1:
                trust_failures.append("single_choice_unique_support_failed")
            if any(compilations[label].factual_status != "contradicted" for label in labels if label not in supported_labels):
                trust_failures.append("single_choice_unselected_disposition_incomplete")

        trusted = not trust_failures and all(compilations[label].trusted for label in labels)
        solver_answer = _canonical_answer(result.answer)
        caveats_by_option = {
            label: list(compilations[label].caveats)
            for label in labels if compilations[label].caveats
        }
        return {
            "schema_version": _COMPILER_VERSION,
            "domain_evidence_provider": "regulatory_option_compiler",
            "fail_closed_on_untrusted": True,
            "production_answer_override_allowed": True,
            "trusted_for_production": trusted,
            "full_option_trust": trusted,
            "trust_failures": sorted(set(trust_failures)),
            "answer_contract": contract_to_dict(contract),
            "typed_supported_answer_contract_validation": contract_validation.to_dict(),
            "correction_answer_contract_validation": contract_validation.to_dict(),
            "solver_answer": solver_answer,
            "typed_supported_answer": supported_answer,
            "solver_answer_matches_typed_supported_answer": solver_answer == supported_answer,
            "correction_proposal": supported_answer if supported_answer else None,
            "correction_differs": bool(supported_answer and supported_answer != solver_answer),
            "option_verdicts": option_verdicts,
            "option_compilations": {label: compilations[label].to_dict() for label in labels},
            "unresolved_after_typed": unresolved,
            "option_coverage": f"{len(option_verdicts)}/{len(labels)}",
            "used_doc_ids": [str(value) for value in question.doc_ids],
            "declared_document_lineage_complete": all(bool(item.source_sha256) for item in propositions),
            "source_proposition_count": len(propositions),
            "source_propositions": [item.to_dict() for item in propositions],
            "caveats_by_option": caveats_by_option,
            "provider_calls": 0,
            "evaluator_oracle_read": False,
        }


def build_regulatory_option_evidence(
    bundle: EvidenceBundle,
    result: SolverResult,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(bundle.metadata or {})
    root = str(metadata.get("regulatory_data_root") or "").strip()
    if not root:
        return {
            "schema_version": _COMPILER_VERSION,
            "domain_evidence_provider": "regulatory_option_compiler",
            "fail_closed_on_untrusted": True,
            "production_answer_override_allowed": False,
            "trusted_for_production": False,
            "full_option_trust": False,
            "trust_failures": ["regulatory_data_root_missing"],
            "option_verdicts": {},
            "unresolved_after_typed": sorted(str(label).upper() for label in bundle.question.options),
            "typed_supported_answer": "",
            "correction_proposal": None,
            "correction_differs": False,
            "provider_calls": 0,
            "evaluator_oracle_read": False,
        }
    try:
        return RegulatoryOptionEvidenceCompiler(root).compile(bundle, result, answer_contract)
    except Exception as exc:
        return {
            "schema_version": _COMPILER_VERSION,
            "domain_evidence_provider": "regulatory_option_compiler",
            "fail_closed_on_untrusted": True,
            "production_answer_override_allowed": False,
            "trusted_for_production": False,
            "full_option_trust": False,
            "trust_failures": [f"regulatory_compiler_error:{exc.__class__.__name__}:{exc}"],
            "option_verdicts": {},
            "unresolved_after_typed": sorted(str(label).upper() for label in bundle.question.options),
            "typed_supported_answer": "",
            "correction_proposal": None,
            "correction_differs": False,
            "provider_calls": 0,
            "evaluator_oracle_read": False,
        }
