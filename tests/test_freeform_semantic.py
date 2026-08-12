from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from evaluation.freeform_semantic import CONTENT_RECALL_THRESHOLD, score_freeform_semantic


REPO = Path(__file__).resolve().parents[1]
CHECKPOINT = REPO / "evaluation_artifacts/external_benchmarks/financebench/e4_real_v1_repair1/answer_ab_checkpoint.jsonl"
SCORER_SOURCE = REPO / "src/evaluation/freeform_semantic.py"
CORRECT_IDS = {"financebench_id_00941", "financebench_id_01858"}


def _rows() -> dict[str, dict[str, object]]:
    return {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in CHECKPOINT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def _prediction(row: dict[str, object]) -> str:
    return str((row.get("predicted_answers") or [""])[0])


def _gold(row: dict[str, object]) -> str:
    return str((row.get("gold_answers") or [""])[0])


def test_scorer_public_api_is_two_text_inputs_plus_fixed_threshold_only() -> None:
    signature = inspect.signature(score_freeform_semantic)
    assert list(signature.parameters) == [
        "predicted_answer",
        "gold_answer",
        "content_recall_threshold",
    ]
    assert signature.parameters["content_recall_threshold"].default == CONTENT_RECALL_THRESHOLD

    source = SCORER_SOURCE.read_text(encoding="utf-8")
    assert "financebench_id_" not in source
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(name=node.module or "")]
        )
    }
    assert imports.isdisjoint({"requests", "httpx", "openai", "anthropic", "sentence_transformers"})


def test_frozen_eight_case_semantic_oracle_agreement() -> None:
    rows = _rows()
    assert len(rows) == 8

    observed_correct: set[str] = set()
    for case_id, row in rows.items():
        result = score_freeform_semantic(_prediction(row), _gold(row))
        if result.semantic_correct:
            observed_correct.add(case_id)

    assert observed_correct == CORRECT_IDS


def test_two_semantic_correct_cases_have_full_anchor_and_content_recall() -> None:
    rows = _rows()
    for case_id in sorted(CORRECT_IDS):
        result = score_freeform_semantic(_prediction(rows[case_id]), _gold(rows[case_id]))
        assert result.semantic_correct is True
        assert result.refusal_detected is False
        assert result.protected_anchor_recall == 1.0
        assert result.content_token_recall is not None
        assert result.content_token_recall >= CONTENT_RECALL_THRESHOLD
        assert result.reason == "semantic_anchor_match"


def test_six_semantic_wrong_cases_fail_with_auditable_reason() -> None:
    rows = _rows()
    wrong_ids = set(rows) - CORRECT_IDS
    assert len(wrong_ids) == 6
    for case_id in sorted(wrong_ids):
        result = score_freeform_semantic(_prediction(rows[case_id]), _gold(rows[case_id]))
        assert result.semantic_correct is False
        assert result.reason in {
            "incompatible_refusal",
            "missing_protected_anchor",
            "missing_numeric_anchor",
            "insufficient_content_token_recall",
        }


def test_counterfactual_formatting_invariance_and_anchor_sensitivity() -> None:
    rows = _rows()
    notes = rows["financebench_id_00941"]
    dividend = rows["financebench_id_01858"]

    notes_prediction = _prediction(notes)
    notes_gold = _gold(notes)
    dividend_prediction = _prediction(dividend)
    dividend_gold = _gold(dividend)

    # Harmless case/punctuation/list-marker changes preserve the semantic result.
    formatting_variant = notes_prediction.upper().replace("- ", " • ").replace("\n", " ; ")
    assert score_freeform_semantic(formatting_variant, notes_gold).semantic_correct is True

    # A critical listed-security identifier mutation must fail.
    wrong_identifier = notes_prediction.replace("MMM31", "MMM32")
    wrong_identifier_result = score_freeform_semantic(wrong_identifier, notes_gold)
    assert wrong_identifier_result.semantic_correct is False
    assert wrong_identifier_result.reason == "missing_protected_anchor"

    # The dividend streak is a protected numeric fact; 65 -> 64 must fail.
    wrong_year_count = dividend_prediction.replace("65th", "64th")
    wrong_year_count_result = score_freeform_semantic(wrong_year_count, dividend_gold)
    assert wrong_year_count_result.semantic_correct is False
    assert wrong_year_count_result.reason == "missing_protected_anchor"

    # A refusal cannot pass merely by appending all Gold anchors afterward.
    refusal_variant = "Cannot confirm from the evidence. " + notes_prediction
    refusal_result = score_freeform_semantic(refusal_variant, notes_gold)
    assert refusal_result.semantic_correct is False
    assert refusal_result.reason == "incompatible_refusal"


def test_generic_overlap_without_protected_numeric_fact_stays_incorrect() -> None:
    row = _rows()["financebench_id_01865"]
    generic_overlap = "The consumer segment shrunk organically."
    result = score_freeform_semantic(generic_overlap, _gold(row))
    assert result.semantic_correct is False
    assert result.reason == "missing_protected_anchor"
    assert result.protected_anchor_recall == 0.0


def test_numeric_only_gold_uses_decimal_normalized_anchor_match() -> None:
    assert score_freeform_semantic("The value is $8.700 billion.", "$8.70").semantic_correct is True
    assert score_freeform_semantic("The value is $8.71 billion.", "$8.70").semantic_correct is False


def test_numeric_coupon_bullet_is_not_misread_as_negative_value() -> None:
    gold = "-1.500% Notes due 2026 (Trading Symbol: MMM26)"
    prediction = "1.500% Notes due 2026 (Trading Symbol MMM26)"
    result = score_freeform_semantic(prediction, gold)
    assert result.protected_anchor_recall == 1.0
    assert result.semantic_correct is True


def test_generic_contradiction_guard_rejects_negated_or_superseded_gold_anchor() -> None:
    cases = [
        (
            "Revenue was 65% higher.",
            "Revenue was not 65% higher.",
            "contradicted_protected_anchor_negation",
        ),
        (
            "The rate was 65%.",
            "The rate was 65%, corrected to 64%.",
            "contradicted_protected_anchor_correction",
        ),
        (
            "8.70",
            "The value is 8.70, but corrected to 8.71.",
            "contradicted_protected_anchor_correction",
        ),
        (
            "8.70",
            "The value is not 8.70; it is 8.71.",
            "contradicted_protected_anchor_negation",
        ),
        (
            "The symbol is MMM26.",
            "The symbol is not MMM26; it is MMM27.",
            "contradicted_protected_anchor_negation",
        ),
        (
            "Organic growth was 0.9%.",
            "Organic growth was initially 0.9%, revised to 1.1%.",
            "contradicted_protected_anchor_correction",
        ),
    ]
    for gold, predicted, expected_reason in cases:
        result = score_freeform_semantic(predicted, gold)
        assert result.semantic_correct is False
        assert result.reason == expected_reason
        assert result.protected_anchor_recall == 1.0


def test_generic_contradiction_guard_preserves_benign_extra_context() -> None:
    cases = [
        ("8.70", "The value is 8.70, compared with 8.50 last year."),
        ("Revenue was 65% higher.", "Revenue was 65% higher than the 50% benchmark."),
        ("The symbol is MMM26.", "The symbol is MMM26; the previous series was MMM25."),
        ("Revenue was 65% higher.", "Revenue was not only 65% higher, but also more stable."),
    ]
    for gold, predicted in cases:
        result = score_freeform_semantic(predicted, gold)
        assert result.semantic_correct is True
        assert result.reason in {"numeric_anchor_match", "semantic_anchor_match"}
