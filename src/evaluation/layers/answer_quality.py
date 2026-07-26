"""E4: end-to-end answer quality metrics."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


def _normalize(value: object) -> str:
    return "".join(str(value or "").strip().split()).upper()


def _numeric(value: object) -> Decimal | None:
    raw = str(value or "").replace(",", "").replace("％", "%").strip()
    if raw.endswith("%"):
        raw = raw[:-1]
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _option_set(value: object) -> set[str] | None:
    normalized = _normalize(value)
    if normalized and all(ch in "ABCD" for ch in normalized):
        return set(normalized)
    return None


@dataclass(frozen=True)
class AnswerQualityResult:
    exact_match: float
    set_precision: float | None
    set_recall: float | None
    set_f1: float | None
    numeric_correct: float | None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "exact_match": self.exact_match,
            "set_precision": self.set_precision,
            "set_recall": self.set_recall,
            "set_f1": self.set_f1,
            "numeric_correct": self.numeric_correct,
        }


def evaluate_answer(
    predicted: object,
    gold: object,
    *,
    numeric_tolerance: Decimal | str | float = Decimal("0.000001"),
) -> AnswerQualityResult:
    predicted_norm = _normalize(predicted)
    gold_norm = _normalize(gold)
    exact_match = float(predicted_norm == gold_norm)

    predicted_set = _option_set(predicted)
    gold_set = _option_set(gold)
    precision = recall = f1 = None
    if predicted_set is not None and gold_set is not None:
        intersection = len(predicted_set & gold_set)
        precision = intersection / len(predicted_set) if predicted_set else 0.0
        recall = intersection / len(gold_set) if gold_set else 0.0
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    predicted_numeric = _numeric(predicted)
    gold_numeric = _numeric(gold)
    numeric_correct = None
    if predicted_numeric is not None and gold_numeric is not None:
        tolerance = _numeric(numeric_tolerance) or Decimal("0.000001")
        numeric_correct = float(abs(predicted_numeric - gold_numeric) <= tolerance)

    return AnswerQualityResult(
        exact_match=exact_match,
        set_precision=precision,
        set_recall=recall,
        set_f1=f1,
        numeric_correct=numeric_correct,
    )
