"""Context-bound regulatory proposition atoms used by the REG-P adapter.

This module compares a complete option proposition with a canonical clause.
A prohibition is a legal modal, not an automatic signal that an option is
false.  Verdicts are produced only after actor, operation, object, modal,
condition, scope, time/value and source lineage are bound.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import (
    TruthOptionResult,
    TruthSource,
    candidate_locally_reproduced,
    compact,
    provenance_for_fragments,
)


NOT_APPLICABLE = "not_applicable"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RegulatoryPropositionSpec:
    subject: str
    subject_context: str
    actor: str
    object: str
    operation: str
    modal: str
    polarity: str
    condition: str
    exception: str
    scope: str
    threshold_or_value: str
    time_limit: str
    effective_date: str
    source_role: str
    required_doc_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SUPPORTED_PROPOSITIONS = {
    "identity_record_retention",
    "beneficial_owner_difference_report",
    "aml_confidentiality",
    "legacy_beneficial_owner_completion",
    "executive_position_change_report",
    "beneficial_owner_photo",
    "fee_change_notice",
    "governance_documents_conform",
    "delisting_risk_reason_disclosure",
    "no_dividend_reason_disclosure",
    "director_candidate_pre_meeting_disclosure",
    "shareholder_meeting_board_report_approval",
    "shareholder_meeting_guarantee_approval",
    "shareholder_meeting_use_of_proceeds_change",
    "half_year_report_board_approval",
    "classification_rule_effective_date",
}


def proposition_type(text: str) -> str:
    value = compact(text)
    checks = (
        (("客户身份资料", "保存"), "identity_record_retention"),
        (("差异", "差异报告"), "beneficial_owner_difference_report"),
        (("反洗钱职责", "不得向任何单位和个人提供"), "aml_confidentiality"),
        (("存量", "受益所有人", "完成"), "legacy_beneficial_owner_completion"),
        (("高级管理人员", "调任", "报告"), "executive_position_change_report"),
        (("受益所有人", "照片"), "beneficial_owner_photo"),
        (("收费标准", "公示"), "fee_change_notice"),
        (("公司章程", "治理相关", "符合"), "governance_documents_conform"),
        (("退市风险警示", "披露", "原因"), "delisting_risk_reason_disclosure"),
        (("现金分红", "披露", "原因"), "no_dividend_reason_disclosure"),
        (("董事候选人", "股东会召开前", "披露"), "director_candidate_pre_meeting_disclosure"),
        (("董事会", "报告", "审议批准"), "shareholder_meeting_board_report_approval"),
        (("担保事项",), "shareholder_meeting_guarantee_approval"),
        (("变更募集资金用途",), "shareholder_meeting_use_of_proceeds_change"),
        (("半年度报告", "审议通过"), "half_year_report_board_approval"),
        (("分类监管规定", "施行"), "classification_rule_effective_date"),
    )
    for tokens, name in checks:
        if all(token in value for token in map(compact, tokens)):
            return name
    return UNRESOLVED


def _date(text: str) -> str:
    match = re.search(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not match:
        return NOT_APPLICABLE
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _time_limit(text: str) -> str:
    value = compact(text)
    patterns = (
        (r"(\d+)个工作日", "working_days"),
        (r"(\d+)个自然日", "calendar_days"),
        (r"(\d+)日内", "days"),
        (r"(\d+)年内", "years"),
        (r"至少保存(十|五)年", "years"),
    )
    chinese = {"五": 5, "十": 10}
    for pattern, unit in patterns:
        match = re.search(pattern, value)
        if match:
            raw = match.group(1)
            number = chinese.get(raw, int(raw) if raw.isdigit() else raw)
            return f"{number}_{unit}"
    return NOT_APPLICABLE


def _modal_for_operation(text: str, operation: str) -> str:
    value = compact(text)
    if operation in {"disclose", "provide_to_others"} and any(token in value for token in ("不得披露", "不得向", "不披露")):
        return "must_not"
    if operation.startswith("omit_") or operation.startswith("waive_"):
        return "may"
    if any(token in value for token in ("应当", "应于", "应在", "应自", "须经")):
        return "shall"
    if any(token in value for token in ("可以", "有权")):
        return "may"
    return "asserted"


def _polarity(modal: str, operation: str) -> str:
    if modal == "must_not" or operation.startswith(("omit_", "waive_", "no_")):
        return "prohibitive"
    if operation == "repeal":
        return "cessation"
    return "affirmative"


def parse_claim(question: Question, label: str) -> tuple[str, RegulatoryPropositionSpec]:
    text = str(question.options[label])
    value = compact(text)
    proposition = proposition_type(text)
    base = dict(
        subject=UNRESOLVED,
        subject_context="regulatory",
        actor=UNRESOLVED,
        object=UNRESOLVED,
        operation=UNRESOLVED,
        condition=NOT_APPLICABLE,
        exception=NOT_APPLICABLE,
        scope="specific",
        threshold_or_value=NOT_APPLICABLE,
        time_limit=_time_limit(text),
        effective_date=_date(text),
        source_role="operative_rule",
        required_doc_ids=tuple(map(str, question.doc_ids)),
    )
    if proposition == "identity_record_retention":
        base.update(subject="financial_institution", subject_context="anti_money_laundering", actor="financial_institution", object="customer_identity_material", operation="retain", scope="all_customer_identity_material", threshold_or_value="10_years" if "十年" in text else UNRESOLVED)
    elif proposition == "beneficial_owner_difference_report":
        non_major = "非重大差异" in text
        base.update(subject="financial_institution", subject_context="beneficial_owner_difference_feedback", actor="financial_institution", object="beneficial_owner_difference", operation="submit_difference_report", condition="difference_discovered", scope="non_major_difference" if non_major else "major_difference")
    elif proposition == "aml_confidentiality":
        base.update(subject="aml_information_holder", subject_context="anti_money_laundering_confidentiality", actor="aml_duty_holder", object="aml_customer_identity_information", operation="provide_to_others", condition="information_obtained_by_aml_duties", exception="only_as_provided_by_law", scope="any_unit_or_individual")
    elif proposition == "legacy_beneficial_owner_completion":
        base.update(subject="financial_institution", subject_context="beneficial_owner_transition", actor="financial_institution", object="all_existing_customer_beneficial_owner_verification", operation="complete_verification", condition="from_rule_effective_date", scope="all_existing_customers", threshold_or_value=_time_limit(text))
    elif proposition == "executive_position_change_report":
        base.update(subject="nonbank_payment_institution", subject_context="payment_institution_personnel_change", actor="nonbank_payment_institution", object="executive_internal_transfer", operation="report_transfer", condition="same_institution_other_executive_position", exception="change_application_waived", scope="approved_executives")
    elif proposition == "beneficial_owner_photo":
        base.update(subject="financial_institution", subject_context="beneficial_owner_identity_information", actor="financial_institution", object="listed_company_beneficial_owner_identity_information", operation="identify_and_retain", condition="transparent_listed_company_customer", scope="minimum_identity_information", threshold_or_value="identity_photo")
    elif proposition == "fee_change_notice":
        base.update(subject="nonbank_payment_institution", subject_context="payment_fee_change", actor="nonbank_payment_institution", object="payment_fee_item_or_standard", operation="publicize", condition="before_adjustment_effective", scope="continuous_prominent_publicity")
    elif proposition == "governance_documents_conform":
        base.update(subject="listed_company", subject_context="listed_company_governance", actor="listed_company", object="articles_and_governance_documents", operation="conform", scope="governance_documents")
    elif proposition == "delisting_risk_reason_disclosure":
        base.update(subject="company", subject_context="annual_report_disclosure", actor="company", object="delisting_risk_reason", operation="disclose", condition="after_annual_report_faces_delisting_risk", scope="reason_and_response")
    elif proposition == "no_dividend_reason_disclosure":
        base.update(subject="listed_company", subject_context="cash_dividend_governance", actor="listed_company", object="reason_for_no_cash_dividend", operation="omit_reason_disclosure" if "不披露" in text else "disclose_reason", condition="not_eligible_for_cash_dividend" if "不具备" in text else "eligible_for_cash_dividend", scope="reason")
    elif proposition == "director_candidate_pre_meeting_disclosure":
        base.update(subject="listed_company", subject_context="director_election", actor="listed_company", object="director_candidate_details", operation="disclose", condition="before_shareholder_meeting", scope="candidate_details")
    elif proposition == "shareholder_meeting_board_report_approval":
        actor = "board" if value.startswith("董事会") else "shareholder_meeting"
        base.update(subject="company", subject_context="shareholder_meeting_powers", actor=actor, object="board_report", operation="approve", scope="statutory_power")
    elif proposition == "shareholder_meeting_guarantee_approval":
        base.update(subject="company", subject_context="shareholder_meeting_powers", actor="shareholder_meeting", object="specified_guarantee_matters", operation="waive_approval" if "无需" in text else "approve", scope="articles_specified_guarantees")
    elif proposition == "shareholder_meeting_use_of_proceeds_change":
        base.update(subject="company", subject_context="shareholder_meeting_powers", actor="shareholder_meeting", object="change_use_of_raised_funds", operation="approve", scope="statutory_power")
    elif proposition == "half_year_report_board_approval":
        if "不得披露" in text:
            actor = "audit_committee" if "审计委员会" in text else "board"
            base.update(subject="half_year_report", subject_context="listed_company_half_year_reporting", actor=actor, object="half_year_report", operation="disclose", condition="without_board_approval", scope="entire_report")
        else:
            actor = "audit_committee" if "审计委员会" in text else ("stock_exchange" if "证券交易所" in text else "listed_company_board")
            base.update(subject="half_year_report", subject_context="listed_company_half_year_reporting", actor=actor, object="half_year_report_content", operation="approve", scope="entire_report")
    elif proposition == "classification_rule_effective_date":
        base.update(subject="securities_company_classification_rule", subject_context="rule_temporal_effect", actor="regulator", object="classification_rule", operation="repeal" if "停止施行" in text else "effective", condition="on_date", scope="whole_rule")
    operation = str(base["operation"])
    modal = _modal_for_operation(text, operation)
    spec = RegulatoryPropositionSpec(modal=modal, polarity=_polarity(modal, operation), **base)
    return proposition, spec


# Every match is a complete legal proposition or a clause plus the minimum
# surrounding context needed to bind its actor/scope.
PATTERNS: dict[str, tuple[str, ...]] = {
    "identity_record_retention": (r"客户身份资料在业务关系结束后[^。\n]*应当至少保存十年",),
    "beneficial_owner_difference_report": (
        r"金融机构有合理理由认为由于备案信息不准确而导致差异且差异重大的[^。\n]*30个工作日内[^。\n]*提交差异报告",
        r"第二十九条[\s\S]{0,1600}?属于非重大差异[\s\S]{0,500}?无需提交差异报告[\s\S]{0,1600}?30个工作日内记录信息比对和分析核实的情况、不报告的原因以及采取的措施",
    ),
    "aml_confidentiality": (r"对依法履行反洗钱职责或者义务获得的客户身份资料和交易信息[^。\n]*应当予以保密；非依法律规定，不得向任何单位和个人提供",),
    "legacy_beneficial_owner_completion": (r"自本办法施行之日起2年内完成全部存量客户的受益所有人识别核实工作",),
    "executive_position_change_report": (r"高级管理人员在同一非银行支付机构内调任其他高级管理人员职位的[^。\n]*无需提交变更申请[^。\n]*变更完成后10日内[^。\n]*报告调任情况",),
    "beneficial_owner_photo": (r"对于透明度较高、信息披露充分的客户，如上市公司[^。\n]*识别、留存的受益所有人身份信息应当至少包括[^。\n]*可以用于身份识别的照片",),
    "fee_change_notice": (r"非银行支付机构调整支付业务的收费项目或者收费标准的[^。\n]*至少于调整施行前30个自然日[^。\n]*持续公示",),
    "governance_documents_conform": (r"上市公司章程及与治理相关的文件，应当符合本准则的要求",),
    "delisting_risk_reason_disclosure": (r"年度报告披露后(?:面临|存在)退市风险警示[^。\n]*应当披露(?:导致)?退市风险警示(?:情形)?的原因",),
    "no_dividend_reason_disclosure": (r"具备条件而不进行现金分红的，应当充分披露原因",),
    "director_candidate_pre_meeting_disclosure": (r"上市公司应当在股东会召开前披露董事候选人的详细资料",),
    "shareholder_meeting_board_report_approval": (r"股东会是公司的权力机构，依法行使下列职权：[\s\S]{0,300}?审议批准董事会的报告",),
    "shareholder_meeting_guarantee_approval": (
        r"股东会是公司的权力机构[\s\S]{0,1800}?审议批准本章程第四十七条规定的担保事项",
        r"公司下列对外担保行为，须经股东会审议通过",
    ),
    "shareholder_meeting_use_of_proceeds_change": (r"股东会是公司的权力机构[\s\S]{0,2200}?审议批准变更募集资金用途事项",),
    "half_year_report_board_approval": (
        r"半年度报告内容应当经上市公司董事会审议通过",
        r"未经董事会审议通过的半年度报告不得披露",
    ),
    "classification_rule_effective_date": (r"本规定自\s*2025\s*年\s*8\s*月\s*22\s*日起施行",),
}


def _fact_atoms(proposition: str, fragment: str, source_doc_id: str) -> RegulatoryPropositionSpec:
    value = compact(fragment)
    base = dict(
        subject=UNRESOLVED,
        subject_context="regulatory",
        actor=UNRESOLVED,
        object=UNRESOLVED,
        operation=UNRESOLVED,
        modal="asserted",
        polarity="affirmative",
        condition=NOT_APPLICABLE,
        exception=NOT_APPLICABLE,
        scope="specific",
        threshold_or_value=NOT_APPLICABLE,
        time_limit=_time_limit(fragment),
        effective_date=_date(fragment),
        source_role="operative_rule",
        required_doc_ids=(source_doc_id,),
    )
    if proposition == "identity_record_retention":
        base.update(subject="financial_institution",subject_context="anti_money_laundering",actor="financial_institution",object="customer_identity_material",operation="retain",modal="shall",scope="all_customer_identity_material",threshold_or_value="10_years")
    elif proposition == "beneficial_owner_difference_report":
        non_major = "非重大差异" in fragment
        base.update(subject="financial_institution",subject_context="beneficial_owner_difference_feedback",actor="financial_institution",object="beneficial_owner_difference",operation="record_nonmajor_difference" if non_major else "submit_difference_report",modal="shall",condition="difference_discovered",exception="difference_report_not_required" if non_major else NOT_APPLICABLE,scope="non_major_difference" if non_major else "major_difference")
    elif proposition == "aml_confidentiality":
        base.update(subject="aml_information_holder",subject_context="anti_money_laundering_confidentiality",actor="aml_duty_holder",object="aml_customer_identity_information",operation="provide_to_others",modal="must_not",polarity="prohibitive",condition="information_obtained_by_aml_duties",exception="only_as_provided_by_law",scope="any_unit_or_individual")
    elif proposition == "legacy_beneficial_owner_completion":
        base.update(subject="financial_institution",subject_context="beneficial_owner_transition",actor="financial_institution",object="all_existing_customer_beneficial_owner_verification",operation="complete_verification",modal="shall",condition="from_rule_effective_date",scope="all_existing_customers",threshold_or_value="2_years")
    elif proposition == "executive_position_change_report":
        base.update(subject="nonbank_payment_institution",subject_context="payment_institution_personnel_change",actor="nonbank_payment_institution",object="executive_internal_transfer",operation="report_transfer",modal="shall",condition="same_institution_other_executive_position",exception="change_application_waived",scope="approved_executives")
    elif proposition == "beneficial_owner_photo":
        base.update(subject="financial_institution",subject_context="beneficial_owner_identity_information",actor="financial_institution",object="listed_company_beneficial_owner_identity_information",operation="identify_and_retain",modal="shall",condition="transparent_listed_company_customer",scope="minimum_identity_information",threshold_or_value="identity_photo")
    elif proposition == "fee_change_notice":
        base.update(subject="nonbank_payment_institution",subject_context="payment_fee_change",actor="nonbank_payment_institution",object="payment_fee_item_or_standard",operation="publicize",modal="shall",condition="before_adjustment_effective",scope="continuous_prominent_publicity")
    elif proposition == "governance_documents_conform":
        base.update(subject="listed_company",subject_context="listed_company_governance",actor="listed_company",object="articles_and_governance_documents",operation="conform",modal="shall",scope="governance_documents")
    elif proposition == "delisting_risk_reason_disclosure":
        base.update(subject="company",subject_context="annual_report_disclosure",actor="company",object="delisting_risk_reason",operation="disclose",modal="shall",condition="after_annual_report_faces_delisting_risk",scope="reason_and_response")
    elif proposition == "no_dividend_reason_disclosure":
        base.update(subject="listed_company",subject_context="cash_dividend_governance",actor="listed_company",object="reason_for_no_cash_dividend",operation="disclose_reason",modal="shall",condition="eligible_for_cash_dividend",scope="reason")
    elif proposition == "director_candidate_pre_meeting_disclosure":
        base.update(subject="listed_company",subject_context="director_election",actor="listed_company",object="director_candidate_details",operation="disclose",modal="shall",condition="before_shareholder_meeting",scope="candidate_details")
    elif proposition == "shareholder_meeting_board_report_approval":
        base.update(subject="company",subject_context="shareholder_meeting_powers",actor="shareholder_meeting",object="board_report",operation="approve",modal="may",scope="statutory_power")
    elif proposition == "shareholder_meeting_guarantee_approval":
        base.update(subject="company",subject_context="shareholder_meeting_powers",actor="shareholder_meeting",object="specified_guarantee_matters",operation="approve",modal="shall",scope="articles_specified_guarantees")
    elif proposition == "shareholder_meeting_use_of_proceeds_change":
        base.update(subject="company",subject_context="shareholder_meeting_powers",actor="shareholder_meeting",object="change_use_of_raised_funds",operation="approve",modal="asserted",scope="statutory_power")
    elif proposition == "half_year_report_board_approval":
        if "不得披露" in fragment:
            base.update(subject="half_year_report",subject_context="listed_company_half_year_reporting",actor="listed_company_board",object="half_year_report",operation="disclose",modal="must_not",polarity="prohibitive",condition="without_board_approval",scope="entire_report")
        else:
            base.update(subject="half_year_report",subject_context="listed_company_half_year_reporting",actor="listed_company_board",object="half_year_report_content",operation="approve",modal="shall",scope="entire_report")
    elif proposition == "classification_rule_effective_date":
        base.update(subject="securities_company_classification_rule",subject_context="rule_temporal_effect",actor="regulator",object="classification_rule",operation="effective",modal="asserted",condition="on_date",scope="whole_rule",effective_date="2025-08-22")
    return RegulatoryPropositionSpec(**base)


def _actor_equivalent(claim: str, fact: str, context: str) -> bool:
    if claim == fact:
        return True
    board_aliases = {"board", "listed_company_board"}
    return context == "listed_company_half_year_reporting" and claim in board_aliases and fact in board_aliases


def _comparison(claim: RegulatoryPropositionSpec, fact: RegulatoryPropositionSpec) -> tuple[str, dict[str, str], str]:
    comparison: dict[str, str] = {}
    conflicts: list[str] = []
    unresolved: list[str] = []
    fields = (
        "subject", "subject_context", "actor", "object", "operation", "modal",
        "polarity", "condition", "exception", "scope", "threshold_or_value",
        "time_limit", "effective_date", "source_role",
    )
    for field in fields:
        left, right = str(getattr(claim, field)), str(getattr(fact, field))
        if left == UNRESOLVED or right == UNRESOLVED:
            comparison[field] = "unresolved"
            unresolved.append(field)
            continue
        if left == NOT_APPLICABLE:
            comparison[field] = "not_required"
            continue
        matched = _actor_equivalent(left, right, claim.subject_context) if field == "actor" else left == right
        if matched:
            comparison[field] = "match"
        else:
            comparison[field] = "conflict"
            conflicts.append(field)
    # A rule that applies only when dividend conditions are satisfied does not
    # prove the converse for an ineligible company. Keep that option unresolved.
    if claim.subject_context == "cash_dividend_governance" and "condition" in conflicts:
        return "unresolved", comparison, "canonical clause governs a different condition; converse is not inferred"
    if unresolved:
        return "unresolved", comparison, f"required proposition atoms unresolved: {','.join(unresolved)}"
    if conflicts:
        return "contradicted", comparison, f"canonical proposition conflicts on: {','.join(conflicts)}"
    return "supported", comparison, "all required regulatory proposition atoms match"


def _candidate_matches(proposition: str, candidate: EvidenceCandidate) -> list[tuple[str, RegulatoryPropositionSpec]]:
    rows: list[tuple[str, RegulatoryPropositionSpec]] = []
    for pattern in PATTERNS.get(proposition, ()):
        for match in re.finditer(pattern, candidate.text, re.I | re.S):
            fragment = match.group(0)
            rows.append((fragment, _fact_atoms(proposition, fragment, str(candidate.doc_id))))
    return rows


def _relevance_score(claim: RegulatoryPropositionSpec, fact: RegulatoryPropositionSpec) -> int:
    score = 0
    for field, weight in (("subject_context",4),("actor",4),("object",4),("scope",3),("condition",3),("time_limit",2),("effective_date",2),("operation",1),("modal",1)):
        left, right = str(getattr(claim,field)), str(getattr(fact,field))
        if left == NOT_APPLICABLE:
            continue
        if field == "actor":
            score += weight if _actor_equivalent(left,right,claim.subject_context) else 0
        elif left == right:
            score += weight
    return score


def evaluate_option(
    *,
    repo_root: Path,
    label: str,
    claim: Mapping[str, Any],
    candidates: Sequence[EvidenceCandidate],
) -> TruthOptionResult | None:
    proposition = str(claim.get("proposition") or UNRESOLVED)
    if proposition not in SUPPORTED_PROPOSITIONS:
        return None
    spec_payload = claim.get("proposition_spec")
    if not isinstance(spec_payload, Mapping):
        return TruthOptionResult(option=label, claim=claim, status="unresolved", blockers=("missing_regulatory_proposition_spec",), reason="missing complete proposition atoms")
    spec = RegulatoryPropositionSpec(**{key: tuple(value) if key == "required_doc_ids" else value for key,value in spec_payload.items()})
    ranked: list[tuple[int, EvidenceCandidate, str, RegulatoryPropositionSpec]] = []
    for candidate in candidates:
        # Production verdicts must be selected from immutable source bytes.
        # Invalid high-score chunks must not shadow a later canonical hit.
        if not candidate_locally_reproduced(repo_root, candidate):
            continue
        for fragment, fact in _candidate_matches(proposition, candidate):
            ranked.append((_relevance_score(spec, fact), candidate, fragment, fact))
    if not ranked:
        return TruthOptionResult(option=label, claim=claim, status="unresolved", blockers=("missing_direct_regulatory_clause",), reason="no complete canonical proposition matched")
    ranked.sort(key=lambda row: (row[0], float(row[1].score or 0), -len(row[2])), reverse=True)
    _, candidate, fragment, fact = ranked[0]
    status, comparison, reason = _comparison(spec, fact)
    source = TruthSource.from_candidate(
        repo_root=repo_root,
        candidate=candidate,
        relevance_fields=("subject","subject_context","actor","object","operation","modal","polarity","condition","exception","scope","threshold_or_value","time_limit","effective_date","source_role"),
    )
    decisive = {
        field: (getattr(fact, field), fragment, f"regulatory_proposition_atom_{field}_v2")
        for field, state in comparison.items()
        if state in {"match", "conflict"}
    }
    provenance = provenance_for_fragments(source=source, fields=decisive)
    blockers: tuple[str, ...] = ()
    if status == "unresolved":
        blockers = ("regulatory_proposition_atoms_not_closed",)
    return TruthOptionResult(
        option=label,
        claim=claim,
        status=status,
        sources=(source,),
        provenance=provenance,
        binding={"required_doc":"match", **comparison},
        rule_steps=(
            {"step":"parse_claim_atoms","atoms":spec.to_dict()},
            {"step":"parse_fact_atoms","atoms":fact.to_dict()},
            {"step":"compare_atoms","comparison":comparison,"status":status},
        ),
        blockers=blockers,
        reason=reason,
    )
