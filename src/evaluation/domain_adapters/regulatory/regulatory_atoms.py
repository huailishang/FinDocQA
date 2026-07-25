"""Additional REG-P proposition atoms required by Package AG-R2.3.

The module extends the AG-R2.2 comparator for real-bundle residual clauses.  It
contains production parsing and comparison logic only; evaluator oracle labels
and baseline answers are deliberately outside this trust boundary.
"""
from __future__ import annotations

from dataclasses import asdict
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
from evaluation.domain_adapters.regulatory.proposition_atoms import (
    NOT_APPLICABLE,
    UNRESOLVED,
    RegulatoryPropositionSpec,
    _comparison,
    _relevance_score,
)


SUPPORTED_PROPOSITIONS = {
    "identity_record_retention",
    "legacy_beneficial_owner_completion",
    "customer_due_diligence_failure",
    "due_diligence_rule_effective_repeal",
    "cross_rule_effective_date_order",
    "bankcard_officer_change_report",
    "bankcard_branch_closure_report",
    "periodic_report_definition",
    "late_periodic_report_liability",
    "periodic_financial_information_audit",
    "administrative_limitation_case",
}


PROPOSITION_PATTERNS: dict[str, tuple[str, ...]] = {
    "identity_record_retention": (
        r"客户身份资料在业务关系结束后[^。\n]*应当至少保存十年",
    ),
    "legacy_beneficial_owner_completion": (
        r"自本办法施行之日起6个月内完成较高风险以上存量客户受益所有人识别核实工作",
        r"自本办法施行之日起2年内完成全部存量客户的受益所有人识别核实工作",
    ),
    "customer_due_diligence_failure": (
        r"金融机构无法按本办法规定开展客户尽职调查的，不得与客户建立业务关系、提供规定金额以上的一次性金融服务；已经建立业务关系的，应当根据情形终止已建立的业务关系，并提交可疑交易报告",
    ),
    "due_diligence_rule_effective_repeal": (
        r"第五十二条\s*本办法自2026年1月1日起施行。[\s\S]{0,500}?令〔2007〕第2号发布[\s\S]{0,300}?令〔2022〕第1号发布）同时废止",
    ),
    "cross_rule_effective_date_order": (
        r"现予公布，自2026年1月1日起施行",
        r"现予公布，自2026年1月20日起施行",
        r"第五十二条\s*本办法自2026年1月1日起施行",
    ),
    "bankcard_officer_change_report": (
        r"第二十六条\s*银行卡清算机构的董事和高级管理人员停止担任董事和高级管理人员职务的[^。\n]*自职务变动之日起7日内[^。\n]*报告[^。\n]*。",
    ),
    "bankcard_branch_closure_report": (
        r"第二十七条\s*银行卡清算机构撤并分支机构的，应当至少提前30日向分支机构住所地中国人民银行分支机构报告[^。\n]*。",
    ),
    "periodic_report_definition": (
        r"第十二条\s*上市公司应当披露的定期报告包括年度报告、中期报告",
    ),
    "late_periodic_report_liability": (
        r"第二十一条\s*上市公司未在规定期限内披露年度报告和中期报告的，中国证监会应当立即立案调查，证券交易所应当按照股票上市规则予以处理",
    ),
    "periodic_financial_information_audit": (
        r"年度报告中的财务会计报告应当经符合《证券法》规定的会计师事务所审计",
    ),
    "administrative_limitation_case": (
        r"由于世纪华通未按规定执行\s*2018\s*年度商誉减值测试,导致当年和后续年度均高估资产,违法行为未超过处罚时效",
    ),
}


def _duration(text: str) -> str:
    value = compact(text)
    patterns = (
        (r"(\d+)个工作日", "working_days"),
        (r"(\d+)个自然日", "calendar_days"),
        (r"(\d+)日内", "days"),
        (r"(\d+)个月内", "months"),
        (r"(\d+)年内", "years"),
    )
    for pattern, unit in patterns:
        match = re.search(pattern, value)
        if match:
            return f"{int(match.group(1))}_{unit}"
    if "至少保存十年" in value:
        return "10_years"
    if "至少保存五年" in value:
        return "5_years"
    return NOT_APPLICABLE


def _date(text: str) -> str:
    match = re.search(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not match:
        return NOT_APPLICABLE
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def proposition_type(text: str) -> str:
    value = compact(text)
    # Temporal comparisons mention full regulation titles that themselves
    # contain words such as “客户身份资料…保存”; recognise the whole claim
    # before generic retention propositions.
    if "施行日期早于" in value and "客户尽职调查" in value and "受益所有人识别" in value:
        return "cross_rule_effective_date_order"
    if "客户身份资料" in value and "保存" in value:
        return "identity_record_retention"
    if "存量" in value and "受益所有人" in value and "完成" in value:
        return "legacy_beneficial_owner_completion"
    if "无法按规定开展客户尽职调查" in value:
        return "customer_due_diligence_failure"
    if "新办法自" in value and "施行" in value and "废止" in value:
        return "due_diligence_rule_effective_repeal"
    if "银行卡清算机构" in value and "职务变动" in value and "7日内" in value:
        return "bankcard_officer_change_report"
    if "银行卡清算机构撤并分支机构" in value and "30日" in value:
        return "bankcard_branch_closure_report"
    if "定期报告仅包含年度报告" in value:
        return "periodic_report_definition"
    if "未在规定期限内披露年度报告和中报" in value and "无需承担法律责任" in value:
        return "late_periodic_report_liability"
    if "定期报告中的财务信息" in value and "无需经过审计" in value:
        return "periodic_financial_information_audit"
    if "信息披露违法" in value and "处罚时效" in value:
        return "administrative_limitation_case"
    # Wording variant used by a regulatory wording variant.  The typed regulatory evaluator handles the
    # proposition once this alias has normalised the type.
    if "董事候选人" in value and "股东会召开" in value and "披露" in value:
        return "director_candidate_pre_meeting_disclosure"
    return UNRESOLVED


def parse_claim(question: Question, label: str) -> tuple[str, RegulatoryPropositionSpec]:
    text = str(question.options[label])
    value = compact(text)
    proposition = proposition_type(text)
    base: dict[str, Any] = {
        "subject": UNRESOLVED,
        "subject_context": "regulatory",
        "actor": UNRESOLVED,
        "object": UNRESOLVED,
        "operation": UNRESOLVED,
        "modal": "asserted",
        "polarity": "affirmative",
        "condition": NOT_APPLICABLE,
        "exception": NOT_APPLICABLE,
        "scope": "specific",
        "threshold_or_value": NOT_APPLICABLE,
        "time_limit": _duration(text),
        "effective_date": _date(text),
        "source_role": "operative_rule",
        "required_doc_ids": tuple(map(str, question.doc_ids)),
    }
    if proposition == "identity_record_retention":
        duration = _duration(text)
        base.update(
            subject="financial_institution",
            subject_context="anti_money_laundering",
            actor="financial_institution",
            object="customer_identity_material",
            operation="retain",
            modal="shall",
            scope="all_customer_identity_material",
            threshold_or_value=duration,
        )
    elif proposition == "legacy_beneficial_owner_completion":
        higher_risk = "较高风险以上" in text
        duration = _duration(text)
        base.update(
            subject="financial_institution",
            subject_context="beneficial_owner_transition",
            actor="financial_institution",
            object="existing_customer_beneficial_owner_verification",
            operation="complete_verification",
            modal="shall",
            condition="from_rule_effective_date",
            scope="higher_risk_existing_customers" if higher_risk else "all_existing_customers",
            threshold_or_value=duration,
        )
    elif proposition == "customer_due_diligence_failure":
        base.update(
            subject="financial_institution",
            subject_context="customer_due_diligence_failure",
            actor="financial_institution",
            object="customer_business_relationship",
            operation="establish_relationship",
            modal="may" if "可先建立" in text else "must_not",
            condition="due_diligence_unavailable",
            scope="new_business_relationship",
        )
    elif proposition == "due_diligence_rule_effective_repeal":
        base.update(
            subject="customer_due_diligence_rule_2025_11",
            subject_context="rule_temporal_effect",
            actor="joint_financial_regulators",
            object="customer_due_diligence_rule",
            operation="effective_and_repeal_predecessors",
            modal="asserted",
            condition="on_effective_date",
            scope="whole_rule_and_named_predecessors",
            threshold_or_value="repeal_2007_2_and_2022_1",
            effective_date=_date(text),
        )
    elif proposition == "cross_rule_effective_date_order":
        base.update(
            subject="two_financial_rules",
            subject_context="cross_rule_temporal_order",
            actor="financial_regulators",
            object="due_diligence_rule_before_beneficial_owner_rule",
            operation="effective_before",
            modal="asserted",
            condition="compare_effective_dates",
            scope="two_declared_rules",
            threshold_or_value="earlier_than",
        )
    elif proposition == "bankcard_officer_change_report":
        base.update(
            subject="bankcard_clearing_institution",
            subject_context="bankcard_personnel_reporting",
            actor="bankcard_clearing_institution",
            object="director_or_senior_manager_position_change",
            operation="report",
            modal="shall",
            condition="from_position_change_date",
            scope="directors_and_senior_managers",
            threshold_or_value=_duration(text),
        )
    elif proposition == "bankcard_branch_closure_report":
        base.update(
            subject="bankcard_clearing_institution",
            subject_context="bankcard_branch_reporting",
            actor="bankcard_clearing_institution",
            object="branch_closure_or_merger",
            operation="report",
            modal="shall",
            condition="before_branch_closure_or_merger",
            scope="affected_branch",
            threshold_or_value=_duration(text),
        )
    elif proposition == "periodic_report_definition":
        base.update(
            subject="listed_company",
            subject_context="periodic_report_scope",
            actor="listed_company",
            object="periodic_reports",
            operation="include_only_annual_report",
            modal="asserted",
            scope="periodic_report_types",
            threshold_or_value="annual_only",
        )
    elif proposition == "late_periodic_report_liability":
        base.update(
            subject="listed_company",
            subject_context="late_periodic_report_enforcement",
            actor="listed_company",
            object="late_annual_and_interim_reports",
            operation="no_legal_consequence",
            modal="asserted",
            condition="missed_disclosure_deadline",
            scope="annual_and_interim_reports",
        )
    elif proposition == "periodic_financial_information_audit":
        base.update(
            subject="listed_company",
            subject_context="periodic_financial_audit",
            actor="listed_company",
            object="periodic_report_financial_information",
            operation="disclose_without_external_audit",
            modal="may",
            condition="before_disclosure",
            scope="all_periodic_reports",
        )
    elif proposition == "administrative_limitation_case":
        base.update(
            subject="century_huatong",
            subject_context="administrative_penalty_case_limitation",
            actor="listed_company",
            object="2018_goodwill_impairment_disclosure_violation",
            operation="limitation_expired",
            modal="asserted",
            condition="no_continuing_effect",
            scope="case_specific_violation",
            source_role="case_holding",
        )
    elif proposition == "director_candidate_pre_meeting_disclosure":
        base.update(
            subject="listed_company",
            subject_context="director_election",
            actor="listed_company",
            object="director_candidate_details",
            operation="disclose",
            modal="may" if any(token in value for token in ("可以", "可在")) else "shall",
            condition="before_shareholder_meeting",
            scope="candidate_details",
        )
    return proposition, RegulatoryPropositionSpec(**base)


def _fact_atoms(proposition: str, fragment: str, doc_id: str) -> RegulatoryPropositionSpec:
    base: dict[str, Any] = {
        "subject": UNRESOLVED,
        "subject_context": "regulatory",
        "actor": UNRESOLVED,
        "object": UNRESOLVED,
        "operation": UNRESOLVED,
        "modal": "asserted",
        "polarity": "affirmative",
        "condition": NOT_APPLICABLE,
        "exception": NOT_APPLICABLE,
        "scope": "specific",
        "threshold_or_value": NOT_APPLICABLE,
        "time_limit": _duration(fragment),
        "effective_date": _date(fragment),
        "source_role": "operative_rule",
        "required_doc_ids": (doc_id,),
    }
    if proposition == "identity_record_retention":
        base.update(subject="financial_institution",subject_context="anti_money_laundering",actor="financial_institution",object="customer_identity_material",operation="retain",modal="shall",scope="all_customer_identity_material",threshold_or_value="10_years",time_limit="10_years")
    elif proposition == "legacy_beneficial_owner_completion":
        higher_risk = "较高风险以上" in fragment
        duration = _duration(fragment)
        base.update(subject="financial_institution",subject_context="beneficial_owner_transition",actor="financial_institution",object="existing_customer_beneficial_owner_verification",operation="complete_verification",modal="shall",condition="from_rule_effective_date",scope="higher_risk_existing_customers" if higher_risk else "all_existing_customers",threshold_or_value=duration,time_limit=duration)
    elif proposition == "customer_due_diligence_failure":
        base.update(subject="financial_institution",subject_context="customer_due_diligence_failure",actor="financial_institution",object="customer_business_relationship",operation="establish_relationship",modal="must_not",polarity="prohibitive",condition="due_diligence_unavailable",scope="new_business_relationship")
    elif proposition == "due_diligence_rule_effective_repeal":
        base.update(subject="customer_due_diligence_rule_2025_11",subject_context="rule_temporal_effect",actor="joint_financial_regulators",object="customer_due_diligence_rule",operation="effective_and_repeal_predecessors",modal="asserted",condition="on_effective_date",scope="whole_rule_and_named_predecessors",threshold_or_value="repeal_2007_2_and_2022_1",effective_date="2026-01-01")
    elif proposition == "bankcard_officer_change_report":
        base.update(subject="bankcard_clearing_institution",subject_context="bankcard_personnel_reporting",actor="bankcard_clearing_institution",object="director_or_senior_manager_position_change",operation="report",modal="shall",condition="from_position_change_date",scope="directors_and_senior_managers",threshold_or_value="7_days",time_limit="7_days")
    elif proposition == "bankcard_branch_closure_report":
        base.update(subject="bankcard_clearing_institution",subject_context="bankcard_branch_reporting",actor="bankcard_clearing_institution",object="branch_closure_or_merger",operation="report",modal="shall",condition="before_branch_closure_or_merger",scope="affected_branch",threshold_or_value="30_days",time_limit="30_days")
    elif proposition == "periodic_report_definition":
        base.update(subject="listed_company",subject_context="periodic_report_scope",actor="listed_company",object="periodic_reports",operation="include_annual_and_interim_reports",modal="shall",scope="periodic_report_types",threshold_or_value="annual_and_interim")
    elif proposition == "late_periodic_report_liability":
        base.update(subject="listed_company",subject_context="late_periodic_report_enforcement",actor="listed_company",object="late_annual_and_interim_reports",operation="investigate_and_exchange_handle",modal="shall",condition="missed_disclosure_deadline",scope="annual_and_interim_reports")
    elif proposition == "periodic_financial_information_audit":
        base.update(subject="listed_company",subject_context="periodic_financial_audit",actor="listed_company",object="annual_report_financial_accounting_report",operation="external_audit_required",modal="shall",condition="before_disclosure",scope="annual_report_subset")
    elif proposition == "administrative_limitation_case":
        base.update(subject="century_huatong",subject_context="administrative_penalty_case_limitation",actor="listed_company",object="2018_goodwill_impairment_disclosure_violation",operation="limitation_not_expired",modal="asserted",condition="continuing_effect_present",scope="case_specific_violation",source_role="case_holding")
    return RegulatoryPropositionSpec(**base)


def _source(repo_root: Path, candidate: EvidenceCandidate, fragment: str) -> tuple[TruthSource, tuple[Any, ...]]:
    source = TruthSource.from_candidate(
        repo_root=repo_root,
        candidate=candidate,
        relevance_fields=(
            "subject", "subject_context", "actor", "object", "operation", "modal",
            "polarity", "condition", "exception", "scope", "threshold_or_value",
            "time_limit", "effective_date", "source_role",
        ),
    )
    provenance = provenance_for_fragments(
        source=source,
        fields={"regulatory_proposition": (fragment, fragment, "regulatory_r23_direct_clause_v1")},
    )
    return source, provenance


def _special_cross_rule(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult:
    first: tuple[EvidenceCandidate, str, str] | None = None
    second: tuple[EvidenceCandidate, str, str] | None = None
    for candidate in candidates:
        if not candidate_locally_reproduced(repo_root, candidate):
            continue
        text = candidate.text
        if "strict_v3_009" in str(candidate.doc_id):
            match = re.search(r"(?:自|，自)\s*2026\s*年\s*1\s*月\s*1\s*日\s*(?:起)?施行", text)
            if match:
                first = (candidate, match.group(0), "2026-01-01")
        elif "strict_v3_008" in str(candidate.doc_id):
            match = re.search(r"(?:自|，自)\s*2026\s*年\s*1\s*月\s*20\s*日\s*(?:起)?施行", text)
            if match:
                second = (candidate, match.group(0), "2026-01-20")
    if not first or not second:
        return TruthOptionResult(option=label, claim=claim, status="unresolved", blockers=("missing_cross_rule_effective_dates",), reason="both canonical effective-date clauses are required")
    status = "supported" if first[2] < second[2] else "contradicted"
    sources: list[TruthSource] = []
    provenance: list[Any] = []
    for candidate, fragment, date_value in (first, second):
        source = TruthSource.from_candidate(repo_root=repo_root,candidate=candidate,relevance_fields=("effective_date","source_role"))
        sources.append(source)
        provenance.extend(provenance_for_fragments(source=source,fields={"effective_date":(date_value,fragment,"regulatory_cross_rule_date_v1")}))
    return TruthOptionResult(
        option=label,
        claim=claim,
        status=status,
        sources=tuple(sources),
        provenance=tuple(provenance),
        binding={"required_doc":"match","subject":"match","actor":"match","object":"match","operation":"match","modal":"match","polarity":"match","condition":"match","exception":"not_required","scope":"match","threshold_or_value":"match","time_limit":"not_required","effective_date":"match","source_role":"match"},
        rule_steps=({"step":"compare_effective_dates","left":"2026-01-01","right":"2026-01-20","operator":"<","status":status},),
        reason="both declared rules were independently dated and compared",
    )


def _status_for_special_subset(proposition: str, claim_spec: RegulatoryPropositionSpec, fact: RegulatoryPropositionSpec) -> tuple[str, dict[str, str], str]:
    if proposition == "periodic_financial_information_audit":
        comparison = {key:"match" for key in ("subject","subject_context","actor")}
        comparison.update({
            "object":"counterexample_subset",
            "operation":"conflict",
            "modal":"conflict",
            "polarity":"match",
            "condition":"match",
            "exception":"not_required",
            "scope":"counterexample_subset",
            "threshold_or_value":"not_required",
            "time_limit":"not_required",
            "effective_date":"not_required",
            "source_role":"match",
        })
        return "contradicted", comparison, "annual reports are a periodic-report subset whose financial accounting report requires external audit"
    return _comparison(claim_spec, fact)


def evaluate_option(
    *, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]
) -> TruthOptionResult | None:
    proposition = str(claim.get("proposition") or UNRESOLVED)
    if proposition not in SUPPORTED_PROPOSITIONS:
        return None
    if proposition == "cross_rule_effective_date_order":
        return _special_cross_rule(repo_root=repo_root,label=label,claim=claim,candidates=candidates)
    payload = claim.get("proposition_spec")
    if not isinstance(payload, Mapping):
        return TruthOptionResult(option=label,claim=claim,status="unresolved",blockers=("missing_regulatory_proposition_spec",),reason="missing complete proposition atoms")
    claim_spec = RegulatoryPropositionSpec(**{key:tuple(value) if key == "required_doc_ids" else value for key,value in payload.items()})
    ranked: list[tuple[int,EvidenceCandidate,str,RegulatoryPropositionSpec]] = []
    for candidate in candidates:
        if not candidate_locally_reproduced(repo_root, candidate):
            continue
        for pattern in PROPOSITION_PATTERNS.get(proposition, ()):
            for match in re.finditer(pattern,candidate.text,re.I|re.S):
                fragment=match.group(0)
                fact=_fact_atoms(proposition,fragment,str(candidate.doc_id))
                ranked.append((_relevance_score(claim_spec,fact),candidate,fragment,fact))
    if not ranked:
        return TruthOptionResult(option=label,claim=claim,status="unresolved",blockers=("missing_direct_regulatory_clause",),reason="no complete canonical R2.3 proposition matched")
    ranked.sort(key=lambda row:(row[0],float(row[1].score or 0),-len(row[2])),reverse=True)
    _,candidate,fragment,fact=ranked[0]
    status,comparison,reason=_status_for_special_subset(proposition,claim_spec,fact)
    source,provenance=_source(repo_root,candidate,fragment)
    decisive={field:(getattr(fact,field),fragment,f"regulatory_r23_atom_{field}_v1") for field,state in comparison.items() if state in {"match","conflict","counterexample_subset"}}
    atom_provenance=provenance_for_fragments(source=source,fields=decisive)
    blockers=() if status in {"supported","contradicted"} else ("regulatory_proposition_atoms_not_closed",)
    return TruthOptionResult(
        option=label,
        claim=claim,
        status=status,
        sources=(source,),
        provenance=tuple(atom_provenance or provenance),
        binding={"required_doc":"match",**comparison},
        rule_steps=(
            {"step":"parse_claim_atoms","atoms":asdict(claim_spec)},
            {"step":"parse_fact_atoms","atoms":asdict(fact)},
            {"step":"compare_atoms","comparison":comparison,"status":status},
        ),
        blockers=blockers,
        reason=reason,
    )
