from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest

from calculation import CalculationExecutionResult, SourceBoundNumericSeriesAggregator
from evidence.c3_numeric_series_binding import SourceBoundSumSeriesBinder
from scripts.evaluate_c3_source_bound_sum_series_binder import _bundle
from scripts.evaluate_c3_sum_normal_pipeline_activation import (
    BASELINE_PATH,
    BASELINE_SHA256,
    SCHEMA_VERSION,
    SCOPE_CAVEAT,
    _generic_freeform_bundle,
    build_report,
    validate_report,
)
from solvers.calculation import CalculationSolver


ROOT = Path(__file__).resolve().parents[1]


def test_factory_same_baseline_improves_from_zero_to_three() -> None:
    report = build_report()

    validate_report(report)
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["before"] == {
        "case_count": 3,
        "calculation_solver_entered_count": 3,
        "correct_deterministic_activation_count": 0,
        "binder_call_count": 0,
        "aggregator_call_count": 0,
    }
    assert report["after"] == {
        "case_count": 3,
        "calculation_solver_entered_count": 3,
        "correct_deterministic_activation_count": 3,
        "binder_call_count": 3,
        "aggregator_call_count": 3,
    }
    assert report["delta"]["correct_deterministic_activation_count"] == 3
    assert [row["answer"] for row in report["positive_records"]] == [
        "60",
        "4000",
        "25",
    ]
    assert all(
        row["answer_source"] == "c3_source_bound_sum_series"
        and row["routed_solver"] == "calculation"
        and row["correct_deterministic_activation"] is True
        and row["final_state"] == "accepted"
        for row in report["positive_records"]
    )


def test_positive_solver_results_are_fully_auditable() -> None:
    report = build_report()

    for record in report["positive_records"]:
        metadata = record["solver_metadata"]
        assert record["request_contract"] == (
            "SourceBoundNumericSeriesAggregationRequest"
        )
        assert record["source_lineage_complete"] is True
        assert len(record["source_refs"]) == 3
        assert record["binding_trace"]
        assert record["result_trace"]
        assert record["gate_status"] == "PASS"
        assert metadata["provider_call_count"] == 0
        assert metadata["prompt_tokens"] == 0
        assert metadata["completion_tokens"] == 0
        assert metadata["total_tokens"] == 0
        assert metadata["legacy_execution_invoked"] is False
        assert metadata["computation_status"] == "completed"
        assert metadata["computation_complete"] is True
        assert metadata["solver_lineage_source"] == (
            "c3_source_bound_sum_series"
        )
        assert metadata["submission_answers"] == [record["answer"]]
        assert all(
            str(ref["source"]).endswith("&table_index=0")
            and ref["doc_id"]
            for ref in record["source_refs"]
        )
        items = metadata["source_bound_sum_series_request"]["series"]["items"]
        assert len(items) == 3
        assert all(
            item["source_coordinate"].endswith("&column_index=1")
            for item in items
        )
        assert record["workflow_integrity"] == {
            "final_state": "accepted",
            "grounded": True,
            "solver_lineage_complete": True,
            "blocking_reasons": [],
        }


def test_all_frozen_binder_negatives_have_zero_false_activation() -> None:
    report = build_report()
    records = report["negative_guardrails"]

    assert len(records) == 33
    assert report["negative_guardrail_count"] == 33
    assert report["false_deterministic_activation_count"] == 0
    assert report["aggregator_calls_on_rejected_binding"] == 0
    assert report["stable_reasons_preserved"] is True
    assert all(
        record["binder_ready"] is False
        and record["expected_reason_preserved"] is True
        and record["binder_calls"] == 1
        and record["aggregator_calls"] == 0
        and record["false_deterministic_activation"] is False
        and record["answer_source"] != "c3_source_bound_sum_series"
        for record in records
    )
    cases = {record["case"]: record for record in records}
    assert "ROW_SOURCE_IDENTITY_MISMATCH" in cases[
        "coordinated_row_source_attack"
    ]["binder_reasons"]
    assert "TABLE_SOURCE_IDENTITY_MISMATCH" in cases[
        "coordinated_table_source_attack"
    ]["binder_reasons"]
    assert "ROW_SOURCE_IDENTITY_MISMATCH" in cases[
        "candidate_source_only_mismatch"
    ]["binder_reasons"]


def test_non_sum_and_insurance_routes_are_equivalent() -> None:
    report = build_report()
    parity = report["path_parity"]

    assert parity["non_sum"]["equivalent"] is True
    assert parity["non_sum"]["binder_calls"] == 1
    assert parity["non_sum"]["aggregator_calls"] == 0
    assert parity["non_sum"]["actual"] == parity["non_sum"]["expected"]
    assert parity["insurance"]["equivalent"] is True
    assert parity["insurance"]["binder_calls"] == 0
    assert parity["insurance"]["aggregator_calls"] == 0
    assert parity["insurance"]["actual"] == parity["insurance"]["expected"]


def test_ready_binding_with_failed_c3_m_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _generic_freeform_bundle(_bundle())
    solver = CalculationSolver(llm_client=None, fallback_llm_client=None)

    def failed_execute(self, request):
        return CalculationExecutionResult(
            ok=False,
            error="forced_c3_m_failure",
            gate_status="BLOCKED",
            audit_reasons=("forced_c3_m_failure",),
            source_refs=tuple(request.series.items[index].source_ref for index in range(len(request.series.items))),
        )

    def forbidden_legacy_path(bundle):
        raise AssertionError("ready Binder must not fall back to freeform/provider path")

    monkeypatch.setattr(SourceBoundNumericSeriesAggregator, "execute", failed_execute)
    monkeypatch.setattr(solver, "_solve_freeform", forbidden_legacy_path)

    result = solver.solve(bundle)

    assert result.answer == ""
    assert result.confidence == 0.0
    assert result.raw_output == "forced_c3_m_failure"
    assert result.metadata["answer_source"] == (
        "c3_source_bound_sum_series_execution_failed"
    )
    assert result.metadata["computation_status"] == "failed"
    assert result.metadata["computation_complete"] is False
    assert result.metadata["provider_call_count"] == 0
    assert result.metadata["total_tokens"] == 0
    assert result.metadata["legacy_execution_invoked"] is False
    assert result.metadata["error"] == "forced_c3_m_failure"
    assert result.metadata["binding_trace"]
    assert result.metadata["audit_reasons"] == ["forced_c3_m_failure"]


def test_activation_report_is_deterministic_and_zero_call() -> None:
    first = build_report()
    second = build_report()

    validate_report(first)
    assert first == second
    assert first["guardrail_result"] == "PASS"
    assert first["scope_caveat"] == SCOPE_CAVEAT
    assert first["measurement_valid"] is True
    assert first["source_lineage_complete"] is True
    assert first["provider_calls"] == 0
    assert first["legacy_calls"] == 0
    assert first["network_calls"] == 0
    assert first["total_tokens"] == 0


def test_frozen_baseline_hash_is_unchanged() -> None:
    import hashlib

    path = ROOT / BASELINE_PATH
    assert hashlib.sha256(path.read_bytes()).hexdigest() == BASELINE_SHA256


def test_activation_cli_writes_valid_machine_report(tmp_path: Path) -> None:
    output = tmp_path / "after.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_c3_sum_normal_pipeline_activation.py",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "before=0/3" in completed.stdout
    assert "after=3/3" in completed.stdout
    assert "guardrail=PASS" in completed.stdout
    written = json.loads(output.read_text(encoding="utf-8"))
    validate_report(written)
    assert written == build_report()


def test_principal_change_stays_inside_calculation_solver() -> None:
    source = (ROOT / "src/solvers/calculation.py").read_text(encoding="utf-8")

    assert "SourceBoundSumSeriesBinder" in source
    assert "SourceBoundNumericSeriesAggregator" in source
    assert "c3_source_bound_sum_series" in source
    assert "PipelineFactory" not in source
    assert "EnhancedBaselineWorkflow" not in source
    assert "RoutedSolver" not in source
    assert "C3ShadowObserver" not in source
