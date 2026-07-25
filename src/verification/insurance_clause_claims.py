"""Compile insurance clause evidence for the production verification sidecar.

Production mode uses only a product-document catalog and automatically
extracted clause facts.  The historical curated registry remains available only
through an explicit offline-evaluator flag.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from contracts import Question
from verification.derived_option_evidence import DerivedOptionEvidence, SourceFact
from verification.insurance_clause_compiler import GenericInsuranceClauseCompiler
from verification.insurance_clause_extractor import load_insurance_product_catalog
from verification.insurance_clause_ledger import (
    InsuranceClauseLedger,
    load_insurance_clause_registry,
)


@lru_cache(maxsize=8)
def _generic_compiler(
    full_text_root: str,
    product_catalog_path: str,
) -> GenericInsuranceClauseCompiler:
    return GenericInsuranceClauseCompiler(
        Path(full_text_root),
        catalog=load_insurance_product_catalog(Path(product_catalog_path)),
        scope_scan_run_id="production_generic_insurance_clause_verification.v1",
    )


@lru_cache(maxsize=4)
def _curated_offline_ledger(
    full_text_root: str,
    registry_path: str,
) -> InsuranceClauseLedger:
    return InsuranceClauseLedger(
        Path(full_text_root),
        registry=load_insurance_clause_registry(
            Path(registry_path),
            allow_curated_fixture_for_offline_evaluation=True,
        ),
        scope_scan_run_id="offline_curated_insurance_clause_evaluation.v1",
    )


def _auto_source_fact(row: Mapping[str, Any]) -> SourceFact:
    value = row.get("normalized_value")
    return SourceFact(
        doc_id=str(row.get("document_id") or ""),
        entity_scope=" / ".join(
            str(value)
            for value in (
                row.get("product_id"), row.get("product_type"),
            )
            if value
        ),
        period_scope="contract_term",
        metric=str(row.get("normalized_relation") or row.get("clause_category") or "insurance_clause"),
        value=value,
        unit="normalized_clause_relation",
        canonical_source=str(row.get("source_relpath") or ""),
        local_window=str(row.get("local_window") or ""),
        fact_state="production_auto_extracted_clause",
        metadata={
            "clause_category": row.get("clause_category"),
            "normalized_relation": row.get("normalized_relation"),
            "normalized_value": value,
            "conditions": row.get("conditions") or [],
            "exceptions": row.get("exceptions") or [],
            "source_sha256": row.get("source_sha256"),
            "page_or_line": row.get("page_or_line"),
            "extraction_rule_id": row.get("extraction_rule_id"),
            "confidence_state": row.get("confidence_state"),
            "production_auto_extracted": True,
        },
    )


def _curated_source_fact(row: Mapping[str, Any]) -> SourceFact:
    return SourceFact(
        doc_id=str(row.get("document_id") or ""),
        entity_scope=" / ".join(
            value for value in (
                str(row.get("product_name") or ""),
                str(row.get("product_type") or ""),
                str(row.get("insurer") or ""),
            ) if value
        ),
        period_scope="contract_term",
        metric=str(row.get("clause_category") or "insurance_clause"),
        value=str(row.get("object_text") or row.get("fact_state") or ""),
        unit="clause_state",
        canonical_source=str(row.get("source_relpath") or ""),
        local_window=str(row.get("local_window") or ""),
        fact_state=str(row.get("fact_state") or "curated_fixture"),
        metadata={
            "product_id": row.get("product_id"),
            "source_sha256": row.get("source_sha256"),
            "page_or_line": row.get("page_or_line"),
            "curated_evaluator_fixture": True,
            "production_input_allowed": False,
        },
    )


def _scope_facts(audit: Mapping[str, Any]) -> tuple[SourceFact, ...]:
    verdict = str(audit.get("verdict") or "")
    if audit.get("scope_absence_proof"):
        proof = audit.get("scope_absence_proof") if isinstance(
            audit.get("scope_absence_proof"), Mapping
        ) else {}
        relpaths = dict(proof.get("source_relpaths") or {})
        required = [str(value) for value in proof.get("required_doc_ids") or []]
        return tuple(
            SourceFact(
                doc_id=doc_id,
                entity_scope=f"declared insurance document {doc_id}",
                period_scope="contract_term",
                metric="declared_document_absence",
                value=False,
                unit="clause_presence",
                canonical_source=str(relpaths.get(doc_id) or f"declared_document:{doc_id}"),
                local_window=(
                    f"complete scope_absence_proof.v2 scan; required_doc_id={doc_id}; "
                    f"coherent_match_count={proof.get('coherent_match_count')}"
                ),
                fact_state="scope_absent",
                metadata={
                    "scope_absence_proof": proof,
                    "verification_only": True,
                    "evidence_tier": 2,
                },
            )
            for doc_id in required
        )
    if verdict == "not_applicable":
        declared = [str(value) for value in audit.get("declared_doc_ids") or []]
        product_docs = [str(value) for value in audit.get("product_document_ids") or []]
        return tuple(
            SourceFact(
                doc_id=doc_id,
                entity_scope=f"option product document {doc_id}",
                period_scope="question_scope",
                metric="product_document_membership",
                value=False,
                unit="scope_membership",
                canonical_source=f"declared_scope_excludes_document:{doc_id}",
                local_window=(
                    f"question_declared_doc_ids={declared}; "
                    f"option_product_document_ids={product_docs}"
                ),
                fact_state="not_applicable",
                metadata={
                    "declared_doc_ids": declared,
                    "product_document_ids": product_docs,
                    "verification_only": True,
                    "evidence_tier": 1,
                },
            )
            for doc_id in product_docs
        )
    return ()


def _derived_from_audit(
    question: Question,
    label: str,
    audit: Mapping[str, Any],
    *,
    curated: bool,
) -> DerivedOptionEvidence:
    facts = tuple(
        (_curated_source_fact(row) if curated else _auto_source_fact(row))
        for row in audit.get("facts") or []
    )
    if not facts:
        facts = _scope_facts(audit)
    verdict = str(audit.get("verdict") or "unresolved")
    if verdict == "supported":
        status = "supported"
        result: bool | None = True
    elif verdict in {"contradicted", "scope_absent", "not_applicable"}:
        status = "contradicted"
        result = False
    else:
        status = "unresolved"
        result = None
    authoritative = bool(audit.get("authoritative"))
    sources = tuple(str(value) for value in audit.get("canonical_sources") or [])
    if not sources:
        sources = tuple(
            dict.fromkeys(fact.canonical_source for fact in facts if fact.canonical_source)
        )
    conflicts = tuple(str(value) for value in audit.get("conflicts") or [])
    if not authoritative:
        conflicts = tuple(sorted(set(conflicts + ("insurance_clause_not_authoritative",))))
    evidence_tier = audit.get("evidence_tier")
    return DerivedOptionEvidence(
        qid=question.qid,
        option_label=str(label),
        claim_type=str(audit.get("claim_type") or "insurance_clause"),
        source_facts=facts,
        formula_or_aggregation=str(audit.get("reason") or "typed insurance clause verdict"),
        variables={
            "insurance_verdict": verdict,
            "compiler_rule_id": audit.get("compiler_rule_id"),
            "question_scope_binding": audit.get("question_scope_binding"),
            "scope_absence_proof_valid": audit.get("scope_absence_proof_valid"),
            "scope_absence_proof": audit.get("scope_absence_proof"),
            "out_of_scope_doc_ids": audit.get("out_of_scope_doc_ids") or [],
            "evidence_tier": evidence_tier,
            "winning_evidence_source": audit.get("winning_evidence_source"),
            "reconciliation_rule_id": audit.get("reconciliation_rule_id"),
            "production_auto_extracted": audit.get("production_auto_extracted") is True,
            "curated_evaluator_fixture": curated,
        },
        units={"verdict": "clause_state"},
        entity_scope=tuple(
            dict.fromkeys(fact.entity_scope for fact in facts if fact.entity_scope)
        ) or tuple(str(value) for value in audit.get("product_document_ids") or []),
        period_scope=("contract_term",),
        document_scope=tuple(str(value) for value in audit.get("product_document_ids") or []),
        result=result,
        status=status,
        canonical_sources=sources,
        conflicts=conflicts,
        trusted_for_option_gate=authoritative and status in {"supported", "contradicted"},
        diagnostics={
            "insurance_clause_audit": dict(audit),
            "verification_only": True,
            "evidence_tier": evidence_tier,
            "winning_evidence_source": audit.get("winning_evidence_source"),
            "superseded_evidence_sources": audit.get("superseded_evidence_sources") or [],
            "conflict_reason": audit.get("conflict_reason"),
            "reconciliation_rule_id": audit.get("reconciliation_rule_id"),
            "production_auto_extracted": audit.get("production_auto_extracted") is True,
            "curated_evaluator_fixture": curated,
        },
    )


def build_insurance_clause_option_evidence(
    question: Question,
    *,
    full_text_root: str | Path | None,
    product_catalog_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    allow_curated_fixture_for_offline_evaluation: bool = False,
) -> tuple[DerivedOptionEvidence, ...]:
    """Return option-local evidence from production or explicit offline mode.

    A registry path is never accepted silently.  It is available only when the
    caller explicitly opts into fixed-dataset offline evaluation.
    """
    if question.domain != "insurance" or not full_text_root:
        return ()
    curated = False
    if product_catalog_path:
        compiler = _generic_compiler(
            str(Path(full_text_root)), str(Path(product_catalog_path))
        )
        audit_option = compiler.audit_option
    elif registry_path:
        if not allow_curated_fixture_for_offline_evaluation:
            # The loader performs the metadata-level hard rejection.
            load_insurance_clause_registry(Path(registry_path))
            return ()
        curated = True
        ledger = _curated_offline_ledger(
            str(Path(full_text_root)), str(Path(registry_path))
        )
        audit_option = ledger.audit_option
    else:
        return ()

    results = []
    for label, option_text in sorted(question.options.items()):
        audit = audit_option(
            question_text=question.text,
            option_label=str(label),
            option_text=str(option_text),
            declared_doc_ids=question.doc_ids,
        )
        results.append(_derived_from_audit(question, str(label), audit, curated=curated))
    return tuple(results)
