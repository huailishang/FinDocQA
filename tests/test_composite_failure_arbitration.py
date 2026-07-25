"""Focused offline tests for BB-P0-14A composite failure arbitration."""

from __future__ import annotations

import importlib.util

from pathlib import Path

import pytest

from verification.composite_failure_arbitrator import arbitrate_failures

from verification.failure_taxonomy import FailureClass, FailureRecord, observe_failure_records

def _classes(decision) -> tuple[FailureClass, tuple[FailureClass, ...]]:
    return (
        decision.primary_failure.failure_class,
        tuple(record.failure_class for record in decision.secondary_failures),
    )

@pytest.mark.parametrize(
    ("signal", "primary", "secondary"),
    [
        (
            {"binding_failed": True, "answer_contract_valid": False, "evidence_refs": ["doc:1"]},
            FailureClass.BINDING_FAILED,
            FailureClass.ANSWER_CONTRACT_FAILED,
        ),
        (
            {"lineage_lost": True, "answer_contract_valid": False, "evidence_refs": ["DOC:1"]},
            FailureClass.LINEAGE_LOST,
            FailureClass.ANSWER_CONTRACT_FAILED,
        ),
        (
            {"calculation_binding_failed": True, "answer_contract_valid": False},
            FailureClass.CALCULATION_BINDING_FAILED,
            FailureClass.ANSWER_CONTRACT_FAILED,
        ),
        (
            {
                "provider_status": "COMPLETED",
                "usage_positive": True,
                "visible_output": "",
                "answer_contract_valid": False,
            },
            FailureClass.EMPTY_VISIBLE_OUTPUT,
            FailureClass.ANSWER_CONTRACT_FAILED,
        ),
        (
            {"missing_evidence": True, "answer_contract_valid": False},
            FailureClass.MISSING_EVIDENCE,
            FailureClass.ANSWER_CONTRACT_FAILED,
        ),
    ],
)
def test_upstream_root_cause_outranks_answer_contract(signal, primary, secondary):
    decision = arbitrate_failures([signal])
    actual_primary, secondaries = _classes(decision)
    assert actual_primary is primary
    assert secondary in secondaries
    assert decision.recovery_execution_authorized is False
    assert decision.provider_calls == 0

def test_observer_preserves_multiple_failures_from_one_structured_signal():
    records = observe_failure_records(
        {
            "binding_failed": True,
            "answer_contract_valid": False,
            "missing_evidence": True,
            "evidence_count": 0,
        }
    )
    classes = {record.failure_class for record in records}
    assert classes == {
        FailureClass.BINDING_FAILED,
        FailureClass.MISSING_EVIDENCE,
        FailureClass.ANSWER_CONTRACT_FAILED,
    }

def test_provider_plus_binding_is_terminal_provider_primary_and_keeps_binding():
    decision = arbitrate_failures([
        {"provider_error": True, "http_status": 524},
        {"binding_failed": True, "evidence_refs": ["doc:1"]},
    ])
    primary, secondaries = _classes(decision)
    assert primary is FailureClass.PROVIDER_ERROR
    assert FailureClass.BINDING_FAILED in secondaries
    assert decision.terminal_stop is True
    assert decision.recovery_execution_authorized is False

def test_budget_plus_missing_evidence_is_terminal_budget_primary():
    decision = arbitrate_failures([
        {"budget_blocked": True},
        {"missing_evidence": True, "evidence_available": False},
    ])
    primary, secondaries = _classes(decision)
    assert primary is FailureClass.BUDGET_BLOCKED
    assert FailureClass.MISSING_EVIDENCE in secondaries
    assert decision.terminal_stop is True

def test_integrity_outranks_any_secondary_and_fails_closed():
    decision = arbitrate_failures([
        {"runtime_integrity_failed": True},
        {"provider_error": True, "http_status": 503},
        {"binding_failed": True},
        {"answer_contract_failed": True},
    ])
    primary, secondaries = _classes(decision)
    assert primary is FailureClass.RUNTIME_INTEGRITY_FAILED
    assert set(secondaries) == {
        FailureClass.PROVIDER_ERROR,
        FailureClass.BINDING_FAILED,
        FailureClass.ANSWER_CONTRACT_FAILED,
    }
    assert decision.terminal_stop is True
    assert decision.evaluator_escalation_required is True

def test_missing_plus_binding_prefers_missing_when_structured_state_proves_zero_evidence():
    decision = arbitrate_failures(
        [{"missing_evidence": True}, {"binding_failed": True}],
        context={"evidence_available": False, "evidence_count": 0},
    )
    primary, secondaries = _classes(decision)
    assert primary is FailureClass.MISSING_EVIDENCE
    assert FailureClass.BINDING_FAILED in secondaries
    assert "no decisive evidence" in decision.arbitration_reason

def test_missing_plus_binding_prefers_binding_when_refs_prove_evidence_exists():
    decision = arbitrate_failures(
        [{"missing_evidence": True}, {"binding_failed": True}],
        context={
            "evidence_available": True,
            "evidence_count": 3,
            "raw_evidence_refs": ["DOC:1", "DOC:2"],
            "binding_auditable": False,
        },
    )
    primary, secondaries = _classes(decision)
    assert primary is FailureClass.BINDING_FAILED
    assert FailureClass.MISSING_EVIDENCE in secondaries
    assert "evidence exists" in decision.arbitration_reason

def test_missing_plus_binding_ambiguous_escalates_instead_of_guessing_retrieval():
    decision = arbitrate_failures(
        [{"missing_evidence": True}, {"binding_failed": True}],
        context={},
    )
    primary, secondaries = _classes(decision)
    assert primary is FailureClass.UNKNOWN_FAILURE
    assert set(secondaries) == {FailureClass.MISSING_EVIDENCE, FailureClass.BINDING_FAILED}
    assert decision.evaluator_escalation_required is True
    assert decision.terminal_stop is True
    assert decision.recovery_execution_authorized is False

def test_deterministic_replay_is_order_invariant_for_primary_and_failure_set():
    observations = [
        {"answer_contract_failed": True},
        {"binding_failed": True, "evidence_refs": ["doc:1"]},
        {"model_output_invalid": True},
    ]
    forward = arbitrate_failures(observations)
    reverse = arbitrate_failures(list(reversed(observations)))
    assert forward.primary_failure.failure_class is FailureClass.BINDING_FAILED
    assert reverse.primary_failure.failure_class is FailureClass.BINDING_FAILED
    assert {item.failure_class for item in forward.observed_failures} == {
        item.failure_class for item in reverse.observed_failures
    }

def test_accepts_failure_records_directly():
    records = observe_failure_records({"binding_failed": True, "answer_contract_failed": True})
    assert all(isinstance(record, FailureRecord) for record in records)
    decision = arbitrate_failures(list(records))
    assert decision.primary_failure.failure_class is FailureClass.BINDING_FAILED
    assert FailureClass.ANSWER_CONTRACT_FAILED in {
        item.failure_class for item in decision.secondary_failures
    }

def test_empty_observation_escalates_unknown_without_recovery_execution():
    decision = arbitrate_failures([])
    assert decision.primary_failure.failure_class is FailureClass.UNKNOWN_FAILURE
    assert decision.evaluator_escalation_required is True
    assert decision.recovery_execution_authorized is False
    assert decision.provider_calls == 0

def test_invalid_observation_type_is_rejected():
    with pytest.raises(TypeError):
        arbitrate_failures(["BINDING_FAILED"])  # type: ignore[list-item]
