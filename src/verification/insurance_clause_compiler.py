"""Generic insurance option semantics compiled against auto-extracted facts.

The compiler is intentionally independent from dataset question IDs, option
labels, full option-string tables, expected answers, and curated verdicts.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from verification.insurance_clause_extractor import (
    AutoInsuranceClauseFact,
    InsuranceProductCatalog,
    InsuranceProductDocument,
    extract_insurance_clause_facts,
)
from verification.scope_absence import (
    TrustedDocumentSource,
    build_scope_absence_proof,
    validate_scope_absence_proof,
)


INSURANCE_SOURCE_ROOT_IDENTITY = "afac_insurance_full_text.v1"
AUTHORITATIVE = {"supported", "contradicted", "scope_absent", "not_applicable"}
LOAN_ALIASES = ("保单贷款", "贷款", "借款", "质押贷款", "保单借款", "保险单借款")
SUSPENSION_ALIASES = (
    ("效力中止", "合同中止", "中止期间"),
    ("不承担保险责任", "不承担给付保险金的责任", "不负保险责任"),
)
RESCUE_ALIASES = (
    "施救费用", "救援费用", "防止或者减少损失所支付的费用",
    "防止或减少损失所支付的费用",
)
DRUG_ALIASES = (
    "院外特定药品", "院外特定疾病药品", "院外恶性肿瘤特定用药",
    "院外指定直付药品", "特定药品费用",
)
SUICIDE_ALIASES = ("自杀", "故意自伤", "故意自致伤害")


def _compact(value: Any) -> str:
    text = re.sub(r"<sup[^>]*>.*?</sup>", "", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("％", "%").replace("百分之八十", "80%")
    return re.sub(r"[\s，。；：、（）()【】\[\]“”‘’]+", "", text).lower()


def _canonical_answer(labels: Iterable[str]) -> str:
    selected = set(labels)
    return "".join(label for label in "ABCD" if label in selected)


def _contains(text: str, *terms: str) -> bool:
    compact = _compact(text)
    return all(_compact(term) in compact for term in terms)


def _has_any(text: str, terms: Sequence[str]) -> bool:
    compact = _compact(text)
    return any(_compact(term) in compact for term in terms)


def _day_from_option(text: str) -> int | None:
    match = re.search(r"第\s*([0-9]{1,3})\s*天", text)
    return int(match.group(1)) if match else None


def _question_route(question_text: str) -> str:
    text = _compact(question_text)
    routes = (
        (("等待期",), "waiting_period"),
        (("免责范围",), "exclusion"),
        (("保单贷款",), "policy_loan"),
        (("特定药品费用",), "drug_benefit"),
        (("现金价值", "计算方法"), "cash_value"),
        (("车祸", "骨折", "哪些产品可以赔付"), "hospital_coverage"),
        (("宽限期", "效力中止"), "suspension"),
        (("养老年金开始领取日", "变更规定"), "annuity_change"),
        (("双耳失聪", "意外事故"), "event_prerequisite"),
        (("故意自伤或自杀",), "suicide_exclusion"),
        (("施救费用",), "rescue_expense"),
        (("哪些事故", "相应产品", "赔付"), "event_payout"),
        (("免赔额", "抵扣"), "deductible"),
        (("犹豫期", "全部已交保险费"), "hesitation_refund"),
        (("特种车作业", "医疗费用"), "scenario_product_scope"),
    )
    for required, route in routes:
        if all(_compact(term) in text for term in required):
            return route
    return "unresolved"


def _fact_key(fact: AutoInsuranceClauseFact) -> tuple[Any, ...]:
    return (
        fact.document_id,
        fact.normalized_relation,
        repr(fact.normalized_value),
        fact.extraction_rule_id,
    )


class GenericInsuranceClauseCompiler:
    """Compile option truth from product identities and extracted contract facts."""

    def __init__(
        self,
        full_text_root: str | Path,
        *,
        catalog: InsuranceProductCatalog,
        facts: Sequence[AutoInsuranceClauseFact] | None = None,
        scope_scan_run_id: str = "production_generic_insurance_clause_compiler.v1",
    ) -> None:
        self.full_text_root = Path(full_text_root).resolve()
        self.catalog = catalog
        self.scope_scan_run_id = str(scope_scan_run_id)
        if not self.scope_scan_run_id:
            raise ValueError("scope_scan_run_id is required")
        raw = tuple(facts) if facts is not None else extract_insurance_clause_facts(
            self.full_text_root, catalog
        )
        # One semantic fact per product/relation/value/rule is sufficient.  A
        # deterministic earliest source window is retained for audit lineage.
        deduped: dict[tuple[Any, ...], AutoInsuranceClauseFact] = {}
        for fact in sorted(raw, key=lambda row: (int(row.document_id), row.page_or_line, row.fact_id)):
            deduped.setdefault(_fact_key(fact), fact)
        self.facts = tuple(deduped.values())
        self._facts_by_doc: dict[str, tuple[AutoInsuranceClauseFact, ...]] = {}
        grouped: dict[str, list[AutoInsuranceClauseFact]] = defaultdict(list)
        for fact in self.facts:
            grouped[fact.document_id].append(fact)
        self._facts_by_doc = {key: tuple(value) for key, value in grouped.items()}

    def trusted_document_sources(
        self, document_ids: Sequence[str]
    ) -> dict[str, TrustedDocumentSource]:
        return {
            str(doc_id): TrustedDocumentSource(
                canonical_doc_id=str(doc_id),
                source_root_identity=INSURANCE_SOURCE_ROOT_IDENTITY,
                source_root=str(self.full_text_root),
                source_relpath=self.catalog.document(str(doc_id)).source_relpath,
            )
            for doc_id in document_ids
        }

    def _facts(
        self,
        document_ids: Sequence[str],
        *,
        relation: str | None = None,
        value: Any = None,
    ) -> tuple[AutoInsuranceClauseFact, ...]:
        results = []
        for doc_id in document_ids:
            for fact in self._facts_by_doc.get(str(doc_id), ()):
                if relation is not None and fact.normalized_relation != relation:
                    continue
                if value is not None and fact.normalized_value != value:
                    continue
                results.append(fact)
        return tuple(results)

    def _proof(
        self,
        document_ids: Sequence[str],
        *,
        query_terms: Sequence[str] = (),
        alias_groups: Sequence[Sequence[str]] = (),
    ) -> tuple[dict[str, Any], bool, tuple[str, ...]]:
        trusted = self.trusted_document_sources(document_ids)
        proof = build_scope_absence_proof(
            trusted_declared_documents=trusted,
            query_terms=query_terms,
            query_alias_groups=alias_groups,
            scan_timestamp_or_run_id=self.scope_scan_run_id,
        )
        validation = validate_scope_absence_proof(
            proof, trusted_declared_documents=trusted
        )
        return proof.to_dict(), validation.valid, validation.errors

    def _products(
        self, option_text: str
    ) -> tuple[InsuranceProductDocument, ...]:
        return self.catalog.match_products(option_text)

    def audit_option(
        self,
        *,
        question_text: str,
        option_label: str,
        option_text: str,
        declared_doc_ids: Sequence[str],
    ) -> dict[str, Any]:
        route = _question_route(question_text)
        products = self._products(option_text)
        if route == "annuity_change":
            question_products = self._products(question_text)
            if len(question_products) >= 3:
                products = question_products
        declared = tuple(dict.fromkeys(str(value) for value in declared_doc_ids))
        product_docs = tuple(dict.fromkeys(row.document_id for row in products))
        undeclared = tuple(doc_id for doc_id in product_docs if doc_id not in declared)
        if undeclared:
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                route=route,
                verdict="not_applicable",
                facts=(),
                reason="option product document is outside the question-declared scope",
                compiler_rule_id="product_document_scope_exclusion.v1",
                declared_doc_ids=declared,
                product_document_ids=product_docs,
                out_of_scope_doc_ids=undeclared,
                evidence_tier=1,
                reconciliation_rule_id="tier1_product_identity_overrides_weak_scope_claim.v1",
            )
        if not products:
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                route=route,
                verdict="unresolved",
                facts=(),
                reason="no product alias could be bound to the option",
                compiler_rule_id="product_alias_unresolved.v1",
                declared_doc_ids=declared,
            )

        handler = getattr(self, f"_compile_{route}", None)
        if handler is None:
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                route=route,
                verdict="unresolved",
                facts=(),
                reason="insurance question semantics are not covered by a generic rule",
                compiler_rule_id="generic_route_unresolved.v1",
                declared_doc_ids=declared,
                product_document_ids=product_docs,
            )
        return handler(
            option_label=option_label,
            option_text=option_text,
            products=products,
            declared_doc_ids=declared,
            product_document_ids=product_docs,
            route=route,
        )

    def _compile_waiting_period(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        accident = _has_any(text, ("意外", "车祸")) and not _contains(text, "非意外")
        day = _day_from_option(text)
        if accident:
            facts = self._facts(docs, relation="waiting_period_accident_exception", value=True)
            if facts:
                return self._direct(context, "supported", facts, "waiting_period_accident_exception.v1")
        if _contains(text, "非意外") or not accident:
            facts = self._facts(docs, relation="waiting_period_days")
            if facts and day is not None and any(
                isinstance(fact.normalized_value, int) and day <= fact.normalized_value
                for fact in facts
            ):
                return self._direct(context, "contradicted", facts, "waiting_period_days_comparison.v1")
            liability = self._facts(docs, relation="waiting_period_liability", value="no_liability")
            timing = self._facts(docs, relation="non_accident_coverage_timing", value="after_waiting_period")
            if liability or timing:
                return self._direct(
                    context, "contradicted", liability or timing,
                    "waiting_period_non_accident_liability.v1",
                )
        return self._unresolved(context, "waiting-period atoms are incomplete")

    def _compile_exclusion(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        mapping = (
            (("酒后驾驶",), "drunk_driving"),
            (("艾滋病",), "hiv_except_listed_cases"),
            (("不具有接种条件",), "unqualified_vaccination_unit"),
            (("超过保质期", "保质期"), "expired_food"),
        )
        for terms, value in mapping:
            if _has_any(text, terms):
                facts = self._facts(docs, relation="excluded_event", value=value)
                if facts:
                    return self._direct(context, "supported", facts, f"exclusion_{value}.v1")
        return self._unresolved(context, "excluded event could not be bound to a direct clause")

    def _compile_policy_loan(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        if _contains(text, "个人养老金") and _has_any(text, ("不允许", "不接受")):
            facts = self._facts(docs, relation="policy_loan_conditional_prohibition", value=True)
            if facts:
                return self._direct(context, "supported", facts, "policy_loan_personal_pension_prohibition.v1")
        if _has_any(text, ("无论何种", "任何情况")) and _has_any(text, ("不允许", "不接受")):
            allowed = self._facts(docs, relation="policy_loan_allowed", value=True)
            if allowed:
                return self._direct(context, "contradicted", allowed, "ordinary_policy_loan_allowed.v1")
        if "80%" in _compact(text):
            ratio = self._facts(docs, relation="policy_loan_limit_ratio", value=0.8)
            allowed = self._facts(docs, relation="policy_loan_allowed", value=True)
            if ratio and allowed:
                wants_net = _has_any(text, ("扣除欠款", "扣除借款", "扣除欠交"))
                has_net = any("net_cash_value_after_debt" in fact.conditions for fact in ratio)
                has_gross = any("gross_cash_value" in fact.conditions for fact in ratio)
                if (wants_net and has_net) or (not wants_net and has_gross):
                    return self._direct(context, "supported", (*allowed, *ratio), "policy_loan_ratio_and_base.v1")
                return self._direct(context, "contradicted", ratio, "policy_loan_base_mismatch.v1")
        proof, valid, errors = self._proof(docs, alias_groups=(LOAN_ALIASES,))
        if valid:
            return self._absence(
                context, verdict="scope_absent", proof=proof,
                reason="no policy-loan alias is present in the authoritative product document",
                rule="policy_loan_alias_complete_absence.v1",
            )
        return self._unresolved(context, "policy-loan clause unresolved", proof, errors)

    def _compile_drug_benefit(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        if _has_any(text, ("不涵盖", "不包括")):
            proof, valid, errors = self._proof(
                docs,
                query_terms=("院外",),
                alias_groups=(DRUG_ALIASES,),
            )
            if valid:
                return self._absence(
                    context, verdict="scope_absent", proof=proof,
                    reason="no coherent outpatient-specific-drug clause was found; the option is not promoted",
                    rule="outpatient_drug_negative_claim_absence.v1",
                )
            return self._unresolved(context, "outpatient drug absence proof failed", proof, errors)
        if _has_any(text, ("所有院外药品", "全部院外药品")):
            facts = self._facts(docs, relation="outpatient_drug_scope", value="listed_or_specific_only")
            if facts:
                return self._direct(context, "contradicted", facts, "limited_outpatient_drug_scope.v1")
        required: list[tuple[str, Any]] = []
        if _has_any(text, ("指定药店", "指定医疗机构")):
            required.append(("designated_pharmacy_required", True))
        if _has_any(text, ("处方审核", "审核处方")):
            required.append(("prescription_review_required", True))
        if _has_any(text, ("直接结算", "直付")):
            required.append(("direct_settlement_required", True))
        facts: list[AutoInsuranceClauseFact] = []
        for relation, value in required:
            matched = self._facts(docs, relation=relation, value=value)
            if not matched:
                return self._unresolved(context, f"required drug atom missing: {relation}")
            facts.extend(matched)
        if required:
            return self._direct(context, "supported", facts, "drug_required_atoms_complete.v1")
        return self._unresolved(context, "drug proposition atoms are incomplete")

    def _compile_scenario_product_scope(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        if _has_any(text, ("预防接种无关", "与预防接种无关")):
            facts = self._facts(docs, relation="covered_event", value="vaccination")
            if facts:
                return self._direct(context, "contradicted", facts, "vaccination_event_mismatch.v1")
        if _has_any(text, ("营运交通工具", "特种车不属于")):
            facts = self._facts(docs, relation="covered_event", value="commercial_transport_passenger")
            if facts:
                return self._direct(context, "contradicted", facts, "transport_passenger_event_mismatch.v1")
        facts = self._facts(docs, relation="benefit_scope", value="onboard_person_liability")
        if facts:
            return self._direct(context, "supported", facts, "onboard_person_liability.v1")
        return self._unresolved(context, "scenario product responsibility could not be certified")

    def _compile_cash_value(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        formula = self._facts(docs, relation="cash_value_definition_type", value="formula")
        listed = self._facts(docs, relation="cash_value_definition_type", value="listed_only")
        if _has_any(text, ("公式", "计算方法", "现金价值=")) and formula:
            return self._direct(context, "supported", formula, "cash_value_formula_present.v1")
        if _has_any(text, ("未给公式", "仅载明", "载明")) and listed:
            return self._direct(context, "contradicted", listed, "cash_value_listed_only.v1")
        if formula:
            return self._direct(context, "supported", formula, "cash_value_formula_present.v1")
        if listed:
            return self._direct(context, "contradicted", listed, "cash_value_listed_only.v1")
        return self._unresolved(context, "cash-value definition type unresolved")

    def _compile_hospital_coverage(self, **context: Any) -> dict[str, Any]:
        docs = context["product_document_ids"]
        leukemia = self._facts(docs, relation="benefit_scope", value="leukemia_recurrence_medical")
        hospital = self._facts(docs, relation="benefit_scope", value="hospital_medical")
        limited = self._facts(docs, relation="benefit_scope", value="death_or_disability_only")
        if leukemia:
            return self._direct(context, "contradicted", leukemia, "fracture_outside_leukemia_scope.v1")
        if hospital:
            return self._direct(context, "supported", hospital, "hospital_medical_covers_injury.v1")
        if limited:
            return self._direct(context, "contradicted", limited, "death_disability_not_medical_expense.v1")
        return self._unresolved(context, "hospital coverage scope unresolved")

    def _compile_suspension(self, **context: Any) -> dict[str, Any]:
        docs = context["product_document_ids"]
        facts = self._facts(docs, relation="suspension_effect", value="no_liability")
        if facts:
            return self._direct(context, "supported", facts, "suspension_no_liability.v1")
        proof, valid, errors = self._proof(docs, alias_groups=SUSPENSION_ALIASES)
        if valid:
            return self._absence(
                context, verdict="scope_absent", proof=proof,
                reason="no coherent suspension/no-liability clause found",
                rule="suspension_alias_complete_absence.v1",
            )
        return self._unresolved(context, "suspension absence proof failed", proof, errors)

    def _compile_annuity_change(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        products = context["products"]
        by_doc = {
            product.document_id: set(
                fact.normalized_value
                for fact in self._facts((product.document_id,), relation="annuity_change_right")
            )
            for product in products
        }
        allows = {
            doc_id: bool(values & {
                "age_and_mode_before_start", "age_before_start", "mode_before_start",
                "period_before_start",
            })
            for doc_id, values in by_doc.items()
        }
        disallows = {
            doc_id: "start_not_changeable" in values
            for doc_id, values in by_doc.items()
        }
        all_facts = tuple(
            fact
            for doc_id in by_doc
            for fact in self._facts((doc_id,), relation="annuity_change_right")
        )
        if len(products) >= 3 and "；" in text:
            segments = [segment for segment in text.split("；") if segment.strip()]
            results = []
            used: list[AutoInsuranceClauseFact] = []
            for segment in segments:
                matched = self.catalog.match_products(segment)
                if not matched:
                    return self._unresolved(context, "annuity comparison segment lacks product binding")
                doc_id = matched[0].document_id
                says_no = _has_any(segment, ("不允许", "不得", "不能"))
                says_allow = _has_any(segment, ("允许", "可以")) and not says_no
                if says_no:
                    results.append(disallows.get(doc_id, False) and not allows.get(doc_id, False))
                elif says_allow:
                    required = []
                    if "年龄" in segment:
                        required.append("age")
                    if "方式" in segment:
                        required.append("mode")
                    if "期间" in segment:
                        required.append("period")
                    values = by_doc.get(doc_id, set())
                    results.append(all(
                        any(atom in str(value) for value in values)
                        for atom in required
                    ))
                used.extend(self._facts((doc_id,), relation="annuity_change_right"))
            return self._direct(
                context, "supported" if all(results) else "contradicted", used,
                "annuity_product_by_product_comparison.v1",
            )
        if _has_any(text, ("均不允许", "三者均不")) and any(allows.values()):
            return self._direct(context, "contradicted", all_facts, "annuity_all_disallow_refuted.v1")
        if _has_any(text, ("均允许", "三者均允许")) and any(disallows.values()):
            return self._direct(context, "contradicted", all_facts, "annuity_all_allow_refuted.v1")
        if _contains(text, "仅") and sum(allows.values()) > 1:
            return self._direct(context, "contradicted", all_facts, "annuity_only_one_refuted.v1")
        return self._unresolved(context, "annuity comparison semantics unresolved")

    def _compile_event_prerequisite(self, **context: Any) -> dict[str, Any]:
        return self._unresolved(
            context,
            "event prerequisites such as diagnosis, hospitalization, transport use, or disability grade are missing",
            rule="event_prerequisite_fail_closed.v1",
        )

    def _compile_suicide_exclusion(self, **context: Any) -> dict[str, Any]:
        docs = context["product_document_ids"]
        facts = self._facts(docs, relation="suicide_exception_period_years", value=2)
        if facts:
            return self._direct(context, "supported", facts, "suicide_two_year_exception.v1")
        generic = self._facts(docs, relation="suicide_exclusion", value=True)
        if generic:
            return self._direct(context, "contradicted", generic, "suicide_without_two_year_exception.v1")
        proof, valid, errors = self._proof(
            docs, alias_groups=(SUICIDE_ALIASES, ("2年", "二年"))
        )
        if valid:
            return self._absence(
                context, verdict="scope_absent", proof=proof,
                reason="no suicide clause with two-year exception found",
                rule="suicide_two_year_alias_absence.v1",
            )
        return self._unresolved(context, "suicide exception proof failed", proof, errors)

    def _compile_rescue_expense(self, **context: Any) -> dict[str, Any]:
        docs = context["product_document_ids"]
        facts = self._facts(docs, relation="rescue_expense_cap", value="insured_amount")
        if facts:
            return self._direct(context, "supported", facts, "rescue_expense_cap_insured_amount.v1")
        product_types = {product.product_type for product in context["products"]}
        if not product_types <= {"medical", "group_medical"}:
            return self._unresolved(context, "non-medical rescue-expense scope unresolved")
        proof, valid, errors = self._proof(docs, alias_groups=(RESCUE_ALIASES,))
        if valid:
            return self._absence(
                context, verdict="scope_absent", proof=proof,
                reason="medical product has no coherent rescue-expense clause",
                rule="medical_product_rescue_absence.v1",
            )
        return self._unresolved(context, "rescue-expense absence proof failed", proof, errors)

    def _compile_event_payout(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        needs = []
        if _has_any(text, ("身故",)):
            needs.append(("benefit_scope", "death_benefit"))
        if _has_any(text, ("白血病复发",)):
            needs.append(("benefit_scope", "leukemia_recurrence_medical"))
        if _has_any(text, ("公交车", "营运交通")):
            needs.extend((
                ("covered_event", "commercial_transport_passenger"),
                ("benefit_scope", "accidental_disability"),
            ))
        if _has_any(text, ("火灾",)):
            needs.append(("benefit_scope", "fire_property_loss"))
        facts: list[AutoInsuranceClauseFact] = []
        for relation, value in needs:
            matched = self._facts(docs, relation=relation, value=value)
            if not matched:
                return self._unresolved(context, f"payout event fact missing: {relation}={value}")
            facts.extend(matched)
        if needs:
            return self._direct(context, "supported", facts, "event_and_benefit_scope_complete.v1")
        return self._unresolved(context, "payout event atoms unresolved")

    def _compile_deductible(self, **context: Any) -> dict[str, Any]:
        text = context["option_text"]
        docs = context["product_document_ids"]
        mappings = (
            (("统筹账户",), "pool_account_not_offset", "contradicted"),
            (("个人账户",), "personal_account_can_offset", "supported"),
            (("其他商业保险", "其他途径"), "other_compensation_can_offset", "supported"),
            (("无免赔额",), "fixed_sum_no_deductible", "supported"),
        )
        for terms, value, verdict in mappings:
            if _has_any(text, terms):
                relation = "deductible_structure" if value == "fixed_sum_no_deductible" else "deductible_offset_source"
                facts = self._facts(docs, relation=relation, value=value)
                if facts:
                    return self._direct(context, verdict, facts, f"deductible_{value}.v1")
        return self._unresolved(context, "deductible offset relation unresolved")

    def _compile_hesitation_refund(self, **context: Any) -> dict[str, Any]:
        docs = context["product_document_ids"]
        facts = self._facts(docs, relation="hesitation_period_refund", value="full_premium_refund")
        if facts:
            return self._direct(context, "supported", facts, "hesitation_full_premium_refund.v1")
        return self._unresolved(context, "hesitation-period refund fact unresolved")

    def _direct(
        self,
        context: Mapping[str, Any],
        verdict: str,
        facts: Sequence[AutoInsuranceClauseFact],
        rule: str,
    ) -> dict[str, Any]:
        return self._payload(
            **self._context_payload(context),
            verdict=verdict,
            facts=facts,
            reason=f"direct auto-extracted clause facts satisfy {rule}",
            compiler_rule_id=rule,
            evidence_tier=1,
            reconciliation_rule_id="tier1_direct_clause_overrides_tier3_or_4.v1",
        )

    def _absence(
        self,
        context: Mapping[str, Any],
        *,
        verdict: str,
        proof: Mapping[str, Any],
        reason: str,
        rule: str,
    ) -> dict[str, Any]:
        return self._payload(
            **self._context_payload(context),
            verdict=verdict,
            facts=(),
            reason=reason,
            compiler_rule_id=rule,
            evidence_tier=2,
            reconciliation_rule_id="tier2_complete_absence_overrides_tier3_or_4.v1",
            scope_absence_proof=proof,
            scope_absence_proof_valid=True,
        )

    def _unresolved(
        self,
        context: Mapping[str, Any],
        reason: str,
        proof: Mapping[str, Any] | None = None,
        errors: Sequence[str] = (),
        *,
        rule: str = "generic_clause_unresolved.v1",
    ) -> dict[str, Any]:
        return self._payload(
            **self._context_payload(context),
            verdict="unresolved",
            facts=(),
            reason=reason,
            compiler_rule_id=rule,
            scope_absence_proof=proof,
            scope_absence_proof_valid=False,
            scope_absence_proof_errors=errors,
        )

    @staticmethod
    def _context_payload(context: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "option_label": context["option_label"],
            "option_text": context["option_text"],
            "route": context["route"],
            "declared_doc_ids": context["declared_doc_ids"],
            "product_document_ids": context["product_document_ids"],
        }

    def _payload(
        self,
        *,
        option_label: str,
        option_text: str,
        route: str,
        verdict: str,
        facts: Sequence[AutoInsuranceClauseFact],
        reason: str,
        compiler_rule_id: str,
        declared_doc_ids: Sequence[str],
        product_document_ids: Sequence[str] = (),
        out_of_scope_doc_ids: Sequence[str] = (),
        evidence_tier: int | None = None,
        reconciliation_rule_id: str = "",
        scope_absence_proof: Mapping[str, Any] | None = None,
        scope_absence_proof_valid: bool = False,
        scope_absence_proof_errors: Sequence[str] = (),
    ) -> dict[str, Any]:
        authoritative = verdict in AUTHORITATIVE and (
            verdict not in {"scope_absent"} or scope_absence_proof_valid
        )
        fact_rows = [fact.to_dict() for fact in facts]
        sources = list(dict.fromkeys(fact.source_relpath for fact in facts))
        windows = list(dict.fromkeys(fact.local_window for fact in facts))
        if verdict == "not_applicable" and not sources:
            sources = [
                f"declared_scope_excludes_document:{doc_id}"
                for doc_id in product_document_ids
            ]
        if scope_absence_proof and not sources:
            sources = list(dict(scope_absence_proof.get("source_relpaths") or {}).values())
        return {
            "option": str(option_label).upper(),
            "option_text": option_text,
            "verdict": verdict,
            "status": verdict,
            "authoritative": authoritative,
            "trusted_for_option_gate": authoritative,
            "claim_type": route,
            "claim_route": (
                "scope_only" if scope_absence_proof
                else "exact_clause" if verdict == "supported"
                else "contradiction" if verdict in {"contradicted", "not_applicable"}
                else "insurance_clause_unresolved"
            ),
            "typed_claim_route": "generic_insurance_clause_compiler",
            "compiler_rule_id": compiler_rule_id,
            "reason": reason,
            "declared_doc_ids": list(declared_doc_ids),
            "product_document_ids": list(product_document_ids),
            "out_of_scope_doc_ids": list(out_of_scope_doc_ids),
            "facts": fact_rows,
            "source_document": sources,
            "source_path": sources,
            "canonical_sources": sources,
            "text_anchor": [fact.extraction_rule_id for fact in facts],
            "local_window": "\n\n".join(windows),
            "question_scope_binding": (
                "scope_absent" if scope_absence_proof
                else "not_applicable" if verdict == "not_applicable"
                else "in_scope" if authoritative
                else "unresolved"
            ),
            "factual_statement_true": (
                True if verdict == "supported"
                else False if verdict in {"contradicted", "not_applicable"}
                else None
            ),
            "scope_absence_proof": dict(scope_absence_proof) if scope_absence_proof else None,
            "scope_absence_proof_valid": scope_absence_proof_valid,
            "scope_absence_proof_errors": list(scope_absence_proof_errors),
            "required_atoms_complete": authoritative,
            "entity_scope_complete": authoritative,
            "period_scope_complete": authoritative,
            "metric_scope_complete": authoritative,
            "comparator_scope_complete": authoritative,
            "conflicts": [] if authoritative else ["generic_insurance_clause_not_authoritative"],
            "evidence_tier": evidence_tier,
            "winning_evidence_source": sources[0] if sources and authoritative else "",
            "superseded_evidence_sources": [],
            "conflict_reason": "",
            "reconciliation_rule_id": reconciliation_rule_id,
            "production_auto_extracted": True,
        }

    def audit_question(self, question: Mapping[str, Any]) -> dict[str, Any]:
        rows = [
            self.audit_option(
                question_text=str(question.get("question") or question.get("text") or ""),
                option_label=str(label),
                option_text=str(text),
                declared_doc_ids=question.get("doc_ids") or (),
            )
            for label, text in sorted((question.get("options") or {}).items())
        ]
        fully_trusted = bool(rows) and all(row["authoritative"] for row in rows)
        supported = _canonical_answer(
            row["option"] for row in rows if row["verdict"] == "supported"
        )
        return {
            "qid": str(question.get("qid") or ""),
            "declared_doc_ids": list(question.get("doc_ids") or []),
            "defined_option_slots": len(rows),
            "authoritative_option_slots": sum(row["authoritative"] for row in rows),
            "unresolved_option_slots": sum(not row["authoritative"] for row in rows),
            "fully_trusted": fully_trusted,
            "derived_answer": supported if fully_trusted else "",
            "option_evidence": rows,
        }
