"""Package C option-evidence slot schema and Package B replacement dry-run helpers.

The module is intentionally qid-agnostic: callers provide baseline/proposed answers,
option slots, and optional Package D calculation grounding metadata.  It returns a
reviewable replacement decision rather than a submission row.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence

from contracts import QuestionAnswerContract
from verification.answer_contract_gate import resolve_replacement_answer_contract
from verification.claim_local_binding import (
    is_generic_numeric_only_legacy,
    source_doc_id,
)
from verification.dual_lineage import accepted_final_state

OPTION_LABELS = ("A", "B", "C", "D")
VALID_STATUSES = {"supported", "contradicted", "not_supported", "not_applicable", "unresolved", "scope_excluded"}
VALID_ROUTES = {
    "direct_evidence",
    "exact_clause",
    "calculation",
    "contradiction",
    "scope_only",
    "weak_related",
    "missing",
}
DIRECT_ADD_ROUTES = {"direct_evidence", "exact_clause", "calculation", "contradiction"}
BLOCKING_CALCULATION_FLAGS = {
    "zero_match",
    "multi_match",
    "unresolved_variables",
    "coverage_gap",
    "no_unique_option_match",
    "zero_option_match",
    "multi_option_match",
}


def canonical_answer(value: Any) -> str:
    return "".join(sorted({ch for ch in str(value or "").upper() if "A" <= ch <= "Z"}))


def _coerce_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Mapping):
        ref = value.get("source") or value.get("doc_id") or value.get("term")
        return [str(ref)] if ref else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        refs: list[str] = []
        for item in value:
            refs.extend(_coerce_refs(item))
        return [ref for ref in refs if ref]
    return [str(value)]


def _as_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_negation(payload: Mapping[str, Any]) -> bool:
    negation = payload.get("negation_found")
    if isinstance(negation, str):
        return bool(negation.strip())
    if isinstance(negation, Sequence) and not isinstance(negation, (str, bytes, bytearray)):
        return any(bool(str(item).strip()) for item in negation)
    return bool(negation)


def _has_anchor_terms(payload: Mapping[str, Any]) -> bool:
    for key in ("required_anchors", "coherent_terms", "matched_terms"):
        terms = payload.get(key)
        if isinstance(terms, Sequence) and not isinstance(terms, (str, bytes, bytearray)):
            if any(str(term).strip() for term in terms):
                return True
    return False


def _evidence_snippet_text(payload: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    matches = payload.get("evidence_matches")
    if isinstance(matches, Sequence) and not isinstance(matches, (str, bytes, bytearray)):
        for match in matches:
            if isinstance(match, Mapping):
                snippet = str(match.get("snippet") or "").strip()
                if snippet:
                    chunks.append(snippet)
    return " ".join(chunks)


def _full_passage_text(payload: Mapping[str, Any]) -> str:
    value = payload.get("full_passage_or_bounded_context")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return ""


def _audit_text(payload: Mapping[str, Any]) -> tuple[str, str]:
    full_passage = _full_passage_text(payload)
    if full_passage:
        return full_passage, "full_passage"
    return _evidence_snippet_text(payload), "short_snippet"


def _terms_from_payload(payload: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("required_anchors", "coherent_terms", "matched_terms"):
        raw = payload.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            terms.extend(str(item).strip() for item in raw if str(item).strip())
    return terms


def audit_payload_evidence_relation(raw: Mapping[str, Any] | None, *, replacement_effect: str = "no_change") -> dict[str, Any]:
    """Audit one option payload against its stored evidence snippets.

    The audit is intentionally conservative and deterministic.  It uses only
    evidence text already stored in diagnostic records.  It does not fetch a
    model or infer support from topic overlap.
    """
    payload = dict(raw or {})
    if payload.get("option_text") and (
        payload.get("source_resolution")
        or payload.get("full_passage_or_bounded_context")
        or payload.get("calculation_refs")
    ):
        from .claim_local_binding import certify_option_claim

        return certify_option_claim(payload, replacement_effect=replacement_effect)
    status = str(payload.get("status") or payload.get("verdict") or "").lower()
    snippets, audit_text_source = _audit_text(payload)
    snippets_lower = snippets.lower()
    refs = _coerce_refs(
        payload.get("resolved_evidence_refs")
        or payload.get("evidence_refs")
        or payload.get("source_refs")
        or payload.get("refs")
        or payload.get("evidence_matches")
    )
    terms = _terms_from_payload(payload)
    meaningful_terms = [term for term in terms if len(term) >= 2 and term.lower() not in {"text", "page"}]
    anchored_terms = [term for term in meaningful_terms if term and term in snippets]
    contradiction_markers = ("不", "未", "无", "不得", "不是", "不存在", "低于", "高于", "减少", "下降", "contradict", "not ", "no ")
    has_contradiction_marker = any(marker in snippets_lower for marker in contradiction_markers)
    numeric_context_complete = payload.get("numeric_context_complete") is not False
    match_ratio = _as_float(payload.get("match_ratio"), 0.0)
    if not refs:
        relation = "missing_evidence_ref"
        safe_rule = "missing refs do not pass"
    elif status == "contradicted" and has_contradiction_marker:
        relation = "contradicted_by_exact_passage"
        safe_rule = "contradiction status plus explicit contradiction marker in evidence snippet"
    elif status == "supported" and meaningful_terms and len(anchored_terms) == len(meaningful_terms) and numeric_context_complete:
        relation = "supported_by_exact_passage"
        safe_rule = "all required/matched terms occur in available evidence snippet"
    elif status == "supported" and match_ratio >= 0.75 and refs and numeric_context_complete and not _has_negation(payload):
        relation = "ambiguous_or_insufficient"
        safe_rule = "high score alone is not enough without exact audited terms"
    elif status in {"missing", "not_supported"} and replacement_effect == "no_change":
        relation = "not_supported_after_evidence_scan"
        safe_rule = "unselected option has no supporting evidence in available refs"
    elif status in {"missing", "not_supported"}:
        relation = "ambiguous_or_insufficient"
        safe_rule = "selected/changed option cannot be disproved merely by missing support"
    else:
        relation = "ambiguous_or_insufficient"
        safe_rule = "no exact support, contradiction, or scope proof in available evidence"
    excerpt = snippets[:180] if snippets else ""
    return {
        "relation_after_audit": relation,
        "safe_rule_candidate": safe_rule,
        "evidence_refs_considered": refs[:5],
        "short_evidence_excerpt_or_reason": excerpt or "no evidence snippet available",
        "audited_terms_found": anchored_terms[:10],
        "audited_terms_missing": [term for term in meaningful_terms if term not in anchored_terms][:10],
        "audit_text_source": audit_text_source,
    }


def _safe_evidence_certification(payload: Mapping[str, Any], *, route: str, refs: Sequence[str], replacement_effect: str) -> dict[str, Any]:
    """Conservatively certify direct option evidence already present in self-check output.

    This does not infer new facts.  It only upgrades the route/term-equivalence
    when the existing self-check payload carries structured anchors or strong
    coherent evidence.  Added options are deliberately stricter: term equivalence
    is never inferred for add_option without an explicit term-equivalence flag.
    """
    status = str(payload.get("status") or payload.get("verdict") or "").lower()
    match_ratio = _as_float(payload.get("match_ratio"), 0.0)
    numeric_complete = payload.get("numeric_context_complete") is not False
    refs_present = bool(refs)
    no_negation = not _has_negation(payload)
    explicit_route = str(payload.get("claim_route") or payload.get("route") or "").lower()
    strong_structured = explicit_route in {"exact_fact", "regulatory_exact_clause", "direct_evidence", "exact_clause"}
    audit = audit_payload_evidence_relation(payload, replacement_effect=replacement_effect)
    audited_support = audit["relation_after_audit"] == "supported_by_exact_passage"
    audited_contradiction = audit["relation_after_audit"] == "contradicted_by_exact_passage"
    route_out = route
    term_out = str(payload.get("term_equivalence") or "")
    status_out = status
    factual_out = _as_bool_or_none(payload.get("factual_statement_true"))
    # A deterministic source-local certification may repair an untrusted model
    # status.  Topic overlap or a model verdict alone never enters these branches.
    if audited_support:
        status_out = "supported"
        factual_out = True
    elif audited_contradiction:
        status_out = "contradicted"
        factual_out = False
    if status_out == "supported" and refs_present and no_negation:
        if strong_structured and audited_support:
            route_out = "exact_clause" if explicit_route in {"exact_fact", "regulatory_exact_clause", "exact_clause"} else "direct_evidence"
        elif route == "missing" and audited_support:
            route_out = "direct_evidence"
        if audited_support and replacement_effect != "add_option" and route_out in {"direct_evidence", "exact_clause", "calculation", "contradiction"}:
            term_out = term_out or "confirmed"
    elif status_out == "contradicted" and refs_present and audited_contradiction:
        if route == "missing":
            route_out = "contradiction"
        term_out = term_out or "not_required"
    return {
        "claim_route": route_out,
        "term_equivalence": term_out,
        "status": status_out,
        "factual_statement_true": factual_out,
        "relation_after_audit": audit["relation_after_audit"],
    }


@dataclass(frozen=True)
class OptionEvidenceSlot:
    option_label: str
    option_text: str
    status: str
    claim_route: str
    evidence_refs: list[str]
    term_equivalence: str
    factual_statement_true: bool | None
    question_scope_binding: str
    calculation_refs: list[str]
    unresolved_reason: str
    replacement_effect: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_option_slot(raw: Mapping[str, Any] | None, *, option_label: str, option_text: str = "", replacement_effect: str = "no_change") -> OptionEvidenceSlot:
    payload = dict(raw or {})
    payload["option_text"] = str(payload.get("option_text") or option_text or "")
    status = str(payload.get("status") or payload.get("verdict") or "unresolved").lower()
    # A missing raw slot means we have no auditable option slot at all, so it
    # stays unresolved.  A present self-check verdict with status=missing means
    # the option was evaluated and no support was found; normalize that to
    # not_supported so selected-vs-unselected policy can distinguish it.
    if status == "missing":
        status = "not_supported" if raw else "unresolved"
    if status == "scope_excluded":
        status = "not_applicable"
    if status not in VALID_STATUSES:
        status = "unresolved"
    # Downstream deterministic certification starts from the normalized schema
    # status, not a legacy alias such as "missing".
    payload["status"] = status
    route = str(payload.get("claim_route") or payload.get("route") or "missing").lower()
    route_aliases = {
        "deterministic_calculation": "calculation",
        "formula_calculation": "calculation",
        "question_scope_exclusion": "scope_only",
        "regulatory_exact_clause": "exact_clause",
        "exact_fact": "exact_clause",
        "direct_contradiction": "contradiction",
        "weak_related_evidence": "weak_related",
        "question_level_truth_value": "weak_related",
        "strict_compound_safety": "weak_related",
    }
    route = route_aliases.get(route, route)
    if route not in VALID_ROUTES:
        route = "missing"
    if route == "scope_only" and status == "contradicted":
        status = "not_applicable"
    evidence_refs = _coerce_refs(
        payload.get("resolved_evidence_refs")
        or payload.get("evidence_refs")
        or payload.get("source_refs")
        or payload.get("refs")
        or payload.get("evidence_matches")
    )
    explicit_term_equivalence = (
        payload.get("term_equivalence")
        or ("confirmed" if payload.get("term_equivalence_confirmed") is True else "")
        or ("not_required" if payload.get("term_equivalence_required") is False else "")
    )
    certification = _safe_evidence_certification(
        payload,
        route=route,
        refs=evidence_refs,
        replacement_effect=replacement_effect,
    )
    route = certification["claim_route"]
    status = str(certification["status"] or status)
    factual_statement_true = certification["factual_statement_true"]
    term_equivalence = str(explicit_term_equivalence or certification["term_equivalence"] or "unknown")
    return OptionEvidenceSlot(
        option_label=str(option_label).upper(),
        option_text=str(payload.get("option_text") or option_text or ""),
        status=status,
        claim_route=route,
        evidence_refs=evidence_refs,
        term_equivalence=term_equivalence,
        factual_statement_true=factual_statement_true,
        question_scope_binding=str(payload.get("question_scope_binding") or payload.get("scope_binding") or ("scope_excluded" if route == "scope_only" else "in_scope")),
        calculation_refs=_coerce_refs(payload.get("calculation_refs")),
        unresolved_reason=str(payload.get("unresolved_reason") or ("missing option evidence slot" if not raw else "")),
        replacement_effect=replacement_effect,
    )


def _typed_option_evidence(*containers: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Return typed evidence even when it is not production-trusted.

    Replay must retain the typed verdicts and their trust failures.  Trust is a
    qualification gate, not a switch that silently replaces typed evidence with
    legacy self-check states.
    """
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        typed = container.get("typed_option_evidence")
        if not isinstance(typed, Mapping):
            continue
        verdicts = typed.get("option_verdicts")
        if isinstance(verdicts, Mapping) and verdicts:
            return typed
    return None


def select_option_evidence_source_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Select one auditable option-evidence source using explicit precedence."""
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    verification_result = record.get("verification_result") if isinstance(record.get("verification_result"), Mapping) else {}
    verification_metadata = verification_result.get("metadata") if isinstance(verification_result.get("metadata"), Mapping) else {}
    preserved_verification = metadata.get("verification_result") if isinstance(metadata.get("verification_result"), Mapping) else {}
    preserved_metadata = preserved_verification.get("metadata") if isinstance(preserved_verification.get("metadata"), Mapping) else {}

    explicit_candidates = [
        ("record.option_evidence_slots", record.get("option_evidence_slots")),
        ("record.option_evidence", record.get("option_evidence")),
        ("metadata.option_evidence_slots", metadata.get("option_evidence_slots")),
        ("metadata.option_evidence", metadata.get("option_evidence")),
    ]
    typed = _typed_option_evidence(metadata, verification_metadata, preserved_metadata)
    typed_present = isinstance(typed, Mapping)
    typed_trusted = bool(typed_present and typed.get("trusted_for_production") is True)

    legacy_candidates: list[tuple[str, Any]] = []
    for location, container in (
        ("metadata.self_check", metadata),
        ("verification_metadata.self_check", verification_metadata),
        ("preserved_metadata.self_check", preserved_metadata),
    ):
        self_check = container.get("self_check") if isinstance(container.get("self_check"), Mapping) else None
        if isinstance(self_check, Mapping):
            legacy_candidates.append((location, self_check.get("option_verdicts")))
    legacy_available = any(isinstance(payload, Mapping) and payload for _, payload in legacy_candidates)

    for location, payload in explicit_candidates:
        if isinstance(payload, Mapping) and payload:
            return {
                "source": "explicit_option_slots",
                "source_location": location,
                "option_payloads": payload,
                "typed_present": typed_present,
                "typed_trusted": typed_trusted,
                "typed_evidence": dict(typed) if typed_present else None,
                "typed_trust_failures": list(typed.get("trust_failures") or []) if typed_present else [],
                "legacy_context_available": legacy_available,
                "fallback_used": False,
                "fallback_reason": "",
            }

    if typed_present:
        return {
            "source": "typed_option_evidence",
            "source_location": "typed_option_evidence.option_verdicts",
            "option_payloads": typed.get("option_verdicts") or {},
            "typed_present": True,
            "typed_trusted": typed_trusted,
            "typed_evidence": dict(typed),
            "typed_trust_failures": list(typed.get("trust_failures") or []),
            "legacy_context_available": legacy_available,
            "fallback_used": False,
            "fallback_reason": "typed evidence retained regardless of trust state",
        }

    for location, payload in legacy_candidates:
        if isinstance(payload, Mapping) and payload:
            return {
                "source": "legacy_self_check",
                "source_location": location,
                "option_payloads": payload,
                "typed_present": False,
                "typed_trusted": False,
                "typed_evidence": None,
                "typed_trust_failures": [],
                "legacy_context_available": True,
                "fallback_used": True,
                "fallback_reason": "no explicit option slots or typed option evidence",
            }

    return {
        "source": "none",
        "source_location": "",
        "option_payloads": {},
        "typed_present": False,
        "typed_trusted": False,
        "typed_evidence": None,
        "typed_trust_failures": [],
        "legacy_context_available": False,
        "fallback_used": False,
        "fallback_reason": "no option evidence source available",
    }


def audit_legacy_against_source_local_typed(record: Mapping[str, Any]) -> dict[str, Any]:
    """Compare legacy self-check with source-local typed verdicts.

    The function is diagnostic and qid-agnostic.  It records why legacy output
    cannot override typed binding when its coherent source is outside the
    solver-used documents or when support consists only of generic numeric
    tokens.
    """
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    typed = metadata.get("typed_option_evidence")
    typed_map = dict(typed) if isinstance(typed, Mapping) else {}
    typed_verdicts = typed_map.get("option_verdicts")
    typed_rows = dict(typed_verdicts) if isinstance(typed_verdicts, Mapping) else {}
    legacy = metadata.get("self_check")
    legacy_rows = (
        dict(legacy.get("option_verdicts") or {})
        if isinstance(legacy, Mapping)
        else {}
    )
    solver_docs = set(
        str(value)
        for value in (
            metadata.get("solver_used_doc_ids")
            or typed_map.get("solver_used_doc_ids")
            or typed_map.get("used_doc_ids")
            or []
        )
        if str(value)
    )
    labels = sorted(set(str(value).upper() for value in (*typed_rows.keys(), *legacy_rows.keys())))
    options: dict[str, dict[str, Any]] = {}
    override_count = 0
    for label in labels:
        typed_row = typed_rows.get(label) or typed_rows.get(label.lower()) or {}
        legacy_row = legacy_rows.get(label) or legacy_rows.get(label.lower()) or {}
        typed_status = str(
            typed_row.get("binding_status")
            or typed_row.get("source_local_verdict")
            or typed_row.get("status")
            or "unresolved_adapter_unavailable"
        )
        legacy_status = str(legacy_row.get("status") or "missing")
        legacy_source = str(legacy_row.get("coherent_source") or "")
        if not legacy_source:
            matches = legacy_row.get("evidence_matches")
            if isinstance(matches, Sequence) and not isinstance(matches, (str, bytes, bytearray)):
                first = next((item for item in matches if isinstance(item, Mapping)), None)
                if first is not None:
                    legacy_source = str(first.get("source") or "")
        legacy_doc = source_doc_id(legacy_source)
        outside_solver_docs = bool(legacy_doc and solver_docs and legacy_doc not in solver_docs)
        numeric_only = is_generic_numeric_only_legacy(legacy_row)
        typed_authoritative = typed_status in {"supported", "contradicted"}
        override = bool(
            typed_authoritative
            and (
                legacy_status != typed_status
                or outside_solver_docs
                or numeric_only
            )
        )
        reasons: list[str] = []
        if outside_solver_docs:
            reasons.append("legacy_source_outside_solver_used_docs")
        if numeric_only:
            reasons.append("legacy_support_is_generic_numeric_only")
        if typed_authoritative and legacy_status != typed_status:
            reasons.append("source_local_typed_verdict_differs_from_legacy")
        if override:
            override_count += 1
        options[label] = {
            "legacy_verdict": legacy_status,
            "legacy_coherent_source": legacy_source,
            "legacy_source_doc_id": legacy_doc,
            "legacy_source_outside_solver_docs": outside_solver_docs,
            "legacy_generic_numeric_only": numeric_only,
            "typed_source_local_verdict": typed_status,
            "typed_evidence_refs": list(typed_row.get("evidence_refs") or []),
            "legacy_override_applied": override,
            "legacy_override_reason": reasons,
            "final_production_basis": (
                "source_local_typed_binding"
                if typed_authoritative
                else typed_status
            ),
        }
    return {
        "schema_version": "legacy_vs_source_local_typed_audit_v1",
        "solver_used_doc_ids": sorted(solver_docs),
        "legacy_authority": "diagnostic_only",
        "legacy_override_count": override_count,
        "options": options,
    }


def _extract_slot_payloads(record: Mapping[str, Any]) -> Mapping[str, Any]:
    return select_option_evidence_source_from_record(record)["option_payloads"]


def extract_option_payloads_from_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Public wrapper used by offline evidence replay tooling."""
    return _extract_slot_payloads(record)


def normalize_option_evidence_slots(
    *,
    baseline_answer: str,
    proposed_answer: str,
    option_payloads: Mapping[str, Any] | None = None,
    option_texts: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    baseline = set(canonical_answer(baseline_answer))
    proposed = set(canonical_answer(proposed_answer))
    payloads = dict(option_payloads or {})
    texts = dict(option_texts or {})
    option_definition_known = option_texts is not None
    slots: list[dict[str, Any]] = []
    for label in OPTION_LABELS:
        if label in proposed - baseline:
            effect = "add_option"
        elif label in baseline - proposed:
            effect = "remove_option"
        elif label in baseline and label in proposed:
            effect = "keep_baseline"
        else:
            effect = "no_change"
        option_defined = not option_definition_known or label in texts
        raw = payloads.get(label) or payloads.get(label.lower()) or payloads.get(f"option_{label}") or payloads.get(f"option_{label.lower()}")
        if not option_defined:
            raw = {
                "status": "not_applicable",
                "claim_route": "scope_only",
                "question_scope_binding": "scope_excluded",
                "unresolved_reason": "option label is not defined by the raw question",
            }
        slot = normalize_option_slot(
            raw if isinstance(raw, Mapping) else None,
            option_label=label,
            option_text=texts.get(label, ""),
            replacement_effect=effect,
        ).to_dict()
        if option_definition_known:
            slot["option_defined"] = option_defined
        slots.append(slot)
    return slots


def normalize_option_evidence_slots_from_record(
    *,
    baseline_answer: str,
    proposed_answer: str,
    record: Mapping[str, Any],
    option_texts: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    selection = select_option_evidence_source_from_record(record)
    return normalize_option_evidence_slots(
        baseline_answer=baseline_answer,
        proposed_answer=proposed_answer,
        option_payloads=selection["option_payloads"],
        option_texts=option_texts,
    )


def _calculation_block_reasons(calculation_grounding: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(calculation_grounding, Mapping):
        return []
    reasons: list[str] = []
    for key in BLOCKING_CALCULATION_FLAGS:
        if calculation_grounding.get(key) is True:
            reasons.append(key)
    candidate_reason = str(calculation_grounding.get("candidate_block_reason") or "")
    if candidate_reason:
        reasons.append(candidate_reason)
    return sorted(set(reasons))


def replacement_decision_from_slots(
    *,
    qid: str,
    baseline_answer: str,
    proposed_answer: str,
    slots: Sequence[Mapping[str, Any]],
    calculation_grounding: Mapping[str, Any] | None = None,
    correction_proposal: str | None = None,
    correction_differs: bool | None = None,
    answer_format: str | None = None,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_proposed = str(proposed_answer or "")
    raw_correction = str(correction_proposal or "")
    baseline = canonical_answer(baseline_answer)
    contract_resolution = resolve_replacement_answer_contract(
        baseline_answer=baseline,
        proposed_answer=raw_proposed,
        correction_answer=raw_correction,
        answer_format=answer_format,
        answer_contract=answer_contract,
    )
    validations = dict(contract_resolution.get("validations") or {})
    normalized_proposed = str((validations.get("proposed") or {}).get("answer") or "")
    normalized_correction = str((validations.get("correction") or {}).get("answer") or "")
    # Keep the normalized proposal visible for evidence diagnostics, while the
    # final effective answer below remains fail-closed on every contract block.
    proposed = canonical_answer(normalized_proposed or raw_proposed)
    correction = canonical_answer(normalized_correction or raw_correction)
    contract_reason = str(contract_resolution.get("reason") or "unknown_answer_contract")
    contract_block_reasons = (
        []
        if contract_reason in {"contract_valid_replacement", "baseline_preserve"}
        else [contract_reason]
    )
    contract_gate = {
        "contract": contract_resolution.get("contract"),
        "valid": not contract_block_reasons,
        "block_reasons": contract_block_reasons,
        "validations": validations,
    }
    by_label = {str(slot.get("option_label") or "").upper(): dict(slot) for slot in slots}
    block_reasons: list[str] = []
    review_reasons: list[str] = []
    hard_unresolved_reasons: list[str] = []
    changed_options = sorted(set(baseline) ^ set(proposed))
    selected = set(proposed)
    for reason in contract_block_reasons:
        block_reasons.append(reason)
        hard_unresolved_reasons.append(reason)

    for label in OPTION_LABELS:
        slot = by_label.get(label)
        if not slot:
            reason = f"option_slot_{label}_missing"
            block_reasons.append(reason)
            hard_unresolved_reasons.append(reason)
            continue
        status = str(slot.get("status") or "")
        route = str(slot.get("claim_route") or "")
        refs_present = bool(slot.get("evidence_refs") or slot.get("calculation_refs"))
        if not status or not route:
            reason = f"option_slot_{label}_incomplete"
            block_reasons.append(reason)
            hard_unresolved_reasons.append(reason)
        if label in selected:
            if status == "supported":
                if route in {"weak_related", "missing"} or not refs_present:
                    reason = f"selected_option_{label}_support_needs_review"
                    block_reasons.append(reason)
                    review_reasons.append(reason)
            elif status == "not_supported":
                reason = f"selected_option_{label}_not_supported"
                block_reasons.append(reason)
                hard_unresolved_reasons.append(reason)
            elif status == "contradicted":
                reason = f"selected_option_{label}_contradicted"
                block_reasons.append(reason)
                hard_unresolved_reasons.append(reason)
            elif status == "not_applicable":
                reason = f"selected_option_{label}_not_applicable"
                block_reasons.append(reason)
                hard_unresolved_reasons.append(reason)
            else:
                reason = f"selected_option_{label}_unresolved"
                block_reasons.append(reason)
                review_reasons.append(reason)
                if label in changed_options:
                    legacy_reason = f"option_slot_{label}_unresolved"
                    block_reasons.append(legacy_reason)
                    review_reasons.append(legacy_reason)
        else:
            if status == "supported":
                reason = f"unselected_option_{label}_supported"
                block_reasons.append(reason)
                hard_unresolved_reasons.append(reason)
            elif status == "unresolved":
                reason = f"unselected_option_{label}_unresolved"
                block_reasons.append(reason)
                review_reasons.append(reason)

    for label in sorted(set(proposed) - set(baseline)):
        slot = by_label.get(label, {})
        route = str(slot.get("claim_route") or "")
        term = str(slot.get("term_equivalence") or "")
        refs_present = bool(slot.get("evidence_refs") or slot.get("calculation_refs"))
        if not (slot.get("status") == "supported" and route in DIRECT_ADD_ROUTES and term in {"confirmed", "not_required"} and refs_present):
            reason = f"add_option_{label}_missing_direct_evidence_or_term_equivalence"
            block_reasons.append(reason)
            review_reasons.append(reason)
    for label in sorted(set(baseline) - set(proposed)):
        slot = by_label.get(label, {})
        factual_true = slot.get("factual_statement_true") is True
        scope_only = slot.get("claim_route") == "scope_only" or slot.get("status") == "not_applicable"
        factual_false = slot.get("factual_statement_true") is False
        contradicted = slot.get("status") == "contradicted" or slot.get("claim_route") == "contradiction"
        intent_mismatch = (
            slot.get("question_scope_binding") == "out_of_requested_intent"
            and slot.get("claim_route") == "contradiction"
            and bool(slot.get("evidence_refs") or slot.get("calculation_refs"))
        )
        if scope_only and factual_true:
            reason = f"remove_option_{label}_scope_only_no_contradiction"
            block_reasons.append(reason)
            hard_unresolved_reasons.append(reason)
        elif not (contradicted and (factual_false or intent_mismatch)):
            reason = f"remove_option_{label}_missing_direct_contradiction"
            block_reasons.append(reason)
            hard_unresolved_reasons.append(reason)
    if correction and correction != proposed:
        reason = "correction_mismatch_requires_option_level_proof"
        block_reasons.append(reason)
        hard_unresolved_reasons.append(reason)
    elif correction_differs is True and not correction:
        reason = "correction_mismatch_unresolved"
        block_reasons.append(reason)
        review_reasons.append(reason)

    calc_blocks = _calculation_block_reasons(calculation_grounding)
    for calc_reason in calc_blocks:
        reason = f"calculation_{calc_reason}"
        block_reasons.append(reason)
        hard_unresolved_reasons.append(reason)

    unique_block_reasons = sorted(set(block_reasons))
    evidence_ready = proposed != "" and not unique_block_reasons
    baseline_preserve_candidate = proposed == baseline and proposed != ""
    accepted_for_replacement = bool(evidence_ready and not baseline_preserve_candidate)
    if accepted_for_replacement:
        decision_class = "replacement_ready"
    elif baseline_preserve_candidate and evidence_ready:
        decision_class = "baseline_preserve"
    elif baseline_preserve_candidate:
        decision_class = "baseline_preserve_needs_evidence"
    else:
        decision_class = "blocked"
    final_effective_answer = proposed if accepted_for_replacement else baseline
    fallback_to_baseline = bool(
        contract_resolution.get("fallback_to_baseline")
        or (proposed != baseline and not accepted_for_replacement)
    )
    return {
        "qid": qid,
        "baseline_answer": baseline,
        "raw_proposed_answer": raw_proposed,
        "normalized_proposed_answer": normalized_proposed,
        "proposed_answer": proposed,
        "correction_answer": correction,
        "correction_proposal": correction,
        "effective_answer": final_effective_answer,
        "replacement_allowed": accepted_for_replacement,
        "fallback_to_baseline": fallback_to_baseline,
        "answer_contract_reason": contract_reason,
        "answer_contract_validations": validations,
        "changed_options": changed_options,
        "answer_contract_gate": contract_gate,
        "per_option_evidence_status": {label: by_label.get(label, {}) for label in OPTION_LABELS},
        "accepted_for_replacement": accepted_for_replacement,
        "baseline_preserve_candidate": baseline_preserve_candidate,
        "evidence_ready": evidence_ready,
        "decision_class": decision_class,
        "review_required_reasons": sorted(set(review_reasons)),
        "hard_unresolved_reasons": sorted(set(hard_unresolved_reasons)),
        "block_reason": ";".join(unique_block_reasons),
    }


def replacement_decision_from_record(
    *,
    qid: str,
    baseline_answer: str,
    proposed_answer: str,
    record: Mapping[str, Any],
    answer_format: str | None = None,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
    option_texts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    selection = select_option_evidence_source_from_record(record)
    slots = normalize_option_evidence_slots(
        baseline_answer=baseline_answer,
        proposed_answer=proposed_answer,
        option_payloads=selection["option_payloads"],
        option_texts=option_texts,
    )
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    solver_meta = metadata.get("solver_metadata") if isinstance(metadata.get("solver_metadata"), Mapping) else {}
    calculation_grounding = metadata.get("calculation_grounding") or solver_meta.get("calculation_grounding")
    verification_result = record.get("verification_result") if isinstance(record.get("verification_result"), Mapping) else {}
    verification_metadata = verification_result.get("metadata") if isinstance(verification_result.get("metadata"), Mapping) else {}
    preserved = metadata.get("verification_result") if isinstance(metadata.get("verification_result"), Mapping) else {}
    preserved_meta = preserved.get("metadata") if isinstance(preserved.get("metadata"), Mapping) else {}
    typed = selection.get("typed_evidence") if isinstance(selection.get("typed_evidence"), Mapping) else None
    correction_source = "legacy_self_check"
    if isinstance(typed, Mapping):
        correction_proposal = typed.get("correction_proposal") or typed.get("typed_supported_answer")
        correction_differs = typed.get("correction_differs")
        correction_source = "typed_option_evidence"
    else:
        self_check = verification_metadata.get("self_check") if isinstance(verification_metadata.get("self_check"), Mapping) else None
        if not isinstance(self_check, Mapping):
            self_check = preserved_meta.get("self_check") if isinstance(preserved_meta.get("self_check"), Mapping) else None
        if not isinstance(self_check, Mapping):
            self_check = metadata.get("self_check") if isinstance(metadata.get("self_check"), Mapping) else None
        correction_proposal = self_check.get("correction_proposal") if isinstance(self_check, Mapping) else None
        correction_differs = self_check.get("correction_differs") if isinstance(self_check, Mapping) else None
    resolved_contract = answer_contract
    if resolved_contract is None:
        resolved_contract = metadata.get("answer_contract")
    if resolved_contract is None and isinstance(typed, Mapping):
        resolved_contract = typed.get("answer_contract")
    resolved_format = answer_format or str(record.get("answer_format") or metadata.get("answer_format") or "")
    decision = replacement_decision_from_slots(
        qid=qid,
        baseline_answer=baseline_answer,
        proposed_answer=proposed_answer,
        slots=slots,
        calculation_grounding=calculation_grounding if isinstance(calculation_grounding, Mapping) else None,
        correction_proposal=str(correction_proposal) if correction_proposal is not None else None,
        correction_differs=bool(correction_differs) if correction_differs is not None else None,
        answer_format=resolved_format or None,
        answer_contract=resolved_contract,
    )

    authority_blocks: list[str] = []
    source = str(selection.get("source") or "none")
    if source == "typed_option_evidence" and selection.get("typed_trusted") is not True:
        authority_blocks.append("typed_option_evidence_untrusted")
    elif source == "legacy_self_check" and canonical_answer(proposed_answer) != canonical_answer(baseline_answer):
        authority_blocks.append("legacy_self_check_not_replacement_authoritative")
    elif source == "none":
        authority_blocks.append("no_option_evidence_source")

    if authority_blocks:
        existing = [reason for reason in str(decision.get("block_reason") or "").split(";") if reason]
        merged = sorted(set(existing + authority_blocks))
        decision["block_reason"] = ";".join(merged)
        decision["hard_unresolved_reasons"] = sorted(
            set(list(decision.get("hard_unresolved_reasons") or []) + authority_blocks)
        )
        decision["evidence_ready"] = False
        decision["accepted_for_replacement"] = False
        decision["replacement_allowed"] = False
        decision["effective_answer"] = decision["baseline_answer"]
        decision["fallback_to_baseline"] = bool(
            decision.get("fallback_to_baseline")
            or decision.get("normalized_proposed_answer") != decision["baseline_answer"]
        )
        decision["decision_class"] = (
            "baseline_preserve_needs_evidence"
            if canonical_answer(proposed_answer) == canonical_answer(baseline_answer) and canonical_answer(proposed_answer)
            else "blocked"
        )

    workflow_state = str(metadata.get("final_state") or "unknown").lower()
    typed_present = bool(selection.get("typed_present"))
    typed_trusted = bool(selection.get("typed_trusted"))
    if accepted_final_state(workflow_state) and typed_present and not typed_trusted:
        diagnostic_class = "workflow_accepted_typed_untrusted"
    elif accepted_final_state(workflow_state):
        diagnostic_class = "workflow_accepted"
    elif typed_present and typed_trusted:
        diagnostic_class = "workflow_blocked_typed_trusted"
    elif typed_present:
        diagnostic_class = "workflow_blocked_typed_untrusted"
    else:
        diagnostic_class = "workflow_blocked_without_typed_evidence"

    audit = {
        key: value
        for key, value in selection.items()
        if key not in {"option_payloads", "typed_evidence"}
    }
    decision["correction_source"] = correction_source
    decision["evidence_source_selection"] = audit
    decision["workflow_final_state"] = workflow_state
    decision["production_typed_present"] = typed_present
    decision["production_typed_trusted"] = typed_trusted
    decision["typed_option_evidence_trusted"] = typed_trusted
    decision["replay_diagnostic_class"] = diagnostic_class
    decision["option_text_missing_count"] = sum(
        1 for slot in (decision.get("per_option_evidence_status") or {}).values()
        if slot.get("option_defined") is not False
        and not str(slot.get("option_text") or "").strip()
    )
    decision["undefined_option_labels"] = [
        label
        for label, slot in (decision.get("per_option_evidence_status") or {}).items()
        if slot.get("option_defined") is False
    ]
    return decision
