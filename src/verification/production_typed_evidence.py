"""Build production-authoritative option evidence from typed local claims.

This module is deliberately conservative.  The legacy option self-check remains
available for diagnostics, but typed evidence is authoritative only when:

* the solver answer matches its own explicit supported judgment set;
* every option is independently certified in one local evidence window;
* an unresolved model judgment may be closed only by one-sided authoritative
  typed evidence (support without contradiction, or contradiction without support);
* explicit model/evidence disagreement remains untrusted;
* no opposite typed certification is found in the allowed document lineage;
* solver, typed-supported, and correction answers satisfy one question contract.

Readable evidence alone is never sufficient, and this module never changes an
answer.  It only produces an auditable evidence contract for production integrity.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from answer_contract import (
    contract_from_mapping,
    contract_from_question,
    contract_to_dict,
    validate_answer_against_contract,
)
from contracts import (
    EvidenceBundle,
    EvidenceCandidate,
    QuestionAnswerContract,
    SolverResult,
    get_verification_candidates,
)
from evidence.structured_tables import (
    structured_table_certification_complete,
    structured_table_row_eligible_for_option,
)
from verification.compound_claims import (
    entity_scope_guard,
    extract_metric,
    extract_periods,
    raw_table_certification_guard,
    route_option_claim,
)
from verification.contract_truth_false import build_truth_false_production_contract
from verification.cross_doc_claim_binding import (
    certify_cross_doc_option,
    detect_cross_doc_claim_spec,
    is_cross_doc_option,
)
from verification.cross_domain_residual_evidence import build_cross_domain_residual_option_evidence
from verification.derived_claim_router import build_derived_option_evidence
from verification.derived_option_evidence import (
    DerivedOptionEvidence,
    SourceFact,
    merge_derived_option_evidence,
)
from verification.insurance_clause_claims import build_insurance_clause_option_evidence
from verification.insurance_calculation_compiler import build_insurance_calculation_option_evidence
from verification.regulatory_option_evidence import build_regulatory_option_evidence
from verification.claim_local_binding import (
    OptionBindingScope,
    certify_option_in_binding_scope,
    select_option_binding_scope,
)
from verification.typed_claim_binding import certify_typed_option_claim


_SUPPORTED_JUDGMENTS = {
    "支持", "supported", "support", "true", "正确", "成立", "yes",
}
_CONTRADICTED_JUDGMENTS = {
    "反驳", "contradicted", "contradict", "refuted", "refute", "false", "错误", "不成立",
}


def _canonical_answer(value: Any) -> str:
    return "".join(sorted({char for char in str(value or "").upper() if "A" <= char <= "D"}))


def _normalise_judgment(value: Any) -> str:
    text = str(value or "").strip().lower()
    compact = "".join(text.split())
    if compact in _SUPPORTED_JUDGMENTS:
        return "supported"
    if compact in _CONTRADICTED_JUDGMENTS:
        return "contradicted"
    return "unresolved"


def _canonical_source(source: Any) -> str:
    normalised = str(source or "").strip().replace("\\", "/")
    lowered = normalised.lower()
    for marker in ("data/processed_mineru_retrieval/", "processed_mineru_retrieval/"):
        index = lowered.find(marker)
        if index >= 0:
            suffix = normalised[index + (0 if marker.startswith("data/") else len("processed_mineru_retrieval/")):]
            if marker.startswith("data/"):
                return suffix
            return "data/processed_mineru_retrieval/" + suffix.lstrip("/")
    return normalised


def _candidate_payload(
    bundle: EvidenceBundle,
    option_text: str,
    candidate: EvidenceCandidate,
) -> dict[str, Any]:
    source = _canonical_source(candidate.source)
    context = "\n\n".join(
        part.strip()
        for part in (candidate.before_text, candidate.text, candidate.after_text)
        if str(part or "").strip()
    )
    return {
        "option_text": str(option_text or ""),
        "question_doc_ids": [str(value) for value in bundle.question.doc_ids],
        "resolved_evidence_refs": [source] if source else [],
        "evidence_refs": [source] if source else [],
        "source_resolution": [
            {
                "canonical_ref": source,
                "resolved_path": str(candidate.source or ""),
                "read_status": "read" if context else "unresolved",
                "bounded_context": context,
                "page_or_lineage": source,
            }
        ],
    }


def _declared_full_document_rows(
    bundle: EvidenceBundle, doc_ids: Sequence[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Load complete declared documents for absence/role-sensitive cross-doc checks."""
    root = str((bundle.metadata or {}).get("structured_table_root") or "").strip()
    if not root:
        return {}
    from pathlib import Path

    base = Path(root)
    rows: dict[str, dict[str, Any]] = {}
    for raw_doc_id in (doc_ids or bundle.question.doc_ids):
        doc_id = str(raw_doc_id)
        candidates = (
            base / bundle.question.domain / doc_id / "auto" / f"{doc_id}.md",
            base / bundle.question.domain / doc_id / f"{doc_id}.md",
            base / bundle.question.domain / doc_id / "auto" / "content.md",
        )
        path = next((item for item in candidates if item.is_file()), None)
        if path is None:
            continue
        rows[doc_id] = {
            "canonical_source": path.as_posix(),
            "local_window": path.read_text(encoding="utf-8-sig", errors="replace"),
            "score": -1000.0,
            "complete_document_scan": True,
        }
    return rows


def _candidate_rank(certification: Mapping[str, Any], candidate: EvidenceCandidate) -> tuple[int, int, float, int]:
    matched = len(certification.get("matched_atoms") or [])
    missing = len(certification.get("missing_atoms") or [])
    conflicts = len(certification.get("conflicting_atoms") or [])
    return (matched, -missing, float(candidate.score or 0.0), -conflicts)


def _direct_scope_contract(
    *,
    option_text: str,
    local_window: str,
    source: str,
    question_doc_ids: Sequence[str],
    missing_atoms: Sequence[Any],
    conflicting_atoms: Sequence[Any],
    explicit_claim_contradiction: bool = False,
) -> dict[str, Any]:
    """Return explicit completeness fields for a direct local-window verdict."""
    route = route_option_claim(option_text, question_doc_ids)
    entity_ok, entity_reasons = entity_scope_guard(option_text, local_window)
    if explicit_claim_contradiction:
        entity_ok = True
        entity_reasons = tuple(
            reason for reason in entity_reasons
            if not str(reason).startswith(("missing_entity:", "unexpected_entity:", "entity_scope"))
        )
    period_haystack = f"{local_window} {source}"
    found_periods = set(extract_periods(period_haystack))
    period_ok = not route.periods or all(period in found_periods for period in route.periods)
    local_metric = extract_metric(local_window)
    metric_ok = not route.metric or local_metric == route.metric
    comparator_ok = route.claim_type == "direct_fact" or not route.comparator
    compound_blocked = route.compound
    required_complete = bool(
        source
        and local_window
        and not missing_atoms
        and not conflicting_atoms
        and not compound_blocked
        and entity_ok
        and period_ok
        and metric_ok
        and comparator_ok
    )
    return {
        "claim_type": route.claim_type,
        "claim_route_metadata": route.to_dict(),
        "trusted_for_option_gate": required_complete,
        "required_atoms_complete": required_complete,
        "entity_scope_complete": entity_ok,
        "entity_scope_reasons": list(entity_reasons),
        "period_scope_complete": period_ok,
        "metric_scope_complete": metric_ok,
        "comparator_scope_complete": comparator_ok,
        "compound_claim_requires_derivation": compound_blocked,
    }


def _numeric_bound_derived_from_certification(
    *,
    bundle: EvidenceBundle,
    option_label: str,
    option_text: str,
    certification: Mapping[str, Any],
    candidate: EvidenceCandidate,
    resolved_status: str,
) -> DerivedOptionEvidence | None:
    """Convert a complete source-local numeric bound into derived evidence."""
    route = route_option_claim(option_text, bundle.question.doc_ids)
    if route.claim_type != "numeric_comparison" or resolved_status != "supported":
        return None
    if str(certification.get("claim_certification_status") or "") != "supported":
        return None
    if certification.get("missing_atoms") or certification.get("conflicting_atoms"):
        return None
    local_window = str(certification.get("local_window") or "").strip()
    source = str(certification.get("canonical_source") or _canonical_source(candidate.source)).strip()
    if not local_window or not source or not route.comparator:
        return None

    option_numbers = [
        token.replace(",", "")
        for token in re.findall(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?!\d)", option_text)
        if not re.fullmatch(r"(?:19|20)\d{2}", token.replace(",", ""))
    ]
    if not option_numbers:
        return None
    threshold_text = option_numbers[0]
    threshold = float(threshold_text)
    source_numbers = {
        token.replace(",", "")
        for token in re.findall(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?!\d)", local_window)
    }
    local_route = route_option_claim(local_window, (candidate.doc_id,))
    if threshold_text not in source_numbers or local_route.comparator != route.comparator:
        return None
    unit_match = re.search(
        rf"{re.escape(option_numbers[0])}\s*(%|％|条|亿元|万元|元|人|家|项|个|倍)?",
        option_text.replace(",", ""),
    )
    unit = str(unit_match.group(1) if unit_match and unit_match.group(1) else "count")
    entity_scope = "/".join(route.entities) or str(candidate.doc_id)
    period_scope = route.periods[0] if route.periods else "document_scope"
    metric = route.metric or "reported_numeric_bound"
    comparator_state = {
        ">": "lower_bound_exclusive",
        "<": "upper_bound_exclusive",
        "=": "exact_bound",
    }.get(route.comparator, "reported_bound")
    fact = SourceFact(
        doc_id=str(candidate.doc_id),
        entity_scope=entity_scope,
        period_scope=period_scope,
        metric=metric,
        value=threshold_text,
        unit=unit,
        canonical_source=source,
        local_window=local_window,
        fact_state=comparator_state,
        metadata={
            "source_comparator": local_route.comparator,
            "claim_comparator": route.comparator,
            "source_bound": threshold_text,
            "claim_bound": threshold_text,
        },
    )
    return DerivedOptionEvidence(
        qid=bundle.question.qid,
        option_label=option_label,
        claim_type="numeric_comparison",
        source_facts=(fact,),
        formula_or_aggregation="source numeric bound entails the option numeric bound",
        variables={
            "source_bound": threshold,
            "claim_bound": threshold,
            "source_comparator": local_route.comparator,
            "claim_comparator": route.comparator,
        },
        units={"source_bound": unit, "claim_bound": unit},
        entity_scope=(entity_scope,),
        period_scope=(period_scope,),
        document_scope=(str(candidate.doc_id),),
        result=True,
        status="supported",
        conflicts=(),
        canonical_sources=(source,),
        trusted_for_option_gate=True,
        diagnostics={
            "route": route.to_dict(),
            "source_route": local_route.to_dict(),
            "candidate_retriever": str(candidate.retriever),
        },
    )


def _direct_certified_source_fact(
    *,
    bundle: EvidenceBundle,
    option_text: str,
    certification: Mapping[str, Any],
    candidate: EvidenceCandidate,
    resolved_status: str,
) -> SourceFact | None:
    """Materialize one production-certified local proposition as SourceFact.

    The fact is created only *after* certify_typed_option_claim selected a
    one-sided authoritative candidate.  Corrective retrieval therefore cannot
    set a verdict directly; it must first survive the normal production
    certifier and scope gate.
    """
    if resolved_status not in {"supported", "contradicted"}:
        return None
    certification_status = str(certification.get("claim_certification_status") or "")
    if certification_status != resolved_status:
        return None
    local_window = str(certification.get("local_window") or "").strip()
    source = str(certification.get("canonical_source") or _canonical_source(candidate.source)).strip()
    basis = str(certification.get("certification_basis") or "").strip()
    if not local_window or not source or not basis:
        return None
    route = route_option_claim(option_text, bundle.question.doc_ids)
    entity_scope = "/".join(route.entities) or str(candidate.doc_id)
    period_scope = route.periods[0] if route.periods else "document_scope"
    metric = route.metric or route.claim_type or "typed_local_proposition"
    retriever = str(candidate.retriever or "")
    return SourceFact(
        doc_id=str(candidate.doc_id),
        entity_scope=entity_scope,
        period_scope=period_scope,
        metric=metric,
        value=str(option_text),
        unit="proposition",
        canonical_source=source,
        local_window=local_window,
        fact_state=f"{resolved_status}_proposition",
        metadata={
            "fact_kind": "typed_local_proposition",
            "certification_basis": basis,
            "certification_status": certification_status,
            "candidate_retriever": retriever,
            "corrective_reentry": "corrective" in retriever.lower(),
            "claim_atoms": certification.get("claim_atoms") or {},
            "matched_atoms": list(certification.get("matched_atoms") or []),
        },
    )


def _cross_doc_source_facts(
    *,
    bundle: EvidenceBundle,
    cross_doc_claim: Mapping[str, Any],
    field_type: str,
    relation_type: str,
) -> list[dict[str, Any]]:
    """Materialize production cross-document subclaims as SourceFact rows."""
    rows: list[dict[str, Any]] = []
    aggregate_status = str(cross_doc_claim.get("aggregate_status") or "")
    for subclaim in cross_doc_claim.get("subclaims") or []:
        status = str(subclaim.get("status") or "")
        source = str(subclaim.get("canonical_source") or "").strip()
        local_window = str(subclaim.get("local_window") or "").strip()
        doc_id = str(subclaim.get("doc_id") or "").strip()
        if status not in {"supported", "contradicted"} or not source or not local_window or not doc_id:
            continue
        corrective_reentry = any(
            str(candidate.doc_id) == doc_id
            and _canonical_source(candidate.source) == source
            and "corrective" in str(candidate.retriever or "").lower()
            for candidate in get_verification_candidates(bundle)
        )
        rows.append(SourceFact(
            doc_id=doc_id,
            entity_scope=doc_id,
            period_scope="document_scope",
            metric=field_type or relation_type or "cross_doc_field",
            value=subclaim.get("value"),
            unit="field_value",
            canonical_source=source,
            local_window=local_window,
            fact_state=f"{status}_subclaim",
            metadata={
                "fact_kind": "cross_doc_subclaim",
                "relation_type": relation_type,
                "field_type": field_type,
                "aggregate_status": aggregate_status,
                "certification_basis": str(subclaim.get("certification_basis") or ""),
                "corrective_reentry": corrective_reentry,
            },
        ).to_dict())
    return rows


def _normalised_solver_judgments(result: SolverResult, option_labels: Sequence[str]) -> dict[str, str]:
    metadata = dict(result.metadata or {})
    raw = metadata.get("judgments")
    raw_map = raw if isinstance(raw, Mapping) else {}
    return {
        label: _normalise_judgment(raw_map.get(label) or raw_map.get(label.lower()))
        for label in option_labels
    }


def _calibrated_option_verdict(
    *,
    binding: Mapping[str, Any],
    model_judgment: str,
) -> tuple[dict[str, Any], str]:
    """Convert the richer binding taxonomy to the stable option schema."""
    binding_status = str(binding.get("status") or "unresolved_adapter_unavailable")
    authoritative = binding_status in {"supported", "contradicted"}
    resolved = binding_status if authoritative else "unresolved"
    refs = [str(value) for value in binding.get("evidence_refs") or [] if str(value)]
    reason = str(binding.get("certification_basis") or binding_status)
    row = {
        "status": resolved,
        "binding_status": binding_status,
        "source_local_verdict": binding_status,
        "claim_route": (
            "exact_clause"
            if resolved == "supported"
            else "contradiction"
            if resolved == "contradicted"
            else "missing"
        ),
        "typed_claim_route": str(binding.get("binding_adapter") or "source_local_binding"),
        "trusted_for_option_gate": authoritative,
        "required_atoms_complete": authoritative,
        "term_equivalence": "confirmed" if resolved == "supported" else "not_required",
        "term_equivalence_confirmed": resolved == "supported",
        "term_equivalence_required": resolved == "supported",
        "factual_statement_true": True if resolved == "supported" else False if resolved == "contradicted" else None,
        "question_scope_binding": "in_scope" if authoritative else "unresolved",
        "reason": reason,
        "unresolved_reason": "" if authoritative else reason,
        "evidence_refs": refs,
        "resolved_evidence_refs": refs,
        "canonical_source": str(binding.get("canonical_source") or (refs[0] if refs else "")),
        "canonical_sources": refs,
        "local_window": str(binding.get("local_window") or ""),
        "certification_basis": reason,
        "matched_atoms": list(binding.get("matched_atoms") or []),
        "missing_atoms": list(binding.get("missing_atoms") or []),
        "contradiction_atoms": list(binding.get("contradiction_atoms") or []),
        "conflicting_atoms": list(binding.get("conflicting_atoms") or []),
        "corrective_retrieval_gaps": dict(binding.get("corrective_retrieval_gaps") or {}),
        "confidence": float(binding.get("confidence") or 0.0),
        "trust_failures": list(binding.get("trust_failures") or []),
        "model_judgment": model_judgment,
        "resolved_judgment": resolved,
        "model_uncertainty_closed_by_typed_evidence": bool(
            model_judgment == "unresolved" and authoritative
        ),
        "model_disagreement_overridden_by_source_local_binding": bool(
            model_judgment in {"supported", "contradicted"}
            and authoritative
            and model_judgment != resolved
        ),
        "legacy_self_check_authority": "diagnostic_only",
        "production_answer_basis": "source_local_typed_binding" if authoritative else binding_status,
    }
    return row, resolved


def _insurance_clause_derived(bundle: EvidenceBundle) -> tuple[DerivedOptionEvidence, ...]:
    metadata = dict(bundle.metadata or {})
    if metadata.get("insurance_clause_verification_enabled") is not True:
        return ()
    return build_insurance_clause_option_evidence(
        bundle.question,
        full_text_root=str(metadata.get("insurance_clause_full_text_root") or ""),
        product_catalog_path=str(
            metadata.get("insurance_clause_product_catalog_path") or ""
        ),
        registry_path=str(metadata.get("insurance_clause_registry_path") or ""),
        allow_curated_fixture_for_offline_evaluation=bool(
            metadata.get("allow_curated_insurance_fixture_for_offline_evaluation")
        ),
    )

def _insurance_calculation_derived(bundle: EvidenceBundle) -> tuple[DerivedOptionEvidence, ...]:
    metadata = dict(bundle.metadata or {})
    if metadata.get("insurance_calculation_verification_enabled") is not True:
        return ()
    return build_insurance_calculation_option_evidence(
        bundle.question,
        full_text_root=str(metadata.get("insurance_calculation_full_text_root") or ""),
        product_catalog_path=str(
            metadata.get("insurance_calculation_product_catalog_path") or ""
        ),
    )


def _semantic_compact(value: Any) -> str:
    return "".join(str(value or "").replace("％", "%").split())


def _production_override_capability(
    bundle: EvidenceBundle,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Return one narrowly scoped, QID-independent answer-override capability."""
    question = bundle.question
    contract = contract_from_question(question)
    docs = {str(value) for value in question.doc_ids}

    if question.domain == "financial_reports":
        source_payload = dict(payload or {})
        rows = dict(source_payload.get("option_verdicts") or {})
        labels = {str(label).upper() for label in question.options}

        def semantic_diagnostics(row: Mapping[str, Any]) -> Mapping[str, Any]:
            derived = row.get("derived_option_evidence") or {}
            return derived.get("diagnostics") or {} if isinstance(derived, Mapping) else {}

        def diagnostics_ready(diagnostics: Mapping[str, Any]) -> bool:
            sufficiency = diagnostics.get("final_sufficiency") or {}
            completion = diagnostics.get("completion_result") or {}
            claim_ast = diagnostics.get("claim_ast") or {}
            post_sufficiency = (
                completion.get("post_completion_sufficiency") or {}
                if isinstance(completion, Mapping) else {}
            )
            return bool(
                diagnostics.get("claim_ast_schema_version")
                and isinstance(claim_ast, Mapping)
                and claim_ast.get("complete") is True
                and not claim_ast.get("unsupported_semantics")
                and isinstance(completion, Mapping)
                and completion.get("schema_version")
                and completion.get("provider_calls") == 0
                and completion.get("whole_corpus_scan") is False
                and completion.get("declared_doc_boundary_pass") is True
                and isinstance(sufficiency, Mapping)
                and sufficiency.get("safe_to_override") is True
                and isinstance(post_sufficiency, Mapping)
                and post_sufficiency.get("safe_to_override") is True
                and diagnostics.get("comparison_formula_derived_from_ast") is True
                and diagnostics.get("no_default_comparator_fallback") is True
            )

        option_rows_ready = bool(
            labels
            and set(rows) == labels
            and source_payload.get("trusted_for_production") is True
            and all(
                str((rows.get(label) or {}).get("status") or "")
                in {"supported", "contradicted"}
                and (rows.get(label) or {}).get("trusted_for_option_gate") is True
                and str((rows.get(label) or {}).get("typed_claim_route") or "")
                == "derived_option_evidence"
                and diagnostics_ready(semantic_diagnostics(rows.get(label) or {}))
                for label in labels
            )
        )

        proposition = source_payload.get("truth_false_proposition") or {}
        clause_results = (
            proposition.get("clause_results") or []
            if isinstance(proposition, Mapping)
            else []
        )
        truth_false_ready = bool(
            contract.answer_format == "tf"
            and source_payload.get("trusted_for_production") is True
            and clause_results
            and all(
                isinstance(clause, Mapping)
                and str(clause.get("status") or "") in {"supported", "contradicted"}
                and clause.get("trusted_for_option_gate") is True
                and diagnostics_ready(clause.get("diagnostics") or {})
                for clause in clause_results
            )
        )
        if option_rows_ready or truth_false_ready:
            return "financial_reports:corpus_lineage_corrective_retrieval_v2"

    if question.domain == "financial_contracts" and contract.answer_format == "multi":
        specs = [
            detect_cross_doc_claim_spec(str(text), question.doc_ids)
            for text in question.options.values()
        ]
        field_types = {str(item.get("field_type") or "") for item in specs}
        relations = {str(item.get("relation_type") or "") for item in specs}
        if (
            len(docs) == 2
            and {
                "issue_scale_cap",
                "registration_amount_wording",
                "net_profit_three_year_series",
                "debt_asset_ratio",
            } <= field_types
            and "document_scoped_field_range_all" in relations
        ):
            return "financial_contracts:limit_profit_series_and_ratio_range"
    return ""


def _attach_financial_question_memory(
    bundle: EvidenceBundle,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    output = dict(payload)
    if bundle.question.domain != "financial_reports":
        return output
    from memory.financial_question_memory import build_financial_question_memory

    claim_specs: dict[str, Mapping[str, Any]] = {}
    evidence_by_option: dict[str, Mapping[str, Any]] = {}
    sufficiency_by_option: dict[str, Mapping[str, Any]] = {}
    completion_by_option: dict[str, Mapping[str, Any]] = {}
    reading_trace: list[Mapping[str, Any]] = []
    aggregate_final_sufficiency: dict[str, Any] = {}
    for label, row in dict(output.get("option_verdicts") or {}).items():
        derived = row.get("derived_option_evidence") or {}
        if not isinstance(derived, Mapping):
            continue
        diagnostics = derived.get("diagnostics") or {}
        if not isinstance(diagnostics, Mapping):
            continue
        label = str(label)
        claim_specs[label] = diagnostics.get("claim_ast") or {}
        final_evidence = diagnostics.get("final_evidence") or derived
        final_sufficiency = (
            diagnostics.get("final_sufficiency")
            or diagnostics.get("financial_evidence_sufficiency")
            or {}
        )
        completion = diagnostics.get("completion_result") or {}
        evidence_by_option[label] = final_evidence
        sufficiency_by_option[label] = final_sufficiency
        completion_by_option[label] = completion
        aggregate_final_sufficiency[label] = final_sufficiency
        if isinstance(completion, Mapping):
            graded = [
                hit for hit in completion.get("graded_hits") or []
                if isinstance(hit, Mapping)
            ]
            reading_trace.extend(graded)
            if not graded:
                reading_trace.extend(
                    hit for hit in completion.get("raw_hits") or []
                    if isinstance(hit, Mapping)
                )
    memory = build_financial_question_memory(
        bundle.question,
        claim_specs=claim_specs,
        evidence_by_option=evidence_by_option,
        sufficiency_by_option=sufficiency_by_option,
        targeted_audits=completion_by_option,
        reading_trace=reading_trace,
        answer_contract=dict(output.get("answer_contract") or {}),
        final_answer=str(output.get("typed_supported_answer") or output.get("solver_answer") or ""),
        final_sufficiency=aggregate_final_sufficiency,
        memory_token_budget=int(dict(bundle.metadata or {}).get("financial_memory_token_budget") or 1600),
        compression_trigger_ratio=float(dict(bundle.metadata or {}).get("financial_memory_compression_trigger_ratio") or 0.70),
    )
    output["financial_question_memory"] = memory.to_dict()
    return output


def _apply_override_capability(
    bundle: EvidenceBundle, payload: dict[str, Any]
) -> dict[str, Any]:
    payload = _attach_financial_question_memory(bundle, payload)
    capability = _production_override_capability(bundle, payload)
    payload = dict(payload)
    payload["production_override_capability"] = capability or None
    payload["production_answer_override_allowed"] = bool(
        capability
        and payload.get("trusted_for_production") is True
        and (payload.get("correction_answer_contract_validation") or {}).get("valid") is True
        and payload.get("typed_supported_answer")
    )
    return payload


def build_production_typed_option_evidence(
    bundle: EvidenceBundle,
    result: SolverResult,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable typed option-evidence contract.

    The function is fail-closed: any exception, incomplete binding, or answer
    contract violation produces an untrusted contract.
    """
    contract = contract_from_mapping(answer_contract) or contract_from_question(bundle.question)
    if (
        bundle.question.domain == "regulatory"
        and dict(bundle.metadata or {}).get("regulatory_option_verification_enabled") is True
    ):
        return build_regulatory_option_evidence(
            bundle, result, answer_contract=contract
        )
    residual_payload = build_cross_domain_residual_option_evidence(
        bundle,
        result,
        answer_contract=contract,
    )
    if residual_payload is not None and bundle.question.domain != "financial_reports":
        return residual_payload
    option_labels = sorted(str(label).upper() for label in bundle.question.options)
    metadata = dict(result.metadata or {})
    judgments = _normalised_solver_judgments(result, option_labels)
    supported_from_judgments = _canonical_answer(
        "".join(label for label, status in judgments.items() if status == "supported")
    )
    solver_answer = _canonical_answer(result.answer)
    solver_contract_validation = validate_answer_against_contract(solver_answer, contract)
    binding_scope, binding_candidates = select_option_binding_scope(bundle, result)
    used_docs = set(binding_scope.solver_used_doc_ids)
    allowed_candidates = list(binding_candidates)
    if contract.answer_format == "tf":
        structured_root = str(bundle.metadata.get("structured_table_root") or "").strip()
        if structured_root and bundle.question.domain == "financial_reports":
            from verification.financial_report_claims import (
                build_financial_report_truth_false_contract,
            )

            return _apply_override_capability(
                bundle, build_financial_report_truth_false_contract(bundle, result)
            )
        payload = build_truth_false_production_contract(
            bundle=bundle,
            result=result,
            contract=contract,
            candidates=allowed_candidates,
            solver_answer=solver_answer,
            solver_contract_validation=solver_contract_validation,
            used_docs=used_docs,
            judgments=judgments,
        )
        structured_root = str(bundle.metadata.get("structured_table_root") or "").strip()
        routed_derived = (
            build_derived_option_evidence(bundle.question, structured_root)
            if structured_root and bundle.question.domain == "financial_reports"
            else ()
        )
        derived = (
            tuple(routed_derived)
            + _insurance_clause_derived(bundle)
            + _insurance_calculation_derived(bundle)
        )
        payload["production_derived_option_evidence"] = [item.to_dict() for item in derived]
        payload["production_derived_option_count"] = len(derived)
        if derived:
            payload = merge_derived_option_evidence(payload, derived)
        return _apply_override_capability(bundle, payload)

    trust_failures: list[str] = []
    if not solver_contract_validation.valid:
        trust_failures.append(f"solver_answer_contract_violation:{solver_contract_validation.reason}")
    if metadata.get("structured_parse_failed") is True:
        trust_failures.append("structured_parse_failed")
    if set(judgments) != set(option_labels):
        trust_failures.append("option_judgment_label_mismatch")
    if solver_answer != supported_from_judgments:
        trust_failures.append("solver_answer_does_not_match_structured_judgments")
    missing_declared = metadata.get("missing_option_judgments")
    if isinstance(missing_declared, Sequence) and not isinstance(missing_declared, (str, bytes, bytearray)) and list(missing_declared):
        trust_failures.append("solver_declared_missing_option_judgments")
    if not used_docs:
        trust_failures.append("used_doc_lineage_missing")
    if not allowed_candidates:
        trust_failures.append("no_candidates_in_used_doc_lineage")

    option_verdicts: dict[str, dict[str, Any]] = {}
    option_diagnostics: dict[str, dict[str, Any]] = {}
    derived_from_certifications: list[DerivedOptionEvidence] = []
    resolved_judgments: dict[str, str] = dict(judgments)

    for label in option_labels:
        desired = judgments[label]
        option_text = str(bundle.question.options.get(label) or "")
        if is_cross_doc_option(option_text, bundle.question.doc_ids):
            evidence_by_doc: dict[str, list[dict[str, Any]]] = {}
            for candidate in allowed_candidates:
                context = "\n\n".join(
                    part.strip()
                    for part in (candidate.before_text, candidate.text, candidate.after_text)
                    if str(part or "").strip()
                )
                if (
                    candidate.retriever == "mineru_structured_table"
                    and not structured_table_row_eligible_for_option(
                        option_text, context, bundle.question.doc_ids
                    )
                ):
                    continue
                evidence_by_doc.setdefault(str(candidate.doc_id), []).append({
                    "canonical_source": _canonical_source(candidate.source),
                    "local_window": context,
                    "score": float(candidate.score or 0.0),
                    "complete_document_scan": False,
                })
            cross_doc_spec = detect_cross_doc_claim_spec(
                option_text, bundle.question.doc_ids
            )
            needs_complete_scan = (
                str(cross_doc_spec.get("relation_type") or "")
                == "document_scoped_field_range_all"
                or str(cross_doc_spec.get("field_type") or "")
                in {"net_profit_three_year_series", "debt_asset_ratio"}
                and (
                    "合并口径" in option_text
                    or str(cross_doc_spec.get("relation_type") or "") == "all_presence"
                )
                or str(cross_doc_spec.get("field_type") or "")
                in {"registration_amount_wording", "issuer_name"}
            )
            if needs_complete_scan:
                for doc_id, full_row in _declared_full_document_rows(
                    bundle, cross_doc_spec.get("required_doc_ids") or bundle.question.doc_ids
                ).items():
                    evidence_by_doc.setdefault(doc_id, []).append(full_row)
            cross_doc_claim = certify_cross_doc_option(
                option_label=label,
                option_text=option_text,
                required_doc_ids=bundle.question.doc_ids,
                evidence_by_doc=evidence_by_doc,
            )
            aggregate_status = str(cross_doc_claim.get("aggregate_status") or "ambiguous")
            trusted_cross_doc = cross_doc_claim.get("trusted_for_option_gate") is True
            resolved_status = desired
            if trusted_cross_doc and aggregate_status in {"supported", "contradicted"}:
                resolved_status = aggregate_status
            option_diagnostics[label] = {
                "model_judgment": desired,
                "resolved_judgment": resolved_status,
                "candidate_count": len(allowed_candidates),
                "cross_doc_claim": cross_doc_claim,
                "lineage_conflict": False,
                "claim_contradiction_docs": list(cross_doc_claim.get("conflicting_docs") or []),
            }
            explicit_disagreement = (
                desired in {"supported", "contradicted"}
                and aggregate_status in {"supported", "contradicted"}
                and aggregate_status != desired
            )
            option_diagnostics[label]["model_disagreement_overridden_by_authoritative_cross_doc"] = bool(
                explicit_disagreement and trusted_cross_doc
            )
            if not trusted_cross_doc or aggregate_status != resolved_status:
                reason = "cross-document subclaims do not fully certify the option claim"
                if desired == "unresolved":
                    reason = "model uncertainty not closed by trusted cross-document aggregation"
                trust_failures.append(f"option_{label}:{reason}")
                option_verdicts[label] = {
                    "status": "unresolved",
                    "claim_route": "cross_doc_claim_unresolved",
                    "reason": reason,
                    "evidence_refs": list(cross_doc_claim.get("evidence_refs") or []),
                    "model_judgment": desired,
                    "resolved_judgment": "unresolved",
                    "cross_doc_claim": cross_doc_claim,
                }
                resolved_judgments[label] = "unresolved"
                continue
            resolved_judgments[label] = resolved_status
            refs = [str(value) for value in cross_doc_claim.get("evidence_refs") or []]
            subclaims = list(cross_doc_claim.get("subclaims") or [])
            local_window = "\n\n".join(
                f"[{row.get('doc_id')}] {row.get('local_window')}"
                for row in subclaims
                if row.get("local_window")
            )
            route = route_option_claim(option_text, bundle.question.doc_ids)
            scope_contract = {
                "claim_type": route.claim_type,
                "claim_route_metadata": route.to_dict(),
                "trusted_for_option_gate": trusted_cross_doc,
                "required_atoms_complete": trusted_cross_doc,
                "entity_scope_complete": trusted_cross_doc,
                "entity_scope_reasons": [],
                "period_scope_complete": trusted_cross_doc,
                "metric_scope_complete": trusted_cross_doc,
                "comparator_scope_complete": trusted_cross_doc,
                "compound_claim_requires_derivation": False,
                "cross_doc_aggregation_complete": trusted_cross_doc,
            }
            option_verdicts[label] = {
                "status": resolved_status,
                "claim_route": "exact_clause" if resolved_status == "supported" else "contradiction",
                "typed_claim_route": "cross_doc_subclaim_aggregation",
                **scope_contract,
                "term_equivalence": "confirmed" if resolved_status == "supported" else "not_required",
                "term_equivalence_confirmed": resolved_status == "supported",
                "term_equivalence_required": resolved_status == "supported",
                "factual_statement_true": resolved_status == "supported",
                "question_scope_binding": "in_scope",
                "reason": str(cross_doc_claim.get("aggregate_basis") or ""),
                "evidence_refs": refs,
                "resolved_evidence_refs": refs,
                "canonical_source": refs[0] if refs else "",
                "canonical_sources": refs,
                "local_window": local_window,
                "certification_basis": str(cross_doc_claim.get("aggregate_basis") or ""),
                "missing_atoms": [],
                "conflicting_atoms": [],
                "conflicts": [],
                "lineage_conflict": False,
                "claim_contradiction_docs": list(cross_doc_claim.get("conflicting_docs") or []),
                "opposite_certification_count": 0,
                "model_judgment": desired,
                "resolved_judgment": resolved_status,
                "model_uncertainty_closed_by_typed_evidence": desired == "unresolved",
                "model_disagreement_overridden_by_authoritative_cross_doc": bool(
                    explicit_disagreement and trusted_cross_doc
                ),
                "cross_doc_claim": cross_doc_claim,
                "source_facts": _cross_doc_source_facts(
                    bundle=bundle,
                    cross_doc_claim=cross_doc_claim,
                    field_type=str(cross_doc_spec.get("field_type") or ""),
                    relation_type=str(cross_doc_spec.get("relation_type") or ""),
                ),
            }
            continue

        # multi-slot calibration: source-local binding is authoritative over the
        # legacy broad candidate-window vote whenever solver lineage is known.
        # legacy declared-doc behavior remains on the established path below.
        if not bundle.question.doc_ids:
            calibrated_binding = certify_option_in_binding_scope(
                bundle=bundle,
                result=result,
                option_label=label,
                option_text=option_text,
                candidates=allowed_candidates,
                scope=binding_scope,
                question_options=bundle.question.options,
            )
            calibrated_row, calibrated_resolved = _calibrated_option_verdict(
                binding=calibrated_binding,
                model_judgment=desired,
            )
            calibrated_row["option_text"] = option_text
            option_diagnostics[label] = {
                **option_diagnostics.get(label, {}),
                "model_judgment": desired,
                "resolved_judgment": calibrated_resolved,
                "candidate_count": len(allowed_candidates),
                "binding_status": calibrated_row["binding_status"],
                "binding_adapter": calibrated_binding.get("binding_adapter"),
                "binding_confidence": calibrated_binding.get("confidence"),
                "matched_atoms": list(calibrated_binding.get("matched_atoms") or []),
                "missing_atoms": list(calibrated_binding.get("missing_atoms") or []),
                "contradiction_atoms": list(calibrated_binding.get("contradiction_atoms") or []),
                "corrective_retrieval_gaps": dict(
                    calibrated_binding.get("corrective_retrieval_gaps") or {}
                ),
                "source_local_binding": calibrated_binding,
                "lineage_conflict": calibrated_row["binding_status"] == "lineage_invalid",
            }
            option_verdicts[label] = calibrated_row
            resolved_judgments[label] = calibrated_resolved
            if calibrated_resolved not in {"supported", "contradicted"}:
                trust_failures.append(
                    f"option_{label}:{calibrated_row['binding_status']}:"
                    f"{calibrated_row['certification_basis']}"
                )
            continue

        certified: list[tuple[dict[str, Any], EvidenceCandidate]] = []
        for candidate in allowed_candidates:
            try:
                certification = certify_typed_option_claim(
                    _candidate_payload(bundle, option_text, candidate),
                    replacement_effect="keep_baseline" if desired == "supported" else "no_change",
                )
            except Exception as exc:
                option_diagnostics.setdefault(label, {})["certifier_error"] = exc.__class__.__name__
                continue
            status = str(certification.get("claim_certification_status") or "ambiguous")
            if (
                candidate.retriever == "mineru_structured_table"
                and not structured_table_certification_complete(
                    option_text,
                    "\n\n".join(
                        part.strip()
                        for part in (
                            candidate.before_text,
                            candidate.text,
                            candidate.after_text,
                        )
                        if str(part or "").strip()
                    ),
                    certification,
                    bundle.question.doc_ids,
                )
            ):
                continue
            if status in {"supported", "contradicted"}:
                certified.append((dict(certification), candidate))

        supported_candidates = [
            item for item in certified
            if item[0].get("claim_certification_status") == "supported"
        ]
        contradicted_candidates = [
            item for item in certified
            if item[0].get("claim_certification_status") == "contradicted"
        ]
        selected: tuple[dict[str, Any], EvidenceCandidate] | None = None
        resolved_status = desired
        if desired in {"supported", "contradicted"}:
            desired_candidates = (
                supported_candidates if desired == "supported" else contradicted_candidates
            )
            opposite_candidates = (
                contradicted_candidates if desired == "supported" else supported_candidates
            )
            if desired_candidates and not opposite_candidates:
                selected = max(
                    desired_candidates,
                    key=lambda item: _candidate_rank(item[0], item[1]),
                )
        else:
            # A model "unresolved" judgment is not itself evidence.  One-sided,
            # source-local typed certification may close it; conflicting or
            # absent certifications remain fail-closed.
            desired_candidates = []
            opposite_candidates = []
            if supported_candidates and not contradicted_candidates:
                resolved_status = "supported"
                selected = max(
                    supported_candidates,
                    key=lambda item: _candidate_rank(item[0], item[1]),
                )
            elif contradicted_candidates and not supported_candidates:
                resolved_status = "contradicted"
                selected = max(
                    contradicted_candidates,
                    key=lambda item: _candidate_rank(item[0], item[1]),
                )

        option_diagnostics[label] = {
            **option_diagnostics.get(label, {}),
            "model_judgment": desired,
            "resolved_judgment": resolved_status,
            "candidate_count": len(allowed_candidates),
            "typed_certified_count": len(certified),
            "supported_certification_count": len(supported_candidates),
            "contradicted_certification_count": len(contradicted_candidates),
            "desired_certification_count": len(desired_candidates),
            "opposite_certification_count": len(opposite_candidates),
            "lineage_conflict": bool(supported_candidates and contradicted_candidates),
        }
        if selected is None:
            if desired == "unresolved":
                reason = "model uncertainty not closed by one-sided typed certification"
                if supported_candidates and contradicted_candidates:
                    reason = "both supporting and contradicting typed certifications exist"
            else:
                reason = "no typed certification matching the structured model judgment"
                if opposite_candidates:
                    reason = "opposite typed certification exists in the allowed document lineage"
            trust_failures.append(f"option_{label}:{reason}")
            option_verdicts[label] = {
                "status": "unresolved",
                "claim_route": "typed_claim_unresolved",
                "reason": reason,
                "evidence_refs": [],
                "model_judgment": desired,
                "resolved_judgment": "unresolved",
            }
            resolved_judgments[label] = "unresolved"
            continue

        resolved_judgments[label] = resolved_status

        certification, candidate = selected
        source = str(certification.get("canonical_source") or _canonical_source(candidate.source))
        local_window = str(certification.get("local_window") or "")
        basis = str(certification.get("certification_basis") or "")
        certifier_missing_atoms = list(certification.get("missing_atoms") or [])
        certifier_conflicting_atoms = list(certification.get("conflicting_atoms") or [])
        explicit_claim_contradiction = bool(
            resolved_status == "contradicted"
            and basis
            and any(
                str(atom).startswith(
                    (
                        "entity_attribution:",
                        "net_profit_polarity:",
                        "numeric_value:",
                        "polarity:",
                    )
                )
                for atom in certifier_conflicting_atoms
            )
        )
        missing_atoms = [] if explicit_claim_contradiction else certifier_missing_atoms
        conflicting_atoms = [] if explicit_claim_contradiction else certifier_conflicting_atoms
        scope_contract = _direct_scope_contract(
            option_text=option_text,
            local_window=local_window,
            source=source,
            question_doc_ids=bundle.question.doc_ids,
            missing_atoms=missing_atoms,
            conflicting_atoms=conflicting_atoms,
            explicit_claim_contradiction=explicit_claim_contradiction,
        )
        if not source or not local_window or not basis:
            trust_failures.append(f"option_{label}:incomplete_typed_evidence_lineage")
        if scope_contract["trusted_for_option_gate"] is not True:
            trust_failures.append(f"option_{label}:authoritative_scope_contract_incomplete")
        numeric_derived = _numeric_bound_derived_from_certification(
            bundle=bundle,
            option_label=label,
            option_text=option_text,
            certification=certification,
            candidate=candidate,
            resolved_status=resolved_status,
        )
        if numeric_derived is not None:
            derived_from_certifications.append(numeric_derived)
        direct_source_fact = _direct_certified_source_fact(
            bundle=bundle,
            option_text=option_text,
            certification=certification,
            candidate=candidate,
            resolved_status=resolved_status,
        )
        option_verdicts[label] = {
            "status": resolved_status,
            "claim_route": "exact_clause" if resolved_status == "supported" else "contradiction",
            "typed_claim_route": "typed_claim_local_window",
            **scope_contract,
            "term_equivalence": "confirmed" if resolved_status == "supported" else "not_required",
            "term_equivalence_confirmed": resolved_status == "supported",
            "term_equivalence_required": resolved_status == "supported",
            "factual_statement_true": resolved_status == "supported",
            "question_scope_binding": "in_scope",
            "reason": basis,
            "evidence_refs": [source] if source else [],
            "resolved_evidence_refs": [source] if source else [],
            "canonical_source": source,
            "canonical_sources": [source] if source else [],
            "local_window": local_window,
            "certification_basis": basis,
            "claim_atoms": certification.get("claim_atoms") or {},
            "matched_atoms": list(certification.get("matched_atoms") or []),
            "missing_atoms": missing_atoms,
            "conflicting_atoms": conflicting_atoms,
            "certifier_missing_atoms": certifier_missing_atoms,
            "certifier_conflicting_atoms": certifier_conflicting_atoms,
            "claim_contradiction_atoms": (
                certifier_conflicting_atoms if explicit_claim_contradiction else []
            ),
            "explicit_claim_contradiction": explicit_claim_contradiction,
            "conflicts": [],
            "lineage_conflict": bool(supported_candidates and contradicted_candidates),
            "opposite_certification_count": len(opposite_candidates),
            "model_judgment": desired,
            "resolved_judgment": resolved_status,
            "model_uncertainty_closed_by_typed_evidence": desired == "unresolved",
            "candidate_doc_id": str(candidate.doc_id),
            "candidate_retriever": str(candidate.retriever),
            "source_facts": [direct_source_fact.to_dict()] if direct_source_fact is not None else [],
        }

    unresolved_after_typed = [
        label
        for label in option_labels
        if option_verdicts.get(label, {}).get("status") == "unresolved"
    ]
    if unresolved_after_typed:
        trust_failures.append("incomplete_or_unknown_model_judgments")
    trusted = not trust_failures and all(
        option_verdicts.get(label, {}).get("status") == resolved_judgments[label]
        and resolved_judgments[label] in {"supported", "contradicted"}
        for label in option_labels
    )
    typed_supported_answer = _canonical_answer(
        "".join(label for label in option_labels if option_verdicts.get(label, {}).get("status") == "supported")
    )
    typed_contract_validation = validate_answer_against_contract(typed_supported_answer, contract)
    correction_contract_validation = validate_answer_against_contract(typed_supported_answer, contract)
    if not typed_contract_validation.valid:
        trusted = False
        trust_failures.append(f"typed_supported_answer_contract_violation:{typed_contract_validation.reason}")
    if not correction_contract_validation.valid:
        trusted = False
        trust_failures.append(f"correction_answer_contract_violation:{correction_contract_validation.reason}")
    solver_answer_matches_typed = typed_supported_answer == solver_answer

    # Existing Production Integrity treats unresolved *unselected* options as
    # benign when every selected option is positively grounded and no
    # unselected option is supported.  Preserve that established policy for the
    # multi-slot source-local contract so a previously accepted, grounded answer
    # does not regress merely because unrelated distractors still need adapter
    # work.  Selected unresolved/contradicted, unsafe answer sources, lineage
    # expansion, or an unselected supported option remain hard failures.
    selected_labels = set(solver_answer)
    selected_source_local_supported = bool(selected_labels) and all(
        option_verdicts.get(label, {}).get("binding_status") == "supported"
        for label in selected_labels
    )
    unselected_supported = [
        label
        for label in option_labels
        if label not in selected_labels
        and option_verdicts.get(label, {}).get("binding_status") == "supported"
    ]
    selected_answer_source_local_trusted = bool(
        not bundle.question.doc_ids
        and binding_scope.lineage_valid
        and not binding_scope.fail_closed
        and solver_contract_validation.valid
        and solver_answer_matches_typed
        and selected_source_local_supported
        and not unselected_supported
    )
    if selected_answer_source_local_trusted:
        trusted = True

    payload = {
        "schema_version": "production_typed_option_evidence_v3",
        "trusted_for_production": trusted,
        "trust_failures": sorted(set(trust_failures)),
        "answer_contract": contract_to_dict(contract),
        "solver_answer_contract_validation": solver_contract_validation.to_dict(),
        "typed_supported_answer_contract_validation": typed_contract_validation.to_dict(),
        "correction_answer_contract_validation": correction_contract_validation.to_dict(),
        "solver_answer": solver_answer,
        "typed_supported_answer": typed_supported_answer,
        "solver_answer_matches_typed_supported_answer": solver_answer_matches_typed,
        "selected_answer_source_local_trusted": selected_answer_source_local_trusted,
        "selected_source_local_supported": selected_source_local_supported,
        "unselected_supported_labels": unselected_supported,
        "model_judgments": judgments,
        "resolved_judgments": resolved_judgments,
        "model_uncertainty_closed_labels": sorted(
            label
            for label in option_labels
            if judgments[label] == "unresolved"
            and resolved_judgments[label] in {"supported", "contradicted"}
        ),
        "unresolved_after_typed": unresolved_after_typed,
        "option_verdicts": option_verdicts,
        "option_diagnostics": option_diagnostics,
        "option_coverage": f"{sum(label in option_verdicts for label in option_labels)}/{len(option_labels)}",
        "used_doc_ids": sorted(used_docs),
        "candidate_count_in_used_doc_lineage": len(allowed_candidates),
        **binding_scope.to_dict(),
        "source_local_binding_enabled": not bool(bundle.question.doc_ids),
        "fail_closed_on_untrusted": not bool(bundle.question.doc_ids),
        "correction_proposal": typed_supported_answer if typed_supported_answer else None,
        "correction_differs": bool(typed_supported_answer and typed_supported_answer != solver_answer),
        "legacy_self_check_policy": (
            "diagnostic_only_when_solver_lineage_is_explicit"
            if not bundle.question.doc_ids
            else "audit_only_when_typed_contract_is_trusted"
        ),
        "production_answer_basis": (
            "source_local_typed_binding"
            if not bundle.question.doc_ids
            else "legacy_typed_claim_binding"
        ),
    }
    structured_root = str(bundle.metadata.get("structured_table_root") or "").strip()
    routed_derived = (
        build_derived_option_evidence(bundle.question, structured_root)
        if structured_root
        else ()
    )
    derived = (
        tuple(derived_from_certifications)
        + tuple(routed_derived)
        + _insurance_clause_derived(bundle)
        + _insurance_calculation_derived(bundle)
    )
    payload["production_derived_option_evidence"] = [item.to_dict() for item in derived]
    payload["production_derived_option_count"] = len(derived)
    if derived:
        payload = merge_derived_option_evidence(payload, derived)
    if (
        payload.get("selected_answer_source_local_trusted") is True
        and _canonical_answer(payload.get("typed_supported_answer")) == solver_answer
        and not payload.get("unselected_supported_labels")
    ):
        # Derived sidecars may be incomplete for unrelated distractors.  They
        # remain diagnostic, but must not erase a solver-answer trust decision
        # already closed by source-local selected-option evidence.
        payload["trusted_for_production"] = True
        payload["selected_answer_trust_preserved_after_derived_merge"] = True
    return _apply_override_capability(bundle, payload)
