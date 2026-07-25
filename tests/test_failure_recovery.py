"""Focused offline tests for BB-P0-12 failure taxonomy and recovery policy."""

from __future__ import annotations

import importlib.util

from pathlib import Path

import pytest

from verification.failure_taxonomy import FailureClass, classify_failure_signal, taxonomy_matrix

from verification.recovery_policy import RecoveryAction, classify_and_route, policy_matrix, route_recovery

@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ({"missing_evidence": True}, FailureClass.MISSING_EVIDENCE),
        (
            {
                "solver_raw_used_doc_ids": ["DOC:1"],
                "used_doc_ids": [],
                "used_docs_source": "unknown",
                "binding_auditable": False,
            },
            FailureClass.LINEAGE_LOST,
        ),
        ({"binding_failed": True, "evidence_refs": ["doc-1"]}, FailureClass.BINDING_FAILED),
        ({"formula_execution_failed": True}, FailureClass.CALCULATION_BINDING_FAILED),
        ({"model_output_invalid": True, "visible_output": "{bad json"}, FailureClass.MODEL_OUTPUT_INVALID),
        (
            {"provider_status": "COMPLETED", "usage_positive": True, "visible_output": ""},
            FailureClass.EMPTY_VISIBLE_OUTPUT,
        ),
        ({"provider_error": True, "http_status": 524}, FailureClass.PROVIDER_ERROR),
        ({"answer_contract_failed": True}, FailureClass.ANSWER_CONTRACT_FAILED),
        ({"budget_blocked": True}, FailureClass.BUDGET_BLOCKED),
        ({"runtime_integrity_failed": True}, FailureClass.RUNTIME_INTEGRITY_FAILED),
        ({"reason": "unrecognized failure shape"}, FailureClass.UNKNOWN_FAILURE),
    ],
)
def test_each_failure_class_is_reachable(signal, expected):
    assert classify_failure_signal(signal).failure_class is expected

def test_failure_record_contains_required_audit_fields():
    record = classify_failure_signal(
        {
            "stage": "verification",
            "binding_failed": True,
            "evidence_refs": ["doc-a", "doc-b"],
            "reason": "claim did not bind",
        }
    ).to_dict()
    for field in (
        "failure_class",
        "stage",
        "retryable",
        "retrieval_related",
        "provider_related",
        "safety_severity",
        "evidence_refs",
        "reason",
    ):
        assert field in record
    assert record["evidence_refs"] == ["doc-a", "doc-b"]

def test_lineage_loss_outranks_generic_missing_evidence_and_does_not_retrieve():
    failure, decision = classify_and_route(
        {
            "missing_evidence": True,
            "solver_raw_used_doc_ids": ["DOC:1", "DOC:2"],
            "used_doc_ids": [],
            "used_docs_source": "unknown",
        }
    )
    assert failure.failure_class is FailureClass.LINEAGE_LOST
    assert decision.action is RecoveryAction.REBIND_LINEAGE
    assert decision.corrective_retrieval_allowed is False

def test_empty_visible_output_outranks_missing_evidence():
    failure, decision = classify_and_route(
        {
            "provider_status": "COMPLETED",
            "usage_positive": True,
            "visible_output": "",
            "missing_evidence": True,
        }
    )
    assert failure.failure_class is FailureClass.EMPTY_VISIBLE_OUTPUT
    assert decision.action is RecoveryAction.REPARSE_VISIBLE_OUTPUT
    assert decision.corrective_retrieval_allowed is False

def test_provider_error_outranks_empty_output_and_contract_failure():
    failure, decision = classify_and_route(
        {
            "provider_error": True,
            "http_status": 524,
            "provider_status": "ERROR",
            "visible_output": "",
            "answer_contract_valid": False,
        }
    )
    assert failure.failure_class is FailureClass.PROVIDER_ERROR
    assert decision.action is RecoveryAction.STOP_PROVIDER_ERROR
    assert decision.provider_retry_allowed is False
    assert decision.execution_authorized is False

def test_calculation_failure_prefers_deterministic_recompute():
    failure, decision = classify_and_route(
        {
            "formula_execution_valid": False,
            "binding_auditable": False,
            "answer_contract_valid": False,
        }
    )
    assert failure.failure_class is FailureClass.CALCULATION_BINDING_FAILED
    assert decision.action is RecoveryAction.RECOMPUTE_DETERMINISTIC
    assert decision.corrective_retrieval_allowed is False

@pytest.mark.parametrize(
    ("failure_class", "expected_action"),
    [
        (FailureClass.MISSING_EVIDENCE, RecoveryAction.CORRECTIVE_RETRIEVAL),
        (FailureClass.LINEAGE_LOST, RecoveryAction.REBIND_LINEAGE),
        (FailureClass.BINDING_FAILED, RecoveryAction.REVERIFY_CLAIM),
        (FailureClass.CALCULATION_BINDING_FAILED, RecoveryAction.RECOMPUTE_DETERMINISTIC),
        (FailureClass.MODEL_OUTPUT_INVALID, RecoveryAction.REPARSE_VISIBLE_OUTPUT),
        (FailureClass.EMPTY_VISIBLE_OUTPUT, RecoveryAction.REPARSE_VISIBLE_OUTPUT),
        (FailureClass.PROVIDER_ERROR, RecoveryAction.STOP_PROVIDER_ERROR),
        (FailureClass.ANSWER_CONTRACT_FAILED, RecoveryAction.STOP_CONTRACT_FAILURE),
        (FailureClass.BUDGET_BLOCKED, RecoveryAction.STOP_BUDGET),
        (FailureClass.RUNTIME_INTEGRITY_FAILED, RecoveryAction.STOP_INTEGRITY),
        (FailureClass.UNKNOWN_FAILURE, RecoveryAction.ESCALATE_EVALUATOR),
    ],
)
def test_deterministic_failure_to_action_mapping(failure_class, expected_action):
    first = route_recovery(failure_class)
    second = route_recovery(failure_class)
    assert first == second
    assert first.action is expected_action
    assert first.execution_authorized is False
    assert first.provider_retry_allowed is False

def test_only_missing_evidence_can_recommend_corrective_retrieval():
    for row in policy_matrix():
        is_retrieval = row["action"] == RecoveryAction.CORRECTIVE_RETRIEVAL.value
        assert is_retrieval == (row["failure_class"] == FailureClass.MISSING_EVIDENCE.value)

def test_budget_and_runtime_integrity_fail_closed():
    for failure_class in (FailureClass.BUDGET_BLOCKED, FailureClass.RUNTIME_INTEGRITY_FAILED):
        decision = route_recovery(failure_class)
        assert decision.fail_closed is True
        assert decision.execution_authorized is False
        assert decision.corrective_retrieval_allowed is False

def test_unknown_failure_escalates_instead_of_guessing():
    decision = route_recovery(FailureClass.UNKNOWN_FAILURE)
    assert decision.action is RecoveryAction.ESCALATE_EVALUATOR
    assert decision.fail_closed is True

def test_no_failure_returns_no_action_without_execution():
    decision = route_recovery(None)
    assert decision.action is RecoveryAction.NO_ACTION
    assert decision.execution_authorized is False
    assert decision.provider_retry_allowed is False

def test_taxonomy_and_policy_cover_same_failure_classes():
    taxonomy_classes = {row["failure_class"] for row in taxonomy_matrix()}
    policy_classes = {row["failure_class"] for row in policy_matrix()}
    assert taxonomy_classes == {item.value for item in FailureClass}
    assert policy_classes == taxonomy_classes
