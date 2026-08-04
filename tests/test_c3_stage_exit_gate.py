from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from agent.factory import PipelineFactory
from scripts.evaluate_c3_stage_exit import (
    CAPABILITY_ORDER,
    LAYER_ORDER,
    SCHEMA_VERSION,
    VALID_ACTIVATION_STATES,
    build_stage_exit_report,
    validate_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _runtime_git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _report() -> dict:
    report = build_stage_exit_report(ROOT)
    validate_report(report)
    return report


def test_report_has_required_schema_fields_and_runtime_baseline() -> None:
    report = _report()
    runtime_head = _runtime_git_head()

    required = {
        "schema_version",
        "baseline_head",
        "capability_inventory",
        "activation_matrix",
        "normal_pipeline_probes",
        "blocking_layer_counts",
        "remaining_operator_summary",
        "stage_decision",
        "stage_decision_reasons",
        "recommended_next_layer",
        "provider_calls",
        "legacy_calls",
        "network_calls",
        "total_tokens",
        "measurement_valid",
    }
    assert required <= set(report)
    assert report["schema_version"] == SCHEMA_VERSION
    assert re.fullmatch(r"[0-9a-f]{40}", runtime_head)
    assert report["baseline_head"] == runtime_head
    assert report["measurement_valid"] is True


def test_capability_inventory_keeps_oracle_and_end_to_end_boundaries_separate() -> None:
    report = _report()
    inventory = report["capability_inventory"]

    assert set(inventory) == set(CAPABILITY_ORDER)
    assert [inventory[name]["stage"] for name in CAPABILITY_ORDER] == [
        "C3-M",
        "C3-N",
        "C3-O",
    ]
    assert [
        inventory[name]["historical_external_coverage_gain"]["new_representable"]
        for name in CAPABILITY_ORDER
    ] == [33, 16, 3]
    for name in CAPABILITY_ORDER:
        assert inventory[name]["failure_closed_boundary"]
        boundary = inventory[name]["evidence_boundary"].lower()
        assert "oracle-program" in boundary
        assert "not retrieval" in boundary
        assert "end-to-end" in boundary


def test_activation_matrix_has_three_capabilities_four_layers_and_unique_states() -> None:
    report = _report()
    matrix = report["activation_matrix"]

    assert set(matrix) == set(CAPABILITY_ORDER)
    for capability in CAPABILITY_ORDER:
        assert set(matrix[capability]) == set(LAYER_ORDER)
        for layer in LAYER_ORDER:
            assert matrix[capability][layer]["status"] in VALID_ACTIVATION_STATES

        assert matrix[capability]["ORACLE_RUNTIME"]["status"] == "ACTIVE"
        assert matrix[capability]["ORACLE_RUNTIME"]["correct_result"] is True
        assert (
            matrix[capability]["EXPLICIT_C3_CALL"]["status"]
            == "EXPLICIT_CALLER_ONLY"
        )
        assert matrix[capability]["EXPLICIT_C3_CALL"]["correct_result"] is True
        assert matrix[capability]["EXPLICIT_C3_CALL"]["manual_program_or_request"] is True
        assert (
            matrix[capability]["SHADOW_OBSERVER"]["status"]
            == "BLOCKED_BY_MISSING_EVIDENCE"
        )
        expected_normal_status = (
            "ACTIVE"
            if capability == CAPABILITY_ORDER[0]
            else "BLOCKED_BY_MISSING_BINDING"
        )
        assert (
            matrix[capability]["NORMAL_PIPELINE"]["status"]
            == expected_normal_status
        )


def test_oracle_and_explicit_local_results_are_exact_and_source_traced() -> None:
    report = _report()
    expected = {
        CAPABILITY_ORDER[0]: "60",
        CAPABILITY_ORDER[1]: "1",
        CAPABILITY_ORDER[2]: "4",
    }

    for capability in CAPABILITY_ORDER:
        oracle = report["activation_matrix"][capability]["ORACLE_RUNTIME"]
        explicit = report["activation_matrix"][capability]["EXPLICIT_C3_CALL"]
        assert oracle["answer"] == expected[capability]
        assert explicit["answer"] == expected[capability]
        assert oracle["trace"]
        assert oracle["source_lineage"]
        assert explicit["trace"]
        assert explicit["metadata"]["source_ref_count"] > 0
        assert explicit["metadata"]["explicit_c3_pipeline_product_request_wiring"] is False


def test_three_natural_questions_use_factory_workflow_and_separate_request_from_executor() -> None:
    report = _report()

    for capability in CAPABILITY_ORDER:
        probe = report["normal_pipeline_probes"][capability]
        assert probe["probe_mode"] == (
            "factory_build_workflow_with_post_build_local_retriever_override"
        )
        assert probe["factory_build_workflow_called"] is True
        assert probe["factory_build_workflow_call_count"] == 1
        assert probe["factory_workflow_class"] == "EnhancedBaselineWorkflow"
        assert probe["factory_solver_class"] == "RoutedSolver"
        assert probe["factory_calculation_solver_class"] == "CalculationSolver"
        assert set(probe["factory_solver_route_keys"]) == {
            "calculation",
            "cross_doc",
            "multi_choice",
        }
        assert probe["retriever_override"] == {
            "applied": True,
            "timing": "after_factory_build_workflow",
            "original_class": "LexicalHybridRetriever",
            "override_class": "_LocalEvidenceRetriever",
            "read_only_local_fixture": True,
        }
        assert probe["provider_clients_forced_none"] is True
        assert all(
            item["forced_none"] is True
            for item in probe["provider_client_overrides"].values()
        )
        assert probe["key_call_path"] == [
            "PipelineFactory.build_workflow",
            "EnhancedBaselineWorkflow.process_one",
            "RuleBasedQuestionClassifier.classify",
            "RoutedSolver.solve",
            "CalculationSolver.solve",
        ]
        assert not any("run.py" in item for item in probe["key_call_path"])
        assert "calculation" in probe["classification_labels"]
        assert probe["routed_solver"] == "calculation"
        assert probe["calculation_solver_entered"] is True
        assert "source_bound_request_created" not in probe
        assert probe["request_assembly_evidence"][
            "runtime_constructor_instrumentation"
        ] is True
        assert probe["provider_calls"] == 0
        assert probe["legacy_calls"] == 0
        assert probe["network_calls"] == 0
        assert probe["total_tokens"] == 0

        if capability == CAPABILITY_ORDER[0]:
            expected_hits = {
                CAPABILITY_ORDER[0]: 1,
                CAPABILITY_ORDER[1]: 0,
                CAPABILITY_ORDER[2]: 0,
            }
            assert probe["request_assembly_observed"] is True
            assert probe["request_assembly_hits"] == expected_hits
            assert probe["request_assembly_evidence"][
                "target_contract_constructor_hits"
            ] == 1
            assert probe["request_assembly_evidence"][
                "static_contract_symbol_present"
            ] is True
            assert probe["request_assembly_evidence"][
                "static_executor_symbol_present"
            ] is True
            assert probe["product_executor_invoked"] is True
            assert probe["product_executor_hits"] == expected_hits
            assert probe["answer"] == "60"
            assert probe["answer_source"] == "c3_source_bound_sum_series"
            assert probe["final_state"] == "accepted"
            assert probe["status"] == "ACTIVE"
            assert probe["trace"]
            assert probe["source_lineage"]
            assert probe["source_lineage_complete"] is True
            assert probe["blocking_module"] == ""
        else:
            expected_hits = {name: 0 for name in CAPABILITY_ORDER}
            assert probe["request_assembly_observed"] is False
            assert probe["request_assembly_hits"] == expected_hits
            assert probe["request_assembly_evidence"][
                "target_contract_constructor_hits"
            ] == 0
            assert probe["request_assembly_evidence"][
                "static_contract_symbol_present"
            ] is False
            assert probe["request_assembly_evidence"][
                "static_executor_symbol_present"
            ] is False
            assert probe["product_executor_invoked"] is False
            assert probe["product_executor_hits"] == expected_hits
            assert probe["answer_source"] == "error"
            assert probe["final_state"] == "blocked"
            assert probe["status"] == "BLOCKED_BY_MISSING_BINDING"
            assert probe["blocking_module"].startswith(
                "binding/evidence assembly"
            )


def test_factory_build_workflow_call_is_observed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PipelineFactory.build_workflow
    call_count = 0

    def tracked(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PipelineFactory, "build_workflow", tracked)

    report = _report()

    assert call_count == 1
    assert all(
        report["normal_pipeline_probes"][capability][
            "factory_build_workflow_called"
        ]
        is True
        for capability in CAPABILITY_ORDER
    )


def test_shadow_is_observational_and_blocks_before_explicit_pipeline() -> None:
    report = _report()

    expected_reasons = {
        CAPABILITY_ORDER[0]: ["candidate_scope_not_exactly_one"],
        CAPABILITY_ORDER[1]: ["c3_shadow_input_invalid"],
        CAPABILITY_ORDER[2]: ["c3_shadow_input_invalid"],
    }
    for capability in CAPABILITY_ORDER:
        shadow = report["activation_matrix"][capability]["SHADOW_OBSERVER"]
        assert shadow["state"] == "BLOCKED"
        assert shadow["reason_codes"] == expected_reasons[capability]
        assert shadow["pipeline_invoked"] is False
        assert shadow["request_assembly_observed"] is False
        assert shadow["request_assembly_hits"] == {
            name: 0 for name in CAPABILITY_ORDER
        }
        assert shadow["product_executor_invoked"] is False
        assert shadow["product_executor_hits"] == {
            name: 0 for name in CAPABILITY_ORDER
        }
        assert shadow["provider_calls"] == 0
        assert shadow["legacy_calls"] == 0
        assert shadow["network_calls"] == 0
        assert shadow["total_tokens"] == 0


def test_static_wiring_audits_request_contracts_and_executors_separately() -> None:
    report = _report()
    audit = report["source_wiring_audit"]

    assert audit["oracle_has_all_executor_symbols"] is True
    assert audit["oracle_has_all_request_contract_symbols"] is True
    assert audit["normal_chain_executor_symbols_present"] is True
    assert audit["normal_chain_request_contract_symbols_present"] is True
    assert audit["unexpected_normal_chain_executor_symbols_present"] is False
    assert audit[
        "unexpected_normal_chain_request_contract_symbols_present"
    ] is False
    assert audit["normal_wiring_matches_expectation"] is True
    assert audit["explicit_pipeline_executor_symbols_present"] is False
    assert audit["explicit_pipeline_request_contract_symbols_present"] is False
    assert audit["shadow_executor_symbols_present"] is False
    assert audit["shadow_request_contract_symbols_present"] is False

    expected = audit["expected_normal_wiring_by_capability"]
    assert expected[CAPABILITY_ORDER[0]] == {
        "expected_executor_paths": ["calculation_solver"],
        "observed_executor_paths": ["calculation_solver"],
        "executor_matches": True,
        "expected_request_contract_paths": ["calculation_solver"],
        "observed_request_contract_paths": ["calculation_solver"],
        "request_contract_matches": True,
    }
    for capability in CAPABILITY_ORDER[1:]:
        assert expected[capability] == {
            "expected_executor_paths": [],
            "observed_executor_paths": [],
            "executor_matches": True,
            "expected_request_contract_paths": [],
            "observed_request_contract_paths": [],
            "request_contract_matches": True,
        }

    for capability in CAPABILITY_ORDER:
        assert audit["executor_symbol_occurrences"]["oracle"][capability]
    for path_name in ("run", "factory", "workflow", "router"):
        for capability in CAPABILITY_ORDER:
            assert not audit["executor_symbol_occurrences"][path_name][capability]
            assert not audit["request_contract_symbol_occurrences"][path_name][capability]
    assert audit["executor_symbol_occurrences"]["calculation_solver"][
        CAPABILITY_ORDER[0]
    ]
    assert audit["request_contract_symbol_occurrences"]["calculation_solver"][
        CAPABILITY_ORDER[0]
    ]
    for capability in CAPABILITY_ORDER[1:]:
        assert not audit["executor_symbol_occurrences"]["calculation_solver"][
            capability
        ]
        assert not audit["request_contract_symbol_occurrences"][
            "calculation_solver"
        ][capability]


def test_remaining_operators_do_not_meet_continue_expansion_gate() -> None:
    report = _report()
    remaining = report["remaining_operator_summary"]

    assert remaining["total"] == 20
    assert remaining["failure_detail_counts"] == {
        "AMBIGUOUS_AGGREGATION_RANGE": 1,
        "LABEL_OUTPUT_NOT_SUPPORTED": 1,
        "answer_type:count": 13,
        "function_call": 5,
    }
    assert remaining["candidate_capability_counts"][
        "PERCENT_LITERAL_OPERATOR_NORMALIZATION"
    ] == 5
    assert remaining["max_qualified_product_family_size"] == 1
    assert remaining["product_family_with_at_least_five_qualified_cases"] is False
    assert remaining["qualified_unique_complete_product_family_counts"] == {
        "SOURCE_BOUND_TABLE_ARGMAX_LABEL": 1,
        "SOURCE_BOUND_TABLE_MISSING_VALUE_CARDINALITY": 1,
    }


def test_frozen_exit_rule_selects_one_next_layer_only() -> None:
    report = _report()
    rules = report["stage_rule_evaluation"]

    assert all(rules["exit_operator_expansion"].values())
    assert rules["exit_rule_satisfied"] is True
    assert rules["continue_rule_satisfied"] is False
    assert rules["continue_operator_expansion"] == {
        "all_three_normal_pipeline_active": False,
        "routing_binding_evidence_not_primary_blocker": False,
        "new_generic_family_at_least_five": False,
    }
    assert rules["derived_decision"] == "EXIT_OPERATOR_EXPANSION"
    assert report["stage_decision"] == rules["derived_decision"]
    assert report["recommended_next_layer"] == "BINDING_AND_EVIDENCE_ASSEMBLY"
    assert isinstance(report["recommended_next_layer"], str)
    assert report["recommended_next_layer"]


def test_validate_report_rejects_hardcoded_status_decision_and_next_layer() -> None:
    report = _report()

    wrong_status = deepcopy(report)
    wrong_status["activation_matrix"][CAPABILITY_ORDER[0]]["NORMAL_PIPELINE"][
        "status"
    ] = "BLOCKED_BY_MISSING_BINDING"
    with pytest.raises(ValueError, match="normal status not observation-derived"):
        validate_report(wrong_status)

    wrong_answer = deepcopy(report)
    wrong_answer["activation_matrix"][CAPABILITY_ORDER[0]]["NORMAL_PIPELINE"][
        "answer"
    ] = "61"
    with pytest.raises(ValueError, match="C3-M normal activation facts invalid"):
        validate_report(wrong_answer)

    wrong_audit = deepcopy(report)
    wrong_audit["source_wiring_audit"][
        "unexpected_normal_chain_executor_symbols_present"
    ] = True
    with pytest.raises(
        ValueError, match="capability-aware source wiring audit invalid"
    ):
        validate_report(wrong_audit)

    wrong_decision = deepcopy(report)
    wrong_decision["stage_decision"] = "CONTINUE_OPERATOR_EXPANSION"
    with pytest.raises(ValueError, match="stage decision does not match"):
        validate_report(wrong_decision)

    wrong_layer = deepcopy(report)
    wrong_layer["recommended_next_layer"] = "SOLVER_ROUTING"
    with pytest.raises(ValueError, match="recommended next layer does not match"):
        validate_report(wrong_layer)


def test_report_is_deterministic_and_cli_writes_machine_readable_output(
    tmp_path: Path,
) -> None:
    first = _report()
    second = _report()
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )

    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_c3_stage_exit.py",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "decision=EXIT_OPERATOR_EXPANSION" in completed.stdout
    written = json.loads(output.read_text(encoding="utf-8"))
    validate_report(written)
    assert written == first


def test_stage_audit_source_contains_no_benchmark_case_id_literal() -> None:
    source = (ROOT / "scripts/evaluate_c3_stage_exit.py").read_text(encoding="utf-8")
    uuid_pattern = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    )
    finqa_case_pattern = re.compile(r"[A-Z]{2,}/\d{4}/page_\d+\.pdf-\d+")

    assert not uuid_pattern.search(source)
    assert not finqa_case_pattern.search(source)
    assert "official answer" not in source.lower()
    assert "case_id ==" not in source


def test_zero_call_accounting_and_snapshot_metrics_are_preserved() -> None:
    report = _report()
    snapshot = report["historical_snapshot"]

    assert report["provider_calls"] == 0
    assert report["legacy_calls"] == 0
    assert report["network_calls"] == 0
    assert report["total_tokens"] == 0
    assert snapshot["measurement_valid"] is True
    assert snapshot["numeric_eligible"] == 1623
    assert snapshot["representable"] == 1602
    assert snapshot["correct"] == 1600
    assert snapshot["incorrect"] == 2
    assert snapshot["c3_errors"] == 0
    assert snapshot["remaining_unsupported_operator"] == 20
    assert snapshot["provider_calls"] == 0
    assert snapshot["legacy_calls"] == 0
    assert snapshot["network_calls"] == 0
    assert snapshot["total_tokens"] == 0
    assert all(
        len(item["sha256"]) == 64
        for item in snapshot["snapshot_files"].values()
    )
