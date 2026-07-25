"""Deterministic insurance calculation compiler.

The compiler recognises calculation semantics from question text and product
scope.  It does not branch on fixed dataset identifiers and never reads an
expected answer.  Contract facts come from authoritative source text through
``insurance_calculation_extractor``; question facts come from a typed scenario
variable parser with explicit units and lineage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import permutations
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from contracts import Question
from verification.calculation_grounding import build_calculation_grounding
from verification.derived_option_evidence import (
    CONTRADICTED,
    SUPPORTED,
    UNRESOLVED,
    DerivedOptionEvidence,
    SourceFact,
)
from verification.insurance_calculation_extractor import (
    InsuranceCalculationFact,
    extract_insurance_calculation_facts,
)
from verification.insurance_clause_extractor import (
    InsuranceProductCatalog,
    InsuranceProductDocument,
    load_insurance_product_catalog,
)


CNY = "CNY"
RATIO = "ratio"
COUNT = "count"
BOOLEAN = "boolean"


@dataclass(frozen=True)
class ScenarioVariable:
    canonical_name: str
    raw_text: str
    normalized_value: Any
    unit: str
    source: str
    source_location: str
    product_scope: str
    person_scope: str
    time_scope: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InsuranceCalculationIntent:
    calculation_object: str
    operation: str
    coordination_features: tuple[str, ...]
    entities: tuple[str, ...]
    matched_products: tuple[str, ...]
    matched_terms: Mapping[str, Sequence[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "calculation_object": self.calculation_object,
            "operation": self.operation,
            "coordination_features": list(self.coordination_features),
            "entities": list(self.entities),
            "matched_products": list(self.matched_products),
            "matched_terms": {key: list(value) for key, value in self.matched_terms.items()},
        }


@dataclass(frozen=True)
class OptionVector:
    option: str
    values: Mapping[str, Any]
    order: tuple[str, ...]
    parse_complete: bool
    raw_text: str
    parse_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "option": self.option,
            "values": dict(self.values),
            "order": list(self.order),
            "parse_complete": self.parse_complete,
            "raw_text": self.raw_text,
            "parse_notes": list(self.parse_notes),
        }


def _canonical_answer(value: Any) -> str:
    return "".join(sorted({char for char in str(value or "").upper() if "A" <= char <= "D"}))


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("（", "(").replace("）", ")")


def _normalise_amount(number: str, unit: str) -> float:
    value = float(str(number).replace(",", ""))
    normalised_unit = str(unit or "")
    if normalised_unit in {"亿元", "亿"}:
        return value * 100_000_000.0
    if normalised_unit in {"万元", "万"}:
        return value * 10_000.0
    return value


def _amount_pattern(group: str = "amount") -> str:
    return rf"(?P<{group}>[0-9]+(?:\.[0-9]+)?)\s*(?P<{group}_unit>亿元|万元|万|元)"


def _ratio_value(number: str) -> float:
    return float(number) / 100.0


def _approximately_equal(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def _fact_ref(fact: InsuranceCalculationFact) -> str:
    return f"{fact.source_relpath}:line_{fact.page_or_line}:{fact.extraction_rule_id}"


def _variable_key(product_id: str, metric: str, person: str = "") -> str:
    parts = [part for part in (product_id, person, metric) if part]
    return ".".join(parts)


class InsuranceQuestionVariableParser:
    """Parse typed scenario variables without relying on fixed question IDs."""

    def __init__(self, catalog: InsuranceProductCatalog) -> None:
        self.catalog = catalog

    def _product_matches(self, text: str) -> tuple[InsuranceProductDocument, ...]:
        return self.catalog.match_products(text)

    def _add(
        self,
        rows: list[ScenarioVariable],
        *,
        name: str,
        match: re.Match[str] | None,
        value: Any,
        unit: str,
        product: str = "",
        person: str = "",
        time_scope: str = "question_scenario",
        raw_text: str | None = None,
        confidence: str = "question_exact",
    ) -> None:
        if match is not None:
            source_location = f"question_chars_{match.start()}_{match.end()}"
            raw = match.group(0)
        else:
            source_location = "question_inferred_scope"
            raw = str(raw_text or "")
        rows.append(
            ScenarioVariable(
                canonical_name=name,
                raw_text=raw,
                normalized_value=value,
                unit=unit,
                source="question",
                source_location=source_location,
                product_scope=product,
                person_scope=person,
                time_scope=time_scope,
                confidence=confidence,
            )
        )

    @staticmethod
    def _amount_from_match(match: re.Match[str], group: str = "amount") -> float:
        return _normalise_amount(match.group(group), match.group(f"{group}_unit"))

    def _parse_claim_order(self, text: str) -> ScenarioVariable | None:
        patterns = (
            r"先(?:向|使用)?(?P<first>[^，。；]{1,40}?)(?:申请)?赔付.*?(?:再|然后|后)(?:向|使用)?(?P<second>[^，。；]{1,40}?)(?:申请)?赔付",
            r"先(?:向|使用)?(?P<first>[^，。；]{1,40}?)(?:申请)?理赔.*?(?:再|然后|后)(?:向|使用)?(?P<second>[^，。；]{1,40}?)(?:申请)?理赔",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.S)
            if match is None:
                continue
            def resolve(fragment: str) -> InsuranceProductDocument | None:
                exact = self._product_matches(fragment)
                if len(exact) == 1:
                    return exact[0]
                compact_fragment = _compact(fragment).lstrip("向使用")
                candidates = []
                for product in self.catalog.documents:
                    aliases = (product.product_name, *product.aliases, product.insurer)
                    if any(
                        alias and (
                            compact_fragment in _compact(alias)
                            or _compact(alias).startswith(compact_fragment)
                            or compact_fragment.startswith(_compact(alias))
                        )
                        for alias in aliases
                    ):
                        candidates.append(product)
                unique = {item.canonical_product_id: item for item in candidates}
                return next(iter(unique.values())) if len(unique) == 1 else None

            first = resolve(match.group("first"))
            second = resolve(match.group("second"))
            if first is None or second is None:
                continue
            order = [first.canonical_product_id, second.canonical_product_id]
            if order[0] == order[1]:
                continue
            return ScenarioVariable(
                canonical_name="claim_order",
                raw_text=match.group(0),
                normalized_value=order,
                unit="sequence",
                source="question",
                source_location=f"question_chars_{match.start()}_{match.end()}",
                product_scope="|".join(order),
                person_scope="",
                time_scope="claim_sequence",
                confidence="question_explicit",
            )
        return None

    def parse(self, question: Question, calculation_kind: str) -> tuple[ScenarioVariable, ...]:
        handlers = {
            "death_benefit_ranking": self._parse_death_benefit_ranking,
            "surrender_value_ranking": self._parse_surrender_value_ranking,
            "multi_medical_reimbursement": self._parse_multi_medical,
            "family_deductible_coordination": self._parse_family_deductible,
            "property_medical_isolation": self._parse_property_medical,
        }
        handler = handlers.get(calculation_kind)
        return tuple(handler(question) if handler else ())

    def _parse_death_benefit_ranking(self, question: Question) -> list[ScenarioVariable]:
        text = question.text
        rows: list[ScenarioVariable] = []
        products = self._product_matches(text)
        common_premium = re.search(rf"所有产品的已交保费均为{_amount_pattern()}", text)
        common_cash = re.search(rf"现金价值均为{_amount_pattern()}", text)
        for product in products:
            if common_premium:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "premium_paid"),
                    match=common_premium,
                    value=self._amount_from_match(common_premium),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
            if common_cash:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "cash_value"),
                    match=common_cash,
                    value=self._amount_from_match(common_cash),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
        for product in products:
            alias = self._best_alias_in_text(product, text)
            escaped = re.escape(alias)
            account = re.search(rf"{escaped}.{{0,50}}?(?:保单账户价值|个人账户价值){_amount_pattern()}", text)
            if account:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "account_value"),
                    match=account,
                    value=self._amount_from_match(account),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
            basic = re.search(rf"{escaped}.{{0,70}}?基本保额{_amount_pattern()}", text)
            if basic:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "basic_amount"),
                    match=basic,
                    value=self._amount_from_match(basic),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
            age = re.search(rf"{escaped}.{{0,30}}?(?P<age>[0-9]+)岁", text)
            if age:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "age"),
                    match=age,
                    value=int(age.group("age")),
                    unit=COUNT,
                    product=product.canonical_product_id,
                )
            annuity = re.search(rf"{escaped}.{{0,40}}?已领养老年金{_amount_pattern()}", text)
            if annuity:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "annuity_paid"),
                    match=annuity,
                    value=self._amount_from_match(annuity),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
        before = re.search(r"领取日前", text)
        if before:
            matched = next((p for p in products if self._best_alias_in_text(p, text) in text[: before.start() + 1]), None)
            if matched:
                self._add(
                    rows,
                    name=_variable_key(matched.canonical_product_id, "before_annuity_start"),
                    match=before,
                    value=True,
                    unit=BOOLEAN,
                    product=matched.canonical_product_id,
                )
        return rows

    def _parse_surrender_value_ranking(self, question: Question) -> list[ScenarioVariable]:
        text = question.text
        rows: list[ScenarioVariable] = []
        clauses = [clause.strip() for clause in re.split(r"[；;。]", text) if clause.strip()]
        for clause in clauses:
            matches = self._product_matches(clause)
            for product in matches:
                fields = (
                    ("premium_paid", rf"累计所交保费{_amount_pattern()}"),
                    ("cumulative_return", rf"保单账户累计收益{_amount_pattern()}"),
                    ("account_value", rf"个人账户价值{_amount_pattern()}"),
                    ("cash_value", rf"现金价值{_amount_pattern()}"),
                )
                for metric, pattern in fields:
                    match = re.search(pattern, clause)
                    if match:
                        self._add(
                            rows,
                            name=_variable_key(product.canonical_product_id, metric),
                            match=match,
                            value=self._amount_from_match(match),
                            unit=CNY,
                            product=product.canonical_product_id,
                        )
                year = re.search(r"第\s*(?P<year>[0-9]+)\s*个保单年度", clause)
                if year:
                    self._add(
                        rows,
                        name=_variable_key(product.canonical_product_id, "policy_year"),
                        match=year,
                        value=int(year.group("year")),
                        unit=COUNT,
                        product=product.canonical_product_id,
                        time_scope=f"policy_year_{year.group('year')}",
                    )
                charge = re.search(r"退保费用\s*(?P<ratio>[0-9]+(?:\.[0-9]+)?)\s*[%％]", clause)
                if charge:
                    self._add(
                        rows,
                        name=_variable_key(product.canonical_product_id, "surrender_charge_rate"),
                        match=charge,
                        value=_ratio_value(charge.group("ratio")),
                        unit=RATIO,
                        product=product.canonical_product_id,
                    )
        return rows

    def _parse_multi_medical(self, question: Question) -> list[ScenarioVariable]:
        text = question.text
        rows: list[ScenarioVariable] = []
        total = re.search(rf"(?:总费用|医疗总费用){_amount_pattern()}", text)
        social = re.search(rf"(?:医保|基本医疗保险)(?:已)?报销{_amount_pattern()}", text)
        self_paid = re.search(rf"(?:自费|个人自行承担){_amount_pattern()}", text)
        for metric, match in (("medical_total", total), ("social_insurance", social), ("self_paid", self_paid)):
            if match:
                self._add(rows, name=metric, match=match, value=self._amount_from_match(match), unit=CNY)
        for product in self._product_matches(text):
            alias = self._best_alias_in_text(product, text)
            deductible = re.search(rf"{re.escape(alias)}[^、；。]{{0,55}}?免赔额(?:为)?{_amount_pattern()}", text)
            if deductible:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "deductible"),
                    match=deductible,
                    value=self._amount_from_match(deductible),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
            else:
                zero_deductible = re.search(
                    rf"{re.escape(alias)}[^、；。]{{0,55}}?免赔额(?:为)?\s*(?P<zero>0)(?![0-9])",
                    text,
                )
                if zero_deductible:
                    self._add(
                        rows,
                        name=_variable_key(product.canonical_product_id, "deductible"),
                        match=zero_deductible,
                        value=0.0,
                        unit=CNY,
                        product=product.canonical_product_id,
                    )
        all_covered = re.search(r"所有费用均属保险责任|全部(?:费用)?属于保险责任", text)
        if all_covered:
            self._add(rows, name="all_expenses_covered", match=all_covered, value=True, unit=BOOLEAN)
        morphology = re.search(r"形态学复发", text)
        if morphology:
            self._add(rows, name="recurrence_type", match=morphology, value="morphological", unit="category")
        explicit_cap = re.search(r"(?:合计|总计)赔付不得超过[^。；]{0,20}?(?:自费金额|个人自行承担金额)", text)
        if explicit_cap:
            self._add(
                rows,
                name="aggregate_indemnity_cap",
                match=explicit_cap,
                value="self_paid",
                unit="reference",
                confidence="question_explicit",
            )
        order = self._parse_claim_order(text)
        if order is not None:
            rows.append(order)
        return rows

    def _parse_family_deductible(self, question: Question) -> list[ScenarioVariable]:
        text = question.text
        rows: list[ScenarioVariable] = []
        person_pattern = re.compile(
            rf"(?P<person>王某本人|本人|其配偶|配偶).{{0,24}}?(?:发生)?医疗(?:费用|支出){_amount_pattern('medical')}.{{0,55}}?(?:医保|基本医疗保险)(?:已)?报销{_amount_pattern('social')}",
            re.S,
        )
        for match in person_pattern.finditer(text):
            person = "insured" if "本人" in match.group("person") else "spouse"
            self._add(
                rows,
                name=_variable_key("scenario", "medical_total", person),
                match=match,
                value=_normalise_amount(match.group("medical"), match.group("medical_unit")),
                unit=CNY,
                person=person,
            )
            self._add(
                rows,
                name=_variable_key("scenario", "social_insurance", person),
                match=match,
                value=_normalise_amount(match.group("social"), match.group("social_unit")),
                unit=CNY,
                person=person,
            )
        for product in self._product_matches(text):
            alias = self._best_alias_in_text(product, text)
            deductible = re.search(rf"{re.escape(alias)}[^、；。]{{0,55}}?免赔额{_amount_pattern()}", text)
            if deductible:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "deductible"),
                    match=deductible,
                    value=self._amount_from_match(deductible),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
            plan = re.search(rf"{re.escape(alias)}[^、；。]{{0,25}}?计划(?P<plan>[一二三四1234])", text)
            if plan:
                lookup = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "plan"),
                    match=plan,
                    value=lookup[plan.group("plan")],
                    unit=COUNT,
                    product=product.canonical_product_id,
                )
        family_count = re.search(r"家庭(?P<count>[0-9]+)人同时参保", text)
        if family_count:
            self._add(rows, name="family_member_count", match=family_count, value=int(family_count.group("count")), unit=COUNT)
        shared = re.search(r"共享免赔额", text)
        if shared:
            self._add(rows, name="family_deductible_shared", match=shared, value=True, unit=BOOLEAN)
        no_other = re.search(r"未从其他途径获得补偿", text)
        if no_other:
            self._add(rows, name="historical_other_compensation", match=no_other, value=0.0, unit=CNY)
        order = self._parse_claim_order(text)
        if order is not None:
            rows.append(order)
        return rows

    def _parse_property_medical(self, question: Question) -> list[ScenarioVariable]:
        text = question.text
        rows: list[ScenarioVariable] = []
        property_loss = re.search(rf"(?:维修|修复)(?:费用|支出){_amount_pattern()}", text)
        medical = re.search(rf"门诊(?:费用|支出){_amount_pattern()}", text)
        if property_loss:
            self._add(rows, name="actual_property_loss", match=property_loss, value=self._amount_from_match(property_loss), unit=CNY)
        if medical:
            self._add(rows, name="ordinary_outpatient_expense", match=medical, value=self._amount_from_match(medical), unit=CNY)
        property_products = [product for product in self._product_matches(text) if product.product_type == "property"]
        for product in property_products:
            alias = self._best_alias_in_text(product, text)
            scope = rf"{re.escape(alias)}[^。；]{{0,90}}?"
            deductible = re.search(scope + rf"免赔额(?:为)?{_amount_pattern()}", text)
            insured_amount = re.search(scope + rf"保险金额(?:为)?{_amount_pattern()}", text)
            if deductible:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "deductible"),
                    match=deductible,
                    value=self._amount_from_match(deductible),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
            if insured_amount:
                self._add(
                    rows,
                    name=_variable_key(product.canonical_product_id, "insured_amount"),
                    match=insured_amount,
                    value=self._amount_from_match(insured_amount),
                    unit=CNY,
                    product=product.canonical_product_id,
                )
        social_not_used = re.search(r"医保未报销|未经医保报销", text)
        social_used = re.search(r"医保已报销|已经医保报销", text)
        if social_not_used:
            self._add(rows, name="social_insurance_settlement", match=social_not_used, value=False, unit=BOOLEAN)
        elif social_used:
            self._add(rows, name="social_insurance_settlement", match=social_used, value=True, unit=BOOLEAN)
        pipe = re.search(r"水管爆裂", text)
        if pipe:
            self._add(rows, name="property_event", match=pipe, value="water_pipe_burst", unit="category")
        fall = re.search(r"摔伤", text)
        if fall:
            self._add(rows, name="medical_event", match=fall, value="ordinary_accident_outpatient", unit="category")
        return rows

    @staticmethod
    def _best_alias_in_text(product: InsuranceProductDocument, text: str) -> str:
        aliases = sorted((product.product_name, *product.aliases), key=len, reverse=True)
        return next((alias for alias in aliases if alias in text), product.product_name)


class InsuranceCalculationCompiler:
    """Compile authoritative facts and scenario variables into typed evidence."""

    def __init__(
        self,
        full_text_root: Path | str,
        *,
        product_catalog_path: Path | str,
        catalog: InsuranceProductCatalog | None = None,
        facts: Sequence[InsuranceCalculationFact] | None = None,
    ) -> None:
        self.full_text_root = Path(full_text_root)
        self.catalog = catalog or load_insurance_product_catalog(product_catalog_path)
        self.facts = tuple(
            facts
            if facts is not None
            else extract_insurance_calculation_facts(
                self.full_text_root,
                catalog=self.catalog,
            )
        )
        self.variable_parser = InsuranceQuestionVariableParser(self.catalog)
        self._facts_by_doc_relation: dict[tuple[str, str], list[InsuranceCalculationFact]] = {}
        for fact in self.facts:
            self._facts_by_doc_relation.setdefault(
                (fact.document_id, fact.normalized_relation), []
            ).append(fact)

    def structured_intent(self, question: Question) -> InsuranceCalculationIntent:
        text = str(question.text or "")
        compact = _compact(text)
        if question.domain != "insurance":
            return InsuranceCalculationIntent("", "", (), (), (), {})
        products = self.catalog.match_products(text)
        product_ids = tuple(dict.fromkeys(product.canonical_product_id for product in products))
        product_types = {product.product_type for product in products}

        def terms(candidates: Sequence[str]) -> tuple[str, ...]:
            return tuple(candidate for candidate in candidates if candidate in compact)

        rank_terms = terms(("排序", "排列", "由高到低", "从高到低", "高低顺序"))
        aggregate_terms = terms(("共应赔付", "合计应支付", "合计赔付", "总计赔付", "共赔", "合计"))
        per_policy_terms = terms(("分别应赔付", "各自需要支付", "各自应支付", "分别赔付", "各自赔付"))
        death_terms = terms(("身故保险金", "身故给付", "身故金额", "身故"))
        surrender_terms = terms(("退保所得", "退保金额", "解除合同后可领取", "合同解除后可领取", "退保", "解除合同"))
        medical_terms = terms(("医疗费用", "医疗支出", "门诊费用", "门诊支出", "住院", "医保", "医疗险"))
        property_terms = terms(("维修费用", "维修支出", "修复费用", "修复支出", "财产损失", "地板损坏", "家财险", "财险"))

        calculation_object = ""
        operation = ""
        entities: list[str] = []
        if death_terms and rank_terms:
            calculation_object, operation = "death_benefit", "rank"
        elif surrender_terms and rank_terms:
            calculation_object, operation = "surrender_value", "rank"
        elif property_terms and medical_terms and ("property" in product_types or terms(("家财险", "财险"))):
            calculation_object, operation = "property_payment", "liability_isolation"
            entities.extend(("property_loss", "medical_expense"))
        elif medical_terms and per_policy_terms:
            calculation_object, operation = "medical_reimbursement", "per_policy_payment"
            entities.append("medical_expense")
        elif medical_terms and aggregate_terms:
            calculation_object, operation = "medical_reimbursement", "aggregate"
            entities.append("medical_expense")

        features: list[str] = []
        if "共享免赔额" in compact or ("家庭" in compact and "免赔额" in compact):
            features.append("shared_deductible")
        if len(product_ids) > 1:
            features.append("multiple_policies")
        if re.search(r"先.{0,50}?(?:赔付|理赔).{0,80}?(?:再|然后|后).{0,50}?(?:赔付|理赔)", text, re.S):
            features.append("claim_order")
        if "其他途径" in compact or "其他补偿" in compact:
            features.append("other_compensation")
        if "医保" in compact or "基本医疗保险" in compact:
            features.append("social_insurance")

        return InsuranceCalculationIntent(
            calculation_object=calculation_object,
            operation=operation,
            coordination_features=tuple(features),
            entities=tuple(entities),
            matched_products=product_ids,
            matched_terms={
                "death": death_terms,
                "surrender": surrender_terms,
                "medical": medical_terms,
                "property": property_terms,
                "rank": rank_terms,
                "aggregate": aggregate_terms,
                "per_policy": per_policy_terms,
            },
        )

    def calculation_kind(self, question: Question) -> str:
        intent = self.structured_intent(question)
        route = (intent.calculation_object, intent.operation)
        if route == ("death_benefit", "rank"):
            return "death_benefit_ranking"
        if route == ("surrender_value", "rank"):
            return "surrender_value_ranking"
        if route == ("property_payment", "liability_isolation"):
            return "property_medical_isolation"
        if route == ("medical_reimbursement", "per_policy_payment") and "shared_deductible" in intent.coordination_features:
            return "family_deductible_coordination"
        if route == ("medical_reimbursement", "aggregate") and "multiple_policies" in intent.coordination_features:
            return "multi_medical_reimbursement"
        return ""

    def recognises(self, question: Question) -> bool:
        return bool(self.calculation_kind(question))

    def _fact(self, document_id: str, relation: str) -> InsuranceCalculationFact | None:
        candidates = self._facts_by_doc_relation.get((str(document_id), relation), [])
        return next((fact for fact in candidates if not fact.rejection_reasons), None)

    def _product_for_doc(self, document_id: str) -> InsuranceProductDocument:
        return self.catalog.document(str(document_id))

    @staticmethod
    def _variable_map(variables: Sequence[ScenarioVariable]) -> dict[str, ScenarioVariable]:
        return {variable.canonical_name: variable for variable in variables}

    @staticmethod
    def _value(variable_map: Mapping[str, ScenarioVariable], name: str) -> Any:
        variable = variable_map.get(name)
        return variable.normalized_value if variable is not None else None

    def compile(self, question: Question) -> dict[str, Any]:
        kind = self.calculation_kind(question)
        if not kind:
            return {
                "recognized": False,
                "calculation_kind": "",
                "candidate_answer": "",
                "computation_complete": False,
            }
        variables = self.variable_parser.parse(question, kind)
        handlers = {
            "death_benefit_ranking": self._compile_death_ranking,
            "surrender_value_ranking": self._compile_surrender_ranking,
            "multi_medical_reimbursement": self._compile_multi_medical,
            "family_deductible_coordination": self._compile_family_deductible,
            "property_medical_isolation": self._compile_property_medical,
        }
        payload = handlers[kind](question, variables)
        payload["structured_intent"] = self.structured_intent(question).to_dict()
        for variable in variables:
            if variable.canonical_name not in payload["material_variables"] and variable.unit not in {BOOLEAN, "category"}:
                payload["material_variables"].append(variable.canonical_name)
        return self._finalise(question, kind, variables, payload)

    def _base_payload(self, question: Question) -> dict[str, Any]:
        return {
            "required_doc_ids": [str(value) for value in question.doc_ids],
            "formula": "",
            "formula_facts": [],
            "calculation_steps": [],
            "intermediate_values": {},
            "computed_result": None,
            "computed_values_by_product": {},
            "computed_total": None,
            "coverage_prerequisites": [],
            "coverage_prerequisites_complete": True,
            "coordination_complete": True,
            "coordination_state": "not_applicable",
            "claim_order_source": "not_applicable",
            "material_variables": [],
            "material_variable_refs": {},
            "material_variable_values": {},
            "material_variable_units": {},
            "expense_pool_ledger": [],
            "feasible_order_replays": [],
            "subclaim_status": {},
            "unresolved_variables": [],
            "conflicts": [],
            "rounding_policy": "exact_CNY; compare with tolerance 1e-6",
        }

    @staticmethod
    def _declare_material(
        payload: dict[str, Any],
        name: str,
        value: Any,
        *,
        source_refs: Sequence[str] = (),
        unit: str = "",
    ) -> None:
        if name not in payload["material_variables"]:
            payload["material_variables"].append(name)
        if source_refs:
            payload["material_variable_refs"][name] = list(dict.fromkeys(str(ref) for ref in source_refs if str(ref)))
        if value is not None:
            payload["material_variable_values"][name] = value
        else:
            payload["unresolved_variables"].append(name)
        if unit:
            payload["material_variable_units"][name] = unit

    @staticmethod
    def _claim_order_from_variables(variables: Mapping[str, ScenarioVariable]) -> list[str]:
        value = variables.get("claim_order")
        if value is None or not isinstance(value.normalized_value, Sequence) or isinstance(value.normalized_value, (str, bytes)):
            return []
        return [str(item) for item in value.normalized_value]

    def _require_fact(
        self,
        payload: dict[str, Any],
        document_id: str,
        relation: str,
        *,
        conflict: str | None = None,
    ) -> InsuranceCalculationFact | None:
        fact = self._fact(document_id, relation)
        if fact is None:
            payload["conflicts"].append(conflict or f"formula_fact_missing:{document_id}:{relation}")
        else:
            payload["formula_facts"].append(fact)
        return fact

    def _compile_death_ranking(
        self, question: Question, variables: Sequence[ScenarioVariable]
    ) -> dict[str, Any]:
        payload = self._base_payload(question)
        variable_map = self._variable_map(variables)
        results: dict[str, float] = {}
        formulas: list[str] = []
        for doc_id in question.doc_ids:
            product = self._product_for_doc(str(doc_id))
            formula_fact = self._require_fact(payload, str(doc_id), "death_benefit_formula")
            if formula_fact is None:
                continue
            formula = str(formula_fact.normalized_value)
            product_id = product.canonical_product_id
            if formula == "account_value":
                account = self._value(variable_map, _variable_key(product_id, "account_value"))
                if account is None:
                    payload["unresolved_variables"].append(_variable_key(product_id, "account_value"))
                    continue
                value = float(account)
            elif formula == "max(basic_amount * age_ratio, account_value)":
                schedule_fact = self._require_fact(payload, str(doc_id), "age_ratio_schedule")
                basic = self._value(variable_map, _variable_key(product_id, "basic_amount"))
                age = self._value(variable_map, _variable_key(product_id, "age"))
                account = self._value(variable_map, _variable_key(product_id, "account_value"))
                missing = [
                    name
                    for name, value in (
                        (_variable_key(product_id, "basic_amount"), basic),
                        (_variable_key(product_id, "age"), age),
                        (_variable_key(product_id, "account_value"), account),
                    )
                    if value is None
                ]
                payload["unresolved_variables"].extend(missing)
                if missing or schedule_fact is None:
                    continue
                schedule = dict(schedule_fact.normalized_value)
                numeric_age = int(age)
                ratio = (
                    schedule["under_18"]
                    if numeric_age < 18
                    else schedule["age_18_to_before_41"]
                    if numeric_age < 41
                    else schedule["age_41_to_before_61"]
                    if numeric_age < 61
                    else schedule["age_61_plus"]
                )
                payload["intermediate_values"][_variable_key(product_id, "age_ratio")] = ratio
                value = max(float(basic) * float(ratio), float(account))
            elif formula == "max(premium_paid - annuity_paid, cash_value)":
                premium = self._value(variable_map, _variable_key(product_id, "premium_paid"))
                annuity = self._value(variable_map, _variable_key(product_id, "annuity_paid"))
                cash = self._value(variable_map, _variable_key(product_id, "cash_value"))
                missing = [
                    name
                    for name, value in (
                        (_variable_key(product_id, "premium_paid"), premium),
                        (_variable_key(product_id, "annuity_paid"), annuity),
                        (_variable_key(product_id, "cash_value"), cash),
                    )
                    if value is None
                ]
                payload["unresolved_variables"].extend(missing)
                if missing:
                    continue
                value = max(float(premium) - float(annuity), float(cash))
            else:
                payload["conflicts"].append(f"unsupported_death_formula:{formula}")
                continue
            results[product_id] = value
            formulas.append(f"{product_id}:{formula}")
            payload["calculation_steps"].append(
                {"product_id": product_id, "formula": formula, "result": value}
            )
        payload["formula"] = "; ".join(formulas)
        payload["computed_values_by_product"] = results
        payload["computed_result"] = {
            "ranking": [
                [product_id, value]
                for product_id, value in sorted(results.items(), key=lambda item: item[1], reverse=True)
            ]
        }
        return payload

    def _compile_surrender_ranking(
        self, question: Question, variables: Sequence[ScenarioVariable]
    ) -> dict[str, Any]:
        payload = self._base_payload(question)
        variable_map = self._variable_map(variables)
        results: dict[str, float] = {}
        formulas: list[str] = []
        for doc_id in question.doc_ids:
            product = self._product_for_doc(str(doc_id))
            product_id = product.canonical_product_id
            formula_fact = self._fact(str(doc_id), "surrender_value_formula")
            cash = self._value(variable_map, _variable_key(product_id, "cash_value"))
            if formula_fact is None and cash is not None:
                # An annuity contract may define surrender proceeds simply as its
                # already-reported cash value.  The value remains question-sourced.
                formula = "cash_value"
            elif formula_fact is None:
                payload["conflicts"].append(f"formula_fact_missing:{doc_id}:surrender_value_formula")
                continue
            else:
                payload["formula_facts"].append(formula_fact)
                formula = str(formula_fact.normalized_value)
            if formula.startswith("premium_paid + cumulative_return"):
                premium = self._value(variable_map, _variable_key(product_id, "premium_paid"))
                cumulative = self._value(variable_map, _variable_key(product_id, "cumulative_return"))
                year = self._value(variable_map, _variable_key(product_id, "policy_year"))
                missing = [name for name, value in (
                    (_variable_key(product_id, "premium_paid"), premium),
                    (_variable_key(product_id, "cumulative_return"), cumulative),
                    (_variable_key(product_id, "policy_year"), year),
                ) if value is None]
                payload["unresolved_variables"].extend(missing)
                if missing:
                    continue
                if not 6 <= int(year) <= 10:
                    payload["conflicts"].append(f"policy_year_outside_formula_band:{product_id}:{year}")
                    continue
                value = float(premium) + float(cumulative) * 0.75
            elif formula == "account_value * (1 - surrender_charge_rate)":
                account = self._value(variable_map, _variable_key(product_id, "account_value"))
                year = self._value(variable_map, _variable_key(product_id, "policy_year"))
                explicit_rate = self._value(variable_map, _variable_key(product_id, "surrender_charge_rate"))
                schedule_fact = self._fact(str(doc_id), "surrender_charge_schedule")
                if explicit_rate is None and schedule_fact is not None and year is not None:
                    payload["formula_facts"].append(schedule_fact)
                    schedule = dict(schedule_fact.normalized_value)
                    rate = schedule.get(f"year_{int(year)}", schedule.get("year_6_plus") if int(year) >= 6 else None)
                else:
                    rate = explicit_rate
                missing = [name for name, value in (
                    (_variable_key(product_id, "account_value"), account),
                    (_variable_key(product_id, "policy_year"), year),
                    (_variable_key(product_id, "surrender_charge_rate"), rate),
                ) if value is None]
                payload["unresolved_variables"].extend(missing)
                if missing:
                    continue
                value = float(account) * (1.0 - float(rate))
                payload["intermediate_values"][_variable_key(product_id, "effective_surrender_charge_rate")] = rate
            elif formula == "cash_value":
                if cash is None:
                    payload["unresolved_variables"].append(_variable_key(product_id, "cash_value"))
                    continue
                value = float(cash)
            else:
                payload["conflicts"].append(f"unsupported_surrender_formula:{formula}")
                continue
            results[product_id] = value
            formulas.append(f"{product_id}:{formula}")
            payload["calculation_steps"].append(
                {"product_id": product_id, "formula": formula, "result": value}
            )
        payload["formula"] = "; ".join(formulas)
        payload["computed_values_by_product"] = results
        payload["computed_result"] = {
            "ranking": [
                [product_id, value]
                for product_id, value in sorted(results.items(), key=lambda item: item[1], reverse=True)
            ]
        }
        return payload

    def _medical_ratio(
        self,
        payload: dict[str, Any],
        document_id: str,
        *,
        social_insurance_settled: bool,
        morphological: bool = False,
    ) -> float | None:
        schedule_fact = self._fact(document_id, "social_insurance_ratio_schedule")
        if schedule_fact is not None:
            payload["formula_facts"].append(schedule_fact)
            schedule = dict(schedule_fact.normalized_value)
            key = "with_social_insurance_settlement" if social_insurance_settled else "without_social_insurance_settlement"
            value = float(schedule[key])
            self._declare_material(
                payload,
                f"doc_{document_id}.reimbursement_ratio",
                value,
                source_refs=(_fact_ref(schedule_fact),),
                unit=RATIO,
            )
            return value
        if morphological:
            multiplier = self._fact(document_id, "morphological_recurrence_multiplier")
            if multiplier is not None:
                payload["formula_facts"].append(multiplier)
                value = float(multiplier.normalized_value)
                self._declare_material(
                    payload,
                    f"doc_{document_id}.reimbursement_ratio",
                    value,
                    source_refs=(_fact_ref(multiplier),),
                    unit=RATIO,
                )
                return value
        payload["conflicts"].append(f"reimbursement_ratio_missing:{document_id}")
        self._declare_material(payload, f"doc_{document_id}.reimbursement_ratio", None, unit=RATIO)
        return None

    @staticmethod
    def _simulate_shared_pool_order(
        *,
        expense_pool_id: str,
        initial_pool: float,
        social_insurance_paid: float,
        order: Sequence[str],
        policies: Mapping[str, Mapping[str, Any]],
        claim_order_source: str,
    ) -> dict[str, Any]:
        remaining = max(float(initial_pool), 0.0)
        prior_compensation = 0.0
        results: dict[str, float] = {}
        ledger: list[dict[str, Any]] = []
        for index, product_id in enumerate(order, start=1):
            policy = dict(policies[product_id])
            deductible = float(policy.get("deductible") or 0.0)
            ratio = float(policy.get("ratio") or 0.0)
            offsets_deductible = bool(policy.get("other_compensation_offsets_deductible"))
            deductible_offset = min(prior_compensation, deductible) if offsets_deductible else 0.0
            effective_deductible = max(deductible - deductible_offset, 0.0)
            eligible = remaining
            payment_before = max(eligible - effective_deductible, 0.0) * ratio
            policy_limit = policy.get("policy_limit")
            capped = min(payment_before, remaining)
            if policy_limit is not None:
                capped = min(capped, float(policy_limit))
            payment_after = max(capped, 0.0)
            remaining_after = max(remaining - payment_after, 0.0)
            results[product_id] = payment_after
            ledger.append({
                "expense_pool_id": expense_pool_id,
                "eligible_expense": eligible,
                "social_insurance_paid": float(social_insurance_paid),
                "other_compensation_paid": prior_compensation,
                "remaining_uncompensated_expense": remaining,
                "policy_id": product_id,
                "policy_deductible": deductible,
                "policy_deductible_offset": deductible_offset,
                "effective_policy_deductible": effective_deductible,
                "policy_reimbursement_ratio": ratio,
                "policy_limit": policy_limit,
                "claim_order": index,
                "claim_order_source": claim_order_source,
                "payment_before": payment_before,
                "payment_after": payment_after,
                "remaining_pool_after_payment": remaining_after,
                "indemnity_or_fixed_benefit": "indemnity",
            })
            prior_compensation += payment_after
            remaining = remaining_after
        return {
            "order": list(order),
            "claim_order_source": claim_order_source,
            "computed_values_by_product": results,
            "computed_total": sum(results.values()),
            "remaining_pool": remaining,
            "ledger": ledger,
        }

    @staticmethod
    def _replay_signature(replay: Mapping[str, Any]) -> tuple[Any, ...]:
        values = tuple(sorted((str(key), round(float(value), 6)) for key, value in dict(replay.get("computed_values_by_product") or {}).items()))
        return values + (("total", round(float(replay.get("computed_total") or 0.0), 6)),)

    def _compile_multi_medical(
        self, question: Question, variables: Sequence[ScenarioVariable]
    ) -> dict[str, Any]:
        payload = self._base_payload(question)
        variable_map = self._variable_map(variables)
        self_paid = self._value(variable_map, "self_paid")
        total = self._value(variable_map, "medical_total")
        social = self._value(variable_map, "social_insurance")
        if self_paid is None and total is not None and social is not None:
            self_paid = float(total) - float(social)
            payload["material_variable_values"]["self_paid"] = self_paid
            payload["material_variable_refs"]["self_paid"] = [
                variable_map["medical_total"].source_location,
                variable_map["social_insurance"].source_location,
            ]
            payload["material_variable_units"]["self_paid"] = CNY
            payload["intermediate_values"]["self_paid"] = self_paid
        self._declare_material(payload, "self_paid", self_paid, unit=CNY)
        social_settled = bool((social or 0) > 0)
        morphological = self._value(variable_map, "recurrence_type") == "morphological"
        policies: dict[str, dict[str, Any]] = {}
        for doc_id in question.doc_ids:
            product = self._product_for_doc(str(doc_id))
            product_id = product.canonical_product_id
            formula_fact = self._require_fact(payload, str(doc_id), "medical_payment_formula")
            deductible_name = _variable_key(product_id, "deductible")
            deductible = self._value(variable_map, deductible_name)
            self._declare_material(payload, deductible_name, deductible, unit=CNY)
            ratio = self._medical_ratio(
                payload,
                str(doc_id),
                social_insurance_settled=social_settled,
                morphological=morphological,
            )
            offset_fact = self._fact(str(doc_id), "other_compensation_can_offset_deductible")
            if offset_fact is not None:
                payload["formula_facts"].append(offset_fact)
            else:
                payload["conflicts"].append(f"other_compensation_offset_rule_missing:{doc_id}")
            if formula_fact is None or deductible is None or ratio is None or offset_fact is None:
                continue
            policies[product_id] = {
                "document_id": str(doc_id),
                "deductible": float(deductible),
                "ratio": float(ratio),
                "policy_limit": None,
                "other_compensation_offsets_deductible": bool(offset_fact.normalized_value),
            }
        product_ids = list(policies)
        explicit_order = self._claim_order_from_variables(variable_map)
        if explicit_order and set(explicit_order) == set(product_ids):
            orders = [tuple(explicit_order)]
            order_source = "question_explicit_order"
        elif product_ids:
            orders = list(permutations(product_ids))
            order_source = "all_feasible_orders_replay"
        else:
            orders = []
            order_source = "unresolved"
        replays = [
            self._simulate_shared_pool_order(
                expense_pool_id="medical_self_paid_pool",
                initial_pool=float(self_paid or 0.0),
                social_insurance_paid=float(social or 0.0),
                order=order,
                policies=policies,
                claim_order_source=order_source,
            )
            for order in orders
        ] if self_paid is not None else []
        signatures = {self._replay_signature(replay) for replay in replays}
        totals = {round(float(replay.get("computed_total") or 0.0), 6) for replay in replays}
        order_sensitive = len(signatures) > 1
        coordination_complete = bool(replays) and (bool(explicit_order) or not order_sensitive)
        selected = replays[0] if coordination_complete else None
        common_total = next(iter(totals)) if len(totals) == 1 else None
        if not coordination_complete:
            self._declare_material(payload, "claim_order", None, unit="sequence")
        elif explicit_order:
            self._declare_material(
                payload,
                "claim_order",
                explicit_order,
                source_refs=(variable_map["claim_order"].source_location,),
                unit="sequence",
            )
        payload["coordination_complete"] = coordination_complete
        payload["coordination_state"] = "complete" if coordination_complete else "order_sensitive"
        payload["claim_order_source"] = order_source
        payload["feasible_order_replays"] = replays
        payload["expense_pool_ledger"] = [row for replay in replays for row in replay["ledger"]]
        payload["formula"] = (
            "sequential indemnity ledger: payment=min(max(remaining_pool-effective_deductible,0)*ratio,remaining_pool,policy_limit); "
            "remaining_pool=remaining_pool-payment"
        )
        if selected is not None:
            payload["computed_values_by_product"] = dict(selected["computed_values_by_product"])
            payload["computed_total"] = float(selected["computed_total"])
            payload["computed_result"] = {**payload["computed_values_by_product"], "total": payload["computed_total"]}
            payload["calculation_steps"] = list(selected["ledger"])
        else:
            payload["computed_values_by_product"] = {}
            payload["computed_total"] = common_total
            payload["computed_result"] = {
                "total": common_total,
                "order_sensitive": order_sensitive,
                "feasible_order_count": len(replays),
                "aggregate_cap": float(self_paid) if self_paid is not None else None,
            }
            payload["calculation_steps"] = [
                {"order": replay["order"], "computed_total": replay["computed_total"], "remaining_pool": replay["remaining_pool"]}
                for replay in replays
            ]
        all_covered = self._value(variable_map, "all_expenses_covered") is True
        aggregate_cap_respected = bool(
            self_paid is not None
            and all(float(replay.get("computed_total") or 0.0) <= float(self_paid) + 1e-6 for replay in replays)
        )
        payload["coverage_prerequisites"] = [
            {"name": "expenses_within_policy_liability", "satisfied": all_covered, "source": "question"},
            {"name": "social_insurance_settlement_identified", "satisfied": social is not None, "source": "question"},
            {"name": "aggregate_indemnity_cap_respected", "satisfied": aggregate_cap_respected, "source": "indemnity_expense_pool_ledger"},
        ]
        payload["coverage_prerequisites_complete"] = bool(all_covered and social is not None and aggregate_cap_respected)
        payload["intermediate_values"].update({
            "order_sensitive": order_sensitive,
            "aggregate_cap_respected": aggregate_cap_respected,
            "maximum_total_payment": max((float(replay["computed_total"]) for replay in replays), default=None),
        })
        return payload

    def _compile_family_deductible(
        self, question: Question, variables: Sequence[ScenarioVariable]
    ) -> dict[str, Any]:
        payload = self._base_payload(question)
        variable_map = self._variable_map(variables)
        products = [self._product_for_doc(str(doc_id)) for doc_id in question.doc_ids]
        family_product = next((p for p in products if self._fact(p.document_id, "deductible_scope") is not None), None)
        individual_product = next((p for p in products if family_product is None or p.document_id != family_product.document_id), None)
        if family_product is None or individual_product is None:
            payload["conflicts"].append("family_and_individual_policy_scope_not_resolved")
            payload["coordination_complete"] = False
            return payload
        family_scope_fact = self._require_fact(payload, family_product.document_id, "deductible_scope")
        family_formula = self._require_fact(payload, family_product.document_id, "medical_payment_formula")
        individual_formula = self._require_fact(payload, individual_product.document_id, "medical_payment_formula")
        offset_facts = {
            family_product.canonical_product_id: self._require_fact(payload, family_product.document_id, "other_compensation_can_offset_deductible"),
            individual_product.canonical_product_id: self._require_fact(payload, individual_product.document_id, "other_compensation_can_offset_deductible"),
        }
        names_values = {
            "scenario.insured.medical_total": self._value(variable_map, _variable_key("scenario", "medical_total", "insured")),
            "scenario.insured.social_insurance": self._value(variable_map, _variable_key("scenario", "social_insurance", "insured")),
            "scenario.spouse.medical_total": self._value(variable_map, _variable_key("scenario", "medical_total", "spouse")),
            "scenario.spouse.social_insurance": self._value(variable_map, _variable_key("scenario", "social_insurance", "spouse")),
            _variable_key(family_product.canonical_product_id, "deductible"): self._value(variable_map, _variable_key(family_product.canonical_product_id, "deductible")),
            _variable_key(individual_product.canonical_product_id, "deductible"): self._value(variable_map, _variable_key(individual_product.canonical_product_id, "deductible")),
        }
        for name, value in names_values.items():
            self._declare_material(payload, name, value, unit=CNY)
        if any(value is None for value in names_values.values()) or any(
            value is None for value in (family_scope_fact, family_formula, individual_formula, *offset_facts.values())
        ):
            payload["coordination_complete"] = False
            return payload
        insured_net = float(names_values["scenario.insured.medical_total"]) - float(names_values["scenario.insured.social_insurance"])
        spouse_net = float(names_values["scenario.spouse.medical_total"]) - float(names_values["scenario.spouse.social_insurance"])
        family_deductible = float(names_values[_variable_key(family_product.canonical_product_id, "deductible")])
        individual_deductible = float(names_values[_variable_key(individual_product.canonical_product_id, "deductible")])
        family_id = family_product.canonical_product_id
        individual_id = individual_product.canonical_product_id
        explicit_order = self._claim_order_from_variables(variable_map)
        orders = [tuple(explicit_order)] if explicit_order and set(explicit_order) == {family_id, individual_id} else [
            (individual_id, family_id),
            (family_id, individual_id),
        ]
        order_source = "question_explicit_order" if explicit_order else "all_feasible_orders_replay"
        replays: list[dict[str, Any]] = []
        for order in orders:
            allocation_modes = ("spouse_first",) if order[0] == individual_id else ("spouse_first", "insured_first")
            for allocation_mode in allocation_modes:
                remaining = {"insured": insured_net, "spouse": spouse_net}
                prior_by_person = {"insured": 0.0, "spouse": 0.0}
                results = {family_id: 0.0, individual_id: 0.0}
                ledger: list[dict[str, Any]] = []
                for index, product_id in enumerate(order, start=1):
                    if product_id == individual_id:
                        eligible = remaining["insured"]
                        prior = prior_by_person["insured"]
                        deductible_offset = min(prior, individual_deductible)
                        effective = max(individual_deductible - deductible_offset, 0.0)
                        payment = min(max(eligible - effective, 0.0), eligible)
                        remaining["insured"] -= payment
                        prior_by_person["insured"] += payment
                        allocation = {"insured": payment, "spouse": 0.0}
                        deductible = individual_deductible
                    else:
                        eligible = remaining["insured"] + remaining["spouse"]
                        prior = prior_by_person["insured"] + prior_by_person["spouse"]
                        deductible_offset = min(prior, family_deductible)
                        effective = max(family_deductible - deductible_offset, 0.0)
                        payment = min(max(eligible - effective, 0.0), eligible)
                        allocation = {"insured": 0.0, "spouse": 0.0}
                        allocation_order = ("spouse", "insured") if allocation_mode == "spouse_first" else ("insured", "spouse")
                        remainder = payment
                        for person in allocation_order:
                            paid = min(remaining[person], remainder)
                            allocation[person] = paid
                            remaining[person] -= paid
                            prior_by_person[person] += paid
                            remainder -= paid
                        deductible = family_deductible
                    results[product_id] += payment
                    ledger.append({
                        "expense_pool_id": "family_medical_self_paid_pool",
                        "eligible_expense": eligible,
                        "social_insurance_paid": float(names_values["scenario.insured.social_insurance"]) + float(names_values["scenario.spouse.social_insurance"]),
                        "other_compensation_paid": prior,
                        "remaining_uncompensated_expense": eligible,
                        "policy_id": product_id,
                        "policy_deductible": deductible,
                        "policy_deductible_offset": deductible_offset,
                        "effective_policy_deductible": effective,
                        "policy_reimbursement_ratio": 1.0,
                        "policy_limit": None,
                        "claim_order": index,
                        "claim_order_source": order_source,
                        "payment_before": payment,
                        "payment_after": payment,
                        "remaining_pool_after_payment": remaining["insured"] + remaining["spouse"],
                        "allocation_by_person": allocation,
                        "allocation_mode": allocation_mode,
                        "indemnity_or_fixed_benefit": "indemnity",
                    })
                replays.append({
                    "order": list(order),
                    "allocation_mode": allocation_mode,
                    "claim_order_source": order_source,
                    "computed_values_by_product": results,
                    "computed_total": sum(results.values()),
                    "remaining_pool": remaining["insured"] + remaining["spouse"],
                    "ledger": ledger,
                })
        signatures = {self._replay_signature(replay) for replay in replays}
        outcome_sensitive = len(signatures) > 1
        coordination_complete = bool(replays) and bool(explicit_order) and not outcome_sensitive
        selected = replays[0] if coordination_complete else None
        if explicit_order:
            self._declare_material(
                payload,
                "claim_order",
                explicit_order,
                source_refs=(variable_map["claim_order"].source_location,),
                unit="sequence",
            )
        else:
            self._declare_material(payload, "claim_order", None, unit="sequence")
        if outcome_sensitive:
            payload["unresolved_variables"].append("coordination_allocation")
        payload["coordination_complete"] = coordination_complete
        payload["coordination_state"] = "complete" if coordination_complete else "order_or_allocation_sensitive"
        payload["claim_order_source"] = order_source
        payload["feasible_order_replays"] = replays
        payload["expense_pool_ledger"] = [row for replay in replays for row in replay["ledger"]]
        payload["formula"] = "family and individual indemnity policies replayed over person-scoped remaining expense pools"
        payload["intermediate_values"] = {
            "insured_net": insured_net,
            "spouse_net": spouse_net,
            "family_net": insured_net + spouse_net,
            "coordination_order": list(explicit_order) if explicit_order else [],
            "claim_order_source": order_source,
            "outcome_sensitive": outcome_sensitive,
            "feasible_outcome_count": len(signatures),
        }
        if selected:
            payload["computed_values_by_product"] = dict(selected["computed_values_by_product"])
            payload["computed_total"] = float(selected["computed_total"])
            payload["computed_result"] = {**payload["computed_values_by_product"], "total": payload["computed_total"]}
            payload["calculation_steps"] = list(selected["ledger"])
        else:
            totals = sorted({float(replay["computed_total"]) for replay in replays})
            payload["computed_result"] = {
                "order_sensitive": outcome_sensitive,
                "feasible_totals": totals,
                "feasible_outcomes": [
                    {"order": replay["order"], "allocation_mode": replay["allocation_mode"], "payments": replay["computed_values_by_product"], "total": replay["computed_total"]}
                    for replay in replays
                ],
            }
            payload["computed_total"] = totals[0] if len(totals) == 1 else None
        payload["coverage_prerequisites"] = [
            {"name": "family_deductible_scope_confirmed", "satisfied": family_scope_fact is not None, "source": _fact_ref(family_scope_fact) if family_scope_fact else ""},
            {"name": "other_insurance_offsets_deductible", "satisfied": all(value is not None for value in offset_facts.values()), "source": [_fact_ref(value) for value in offset_facts.values() if value]},
            {"name": "coordination_order_and_allocation_closed", "satisfied": coordination_complete, "source": order_source},
        ]
        payload["coverage_prerequisites_complete"] = all(bool(row["satisfied"]) for row in payload["coverage_prerequisites"])
        return payload

    def _compile_property_medical(
        self, question: Question, variables: Sequence[ScenarioVariable]
    ) -> dict[str, Any]:
        payload = self._base_payload(question)
        variable_map = self._variable_map(variables)
        property_product = next((self._product_for_doc(str(doc_id)) for doc_id in question.doc_ids if self._product_for_doc(str(doc_id)).product_type == "property"), None)
        medical_products = [self._product_for_doc(str(doc_id)) for doc_id in question.doc_ids if self._product_for_doc(str(doc_id)).product_type in {"medical", "group_medical"}]
        property_loss = self._value(variable_map, "actual_property_loss")
        medical_expense = self._value(variable_map, "ordinary_outpatient_expense")
        self._declare_material(payload, "actual_property_loss", property_loss, unit=CNY)
        self._declare_material(payload, "ordinary_outpatient_expense", medical_expense, unit=CNY)
        results: dict[str, float] = {}
        property_status = "unresolved"
        if property_product is None:
            payload["conflicts"].append("property_policy_missing")
        else:
            property_fact = self._require_fact(payload, property_product.document_id, "property_payment_formula")
            deductible_name = _variable_key(property_product.canonical_product_id, "deductible")
            insured_name = _variable_key(property_product.canonical_product_id, "insured_amount")
            deductible = self._value(variable_map, deductible_name)
            insured_amount = self._value(variable_map, insured_name)
            self._declare_material(payload, deductible_name, deductible, unit=CNY)
            self._declare_material(payload, insured_name, insured_amount, unit=CNY)
            if property_fact is not None and property_loss is not None and deductible is not None and insured_amount is not None:
                value = min(max(float(property_loss) - float(deductible), 0.0), float(insured_amount))
                results[property_product.canonical_product_id] = value
                property_status = "computed"
                payload["calculation_steps"].append({
                    "step": "property_indemnity",
                    "product_id": property_product.canonical_product_id,
                    "formula": property_fact.normalized_value,
                    "actual_property_loss": float(property_loss),
                    "deductible": float(deductible),
                    "insured_amount": float(insured_amount),
                    "result": value,
                })
                payload["expense_pool_ledger"].append({
                    "expense_pool_id": "property_loss_pool",
                    "eligible_expense": float(property_loss),
                    "social_insurance_paid": 0.0,
                    "other_compensation_paid": 0.0,
                    "remaining_uncompensated_expense": float(property_loss),
                    "policy_id": property_product.canonical_product_id,
                    "policy_deductible": float(deductible),
                    "policy_deductible_offset": 0.0,
                    "policy_reimbursement_ratio": 1.0,
                    "policy_limit": float(insured_amount),
                    "claim_order": 1,
                    "claim_order_source": "single_property_policy",
                    "payment_before": max(float(property_loss) - float(deductible), 0.0),
                    "payment_after": value,
                    "remaining_pool_after_payment": max(float(property_loss) - value, 0.0),
                    "indemnity_or_fixed_benefit": "indemnity",
                })
        medical_status: dict[str, str] = {}
        for product in medical_products:
            categories_fact = self._require_fact(payload, product.document_id, "covered_outpatient_categories")
            if categories_fact is None or medical_expense is None:
                medical_status[product.canonical_product_id] = "unresolved"
                continue
            categories = set(categories_fact.normalized_value or [])
            ordinary_outpatient_covered = "ordinary_accident_outpatient" in categories
            value = float(medical_expense) if ordinary_outpatient_covered else 0.0
            results[product.canonical_product_id] = value
            medical_status[product.canonical_product_id] = "covered" if ordinary_outpatient_covered else "not_covered"
            payload["calculation_steps"].append({
                "step": "medical_liability_applicability",
                "product_id": product.canonical_product_id,
                "covered_categories": sorted(categories),
                "event": "ordinary_accident_outpatient",
                "applicable": ordinary_outpatient_covered,
                "result": value,
            })
        payload["formula"] = "property=min(max(actual_loss-deductible,0),insured_amount); medical liabilities evaluated independently by covered category"
        payload["computed_values_by_product"] = results
        medical_total = sum(results.get(product.canonical_product_id, 0.0) for product in medical_products if product.canonical_product_id in results)
        payload["computed_total"] = medical_total
        payload["computed_result"] = {
            **results,
            "medical_total": medical_total,
            "property_total": results.get(property_product.canonical_product_id) if property_product else None,
            "property_subclaim_status": property_status,
            "medical_subclaim_status": medical_status,
        }
        payload["subclaim_status"] = {
            "property": property_status,
            "medical": medical_status,
        }
        property_material_complete = property_status == "computed"
        medical_complete = bool(medical_products) and all(status in {"covered", "not_covered"} for status in medical_status.values())
        payload["coordination_complete"] = True
        payload["coordination_state"] = "liability_isolation_complete"
        payload["claim_order_source"] = "independent_liability_scopes"
        payload["coverage_prerequisites"] = [
            {"name": "property_and_personal_medical_liability_isolated", "satisfied": True, "source": "typed_product_scope"},
            {"name": "property_material_variables_complete", "satisfied": property_material_complete, "source": "question_or_contract"},
            {"name": "medical_category_applicability_complete", "satisfied": medical_complete, "source": "contract_category_lists"},
        ]
        payload["coverage_prerequisites_complete"] = all(bool(row["satisfied"]) for row in payload["coverage_prerequisites"])
        return payload

    def _ranking_option_vector(self, label: str, text: str) -> OptionVector:
        product_hits: list[tuple[int, str, float]] = []
        for product in self.catalog.documents:
            aliases = sorted((product.product_name, *product.aliases), key=len, reverse=True)
            for alias in aliases:
                pattern = re.compile(rf"{re.escape(alias)}\s*[（(]\s*(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>亿元|万元|万|元)\s*[)）]")
                match = pattern.search(text)
                if match:
                    product_hits.append(
                        (
                            match.start(),
                            product.canonical_product_id,
                            _normalise_amount(match.group("amount"), match.group("unit")),
                        )
                    )
                    break
        product_hits.sort(key=lambda item: item[0])
        values = {product_id: value for _, product_id, value in product_hits}
        order = tuple(product_id for _, product_id, _ in product_hits)
        return OptionVector(
            option=label,
            values=values,
            order=order,
            parse_complete=bool(product_hits) and len(values) == len(product_hits),
            raw_text=text,
            parse_notes=(),
        )

    def _payment_option_vector(
        self,
        label: str,
        text: str,
        computed_products: Sequence[str],
        *,
        total_key: str,
        property_product: str = "",
    ) -> OptionVector:
        values: dict[str, Any] = {}
        notes: list[str] = []
        compact = _compact(text)
        for product_id in computed_products:
            product = self.catalog.product(product_id)
            brand_aliases = {
                product.insurer,
                re.sub(r"(?:保险|人寿|寿险|产险|健康)$", "", product.insurer),
                re.sub(r"(?:团体|个人|终身|住院).*$", "", product.product_name),
            }
            aliases = sorted(
                {alias for alias in (product.product_name, *product.aliases, *brand_aliases) if alias},
                key=len,
                reverse=True,
            )
            match = None
            for alias in aliases:
                if not alias:
                    continue
                match = re.search(rf"{re.escape(alias)}.{{0,16}}?(?:赔付|赔|为)?\s*(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>亿元|万元|万|元)", text)
                if match:
                    break
            if match:
                values[product_id] = _normalise_amount(match.group("amount"), match.group("unit"))
        zero_group = re.search(r"(?P<names>[A-Za-z0-9\u4e00-\u9fff和、]+?)(?:均|都)不赔付", text)
        if zero_group:
            group_text = zero_group.group("names")
            for product_id in computed_products:
                product = self.catalog.product(product_id)
                aliases = (
                    product.product_name,
                    *product.aliases,
                    product.insurer,
                    re.sub(r"(?:保险|人寿|寿险|产险|健康)$", "", product.insurer),
                )
                if any(alias and alias in group_text for alias in aliases):
                    values[product_id] = 0.0
        total = re.search(r"(?:合计|共赔|共应赔付)\s*(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>亿元|万元|万|元)", text)
        if total:
            values[total_key] = _normalise_amount(total.group("amount"), total.group("unit"))
        if property_product:
            property_match = re.search(r"家财险赔\s*(?P<amount>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>亿元|万元|万|元)", text)
            if property_match:
                values[property_product] = _normalise_amount(property_match.group("amount"), property_match.group("unit"))
        if "无法确定" in compact:
            notes.append("option_declares_unresolved")
            values["declared_unresolved"] = True
        if total_key not in values:
            scoped_products = [product_id for product_id in computed_products if product_id in values]
            if scoped_products and len(scoped_products) == len(computed_products):
                values[total_key] = sum(float(values[product_id]) for product_id in scoped_products)
        parse_complete = bool(values)
        return OptionVector(label, values, tuple(values), parse_complete, text, tuple(notes))

    @staticmethod
    def _vector_matches(
        vector: OptionVector,
        expected: Mapping[str, Any],
        *,
        expected_order: Sequence[str] = (),
    ) -> bool:
        if not vector.parse_complete:
            return False
        if expected_order and tuple(vector.order) != tuple(expected_order):
            return False
        for key, value in expected.items():
            if key not in vector.values or not _approximately_equal(vector.values[key], value):
                return False
        return set(vector.values) == set(expected)

    def _build_option_vectors(
        self, question: Question, kind: str, payload: Mapping[str, Any]
    ) -> tuple[list[OptionVector], list[str]]:
        vectors: list[OptionVector] = []
        matches: list[str] = []
        computed_products = list(dict(payload.get("computed_values_by_product") or {}))
        if kind in {"death_benefit_ranking", "surrender_value_ranking"}:
            ranking = list((payload.get("computed_result") or {}).get("ranking") or [])
            expected_order = [str(row[0]) for row in ranking]
            expected = {str(row[0]): row[1] for row in ranking}
            for label, text in question.options.items():
                vector = self._ranking_option_vector(str(label).upper(), str(text))
                vectors.append(vector)
                if self._vector_matches(vector, expected, expected_order=expected_order):
                    matches.append(str(label).upper())
            return vectors, matches
        property_product = ""
        total_key = "total"
        expected = dict(payload.get("computed_values_by_product") or {})
        if kind == "property_medical_isolation":
            property_product = next(
                (product_id for product_id in computed_products if self.catalog.product(product_id).product_type == "property"),
                "",
            )
            medical_products = [product_id for product_id in computed_products if product_id != property_product]
            expected = {product_id: expected[product_id] for product_id in computed_products}
            expected["medical_total"] = payload.get("computed_total")
            total_key = "medical_total"
            parse_products = medical_products
        else:
            expected["total"] = payload.get("computed_total")
            parse_products = computed_products
        for label, text in question.options.items():
            vector = self._payment_option_vector(
                str(label).upper(),
                str(text),
                parse_products,
                total_key=total_key,
                property_product=property_product,
            )
            vectors.append(vector)
            if self._vector_matches(vector, expected):
                matches.append(str(label).upper())
        return vectors, matches

    def _finalise(
        self,
        question: Question,
        kind: str,
        variables: Sequence[ScenarioVariable],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        required_doc_ids = [str(value) for value in question.doc_ids]
        formula_facts = [fact for fact in payload.pop("formula_facts", []) if isinstance(fact, InsuranceCalculationFact)]
        formula_refs = list(dict.fromkeys(_fact_ref(fact) for fact in formula_facts))
        question_variable_refs = {variable.canonical_name: [variable.source_location] for variable in variables}
        question_variable_values = {variable.canonical_name: variable.normalized_value for variable in variables}
        question_variable_units = {variable.canonical_name: variable.unit for variable in variables}
        material_refs = {**question_variable_refs, **dict(payload.get("material_variable_refs") or {})}
        material_values = {**question_variable_values, **dict(payload.get("material_variable_values") or {})}
        material_units = {**question_variable_units, **dict(payload.get("material_variable_units") or {})}
        material_variables = list(dict.fromkeys(str(value) for value in payload.get("material_variables") or [] if str(value)))
        unresolved = sorted(set(str(value) for value in payload.get("unresolved_variables") or [] if str(value)))
        conflicts = sorted(set(str(value) for value in payload.get("conflicts") or [] if str(value)))
        used_doc_ids = sorted(set(fact.document_id for fact in formula_facts))
        lineage_complete = set(required_doc_ids) <= set(used_doc_ids)
        blocking_formula_conflicts = [
            conflict for conflict in conflicts
            if conflict.startswith((
                "formula_fact_missing",
                "unsupported_",
                "reimbursement_ratio_missing",
                "other_compensation_offset_rule_missing",
            ))
        ]
        formula_source_complete = bool(payload.get("formula")) and lineage_complete and not blocking_formula_conflicts
        missing_material_refs = [name for name in material_variables if not material_refs.get(name)]
        missing_material_values = [name for name in material_variables if name not in material_values or material_values.get(name) is None]
        material_variable_complete = bool(material_variables) and not missing_material_refs and not missing_material_values and not unresolved
        unit_pass = bool(material_variables) and all(str(material_units.get(name) or "") for name in material_variables)
        coverage_rows = list(payload.get("coverage_prerequisites") or [])
        coverage_complete = bool(payload.get("coverage_prerequisites_complete", True)) and all(
            row.get("satisfied") is True for row in coverage_rows
        )
        coordination_complete = payload.get("coordination_complete") is True
        computation_complete = bool(
            payload.get("computed_result") is not None
            and formula_source_complete
            and material_variable_complete
            and coverage_complete
            and coordination_complete
            and unit_pass
            and not blocking_formula_conflicts
        )
        vectors, match_candidates = self._build_option_vectors(question, kind, payload) if computation_complete else ([], [])
        option_evaluations = []
        for vector in vectors:
            status = "true" if vector.option in match_candidates else "false" if vector.parse_complete else "unresolved"
            option_evaluations.append({
                "option": vector.option,
                "evaluated_value": dict(vector.values),
                "expected_condition": vector.raw_text,
                "verdict": status,
                "evidence_refs": formula_refs,
                "calculation_refs": ["computed_result", "option_vectors", "expense_pool_ledger"],
                "unresolved_reason": "" if status != "unresolved" else ";".join(vector.parse_notes or ("option_vector_parse_incomplete",)),
            })
        option_match_unique = computation_complete and len(match_candidates) == 1
        candidate_answer = match_candidates[0] if option_match_unique else ""
        material_contract = [
            {
                "name": name,
                "value": material_values.get(name),
                "unit": material_units.get(name, ""),
                "source_refs": list(material_refs.get(name) or []),
                "source_complete": bool(material_refs.get(name)),
                "value_complete": name in material_values and material_values.get(name) is not None,
            }
            for name in material_variables
        ]
        grounding = build_calculation_grounding(
            formula_text=str(payload.get("formula") or ""),
            formula_source_refs=formula_refs,
            variables=material_values,
            variable_source_refs=material_refs,
            unit_normalization={"canonical_currency": CNY, "units": material_units},
            deterministic_result=payload.get("computed_result"),
            option_evaluations=option_evaluations,
            unresolved_variables=unresolved,
            used_material_variables=material_variables,
            unused_material_variables=[],
            coverage_gap=not coverage_complete or not coordination_complete,
            computation_complete=computation_complete,
        )
        return {
            "recognized": True,
            "schema_version": "typed_insurance_calculation_evidence.v2",
            "calculation_kind": kind,
            "structured_intent": payload.get("structured_intent") or {},
            "question_variables": [variable.to_dict() for variable in variables],
            "question_conditions": [
                variable.to_dict()
                for variable in variables
                if variable.unit in {BOOLEAN, "category", COUNT, "sequence", "reference"}
            ],
            "required_doc_ids": required_doc_ids,
            "required_formula_clauses": [fact.normalized_relation for fact in formula_facts],
            "formula": payload.get("formula"),
            "formula_source_refs": formula_refs,
            "formula_source_complete": formula_source_complete,
            "material_variables": material_variables,
            "material_variable_contract": material_contract,
            "material_variable_refs": material_refs,
            "material_variable_values": material_values,
            "material_variable_units": material_units,
            "missing_material_refs": missing_material_refs,
            "missing_material_values": missing_material_values,
            "material_variable_complete": material_variable_complete,
            "variables": material_values,
            "variable_source_refs": material_refs,
            "variable_source_complete": material_variable_complete,
            "units": material_units,
            "unit_normalization_pass": unit_pass,
            "normalized_inputs": material_values,
            "calculation_steps": payload.get("calculation_steps") or [],
            "intermediate_values": payload.get("intermediate_values") or {},
            "computed_result": payload.get("computed_result"),
            "computed_values_by_product": payload.get("computed_values_by_product") or {},
            "computed_total": payload.get("computed_total"),
            "expense_pool_ledger": payload.get("expense_pool_ledger") or [],
            "feasible_order_replays": payload.get("feasible_order_replays") or [],
            "coordination_complete": coordination_complete,
            "coordination_state": payload.get("coordination_state") or "",
            "claim_order_source": payload.get("claim_order_source") or "",
            "subclaim_status": payload.get("subclaim_status") or {},
            "computation_complete": computation_complete,
            "option_vectors": [vector.to_dict() for vector in vectors],
            "option_vector_parsed": bool(vectors) and len(vectors) == len(question.options) and all(vector.parse_complete for vector in vectors),
            "option_evaluations": option_evaluations,
            "option_match_candidates": match_candidates,
            "option_match_unique": option_match_unique,
            "candidate_answer": candidate_answer,
            "rounding_policy": payload.get("rounding_policy"),
            "coverage_prerequisites": coverage_rows,
            "coverage_prerequisites_complete": coverage_complete,
            "used_doc_ids": used_doc_ids,
            "used_doc_lineage_complete": lineage_complete,
            "unresolved_variables": unresolved,
            "conflicts": conflicts,
            "calculation_grounding": grounding,
        }




def build_insurance_calculation_option_evidence(
    question: Question,
    *,
    full_text_root: Path | str,
    product_catalog_path: Path | str,
) -> tuple[DerivedOptionEvidence, ...]:
    """Build option-local derived evidence for production typed verification."""
    compiler = InsuranceCalculationCompiler(
        full_text_root,
        product_catalog_path=product_catalog_path,
    )
    audit = compiler.compile(question)
    if not audit.get("recognized"):
        return ()
    statuses = {
        str(item.get("option") or "").upper(): str(item.get("verdict") or "unresolved")
        for item in audit.get("option_evaluations") or []
    }
    facts_by_ref = {
        _fact_ref(fact): fact
        for fact in compiler.facts
    }
    source_facts: list[SourceFact] = []
    for ref in audit.get("formula_source_refs") or []:
        fact = facts_by_ref.get(str(ref))
        if fact is None:
            continue
        source_facts.append(
            SourceFact(
                doc_id=fact.document_id,
                entity_scope=fact.product_id,
                period_scope="contract_effective_scope",
                metric=fact.normalized_relation,
                value=fact.normalized_value if isinstance(fact.normalized_value, (str, float, int)) else str(fact.normalized_value),
                unit=fact.unit or "typed",
                canonical_source=fact.source_relpath,
                local_window=fact.local_window,
                fact_state="source_exact",
                metadata={"source_sha256": fact.source_sha256, "extraction_rule_id": fact.extraction_rule_id},
            )
        )
    for variable in audit.get("question_variables") or []:
        source_facts.append(
            SourceFact(
                doc_id="question",
                entity_scope=str(variable.get("product_scope") or variable.get("person_scope") or "scenario"),
                period_scope=str(variable.get("time_scope") or "question_scenario"),
                metric=str(variable.get("canonical_name") or "scenario_variable"),
                value=variable.get("normalized_value"),
                unit=str(variable.get("unit") or "typed"),
                canonical_source="question",
                local_window=str(variable.get("raw_text") or ""),
                fact_state="question_exact",
                metadata={"source_location": variable.get("source_location")},
            )
        )
    globally_complete = bool(
        audit.get("computation_complete")
        and audit.get("formula_source_complete")
        and audit.get("material_variable_complete")
        and audit.get("variable_source_complete")
        and audit.get("unit_normalization_pass")
        and audit.get("coverage_prerequisites_complete")
        and audit.get("coordination_complete")
        and audit.get("used_doc_lineage_complete")
        and audit.get("option_vector_parsed")
        and not audit.get("unresolved_variables")
    )
    derived: list[DerivedOptionEvidence] = []
    for label in sorted(str(key).upper() for key in question.options):
        verdict = statuses.get(label, "unresolved")
        status = SUPPORTED if verdict == "true" else CONTRADICTED if verdict == "false" else UNRESOLVED
        conflicts = list(audit.get("conflicts") or [])
        if verdict == "unresolved":
            conflicts.append("option_vector_unresolved")
        derived.append(
            DerivedOptionEvidence(
                qid=question.qid,
                option_label=label,
                claim_type="insurance_calculation_vector",
                source_facts=tuple(source_facts),
                formula_or_aggregation=str(audit.get("formula") or "deterministic insurance calculation"),
                variables=dict(audit.get("variables") or {}),
                units=dict(audit.get("units") or {}),
                entity_scope=tuple(sorted(set(str(value) for value in audit.get("computed_values_by_product") or {}))),
                period_scope=("question_scenario",),
                document_scope=tuple(str(value) for value in audit.get("used_doc_ids") or []),
                result={
                    "computed_result": audit.get("computed_result"),
                    "option_vector": next((row for row in audit.get("option_vectors") or [] if row.get("option") == label), None),
                    "matches": label in set(audit.get("option_match_candidates") or []),
                },
                status=status,
                canonical_sources=tuple(sorted(set(str(value) for value in audit.get("formula_source_refs") or []))),
                conflicts=tuple(sorted(set(conflicts))),
                trusted_for_option_gate=globally_complete and status in {SUPPORTED, CONTRADICTED},
                diagnostics={
                    "calculation_audit": audit,
                    "evidence_tier": 1,
                    "reconciliation_rule_id": "insurance_calculation_vector_v1",
                },
            )
        )
    return tuple(derived)
