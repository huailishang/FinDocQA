"""Official or byte-equivalent native scoring for emitted Oracle predictions."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import importlib.util
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.external_benchmarks.contracts import ExternalCaseRecord, OracleCase


def _finqa_number(raw: str) -> float:
    text = str(raw).replace(",", "").strip()
    if text.startswith("const_"):
        text = text[len("const_") :]
        if text == "m1":
            text = "-1"
    if text.endswith("%"):
        return float(text[:-1]) / 100.0
    return float(text)


def finqa_internal_eval_program(program: str) -> float:
    """Byte-equivalent arithmetic subset of FinQA's official eval_program."""
    from evaluation.external_benchmarks.finqa_adapter import _parse_steps

    results: list[float] = []
    current = 0.0
    for index, (operator, left_raw, right_raw) in enumerate(_parse_steps(program)):
        def resolve(raw: str) -> float:
            if raw.startswith("#"):
                ref = int(raw[1:])
                if ref < 0 or ref >= index:
                    raise ValueError(f"invalid reference:{raw}")
                return results[ref]
            return _finqa_number(raw)

        left = resolve(left_raw)
        right = resolve(right_raw)
        if operator == "add":
            current = left + right
        elif operator == "subtract":
            current = left - right
        elif operator == "multiply":
            current = left * right
        elif operator == "divide":
            current = left / right
        elif operator == "exp":
            current = left ** right
        else:
            raise ValueError(f"unsupported internal FinQA operator:{operator}")
        results.append(current)
    return round(current, 5)


def _load_finqa_official_scorer(path: str | Path):
    scorer_path = Path(path)
    spec = importlib.util.spec_from_file_location("finqa_official_evaluate_c3k", scorer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load FinQA scorer:{scorer_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_finqa_numeric(raw: Any) -> float | None:
    """Normalize a FinQA emitted number to the official five-decimal boundary."""
    text = str(raw).replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, 5)


def score_finqa_predictions(
    cases: Sequence[OracleCase],
    records: Sequence[ExternalCaseRecord],
    *,
    scorer_path: str | Path,
) -> Mapping[str, Any]:
    """Score actual C3 predictions against official and independent expectations."""
    official = _load_finqa_official_scorer(scorer_path)
    case_by_id = {case.case_id: case for case in cases}
    selected = [
        record
        for record in records
        if record.dataset == "finqa" and record.native_prediction_emitted
    ]
    official_correct = 0
    internal_correct = 0
    output_mismatch = 0
    invalid_prediction_count = 0
    for record in selected:
        case = case_by_id[record.case_id]
        if case.runtime is None:
            raise AssertionError("native FinQA prediction requires runtime")
        tokens = official.program_tokenization(case.runtime.native_program)
        invalid, official_value = official.eval_program(
            tokens, case.label.native_context.get("table") or []
        )
        internal_value = finqa_internal_eval_program(case.runtime.native_program)
        if invalid != 0:
            raise AssertionError(f"representable FinQA prediction invalid:{record.case_id}")
        official_expected = _normalize_finqa_numeric(official_value)
        internal_expected = _normalize_finqa_numeric(internal_value)
        if official_expected is None or internal_expected is None:
            raise AssertionError(f"non-numeric FinQA expected value:{record.case_id}")
        output_mismatch += int(official_expected != internal_expected)
        predicted = _normalize_finqa_numeric(record.predicted_answer)
        if predicted is None:
            invalid_prediction_count += 1
            continue
        official_correct += int(predicted == official_expected)
        internal_correct += int(predicted == internal_expected)
    denominator = len(selected)
    official_score = official_correct / denominator if denominator else 0.0
    internal_score = internal_correct / denominator if denominator else 0.0
    return {
        "prediction_count": denominator,
        "native_correct_count": official_correct,
        "internal_equivalent_correct_count": internal_correct,
        "invalid_prediction_count": invalid_prediction_count,
        "native_score": official_score,
        "internal_equivalent_score": internal_score,
        "parity_delta": abs(official_score - internal_score),
        "per_prediction_output_mismatch_count": output_mismatch,
    }


_SCALE_FACTORS = {
    "": Decimal("1"),
    "thousand": Decimal("1000"),
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
    "percent": Decimal("0.01"),
}


def _tatqa_official_extracted_string(value: Any, scale: str) -> str:
    """Exact arithmetic branch extracted from official get_answer_str."""
    number = float(str(value))
    factor = float(_SCALE_FACTORS[scale])
    return "%.4f" % (round(number, 2) * factor)


def _tatqa_internal_string(value: Any, scale: str) -> str:
    """Independent Decimal implementation of the same released semantics."""
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid TAT-QA numeric answer:{value}") from exc
    rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    normalized = rounded * _SCALE_FACTORS[scale]
    return f"{normalized:.4f}"


def score_tatqa_predictions(
    cases: Sequence[OracleCase],
    records: Sequence[ExternalCaseRecord],
) -> Mapping[str, Any]:
    """Score arithmetic predictions through released TAT-QA normalization semantics."""
    case_by_id = {case.case_id: case for case in cases}
    selected = [
        record
        for record in records
        if record.dataset == "tatqa" and record.native_prediction_emitted
    ]
    native_correct = 0
    internal_correct = 0
    output_mismatch = 0
    for record in selected:
        case = case_by_id[record.case_id]
        native_prediction = _tatqa_official_extracted_string(record.predicted_answer, record.scale)
        native_gold = _tatqa_official_extracted_string(case.label.answer, case.label.scale)
        internal_prediction = _tatqa_internal_string(record.predicted_answer, record.scale)
        internal_gold = _tatqa_internal_string(case.label.answer, case.label.scale)
        native_correct += int(native_prediction == native_gold)
        internal_correct += int(internal_prediction == internal_gold)
        output_mismatch += int(
            native_prediction != internal_prediction or native_gold != internal_gold
        )
    denominator = len(selected)
    native_score = native_correct / denominator if denominator else 0.0
    internal_score = internal_correct / denominator if denominator else 0.0
    return {
        "prediction_count": denominator,
        "native_correct_count": native_correct,
        "internal_equivalent_correct_count": internal_correct,
        "invalid_prediction_count": 0,
        "native_score": native_score,
        "internal_equivalent_score": internal_score,
        "parity_delta": abs(native_score - internal_score),
        "per_prediction_output_mismatch_count": output_mismatch,
        "native_semantics": "official_tatqa_arithmetic_get_answer_str_extract",
    }


__all__ = [
    "finqa_internal_eval_program",
    "score_finqa_predictions",
    "score_tatqa_predictions",
]
