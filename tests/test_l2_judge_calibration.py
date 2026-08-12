from __future__ import annotations

import json

import pytest

from evaluation.contracts import EvaluationCase, EvaluationObservation
from evaluation.l2_judge_calibration import (
    CANNOT_ASSESS,
    CORRECT,
    INCORRECT,
    REAL_CALIBRATION,
    TRUST_TEST,
    evaluate_l2_judge_calibration,
)

MODULE = "l2_judge"


def out(decision: str) -> dict[str, object]:
    return {
        "decision": decision,
        "reason_code": "fixture",
        "short_rationale": "fixture",
        "required_gold_elements_covered": True,
        "contradiction_found": False,
        "uncertainty_reason": "uncertain" if decision == CANNOT_ASSESS else "",
    }


def case(case_id: str, expected: object, stratum: str = REAL_CALIBRATION) -> EvaluationCase:
    return EvaluationCase(case_id=case_id, module_id=MODULE, expected=expected, slice=stratum)


def obs(case_id: str, decision: str, module: str = MODULE) -> EvaluationObservation:
    return EvaluationObservation(module_id=module, case_id=case_id, output=out(decision))


def test_frozen_metric_arithmetic_and_strata_separation() -> None:
    cases = [
        case("r1", CORRECT), case("r2", INCORRECT),
        case("r3", CORRECT), case("r4", INCORRECT),
        case("t1", INCORRECT, TRUST_TEST), case("t2", CANNOT_ASSESS, TRUST_TEST),
    ]
    observations = [
        obs("r1", CORRECT), obs("r2", CORRECT),
        obs("r3", INCORRECT), obs("r4", CANNOT_ASSESS),
        obs("t1", INCORRECT), obs("t2", CANNOT_ASSESS),
    ]
    summary = evaluate_l2_judge_calibration(cases, observations)
    assert summary.real_total == 4
    assert summary.judge_decided == 3
    assert summary.judge_abstain == 1
    assert summary.judge_coverage == 0.75
    assert summary.agreement == 0.25
    assert summary.false_accept == 1
    assert summary.false_reject == 1
    assert summary.cannot_assess == 1
    assert summary.unresolved_rate == 0.25
    assert summary.trust_total == 2
    assert summary.trust_pass == 2
    assert summary.trust_fail == 0
    assert summary.trust_abstain == 1


def test_summary_is_deterministic() -> None:
    cases = [case("r", CORRECT), case("t", INCORRECT, TRUST_TEST)]
    observations = [obs("r", CORRECT), obs("t", INCORRECT)]
    first = evaluate_l2_judge_calibration(cases, observations).to_dict()
    second = evaluate_l2_judge_calibration(cases, observations).to_dict()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_fail_closed_empty_case_id_uses_existing_contract() -> None:
    with pytest.raises(ValueError):
        EvaluationCase(case_id="", module_id=MODULE, expected=CORRECT, slice=REAL_CALIBRATION)


def test_fail_closed_invalid_label_and_missing_audit_field() -> None:
    base = case("x", CORRECT)
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([base], [obs("x", "MAYBE")])

    broken = out(CORRECT)
    broken.pop("reason_code")
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration(
            [base], [EvaluationObservation(module_id=MODULE, case_id="x", output=broken)]
        )


def test_fail_closed_invalid_stratum_or_reference() -> None:
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([case("x", CORRECT, "OTHER")], [obs("x", CORRECT)])
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([case("x", None)], [obs("x", CORRECT)])
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([case("x", CANNOT_ASSESS)], [obs("x", CORRECT)])


def test_fail_closed_missing_extra_duplicate_or_module_mismatch() -> None:
    base = case("x", CORRECT)
    observation = obs("x", CORRECT)
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([base], [])
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([], [observation])
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([base, base], [observation])
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([base], [observation, observation])
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration([base], [obs("x", CORRECT, "other")])


def test_fail_closed_malformed_audit_values() -> None:
    base = case("x", CORRECT)
    missing_uncertainty = out(CANNOT_ASSESS)
    missing_uncertainty["uncertainty_reason"] = ""
    with pytest.raises(ValueError):
        evaluate_l2_judge_calibration(
            [base],
            [EvaluationObservation(module_id=MODULE, case_id="x", output=missing_uncertainty)],
        )

    with pytest.raises(TypeError):
        evaluate_l2_judge_calibration(
            [base], [EvaluationObservation(module_id=MODULE, case_id="x", output="CORRECT")]
        )
