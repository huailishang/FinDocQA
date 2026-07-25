"""Typed insurance-clause ledger with product/document lineage.

Production code in this module is dataset-neutral. Product identities, source
anchors and claim templates are supplied by an explicit registry. Every fact is
materialized from the authoritative full-text corpus and keeps source hash,
lineage and typed clause fields. Missing or mismatched lineage fails closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from verification.insurance_clause_extractor import reject_curated_fixture_metadata
from verification.scope_absence import (
    TrustedDocumentSource,
    build_scope_absence_proof,
    validate_scope_absence_proof,
)

INSURANCE_SOURCE_ROOT_IDENTITY = "afac_insurance_full_text.v1"
AUTHORITATIVE_VERDICTS = {
    "supported", "contradicted", "scope_absent", "not_applicable",
}
ALL_VERDICTS = AUTHORITATIVE_VERDICTS | {"unresolved"}


def _compact(value: Any) -> str:
    text = re.sub(r"<sup[^>]*>.*?</sup>", "", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", "", text).replace("％", "%").lower()


def _tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


@dataclass(frozen=True)
class InsuranceDocumentSpec:
    document_id: str
    product_id: str
    product_name: str
    product_type: str
    insurer: str
    source_relpath: str
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "InsuranceDocumentSpec":
        return cls(
            document_id=str(row.get("document_id") or ""),
            product_id=str(row.get("product_id") or ""),
            product_name=str(row.get("product_name") or ""),
            product_type=str(row.get("product_type") or ""),
            insurer=str(row.get("insurer") or ""),
            source_relpath=str(row.get("source_relpath") or ""),
            aliases=_tuple(row.get("aliases")),
        )


@dataclass(frozen=True)
class InsuranceFactSeed:
    fact_id: str
    document_id: str
    source_anchor: str
    clause_category: str
    clause_subtype: str
    trigger_event: str
    waiting_period: str
    exclusion: str
    right_or_obligation: str
    payment_formula: str
    benefit_scope: str
    deductible_rule: str
    grace_or_suspension_state: str
    fact_state: str
    subject_text: str
    predicate_text: str
    object_text: str
    covered_event: str = ""
    excluded_event: str = ""
    exception_to_exclusion: str = ""
    benefit_type: str = ""
    trigger_condition: str = ""
    waiting_period_days: str = ""
    accident_exception: str = ""
    loan_allowed: str = ""
    loan_limit_ratio: str = ""
    loan_base: str = ""
    conditional_prohibition: str = ""
    prescription_review_required: str = ""
    designated_pharmacy_required: str = ""
    direct_settlement_required: str = ""
    grace_period_days: str = ""
    suspension_effect: str = ""
    cash_value_definition_type: str = ""
    annuity_change_dimension: str = ""
    rescue_expense_cap: str = ""
    deductible_offset_source: str = ""
    hesitation_period_refund_rule: str = ""
    fact_polarity: str = ""
    question_scope_binding: str = "unbound"
    rejection_reasons: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "InsuranceFactSeed":
        values: dict[str, Any] = {}
        for name in cls.__dataclass_fields__:
            if name == "rejection_reasons":
                values[name] = _tuple(row.get(name))
            else:
                values[name] = str(row.get(name) or "")
        return cls(**values)


@dataclass(frozen=True)
class InsuranceFact:
    fact_id: str
    document_id: str
    product_id: str
    product_name: str
    product_type: str
    insurer: str
    source_path: str
    source_relpath: str
    source_sha256: str
    page_or_line: int
    local_window: str
    source_anchor: str
    clause_category: str
    clause_subtype: str
    trigger_event: str
    waiting_period: str
    exclusion: str
    right_or_obligation: str
    payment_formula: str
    benefit_scope: str
    deductible_rule: str
    grace_or_suspension_state: str
    fact_state: str
    subject_text: str
    predicate_text: str
    object_text: str
    canonical_product_id: str = ""
    covered_event: str = ""
    excluded_event: str = ""
    exception_to_exclusion: str = ""
    benefit_type: str = ""
    trigger_condition: str = ""
    waiting_period_days: str = ""
    accident_exception: str = ""
    loan_allowed: str = ""
    loan_limit_ratio: str = ""
    loan_base: str = ""
    conditional_prohibition: str = ""
    prescription_review_required: str = ""
    designated_pharmacy_required: str = ""
    direct_settlement_required: str = ""
    grace_period_days: str = ""
    suspension_effect: str = ""
    cash_value_definition_type: str = ""
    annuity_change_dimension: str = ""
    rescue_expense_cap: str = ""
    deductible_offset_source: str = ""
    hesitation_period_refund_rule: str = ""
    fact_polarity: str = ""
    question_scope_binding: str = "unbound"
    rejection_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InsuranceClaimTemplate:
    template_id: str
    option_text: str
    required_terms: tuple[str, ...]
    fact_ids: tuple[str, ...]
    verdict: str
    claim_type: str
    reason: str
    product_document_ids: tuple[str, ...]
    absence_query_terms: tuple[str, ...] = ()
    absence_alias_groups: tuple[tuple[str, ...], ...] = ()
    document_absent_verdict: str = ""

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "InsuranceClaimTemplate":
        groups = row.get("absence_alias_groups") or []
        return cls(
            template_id=str(row.get("template_id") or ""),
            option_text=str(row.get("option_text") or ""),
            required_terms=_tuple(row.get("required_terms")),
            fact_ids=_tuple(row.get("fact_ids")),
            verdict=str(row.get("verdict") or "unresolved"),
            claim_type=str(row.get("claim_type") or "insurance_clause"),
            reason=str(row.get("reason") or ""),
            product_document_ids=_tuple(row.get("product_document_ids")),
            absence_query_terms=_tuple(row.get("absence_query_terms")),
            absence_alias_groups=tuple(_tuple(group) for group in groups),
            document_absent_verdict=str(row.get("document_absent_verdict") or ""),
        )

    def matches(self, option_text: str) -> bool:
        option = _compact(option_text)
        if self.option_text and option == _compact(self.option_text):
            return True
        return bool(self.required_terms) and all(_compact(term) in option for term in self.required_terms)


@dataclass(frozen=True)
class InsuranceClauseRegistry:
    metadata: Mapping[str, Any]
    documents: tuple[InsuranceDocumentSpec, ...]
    fact_seeds: tuple[InsuranceFactSeed, ...]
    claim_templates: tuple[InsuranceClaimTemplate, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InsuranceClauseRegistry":
        return cls(
            metadata=dict(payload.get("metadata") or {}),
            documents=tuple(
                InsuranceDocumentSpec.from_mapping(row)
                for row in payload.get("documents") or []
            ),
            fact_seeds=tuple(
                InsuranceFactSeed.from_mapping(row)
                for row in payload.get("fact_seeds") or []
            ),
            claim_templates=tuple(
                InsuranceClaimTemplate.from_mapping(row)
                for row in payload.get("claim_templates") or []
            ),
        )


def load_insurance_clause_registry(
    path: str | Path,
    *,
    allow_curated_fixture_for_offline_evaluation: bool = False,
) -> InsuranceClauseRegistry:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("insurance clause registry must be a mapping")
    reject_curated_fixture_metadata(
        dict(payload.get("metadata") or {}),
        allow_curated_fixture_for_offline_evaluation=(
            allow_curated_fixture_for_offline_evaluation
        ),
    )
    return InsuranceClauseRegistry.from_mapping(payload)


class InsuranceClauseLedger:
    """Materialize typed facts and compile option-local clause verdicts."""

    def __init__(
        self,
        full_text_root: str | Path,
        *,
        registry: InsuranceClauseRegistry,
        scope_scan_run_id: str,
    ) -> None:
        self.full_text_root = Path(full_text_root).resolve()
        self.registry = registry
        self.scope_scan_run_id = str(scope_scan_run_id)
        if not self.scope_scan_run_id:
            raise ValueError("scope_scan_run_id is required")
        self._documents = {row.document_id: row for row in registry.documents}
        if len(self._documents) != len(registry.documents):
            raise ValueError("duplicate insurance document_id")
        self._facts: dict[str, InsuranceFact] = {}
        for seed in registry.fact_seeds:
            if seed.fact_id in self._facts:
                raise ValueError(f"duplicate insurance fact_id: {seed.fact_id}")
            self._facts[seed.fact_id] = self._materialize(seed)

    def document_spec(self, document_id: str) -> InsuranceDocumentSpec:
        return self._documents[str(document_id)]

    def document_path(self, document_id: str) -> Path:
        return self.full_text_root / self.document_spec(document_id).source_relpath

    def trusted_document_sources(
        self, document_ids: Sequence[str]
    ) -> dict[str, TrustedDocumentSource]:
        return {
            str(doc_id): TrustedDocumentSource(
                canonical_doc_id=str(doc_id),
                source_root_identity=INSURANCE_SOURCE_ROOT_IDENTITY,
                source_root=str(self.full_text_root),
                source_relpath=self.document_spec(str(doc_id)).source_relpath,
            )
            for doc_id in document_ids
        }

    def _materialize(self, seed: InsuranceFactSeed) -> InsuranceFact:
        spec = self.document_spec(seed.document_id)
        path = self.document_path(seed.document_id)
        data = path.read_bytes()
        text = data.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        anchor = _compact(seed.source_anchor)
        hits = [index for index, line in enumerate(lines) if anchor in _compact(line)]
        if not hits:
            raise ValueError(
                f"insurance fact anchor missing: {seed.fact_id}: {path}: {seed.source_anchor}"
            )
        index = hits[0]
        window = "\n".join(lines[max(0, index - 2):min(len(lines), index + 3)]).strip()
        return InsuranceFact(
            fact_id=seed.fact_id,
            document_id=seed.document_id,
            product_id=spec.product_id,
            product_name=spec.product_name,
            product_type=spec.product_type,
            insurer=spec.insurer,
            source_path=str(path),
            source_relpath=spec.source_relpath,
            source_sha256=sha256(data).hexdigest(),
            page_or_line=index + 1,
            local_window=window,
            source_anchor=seed.source_anchor,
            clause_category=seed.clause_category,
            clause_subtype=seed.clause_subtype,
            trigger_event=seed.trigger_event,
            waiting_period=seed.waiting_period,
            exclusion=seed.exclusion,
            right_or_obligation=seed.right_or_obligation,
            payment_formula=seed.payment_formula,
            benefit_scope=seed.benefit_scope,
            deductible_rule=seed.deductible_rule,
            grace_or_suspension_state=seed.grace_or_suspension_state,
            fact_state=seed.fact_state,
            subject_text=seed.subject_text,
            predicate_text=seed.predicate_text,
            object_text=seed.object_text,
            canonical_product_id=spec.product_id,
            covered_event=seed.covered_event,
            excluded_event=seed.excluded_event,
            exception_to_exclusion=seed.exception_to_exclusion,
            benefit_type=seed.benefit_type,
            trigger_condition=seed.trigger_condition,
            waiting_period_days=seed.waiting_period_days,
            accident_exception=seed.accident_exception,
            loan_allowed=seed.loan_allowed,
            loan_limit_ratio=seed.loan_limit_ratio,
            loan_base=seed.loan_base,
            conditional_prohibition=seed.conditional_prohibition,
            prescription_review_required=seed.prescription_review_required,
            designated_pharmacy_required=seed.designated_pharmacy_required,
            direct_settlement_required=seed.direct_settlement_required,
            grace_period_days=seed.grace_period_days,
            suspension_effect=seed.suspension_effect,
            cash_value_definition_type=seed.cash_value_definition_type,
            annuity_change_dimension=seed.annuity_change_dimension,
            rescue_expense_cap=seed.rescue_expense_cap,
            deductible_offset_source=seed.deductible_offset_source,
            hesitation_period_refund_rule=seed.hesitation_period_refund_rule,
            fact_polarity=seed.fact_polarity,
            question_scope_binding=seed.question_scope_binding or "unbound",
            rejection_reasons=seed.rejection_reasons,
        )

    @property
    def facts(self) -> tuple[InsuranceFact, ...]:
        return tuple(self._facts[key] for key in sorted(self._facts))

    def fact(self, fact_id: str) -> InsuranceFact:
        return self._facts[fact_id]

    def _template(self, option_text: str) -> InsuranceClaimTemplate | None:
        matches = [row for row in self.registry.claim_templates if row.matches(option_text)]
        if not matches:
            return None
        matches.sort(
            key=lambda row: (bool(row.option_text), sum(len(_compact(x)) for x in row.required_terms)),
            reverse=True,
        )
        return matches[0]

    def audit_option(
        self,
        *,
        question_text: str,
        option_label: str,
        option_text: str,
        declared_doc_ids: Sequence[str],
    ) -> dict[str, Any]:
        declared = tuple(dict.fromkeys(str(value) for value in declared_doc_ids))
        template = self._template(option_text)
        if template is None:
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                verdict="unresolved",
                authoritative=False,
                reason="no insurance claim template matched",
                template_id="unmatched_claim_shape",
                facts=(),
                declared_doc_ids=declared,
            )
        if template.verdict not in ALL_VERDICTS:
            raise ValueError(f"invalid insurance verdict: {template.verdict}")

        required_docs = tuple(dict.fromkeys(template.product_document_ids))
        undeclared = tuple(doc for doc in required_docs if doc not in declared)
        if undeclared and template.document_absent_verdict == "not_applicable":
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                verdict="not_applicable",
                authoritative=True,
                reason=template.reason or "product document is outside question scope",
                template_id=template.template_id,
                facts=(),
                declared_doc_ids=declared,
                product_document_ids=required_docs,
                out_of_scope_doc_ids=undeclared,
            )
        if undeclared:
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                verdict="unresolved",
                authoritative=False,
                reason="claim fact source is outside declared product documents",
                template_id=template.template_id,
                facts=(),
                declared_doc_ids=declared,
                product_document_ids=required_docs,
                out_of_scope_doc_ids=undeclared,
            )

        facts = tuple(self.fact(fact_id) for fact_id in template.fact_ids)
        if any(fact.document_id not in declared for fact in facts):
            return self._payload(
                option_label=option_label,
                option_text=option_text,
                verdict="unresolved",
                authoritative=False,
                reason="typed fact lineage is outside declared product documents",
                template_id=template.template_id,
                facts=facts,
                declared_doc_ids=declared,
                product_document_ids=required_docs,
            )

        proof_payload: dict[str, Any] | None = None
        proof_valid = False
        proof_errors: tuple[str, ...] = ()
        verdict = template.verdict
        authoritative = verdict in AUTHORITATIVE_VERDICTS
        if verdict == "scope_absent":
            trusted = self.trusted_document_sources(required_docs)
            proof = build_scope_absence_proof(
                trusted_declared_documents=trusted,
                query_terms=template.absence_query_terms,
                query_alias_groups=template.absence_alias_groups,
                scan_timestamp_or_run_id=self.scope_scan_run_id,
            )
            validation = validate_scope_absence_proof(
                proof, trusted_declared_documents=trusted
            )
            proof_payload = proof.to_dict()
            proof_valid = validation.valid
            proof_errors = validation.errors
            authoritative = validation.valid
            if not validation.valid:
                verdict = "unresolved"

        return self._payload(
            option_label=option_label,
            option_text=option_text,
            verdict=verdict,
            authoritative=authoritative,
            reason=template.reason,
            template_id=template.template_id,
            facts=facts,
            declared_doc_ids=declared,
            product_document_ids=required_docs,
            scope_absence_proof=proof_payload,
            scope_absence_proof_valid=proof_valid,
            scope_absence_proof_errors=proof_errors,
            claim_type=template.claim_type,
        )

    def _payload(
        self,
        *,
        option_label: str,
        option_text: str,
        verdict: str,
        authoritative: bool,
        reason: str,
        template_id: str,
        facts: Sequence[InsuranceFact],
        declared_doc_ids: Sequence[str],
        product_document_ids: Sequence[str] = (),
        out_of_scope_doc_ids: Sequence[str] = (),
        scope_absence_proof: Mapping[str, Any] | None = None,
        scope_absence_proof_valid: bool = False,
        scope_absence_proof_errors: Sequence[str] = (),
        claim_type: str = "insurance_clause",
    ) -> dict[str, Any]:
        fact_rows = [fact.to_dict() for fact in facts]
        sources = list(dict.fromkeys(fact.source_relpath for fact in facts))
        if verdict == "not_applicable" and not sources:
            sources = [f"declared_scope_excludes_document:{doc_id}" for doc_id in product_document_ids]
        windows = list(dict.fromkeys(fact.local_window for fact in facts))
        return {
            "option": str(option_label).upper(),
            "option_text": option_text,
            "verdict": verdict,
            "status": verdict,
            "authoritative": authoritative,
            "trusted_for_option_gate": authoritative,
            "claim_type": claim_type,
            "claim_route": (
                "scope_only" if verdict in {"scope_absent", "not_applicable"}
                else "contradiction" if verdict == "contradicted"
                else "exact_clause" if verdict == "supported"
                else "insurance_clause_unresolved"
            ),
            "typed_claim_route": "insurance_clause_ledger",
            "template_id": template_id,
            "reason": reason,
            "declared_doc_ids": list(declared_doc_ids),
            "product_document_ids": list(product_document_ids),
            "out_of_scope_doc_ids": list(out_of_scope_doc_ids),
            "facts": fact_rows,
            "source_document": sources,
            "source_path": sources,
            "canonical_sources": sources,
            "text_anchor": [fact.source_anchor for fact in facts],
            "local_window": "\n\n".join(windows),
            "question_scope_binding": (
                "scope_absent" if verdict == "scope_absent"
                else "not_applicable" if verdict == "not_applicable"
                else "in_scope" if verdict in {"supported", "contradicted"}
                else "unresolved"
            ),
            "factual_statement_true": (
                True if verdict == "supported"
                else False if verdict == "contradicted"
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
            "conflicts": [] if authoritative else ["insurance_clause_not_authoritative"],
        }

    def audit_question(self, question: Mapping[str, Any]) -> dict[str, Any]:
        options = question.get("options") or {}
        rows = [
            self.audit_option(
                question_text=str(question.get("question") or question.get("text") or ""),
                option_label=str(label),
                option_text=str(text),
                declared_doc_ids=question.get("doc_ids") or (),
            )
            for label, text in sorted(options.items())
        ]
        supported = "".join(row["option"] for row in rows if row["verdict"] == "supported")
        fully_trusted = bool(rows) and all(row["authoritative"] for row in rows)
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
