"""E3: reasoning and verification quality evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping


def _ratio(hit: int, total: int) -> float | None:
    return None if total == 0 else hit / total


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _normalize_formula(value: str) -> str:
    return "".join(str(value or "").split()).lower()


@dataclass(frozen=True)
class ReasoningGold:
    claim_verdicts: Mapping[str, str] = field(default_factory=dict)
    numeric_values: Mapping[str, str] = field(default_factory=dict)
    formulas: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasoningQualityResult:
    claim_accuracy: float | None
    numeric_accuracy: float | None
    formula_accuracy: float | None
    claim_correct: int
    claim_total: int
    numeric_correct: int
    numeric_total: int
    formula_correct: int
    formula_total: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "claim_accuracy": self.claim_accuracy,
            "numeric_accuracy": self.numeric_accuracy,
            "formula_accuracy": self.formula_accuracy,
            "claim_correct": self.claim_correct,
            "claim_total": self.claim_total,
            "numeric_correct": self.numeric_correct,
            "numeric_total": self.numeric_total,
            "formula_correct": self.formula_correct,
            "formula_total": self.formula_total,
        }


def evaluate_reasoning(
    *,
    predicted_claim_verdicts: Mapping[str, str] | None = None,
    predicted_numeric_values: Mapping[str, object] | None = None,
    predicted_formulas: Mapping[str, str] | None = None,
    gold: ReasoningGold,
    numeric_tolerance: Decimal | str | float = Decimal("0.000001"),
) -> ReasoningQualityResult:
    predicted_claim_verdicts = predicted_claim_verdicts or {}
    predicted_numeric_values = predicted_numeric_values or {}
    predicted_formulas = predicted_formulas or {}
    tolerance = _decimal(numeric_tolerance) or Decimal("0.000001")

    claim_correct = sum(
        1
        for key, expected in gold.claim_verdicts.items()
        if str(predicted_claim_verdicts.get(key, "")).strip().upper()
        == str(expected).strip().upper()
    )

    numeric_correct = 0
    for key, expected in gold.numeric_values.items():
        actual_decimal = _decimal(predicted_numeric_values.get(key))
        expected_decimal = _decimal(expected)
        if actual_decimal is None or expected_decimal is None:
            continue
        if abs(actual_decimal - expected_decimal) <= tolerance:
            numeric_correct += 1

    formula_correct = sum(
        1
        for key, expected in gold.formulas.items()
        if _normalize_formula(predicted_formulas.get(key, ""))
        == _normalize_formula(expected)
    )

    return ReasoningQualityResult(
        claim_accuracy=_ratio(claim_correct, len(gold.claim_verdicts)),
        numeric_accuracy=_ratio(numeric_correct, len(gold.numeric_values)),
        formula_accuracy=_ratio(formula_correct, len(gold.formulas)),
        claim_correct=claim_correct,
        claim_total=len(gold.claim_verdicts),
        numeric_correct=numeric_correct,
        numeric_total=len(gold.numeric_values),
        formula_correct=formula_correct,
        formula_total=len(gold.formulas),
    )
