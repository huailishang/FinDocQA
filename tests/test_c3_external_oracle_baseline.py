from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from evaluation.external_benchmarks.c3_oracle_baseline import (
    _bottlenecks,
    _metrics,
    _native_scorer_consistency,
    build_source_manifest,
    deny_network,
    execute_c3_runtime,
    run_cases,
    verify_source_manifest,
)
from evaluation.external_benchmarks.contracts import (
    C3ExecutionObservation,
    OracleRuntime,
    TerminalClassification,
)
from evaluation.external_benchmarks.finqa_adapter import (
    FinQASeriesOracleRuntime,
    _referenced_variable_names,
    _table_numeric_value,
    load_finqa_cases,
)
from evaluation.external_benchmarks.native_scorers import (
    score_finqa_predictions,
    score_tatqa_predictions,
)
from evaluation.external_benchmarks.tatqa_adapter import (
    TATQAPredicateCardinalityOracleRuntime,
    _predicate_runtime_from_proof,
    _runtime_from_derivation,
    load_tatqa_cases,
)


FIXTURES = Path("tests/fixtures/external_benchmark_schema")
FINQA_ROOT = Path("evaluation_artifacts/external_benchmarks/finqa")
TATQA_ROOT = Path("evaluation_artifacts/external_benchmarks/tatqa")


def test_finqa_adapter_preserves_complete_input_and_fails_closed() -> None:
    cases = load_finqa_cases(FIXTURES / "finqa_minimal.json")

    assert [case.case_id for case in cases] == [
        "finqa-demo-add",
        "finqa-demo-table",
        "finqa-demo-greater",
    ]
    assert cases[0].runtime is not None
    assert cases[0].preclassified is None
    assert cases[1].preclassified is TerminalClassification.UNSUPPORTED_OPERATOR
    assert cases[2].preclassified is TerminalClassification.INELIGIBLE_NON_NUMERIC


def test_tatqa_adapter_classifies_arithmetic_count_and_span() -> None:
    cases = load_tatqa_cases(FIXTURES / "tatqa_minimal.json")

    assert len(cases) == 4
    assert cases[0].runtime is not None
    assert cases[0].runtime.output_multiplier == "1"
    assert cases[1].runtime is not None
    assert cases[1].runtime.output_multiplier == "100"
    assert cases[2].preclassified is TerminalClassification.UNSUPPORTED_OPERATOR
    assert cases[2].numeric_eligible is True
    assert cases[3].preclassified is TerminalClassification.INELIGIBLE_NON_NUMERIC
    assert cases[3].numeric_eligible is False


def test_tatqa_financial_parentheses_and_inline_scales_are_handled_explicitly() -> None:
    runtime, terminal, _detail, _parsed = _runtime_from_derivation(
        case_id="negative-parentheses",
        question="total",
        derivation="3 + (13) + 26",
        scale="",
    )
    assert terminal is None
    assert runtime is not None
    record = run_cases(
        (
            replace(
                load_tatqa_cases(FIXTURES / "tatqa_minimal.json")[0],
                case_id="negative-parentheses",
                runtime=runtime,
                label=replace(
                    load_tatqa_cases(FIXTURES / "tatqa_minimal.json")[0].label,
                    answer=16,
                    scale="",
                ),
            ),
        )
    )[0]
    assert record.terminal_classification is TerminalClassification.EXECUTED_CORRECT

    runtime, terminal, detail, parsed = _runtime_from_derivation(
        case_id="inline-scale",
        question="sum",
        derivation="60.3 million + 32,137 thousand",
        scale="thousand",
    )
    assert runtime is None
    assert terminal is TerminalClassification.UNSUPPORTED_SCALE_OR_UNIT
    assert detail.startswith("inline_scale:")
    assert parsed is False


def test_runtime_object_contains_no_gold_answer_and_executor_receives_runtime_only() -> None:
    case = load_finqa_cases(FIXTURES / "finqa_minimal.json")[0]
    received: list[OracleRuntime] = []

    def spy(runtime: OracleRuntime) -> C3ExecutionObservation:
        received.append(runtime)
        return C3ExecutionObservation(ok=True, answer="5")

    records = run_cases((case,), executor=spy)

    assert len(received) == 1
    assert not hasattr(received[0], "answer")
    assert "gold" not in received[0].to_dict()
    assert "expected" not in received[0].to_dict()
    assert records[0].terminal_classification is TerminalClassification.EXECUTED_CORRECT


def test_existing_explicit_c3_path_executes_supported_program_without_side_effects() -> None:
    runtime = load_finqa_cases(FIXTURES / "finqa_minimal.json")[0].runtime
    assert runtime is not None

    observation = execute_c3_runtime(runtime)

    assert observation.ok is True
    assert observation.answer == "5"
    assert observation.provider_call_count == 0
    assert observation.legacy_call_count == 0
    assert observation.prompt_tokens == 0
    assert observation.completion_tokens == 0
    assert observation.total_tokens == 0
    assert observation.trace
    assert observation.source_lineage


def test_minimal_corpora_have_one_terminal_record_per_case_and_stable_metrics() -> None:
    cases = (
        *load_finqa_cases(FIXTURES / "finqa_minimal.json"),
        *load_tatqa_cases(FIXTURES / "tatqa_minimal.json"),
    )
    first = run_cases(cases)
    second = run_cases(cases)

    assert [row.to_dict() for row in first] == [row.to_dict() for row in second]
    assert len(first) == len(cases) == 7
    combined = _metrics(first, None)
    assert combined["source_case_count"] == 7
    assert combined["terminal_record_count"] == 7
    assert combined["numeric_eligible_count"] == 5
    assert combined["c3_representable_count"] == 3
    assert combined["supported_subset_execution_exact_match_rate"]["value"] == 1.0


def test_failure_taxonomy_and_primary_bottleneck_are_data_derived() -> None:
    cases = (
        *load_finqa_cases(FIXTURES / "finqa_minimal.json"),
        *load_tatqa_cases(FIXTURES / "tatqa_minimal.json"),
    )
    records = run_cases(cases)
    report, primary = _bottlenecks(records)

    assert report["UNSUPPORTED_OPERATOR"]["case_count"] == 2
    assert primary == "UNSUPPORTED_OPERATOR"


def test_native_scorer_parity_for_emitted_predictions() -> None:
    if not (FINQA_ROOT / "code/evaluate/evaluate.py").exists():
        pytest.skip("official FinQA repository not acquired")
    finqa_cases = load_finqa_cases(FIXTURES / "finqa_minimal.json")
    tatqa_cases = load_tatqa_cases(FIXTURES / "tatqa_minimal.json")
    records = run_cases((*finqa_cases, *tatqa_cases))

    finqa = score_finqa_predictions(
        finqa_cases,
        records,
        scorer_path=FINQA_ROOT / "code/evaluate/evaluate.py",
    )
    tatqa = score_tatqa_predictions(tatqa_cases, records)

    assert finqa["parity_delta"] == 0
    assert finqa["per_prediction_output_mismatch_count"] == 0
    assert tatqa["parity_delta"] == 0
    assert tatqa["per_prediction_output_mismatch_count"] == 0


def test_source_manifest_freezes_commits_hashes_and_license() -> None:
    if not (FINQA_ROOT / ".git").exists() or not (TATQA_ROOT / ".git").exists():
        pytest.skip("official repositories not acquired")
    manifest = build_source_manifest(
        FINQA_ROOT,
        TATQA_ROOT,
        retrieved_at="2026-08-01T12:00:00+00:00",
    )

    verify_source_manifest(manifest, FINQA_ROOT, TATQA_ROOT)
    assert [source["license_identifier"] for source in manifest["sources"]] == ["MIT", "MIT"]
    broken = {**manifest, "sources": [dict(manifest["sources"][0]), dict(manifest["sources"][1])]}
    broken["sources"][0]["selected_split_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest mismatch"):
        verify_source_manifest(broken, FINQA_ROOT, TATQA_ROOT)


def test_network_guard_fails_closed_and_counts_attempt() -> None:
    with deny_network() as counter:
        with pytest.raises(AssertionError, match="network access"):
            import socket

            socket.create_connection(("example.invalid", 443))
    assert counter["count"] == 1


def test_incorrect_execution_remains_in_supported_denominator() -> None:
    case = load_finqa_cases(FIXTURES / "finqa_minimal.json")[0]

    records = run_cases(
        (case,), executor=lambda runtime: C3ExecutionObservation(ok=True, answer="999")
    )
    metrics = _metrics(records, "finqa")

    assert records[0].terminal_classification is TerminalClassification.EXECUTED_INCORRECT
    assert metrics["c3_representable_count"] == 1
    assert metrics["supported_subset_execution_exact_match_rate"]["denominator"] == 1
    assert metrics["supported_subset_execution_exact_match_rate"]["numerator"] == 0


def test_finqa_full_split_projects_exact_final_expression_variables() -> None:
    source = FINQA_ROOT / "dataset/dev.json"
    if not source.exists():
        pytest.skip("official FinQA repository not acquired")

    for case in load_finqa_cases(source):
        if case.runtime is None:
            continue
        if isinstance(case.runtime, FinQASeriesOracleRuntime):
            assert case.runtime.expression == ""
            assert case.runtime.variables == ()
            assert case.runtime.aggregation_request is not None
            continue
        referenced = set(_referenced_variable_names(case.runtime.expression))
        supplied = {variable.name for variable in case.runtime.variables}
        assert supplied == referenced, case.case_id


def test_finqa_projection_repairs_evaluator_counterexamples() -> None:
    source = FINQA_ROOT / "dataset/dev.json"
    if not source.exists():
        pytest.skip("official FinQA repository not acquired")
    cases = {case.case_id: case for case in load_finqa_cases(source)}

    expected = {
        "ETR/2011/page_324.pdf-3": "-16402",
        "CDNS/2012/page_30.pdf-2": "-31.86",
    }
    for case_id, answer in expected.items():
        runtime = cases[case_id].runtime
        assert runtime is not None
        assert {variable.name for variable in runtime.variables} == set(
            _referenced_variable_names(runtime.expression)
        )
        observation = execute_c3_runtime(runtime)
        assert observation.ok is True
        assert observation.answer == answer


def test_finqa_native_scorer_uses_actual_prediction_and_fails_closed() -> None:
    if not (FINQA_ROOT / "code/evaluate/evaluate.py").exists():
        pytest.skip("official FinQA repository not acquired")
    cases = load_finqa_cases(FIXTURES / "finqa_minimal.json")
    records = list(run_cases(cases))
    records[0] = replace(
        records[0],
        predicted_answer="999",
        terminal_classification=TerminalClassification.EXECUTED_INCORRECT,
        native_prediction_emitted=True,
    )

    score = score_finqa_predictions(
        cases,
        records,
        scorer_path=FINQA_ROOT / "code/evaluate/evaluate.py",
    )

    assert score["native_score"] == 0
    assert score["internal_equivalent_score"] == 0
    assert score["parity_delta"] == 0
    assert score["per_prediction_output_mismatch_count"] == 0
    assert score["native_correct_count"] == 0
    assert score["internal_equivalent_correct_count"] == 0

    for malformed in ("", "not-a-number", "nan", "inf", "-inf"):
        malformed_records = list(records)
        malformed_records[0] = replace(records[0], predicted_answer=malformed)
        malformed_score = score_finqa_predictions(
            cases,
            malformed_records,
            scorer_path=FINQA_ROOT / "code/evaluate/evaluate.py",
        )
        assert malformed_score["native_score"] == 0
        assert malformed_score["internal_equivalent_score"] == 0
        assert malformed_score["invalid_prediction_count"] == 1


def test_native_scorer_consistency_detects_stale_correctness() -> None:
    cases = load_finqa_cases(FIXTURES / "finqa_minimal.json")
    records = list(run_cases(cases))
    records[0] = replace(
        records[0],
        predicted_answer="999",
        terminal_classification=TerminalClassification.EXECUTED_INCORRECT,
        native_prediction_emitted=True,
    )

    stale = _native_scorer_consistency(
        records,
        "finqa",
        {"prediction_count": 1, "native_correct_count": 1},
    )
    assert stale["native_prediction_count_consistency_delta"] == 0
    assert stale["native_score_consistency_delta"] == 1

    repaired = _native_scorer_consistency(
        records,
        "finqa",
        {"prediction_count": 1, "native_correct_count": 0},
    )
    assert repaired["native_prediction_count_consistency_delta"] == 0
    assert repaired["native_score_consistency_delta"] == 0

def test_finqa_selected_series_subset_is_exact_generic_and_source_bound() -> None:
    source = FINQA_ROOT / "dataset/dev.json"
    triage_path = Path(
        "evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl"
    )
    if not source.exists() or not triage_path.exists():
        pytest.skip("official FinQA source or accepted triage missing")

    cases = load_finqa_cases(source)
    source_by_id = {
        row["id"]: row
        for row in json.loads(source.read_text(encoding="utf-8"))
    }
    selected = [case for case in cases if isinstance(case.runtime, FinQASeriesOracleRuntime)]
    accepted = {
        row["case_id"]
        for row in (
            json.loads(line)
            for line in triage_path.read_text(encoding="utf-8").splitlines()
            if line
        )
        if row["candidate_capability"] == "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
        and row["selection_eligibility"]
    }

    assert len(selected) == 33
    assert len({case.case_id for case in selected}) == 33
    assert {case.case_id for case in selected} == accepted
    for case in selected:
        runtime = case.runtime
        assert isinstance(runtime, FinQASeriesOracleRuntime)
        request = runtime.aggregation_request
        assert request is not None
        source_record = source_by_id[case.case_id]
        source_table = source_record["table"]
        source_rows = [
            row for row in source_table if row and row[0] == request.series.metric
        ]
        assert len(source_rows) == 1
        source_row = source_rows[0]
        assert runtime.official_table_program == source_record["qa"]["program"]
        assert "table_" not in runtime.native_program
        assert [item.value for item in request.series.items] == [
            _table_numeric_value(cell) for cell in source_row[1:]
        ]
        assert [item.header_label for item in request.series.items] == list(
            source_table[0][1:]
        )
        assert request.question_aggregation_match.passed is True
        assert request.series.aggregation_range_explicit is True
        assert request.series.total_components_ambiguity is False
        assert request.series.items
        assert [item.position for item in request.series.items] == list(
            range(len(request.series.items))
        )
        assert len({item.source_coordinate for item in request.series.items}) == len(
            request.series.items
        )
        assert all(
            item.source_ref is not None
            and item.source_ref.source == request.series.source_object_id
            and item.source_object_id == request.series.source_object_id
            for item in request.series.items
        )


def test_finqa_selected_series_subset_executes_33_of_33_through_product_api() -> None:
    source = FINQA_ROOT / "dataset/dev.json"
    if not source.exists():
        pytest.skip("official FinQA repository not acquired")

    selected = tuple(
        case
        for case in load_finqa_cases(source)
        if isinstance(case.runtime, FinQASeriesOracleRuntime)
    )
    records = run_cases(selected)

    assert len(records) == 33
    assert all(
        record.terminal_classification is TerminalClassification.EXECUTED_CORRECT
        for record in records
    )
    assert all(record.c3_representable for record in records)
    assert all(record.provider_call_count == 0 for record in records)
    assert all(record.legacy_call_count == 0 for record in records)
    assert all(record.total_tokens == 0 for record in records)

    for case in selected:
        assert case.runtime is not None
        observation = execute_c3_runtime(case.runtime)
        assert observation.ok is True
        assert observation.trace
        assert observation.source_lineage
        assert all(item["source_coordinate"] for item in observation.source_lineage)


def test_finqa_excluded_series_cases_remain_fail_closed() -> None:
    source = FINQA_ROOT / "dataset/dev.json"
    if not source.exists():
        pytest.skip("official FinQA repository not acquired")
    cases = {case.case_id: case for case in load_finqa_cases(source)}

    label_case = cases["AAPL/2014/page_38.pdf-1"]
    ambiguous_case = cases["ABMD/2009/page_56.pdf-1"]
    assert label_case.runtime is None
    assert label_case.preclassified is TerminalClassification.UNSUPPORTED_OPERATOR
    assert label_case.failure_detail == "LABEL_OUTPUT_NOT_SUPPORTED"
    assert ambiguous_case.runtime is None
    assert ambiguous_case.preclassified is TerminalClassification.UNSUPPORTED_OPERATOR
    assert ambiguous_case.failure_detail == "AMBIGUOUS_AGGREGATION_RANGE"


def test_c3m_snapshot_does_not_overwrite_c3l_frozen_baseline() -> None:
    baseline_root = Path("evaluation_artifacts/c3_external_oracle_baseline_v1")
    c3l_report = json.loads(
        (baseline_root / "aggregate_report.json").read_text(encoding="utf-8")
    )
    c3m_report = json.loads(
        (
            baseline_root
            / "c3m_source_bound_numeric_series_aggregation_v1"
            / "aggregate_report.json"
        ).read_text(encoding="utf-8")
    )

    assert c3l_report["datasets"]["combined"]["c3_representable_count"] == 1550
    assert c3l_report["datasets"]["combined"]["terminal_executed_correct_count"] == 1548
    assert c3m_report["datasets"]["combined"]["c3_representable_count"] == 1583
    assert c3m_report["datasets"]["combined"]["terminal_executed_correct_count"] == 1581
    assert c3l_report["first_record_sha256"] != c3m_report["first_record_sha256"]
    assert c3l_report["measurement_valid"] is True
    assert c3m_report["measurement_valid"] is True


def test_series_product_code_has_no_case_or_dataset_dispatch() -> None:
    product_source = Path("src/calculation/series_aggregation.py").read_text(
        encoding="utf-8"
    ).lower()
    adapter_source = Path(
        "src/evaluation/external_benchmarks/finqa_adapter.py"
    ).read_text(encoding="utf-8")

    assert "finqa" not in product_source
    assert "tatqa" not in product_source
    assert "AAPL/2014/page_38.pdf-1" not in adapter_source
    assert "ABMD/2009/page_56.pdf-1" not in adapter_source



def _accepted_predicate_case_ids() -> set[str]:
    triage_path = Path(
        "evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl"
    )
    rows = [
        json.loads(line)
        for line in triage_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return {
        row["case_id"]
        for row in rows
        if row.get("candidate_capability")
        == "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY"
        and row.get("candidate_type") == "PRODUCT_CAPABILITY"
        and row.get("selection_eligibility") is True
        and row.get("binding_uniqueness_status") == "UNIQUE"
        and (row.get("oracle_proof") or {}).get("proof_status") == "COMPLETE"
    }


def test_tatqa_selected_predicate_subset_is_exact_generic_and_source_bound() -> None:
    source = TATQA_ROOT / "dataset_raw/tatqa_dataset_dev.json"
    triage_path = Path(
        "evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl"
    )
    if not source.exists() or not triage_path.exists():
        pytest.skip("official TAT-QA source or accepted triage missing")

    selected = [
        case
        for case in load_tatqa_cases(source)
        if isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
    ]
    accepted = _accepted_predicate_case_ids()

    assert len(selected) == 16
    assert len({case.case_id for case in selected}) == 16
    assert {case.case_id for case in selected} == accepted
    runtimes = [case.runtime for case in selected]
    assert all(isinstance(runtime, TATQAPredicateCardinalityOracleRuntime) for runtime in runtimes)
    assert Counter(runtime.oracle_axis for runtime in runtimes) == {
        "ROW_ACROSS_PERIOD_COLUMNS": 13,
        "CATEGORY_ROWS_IN_SINGLE_PERIOD_COLUMN": 2,
        "CATEGORY_ROWS_IN_BOUND_SECTION": 1,
    }
    assert Counter(runtime.predicate_request.operator.value for runtime in runtimes) == {
        "GREATER_THAN": 15,
        "LESS_THAN": 1,
    }
    assert Counter(runtime.predicate_request.threshold_unit for runtime in runtimes) == {
        "million": 8,
        "thousand": 8,
    }
    assert Counter(
        len(runtime.predicate_request.collection.items) for runtime in runtimes
    ) == {2: 5, 3: 10, 15: 1}

    for runtime in runtimes:
        request = runtime.predicate_request
        assert request is not None
        assert request.question_predicate_match.passed is True
        assert isinstance(request.threshold, Decimal)
        assert request.threshold.is_finite()
        assert request.collection.binding_status.value == "EXACT"
        assert request.collection.aggregation_range_explicit is True
        assert request.collection.total_components_ambiguity is False
        assert [item.position for item in request.collection.items] == list(
            range(len(request.collection.items))
        )
        assert len({item.source_coordinate for item in request.collection.items}) == len(
            request.collection.items
        )
        assert all(
            item.source_ref is not None
            and item.source_ref.source == request.collection.source_object_id
            and item.source_object_id == request.collection.source_object_id
            and item.unit == request.threshold_unit
            and item.dimension == request.threshold_dimension
            and item.header_label
            for item in request.collection.items
        )


def test_tatqa_predicate_subset_executes_16_of_16_with_complete_trace() -> None:
    source = TATQA_ROOT / "dataset_raw/tatqa_dataset_dev.json"
    if not source.exists():
        pytest.skip("official TAT-QA repository not acquired")
    selected = tuple(
        case
        for case in load_tatqa_cases(source)
        if isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
    )
    records = run_cases(selected)
    by_id = {record.case_id: record for record in records}

    assert len(records) == 16
    assert all(
        record.terminal_classification is TerminalClassification.EXECUTED_CORRECT
        for record in records
    )
    assert all(record.c3_representable for record in records)
    assert all(record.provider_call_count == 0 for record in records)
    assert all(record.legacy_call_count == 0 for record in records)
    assert all(record.total_tokens == 0 for record in records)

    for case in selected:
        runtime = case.runtime
        assert isinstance(runtime, TATQAPredicateCardinalityOracleRuntime)
        request = runtime.predicate_request
        assert request is not None
        observation = execute_c3_runtime(runtime)
        assert observation.ok is True
        assert observation.answer == by_id[case.case_id].predicted_answer
        assert len(observation.source_lineage) == len(request.collection.items)
        assert len(observation.trace) == len(request.collection.items) + 1
        comparisons = observation.trace[:-1]
        summary = observation.trace[-1]
        assert all(row["trace_type"] == "predicate_comparison" for row in comparisons)
        assert [row["source_coordinate"] for row in comparisons] == [
            item.source_coordinate for item in request.collection.items
        ]
        assert all(type(row["matched"]) is bool for row in comparisons)
        assert summary["trace_type"] == "predicate_cardinality_summary"
        assert summary["total_member_count"] == len(request.collection.items)
        assert summary["matched_count"] == int(observation.answer)


def test_tatqa_predicate_selection_is_answer_independent_and_scoring_uses_prediction(
    tmp_path: Path,
) -> None:
    source = TATQA_ROOT / "dataset_raw/tatqa_dataset_dev.json"
    scorer_root = TATQA_ROOT
    triage_path = Path(
        "evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl"
    )
    if not source.exists() or not triage_path.exists():
        pytest.skip("official TAT-QA source or accepted triage missing")

    original = load_tatqa_cases(source)
    original_selected = {
        case.case_id: case.runtime.predicate_request
        for case in original
        if isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
    }
    payload = json.loads(source.read_text(encoding="utf-8"))
    for document in payload:
        for row in document.get("questions", []):
            row["answer"] = "987654321"
    mutated_path = tmp_path / "tatqa_mutated_answers.json"
    mutated_path.write_text(json.dumps(payload), encoding="utf-8")
    mutated = load_tatqa_cases(
        mutated_path,
        predicate_taxonomy_path=triage_path,
    )
    mutated_selected = {
        case.case_id: case.runtime.predicate_request
        for case in mutated
        if isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
    }

    assert len(original_selected) == 16
    assert original_selected == mutated_selected
    selected_cases = tuple(
        case
        for case in original
        if isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
    )
    records = list(run_cases(selected_cases))
    base_score = score_tatqa_predictions(selected_cases, records)
    records[0] = replace(
        records[0],
        predicted_answer="999999999",
        terminal_classification=TerminalClassification.EXECUTED_INCORRECT,
    )
    wrong_score = score_tatqa_predictions(selected_cases, records)

    assert base_score["native_correct_count"] == 16
    assert wrong_score["native_correct_count"] == 15
    assert wrong_score["internal_equivalent_correct_count"] == 15
    assert wrong_score["parity_delta"] == 0
    assert scorer_root.exists()


def test_predicate_cardinality_can_be_disabled_to_preserve_c3m_behavior() -> None:
    source = TATQA_ROOT / "dataset_raw/tatqa_dataset_dev.json"
    if not source.exists():
        pytest.skip("official TAT-QA repository not acquired")
    enabled = load_tatqa_cases(source)
    disabled = load_tatqa_cases(source, enable_predicate_cardinality=False)

    assert sum(
        isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
        for case in enabled
    ) == 16
    assert not any(
        isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
        for case in disabled
    )
    assert sum(
        case.preclassified is TerminalClassification.UNSUPPORTED_OPERATOR
        and case.failure_detail == "answer_type:count"
        for case in disabled
    ) >= 16


def test_predicate_product_code_has_no_case_dataset_or_answer_dispatch() -> None:
    product_source = Path("src/calculation/predicate_cardinality.py").read_text(
        encoding="utf-8"
    ).lower()
    adapter_source = Path(
        "src/evaluation/external_benchmarks/tatqa_adapter.py"
    ).read_text(encoding="utf-8")

    assert "tatqa" not in product_source
    assert "finqa" not in product_source
    assert "case_id" not in product_source
    assert "independently_derived_expected_count" not in product_source
    for case_id in _accepted_predicate_case_ids():
        assert case_id not in adapter_source


def test_c3n_snapshot_is_isolated_from_c3l_and_c3m_snapshots() -> None:
    baseline_root = Path("evaluation_artifacts/c3_external_oracle_baseline_v1")
    c3l = json.loads(
        (baseline_root / "aggregate_report.json").read_text(encoding="utf-8")
    )
    c3m = json.loads(
        (
            baseline_root
            / "c3m_source_bound_numeric_series_aggregation_v1"
            / "aggregate_report.json"
        ).read_text(encoding="utf-8")
    )
    c3n = json.loads(
        (
            baseline_root
            / "c3n_source_bound_table_predicate_cardinality_v1"
            / "aggregate_report.json"
        ).read_text(encoding="utf-8")
    )

    assert c3l["datasets"]["combined"]["c3_representable_count"] == 1550
    assert c3l["datasets"]["combined"]["terminal_executed_correct_count"] == 1548
    assert c3m["datasets"]["combined"]["c3_representable_count"] == 1583
    assert c3m["datasets"]["combined"]["terminal_executed_correct_count"] == 1581
    assert c3n["datasets"]["combined"]["c3_representable_count"] == 1599
    assert c3n["datasets"]["combined"]["terminal_executed_correct_count"] == 1597
    assert c3n["datasets"]["combined"]["executed_incorrect_count"] == 2
    assert c3n["datasets"]["combined"]["c3_execution_error_count"] == 0
    assert c3n["bottlenecks"]["UNSUPPORTED_OPERATOR"]["case_count"] == 23
    assert c3l["first_record_sha256"] != c3m["first_record_sha256"]
    assert c3m["first_record_sha256"] != c3n["first_record_sha256"]
    assert c3l["measurement_valid"] is True
    assert c3m["measurement_valid"] is True
    assert c3n["measurement_valid"] is True

ROW_AXIS_CASE = "08ec1b4c-f5dc-4654-b931-4ddd23f81113"
SINGLE_COLUMN_CASE = "3d384cee-82de-48f1-98ff-a972404bce4c"
BOUND_SECTION_CASE = "378b81b7-c7d6-46f9-aca6-58729440a889"


def _predicate_axis_test_inputs() -> tuple[
    dict[str, tuple[dict[str, object], dict[str, object]]],
    dict[str, dict[str, object]],
]:
    source = json.loads(
        (TATQA_ROOT / "dataset_raw/tatqa_dataset_dev.json").read_text(
            encoding="utf-8"
        )
    )
    index = {
        question["uid"]: (document["table"], question)
        for document in source
        for question in document["questions"]
    }
    rows = [
        json.loads(line)
        for line in Path(
            "evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line
    ]
    proofs = {
        row["case_id"]: row["oracle_proof"]
        for row in rows
        if row.get("candidate_capability")
        == "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY"
        and row.get("selection_eligibility") is True
    }
    return index, proofs


def _build_predicate_runtime_from_copies(
    case_id: str,
    *,
    axis_changes: dict[str, object] | None = None,
    member_mutator=None,
    table_mutator=None,
    proof_mutator=None,
):
    index, proofs = _predicate_axis_test_inputs()
    table_payload, question = deepcopy(index[case_id])
    proof = deepcopy(proofs[case_id])
    if axis_changes:
        proof["bound_axis_or_section"].update(axis_changes)
    if member_mutator is not None:
        member_mutator(proof["bound_member_or_value_coordinates"])
    if table_mutator is not None:
        table_mutator(table_payload["table"])
    if proof_mutator is not None:
        proof_mutator(proof)
    return _predicate_runtime_from_proof(
        table_payload=table_payload,
        question_row=question,
        proof=proof,
    )


@pytest.mark.parametrize(
    "axis_changes",
    [
        {"row_index": 999},
        {"row_index": True},
        {"row_label": "not the real row"},
        {"period_labels": ["2099"]},
        {"period_labels": None},
        {"period_labels": [True, "2018", "2017"]},
    ],
)
def test_row_axis_tampering_fails_closed(axis_changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            ROW_AXIS_CASE,
            axis_changes=axis_changes,
        )


@pytest.mark.parametrize(
    "member_mutator",
    [
        lambda members: members[0].update({"row_index": 5}),
        lambda members: members[0].update({"row_index": True}),
        lambda members: members[0].update({"column_index": 2}),
        lambda members: members[0].update({"column_index": True}),
        lambda members: members[0].update({"period_label": "2099"}),
        lambda members: members.pop(),
        lambda members: members.append(deepcopy(members[-1])),
    ],
)
def test_row_axis_member_tampering_fails_closed(member_mutator) -> None:
    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            ROW_AXIS_CASE,
            member_mutator=member_mutator,
        )


@pytest.mark.parametrize(
    "axis_changes",
    [
        {"column_index": 999},
        {"column_index": True},
        {"start_row": 999, "end_row_exclusive": 1000},
        {"start_row": True},
        {"end_row_exclusive": True},
        {"start_row": 3, "end_row_exclusive": 3},
    ],
)
def test_single_period_column_axis_tampering_fails_closed(
    axis_changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            axis_changes=axis_changes,
        )


@pytest.mark.parametrize(
    "member_mutator",
    [
        lambda members: members[0].update({"column_index": 2}),
        lambda members: members[0].update({"column_index": True}),
        lambda members: members[1].update({"row_index": 3}),
        lambda members: members.pop(1),
        lambda members: members.append(
            {
                **deepcopy(members[-1]),
                "row_index": 4,
                "coordinate": str(members[-1]["coordinate"]).replace("r3", "r4"),
            }
        ),
    ],
)
def test_single_period_column_member_tampering_fails_closed(
    member_mutator,
) -> None:
    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            member_mutator=member_mutator,
        )


@pytest.mark.parametrize(
    "axis_changes",
    [
        {"section_phrase": "not the real section"},
        {"section_phrase": None},
        {"column_index": 999},
        {"column_index": True},
        {"start_row": 999, "end_row_exclusive": 1000},
        {"start_row": 2},
        {"end_row_exclusive": 17},
    ],
)
def test_bound_section_axis_tampering_fails_closed(
    axis_changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            axis_changes=axis_changes,
        )


def test_bound_section_heading_tampering_fails_closed() -> None:
    def mutate_heading(table: list[list[object]]) -> None:
        table[2][0] = "Deferred tax liabilities:"

    with pytest.raises(ValueError, match="section heading mismatch"):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            table_mutator=mutate_heading,
        )


@pytest.mark.parametrize(
    "member_mutator",
    [
        lambda members: members[0].update({"column_index": 2}),
        lambda members: members[0].update({"row_index": 2}),
        lambda members: members[-1].update({"row_index": 18}),
        lambda members: members.pop(),
        lambda members: members.append(
            {
                **deepcopy(members[-1]),
                "row_index": 18,
                "coordinate": str(members[-1]["coordinate"]).replace("r17", "r18"),
            }
        ),
    ],
)
def test_bound_section_member_tampering_fails_closed(member_mutator) -> None:
    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            member_mutator=member_mutator,
        )


def test_all_normal_predicate_axis_bindings_still_build_exactly_16() -> None:
    source = TATQA_ROOT / "dataset_raw/tatqa_dataset_dev.json"
    selected = [
        case
        for case in load_tatqa_cases(source)
        if isinstance(case.runtime, TATQAPredicateCardinalityOracleRuntime)
    ]
    assert len(selected) == 16
    assert Counter(case.runtime.oracle_axis for case in selected) == {
        "ROW_ACROSS_PERIOD_COLUMNS": 13,
        "CATEGORY_ROWS_IN_SINGLE_PERIOD_COLUMN": 2,
        "CATEGORY_ROWS_IN_BOUND_SECTION": 1,
    }

def test_single_period_coordinated_end_truncation_fails_closed() -> None:
    def mutate(proof: dict[str, object]) -> None:
        axis = proof["bound_axis_or_section"]
        members = proof["bound_member_or_value_coordinates"]
        axis["end_row_exclusive"] = 3
        proof["bound_member_or_value_coordinates"] = members[:-1]

    with pytest.raises(ValueError, match="official complete range"):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            proof_mutator=mutate,
        )


def test_single_period_coordinated_start_truncation_fails_closed() -> None:
    def mutate(proof: dict[str, object]) -> None:
        axis = proof["bound_axis_or_section"]
        members = proof["bound_member_or_value_coordinates"]
        axis["start_row"] = 2
        proof["bound_member_or_value_coordinates"] = members[1:]
        proof["independently_derived_expected_count"] = 0

    with pytest.raises(ValueError, match="official complete range"):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            proof_mutator=mutate,
        )


def test_single_period_middle_member_omission_fails_closed() -> None:
    def mutate(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        proof["bound_member_or_value_coordinates"] = [members[0], members[2]]

    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            proof_mutator=mutate,
        )


@pytest.mark.parametrize("range_rule", [None, True, object(), "unknown rule"])
def test_single_period_unknown_range_rule_fails_closed(range_rule: object) -> None:
    with pytest.raises(ValueError, match="range_rule unsupported"):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            axis_changes={"range_rule": range_rule},
        )


def test_single_period_multiple_total_boundaries_are_ambiguous() -> None:
    def mutate_table(table: list[list[object]]) -> None:
        table.append(deepcopy(table[4]))

    with pytest.raises(ValueError, match="Total boundary is not unique"):
        _build_predicate_runtime_from_copies(
            SINGLE_COLUMN_CASE,
            table_mutator=mutate_table,
        )


def test_single_period_total_row_is_numeric_but_excluded() -> None:
    runtime = _build_predicate_runtime_from_copies(SINGLE_COLUMN_CASE)
    request = runtime.predicate_request
    assert request is not None
    assert len(request.collection.items) == 3
    assert all("/r4c" not in item.source_coordinate for item in request.collection.items)


@pytest.mark.parametrize("removed_count", [1, 2])
def test_bound_section_coordinated_end_truncation_fails_closed(
    removed_count: int,
) -> None:
    def mutate(proof: dict[str, object]) -> None:
        axis = proof["bound_axis_or_section"]
        members = proof["bound_member_or_value_coordinates"]
        axis["end_row_exclusive"] = 18 - removed_count
        proof["bound_member_or_value_coordinates"] = members[:-removed_count]

    with pytest.raises(ValueError, match="official complete range"):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            proof_mutator=mutate,
        )


def test_bound_section_coordinated_start_truncation_fails_closed() -> None:
    def mutate(proof: dict[str, object]) -> None:
        axis = proof["bound_axis_or_section"]
        members = proof["bound_member_or_value_coordinates"]
        axis["start_row"] = 4
        proof["bound_member_or_value_coordinates"] = members[1:]
        proof["independently_derived_expected_count"] = 2

    with pytest.raises(ValueError, match="official complete range"):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            proof_mutator=mutate,
        )


def test_bound_section_middle_member_omission_fails_closed() -> None:
    def mutate(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        proof["bound_member_or_value_coordinates"] = members[:5] + members[6:]

    with pytest.raises(ValueError):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            proof_mutator=mutate,
        )


def test_bound_section_cannot_include_total_row() -> None:
    def mutate(proof: dict[str, object]) -> None:
        axis = proof["bound_axis_or_section"]
        members = proof["bound_member_or_value_coordinates"]
        total_member = deepcopy(members[-1])
        total_member.update(
            {
                "row_index": 18,
                "column_index": 1,
                "coordinate": "tatqa://table/9afaa852-4103-4782-9109-34104931dbc1/r18c1",
                "member_label": "Total deferred tax assets before valuation allowances",
                "raw_value": "489,689",
                "numeric_value": "489689",
            }
        )
        axis["end_row_exclusive"] = 19
        proof["bound_member_or_value_coordinates"] = [*members, total_member]
        proof["independently_derived_expected_count"] = 4

    with pytest.raises(ValueError, match="official complete range"):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            proof_mutator=mutate,
        )


def test_bound_section_total_must_name_the_section() -> None:
    def mutate_table(table: list[list[object]]) -> None:
        table[18][0] = "Total unrelated assets"

    with pytest.raises(ValueError, match="Total row does not match section"):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            table_mutator=mutate_table,
        )


def test_bound_section_requires_numeric_detail_immediately_after_heading() -> None:
    def mutate_table(table: list[list[object]]) -> None:
        table[3][1] = ""

    with pytest.raises(ValueError, match="table cell is not numeric"):
        _build_predicate_runtime_from_copies(
            BOUND_SECTION_CASE,
            table_mutator=mutate_table,
        )
