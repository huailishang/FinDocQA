"""Canonical replacement qualification shared by replay and candidate builders.

The object in this module is the only replacement decision consumed by Package S
and the accepted-only candidate builder.  It deliberately keeps the saved runtime
proposal visible, but it never promotes a blocked proposal unless either the
production option-evidence decision passed or a complete independent Oracle proves
all defined options.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from verification.answer_contract_gate import assess_replacement_answer_contract
from verification.option_evidence_schema import (
    OPTION_LABELS,
    canonical_answer,
    replacement_decision_from_record,
)
from verification.scope_absence import TrustedDocumentSource, validate_scope_absence_proof

SAFE_ANSWER_SOURCES = {"generated", "fallback", "deterministic", "oracle", "typed_evidence"}
DIRECT_SUPPORT_ROUTES = {"direct_evidence", "exact_clause", "calculation", "contradiction"}
CONTRADICTION_STATUSES = {"contradicted", "not_supported", "not_applicable", "scope_excluded"}
PRODUCTION_INTEGRITY_PREFIX = "production_integrity:"


def _metadata(record: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(record, Mapping):
        return {}
    value = record.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _normalise_oracle(oracle: Mapping[str, Any] | None) -> tuple[str, dict[str, dict[str, Any]]]:
    if not isinstance(oracle, Mapping):
        return "not_run", {}
    status = str(oracle.get("status") or oracle.get("oracle_status") or "not_run").lower()
    raw = oracle.get("options") or oracle.get("per_option_verdicts") or {}
    options: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for label in OPTION_LABELS:
            item = raw.get(label)
            if isinstance(item, Mapping):
                options[label] = dict(item)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            label = str(item.get("option") or item.get("option_label") or "").upper()
            if label in OPTION_LABELS:
                options[label] = dict(item)
    return status, options


def _slot_payload(
    payload: Mapping[str, Any],
    *,
    source: str,
    default_term_equivalence: str,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None,
) -> dict[str, Any]:
    status = str(payload.get("status") or payload.get("verdict") or "unresolved").lower()
    refs = payload.get("evidence_refs") or payload.get("calculation_refs") or payload.get("source_document") or payload.get("source") or []
    if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
        evidence_refs = list(refs)
    else:
        evidence_refs = [str(refs)] if refs else []
    route = str(payload.get("claim_route") or payload.get("route") or "").lower()
    if not route:
        route = "scope_only" if status in {"not_applicable", "scope_excluded", "scope_absent"} else ("contradiction" if status in CONTRADICTION_STATUSES else "direct_evidence")

    proof = payload.get("scope_absence_proof")
    proof_trusted_documents = trusted_declared_documents
    if isinstance(proof, Mapping) and isinstance(trusted_declared_documents, Mapping):
        required_doc_ids = proof.get("required_doc_ids")
        if isinstance(required_doc_ids, Sequence) and not isinstance(
            required_doc_ids, (str, bytes, bytearray)
        ):
            proof_trusted_documents = {
                str(doc_id): trusted_declared_documents[str(doc_id)]
                for doc_id in required_doc_ids
                if str(doc_id) in trusted_declared_documents
            }
    proof_validation = validate_scope_absence_proof(
        proof,
        trusted_declared_documents=proof_trusted_documents,
    )
    if status == "scope_absent":
        factual_statement_true = None
        question_scope_binding = "scope_absent"
    elif status in {"supported", "contradicted"}:
        factual_statement_true = status == "supported"
        question_scope_binding = str(payload.get("question_scope_binding") or "in_scope")
    else:
        factual_statement_true = payload.get("factual_statement_true")
        question_scope_binding = str(payload.get("question_scope_binding") or "")

    return {
        "status": status,
        "claim_route": route,
        "evidence_refs_present": bool(evidence_refs or payload.get("text_anchor") or payload.get("evidence")),
        "evidence_refs": evidence_refs,
        "term_equivalence": str(payload.get("term_equivalence") or default_term_equivalence),
        "factual_statement_true": factual_statement_true,
        "question_scope_binding": question_scope_binding,
        "scope_absence_proof": proof_validation.normalized_proof if payload.get("scope_absence_proof") is not None else None,
        "scope_absence_proof_valid": proof_validation.valid if status == "scope_absent" else False,
        "scope_absence_proof_errors": list(proof_validation.errors) if status == "scope_absent" else [],
        "source": source,
        "reason": str(payload.get("reason") or ""),
    }


def _production_slot(
    slot: Mapping[str, Any] | None,
    *,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None,
) -> dict[str, Any]:
    return _slot_payload(
        dict(slot or {}),
        source="production_typed_evidence",
        default_term_equivalence="",
        trusted_declared_documents=trusted_declared_documents,
    )


def _oracle_slot(
    item: Mapping[str, Any],
    *,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None,
) -> dict[str, Any]:
    return _slot_payload(
        item,
        source="independent_oracle",
        default_term_equivalence="not_required",
        trusted_declared_documents=trusted_declared_documents,
    )


def _defined_labels(option_texts: Mapping[str, str] | None) -> list[str]:
    if isinstance(option_texts, Mapping):
        labels = [label for label in OPTION_LABELS if str(option_texts.get(label) or "").strip()]
        if labels:
            return labels
    return list(OPTION_LABELS)


def _supported(slot: Mapping[str, Any]) -> bool:
    return (
        str(slot.get("status") or "") == "supported"
        and bool(slot.get("evidence_refs_present"))
        and str(slot.get("claim_route") or "") in DIRECT_SUPPORT_ROUTES
    )


def _disposed(slot: Mapping[str, Any]) -> bool:
    status = str(slot.get("status") or "")
    route = str(slot.get("claim_route") or "")
    if status == "scope_absent":
        return route == "scope_only" and bool(slot.get("scope_absence_proof_valid"))
    if not bool(slot.get("evidence_refs_present")):
        return False
    if status in {"not_applicable", "scope_excluded"}:
        return route == "scope_only"
    if status in {"contradicted", "not_supported"}:
        return route in {"contradiction", "direct_evidence", "exact_clause", "calculation"}
    return False


def _saved_proposal(record: Mapping[str, Any] | None, explicit: str | None = None) -> str:
    if explicit is not None:
        return canonical_answer(explicit)
    record_map = record if isinstance(record, Mapping) else {}
    direct = canonical_answer(record_map.get("answer"))
    if direct:
        return direct
    metadata = _metadata(record_map)
    return canonical_answer(metadata.get("attempted_answer") or metadata.get("proposed_answer"))


@dataclass(frozen=True)
class ReplacementQualification:
    qid: str
    baseline_answer: str
    proposed_answer: str
    effective_answer: str
    runtime_final_state: str
    record_error: str
    answer_contract_valid: bool
    source_safe: bool
    truncation_safe: bool
    typed_evidence_trusted: bool
    per_option_verdicts: dict[str, dict[str, Any]]
    defined_option_labels: list[str]
    added_options: list[str]
    removed_options: list[str]
    added_option_support_complete: bool
    removed_option_contradiction_complete: bool
    selected_option_support_complete: bool
    unselected_option_disposition_complete: bool
    correction_reconciled: bool
    independent_oracle_status: str
    independent_oracle_answer: str
    oracle_matches_proposal: bool
    production_qualification_pass: bool
    replacement_allowed: bool
    qualification_tier: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualify_replacement(
    *,
    qid: str,
    baseline_answer: str,
    proposed_answer: str,
    record: Mapping[str, Any] | None,
    answer_format: str | None = None,
    option_texts: Mapping[str, str] | None = None,
    independent_oracle: Mapping[str, Any] | None = None,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None = None,
) -> ReplacementQualification:
    """Build one fail-closed replacement qualification object."""
    baseline = canonical_answer(baseline_answer)
    proposed = canonical_answer(proposed_answer)
    record_map = dict(record or {})
    metadata = _metadata(record_map)
    decision = replacement_decision_from_record(
        qid=qid,
        baseline_answer=baseline,
        proposed_answer=proposed,
        record=record_map,
        answer_format=answer_format,
        option_texts=option_texts,
    )

    contract_gate = decision.get("answer_contract_gate") or {}
    answer_contract_valid = bool(contract_gate.get("valid"))
    runtime_final_state = str(metadata.get("final_state") or "missing").lower()
    record_error = str(record_map.get("error") or "")
    answer_source = str(metadata.get("answer_source") or "generated").lower()
    truncation_safe = not bool(metadata.get("truncation_risk"))
    typed_evidence_trusted = bool(
        decision.get("typed_option_evidence_trusted") is True
        or metadata.get("typed_option_evidence_trusted") is True
    )

    oracle_status, oracle_options = _normalise_oracle(independent_oracle)
    labels = _defined_labels(option_texts)
    oracle_complete = oracle_status == "pass" and all(label in oracle_options for label in labels)
    if oracle_complete:
        # An independent Oracle reconciles evidence/correction conflicts, but the
        # baseline and proposed answer must still satisfy the question contract.
        answer_contract_valid = bool(
            assess_replacement_answer_contract(
                baseline_answer=baseline,
                proposed_answer=proposed,
                answer_format=answer_format,
            ).get("valid")
        )
    production_slots = decision.get("per_option_evidence_status") or {}
    per_option: dict[str, dict[str, Any]] = {}
    for label in OPTION_LABELS:
        if oracle_complete and label in oracle_options:
            per_option[label] = _oracle_slot(
                oracle_options[label],
                trusted_declared_documents=trusted_declared_documents,
            )
        else:
            slot = production_slots.get(label) if isinstance(production_slots, Mapping) else None
            per_option[label] = _production_slot(
                slot if isinstance(slot, Mapping) else None,
                trusted_declared_documents=trusted_declared_documents,
            )

    oracle_answer = canonical_answer(
        independent_oracle.get("oracle_answer") if isinstance(independent_oracle, Mapping) else ""
    )
    if oracle_complete and not oracle_answer:
        oracle_answer = "".join(label for label in labels if per_option[label]["status"] == "supported")
    oracle_matches = bool(oracle_complete and oracle_answer == proposed)

    selected = set(proposed)
    added = sorted(selected - set(baseline))
    removed = sorted(set(baseline) - selected)
    selected_complete = bool(proposed) and all(_supported(per_option[label]) for label in proposed)
    unselected_complete = all(_disposed(per_option[label]) for label in labels if label not in selected)
    added_complete = all(_supported(per_option[label]) for label in added)
    removed_complete = all(_disposed(per_option[label]) for label in removed)

    correction_conflict = bool(
        metadata.get("correction_differs")
        or metadata.get("correction_gate_required")
        or "correction_proposal_differs" in (metadata.get("blocking_reasons") or [])
    )
    correction_reconciled = not correction_conflict or oracle_complete

    integrity_only_error = bool(record_error.startswith(PRODUCTION_INTEGRITY_PREFIX))
    if oracle_complete and integrity_only_error and proposed:
        source_safe = True
    else:
        source_safe = answer_source in SAFE_ANSWER_SOURCES and not record_error

    production_pass = bool(decision.get("replacement_allowed"))
    oracle_pass = bool(
        oracle_complete
        and oracle_matches
        and selected_complete
        and unselected_complete
        and added_complete
        and removed_complete
    )

    reasons: list[str] = []
    if not proposed:
        reasons.append("empty_proposed_answer")
    if not answer_contract_valid:
        reasons.append("answer_contract_invalid")
    if not source_safe:
        reasons.append("unsafe_answer_source_or_runtime_error")
    if not truncation_safe:
        reasons.append("truncation_risk")
    if not correction_reconciled:
        reasons.append("correction_not_reconciled")
    if oracle_complete:
        if not oracle_matches:
            reasons.append("independent_oracle_answer_differs_from_proposal")
        if not selected_complete:
            reasons.append("selected_option_support_incomplete")
        if not unselected_complete:
            reasons.append("unselected_option_disposition_incomplete")
        if not added_complete:
            reasons.append("added_option_support_incomplete")
        if not removed_complete:
            reasons.append("removed_option_contradiction_incomplete")
    elif not production_pass:
        reasons.append("production_qualification_failed_and_no_complete_oracle")
        reasons.extend(
            reason for reason in str(decision.get("block_reason") or "").split(";")
            if reason
        )

    replacement_allowed = bool(
        proposed != baseline
        and answer_contract_valid
        and source_safe
        and truncation_safe
        and correction_reconciled
        and (oracle_pass or production_pass)
    )
    effective_answer = proposed if replacement_allowed else baseline

    if replacement_allowed and oracle_pass:
        tier = "A_INDEPENDENT_ORACLE_AND_QUALIFICATION_PASS"
    elif replacement_allowed:
        tier = "B_PRODUCTION_QUALIFICATION_PASS"
    elif oracle_complete and oracle_answer == baseline:
        tier = "BASELINE_PRESERVE_ORACLE"
    elif proposed == baseline or not proposed:
        tier = "BASELINE_PRESERVE"
    else:
        tier = "BLOCKED"

    if proposed == baseline and "no_answer_change" not in reasons:
        reasons.append("no_answer_change")

    return ReplacementQualification(
        qid=qid,
        baseline_answer=baseline,
        proposed_answer=proposed,
        effective_answer=effective_answer,
        runtime_final_state=runtime_final_state,
        record_error=record_error,
        answer_contract_valid=answer_contract_valid,
        source_safe=source_safe,
        truncation_safe=truncation_safe,
        typed_evidence_trusted=typed_evidence_trusted,
        per_option_verdicts=per_option,
        defined_option_labels=labels,
        added_options=added,
        removed_options=removed,
        added_option_support_complete=added_complete,
        removed_option_contradiction_complete=removed_complete,
        selected_option_support_complete=selected_complete,
        unselected_option_disposition_complete=unselected_complete,
        correction_reconciled=correction_reconciled,
        independent_oracle_status=oracle_status,
        independent_oracle_answer=oracle_answer,
        oracle_matches_proposal=oracle_matches,
        production_qualification_pass=production_pass,
        replacement_allowed=replacement_allowed,
        qualification_tier=tier,
        reasons=sorted(set(reasons)),
    )


def qualification_from_record(
    *,
    qid: str,
    baseline_answer: str,
    record: Mapping[str, Any] | None,
    proposed_answer: str | None = None,
    answer_format: str | None = None,
    option_texts: Mapping[str, str] | None = None,
    independent_oracle: Mapping[str, Any] | None = None,
    trusted_declared_documents: Mapping[str, TrustedDocumentSource | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return qualify_replacement(
        qid=qid,
        baseline_answer=baseline_answer,
        proposed_answer=_saved_proposal(record, proposed_answer),
        record=record,
        answer_format=answer_format,
        option_texts=option_texts,
        independent_oracle=independent_oracle,
        trusted_declared_documents=trusted_declared_documents,
    ).to_dict()
