"""Strict structured parsing and formatting for multi-slot freeform answer slots."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from answer_contract import contract_from_answer_format, validate_answer_against_contract

_ALLOWED_KINDS = {"number", "percentage", "percentage_point", "date", "ordering", "text"}
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
_DATE_NUMERIC_RE = re.compile(r"^(\d{4})([-/])(\d{1,2})\2(\d{1,2})$")
_DATE_CHINESE_RE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")
_NUMBER_RE = re.compile(
    r"^[+-]?(?:(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?|\.\d+)$"
)
_TWO_DECIMAL_RE = re.compile(
    r"(?:保留|四舍五入(?:至|到)?|精确到?)\s*(?:小数点后)?\s*两位|两位小数"
)
_ARABIC_SCALE_RE = re.compile(r"(?:保留|四舍五入(?:至|到)?|精确到?)?\s*(\d+)\s*位小数")
_CHINESE_SCALE_RE = re.compile(r"(?:保留|四舍五入(?:至|到)?|精确到?)?\s*([零一二三四五六])\s*位小数")
_CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


@dataclass(frozen=True)
class KindValidationResult:
    valid: bool
    reason: str
    normalized: str
    kind: str
    numeric_value: str | None = None
    decimal_places: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreeformSubmissionParse:
    valid: bool
    reason: str
    answers: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    answer_items: tuple[Mapping[str, Any], ...] = ()
    slot_validations: tuple[Mapping[str, Any], ...] = ()
    used_doc_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    payload: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["answers"] = list(self.answers)
        payload["kinds"] = list(self.kinds)
        payload["answer_items"] = [dict(item) for item in self.answer_items]
        payload["slot_validations"] = [dict(item) for item in self.slot_validations]
        payload["used_doc_ids"] = list(self.used_doc_ids)
        payload["payload"] = dict(self.payload or {})
        return payload


def decimal_scale_from_question(question_text: str) -> int | None:
    """Return the explicitly requested decimal places, if stated."""
    text = str(question_text or "")
    if _TWO_DECIMAL_RE.search(text):
        return 2
    match = _ARABIC_SCALE_RE.search(text)
    if match:
        scale = int(match.group(1))
        return scale if 0 <= scale <= 12 else None
    match = _CHINESE_SCALE_RE.search(text)
    if match:
        return _CHINESE_DIGITS[match.group(1)]
    return None


def parse_finite_decimal(value: Any, *, percentage: bool = False) -> Decimal | None:
    """Parse a strict finite decimal display value.

    Percentage values must contain an explicit percent suffix; number values must
    not contain one. Explanatory text and non-finite Decimal values are rejected.
    """
    text = str(value or "").strip().replace("％", "%")
    if percentage:
        if not text.endswith("%"):
            return None
        text = text[:-1].strip()
    elif "%" in text:
        return None
    if not text or not _NUMBER_RE.fullmatch(text):
        return None
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def format_decimal_for_submission(
    number: Decimal,
    *,
    scale: int | None,
    percentage: bool = False,
    preserve_scale: int | None = None,
) -> str:
    """Format a finite Decimal using ROUND_HALF_UP and an optional percent suffix."""
    if not number.is_finite():
        raise ValueError("cannot format non-finite decimal")
    effective_scale = scale if scale is not None else preserve_scale
    if effective_scale is not None:
        quantum = Decimal(1).scaleb(-effective_scale)
        rendered = format(number.quantize(quantum, rounding=ROUND_HALF_UP), f".{effective_scale}f")
    else:
        rendered = format(number, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".") or "0"
    return rendered + ("%" if percentage else "")


def _strip_allowed_unit_suffix(value: str, slot_contract: Mapping[str, Any]) -> tuple[str, str | None]:
    text = str(value or "").strip()
    if slot_contract.get("unit_must_be_omitted") is not True:
        return text, None
    suffixes = sorted(
        (str(item) for item in slot_contract.get("allowed_input_unit_suffixes") or () if str(item)),
        key=len,
        reverse=True,
    )
    for suffix in suffixes:
        if text.endswith(suffix):
            stripped = text[: -len(suffix)].strip()
            if stripped:
                return stripped, suffix
    return text, None


def validate_freeform_kind_value(
    value: Any,
    *,
    kind: str,
    question_text: str,
    slot_contract: Mapping[str, Any] | None = None,
) -> KindValidationResult:
    """Validate one slot against the authoritative expected-kind contract."""
    contract_meta = dict(slot_contract or {})
    declared_kind = str(kind or "").strip().lower()
    expected_kind = str(contract_meta.get("expected_kind") or declared_kind).strip().lower()
    text = str(value or "").strip()
    if expected_kind not in _ALLOWED_KINDS:
        return KindValidationResult(
            False,
            f"unsupported_freeform_kind:{expected_kind}",
            text,
            expected_kind,
        )
    if declared_kind != expected_kind:
        return KindValidationResult(
            False,
            f"freeform_model_kind_mismatch:{declared_kind}!={expected_kind}",
            text,
            expected_kind,
        )
    if not text:
        return KindValidationResult(False, "empty_submission_slot", "", expected_kind)

    text, stripped_unit = _strip_allowed_unit_suffix(text, contract_meta)
    if contract_meta.get("unit_must_be_omitted") is True:
        prohibited_units = tuple(
            str(item) for item in contract_meta.get("allowed_input_unit_suffixes") or () if str(item)
        )
        if any(text.endswith(unit) for unit in prohibited_units):
            return KindValidationResult(False, "invalid_freeform_unit_suffix", text, expected_kind)

    explicit_scale = contract_meta.get("expected_decimal_places")
    scale = explicit_scale if isinstance(explicit_scale, int) and not isinstance(explicit_scale, bool) else decimal_scale_from_question(question_text)
    if expected_kind in {"number", "percentage", "percentage_point"}:
        is_percentage = expected_kind == "percentage"
        number = parse_finite_decimal(text, percentage=is_percentage)
        if number is None:
            reason_by_kind = {
                "number": "invalid_freeform_number",
                "percentage": "invalid_freeform_percentage",
                "percentage_point": "invalid_freeform_percentage_point",
            }
            return KindValidationResult(False, reason_by_kind[expected_kind], text, expected_kind)
        raw_numeric = text.replace("％", "%").rstrip("%").replace(",", "").strip()
        raw_places = len(raw_numeric.partition(".")[2]) if "." in raw_numeric else 0
        rendered = format_decimal_for_submission(
            number,
            scale=scale,
            percentage=is_percentage,
            preserve_scale=raw_places,
        )
        reason = {
            "number": "valid_freeform_number",
            "percentage": "valid_freeform_percentage",
            "percentage_point": "valid_freeform_percentage_point",
        }[expected_kind]
        return KindValidationResult(
            True,
            reason,
            rendered,
            expected_kind,
            numeric_value=str(number),
            decimal_places=scale if scale is not None else raw_places,
        )

    if expected_kind == "date":
        compact = re.sub(r"\s+", "", text)
        numeric_match = _DATE_NUMERIC_RE.fullmatch(compact)
        chinese_match = _DATE_CHINESE_RE.fullmatch(compact)
        if numeric_match:
            year = int(numeric_match.group(1))
            month = int(numeric_match.group(3))
            day_value = int(numeric_match.group(4))
        elif chinese_match:
            year, month, day_value = (int(part) for part in chinese_match.groups())
        else:
            return KindValidationResult(False, "invalid_freeform_date", text, expected_kind)
        try:
            checked = date(year, month, day_value)
        except ValueError:
            return KindValidationResult(False, "invalid_freeform_date", text, expected_kind)
        return KindValidationResult(
            True,
            "valid_freeform_date",
            f"{checked.year}年{checked.month}月{checked.day}日",
            expected_kind,
        )

    if expected_kind == "ordering":
        compact = text.replace("＞", ">").strip()
        if ">" not in compact:
            return KindValidationResult(False, "invalid_freeform_ordering", text, expected_kind)
        members = [member.strip() for member in compact.split(">")]
        if len(members) < 2 or any(not member for member in members):
            return KindValidationResult(False, "invalid_freeform_ordering", text, expected_kind)
        return KindValidationResult(
            True,
            "valid_freeform_ordering",
            ">".join(members),
            expected_kind,
        )

    generic_contract = contract_from_answer_format("freeform")
    generic = validate_answer_against_contract(text, generic_contract)
    if not generic.valid:
        return KindValidationResult(False, generic.reason, text, expected_kind)
    return KindValidationResult(True, "valid_freeform_text", generic.answer, expected_kind)


def parse_freeform_submission_answers(
    raw: Any,
    *,
    expected_slots: int,
    question_text: str,
    expected_slot_contracts: Sequence[Mapping[str, Any]] = (),
) -> FreeformSubmissionParse:
    """Parse a structured 1-4 slot freeform response without letter fallback."""
    if expected_slots not in {1, 2, 3, 4}:
        return FreeformSubmissionParse(False, "invalid_expected_slot_count")
    payload = _parse_json_object(raw)
    if payload is None:
        return FreeformSubmissionParse(False, "freeform_json_parse_failed")
    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, Sequence) or isinstance(raw_answers, (str, bytes)):
        return FreeformSubmissionParse(False, "answers_must_be_array", payload=payload)
    if len(raw_answers) != expected_slots:
        return FreeformSubmissionParse(
            False,
            "submission_slot_count_mismatch",
            payload=payload,
        )
    slot_contracts = tuple(dict(item) for item in expected_slot_contracts)
    if slot_contracts and len(slot_contracts) != expected_slots:
        return FreeformSubmissionParse(
            False,
            "expected_slot_contract_count_mismatch",
            payload=payload,
        )

    answers: list[str] = []
    kinds: list[str] = []
    items: list[Mapping[str, Any]] = []
    slot_validations: list[Mapping[str, Any]] = []
    first_error = ""
    contract = contract_from_answer_format("freeform")
    for index, raw_item in enumerate(raw_answers, start=1):
        if isinstance(raw_item, Mapping):
            item = dict(raw_item)
            value = str(item.get("value") or "").strip()
            kind = str(item.get("kind") or "text").strip().lower()
        else:
            item = {"value": str(raw_item or ""), "kind": "text"}
            value = str(raw_item or "").strip()
            kind = "text"

        slot_contract = slot_contracts[index - 1] if slot_contracts else {}
        kind_validation = validate_freeform_kind_value(
            value,
            kind=kind,
            question_text=question_text,
            slot_contract=slot_contract,
        )
        normalized = kind_validation.normalized
        expected_kind = str(slot_contract.get("expected_kind") or kind).strip().lower()
        generic = validate_answer_against_contract(normalized, contract)
        valid = bool(kind_validation.valid and generic.valid)
        reason = (
            "valid_submission_slot"
            if valid
            else kind_validation.reason if not kind_validation.valid else generic.reason
        )
        if not valid and not first_error:
            first_error = reason
        item["value"] = normalized
        item["kind"] = kind
        item["expected_kind"] = expected_kind
        item["expected_slot_contract"] = dict(slot_contract)
        item["format_valid"] = valid
        item["format_reason"] = reason
        item["submitted_numeric_value"] = kind_validation.numeric_value
        item["comparison_scale"] = kind_validation.decimal_places
        answers.append(normalized)
        kinds.append(kind)
        items.append(item)
        slot_validations.append(
            {
                "slot": index,
                "kind": kind,
                "expected_kind": expected_kind,
                "model_kind_matches_expected": kind == expected_kind,
                "valid": valid,
                "reason": reason,
                "normalized": normalized,
                "numeric_value": kind_validation.numeric_value,
                "decimal_places": kind_validation.decimal_places,
            }
        )

    raw_used = payload.get("used_doc_ids")
    used_doc_ids = (
        tuple(dict.fromkeys(str(value).strip() for value in raw_used if str(value).strip()))
        if isinstance(raw_used, Sequence) and not isinstance(raw_used, (str, bytes))
        else ()
    )
    try:
        confidence = max(0.0, min(float(payload.get("confidence", 0.0) or 0.0), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return FreeformSubmissionParse(
        not first_error,
        first_error or "valid_freeform_submission_answers",
        answers=tuple(answers),
        kinds=tuple(kinds),
        answer_items=tuple(items),
        slot_validations=tuple(slot_validations),
        used_doc_ids=used_doc_ids,
        confidence=confidence,
        payload=payload,
    )


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    candidates = [text]
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1))
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None
