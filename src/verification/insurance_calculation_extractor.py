"""Dataset-neutral insurance calculation fact extraction.

Production inputs are limited to the insurance product/document catalog and the
corresponding authoritative contract text.  The extractor never consumes
question identifiers, option labels, complete option strings, expected answers,
or evaluator oracle results.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from verification.insurance_clause_extractor import (
    InsuranceProductCatalog,
    InsuranceProductDocument,
    load_insurance_product_catalog,
)


FORBIDDEN_PRODUCTION_KEYS = {
    "qid",
    "option",
    "option_label",
    "option_text",
    "expected_answer",
    "oracle_answer",
    "answer",
}


class FixedCalculationOracleRejected(ValueError):
    """Raised when a fixed-dataset oracle reaches a production input path."""


@dataclass(frozen=True)
class InsuranceCalculationFact:
    product_id: str
    document_id: str
    calculation_category: str
    normalized_relation: str
    normalized_value: Any
    unit: str
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
class CalculationExtractionRule:
    rule_id: str
    calculation_category: str
    normalized_relation: str
    pattern: str
    value_builder: Callable[[re.Match[str], str], Any]
    unit: str = ""
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    confidence_state: str = "source_exact"
    flags: int = re.S

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)


def _literal(value: Any) -> Callable[[re.Match[str], str], Any]:
    return lambda _match, _text: value


def _number(text: str) -> float:
    raw = str(text or "").strip().replace(",", "").replace("％", "%")
    multiplier = 1.0
    if raw.endswith("亿元"):
        multiplier, raw = 100_000_000.0, raw[:-2]
    elif raw.endswith("万元"):
        multiplier, raw = 10_000.0, raw[:-2]
    elif raw.endswith("万"):
        multiplier, raw = 10_000.0, raw[:-1]
    elif raw.endswith("元"):
        raw = raw[:-1]
    if raw.endswith("%"):
        return float(raw[:-1]) / 100.0
    return float(raw) * multiplier


def _captured_number(group: str = "value") -> Callable[[re.Match[str], str], Any]:
    return lambda match, _text: _number(match.group(group))


def _captured_ratio_schedule(match: re.Match[str], _text: str) -> dict[str, float]:
    return {
        "under_18": _number(match.group("r1")),
        "age_18_to_before_41": _number(match.group("r2")),
        "age_41_to_before_61": _number(match.group("r3")),
        "age_61_plus": _number(match.group("r4")),
    }


def _captured_surrender_schedule(match: re.Match[str], _text: str) -> dict[str, float]:
    values = [_number(match.group(name)) for name in ("y1", "y2", "y3", "y4", "y5", "y6")]
    return {
        "year_1": values[0],
        "year_2": values[1],
        "year_3": values[2],
        "year_4": values[3],
        "year_5": values[4],
        "year_6_plus": values[5],
    }


def _captured_plan_deductibles(match: re.Match[str], _text: str) -> dict[str, float]:
    return {
        "plan_1": _number(match.group("p1")),
        "plan_2": _number(match.group("p2")),
        "plan_3": _number(match.group("p3")),
        "plan_4": _number(match.group("p4")),
    }


def _captured_medical_ratios(match: re.Match[str], _text: str) -> dict[str, float]:
    return {
        "with_social_insurance_settlement": _number(match.group("with_ratio")),
        "without_social_insurance_settlement": _number(match.group("without_ratio")),
    }


def _covered_outpatient_categories(_match: re.Match[str], text: str) -> list[str]:
    categories: list[str] = []
    lookup = {
        "门诊肾透析": "renal_dialysis",
        "肾透析": "renal_dialysis",
        "门诊恶性肿瘤": "malignant_tumor_outpatient_treatment",
        "恶性肿瘤治疗": "malignant_tumor_outpatient_treatment",
        "器官移植后的门诊抗排异": "post_transplant_anti_rejection",
        "移植后抗排异": "post_transplant_anti_rejection",
        "门诊手术": "outpatient_surgery",
    }
    for phrase, canonical in lookup.items():
        if phrase in text and canonical not in categories:
            categories.append(canonical)
    return categories


EXTRACTION_RULES: tuple[CalculationExtractionRule, ...] = (
    CalculationExtractionRule(
        "death_before_annuity_account_value_v1",
        "death_benefit",
        "death_benefit_formula",
        r"养老保险金开始领取日之前身故.{0,80}?按.{0,30}?保单账户价值.{0,30}?给付身故保险金",
        _literal("account_value"),
        unit="formula",
        conditions=("before_annuity_start",),
    ),
    CalculationExtractionRule(
        "death_max_age_basic_or_account_v1",
        "death_benefit",
        "death_benefit_formula",
        r"身故保险金额为下列两者的较大值.{0,120}?身故给付比例与基本保险金额的乘积.{0,80}?个人账户价值",
        _literal("max(basic_amount * age_ratio, account_value)"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "death_age_ratio_schedule_v1",
        "death_benefit",
        "age_ratio_schedule",
        r"(?:至年满18周岁|未满18周岁).{0,140}?(?P<r1>100[%％]).{0,220}?年满18周岁.{0,100}?年满41周岁.{0,140}?(?P<r2>160[%％]).{0,220}?年满41周岁.{0,100}?年满61周岁.{0,140}?(?P<r3>140[%％]).{0,220}?年满61周岁.{0,140}?(?P<r4>120[%％])",
        _captured_ratio_schedule,
        unit="ratio_schedule",
    ),
    CalculationExtractionRule(
        "death_premium_less_annuity_or_cash_list_v1",
        "death_benefit",
        "death_benefit_formula",
        r"身故当时下列两者的较大值.{0,260}?所交保险费.{0,80}?减去.{0,80}?累计已给付的养老年金.{0,180}?现金价值",
        _literal("max(premium_paid - annuity_paid, cash_value)"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "death_premium_less_annuity_or_cash_inline_v1",
        "death_benefit",
        "death_benefit_formula",
        r"累计已交保险费扣除累计已给付养老保险金后的余额与本合同的现金价值的较大者",
        _literal("max(premium_paid - annuity_paid, cash_value)"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "death_premium_less_annuity_or_cash_v1",
        "death_benefit",
        "death_benefit_formula",
        r"(?:身故当时下列两者的较大值|累计已交保险费扣除累计已给付养老保险金后的余额与本合同的现金价值的较大者).{0,180}?(?:所交保险费|累计已交保险费).{0,40}?(?:减去|扣除).{0,60}?累计已给付.{0,40}?养老(?:保险)?金.{0,120}?现金价值",
        _literal("max(premium_paid - annuity_paid, cash_value)"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "surrender_year_6_to_10_premium_plus_return_v1",
        "cash_value_formula",
        "surrender_value_formula",
        r"第\s*6\s*个保单年度至第\s*10\s*个保单年度.{0,180}?现金价值等于以下两项金额之和.{0,120}?累计所交保险费.{0,120}?保单账户累计收益.{0,30}?(?P<value>75[%％])",
        lambda match, _text: f"premium_paid + cumulative_return * {_number(match.group('value'))}",
        unit="formula",
        conditions=("policy_year_6_to_10",),
    ),
    CalculationExtractionRule(
        "surrender_returns_cash_value_v1",
        "cash_value_formula",
        "surrender_value_formula",
        r"解除合同申请书.{0,180}?退还本合同的现金价值|现金价值.{0,80}?通常体现为解除合同时.{0,80}?退还的那部分金额",
        _literal("cash_value"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "surrender_account_less_charge_v1",
        "cash_value_formula",
        "surrender_value_formula",
        r"现金价值等于个人账户价值扣除相应的退保费用后的余额",
        _literal("account_value * (1 - surrender_charge_rate)"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "surrender_charge_schedule_v1",
        "surrender_charge_rate",
        "surrender_charge_schedule",
        r"第一年</td><td[^>]*>(?P<y1>[0-9.]+[%％]).{0,80}?第二年</td><td[^>]*>(?P<y2>[0-9.]+[%％]).{0,80}?第三年</td><td[^>]*>(?P<y3>[0-9.]+[%％]).{0,80}?第四年</td><td[^>]*>(?P<y4>[0-9.]+[%％]).{0,80}?第五年</td><td[^>]*>(?P<y5>[0-9.]+[%％]).{0,100}?第六年及以后</td><td[^>]*>(?P<y6>[0-9.]+[%％])",
        _captured_surrender_schedule,
        unit="ratio_schedule",
    ),
    CalculationExtractionRule(
        "medical_expense_after_offsets_v1",
        "medical_reimbursement",
        "medical_payment_formula",
        r"(?:保险金数额|应当给付的保险金)\s*=\s*[（(]?.{0,520}?(?:医疗费用|个人自行承担).{0,520}?-(?:约定的|未抵扣完毕的)?免赔额(?:余额)?.{0,160}?[）)]?\s*[×xX*].{0,80}?(?:赔付比例|100[%％]|60[%％])",
        _literal("max(covered_expense - social_insurance - other_compensation - deductible_remaining, 0) * reimbursement_ratio"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "medical_expense_after_offsets_clause_v1",
        "medical_reimbursement",
        "medical_payment_formula",
        r"扣除已从其他途径获得的医疗费用补偿以及约定的免赔额后.{0,80}?依照.{0,40}?给付比例进行赔付",
        _literal("max(covered_expense - other_compensation - deductible_remaining, 0) * reimbursement_ratio"),
        unit="formula",
    ),
    CalculationExtractionRule(
        "morphological_recurrence_multiplier_v1",
        "medical_reimbursement",
        "morphological_recurrence_multiplier",
        r"对于形态学复发.{0,220}?依照.{0,80}?给付比例进行赔付.{0,160}?对于MRD复发.{0,220}?给付比例再乘以\s*25[%％]",
        _literal(1.0),
        unit="ratio",
        conditions=("morphological_recurrence",),
    ),
    CalculationExtractionRule(
        "plan_one_family_shared_deductible_v1",
        "family_deductible",
        "deductible_scope",
        r"若选择投保计划一.{0,180}?同一保单中同时参保本保险同一计划的被保险人.{0,120}?个人自行承担",
        _literal("shared_by_same_policy_family_members"),
        unit="scope",
        conditions=("plan_1",),
    ),
    CalculationExtractionRule(
        "medical_plan_deductible_schedule_v1",
        "annual_deductible",
        "plan_deductible_schedule",
        r"计划一</td><td[^>]*>计划二</td><td[^>]*>计划三</td><td[^>]*>计划四</td>.{0,1800}?一般医疗保险金</td><td[^>]*>(?P<p1>[0-9.]+万元).{0,120}?共享.{0,80}?</td><td[^>]*>(?P<p2>[0-9.]+元).{0,80}?</td><td[^>]*>(?P<p3>[0-9.]+万元).{0,80}?</td><td[^>]*>(?P<p4>[0-9.]+元)",
        _captured_plan_deductibles,
        unit="CNY_schedule",
    ),
    CalculationExtractionRule(
        "medical_with_without_social_ratio_v1",
        "reimbursement_ratio",
        "social_insurance_ratio_schedule",
        r"一般赔付比例B为\s*(?P<with_ratio>100[%％]).{0,180}?未经基本医疗保险.{0,100}?一般赔付比例B为\s*(?P<without_ratio>60[%％])",
        _captured_medical_ratios,
        unit="ratio_schedule",
    ),
    CalculationExtractionRule(
        "medical_with_without_social_ratio_alt_v1",
        "reimbursement_ratio",
        "social_insurance_ratio_schedule",
        r"已经过公费医疗、基本医疗保险.{0,260}?[×xX*]\s*(?P<with_ratio>100[%％]).{0,180}?未经过公费医疗、基本医疗保险.{0,260}?[×xX*]\s*(?P<without_ratio>60[%％])",
        _captured_medical_ratios,
        unit="ratio_schedule",
    ),
    CalculationExtractionRule(
        "other_insurance_offset_v1",
        "payment_coordination_order",
        "other_insurance_offset",
        r"(?:其他途径|商业保险机构).{0,120}?(?:取得|获得).{0,40}?医疗费用补偿.{0,160}?(?:扣除|余额|最高给付金额不超过)",
        _literal(True),
        unit="boolean",
    ),
    CalculationExtractionRule(
        "deductible_non_social_compensation_offset_exact_v1",
        "annual_deductible",
        "other_compensation_can_offset_deductible",
        r"从基本医疗保险、公费医疗或城乡居民大病保险之外的其他途径获得的.{0,180}?医疗费用补偿",
        _literal(True),
        unit="boolean",
    ),
    CalculationExtractionRule(
        "deductible_other_compensation_offset_alt_v1",
        "annual_deductible",
        "other_compensation_can_offset_deductible",
        r"从其他途径已获得的医疗费用补偿可用于抵扣免赔额|基本医疗保险.{0,180}?之外的其他途径.{0,220}?补偿.{0,80}?抵扣免赔额",
        _literal(True),
        unit="boolean",
    ),
    CalculationExtractionRule(
        "deductible_other_compensation_offset_v1",
        "annual_deductible",
        "other_compensation_can_offset_deductible",
        r"(?:基本医疗保险|公费医疗).{0,100}?之外的其他途径.{0,120}?医疗费用补偿可抵扣免赔额",
        _literal(True),
        unit="boolean",
    ),
    CalculationExtractionRule(
        "designated_outpatient_categories_v1",
        "medical_expense_scope",
        "covered_outpatient_categories",
        r"(?:指定门诊急诊医疗保险金|指定门诊医疗费用).{0,1800}?(?:门诊肾透析|肾透析).{0,700}?(?:门诊肿瘤治疗|门诊恶性肿瘤治疗|恶性肿瘤治疗).{0,700}?(?:器官移植后的门诊抗排异|移植后的门诊抗排异).{0,700}?门诊手术",
        _covered_outpatient_categories,
        unit="category_list",
    ),
    CalculationExtractionRule(
        "property_actual_loss_less_deductible_v1",
        "property_loss_limit",
        "property_payment_formula",
        r"按照实际损失扣除免赔额后的金额计算赔偿.{0,80}?最高不得超过.{0,30}?保险(?:单|合同)载明的保险金额",
        _literal("min(max(actual_property_loss - deductible, 0), insured_amount)"),
        unit="formula",
    ),
)


def _reject_forbidden_keys(payload: Any, path: str = "root") -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_PRODUCTION_KEYS:
                raise FixedCalculationOracleRejected(f"forbidden fixed-dataset key at {path}.{key}")
            _reject_forbidden_keys(value, f"{path}.{key}")
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, value in enumerate(payload):
            _reject_forbidden_keys(value, f"{path}[{index}]")


def load_calculation_oracle(
    path: Path | str,
    *,
    allow_fixed_dataset_for_offline_evaluation: bool = False,
) -> Mapping[str, Any]:
    """Load an evaluator oracle only when the caller explicitly opts into offline mode."""
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload, Mapping) else {}
    is_fixed = metadata.get("FIXED_DATASET_REGRESSION_ONLY") == "YES"
    production_allowed = metadata.get("PRODUCTION_INPUT_ALLOWED") == "YES"
    if is_fixed and not allow_fixed_dataset_for_offline_evaluation:
        raise FixedCalculationOracleRejected("fixed insurance calculation oracle rejected in production mode")
    if not production_allowed and not allow_fixed_dataset_for_offline_evaluation:
        raise FixedCalculationOracleRejected("insurance calculation oracle is not production-approved")
    if not allow_fixed_dataset_for_offline_evaluation:
        _reject_forbidden_keys(payload)
    return payload


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _local_window(text: str, start: int, end: int, *, radius: int = 220) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right].strip()


def _source_text(root: Path, document: InsuranceProductDocument) -> tuple[Path, str, str]:
    path = root / document.source_relpath
    text = path.read_text(encoding="utf-8-sig")
    return path, text, sha256(path.read_bytes()).hexdigest()


def extract_insurance_calculation_facts(
    full_text_root: Path | str,
    *,
    catalog: InsuranceProductCatalog | None = None,
    product_catalog_path: Path | str | None = None,
    rules: Iterable[CalculationExtractionRule] = EXTRACTION_RULES,
) -> tuple[InsuranceCalculationFact, ...]:
    """Extract reusable calculation facts from every catalog document."""
    root = Path(full_text_root)
    resolved_catalog = catalog or load_insurance_product_catalog(product_catalog_path or "")
    facts: list[InsuranceCalculationFact] = []
    seen: set[tuple[str, str, str]] = set()
    for document in resolved_catalog.documents:
        source_path, text, source_hash = _source_text(root, document)
        for rule in rules:
            match = rule.compiled().search(text)
            if match is None:
                continue
            try:
                value = rule.value_builder(match, text)
                rejection_reasons: tuple[str, ...] = ()
                confidence = rule.confidence_state
            except Exception as exc:  # fail closed and preserve source lineage
                value = None
                rejection_reasons = (f"value_parse_error:{exc.__class__.__name__}",)
                confidence = "rejected"
            key = (document.document_id, rule.calculation_category, rule.normalized_relation)
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                InsuranceCalculationFact(
                    product_id=document.canonical_product_id,
                    document_id=document.document_id,
                    calculation_category=rule.calculation_category,
                    normalized_relation=rule.normalized_relation,
                    normalized_value=value,
                    unit=rule.unit,
                    conditions=rule.conditions,
                    exceptions=rule.exceptions,
                    source_relpath=document.source_relpath.replace("\\", "/"),
                    source_sha256=source_hash,
                    local_window=_local_window(text, match.start(), match.end()),
                    page_or_line=_line_number(text, match.start()),
                    extraction_rule_id=rule.rule_id,
                    confidence_state=confidence,
                    rejection_reasons=rejection_reasons,
                )
            )
    return tuple(sorted(facts, key=lambda item: (item.document_id, item.calculation_category, item.normalized_relation)))


def facts_to_dict(facts: Sequence[InsuranceCalculationFact]) -> dict[str, Any]:
    return {
        "schema_version": "insurance_calculation_facts.v1",
        "production_input_allowed": True,
        "fact_count": len(facts),
        "facts": [fact.to_dict() for fact in facts],
    }
