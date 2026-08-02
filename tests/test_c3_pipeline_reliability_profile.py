"""Offline reliability attacks for the real ExplicitC3Pipeline path."""
from __future__ import annotations

from dataclasses import replace
from itertools import combinations

import pytest

import evaluation.oracles.c3_pipeline as oracle_module
from evaluation.c3_pipeline_profile import (
    C3_PIPELINE_MODULE_ID,
    C3_PIPELINE_REQUIRED_INVARIANTS,
    adapt_c3_pipeline_evaluation,
    build_c3_pipeline_reliability_profile,
    build_erroneous_allow_sentinel,
    run_c3_pipeline_case,
    run_c3_pipeline_rows,
)
from evaluation.contracts import EvaluationResult, GateStatus
from evaluation.gates import ReliabilityGate
from evaluation.oracles.c3_pipeline import (
    C3PipelineDecisionRow,
    C3PipelineExpectation,
    C3PipelineFactors,
    C3PipelineObservation,
    C3_PIPELINE_DECISION_TABLE_V1,
    C3_PIPELINE_PAIRWISE_CASES_V1,
    C3_PIPELINE_SELECTED_3WAY_CASES_V1,
    all_valid_c3_pipeline_factors,
    evaluate_c3_pipeline_factors,
)


_FACTOR_NAMES = (
    "candidate_membership",
    "material_formula_state",
    "semantic_binding_state",
    "question_formula_match",
    "unrelated_bundle_candidate",
)


def _gate(*results: EvaluationResult) -> GateStatus:
    return ReliabilityGate().evaluate(
        results,
        build_c3_pipeline_reliability_profile(),
    ).status


def _pair_universe(factors_rows) -> set[tuple[str, str, str, str]]:
    pairs: set[tuple[str, str, str, str]] = set()
    for factors in factors_rows:
        for left, right in combinations(_FACTOR_NAMES, 2):
            pairs.add(
                (
                    left,
                    str(getattr(factors, left)),
                    right,
                    str(getattr(factors, right)),
                )
            )
    return pairs


def test_profile_declares_critical_invariants_and_only_implemented_techniques() -> None:
    profile = build_c3_pipeline_reliability_profile()

    assert profile.module_id == C3_PIPELINE_MODULE_ID
    assert profile.risk_level == "CRITICAL"
    assert set(profile.required_invariants) == set(C3_PIPELINE_REQUIRED_INVARIANTS)
    assert set(profile.required_invariants) >= {
        "candidate_scope_exactly_once_must_hold",
        "ambiguous_or_incomplete_assembly_must_not_execute",
        "non_pass_question_match_must_not_execute",
        "blocked_path_must_not_call_legacy_or_provider",
        "successful_result_must_preserve_trace_and_lineage",
        "unrelated_bundle_evidence_must_not_change_decision",
    }
    assert set(profile.test_techniques) == {
        "explicit_decision_table",
        "constrained_pairwise_covering_set",
        "selected_3way_high_risk_cases",
        "metamorphic_unrelated_evidence",
        "mutation_style_erroneous_allow_sentinel",
    }
    assert "property_based" not in profile.test_techniques
    assert "generic_mutation_runner" not in profile.test_techniques


def test_oracle_has_no_production_pipeline_dependencies() -> None:
    assert not hasattr(oracle_module, "ExplicitC3Pipeline")
    assert not hasattr(oracle_module, "C3InputAssembler")
    assert not hasattr(oracle_module, "CalculationSolver")


def test_oracle_permits_only_the_single_safe_state() -> None:
    rows = all_valid_c3_pipeline_factors()
    permitted = [row for row in rows if evaluate_c3_pipeline_factors(row).should_execute]

    assert len(rows) == 108
    assert permitted == [
        C3PipelineFactors("exactly_one", "one", "complete", "pass", "absent"),
        C3PipelineFactors("exactly_one", "one", "complete", "pass", "present"),
    ]


def test_invalid_factor_values_and_inconsistent_rows_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported candidate_membership"):
        C3PipelineFactors("one", "one", "complete", "pass", "absent")

    safe = C3PipelineFactors("exactly_one", "one", "complete", "pass", "absent")
    with pytest.raises(ValueError, match="disagrees with independent oracle"):
        C3PipelineDecisionRow(
            "invalid-expectation",
            safe,
            C3PipelineExpectation(False, False, False),
        )


def test_decision_table_drives_real_pipeline_and_matches_oracle() -> None:
    runs = run_c3_pipeline_rows(C3_PIPELINE_DECISION_TABLE_V1)

    assert len(runs) == 9
    assert _gate(*(run.evaluation for run in runs)) is GateStatus.PASS
    for row, run in zip(C3_PIPELINE_DECISION_TABLE_V1, runs, strict=True):
        assert run.observation.executed is row.expected.should_execute, row.case_id
        assert run.observation.legacy_call_count == 0, row.case_id
        assert run.observation.provider_call_count == 0, row.case_id
        if row.expected.should_execute:
            assert run.result.answer == "5", row.case_id
            assert run.observation.trace, row.case_id
            assert run.observation.lineage, row.case_id
        else:
            assert run.result.answer == "", row.case_id


def test_pairwise_set_covers_every_factor_value_pair_without_cartesian_execution() -> None:
    valid = all_valid_c3_pipeline_factors()
    selected = tuple(row.factors for row in C3_PIPELINE_PAIRWISE_CASES_V1)

    assert _pair_universe(selected) >= _pair_universe(valid)
    assert len(selected) < len(valid) / 4

    runs = run_c3_pipeline_rows(C3_PIPELINE_PAIRWISE_CASES_V1)
    assert _gate(*(run.evaluation for run in runs)) is GateStatus.PASS
    assert all(run.observation.executed is row.expected.should_execute for row, run in zip(C3_PIPELINE_PAIRWISE_CASES_V1, runs, strict=True))
    assert all(run.observation.legacy_call_count == 0 for run in runs)
    assert all(run.observation.provider_call_count == 0 for run in runs)


def test_selected_3way_high_risk_rows_drive_real_pipeline() -> None:
    runs = run_c3_pipeline_rows(C3_PIPELINE_SELECTED_3WAY_CASES_V1)

    assert len(runs) == 4
    assert all(not row.expected.should_execute for row in C3_PIPELINE_SELECTED_3WAY_CASES_V1)
    assert all(not run.observation.executed for run in runs)
    assert all(run.observation.legacy_call_count == 0 for run in runs)
    assert all(run.observation.provider_call_count == 0 for run in runs)
    assert _gate(*(run.evaluation for run in runs)) is GateStatus.PASS


def test_unrelated_bundle_candidate_is_metamorphically_inert() -> None:
    baseline_row = next(
        row for row in C3_PIPELINE_DECISION_TABLE_V1 if row.case_id == "safe_single_candidate"
    )
    mutated_row = next(
        row
        for row in C3_PIPELINE_DECISION_TABLE_V1
        if row.case_id == "safe_with_unrelated_candidate"
    )
    baseline = run_c3_pipeline_case(baseline_row)
    mutated = run_c3_pipeline_case(
        mutated_row,
        baseline_observation=baseline.observation,
    )

    assert baseline.observation.behavior_fingerprint() == mutated.observation.behavior_fingerprint()
    assert baseline.result.answer == mutated.result.answer == "5"
    assert mutated.evaluation.metric(
        "unrelated_bundle_evidence_must_not_change_decision"
    ).passed is True
    assert _gate(baseline.evaluation, mutated.evaluation) is GateStatus.PASS


def test_erroneous_allow_mutation_sentinel_forces_gate_fail() -> None:
    sentinel = build_erroneous_allow_sentinel()

    assert sentinel.metric("candidate_scope_exactly_once_must_hold").passed is False
    assert sentinel.metric("independent_oracle_decision_must_match").passed is False
    assert _gate(sentinel) is GateStatus.FAIL


def test_missing_required_invariant_forces_gate_fail() -> None:
    safe_run = run_c3_pipeline_case(C3_PIPELINE_DECISION_TABLE_V1[0])
    stripped = replace(
        safe_run.evaluation,
        metrics=tuple(
            metric
            for metric in safe_run.evaluation.metrics
            if metric.metric_name != "candidate_scope_exactly_once_must_hold"
        ),
    )

    assert _gate(stripped) is GateStatus.FAIL


def test_forged_safe_result_without_trace_or_lineage_forces_gate_fail() -> None:
    factors = C3PipelineFactors("exactly_one", "one", "complete", "pass", "absent")
    forged = C3PipelineObservation(executed=True, answer="5")
    evaluation = adapt_c3_pipeline_evaluation(
        case_id="forged-missing-lineage",
        factors=factors,
        observation=forged,
    )

    assert evaluation.metric(
        "successful_result_must_preserve_trace_and_lineage"
    ).passed is False
    assert _gate(evaluation) is GateStatus.FAIL


def test_c3_pipeline_reliability_evidence_manifest() -> None:
    """Emit bounded counts for the executor evidence log when run with ``-s``."""

    decision_runs = run_c3_pipeline_rows(C3_PIPELINE_DECISION_TABLE_V1)
    pairwise_runs = run_c3_pipeline_rows(C3_PIPELINE_PAIRWISE_CASES_V1)
    three_way_runs = run_c3_pipeline_rows(C3_PIPELINE_SELECTED_3WAY_CASES_V1)
    all_runs = (*decision_runs, *pairwise_runs, *three_way_runs)
    safe_count = sum(run.observation.executed for run in all_runs)
    blocked_count = len(all_runs) - safe_count
    legacy_calls = sum(run.observation.legacy_call_count for run in all_runs)
    provider_calls = sum(run.observation.provider_call_count for run in all_runs)

    print(
        "C3I_EVIDENCE",
        f"decision_table={len(decision_runs)}",
        f"pairwise={len(pairwise_runs)}",
        f"selected_3way={len(three_way_runs)}",
        f"real_pipeline_runs={len(all_runs)}",
        f"executed={safe_count}",
        f"blocked={blocked_count}",
        f"legacy_calls={legacy_calls}",
        f"provider_calls={provider_calls}",
        f"aggregate_gate={_gate(*(run.evaluation for run in all_runs)).value}",
        f"mutation_gate={_gate(build_erroneous_allow_sentinel()).value}",
    )

    assert legacy_calls == 0
    assert provider_calls == 0
