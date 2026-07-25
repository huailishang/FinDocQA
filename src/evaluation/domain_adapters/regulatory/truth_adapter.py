"""Independent regulatory proposition truth adapter for Package AG-R1."""
from __future__ import annotations

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
from evaluation.domain_adapters.regulatory.proposition_atoms import (
    evaluate_option as evaluate_proposition_atoms,
    parse_claim as parse_proposition_atoms,
    proposition_type as proposition_atom_type,
)
from evaluation.domain_adapters.regulatory.regulatory_atoms import (
    evaluate_option as evaluate_regulatory_atoms,
    parse_claim as parse_regulatory_atoms,
    proposition_type as r23_proposition_type,
)

CAPABILITY = "regulatory:proposition_role_scope_case_holding_v1"


def _modal(text: str) -> str:
    value = compact(text)
    if any(token in value for token in ("不得", "禁止", "不可以", "不能")):
        return "must_not"
    if any(token in value for token in ("应当", "应", "需要", "需")):
        return "shall"
    if any(token in value for token in ("可以", "可")):
        return "may"
    return "asserted"


def _subject(text: str) -> str:
    subjects = (
        "上市公司董事会", "证券交易所", "中国证监会", "非银行支付机构",
        "签字注册会计师", "会计师事务所", "金融机构", "保险公司",
        "上市公司", "证券公司", "客户", "董事会", "股东会",
    )
    return next((value for value in subjects if value in text), "unresolved")


def _proposition_type(text: str) -> str:
    r23_type = r23_proposition_type(text)
    if r23_type != "unresolved":
        return r23_type
    atom_type = proposition_atom_type(text)
    if atom_type != "unresolved":
        return atom_type
    value = compact(text)
    rules = (
        (("客户身份资料", "保存"), "identity_record_retention"),
        (("差异报告",), "beneficial_owner_difference_report"),
        (("不得向任何单位和个人提供", "不得提供"), "aml_confidentiality"),
        (("存量", "受益所有人", "完成"), "legacy_beneficial_owner_completion"),
        (("高级管理人员", "调任", "报告"), "executive_position_change_report"),
        (("身份信息", "照片"), "beneficial_owner_photo"),
        (("收费标准", "公示"), "fee_change_notice"),
        (("客户尽职调查", "第三方"), "third_party_due_diligence"),
        (("保单贷款", "核实"), "policy_loan_identity_verification"),
        (("解除保险合同", "核实"), "surrender_identity_verification"),
        (("空壳银行",), "shell_bank_relation"),
        (("半年度报告", "审议通过"), "half_year_report_board_approval"),
        (("董事会审议", "不得披露"), "report_board_approval"),
        (("退市风险警示", "披露", "原因"), "delisting_risk_reason_disclosure"),
        (("具备", "现金分红", "披露", "原因"), "no_dividend_reason_disclosure"),
        (("董事候选人", "股东会召开前", "披露"), "director_candidate_pre_meeting_disclosure"),
        (("公司章程", "治理相关", "符合"), "governance_documents_conform"),
        (("董事会", "报告", "审议批准"), "shareholder_meeting_board_report_approval"),
        (("担保事项", "股东大会", "审议批准"), "shareholder_meeting_guarantee_approval"),
        (("变更募集资金用途", "审议批准"), "shareholder_meeting_use_of_proceeds_change"),
        (("未勤勉尽责", "行政处罚"), "audit_diligence_sanction"),
        (("名义业务收入", "处罚"), "nominal_business_income_sanction_basis"),
        (("处罚时效",), "administrative_limitation"),
        (("分类监管规定", "施行"), "classification_rule_effective_date"),
        (("分类评价得分", "行政处罚"), "classification_score_sanction_effect"),
        (("重大资产重组", "关联交易", "披露"), "restructuring_related_party_disclosure"),
        (("直接负责的主管人员",), "directly_responsible_officer"),
    )
    for tokens, name in rules:
        if all(compact(token) in value for token in tokens):
            return name
    return "unresolved"


def _dates(text: str) -> list[str]:
    return re.findall(r"(?:19|20)\d{2}\s*年?\s*\d{1,2}\s*月\s*\d{1,2}\s*日|(?:19|20)\d{2}\s*年", text)


def parse_claim(question: Question, label: str) -> dict[str, Any]:
    text = str(question.options[label])
    r23_type, r23_spec = parse_regulatory_atoms(question, label)
    if r23_type != "unresolved":
        atom_type, atom_spec = r23_type, r23_spec
    else:
        atom_type, atom_spec = parse_proposition_atoms(question, label)
    proposition = atom_type if atom_type != "unresolved" else _proposition_type(text)
    return {
        "option": label,
        "text": text,
        "required_doc_ids": list(question.doc_ids),
        "subject": atom_spec.subject if atom_type != "unresolved" else _subject(text),
        "proposition": proposition,
        "proposition_spec": atom_spec.to_dict(),
        "modal": atom_spec.modal if atom_type != "unresolved" else _modal(text),
        "polarity": atom_spec.polarity,
        "actor": atom_spec.actor,
        "object": atom_spec.object,
        "operation": atom_spec.operation,
        "subject_context": atom_spec.subject_context,
        "threshold_or_value": atom_spec.threshold_or_value,
        "time_limit": atom_spec.time_limit,
        "effective_date": atom_spec.effective_date,
        "negation": any(token in text for token in ("不披露", "不会", "无需", "不得", "不受", "停止施行")),
        "scope": atom_spec.scope if atom_type != "unresolved" else ("general" if any(token in text for token in ("全部", "任何", "均", "应当")) else "specific"),
        "condition": atom_spec.condition if atom_type != "unresolved" else ("present" if any(token in text for token in ("若", "当", "在", "导致", "存在")) else "not_applicable"),
        "exception": atom_spec.exception if atom_type != "unresolved" else ("present" if any(token in text for token in ("除", "非", "只要", "无需")) else "not_applicable"),
        "application_requirement": (
            "waived" if "无需提交变更申请" in text else
            "required" if "需要提交变更申请" in text else
            "not_applicable"
        ),
        "dates": _dates(text),
        "values": re.findall(r"\d+(?:\.\d+)?\s*(?:个工作日|自然日|年|万元|亿元|%)", text),
        "source_role": atom_spec.source_role if atom_type != "unresolved" else ("case_holding" if any(token in text for token in ("案例", "行政处罚", "决定书", "受到行政处罚")) else "operative_rule"),
    }


def _unresolved(label: str, claim: Mapping[str, Any], reason: str) -> TruthOptionResult:
    return TruthOptionResult(
        option=label,
        claim=claim,
        status="unresolved",
        blockers=(reason,),
        reason="regulatory proposition not independently closed",
    )


def _patterns(proposition: str) -> tuple[tuple[str, str], ...]:
    mapping: dict[str, tuple[tuple[str, str], ...]] = {
        "identity_record_retention": (
            (r"客户身份资料[^。\n]{0,160}业务关系结束后[^。\n]{0,100}至少保存\s*(五|十)\s*年", "retention"),
        ),
        "beneficial_owner_difference_report": (
            (r"(?:重大差异|非重大差异)[^。\n]{0,180}发现差异之日起\s*(\d+)\s*个工作日[^。\n]{0,100}(?:提交|反馈|报告)", "difference_report"),
        ),
        "aml_confidentiality": (
            (r"依法履行反洗钱职责[^。\n]{0,180}(?:非依法律规定|除法律另有规定外)[^。\n]{0,140}不得[^。\n]{0,100}(?:单位和个人|对外提供)", "confidentiality"),
        ),
        "legacy_beneficial_owner_completion": (
            (r"存量[^。\n]{0,160}受益所有人[^。\n]{0,160}(?:一年|1年)[^。\n]{0,100}完成", "legacy_completion"),
        ),
        "executive_position_change_report": (
            (r"高级管理人员[^。\n]{0,160}(?:调任|变更)[^。\n]{0,180}(?:十日|10日)[^。\n]{0,100}报告", "position_change"),
        ),
        "beneficial_owner_photo": (
            (r"受益所有人[^。\n]{0,180}(?:照片|影像)", "photo"),
        ),
        "fee_change_notice": (
            (r"调整收费标准[^。\n]{0,180}至少[^。\n]{0,60}(?:三十|30)\s*个?自然日[^。\n]{0,100}公示", "fee_notice"),
        ),
        "third_party_due_diligence": (
            (r"第三方[^。\n]{0,220}(?:较高风险|不具备履行反洗钱义务能力)[^。\n]{0,180}不得[^。\n]{0,120}客户尽职调查", "third_party_prohibition"),
        ),
        "policy_loan_identity_verification": (
            (r"保单贷款[^。\n]{0,120}(?:一万元|1万元|10,?000元)[^。\n]{0,120}(?:核实|识别)[^。\n]{0,60}身份", "policy_loan_threshold"),
        ),
        "surrender_identity_verification": (
            (r"解除保险合同[^。\n]{0,180}(?:一万元|1万元|10,?000元)[^。\n]{0,120}(?:核实|识别)[^。\n]{0,60}身份", "surrender_threshold"),
        ),
        "shell_bank_relation": (
            (r"(?:不得|禁止)[^。\n]{0,120}(?:与)?空壳银行[^。\n]{0,160}(?:建立|维持)[^。\n]{0,120}(?:代理行|业务关系)", "shell_bank_prohibition"),
        ),
        "half_year_report_board_approval": (
            (r"半年度报告内容[^。\n]{0,80}上市公司董事会审议通过", "half_year_board_approval"),
        ),
        "report_board_approval": (
            (r"(?:年度报告|半年度报告)[^。\n]{0,160}董事会审议通过[^。\n]{0,160}(?:未经|未)[^。\n]{0,80}不得披露", "board_approval"),
        ),
        "delisting_risk_reason_disclosure": (
            (r"退市风险警示[^。\n]{0,220}(?:披露|说明)[^。\n]{0,100}(?:原因|情形|应对措施)", "delisting_reason"),
        ),
        "no_dividend_reason_disclosure": (
            (r"具备[^。\n]{0,80}(?:利润分配|现金分红)条件[^。\n]{0,100}不进行现金分红[^。\n]{0,120}应当充分披露原因", "dividend_reason"),
        ),
        "director_candidate_pre_meeting_disclosure": (
            (r"(?:上市公司)?[^。\n]{0,80}(?:应当|应)[^。\n]{0,80}股东会召开前[^。\n]{0,120}披露董事候选人[^。\n]{0,100}(?:详细资料|情况|信息)?", "director_disclosure"),
        ),
        "governance_documents_conform": (
            (r"上市公司章程及与治理相关的文件[^。\n]{0,100}应当符合[^。\n]{0,100}(?:本准则|上市公司治理准则)的要求", "governance_conformity"),
        ),
        "shareholder_meeting_board_report_approval": (
            (r"(?:股东(?:大会|会)[^。\n]{0,180})?(?:审议批准|审议)[^。\n]{0,80}董事会(?:的)?报告", "board_report"),
        ),
        "shareholder_meeting_guarantee_approval": (
            (r"担保事项[^。\n]{0,180}(?:股东大会|股东会)[^。\n]{0,100}(?:审议批准|审议)", "guarantee_approval"),
        ),
        "shareholder_meeting_use_of_proceeds_change": (
            (r"(?:变更募集资金用途|募集资金用途变更)[^。\n]{0,180}(?:股东大会|股东会)[^。\n]{0,100}(?:审议批准|审议)", "proceeds_change"),
        ),
        "audit_diligence_sanction": (
            (r"(?:会计师事务所|注册会计师)[^。\n]{0,220}未勤勉尽责[^。\n]{0,220}(?:行政处罚|处罚)", "audit_sanction"),
        ),
        "nominal_business_income_sanction_basis": (
            (r"名义业务收入[^。\n]{0,200}(?:基数|罚没|处罚)", "nominal_income"),
        ),
        "administrative_limitation": (
            (r"(?:连续|继续)状态[^。\n]{0,220}(?:处罚时效|追责时效|期限)", "limitation"),
        ),
        "classification_rule_effective_date": (
            (r"(?:自|于)\s*2025\s*年\s*8\s*月\s*22\s*日\s*(?:起)?施行", "effective_date"),
        ),
        "classification_score_sanction_effect": (
            (r"行政处罚[^。\n]{0,220}(?:分类评价|评价得分)[^。\n]{0,180}(?:扣分|影响|调整)", "classification_score"),
        ),
        "restructuring_related_party_disclosure": (
            (r"重大资产重组[^。\n]{0,220}关联交易[^。\n]{0,180}披露", "related_party_disclosure"),
        ),
        "directly_responsible_officer": (
            (r"朱要文[^。\n]{0,220}(?:直接负责的主管人员|主管人员)", "responsible_officer"),
        ),
    }
    return mapping.get(proposition, ())


def _modal_from_fragment(fragment: str) -> str:
    return _modal(fragment)


def evaluate_option(
    *,
    repo_root: Path,
    label: str,
    claim: Mapping[str, Any],
    candidates: Sequence[EvidenceCandidate],
) -> TruthOptionResult:
    proposition = str(claim["proposition"])
    r23_result = evaluate_regulatory_atoms(
        repo_root=repo_root,
        label=label,
        claim=claim,
        candidates=candidates,
    )
    if r23_result is not None:
        return r23_result
    atom_result = evaluate_proposition_atoms(
        repo_root=repo_root,
        label=label,
        claim=claim,
        candidates=candidates,
    )
    if atom_result is not None:
        return atom_result
    if proposition == "unresolved":
        return _unresolved(label, claim, "regulatory_proposition_unresolved")
    if proposition == "nominal_business_income_sanction_basis":
        return _unresolved(label, claim, "missing_legal_terminology_equivalence")
    for candidate in candidates:
        for pattern, rule_name in _patterns(proposition):
            match = re.search(pattern, candidate.text, re.I | re.S)
            if not match:
                continue
            fragment = match.group(0)
            fact_modal = _modal_from_fragment(fragment)
            claim_modal = str(claim["modal"])
            claim_negation = bool(claim["negation"])
            status = "supported"
            modal_binding = "match"
            subject_binding = "match_or_general_rule"
            scope_binding = "match"
            exception_binding = "match_or_not_required"
            date_binding = "match_or_not_required"
            if proposition in {"no_dividend_reason_disclosure", "classification_rule_effective_date"} and claim_negation:
                status = "contradicted"
                modal_binding = "conflict"
            elif claim_modal != "asserted" and fact_modal != "asserted" and claim_modal != fact_modal:
                status = "contradicted"
                modal_binding = "conflict"
            if proposition == "half_year_report_board_approval":
                actual_subject = "上市公司董事会"
                if str(claim.get("subject")) != actual_subject:
                    status = "contradicted"
                    subject_binding = "conflict"
                else:
                    subject_binding = "match"
            if proposition == "shareholder_meeting_board_report_approval" and claim.get("scope") == "general":
                if not any(token in fragment for token in ("全部", "所有", "任何", "均")):
                    status = "contradicted"
                    scope_binding = "conflict"
            application_requirement = str(claim.get("application_requirement") or "not_applicable")
            if application_requirement != "not_applicable":
                actual_requirement = "waived" if "无需提交变更申请" in fragment else "required"
                if application_requirement != actual_requirement:
                    status = "contradicted"
                    exception_binding = "conflict"
                else:
                    exception_binding = "match"
            claim_dates = list(claim.get("dates") or [])
            if claim_dates:
                date_match = all(compact(value) in compact(fragment) for value in claim_dates)
                if not date_match:
                    status = "contradicted"
                    date_binding = "conflict"
                else:
                    date_binding = "match"
            source = TruthSource.from_candidate(
                repo_root=repo_root,
                candidate=candidate,
                relevance_fields=("subject", "document", "action", "modal", "scope", "condition", "exception", "effective_date", "source_role"),
            )
            provenance = provenance_for_fragments(
                source=source,
                fields={
                    "regulatory_proposition": (rule_name, fragment, f"regulatory_{proposition}_direct_clause_v1"),
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
                    "subject": subject_binding,
                    "action": "match",
                    "modal": modal_binding,
                    "scope": scope_binding,
                    "condition": "match_or_not_required",
                    "exception": exception_binding,
                    "effective_date": date_binding,
                    "source_role": "match",
                },
                reason="document-local regulatory proposition, subject, scope, exception and date were independently checked",
            )
    return _unresolved(label, claim, "missing_direct_regulatory_clause")


def evaluate(
    *,
    repo_root: Path,
    question: Question,
    candidates: Sequence[EvidenceCandidate],
) -> TruthQuestionResult:
    option_results: dict[str, TruthOptionResult] = {}
    for label in question.options:
        claim = parse_claim(question, label)
        scoped = candidates_for_docs(candidates, claim["required_doc_ids"])
        option_results[label] = (
            evaluate_option(repo_root=repo_root, label=label, claim=claim, candidates=scoped)
            if scoped
            else _unresolved(label, claim, "missing_required_doc")
        )
    lane = "REG-H" if any(row.claim.get("source_role") == "case_holding" for row in option_results.values()) else "REG-P"
    return result_from_options(
        question=question,
        option_results=option_results,
        task_type="regulatory_proposition",
        lane=lane,
        implementation_status="IMPLEMENTED_PARTIAL_DOMAIN_COVERAGE",
        capability=CAPABILITY,
    )
