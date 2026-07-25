"""Dataset-neutral insurance product catalog and clause fact extraction.

Production inputs are limited to document identity metadata plus authoritative
contract text.  Question IDs, option labels, option strings, expected answers,
and curated verdicts are forbidden by validation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


FORBIDDEN_PRODUCTION_KEYS = {
    "qid",
    "option",
    "option_label",
    "option_text",
    "verdict",
    "expected_answer",
    "oracle_answer",
    "answer",
    "template_id",
    "claim_templates",
    "fact_seeds",
}


class FixedDatasetRegistryRejected(ValueError):
    """Raised when a curated fixed-dataset fixture reaches production mode."""


@dataclass(frozen=True)
class InsuranceProductDocument:
    document_id: str
    canonical_product_id: str
    product_name: str
    product_type: str
    insurer: str
    source_relpath: str
    aliases: tuple[str, ...]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "InsuranceProductDocument":
        return cls(
            document_id=str(row.get("document_id") or ""),
            canonical_product_id=str(row.get("canonical_product_id") or ""),
            product_name=str(row.get("product_name") or ""),
            product_type=str(row.get("product_type") or ""),
            insurer=str(row.get("insurer") or ""),
            source_relpath=str(row.get("source_relpath") or ""),
            aliases=tuple(
                str(value).strip()
                for value in row.get("aliases") or []
                if str(value).strip()
            ),
        )


@dataclass(frozen=True)
class InsuranceProductCatalog:
    metadata: Mapping[str, Any]
    documents: tuple[InsuranceProductDocument, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InsuranceProductCatalog":
        _reject_forbidden_keys(payload)
        metadata = dict(payload.get("metadata") or {})
        if metadata.get("PRODUCTION_INPUT_ALLOWED") != "YES":
            raise ValueError("insurance product catalog is not approved for production input")
        documents = tuple(
            InsuranceProductDocument.from_mapping(row)
            for row in payload.get("documents") or []
        )
        if not documents:
            raise ValueError("insurance product catalog has no documents")
        doc_ids = [row.document_id for row in documents]
        product_ids = [row.canonical_product_id for row in documents]
        if len(set(doc_ids)) != len(doc_ids):
            raise ValueError("duplicate insurance catalog document_id")
        if len(set(product_ids)) != len(product_ids):
            raise ValueError("duplicate insurance catalog product_id")
        for row in documents:
            if not all((
                row.document_id,
                row.canonical_product_id,
                row.product_name,
                row.source_relpath,
                row.aliases,
            )):
                raise ValueError(f"incomplete insurance product document: {row.document_id}")
        return cls(metadata=metadata, documents=documents)

    def document(self, document_id: str) -> InsuranceProductDocument:
        for row in self.documents:
            if row.document_id == str(document_id):
                return row
        raise KeyError(str(document_id))

    def product(self, product_id: str) -> InsuranceProductDocument:
        for row in self.documents:
            if row.canonical_product_id == str(product_id):
                return row
        raise KeyError(str(product_id))

    def match_products(self, text: str) -> tuple[InsuranceProductDocument, ...]:
        compact = _compact(text)
        matches: list[InsuranceProductDocument] = []
        for row in self.documents:
            aliases = (row.product_name, *row.aliases)
            if any(_compact(alias) and _compact(alias) in compact for alias in aliases):
                matches.append(row)
        matches.sort(key=lambda row: len(_compact(row.product_name)), reverse=True)
        return tuple(matches)


@dataclass(frozen=True)
class AutoInsuranceClauseFact:
    fact_id: str
    product_id: str
    document_id: str
    product_type: str
    clause_category: str
    normalized_relation: str
    normalized_value: Any
    conditions: tuple[str, ...]
    exceptions: tuple[str, ...]
    source_relpath: str
    source_sha256: str
    local_window: str
    page_or_line: int
    extraction_rule_id: str
    confidence_state: str
    rejection_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionRule:
    rule_id: str
    clause_category: str
    normalized_relation: str
    normalized_value: Any
    pattern: str
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    flags: int = 0

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)


# These rules describe recurring insurance clause language.  They are applied
# uniformly to every catalog document and never inspect question IDs/options.
EXTRACTION_RULES: tuple[ExtractionRule, ...] = (
    ExtractionRule(
        "waiting_period_accident_exception_v1", "waiting_period",
        "waiting_period_accident_exception", True,
        r"因意外伤害.{0,30}无等待期",
        conditions=("accident",),
    ),
    ExtractionRule(
        "waiting_period_no_liability_v1", "waiting_period",
        "waiting_period_liability", "no_liability",
        r"等待期内.{0,80}(不承担给付保险金的责任|不承担保险责任)",
        conditions=("within_waiting_period",),
    ),
    ExtractionRule(
        "waiting_period_days_v1", "waiting_period",
        "waiting_period_days", "capture_days",
        r"(?:生效|恢复).{0,25}?([0-9]{1,3})\s*日内.{0,15}等待期",
    ),
    ExtractionRule(
        "waiting_period_after_non_accident_v1", "waiting_period",
        "non_accident_coverage_timing", "after_waiting_period",
        r"等待期后因意外伤害以外的原因",
        conditions=("non_accident",),
    ),
    ExtractionRule(
        "exclusion_drunk_driving_v1", "exclusion",
        "excluded_event", "drunk_driving",
        r"被保险人酒后驾驶.{0,20}机动车",
    ),
    ExtractionRule(
        "exclusion_hiv_exception_v1", "exclusion",
        "excluded_event", "hiv_except_listed_cases",
        r"感染艾滋病病毒或患艾滋病.{0,80}不包括.{0,20}输血.{0,20}职业.{0,20}器官移植",
        exceptions=("blood_transfusion", "occupational_exposure", "organ_transplant"),
    ),
    ExtractionRule(
        "exclusion_unqualified_vaccination_unit_v1", "exclusion",
        "excluded_event", "unqualified_vaccination_unit",
        r"不具有卫生主管部门要求的预防接种条件的单位接种疫苗",
    ),
    ExtractionRule(
        "exclusion_expired_food_v1", "exclusion",
        "excluded_event", "expired_food",
        r"食品超过规定的保质期限",
    ),
    ExtractionRule(
        "exclusion_suicide_two_year_v1", "exclusion",
        "suicide_exception_period_years", 2,
        r"(?:成立|效力恢复).{0,18}?2\s*年内自杀.{0,50}?无民事行为能力人的除外",
        conditions=("suicide",), exceptions=("no_civil_capacity",),
    ),
    ExtractionRule(
        "exclusion_suicide_generic_v1", "exclusion",
        "suicide_exclusion", True,
        r"(?:故意自致伤害|故意自伤|自杀).{0,80}(?:不承担|责任免除)|(?:责任免除).{0,100}(?:故意自致伤害|故意自伤|自杀)|(?:故意自致伤害或自杀)",
        conditions=("suicide",),
    ),
    ExtractionRule(
        "policy_loan_allowed_v1", "policy_loan",
        "policy_loan_allowed", True,
        r"(?:可以|可|您可).{0,20}(?:申请|办理)(?:保单|保险单)?(?:贷款|借款)|(?:申请|办理)(?:保单|保险单)?(?:贷款|借款)功能",
    ),
    ExtractionRule(
        "policy_loan_ratio_net_cash_v1", "policy_loan",
        "policy_loan_limit_ratio", 0.8,
        r"最高(?:借款|贷款)金额.{0,50}?现金价值.{0,70}?(?:扣除|欠交|欠款).{0,60}?(?:80%|百分之八十)",
        conditions=("net_cash_value_after_debt",),
    ),
    ExtractionRule(
        "policy_loan_ratio_cash_v1", "policy_loan",
        "policy_loan_limit_ratio", 0.8,
        r"(?:贷款|借款)金额不得超过.{0,30}?现金价值的\s*80%",
        conditions=("gross_cash_value",),
    ),
    ExtractionRule(
        "policy_loan_personal_pension_prohibition_v1", "policy_loan",
        "policy_loan_conditional_prohibition", True,
        r"个人养老金制度.{0,40}不接受保单贷款申请",
        conditions=("personal_pension_mode",),
    ),
    ExtractionRule(
        "drug_designated_pharmacy_v1", "drug_benefit",
        "designated_pharmacy_required", True,
        r"指定(?:医疗机构或)?药店.{0,100}(?:特定药品|药品)|(?:特定药品|药品).{0,100}指定(?:医疗机构或)?药店",
    ),
    ExtractionRule(
        "drug_prescription_review_v1", "drug_benefit",
        "prescription_review_required", True,
        r"(?:药品)?处方.{0,30}(?:需|需要|须).{0,20}(?:审核|经我们.*审核)|通过处方审核|药品处方审核未通过.{0,30}不承担",
    ),
    ExtractionRule(
        "drug_direct_settlement_v1", "drug_benefit",
        "direct_settlement_required", True,
        r"与(?:指定的?)?药店直接结算|指定药店.{0,60}直接结算",
    ),
    ExtractionRule(
        "drug_limited_outpatient_scope_v1", "drug_benefit",
        "outpatient_drug_scope", "listed_or_specific_only",
        r"(?:院外恶性肿瘤特定|院外指定直付|特定药品|门诊恶性肿瘤治疗费)",
    ),
    ExtractionRule(
        "event_vaccination_trigger_v1", "event_trigger",
        "covered_event", "vaccination",
        r"预防接种单位接种.{0,60}(?:异常反应|意外伤害|保险金)",
    ),
    ExtractionRule(
        "event_transport_passenger_trigger_v1", "event_trigger",
        "covered_event", "commercial_transport_passenger",
        r"持有效客票.{0,70}乘坐.{0,30}营运交通工具",
    ),
    ExtractionRule(
        "vehicle_onboard_liability_v1", "benefit_scope",
        "benefit_scope", "onboard_person_liability",
        r"使用被保险机动车过程中发生意外事故.{0,80}车上人员遭受人身伤亡",
    ),
    ExtractionRule(
        "cash_value_formula_ratio_v1", "cash_value",
        "cash_value_definition_type", "formula",
        r"现金价值按以下方法计算|现金价值等于.{0,80}(?:乘以|之和|差额)",
    ),
    ExtractionRule(
        "cash_value_formula_account_minus_fee_v1", "cash_value",
        "cash_value_definition_type", "formula",
        r"现金价值等于个人账户价值.{0,20}扣除.{0,30}退保费用",
        conditions=("account_value_minus_surrender_charge",),
    ),
    ExtractionRule(
        "cash_value_listed_only_v1", "cash_value",
        "cash_value_definition_type", "listed_only",
        r"保单年度末的现金价值.{0,20}(?:保险合同|保险单)上载明",
    ),
    ExtractionRule(
        "benefit_leukemia_recurrence_v1", "benefit_scope",
        "benefit_scope", "leukemia_recurrence_medical",
        r"急性白血病首次复发.{0,180}(?:住院医疗费用|医疗保险金)",
        conditions=("leukemia_recurrence",),
    ),
    ExtractionRule(
        "benefit_hospital_medical_v1", "benefit_scope",
        "benefit_scope", "hospital_medical",
        r"一般医疗保险金.{0,80}(?:包含|必选责任).{0,40}住院医疗保险金|一般医疗保险金（必选责任）",
    ),
    ExtractionRule(
        "benefit_death_disability_only_v1", "benefit_scope",
        "benefit_scope", "death_or_disability_only",
        r"营运交通工具.{0,100}意外伤害.{0,60}导致身故或伤残",
    ),
    ExtractionRule(
        "suspension_no_liability_v1", "suspension",
        "suspension_effect", "no_liability",
        r"效力中止期间.{0,30}(?:不承担保险责任|不承担给付保险金的责任)",
    ),
    ExtractionRule(
        "annuity_change_age_mode_v1", "annuity_change",
        "annuity_change_right", "age_and_mode_before_start",
        r"开始领取日前.{0,100}可以申请变更.{0,40}领取年龄.{0,60}领取方式",
    ),
    ExtractionRule(
        "annuity_start_not_changeable_v1", "annuity_change",
        "annuity_change_right", "start_not_changeable",
        r"养老年金开始领取日一经确定.{0,40}(?:不得|不允许).{0,20}变更",
    ),
    ExtractionRule(
        "annuity_change_age_v1", "annuity_change",
        "annuity_change_right", "age_before_start",
        r"申请变更养老保险金领取年龄",
    ),
    ExtractionRule(
        "annuity_change_mode_v1", "annuity_change",
        "annuity_change_right", "mode_before_start",
        r"申请变更养老保险金领取方式",
    ),
    ExtractionRule(
        "annuity_change_period_v1", "annuity_change",
        "annuity_change_right", "period_before_start",
        r"申请变更养老保险金.{0,30}领取期间",
    ),
    ExtractionRule(
        "rescue_expense_cap_v1", "rescue_expense",
        "rescue_expense_cap", "insured_amount",
        r"施救费用.{0,100}最高不超过保险金额",
    ),
    ExtractionRule(
        "death_benefit_v1", "benefit_scope",
        "benefit_scope", "death_benefit",
        r"(?:提供身故保障|给付身故保险金)",
    ),
    ExtractionRule(
        "accidental_disability_v1", "benefit_scope",
        "benefit_scope", "accidental_disability",
        r"意外伤残保险(?:责任|金)",
    ),
    ExtractionRule(
        "fire_property_loss_v1", "benefit_scope",
        "benefit_scope", "fire_property_loss",
        r"(?:保险责任|承保风险).{0,100}火灾、爆炸|\（一\）火灾、爆炸",
    ),
    ExtractionRule(
        "deductible_pool_account_not_offset_v1", "deductible",
        "deductible_offset_source", "pool_account_not_offset",
        r"基本医疗保险统筹账户.{0,100}(?:不属于|不得|不可).{0,40}免赔额|免赔额.{0,100}基本医疗保险统筹账户.{0,80}(?:不抵扣|不可抵扣)",
    ),
    ExtractionRule(
        "deductible_personal_account_offset_v1", "deductible",
        "deductible_offset_source", "personal_account_can_offset",
        r"基本医疗保险个人账户支出.{0,120}(?:抵扣|计入).{0,30}免赔额|免赔额.{0,160}基本医疗保险个人账户|个人账户支出的医疗费用.{0,120}可抵扣免赔额",
    ),
    ExtractionRule(
        "deductible_other_compensation_offset_v1", "deductible",
        "deductible_offset_source", "other_compensation_can_offset",
        r"其他途径已获得的医疗费用补偿.{0,60}(?:抵扣|扣除).{0,30}免赔额|可用于抵扣免赔额",
    ),
    ExtractionRule(
        "fixed_sum_no_deductible_v1", "deductible",
        "deductible_structure", "fixed_sum_no_deductible",
        r"按照本合同基本保险金额给付.{0,30}重大疾病保险金",
    ),
    ExtractionRule(
        "hesitation_full_refund_v1", "hesitation_period",
        "hesitation_period_refund", "full_premium_refund",
        r"犹豫期.{0,120}(?:退还|退还已收|全额退还).{0,30}全部保险费|签收保险单后.{0,20}日内.{0,120}退还.{0,30}全部保险费",
    ),
)


def _compact(value: Any) -> str:
    text = re.sub(r"<sup[^>]*>.*?</sup>", "", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("％", "%").replace("百分之八十", "80%")
    return re.sub(r"\s+", "", text).lower()


def _reject_forbidden_keys(payload: Any, path: str = "$.") -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_PRODUCTION_KEYS:
                raise ValueError(f"forbidden insurance production key: {path}{key}")
            _reject_forbidden_keys(value, f"{path}{key}.")
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, value in enumerate(payload):
            _reject_forbidden_keys(value, f"{path}[{index}].")


def reject_curated_fixture_metadata(
    metadata: Mapping[str, Any],
    *,
    allow_curated_fixture_for_offline_evaluation: bool = False,
) -> None:
    fixed = str(metadata.get("FIXED_DATASET_REGRESSION_ONLY") or "").upper() == "YES"
    not_auto = str(metadata.get("PRODUCTION_AUTO_EXTRACTED") or "").upper() == "NO"
    production_forbidden = str(metadata.get("PRODUCTION_INPUT_ALLOWED") or "").upper() == "NO"
    curated = str(metadata.get("CURATED_EVALUATOR_ORACLE") or "").upper() == "YES"
    if (fixed or not_auto or production_forbidden or curated) and not allow_curated_fixture_for_offline_evaluation:
        raise FixedDatasetRegistryRejected("fixed_dataset_registry_rejected")


def load_insurance_product_catalog(path: str | Path) -> InsuranceProductCatalog:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("insurance product catalog must be a mapping")
    return InsuranceProductCatalog.from_mapping(payload)


def _normalized_value(rule: ExtractionRule, match: re.Match[str]) -> Any:
    if rule.normalized_value != "capture_days":
        return rule.normalized_value
    return int(match.group(1))


def _line_windows(lines: Sequence[str], radius: int = 2) -> Iterable[tuple[int, str]]:
    for index in range(len(lines)):
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)
        yield index, "\n".join(lines[start:end]).strip()


def extract_insurance_clause_facts(
    full_text_root: str | Path,
    catalog: InsuranceProductCatalog,
) -> tuple[AutoInsuranceClauseFact, ...]:
    root = Path(full_text_root).resolve()
    results: list[AutoInsuranceClauseFact] = []
    seen: set[tuple[str, str, str, int]] = set()
    compiled = [(rule, rule.compiled()) for rule in EXTRACTION_RULES]
    for document in catalog.documents:
        path = root / document.source_relpath
        data = path.read_bytes()
        text = data.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        digest = sha256(data).hexdigest()
        for index, window in _line_windows(lines):
            compact_window = _compact(window)
            for rule, pattern in compiled:
                match = pattern.search(compact_window)
                if match is None:
                    continue
                value = _normalized_value(rule, match)
                key = (
                    document.document_id,
                    rule.normalized_relation,
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    index,
                )
                if key in seen:
                    continue
                seen.add(key)
                fact_id = (
                    f"auto:{document.document_id}:{rule.normalized_relation}:"
                    f"{index + 1}:{len(results) + 1}"
                )
                results.append(AutoInsuranceClauseFact(
                    fact_id=fact_id,
                    product_id=document.canonical_product_id,
                    document_id=document.document_id,
                    product_type=document.product_type,
                    clause_category=rule.clause_category,
                    normalized_relation=rule.normalized_relation,
                    normalized_value=value,
                    conditions=rule.conditions,
                    exceptions=rule.exceptions,
                    source_relpath=document.source_relpath,
                    source_sha256=digest,
                    local_window=window,
                    page_or_line=index + 1,
                    extraction_rule_id=rule.rule_id,
                    confidence_state="direct_clause_rule_match",
                    rejection_reasons=(),
                ))
    return tuple(results)


def catalog_to_dict(catalog: InsuranceProductCatalog) -> dict[str, Any]:
    return {
        "metadata": dict(catalog.metadata),
        "documents": [asdict(row) for row in catalog.documents],
    }
