"""Canonical question-answer cardinality contract.

The explicit ``answer_format`` field is authoritative.  Raw ``type`` is used
only for safe alias inference when the explicit field is absent, and for
consistency auditing when both are present.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

from contracts import Question, QuestionAnswerContract

SCHEMA_VERSION = "question_answer_contract_v1"
_CANONICAL_ORDER = ("A", "B", "C", "D")
_FORMAT_ALIASES = {
    "mcq": "mcq",
    "single": "mcq",
    "single_choice": "mcq",
    "single-choice": "mcq",
    "单选": "mcq",
    "单选题": "mcq",
    "tf": "tf",
    "true_false": "tf",
    "true-false": "tf",
    "判断": "tf",
    "判断题": "tf",
    "multi": "multi",
    "multiple": "multi",
    "multiple_choice": "multi",
    "multiple-choice": "multi",
    "多选": "multi",
    "多选题": "multi",
    "freeform": "freeform",
    "free_form": "freeform",
    "free-form": "freeform",
    "calculation": "freeform",
    "calculation_question": "freeform",
    "计算": "freeform",
    "计算题": "freeform",
    "抽取": "freeform",
    "抽取题": "freeform",
    "extraction": "freeform",
    "extraction_question": "freeform",
    "number": "freeform",
    "percentage": "freeform",
    "date": "freeform",
    "text": "freeform",
    "ordering": "freeform",
}


@dataclass(frozen=True)
class AnswerContractValidation:
    answer: str
    valid: bool
    reason: str
    violations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_answer_format(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _FORMAT_ALIASES.get(text, "unknown")


def explicit_type_alias(value: Any) -> str:
    """Return a format only for unambiguous cardinality aliases."""
    return normalize_answer_format(value)


def build_question_answer_contract(
    *,
    qid: str,
    raw_type: Any,
    raw_answer_format: Any,
    options: Mapping[str, Any] | None = None,
) -> QuestionAnswerContract:
    explicit = normalize_answer_format(raw_answer_format)
    inferred = explicit_type_alias(raw_type)
    warnings: list[str] = []
    source_of_truth = "explicit_answer_format"
    answer_format = explicit
    if explicit == "unknown":
        if inferred != "unknown":
            answer_format = inferred
            source_of_truth = "safe_type_alias_inference"
            warnings.append("missing_explicit_answer_format")
        else:
            answer_format = "unknown"
            source_of_truth = "unknown"
            warnings.append("unknown_answer_contract")
    elif inferred != "unknown" and inferred != explicit:
        warnings.append(f"type_answer_format_conflict:{inferred}!={explicit}")

    available = tuple(
        label
        for label in _CANONICAL_ORDER
        if not options or label in {str(key).upper() for key in options}
    )
    if not available and answer_format != "unknown":
        available = _CANONICAL_ORDER

    if answer_format == "tf":
        allowed = tuple(label for label in ("A", "B") if label in available or not options)
        allowed = allowed or ("A", "B")
        min_selected = max_selected = 1
    elif answer_format == "mcq":
        allowed = available or _CANONICAL_ORDER
        min_selected = max_selected = 1
    elif answer_format == "multi":
        allowed = available or _CANONICAL_ORDER
        min_selected = 1
        max_selected = len(allowed)
    elif answer_format == "freeform":
        allowed = ()
        min_selected = 1
        max_selected = 4
    else:
        allowed = ()
        min_selected = 0
        max_selected = 0

    return QuestionAnswerContract(
        schema_version=SCHEMA_VERSION,
        qid=str(qid or ""),
        raw_type=str(raw_type or ""),
        raw_answer_format=str(raw_answer_format or ""),
        answer_format=answer_format,
        allowed_labels=allowed,
        min_selected=min_selected,
        max_selected=max_selected,
        canonical_order=_CANONICAL_ORDER,
        source_of_truth=source_of_truth,
        consistency_warnings=tuple(warnings),
    )


def contract_from_answer_format(answer_format: Any) -> QuestionAnswerContract:
    return build_question_answer_contract(
        qid="",
        raw_type="",
        raw_answer_format=answer_format,
        options={label: "" for label in _CANONICAL_ORDER},
    )


def contract_from_question(question: Question) -> QuestionAnswerContract:
    if isinstance(question.answer_contract, QuestionAnswerContract):
        return question.answer_contract
    raw = dict(question.raw or {})
    return build_question_answer_contract(
        qid=question.qid,
        raw_type=raw.get("type"),
        raw_answer_format=raw.get("answer_format", question.answer_format),
        options=question.options,
    )


def contract_from_mapping(value: Mapping[str, Any] | QuestionAnswerContract | None) -> QuestionAnswerContract | None:
    if isinstance(value, QuestionAnswerContract):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return QuestionAnswerContract(
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
            qid=str(value.get("qid") or ""),
            raw_type=str(value.get("raw_type") or ""),
            raw_answer_format=str(value.get("raw_answer_format") or ""),
            answer_format=str(value.get("answer_format") or "unknown"),
            allowed_labels=tuple(str(item) for item in value.get("allowed_labels") or ()),
            min_selected=int(value.get("min_selected") or 0),
            max_selected=int(value.get("max_selected") or 0),
            canonical_order=tuple(str(item) for item in value.get("canonical_order") or _CANONICAL_ORDER),
            source_of_truth=str(value.get("source_of_truth") or "unknown"),
            consistency_warnings=tuple(str(item) for item in value.get("consistency_warnings") or ()),
        )
    except (TypeError, ValueError):
        return None


_FREEFORM_NUMBER_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_FREEFORM_PERCENT_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[%％]$")
_FREEFORM_DATE_RE = re.compile(
    r"^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)$"
)
_FREEFORM_ORDERING_RE = re.compile(r"(?:>|<|≥|≤|大于|小于|高于|低于|先于|后于)")


def classify_freeform_answer_kind(value: Any) -> str:
    """Classify a non-empty multi-slot freeform answer for minimal format audit."""
    text = str(value or "").strip()
    if _FREEFORM_PERCENT_RE.fullmatch(text):
        return "percentage"
    if _FREEFORM_NUMBER_RE.fullmatch(text):
        return "number"
    if _FREEFORM_DATE_RE.fullmatch(text):
        return "date"
    if _FREEFORM_ORDERING_RE.search(text):
        return "ordering"
    return "text"


def validate_answer_against_contract(
    answer: Any,
    contract: QuestionAnswerContract,
) -> AnswerContractValidation:
    raw_text = str(answer or "").strip()
    if contract.answer_format == "freeform":
        if not raw_text:
            return AnswerContractValidation(raw_text, False, "empty_answer", ("empty_answer",))
        if "\x00" in raw_text:
            return AnswerContractValidation(
                raw_text,
                False,
                "freeform_contains_nul",
                ("freeform_contains_nul",),
            )
        if len(raw_text) > 500:
            return AnswerContractValidation(
                raw_text,
                False,
                "freeform_answer_too_long",
                ("freeform_answer_too_long",),
            )
        kind = classify_freeform_answer_kind(raw_text)
        return AnswerContractValidation(raw_text, True, f"valid_freeform:{kind}")

    normalized = raw_text.upper()
    if contract.answer_format == "unknown" or not contract.allowed_labels:
        return AnswerContractValidation(normalized, False, "unknown_answer_contract", ("unknown_answer_contract",))
    if not normalized:
        return AnswerContractValidation(normalized, False, "empty_answer", ("empty_answer",))
    if any(char not in contract.canonical_order for char in normalized):
        return AnswerContractValidation(
            normalized,
            False,
            "contains_non_abcd_character",
            ("contains_non_abcd_character",),
        )
    if contract.answer_format == "tf" and normalized not in {"A", "B"}:
        return AnswerContractValidation(
            normalized,
            False,
            "tf_requires_exactly_one_of_a_b",
            ("selected_count_out_of_range",),
        )
    if any(char not in contract.allowed_labels for char in normalized):
        return AnswerContractValidation(
            normalized,
            False,
            "contains_disallowed_label",
            ("contains_disallowed_label",),
        )
    if contract.answer_format == "multi":
        canonical = "".join(label for label in contract.canonical_order if label in set(normalized))
        selected = len(canonical)
        if selected < contract.min_selected or selected > contract.max_selected:
            return AnswerContractValidation(
                canonical,
                False,
                "multi_selected_count_out_of_range",
                ("selected_count_out_of_range",),
            )
        reason = "valid_multi_canonicalized" if canonical != normalized else "valid_multi"
        return AnswerContractValidation(canonical, True, reason)

    selected = len(normalized)
    if selected != contract.min_selected or selected != contract.max_selected:
        reason = (
            "mcq_requires_exactly_one_letter"
            if contract.answer_format == "mcq"
            else "tf_requires_exactly_one_of_a_b"
        )
        return AnswerContractValidation(
            normalized,
            False,
            reason,
            ("selected_count_out_of_range",),
        )
    reason = "valid_mcq" if contract.answer_format == "mcq" else "valid_tf"
    return AnswerContractValidation(normalized, True, reason)


_EXPLICIT_ANSWER_RE = re.compile(
    r"(?:答案|选项|选择|answer)\s*(?:是|为|[:：])?\s*"
    r"([A-D](?:\s*[,，、/和与及]?\s*[A-D])*)",
    re.IGNORECASE,
)


def normalize_answer_candidate(
    value: Any,
    contract: QuestionAnswerContract,
) -> AnswerContractValidation:
    """Normalize only an explicitly labelled answer without guessing cardinality.

    A plain valid answer is returned unchanged.  Explanatory text is accepted only
    when it contains one unambiguous ``答案/选项/answer`` clause whose letters form
    a contract-valid answer.  Invalid single-choice strings such as ``AB`` are not
    truncated to the first letter, and unknown contracts remain blocked.
    """
    direct = validate_answer_against_contract(value, contract)
    if direct.valid:
        return direct

    text = str(value or "").strip()
    valid_candidates: dict[str, AnswerContractValidation] = {}
    for match in _EXPLICIT_ANSWER_RE.finditer(text):
        letters = {char for char in match.group(1).upper() if char in contract.canonical_order}
        candidate = "".join(label for label in contract.canonical_order if label in letters)
        result = validate_answer_against_contract(candidate, contract)
        if result.valid:
            valid_candidates[result.answer] = result

    if len(valid_candidates) == 1:
        result = next(iter(valid_candidates.values()))
        return AnswerContractValidation(
            result.answer,
            True,
            "valid_explicit_answer_text",
        )
    reason = "ambiguous_answer_text" if len(valid_candidates) > 1 else direct.reason
    return AnswerContractValidation(
        direct.answer,
        False,
        reason,
        direct.violations or (reason,),
    )


def contract_to_dict(contract: QuestionAnswerContract) -> dict[str, Any]:
    payload = asdict(contract)
    payload["allowed_labels"] = list(contract.allowed_labels)
    payload["canonical_order"] = list(contract.canonical_order)
    payload["consistency_warnings"] = list(contract.consistency_warnings)
    return payload


def build_contract_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, QuestionAnswerContract]:
    result: dict[str, QuestionAnswerContract] = {}
    for row in rows:
        qid = str(row.get("qid") or "")
        if not qid:
            continue
        result[qid] = build_question_answer_contract(
            qid=qid,
            raw_type=row.get("type"),
            raw_answer_format=row.get("answer_format"),
            options=row.get("options") if isinstance(row.get("options"), Mapping) else {},
        )
    return result
