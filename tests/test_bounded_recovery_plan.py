"""Focused offline tests for BB-P0-14C bounded recovery plans."""

from __future__ import annotations

import importlib.util

from pathlib import Path

import pytest

from verification.composite_failure_arbitrator import arbitrate_failures

from verification.failure_taxonomy import FailureClass

from verification.recovery_orchestrator import build_bounded_recovery_plan

from verification.recovery_policy import RecoveryAction

def _plan(signal: dict, *, context: dict | None = None):
    decision = arbitrate_failures([signal], context=context)
    return build_bounded_recovery_plan(decision)

def test_missing_evidence_allows_one_corrective_retrieval_step():
    plan = _plan({"missing_evidence": True, "evidence_available": False})
    assert plan.primary_failure.failure_class is FailureClass.MISSING_EVIDENCE
    assert plan.recommended_action is RecoveryAction.CORRECTIVE_RETRIEVAL
    assert plan.max_recovery_steps == 1
    assert plan.corrective_retrieval_allowed is True
    assert plan.provider_retry_allowed is False
    assert plan.execution_authorized is False
    assert plan.terminal_stop is False

def test_lineage_lost_rebinds_without_retrieval():
    plan = _plan({"lineage_lost": True, "evidence_refs": ["DOC:1"]})
    assert plan.recommended_action is RecoveryAction.REBIND_LINEAGE
    assert plan.max_recovery_steps == 1
    assert plan.corrective_retrieval_allowed is False

def test_binding_failed_reverifies_without_retrieval():
    plan = _plan({"binding_failed": True, "evidence_refs": ["doc:1"]})
    assert plan.recommended_action is RecoveryAction.REVERIFY_CLAIM
    assert plan.max_recovery_steps == 1
    assert plan.corrective_retrieval_allowed is False

def test_calculation_binding_failed_recomputes_deterministically():
    plan = _plan({"calculation_binding_failed": True})
    assert plan.recommended_action is RecoveryAction.RECOMPUTE_DETERMINISTIC
    assert plan.max_recovery_steps == 1
    assert plan.provider_retry_allowed is False

@pytest.mark.parametrize("signal", [
    {"model_output_invalid": True},
    {"provider_status": "COMPLETED", "usage_positive": True, "visible_output": ""},
])
def test_output_failures_reparse_but_never_provider_retry(signal):
    plan = _plan(signal)
    assert plan.recommended_action is RecoveryAction.REPARSE_VISIBLE_OUTPUT
    assert plan.max_recovery_steps == 1
    assert plan.requires_evaluator is True
    assert plan.provider_retry_allowed is False
    assert plan.execution_authorized is False

def test_provider_primary_with_binding_secondary_stops_and_does_not_reverify():
    decision = arbitrate_failures([
        {"provider_error": True, "http_status": 524},
        {"binding_failed": True, "evidence_refs": ["doc:1"]},
    ])
    plan = build_bounded_recovery_plan(decision)
    assert plan.primary_failure.failure_class is FailureClass.PROVIDER_ERROR
    assert FailureClass.BINDING_FAILED in {item.failure_class for item in plan.secondary_failures}
    assert plan.recommended_action is RecoveryAction.STOP_PROVIDER_ERROR
    assert plan.max_recovery_steps == 0
    assert plan.terminal_stop is True
    assert plan.provider_retry_allowed is False
    assert plan.execution_authorized is False

def test_budget_blocked_stops():
    plan = _plan({"budget_blocked": True})
    assert plan.recommended_action is RecoveryAction.STOP_BUDGET
    assert plan.max_recovery_steps == 0
    assert plan.terminal_stop is True

def test_runtime_integrity_failed_stops():
    plan = _plan({"runtime_integrity_failed": True})
    assert plan.recommended_action is RecoveryAction.STOP_INTEGRITY
    assert plan.max_recovery_steps == 0
    assert plan.terminal_stop is True

def test_unknown_failure_escalates_fail_closed():
    plan = _plan({"reason": "unclassified structured failure"})
    assert plan.primary_failure.failure_class is FailureClass.UNKNOWN_FAILURE
    assert plan.recommended_action is RecoveryAction.ESCALATE_EVALUATOR
    assert plan.max_recovery_steps == 0
    assert plan.requires_evaluator is True
    assert plan.terminal_stop is True
    assert plan.corrective_retrieval_allowed is False

def test_binding_primary_contract_secondary_keeps_reverify_as_first_action():
    decision = arbitrate_failures([
        {"binding_failed": True, "answer_contract_valid": False, "evidence_refs": ["doc:1"]}
    ])
    plan = build_bounded_recovery_plan(decision)
    assert plan.primary_failure.failure_class is FailureClass.BINDING_FAILED
    assert FailureClass.ANSWER_CONTRACT_FAILED in {item.failure_class for item in plan.secondary_failures}
    assert plan.recommended_action is RecoveryAction.REVERIFY_CLAIM
    assert plan.corrective_retrieval_allowed is False

def test_missing_primary_binding_secondary_allows_retrieval_only_after_p14a_selection():
    decision = arbitrate_failures(
        [{"missing_evidence": True}, {"binding_failed": True}],
        context={"evidence_available": False, "evidence_count": 0},
    )
    plan = build_bounded_recovery_plan(decision)
    assert plan.primary_failure.failure_class is FailureClass.MISSING_EVIDENCE
    assert FailureClass.BINDING_FAILED in {item.failure_class for item in plan.secondary_failures}
    assert plan.recommended_action is RecoveryAction.CORRECTIVE_RETRIEVAL
    assert plan.corrective_retrieval_allowed is True

def test_binding_primary_missing_secondary_forbids_retrieval():
    decision = arbitrate_failures(
        [{"missing_evidence": True}, {"binding_failed": True}],
        context={
            "evidence_available": True,
            "evidence_count": 2,
            "raw_evidence_refs": ["DOC:1"],
            "binding_auditable": False,
        },
    )
    plan = build_bounded_recovery_plan(decision)
    assert plan.primary_failure.failure_class is FailureClass.BINDING_FAILED
    assert plan.recommended_action is RecoveryAction.REVERIFY_CLAIM
    assert plan.corrective_retrieval_allowed is False

def test_ambiguous_missing_binding_escalates_without_retrieval():
    decision = arbitrate_failures(
        [{"missing_evidence": True}, {"binding_failed": True}],
        context={},
    )
    plan = build_bounded_recovery_plan(decision)
    assert plan.primary_failure.failure_class is FailureClass.UNKNOWN_FAILURE
    assert plan.recommended_action is RecoveryAction.ESCALATE_EVALUATOR
    assert plan.corrective_retrieval_allowed is False
    assert plan.terminal_stop is True

def test_same_composite_decision_replays_to_identical_plan():
    decision = arbitrate_failures([
        {"binding_failed": True, "answer_contract_valid": False, "evidence_refs": ["doc:1"]}
    ])
    first = build_bounded_recovery_plan(decision).to_dict()
    second = build_bounded_recovery_plan(decision).to_dict()
    assert first == second

def test_context_cannot_override_primary_or_authorize_execution():
    decision = arbitrate_failures([{"binding_failed": True}])
    plan = build_bounded_recovery_plan(
        decision,
        context={
            "force_action": "CORRECTIVE_RETRIEVAL",
            "execution_authorized": True,
            "provider_retry_allowed": True,
        },
    )
    assert plan.recommended_action is RecoveryAction.REVERIFY_CLAIM
    assert plan.corrective_retrieval_allowed is False
    assert plan.execution_authorized is False
    assert plan.provider_retry_allowed is False

def test_direct_primary_secondary_contract_supported():
    decision = arbitrate_failures([
        {"binding_failed": True},
        {"answer_contract_failed": True},
    ])
    plan = build_bounded_recovery_plan(
        primary_failure=decision.primary_failure,
        secondary_failures=decision.secondary_failures,
        context={"trace_id": "offline-only"},
    )
    assert plan.recommended_action is RecoveryAction.REVERIFY_CLAIM
    assert len(plan.secondary_failures) == 1

def test_decision_and_primary_cannot_both_be_supplied():
    decision = arbitrate_failures([{"binding_failed": True}])
    with pytest.raises(ValueError):
        build_bounded_recovery_plan(decision, primary_failure=decision.primary_failure)

def test_missing_primary_input_is_rejected():
    with pytest.raises(ValueError):
        build_bounded_recovery_plan()

def test_answer_contract_primary_is_bounded_stop():
    plan = _plan({"answer_contract_failed": True})
    assert plan.recommended_action is RecoveryAction.STOP_CONTRACT_FAILURE
    assert plan.max_recovery_steps == 0
    assert plan.terminal_stop is True
    assert plan.execution_authorized is False
