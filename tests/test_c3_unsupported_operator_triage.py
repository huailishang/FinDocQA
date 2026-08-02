from __future__ import annotations

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path

import pytest

from evaluation.external_benchmarks.unsupported_operator_triage import (
    TriageError,
    _period_predicate_oracle,
    build_capability_decision,
    build_triage,
    rank_candidates,
    select_capability,
    write_triage_outputs,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_RECORDS = ROOT / "evaluation_artifacts/c3_external_oracle_baseline_v1/per_case_records.jsonl"
TATQA_DEV = ROOT / "evaluation_artifacts/external_benchmarks/tatqa/dataset_raw/tatqa_dataset_dev.json"
OUTPUT_NAMES = (
    "per_case_taxonomy.jsonl",
    "aggregate_taxonomy.json",
    "capability_decision.json",
    "report.md",
)
REQUIRED_ROW_FIELDS = {
    "dataset",
    "case_id",
    "baseline_failure_detail",
    "official_answer_type",
    "official_program_or_derivation_shape",
    "semantic_family",
    "required_operation",
    "required_input_shape",
    "required_source_binding",
    "current_blocking_boundary",
    "existing_contract_representable",
    "exact_oracle_available",
    "generic_financial_document_value",
    "minimum_product_surface",
    "estimated_recoverable_case_count",
    "candidate_capability",
    "candidate_type",
    "binding_uniqueness_status",
    "oracle_proof",
    "selection_eligibility",
    "selection_exclusion_reason",
    "source_lineage",
}
MANDATORY_FAIL_CLOSED = {
    "e14c60c4-3460-42eb-8d21-c29abdbce3a8",
    "ef93f805-b103-4424-b30f-ba194d27102e",
    "1f781a60-f0df-4a3a-861c-71b8eaaf379c",
    "f3eb9649-910b-400e-98a9-70c0591d3072",
}


@pytest.fixture(scope="module")
def triage() -> tuple[list[dict], dict, dict, str]:
    return build_triage(root=ROOT)


def _proof_count(proof: dict) -> int:
    coordinates = proof["bound_member_or_value_coordinates"]
    rule = proof["predicate_or_membership_rule"]
    rule_type = rule["rule_type"]
    if rule_type == "SCALAR_PREDICATE_CARDINALITY":
        threshold = Decimal(str(rule["threshold_in_source_units"]))
        if rule["operator"] == ">":
            return sum(Decimal(item["numeric_value"]) > threshold for item in coordinates)
        return sum(Decimal(item["numeric_value"]) < threshold for item in coordinates)
    if rule_type in {"SECTION_MEMBER_CARDINALITY", "WHOLE_TABLE_ENTITY_CARDINALITY"}:
        return len(coordinates)
    if rule_type == "PRESENT_IN_FIRST_PERIOD_AND_MISSING_IN_SECOND":
        return sum(bool(item["predicate_match"]) for item in coordinates)
    raise AssertionError(f"unexpected cardinality rule: {rule_type}")


def _tatqa_count_answers() -> dict[str, int]:
    payload = json.loads(TATQA_DEV.read_text(encoding="utf-8"))
    return {
        question["uid"]: int(question["answer"])
        for document in payload
        for question in document["questions"]
        if question["answer_type"] == "count"
    }


def test_complete_taxonomy_matches_frozen_population(triage) -> None:
    rows, aggregate, _decision, _report = triage

    assert len(rows) == 72
    assert len({row["case_id"] for row in rows}) == 72
    assert aggregate["dataset_totals"] == {"finqa": 35, "tatqa": 37}
    assert aggregate["semantic_family_totals"] == {
        "FINQA_TABLE_AGGREGATION": 35,
        "TATQA_COUNT_CARDINALITY": 32,
        "TATQA_FUNCTION_DERIVATION": 5,
    }
    assert aggregate["failure_detail_totals"] == {
        "answer_type:count": 32,
        "function_call": 5,
        "operators:table_average": 18,
        "operators:table_average,table_max": 1,
        "operators:table_max": 7,
        "operators:table_min": 5,
        "operators:table_sum": 4,
    }


def test_every_row_has_contract_fields_and_source_lineage(triage) -> None:
    rows, aggregate, _decision, _report = triage

    assert aggregate["candidate_type_totals"] == {
        "INELIGIBLE_COMPOSITE_OR_AMBIGUOUS": 3,
        "MEASUREMENT_ADAPTER_REPAIR": 5,
        "PRODUCT_CAPABILITY": 64,
    }
    for row in rows:
        assert REQUIRED_ROW_FIELDS <= row.keys()
        assert row["candidate_type"] in {
            "PRODUCT_CAPABILITY",
            "MEASUREMENT_ADAPTER_REPAIR",
            "INELIGIBLE_COMPOSITE_OR_AMBIGUOUS",
        }
        assert row["binding_uniqueness_status"] in {"UNIQUE", "AMBIGUOUS", "UNBOUND"}
        lineage = row["source_lineage"]
        assert lineage["baseline_records_path"].endswith("per_case_records.jsonl")
        assert isinstance(lineage["baseline_line_number"], int)
        assert lineage["official_split_sha256"]
        assert lineage["official_repository_commit"]
        assert row["existing_contract_representable"] is False


def test_semantic_families_remain_distinct(triage) -> None:
    rows, aggregate, _decision, _report = triage

    subfamilies = aggregate["semantic_subfamily_totals"]
    assert subfamilies["TABLE_ARGMAX_LABEL"] == 1
    assert subfamilies["TABLE_SUM_NUMERIC"] == 4
    assert subfamilies["TABLE_PERIOD_PREDICATE_CARDINALITY"] == 18
    assert subfamilies["TABLE_CATEGORY_PREDICATE_CARDINALITY"] == 3
    assert subfamilies["TABLE_SECTION_CARDINALITY"] == 7
    assert subfamilies["TABLE_MISSING_VALUE_CARDINALITY"] == 1
    assert subfamilies["TEXT_ENUMERATION_CARDINALITY"] == 2
    assert subfamilies["COMPOSITE_SECTION_AGGREGATE_PREDICATE_CARDINALITY"] == 1
    assert subfamilies["PERCENT_SERIES_AVERAGE_NORMALIZATION"] == 2
    assert subfamilies["PERCENTAGE_POINT_DIFFERENCE_NORMALIZATION"] == 3
    assert Counter(row["semantic_family"] for row in rows) == Counter(
        {
            "FINQA_TABLE_AGGREGATION": 35,
            "TATQA_COUNT_CARDINALITY": 32,
            "TATQA_FUNCTION_DERIVATION": 5,
        }
    )


def test_every_eligible_cardinality_case_has_complete_unique_oracle_proof(triage) -> None:
    rows, aggregate, _decision, _report = triage
    answers = _tatqa_count_answers()
    eligible = [
        row
        for row in rows
        if row["semantic_family"] == "TATQA_COUNT_CARDINALITY"
        and row["selection_eligibility"]
    ]

    assert len(eligible) == 20
    for row in eligible:
        proof = row["oracle_proof"]
        assert row["candidate_type"] == "PRODUCT_CAPABILITY"
        assert row["binding_uniqueness_status"] == "UNIQUE"
        assert row["exact_oracle_available"] is True
        assert proof["proof_status"] == "COMPLETE"
        assert proof["binding_uniqueness_status"] == "UNIQUE"
        assert proof["bound_source_object_ids"]
        assert proof["bound_axis_or_section"]
        assert proof["bound_member_or_value_coordinates"]
        assert proof["predicate_or_membership_rule"]
        assert isinstance(proof["independently_derived_expected_count"], int)
        assert _proof_count(proof) == proof["independently_derived_expected_count"]
        assert proof["independently_derived_expected_count"] == answers[row["case_id"]]

    assert aggregate["eligible_product_case_count"] == 54


def test_mandatory_counterexamples_fail_closed(triage) -> None:
    rows, _aggregate, _decision, _report = triage
    by_id = {row["case_id"]: row for row in rows}

    assert MANDATORY_FAIL_CLOSED <= by_id.keys()
    for case_id in MANDATORY_FAIL_CLOSED:
        row = by_id[case_id]
        proof = row["oracle_proof"]
        assert row["selection_eligibility"] is False
        assert row["exact_oracle_available"] is False
        assert row["binding_uniqueness_status"] in {"AMBIGUOUS", "UNBOUND"}
        assert proof["proof_status"] == "INCOMPLETE"
        assert proof["independently_derived_expected_count"] is None
        assert proof["failure_reason"]

    assert "multiple equally specific" in by_id["e14c60c4-3460-42eb-8d21-c29abdbce3a8"]["selection_exclusion_reason"]
    assert "multiple generic Total rows" in by_id["ef93f805-b103-4424-b30f-ba194d27102e"]["selection_exclusion_reason"]
    assert "no unique numeric metric row" in by_id["1f781a60-f0df-4a3a-861c-71b8eaaf379c"]["selection_exclusion_reason"]
    assert "mixed metric columns" in by_id["f3eb9649-910b-400e-98a9-70c0591d3072"]["selection_exclusion_reason"]


def test_zero_row_duplicate_row_and_mixed_columns_fail_closed() -> None:
    zero = _period_predicate_oracle(
        table_uid="zero",
        table=[["", "2019", "2018"], ["Unrelated metric", "2", "3"]],
        question="How many years did target metric exceed 1 million?",
        context_text="table in millions",
    )
    duplicate = _period_predicate_oracle(
        table_uid="duplicate",
        table=[
            ["", "2019", "2018"],
            ["Basic earnings per share", "0.35", "0.22"],
            ["Basic earnings per share", "0.31", "0.53"],
        ],
        question="How many years did basic earnings per share exceed 0.30?",
        context_text="per share data",
    )
    mixed = _period_predicate_oracle(
        table_uid="mixed",
        table=[
            ["", "2019", "% of total", "2018", "% of total"],
            ["EMEA", "315535", "22.8%", "277898", "23.1%"],
        ],
        question="How many years did EMEA exceed 20%?",
        context_text="dollars in thousands",
    )

    for proof in (zero, duplicate, mixed):
        assert proof["proof_status"] == "INCOMPLETE"
        assert proof["binding_uniqueness_status"] in {"AMBIGUOUS", "UNBOUND"}
        assert proof["independently_derived_expected_count"] is None
        assert proof["failure_reason"]


def test_ambiguous_finqa_aggregation_range_fails_closed(triage) -> None:
    rows, _aggregate, _decision, _report = triage
    ambiguous = [
        row
        for row in rows
        if row["diagnostics"].get("contains_precomputed_total_and_components")
    ]

    assert len(ambiguous) == 1
    row = ambiguous[0]
    assert row["semantic_subfamily"] == "TABLE_SUM_NUMERIC"
    assert row["selection_eligibility"] is False
    assert row["binding_uniqueness_status"] == "AMBIGUOUS"
    assert row["oracle_proof"]["proof_status"] == "INCOMPLETE"
    assert "precomputed total" in row["selection_exclusion_reason"]


def test_product_ranking_and_upper_bound_are_recomputed(triage) -> None:
    _rows, aggregate, decision, report = triage

    assert decision["selection_rule_scope"] == "PRODUCT_CAPABILITY_ONLY"
    assert decision["selected_capability"] == "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
    assert decision["selected_candidate_type"] == "PRODUCT_CAPABILITY"
    assert decision["projected_recoverable_case_count"] == 33
    assert decision["projected_combined_representable_count"] == 1583
    upper = decision["projected_combined_effective_oracle_accuracy_upper_bound"]
    assert upper["numerator"] == 1581
    assert upper["denominator"] == 1623
    assert upper["value"] == pytest.approx(1581 / 1623)

    trace = decision["selection_rule_trace"]
    assert all(item["candidate_type"] == "PRODUCT_CAPABILITY" for item in trace)
    assert trace[0]["candidate_name"] == decision["selected_capability"]
    assert trace[0]["rank_key"] == [
        -33,
        3,
        1,
        1,
        "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION",
    ]
    assert aggregate["selection_eligible_totals"] == {
        "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION": 33,
        "SOURCE_BOUND_TABLE_ARGMAX_LABEL": 1,
        "SOURCE_BOUND_TABLE_MISSING_VALUE_CARDINALITY": 1,
        "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY": 16,
        "SOURCE_BOUND_TABLE_SECTION_CARDINALITY": 3,
    }
    assert "upper bound only" in report.lower()
    assert decision["active_route_authority"] is False
    assert decision["shadow_promotion_authority"] is False
    assert decision["production_correctness_authority"] is False


def test_percent_normalizer_is_measurement_repair_not_product_candidate(triage) -> None:
    rows, aggregate, decision, report = triage
    function_rows = [
        row for row in rows if row["semantic_family"] == "TATQA_FUNCTION_DERIVATION"
    ]

    assert len(function_rows) == 5
    assert all(row["candidate_capability"] == "PERCENT_LITERAL_OPERATOR_NORMALIZATION" for row in function_rows)
    assert all(row["candidate_type"] == "MEASUREMENT_ADAPTER_REPAIR" for row in function_rows)
    assert all(row["selection_eligibility"] is False for row in function_rows)
    assert all(row["binding_uniqueness_status"] == "UNIQUE" for row in function_rows)
    assert aggregate["measurement_adapter_repair_summary"] == {
        "candidate_count": 1,
        "case_count": 5,
        "candidates": {"PERCENT_LITERAL_OPERATOR_NORMALIZATION": 5},
        "excluded_from_product_ranking": True,
    }
    assert decision["measurement_adapter_repair_opportunities"][0]["case_count"] == 5
    assert all(item["candidate_name"] != "PERCENT_LITERAL_OPERATOR_NORMALIZATION" for item in decision["selection_rule_trace"])
    assert "excluded" in report.lower()


def test_missing_and_duplicate_frozen_cases_fail_closed(tmp_path: Path) -> None:
    original = [json.loads(line) for line in BASELINE_RECORDS.read_text(encoding="utf-8").splitlines()]
    unsupported_indices = [
        index
        for index, row in enumerate(original)
        if row["terminal_classification"] == "UNSUPPORTED_OPERATOR"
    ]

    missing_rows = [row for index, row in enumerate(original) if index != unsupported_indices[0]]
    missing_path = tmp_path / "missing.jsonl"
    missing_path.write_text("".join(json.dumps(row) + "\n" for row in missing_rows), encoding="utf-8")
    with pytest.raises(TriageError, match="Expected 72 unsupported cases"):
        build_triage(root=ROOT, baseline_records_path=missing_path)

    duplicate_rows = [dict(row) for row in original]
    first, second = unsupported_indices[:2]
    duplicate_rows[second]["case_id"] = duplicate_rows[first]["case_id"]
    duplicate_path = tmp_path / "duplicate.jsonl"
    duplicate_path.write_text("".join(json.dumps(row) + "\n" for row in duplicate_rows), encoding="utf-8")
    with pytest.raises(TriageError, match="Duplicate unsupported case IDs"):
        build_triage(root=ROOT, baseline_records_path=duplicate_path)


def test_ranking_tie_uses_stable_lexical_name_and_excludes_measurement() -> None:
    candidates = [
        {"candidate_name": "BETA", "candidate_type": "PRODUCT_CAPABILITY", "eligible": True, "rank_key": [-4, 2, 1, 1, "BETA"]},
        {"candidate_name": "ALPHA", "candidate_type": "PRODUCT_CAPABILITY", "eligible": True, "rank_key": [-4, 2, 1, 1, "ALPHA"]},
        {"candidate_name": "MORE_CASES", "candidate_type": "PRODUCT_CAPABILITY", "eligible": True, "rank_key": [-5, 9, 9, 9, "MORE_CASES"]},
        {"candidate_name": "MEASUREMENT", "candidate_type": "MEASUREMENT_ADAPTER_REPAIR", "eligible": True, "rank_key": [-99, 0, 0, 0, "MEASUREMENT"]},
    ]

    ranked = rank_candidates(candidates)
    assert [item["candidate_name"] for item in ranked] == ["MORE_CASES", "ALPHA", "BETA"]
    assert select_capability(candidates)["candidate_name"] == "MORE_CASES"


def test_no_eligible_product_candidate_emits_explicit_decision() -> None:
    candidates = [
        {
            "candidate_name": "A",
            "candidate_type": "PRODUCT_CAPABILITY",
            "eligible": False,
            "projected_recoverable_case_count": 0,
            "rank_key": [0, 1, 1, 1, "A"],
            "failed_rules": ["exact_deterministic_oracle"],
            "measurement_repair_case_count": 0,
            "measurement_repair_case_ids": [],
        },
        {
            "candidate_name": "M",
            "candidate_type": "MEASUREMENT_ADAPTER_REPAIR",
            "eligible": False,
            "projected_recoverable_case_count": 0,
            "rank_key": [0, 1, 1, 1, "M"],
            "failed_rules": ["candidate_type_is_product_capability"],
            "measurement_repair_case_count": 2,
            "measurement_repair_case_ids": ["m1", "m2"],
        },
    ]
    baseline = {
        "datasets": {
            "combined": {
                "c3_representable_count": 1550,
                "terminal_executed_correct_count": 1548,
                "numeric_eligible_count": 1623,
            }
        }
    }

    decision = build_capability_decision(candidates=candidates, aggregate_report=baseline)
    assert decision["selected_capability"] == "NO_ELIGIBLE_CAPABILITY"
    assert decision["selected_candidate_type"] is None
    assert decision["projected_recoverable_case_count"] == 0
    assert decision["projected_combined_representable_count"] == 1550
    assert decision["measurement_adapter_repair_opportunities"][0]["case_count"] == 2


def test_output_files_are_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_triage_outputs(root=ROOT, output_dir=first)
    write_triage_outputs(root=ROOT, output_dir=second)

    for name in OUTPUT_NAMES:
        assert (first / name).read_bytes() == (second / name).read_bytes()
