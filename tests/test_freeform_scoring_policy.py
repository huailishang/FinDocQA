from __future__ import annotations

import inspect
import json
from pathlib import Path

from evaluation.freeform_scoring_policy import (
    AUTO_CORRECT,
    AUTO_INCORRECT,
    SEMANTIC_REVIEW_REQUIRED,
    route_freeform_scoring_policy,
)


REPO = Path(__file__).resolve().parents[1]
CHECKPOINT = REPO / "evaluation_artifacts/external_benchmarks/financebench/e4_real_v1_repair1/answer_ab_checkpoint.jsonl"
POLICY_SOURCE = REPO / "src/evaluation/freeform_scoring_policy.py"

EXPECTED_REAL_ROUTES = {
    "financebench_id_03029": AUTO_INCORRECT,
    "financebench_id_04672": AUTO_INCORRECT,
    "financebench_id_00499": AUTO_INCORRECT,
    "financebench_id_01226": AUTO_INCORRECT,
    "financebench_id_01865": AUTO_INCORRECT,
    "financebench_id_00807": AUTO_INCORRECT,
    "financebench_id_00941": SEMANTIC_REVIEW_REQUIRED,
    "financebench_id_01858": SEMANTIC_REVIEW_REQUIRED,
}

FROZEN_CONTRADICTION_PROBES = [
    ("Revenue was 65% higher.", "Revenue was not 65% higher."),
    ("The rate was 65%.", "The rate was 65%, corrected to 64%."),
    ("8.70", "The value is 8.70, but corrected to 8.71."),
    ("8.70", "The value is not 8.70; it is 8.71."),
    ("The symbol is MMM26.", "The symbol is not MMM26; it is MMM27."),
    ("Organic growth was 0.9%.", "Organic growth was initially 0.9%, revised to 1.1%."),
]

UNSEEN_GENERALIZATION_PROBES = [
    ("Revenue was 65% higher.", "Revenue was 65% higher, but that figure was wrong; 64% is correct."),
    ("Revenue was 65% higher.", "Revenue was 65% higher initially; the actual figure is 64%."),
    ("Revenue was 65% higher.", "Revenue was not 65% higher but 64% higher."),
    ("Revenue was 65% higher.", "65% was reported in error; the correct figure is 64%."),
    ("8.70", "8.70 is incorrect; the correct value is 8.71."),
    ("8.70", "The value should be 8.71, not 8.70."),
    ("The symbol is MMM26.", "MMM26 was the old code; the current code is MMM27."),
    ("Revenue was 65% higher.", "Revenue was 65% higher rather than 64%."),
]

BENIGN_FREEFORM_CONTROLS = [
    ("8.70", "The value is 8.70, compared with 8.50 last year."),
    ("Revenue was 65% higher.", "Revenue was 65% higher than the 50% benchmark."),
    ("The symbol is MMM26.", "The symbol is MMM26; the previous series was MMM25."),
    ("Revenue was 65% higher.", "Revenue was not only 65% higher, but also more stable."),
]

CANNOT_MODALITY_CONTROLS = [
    ("The maximum permitted value is 8.70.", "The value cannot exceed 8.70."),
    ("The minimum threshold is 8.70.", "The value cannot fall below 8.70."),
    (
        "The company is prohibited from calculating the ratio after termination.",
        "The company cannot calculate the ratio after termination.",
    ),
    ("The notes may not be redeemed before 2026.", "The notes cannot be redeemed before 2026."),
]

ANSWERABILITY_REFUSAL_CONTROLS = [
    "无法从现有证据确认。",
    "证据不足，无法确定该数值。",
    "无法从现有证据计算所需比率。",
    "Cannot confirm the requested value from the provided evidence.",
    "I cannot determine the answer from the available information.",
    "Insufficient evidence to calculate the requested ratio.",
]


def _checkpoint_rows() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _first_answer(row: dict[str, object], field: str) -> str:
    values = row.get(field) or []
    assert isinstance(values, list) and values
    return str(values[0])


def test_public_api_is_prediction_and_gold_only_with_required_route_schema() -> None:
    assert list(inspect.signature(route_freeform_scoring_policy).parameters) == [
        "predicted_answer",
        "gold_answer",
    ]
    result = route_freeform_scoring_policy("8.70", "8.70")
    assert result.to_dict().keys() >= {
        "route",
        "reason_code",
        "exact_match_signal",
        "refusal_signal",
    }

    source = POLICY_SOURCE.read_text(encoding="utf-8")
    assert "financebench_id_" not in source
    for forbidden in ("qid", "question", "retrieval", "provider", "evaluator_label"):
        assert forbidden not in source.casefold()


def test_normalized_nonempty_exact_equality_is_the_only_auto_correct_path() -> None:
    exact = route_freeform_scoring_policy("  Revenue\tWAS 65% Higher. ", "Revenue was 65% higher.")
    assert exact.route == AUTO_CORRECT
    assert exact.reason_code == "normalized_exact_equality"
    assert exact.exact_match_signal is True

    empty = route_freeform_scoring_policy("", "")
    assert empty.route == SEMANTIC_REVIEW_REQUIRED
    assert empty.exact_match_signal is False

    descriptive = route_freeform_scoring_policy(
        "Revenue was 65% higher than the 50% benchmark.",
        "Revenue was 65% higher.",
    )
    assert descriptive.route == SEMANTIC_REVIEW_REQUIRED
    assert descriptive.exact_match_signal is False


def test_explicit_answerability_refusals_are_auto_incorrect() -> None:
    for predicted in ANSWERABILITY_REFUSAL_CONTROLS:
        result = route_freeform_scoring_policy(predicted, "8.70")
        assert result.route == AUTO_INCORRECT, predicted
        assert result.reason_code == "incompatible_explicit_refusal"
        assert result.refusal_signal is True
        assert result.gold_refusal_signal is False


def test_bare_cannot_financial_and_contract_modality_is_not_refusal_authority() -> None:
    for gold, predicted in CANNOT_MODALITY_CONTROLS:
        result = route_freeform_scoring_policy(predicted, gold)
        assert result.route == SEMANTIC_REVIEW_REQUIRED, predicted
        assert result.refusal_signal is False

    source = inspect.getsource(route_freeform_scoring_policy)
    assert "score_freeform_semantic" not in source
    assert ".refusal_detected" not in source


def test_real_eight_case_shadow_routes_match_frozen_policy() -> None:
    rows = _checkpoint_rows()
    assert len(rows) == 8
    observed = {
        str(row["case_id"]): route_freeform_scoring_policy(
            _first_answer(row, "predicted_answers"),
            _first_answer(row, "gold_answers"),
        ).route
        for row in rows
    }
    assert observed == EXPECTED_REAL_ROUTES
    assert list(observed.values()).count(AUTO_INCORRECT) == 6
    assert list(observed.values()).count(AUTO_CORRECT) == 0
    assert list(observed.values()).count(SEMANTIC_REVIEW_REQUIRED) == 2


def test_complex_contradiction_and_generalization_probes_never_auto_correct() -> None:
    for gold, predicted in FROZEN_CONTRADICTION_PROBES + UNSEEN_GENERALIZATION_PROBES:
        result = route_freeform_scoring_policy(predicted, gold)
        assert result.route != AUTO_CORRECT
        assert result.route == SEMANTIC_REVIEW_REQUIRED


def test_benign_freeform_controls_abstain_without_false_auto_incorrect() -> None:
    for gold, predicted in BENIGN_FREEFORM_CONTROLS:
        result = route_freeform_scoring_policy(predicted, gold)
        assert result.route == SEMANTIC_REVIEW_REQUIRED
        assert result.route != AUTO_INCORRECT
