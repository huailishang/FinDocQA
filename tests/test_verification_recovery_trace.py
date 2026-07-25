from __future__ import annotations

import pytest

from verification.atom_evidence_verifier import AtomVerdict, REFUTE, SUPPORT, UNRESOLVED
from verification.claim_atoms import ClaimAtom
from verification.verification_recovery_shadow_trace import build_atom_shadow_trace


def atom(*, scope_confidence: str = "HIGH") -> ClaimAtom:
    return ClaimAtom(
        atom_id="a1",
        subject="公司",
        object_or_metric="净利润",
        time_scope="2025年",
        value="100",
        unit="万元",
        relation="=",
        polarity="positive",
        condition="",
        exception="",
        quantifier="",
        source_text="公司2025年净利润为100万元",
        atom_text="公司2025年净利润为100万元",
        scope_confidence=scope_confidence,
    )


def verdict(kind: str, reasons=(), refs=("doc1/page_1.md",), *, auditable: bool = False) -> AtomVerdict:
    return AtomVerdict(
        atom_id="a1",
        verdict=kind,
        reason_codes=tuple(reasons),
        evidence_refs=tuple(refs),
        bound_doc_id="doc1" if auditable else "",
        bound_page="1" if auditable else "",
        bound_source="doc1/page_1.md" if auditable else "",
        matched_span="净利润为100万元" if auditable else "",
        binding_auditable=auditable,
    )


def classes(trace) -> list[str]:
    return [row.failure_class.value for row in trace.all_observed_failures]


@pytest.mark.parametrize("kind", [SUPPORT, REFUTE])
def test_conclusive_verdict_is_complete_noop_trace(kind: str) -> None:
    trace = build_atom_shadow_trace(atom(), verdict(kind, auditable=True), evidence_count=1, used_doc_ids=("doc1",))
    assert trace.verifier_verdict == kind
    assert trace.failure_signals == ()
    assert trace.primary_failure is None
    assert trace.recommended_action == "NO_ACTION"
    assert trace.max_recovery_steps == 0
    assert trace.provider_retry_allowed is False
    assert trace.execution_authorized is False
    assert trace.provider_calls == 0
    assert trace.recovery_execution == 0


def test_unresolved_real_missing_is_only_retrieval_route() -> None:
    trace = build_atom_shadow_trace(
        atom(), verdict(UNRESOLVED, ("NO_EVIDENCE_CANDIDATES",), refs=()), evidence_count=0,
    )
    assert trace.failure_signals == ("MISSING_EVIDENCE",)
    assert trace.primary_failure.failure_class.value == "MISSING_EVIDENCE"
    assert trace.recommended_action == "CORRECTIVE_RETRIEVAL"
    assert trace.corrective_retrieval_allowed is True
    assert trace.max_recovery_steps == 1
    assert trace.terminal_stop is False


def test_unresolved_lineage_is_rebind_not_retrieval() -> None:
    trace = build_atom_shadow_trace(
        atom(), verdict(UNRESOLVED, ("LINEAGE_INCOMPLETE",)), evidence_count=1, raw_evidence_refs=("DOC:1",),
    )
    assert trace.failure_signals == ("LINEAGE_LOST",)
    assert trace.primary_failure.failure_class.value == "LINEAGE_LOST"
    assert trace.recommended_action == "REBIND_LINEAGE"
    assert trace.corrective_retrieval_allowed is False


def test_unresolved_binding_is_reverify_not_retrieval() -> None:
    trace = build_atom_shadow_trace(
        atom(), verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",)), evidence_count=1, used_doc_ids=("doc1",),
    )
    assert trace.failure_signals == ("BINDING_FAILED",)
    assert trace.primary_failure.failure_class.value == "BINDING_FAILED"
    assert trace.recommended_action == "REVERIFY_CLAIM"
    assert trace.corrective_retrieval_allowed is False


def test_unresolved_scope_unknown_fails_closed() -> None:
    trace = build_atom_shadow_trace(
        atom(scope_confidence="LOW"), verdict(UNRESOLVED, ("SCOPE_CONFIDENCE_LOW",)), evidence_count=1, used_doc_ids=("doc1",),
    )
    assert trace.failure_signals == ("UNKNOWN_FAILURE",)
    assert trace.primary_failure.failure_class.value == "UNKNOWN_FAILURE"
    assert trace.recommended_action == "ESCALATE_EVALUATOR"
    assert trace.terminal_stop is True
    assert trace.requires_evaluator is True


def test_binding_primary_beats_answer_contract_secondary() -> None:
    trace = build_atom_shadow_trace(
        atom(),
        verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",)),
        evidence_count=1,
        used_doc_ids=("doc1",),
        additional_observations=({"answer_contract_failed": True},),
    )
    assert trace.primary_failure.failure_class.value == "BINDING_FAILED"
    assert [row.failure_class.value for row in trace.secondary_failures] == ["ANSWER_CONTRACT_FAILED"]
    assert trace.recommended_action == "REVERIFY_CLAIM"
    assert trace.terminal_stop is False


def test_provider_primary_beats_binding_secondary_and_stops() -> None:
    trace = build_atom_shadow_trace(
        atom(),
        verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",)),
        evidence_count=1,
        used_doc_ids=("doc1",),
        additional_observations=({"provider_error": True, "http_status": 524, "raw_error": "HTTP 524"},),
    )
    assert trace.primary_failure.failure_class.value == "PROVIDER_ERROR"
    assert "BINDING_FAILED" in [row.failure_class.value for row in trace.secondary_failures]
    assert trace.recommended_action == "STOP_PROVIDER_ERROR"
    assert trace.terminal_stop is True
    assert trace.provider_retry_allowed is False


def test_calculation_binding_precedes_generic_binding() -> None:
    trace = build_atom_shadow_trace(
        atom(),
        verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",)),
        evidence_count=1,
        used_doc_ids=("doc1",),
        additional_observations=({"calculation_binding_failed": True},),
    )
    assert trace.primary_failure.failure_class.value == "CALCULATION_BINDING_FAILED"
    assert "BINDING_FAILED" in classes(trace)
    assert trace.recommended_action == "RECOMPUTE_DETERMINISTIC"
    assert trace.max_recovery_steps == 1


def test_empty_visible_output_precedes_unknown() -> None:
    trace = build_atom_shadow_trace(
        atom(scope_confidence="LOW"),
        verdict(UNRESOLVED, ("SCOPE_CONFIDENCE_LOW",)),
        evidence_count=1,
        used_doc_ids=("doc1",),
        additional_observations=({"empty_visible_output": True, "provider_status": "COMPLETED", "total_tokens": 10},),
    )
    assert trace.primary_failure.failure_class.value == "EMPTY_VISIBLE_OUTPUT"
    assert "UNKNOWN_FAILURE" in [row.failure_class.value for row in trace.secondary_failures]
    assert trace.recommended_action == "REPARSE_VISIBLE_OUTPUT"
    assert trace.corrective_retrieval_allowed is False


def test_trace_serialization_contains_full_required_chain() -> None:
    trace = build_atom_shadow_trace(
        atom(), verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",)), evidence_count=1, used_doc_ids=("doc1",), trace_id="fixture",
    )
    payload = trace.to_dict()
    required = {
        "verifier_verdict", "failure_signals", "all_observed_failures", "primary_failure", "secondary_failures",
        "arbitration_reason", "recommended_action", "max_recovery_steps", "requires_evaluator",
        "provider_retry_allowed", "corrective_retrieval_allowed", "execution_authorized", "terminal_stop", "stop_reason",
    }
    assert required <= payload.keys()
    assert payload["trace_id"] == "fixture"
    assert payload["provider_calls"] == 0
    assert payload["recovery_execution"] == 0


def test_only_missing_evidence_can_enable_corrective_retrieval() -> None:
    fixtures = [
        build_atom_shadow_trace(atom(), verdict(UNRESOLVED, ("NO_EVIDENCE_CANDIDATES",), refs=()), evidence_count=0),
        build_atom_shadow_trace(atom(), verdict(UNRESOLVED, ("LINEAGE_INCOMPLETE",)), evidence_count=1, raw_evidence_refs=("DOC:1",)),
        build_atom_shadow_trace(atom(), verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",)), evidence_count=1, used_doc_ids=("doc1",)),
        build_atom_shadow_trace(atom(scope_confidence="LOW"), verdict(UNRESOLVED, ("SCOPE_CONFIDENCE_LOW",)), evidence_count=1, used_doc_ids=("doc1",)),
    ]
    enabled = [row for row in fixtures if row.corrective_retrieval_allowed]
    assert len(enabled) == 1
    assert enabled[0].primary_failure.failure_class.value == "MISSING_EVIDENCE"
    assert all(row.provider_retry_allowed is False and row.execution_authorized is False for row in fixtures)
