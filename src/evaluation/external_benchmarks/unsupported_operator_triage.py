"""Deterministic capability triage for the frozen C3 unsupported-operator set.

This module is evaluation-only.  It classifies source-backed semantics and ranks
bounded capability experiments without changing C3 runtime behaviour.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "c3-unsupported-operator-triage/v1"
DECISION_SCHEMA_VERSION = "c3-capability-decision/v1"
EXPECTED_TOTAL = 72
EXPECTED_DATASETS = {"finqa": 35, "tatqa": 37}
EXPECTED_FAMILIES = {
    "FINQA_TABLE_AGGREGATION": 35,
    "TATQA_COUNT_CARDINALITY": 32,
    "TATQA_FUNCTION_DERIVATION": 5,
}
EXPECTED_FAILURE_DETAILS = {
    "operators:table_average": 18,
    "operators:table_average,table_max": 1,
    "operators:table_max": 7,
    "operators:table_min": 5,
    "operators:table_sum": 4,
    "answer_type:count": 32,
    "function_call": 5,
}

_BASELINE_RECORDS = "evaluation_artifacts/c3_external_oracle_baseline_v1/per_case_records.jsonl"
_BASELINE_REPORT = "evaluation_artifacts/c3_external_oracle_baseline_v1/aggregate_report.json"
_SOURCE_MANIFEST = "evaluation_artifacts/c3_external_oracle_baseline_v1/source_manifest.json"
_FINQA_SPLIT = "evaluation_artifacts/external_benchmarks/finqa/dataset/dev.json"
_TATQA_SPLIT = "evaluation_artifacts/external_benchmarks/tatqa/dataset_raw/tatqa_dataset_dev.json"

_FINQA_STEP_RE = re.compile(r"\s*([A-Za-z_]+)\(([^()]*)\)\s*(?:,\s*|$)")
_PERCENT_LITERAL_RE = re.compile(r"(?<![\w.])([+-]?\d[\d,]*(?:\.\d+)?)\s*%")
_COMPARATOR_RE = re.compile(
    r"\b(exceed(?:ed|s)?|above|below|lower than|more than|greater than|less than)\b",
    flags=re.IGNORECASE,
)
_PERIOD_RE = re.compile(r"\b(years?|quarters?|periods?)\b", flags=re.IGNORECASE)
_ARGMAX_LABEL_RE = re.compile(
    r"\b(?:in\s+)?(?:what|which)\s+year\b|\bwhat\s+year\b|\bwhich\s+year\b",
    flags=re.IGNORECASE,
)


_THRESHOLD_RE = re.compile(
    r"\b(exceed(?:ed|s)?|above|below|lower than|more than|greater than|less than)"
    r"\s+[$]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(%|thousand|million|billion)?\b",
    flags=re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_QUARTER_WORDS = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
_GENERIC_ROW_LABELS = {"total", "net", "change", "number", "revenue", "expenses", "assets", "services"}
_CANDIDATE_TYPES = {
    "PRODUCT_CAPABILITY",
    "MEASUREMENT_ADAPTER_REPAIR",
    "INELIGIBLE_COMPOSITE_OR_AMBIGUOUS",
}


class TriageError(ValueError):
    """Raised when a frozen input or deterministic taxonomy invariant fails."""


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    candidate_type: str
    required_product_surface_rank: int
    binding_ambiguity_rank: int
    new_contract_type_count: int
    required_contract_changes: tuple[str, ...]
    required_product_modules: tuple[str, ...]
    required_evaluation_changes: tuple[str, ...]
    explicit_non_goals: tuple[str, ...]
    generic_capability: bool = True
    fail_closed_design_available: bool = True
    independently_testable: bool = True


_CANDIDATE_SPECS: dict[str, CandidateSpec] = {
    "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION": CandidateSpec(
        name="SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION",
        candidate_type="PRODUCT_CAPABILITY",
        required_product_surface_rank=3,
        binding_ambiguity_rank=1,
        new_contract_type_count=1,
        required_contract_changes=(
            "Add one source-bound numeric-series input contract carrying ordered values, table coordinates, headers, unit and lineage.",
            "Add an aggregation selector limited to average, minimum, maximum and sum, with explicit empty/null/mixed-unit rejection.",
        ),
        required_product_modules=(
            "C3 deterministic calculation contract",
            "source-bound table-series binder",
            "deterministic aggregation executor",
        ),
        required_evaluation_changes=(
            "Add isolated source-bound numeric-series fixtures and exact aggregation Oracles.",
            "Re-run the frozen external Oracle subset without enabling production routing.",
        ),
        explicit_non_goals=(
            "No argmax label output.",
            "No aggregation across rows containing a precomputed total plus component columns.",
            "No count/cardinality semantics.",
            "No production or shadow-route authority.",
        ),
    ),
    "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY": CandidateSpec(
        name="SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY",
        candidate_type="PRODUCT_CAPABILITY",
        required_product_surface_rank=4,
        binding_ambiguity_rank=2,
        new_contract_type_count=2,
        required_contract_changes=(
            "Add a source-bound collection contract.",
            "Add a deterministic scalar predicate contract and integer cardinality output.",
        ),
        required_product_modules=(
            "table axis/range binder",
            "predicate evaluator",
            "cardinality executor",
        ),
        required_evaluation_changes=(
            "Add row-axis and column-axis predicate-count fixtures.",
        ),
        explicit_non_goals=(
            "No natural-language list segmentation.",
            "No prerequisite subtotal calculation.",
            "No production routing.",
        ),
    ),
    "SOURCE_BOUND_TABLE_SECTION_CARDINALITY": CandidateSpec(
        name="SOURCE_BOUND_TABLE_SECTION_CARDINALITY",
        candidate_type="PRODUCT_CAPABILITY",
        required_product_surface_rank=4,
        binding_ambiguity_rank=2,
        new_contract_type_count=2,
        required_contract_changes=(
            "Add an explicit table-section boundary contract.",
            "Add integer cardinality output over bound members.",
        ),
        required_product_modules=(
            "hierarchical table section binder",
            "cardinality executor",
        ),
        required_evaluation_changes=(
            "Add section-header, subtotal-boundary and whole-table membership fixtures.",
        ),
        explicit_non_goals=(
            "No text-list segmentation.",
            "No numeric threshold predicate.",
            "No production routing.",
        ),
    ),
    "SOURCE_BOUND_TABLE_MISSING_VALUE_CARDINALITY": CandidateSpec(
        name="SOURCE_BOUND_TABLE_MISSING_VALUE_CARDINALITY",
        candidate_type="PRODUCT_CAPABILITY",
        required_product_surface_rank=3,
        binding_ambiguity_rank=1,
        new_contract_type_count=2,
        required_contract_changes=(
            "Add a typed missing-value predicate over a source-bound table range.",
            "Add integer cardinality output.",
        ),
        required_product_modules=(
            "table range binder",
            "missing-value normalizer",
            "cardinality executor",
        ),
        required_evaluation_changes=(
            "Add dash/null/blank missing-value fixtures.",
        ),
        explicit_non_goals=(
            "No numeric threshold comparison.",
            "No production routing.",
        ),
    ),
    "SOURCE_BOUND_TABLE_ARGMAX_LABEL": CandidateSpec(
        name="SOURCE_BOUND_TABLE_ARGMAX_LABEL",
        candidate_type="PRODUCT_CAPABILITY",
        required_product_surface_rank=4,
        binding_ambiguity_rank=1,
        new_contract_type_count=2,
        required_contract_changes=(
            "Add source-bound numeric-series input with paired labels.",
            "Add argmax/argmin label output contract and tie policy.",
        ),
        required_product_modules=(
            "table-series binder",
            "arg-extreme executor",
            "label-output verifier",
        ),
        required_evaluation_changes=(
            "Add label-output and tie fixtures.",
        ),
        explicit_non_goals=(
            "No scalar maximum answer substitution.",
            "No production routing.",
        ),
    ),
    "PERCENT_LITERAL_OPERATOR_NORMALIZATION": CandidateSpec(
        name="PERCENT_LITERAL_OPERATOR_NORMALIZATION",
        candidate_type="MEASUREMENT_ADAPTER_REPAIR",
        required_product_surface_rank=1,
        binding_ambiguity_rank=1,
        new_contract_type_count=0,
        required_contract_changes=(
            "Correct percent-literal lexical normalization so adjacent arithmetic operators are preserved.",
        ),
        required_product_modules=(
            "TAT-QA evaluation derivation normalizer",
        ),
        required_evaluation_changes=(
            "Add percent-average and percentage-point-difference parser fixtures.",
        ),
        explicit_non_goals=(
            "No new C3 function-call semantics.",
            "No production routing.",
        ),
    ),
    "SOURCE_BACKED_TEXT_ENUMERATION_CARDINALITY": CandidateSpec(
        name="SOURCE_BACKED_TEXT_ENUMERATION_CARDINALITY",
        candidate_type="INELIGIBLE_COMPOSITE_OR_AMBIGUOUS",
        required_product_surface_rank=5,
        binding_ambiguity_rank=3,
        new_contract_type_count=2,
        required_contract_changes=(
            "Would require a source-backed semantic list-segmentation contract.",
            "Would require integer cardinality output.",
        ),
        required_product_modules=(
            "text semantic segmenter",
            "list membership binder",
            "cardinality executor",
        ),
        required_evaluation_changes=(
            "Would require independently labelled text-member spans.",
        ),
        explicit_non_goals=(
            "Do not infer members from punctuation alone.",
            "Do not use the official answer count as segmentation authority.",
        ),
        fail_closed_design_available=False,
    ),
    "SOURCE_BOUND_COMPOSITE_AGGREGATE_CARDINALITY": CandidateSpec(
        name="SOURCE_BOUND_COMPOSITE_AGGREGATE_CARDINALITY",
        candidate_type="INELIGIBLE_COMPOSITE_OR_AMBIGUOUS",
        required_product_surface_rank=6,
        binding_ambiguity_rank=3,
        new_contract_type_count=3,
        required_contract_changes=(
            "Would require section aggregation before predicate cardinality.",
        ),
        required_product_modules=(
            "hierarchical section binder",
            "aggregation executor",
            "predicate cardinality executor",
        ),
        required_evaluation_changes=(
            "Would require a separately evaluated prerequisite aggregation capability.",
        ),
        explicit_non_goals=(
            "Do not bundle two unimplemented capabilities into one experiment.",
        ),
        independently_testable=False,
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise TriageError(f"JSONL row {line_number} is not an object")
        payload = dict(payload)
        payload["_baseline_line_number"] = line_number
        rows.append(payload)
    return rows


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("ﬁ", "fi").replace("–", "-").replace("—", "-")
    return " ".join(re.sub(r"[^a-z0-9%$.-]+", " ", text).split())


def _is_numeric_cell(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or text in {"-", "–", "—"}:
        return False
    cleaned = text.replace("$", "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace(",", "").replace("%", "").strip()
    cleaned = re.sub(r"^\((.*)\)$", r"\1", cleaned).strip()
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()
    try:
        float(cleaned)
    except ValueError:
        return False
    return True

def _numeric_cell_value(value: Any) -> tuple[Decimal, bool] | None:
    text = str(value or "").strip()
    if not text or text in {"-", "–", "—"}:
        return None
    is_percent = "%" in text
    cleaned = text.replace(chr(36), "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace(",", "").replace("%", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    return (-number if negative else number), is_percent


def _source_coordinate(table_uid: Any, row_index: int, column_index: int) -> str:
    return f"tatqa://table/{table_uid}/r{row_index}c{column_index}"


def _empty_oracle_proof(
    *,
    table_uid: Any,
    uniqueness: str,
    reason: str,
    candidate_coordinates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "proof_status": "INCOMPLETE",
        "bound_source_object_ids": [f"tatqa://table/{table_uid}"] if table_uid else [],
        "bound_axis_or_section": None,
        "bound_member_or_value_coordinates": list(candidate_coordinates),
        "predicate_or_membership_rule": None,
        "independently_derived_expected_count": None,
        "binding_uniqueness_status": uniqueness,
        "failure_reason": reason,
    }


def _complete_oracle_proof(
    *,
    table_uid: Any,
    axis_or_section: Mapping[str, Any],
    coordinates: Sequence[Mapping[str, Any]],
    rule: Mapping[str, Any],
    expected_count: int,
) -> dict[str, Any]:
    if not coordinates:
        raise TriageError("Complete Oracle proof requires non-empty source coordinates")
    return {
        "proof_status": "COMPLETE",
        "bound_source_object_ids": [f"tatqa://table/{table_uid}"],
        "bound_axis_or_section": dict(axis_or_section),
        "bound_member_or_value_coordinates": [dict(item) for item in coordinates],
        "predicate_or_membership_rule": dict(rule),
        "independently_derived_expected_count": int(expected_count),
        "binding_uniqueness_status": "UNIQUE",
        "failure_reason": "",
    }


def _predicate_from_question(question: str) -> dict[str, Any] | None:
    match = _THRESHOLD_RE.search(question)
    if match is None:
        return None
    phrase, raw_threshold, unit = match.groups()
    phrase_norm = phrase.lower()
    operator = "<" if phrase_norm in {"below", "lower than", "less than"} else ">"
    threshold = Decimal(raw_threshold.replace(",", ""))
    return {
        "operator": operator,
        "threshold": str(threshold),
        "unit": (unit or "").lower(),
        "source_phrase": phrase,
    }


def _source_scale(context_text: str) -> str:
    text = _normalise(context_text)
    patterns = (
        ("billion", ("in billions", "amounts in billions", "dollars in billions", "table in billions")),
        ("million", ("in millions", "amounts in millions", "dollars in millions", "table in millions", "tabular amounts in millions")),
        ("thousand", ("in thousands", "amounts in thousands", "dollars in thousands", "table in thousands", "tabular amounts in thousands")),
    )
    found = [unit for unit, phrases in patterns if any(phrase in text for phrase in phrases)]
    return found[0] if len(found) == 1 else ""


def _predicate_in_source_units(
    predicate: Mapping[str, Any], context_text: str
) -> tuple[dict[str, Any] | None, str]:
    result = dict(predicate)
    if predicate.get("unit") == "%":
        result["source_scale"] = "%"
        result["threshold_in_source_units"] = str(predicate["threshold"])
        return result, ""
    question_unit = str(predicate.get("unit") or "")
    source_unit = _source_scale(context_text)
    if question_unit and not source_unit:
        return None, f"source table scale is not uniquely established for question unit {question_unit}"
    if not question_unit and source_unit:
        question_unit = source_unit
    if question_unit and source_unit:
        factors = {
            "thousand": Decimal("1000"),
            "million": Decimal("1000000"),
            "billion": Decimal("1000000000"),
        }
        threshold = Decimal(str(predicate["threshold"]))
        converted = threshold * factors[question_unit] / factors[source_unit]
        result["threshold"] = str(converted)
        result["question_unit"] = question_unit
        result["source_scale"] = source_unit
        result["threshold_in_source_units"] = str(converted)
    else:
        result["source_scale"] = source_unit
        result["threshold_in_source_units"] = str(predicate["threshold"])
    return result, ""


def _evaluate_predicate(value: Decimal, predicate: Mapping[str, Any]) -> bool:
    threshold = Decimal(str(predicate["threshold"]))
    return value > threshold if predicate["operator"] == ">" else value < threshold


def _normalised_row_label(value: Any) -> str:
    text = _normalise(value)
    return re.sub(r"\s+\d+$", "", text).strip()


def _metric_row_candidates(
    table: Sequence[Any], question: str
) -> list[dict[str, Any]]:
    question_norm = _normalise(question)
    question_tokens = set(question_norm.split())
    candidates: list[dict[str, Any]] = []
    for row_index, row in enumerate(table):
        if not isinstance(row, list) or not row:
            continue
        label = _normalised_row_label(row[0])
        numeric_cells = [
            column_index
            for column_index, cell in enumerate(row[1:], start=1)
            if _numeric_cell_value(cell) is not None
        ]
        if not label or not numeric_cells:
            continue
        label_tokens = set(label.split())
        if not label_tokens:
            continue
        phrase_match = label in question_norm
        token_coverage = len(label_tokens & question_tokens) / len(label_tokens)
        if not phrase_match and token_coverage < 0.8:
            continue
        if len(label_tokens) == 1 and label in _GENERIC_ROW_LABELS:
            continue
        candidates.append(
            {
                "row_index": row_index,
                "row_label": label,
                "raw_row_label": str(row[0]),
                "numeric_columns": numeric_cells,
                "phrase_match": phrase_match,
                "token_coverage": token_coverage,
                "token_count": len(label_tokens),
            }
        )
    if not candidates:
        return []
    best_key = max(
        (int(item["phrase_match"]), item["token_coverage"], item["token_count"])
        for item in candidates
    )
    return [
        item
        for item in candidates
        if (int(item["phrase_match"]), item["token_coverage"], item["token_count"])
        == best_key
    ]


def _period_label(value: Any) -> str | None:
    text = _normalise(value)
    years = _YEAR_RE.findall(text)
    if len(years) == 1:
        return years[0]
    for word, label in _QUARTER_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return label
    if text == "thereafter":
        return "THEREAFTER"
    return None


def _column_period_label(
    table: Sequence[Any], *, row_index: int, column_index: int
) -> str | None:
    for header_row_index in range(row_index - 1, -1, -1):
        row = table[header_row_index]
        if not isinstance(row, list) or column_index >= len(row):
            continue
        label = _period_label(row[column_index])
        if label is not None:
            return label
    return None


def _question_year_scope(question: str) -> set[str] | None:
    years = [int(item) for item in _YEAR_RE.findall(question)]
    if not years:
        return None
    if len(years) == 2 and re.search(r"\b(?:from|between)\b", question, flags=re.IGNORECASE):
        low, high = sorted(years)
        return {str(year) for year in range(low, high + 1)}
    return {str(year) for year in years}


def _period_predicate_oracle(
    *, table_uid: Any, table: Sequence[Any], question: str, context_text: str
) -> dict[str, Any]:
    predicate = _predicate_from_question(question)
    if predicate is None:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="UNBOUND",
            reason="comparison threshold could not be parsed from the question",
        )
    predicate, unit_reason = _predicate_in_source_units(predicate, context_text)
    if predicate is None:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason=unit_reason,
        )
    rows = _metric_row_candidates(table, question)
    generic_total_rows = [
        row_index
        for row_index, row in enumerate(table)
        if isinstance(row, list)
        and row
        and _normalised_row_label(row[0]) == "total"
        and any(_numeric_cell_value(cell) is not None for cell in row[1:])
    ]
    if len(rows) != 1:
        reason = (
            "no unique numeric metric row located from the question"
            if not rows
            else f"multiple equally specific numeric metric rows located: {[item['row_index'] for item in rows]}"
        )
        if not rows and len(generic_total_rows) > 1 and "total" in _normalise(question):
            reason = f"multiple generic Total rows require an unresolved section binding: {generic_total_rows}"
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="UNBOUND" if not rows else "AMBIGUOUS",
            reason=reason,
            candidate_coordinates=[
                {"row_index": item["row_index"], "row_label": item["raw_row_label"]}
                for item in rows
            ],
        )
    row = rows[0]
    row_index = int(row["row_index"])
    raw_row = table[row_index]
    values: list[dict[str, Any]] = []
    unresolved_numeric_columns: list[int] = []
    for column_index in row["numeric_columns"]:
        parsed = _numeric_cell_value(raw_row[column_index])
        if parsed is None:
            continue
        value, is_percent = parsed
        period = _column_period_label(
            table, row_index=row_index, column_index=column_index
        )
        if period is None:
            unresolved_numeric_columns.append(column_index)
            continue
        values.append(
            {
                "coordinate": _source_coordinate(table_uid, row_index, column_index),
                "row_index": row_index,
                "column_index": column_index,
                "row_label": row["raw_row_label"],
                "period_label": period,
                "raw_value": str(raw_row[column_index]),
                "numeric_value": str(value),
                "is_percent": is_percent,
            }
        )
    if unresolved_numeric_columns:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason=(
                "numeric metric row contains value columns without unique period headers; "
                f"mixed metric columns or merged headers remain unresolved: {unresolved_numeric_columns}"
            ),
            candidate_coordinates=values,
        )
    period_labels = [item["period_label"] for item in values]
    if not values or len(period_labels) != len(set(period_labels)):
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason="period binding is empty or contains duplicate period labels",
            candidate_coordinates=values,
        )
    year_scope = _question_year_scope(question)
    if year_scope is not None:
        scoped = [item for item in values if item["period_label"] in year_scope]
        if not scoped or {item["period_label"] for item in scoped} != year_scope:
            return _empty_oracle_proof(
                table_uid=table_uid,
                uniqueness="AMBIGUOUS",
                reason=f"question year scope {sorted(year_scope)} does not bind uniquely to value columns",
                candidate_coordinates=values,
            )
        values = scoped
    expects_percent = predicate["unit"] == "%"
    if any(bool(item["is_percent"]) != expects_percent for item in values):
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason="bound value columns mix amount and percentage metrics or disagree with the question unit",
            candidate_coordinates=values,
        )
    expected = sum(
        _evaluate_predicate(Decimal(item["numeric_value"]), predicate)
        for item in values
    )
    return _complete_oracle_proof(
        table_uid=table_uid,
        axis_or_section={
            "axis": "ROW_ACROSS_PERIOD_COLUMNS",
            "row_index": row_index,
            "row_label": row["raw_row_label"],
            "period_labels": [item["period_label"] for item in values],
        },
        coordinates=values,
        rule={
            "rule_type": "SCALAR_PREDICATE_CARDINALITY",
            **predicate,
        },
        expected_count=expected,
    )


def _unique_year_column(
    table: Sequence[Any], question: str
) -> tuple[int | None, str, list[int]]:
    years = _YEAR_RE.findall(question)
    if len(set(years)) != 1:
        return None, "question does not identify exactly one target year", []
    target_year = years[0]
    matches: list[int] = []
    width = max((len(row) for row in table if isinstance(row, list)), default=0)
    for column_index in range(1, width):
        labels = {
            _period_label(row[column_index])
            for row in table[:4]
            if isinstance(row, list) and column_index < len(row)
        }
        if target_year in labels:
            matches.append(column_index)
    if len(matches) != 1:
        return None, f"target year {target_year} maps to {len(matches)} columns: {matches}", matches
    return matches[0], "", matches


def _section_phrase(question: str) -> str:
    norm = _normalise(question)
    match = re.search(r"\bunder\s+(.+?)(?:\?|$)", norm)
    if match:
        return match.group(1).strip()
    match = re.search(r"\bcomponents?\s+of\s+(.+?)(?:\s+exceed|\?|$)", norm)
    if match:
        return match.group(1).strip()
    return ""


def _unique_section_range(
    table: Sequence[Any], section_phrase: str
) -> tuple[tuple[int, int] | None, str, list[int]]:
    if not section_phrase:
        return None, "question does not expose an explicit table section phrase", []
    candidates: list[int] = []
    phrase_tokens = set(section_phrase.split())
    for row_index, row in enumerate(table):
        if not isinstance(row, list) or not row:
            continue
        label = _normalised_row_label(row[0]).rstrip(":")
        if not label:
            continue
        label_tokens = set(label.split())
        if label == section_phrase or (
            len(label_tokens) >= 2
            and label_tokens <= phrase_tokens
            and len(label_tokens) / max(len(phrase_tokens), 1) >= 0.75
        ):
            if not any(_numeric_cell_value(cell) is not None for cell in row[1:]):
                candidates.append(row_index)
    if len(candidates) != 1:
        return None, f"section phrase maps to {len(candidates)} section headers: {candidates}", candidates
    start = candidates[0]
    end: int | None = None
    for row_index in range(start + 1, len(table)):
        row = table[row_index]
        if not isinstance(row, list) or not row:
            continue
        label = _normalised_row_label(row[0]).rstrip(":")
        label_tokens = set(label.split())
        section_tokens = set(section_phrase.split())
        is_total_boundary = bool(re.match(r"^total\b", label))
        is_qualified_aggregate_boundary = (
            bool(re.match(r"^(?:gross|net|less)\b", label))
            and bool(section_tokens)
            and len(label_tokens & section_tokens) / len(section_tokens) >= 0.75
        )
        if is_total_boundary or is_qualified_aggregate_boundary:
            end = row_index
            break
        if row_index > start + 1 and not any(
            _numeric_cell_value(cell) is not None for cell in row[1:]
        ):
            end = row_index
            break
    if end is None:
        return None, "section has no explicit subtotal or next-section boundary", candidates
    if end <= start + 1:
        return None, "section contains no member rows", candidates
    return (start + 1, end), "", candidates


def _category_predicate_oracle(
    *, table_uid: Any, table: Sequence[Any], question: str, context_text: str
) -> dict[str, Any]:
    predicate = _predicate_from_question(question)
    if predicate is None:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="UNBOUND",
            reason="comparison threshold could not be parsed from the question",
        )
    predicate, unit_reason = _predicate_in_source_units(predicate, context_text)
    if predicate is None:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason=unit_reason,
        )
    column_index, column_reason, column_candidates = _unique_year_column(table, question)
    if column_index is None:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS" if column_candidates else "UNBOUND",
            reason=column_reason,
        )
    section_phrase = _section_phrase(question)
    section_range, section_reason, section_candidates = _unique_section_range(
        table, section_phrase
    )
    if section_range is None:
        # A compact table with one explicit Total boundary can define its detail rows.
        detail_rows = [
            row_index
            for row_index, row in enumerate(table)
            if isinstance(row, list)
            and row
            and str(row[0]).strip()
            and column_index < len(row)
            and _numeric_cell_value(row[column_index]) is not None
            and not re.match(r"^total\b", _normalised_row_label(row[0]))
        ]
        total_rows = [
            row_index
            for row_index, row in enumerate(table)
            if isinstance(row, list)
            and row
            and re.match(r"^total\b", _normalised_row_label(row[0]))
        ]
        if not detail_rows or len(total_rows) != 1 or max(detail_rows) >= total_rows[0]:
            return _empty_oracle_proof(
                table_uid=table_uid,
                uniqueness="AMBIGUOUS",
                reason=(
                    section_reason
                    + "; no unique whole-table detail range ending at one Total row"
                ),
                candidate_coordinates=[{"section_header_rows": section_candidates}],
            )
        member_rows = detail_rows
        section_descriptor = {
            "axis": "CATEGORY_ROWS_IN_SINGLE_PERIOD_COLUMN",
            "range_rule": "all numeric detail rows before the unique Total row",
            "start_row": min(detail_rows),
            "end_row_exclusive": total_rows[0],
            "column_index": column_index,
        }
    else:
        start, end = section_range
        member_rows = [
            row_index
            for row_index in range(start, end)
            if isinstance(table[row_index], list)
            and column_index < len(table[row_index])
            and _numeric_cell_value(table[row_index][column_index]) is not None
        ]
        section_descriptor = {
            "axis": "CATEGORY_ROWS_IN_BOUND_SECTION",
            "section_phrase": section_phrase,
            "start_row": start,
            "end_row_exclusive": end,
            "column_index": column_index,
        }
    if not member_rows:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="UNBOUND",
            reason="bound category range contains no numeric members",
        )
    values: list[dict[str, Any]] = []
    expects_percent = predicate["unit"] == "%"
    for row_index in member_rows:
        row = table[row_index]
        parsed = _numeric_cell_value(row[column_index])
        if parsed is None:
            continue
        value, is_percent = parsed
        if is_percent != expects_percent:
            return _empty_oracle_proof(
                table_uid=table_uid,
                uniqueness="AMBIGUOUS",
                reason="bound category range mixes amount and percentage metrics",
                candidate_coordinates=values,
            )
        values.append(
            {
                "coordinate": _source_coordinate(table_uid, row_index, column_index),
                "row_index": row_index,
                "column_index": column_index,
                "member_label": str(row[0]),
                "raw_value": str(row[column_index]),
                "numeric_value": str(value),
                "is_percent": is_percent,
            }
        )
    expected = sum(
        _evaluate_predicate(Decimal(item["numeric_value"]), predicate)
        for item in values
    )
    return _complete_oracle_proof(
        table_uid=table_uid,
        axis_or_section=section_descriptor,
        coordinates=values,
        rule={"rule_type": "SCALAR_PREDICATE_CARDINALITY", **predicate},
        expected_count=expected,
    )


def _section_cardinality_oracle(
    *, table_uid: Any, table: Sequence[Any], question: str
) -> dict[str, Any]:
    section_phrase = _section_phrase(question)
    section_range, reason, candidates = _unique_section_range(table, section_phrase)
    if section_range is not None:
        start, end = section_range
        members = [
            {
                "coordinate": _source_coordinate(table_uid, row_index, 0),
                "row_index": row_index,
                "column_index": 0,
                "member_label": str(table[row_index][0]),
            }
            for row_index in range(start, end)
            if isinstance(table[row_index], list)
            and table[row_index]
            and str(table[row_index][0]).strip()
        ]
        return _complete_oracle_proof(
            table_uid=table_uid,
            axis_or_section={
                "axis": "ROWS_IN_BOUND_SECTION",
                "section_phrase": section_phrase,
                "start_row": start,
                "end_row_exclusive": end,
            },
            coordinates=members,
            rule={
                "rule_type": "SECTION_MEMBER_CARDINALITY",
                "exclude_boundary_and_subtotal_rows": True,
            },
            expected_count=len(members),
        )
    # Whole-table entity lists require a header plus uniformly populated data rows and no totals.
    if table and isinstance(table[0], list) and sum(bool(str(cell).strip()) for cell in table[0]) >= 2:
        data_rows = []
        valid = True
        for row_index, row in enumerate(table[1:], start=1):
            if not isinstance(row, list) or not row or not str(row[0]).strip():
                valid = False
                break
            label = _normalised_row_label(row[0])
            if re.match(r"^(?:total|gross|net|less)\b", label):
                valid = False
                break
            if sum(bool(str(cell).strip()) for cell in row) < 2:
                valid = False
                break
            data_rows.append(row_index)
        question_norm = _normalise(question)
        entity_intent = bool(re.search(r"\b(?:officers|directors|employees|members)\b", question_norm))
        if valid and data_rows and entity_intent:
            members = [
                {
                    "coordinate": _source_coordinate(table_uid, row_index, 0),
                    "row_index": row_index,
                    "column_index": 0,
                    "member_label": str(table[row_index][0]),
                }
                for row_index in data_rows
            ]
            return _complete_oracle_proof(
                table_uid=table_uid,
                axis_or_section={
                    "axis": "WHOLE_TABLE_ENTITY_ROWS",
                    "header_row": 0,
                    "start_row": 1,
                    "end_row_exclusive": len(table),
                },
                coordinates=members,
                rule={"rule_type": "WHOLE_TABLE_ENTITY_CARDINALITY"},
                expected_count=len(members),
            )
    return _empty_oracle_proof(
        table_uid=table_uid,
        uniqueness="AMBIGUOUS" if candidates else "UNBOUND",
        reason=reason or "no unique section or whole-table entity range can be proved",
        candidate_coordinates=[{"section_header_rows": candidates}],
    )


def _missing_value_cardinality_oracle(
    *, table_uid: Any, table: Sequence[Any], question: str
) -> dict[str, Any]:
    years = _YEAR_RE.findall(question)
    if len(years) != 2:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="UNBOUND",
            reason="missing-value question does not identify exactly two periods",
        )
    present_year, missing_year = years[0], years[1]
    columns: dict[str, list[int]] = {present_year: [], missing_year: []}
    width = max((len(row) for row in table if isinstance(row, list)), default=0)
    for column_index in range(1, width):
        labels = {
            _period_label(row[column_index])
            for row in table[:4]
            if isinstance(row, list) and column_index < len(row)
        }
        for year in columns:
            if year in labels:
                columns[year].append(column_index)
    if any(len(value) != 1 for value in columns.values()):
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason=f"period columns are not unique: {columns}",
        )
    present_col = columns[present_year][0]
    missing_col = columns[missing_year][0]
    coordinates: list[dict[str, Any]] = []
    for row_index, row in enumerate(table):
        if not isinstance(row, list) or max(present_col, missing_col) >= len(row):
            continue
        label = str(row[0]).strip() if row else ""
        if not label or _period_label(label):
            continue
        present_raw = str(row[present_col]).strip()
        missing_raw = str(row[missing_col]).strip()
        if not present_raw and not missing_raw:
            continue
        coordinates.append(
            {
                "member_label": label,
                "present_coordinate": _source_coordinate(table_uid, row_index, present_col),
                "missing_coordinate": _source_coordinate(table_uid, row_index, missing_col),
                "present_raw": present_raw,
                "missing_raw": missing_raw,
                "predicate_match": (
                    present_raw not in {"", "-", "–", "—"}
                    and missing_raw in {"", "-", "–", "—"}
                ),
            }
        )
    if not coordinates:
        return _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="UNBOUND",
            reason="no table members were located for the two bound periods",
        )
    expected = sum(bool(item["predicate_match"]) for item in coordinates)
    return _complete_oracle_proof(
        table_uid=table_uid,
        axis_or_section={
            "axis": "ROWS_ACROSS_TWO_PERIOD_COLUMNS",
            "present_year": present_year,
            "present_column": present_col,
            "missing_year": missing_year,
            "missing_column": missing_col,
        },
        coordinates=coordinates,
        rule={
            "rule_type": "PRESENT_IN_FIRST_PERIOD_AND_MISSING_IN_SECOND",
            "missing_markers": ["", "-", "–", "—"],
        },
        expected_count=expected,
    )


def _counter_dict(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _parse_finqa_steps(program: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    text = str(program or "").strip()
    rows: list[tuple[str, tuple[str, ...]]] = []
    position = 0
    while position < len(text):
        match = _FINQA_STEP_RE.match(text, position)
        if match is None:
            raise TriageError(f"FinQA program cannot be parsed at {position}: {program}")
        operator, body = match.groups()
        arguments = tuple(item.strip() for item in body.split(","))
        rows.append((operator, arguments))
        position = match.end()
    return tuple(rows)


def _validate_frozen_inputs(
    *,
    root: Path,
    records: Sequence[Mapping[str, Any]],
    aggregate_report: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not aggregate_report.get("measurement_valid"):
        raise TriageError("Frozen external Oracle baseline is not measurement-valid")
    combined = aggregate_report.get("datasets", {}).get("combined", {})
    if combined.get("unsupported_operator_distribution") != EXPECTED_FAILURE_DETAILS:
        raise TriageError("Frozen aggregate unsupported-operator distribution differs")
    if combined.get("c3_representable_count") != 1550:
        raise TriageError("Frozen combined representable count differs")
    if combined.get("terminal_executed_correct_count") != 1548:
        raise TriageError("Frozen combined correct count differs")
    if combined.get("numeric_eligible_count") != 1623:
        raise TriageError("Frozen combined numeric-eligible count differs")

    unsupported = [
        dict(row)
        for row in records
        if row.get("terminal_classification") == "UNSUPPORTED_OPERATOR"
    ]
    if len(unsupported) != EXPECTED_TOTAL:
        raise TriageError(f"Expected {EXPECTED_TOTAL} unsupported cases, got {len(unsupported)}")
    case_ids = [str(row.get("case_id") or "") for row in unsupported]
    if any(not case_id for case_id in case_ids):
        raise TriageError("Unsupported record has a missing case_id")
    duplicates = sorted(case_id for case_id, count in Counter(case_ids).items() if count != 1)
    if duplicates:
        raise TriageError(f"Duplicate unsupported case IDs: {duplicates}")
    if _counter_dict(str(row.get("dataset")) for row in unsupported) != EXPECTED_DATASETS:
        raise TriageError("Frozen unsupported dataset totals differ")
    if _counter_dict(str(row.get("failure_detail")) for row in unsupported) != EXPECTED_FAILURE_DETAILS:
        raise TriageError("Frozen unsupported failure-detail totals differ")

    manifest_entries = {
        str(item.get("dataset_name")): item
        for item in source_manifest.get("sources", [])
        if isinstance(item, Mapping)
    }
    source_paths = {
        "finqa": root / _FINQA_SPLIT,
        "tatqa": root / _TATQA_SPLIT,
    }
    for dataset, path in source_paths.items():
        entry = manifest_entries.get(dataset)
        if entry is None:
            raise TriageError(f"Source manifest missing {dataset}")
        actual = _sha256(path)
        expected = str(entry.get("selected_split_sha256") or "")
        if actual != expected:
            raise TriageError(f"Frozen {dataset} split hash differs")
    return sorted(unsupported, key=lambda row: (str(row["dataset"]), str(row["case_id"])))


def _source_manifest_entry(source_manifest: Mapping[str, Any], dataset: str) -> Mapping[str, Any]:
    for item in source_manifest.get("sources", []):
        if isinstance(item, Mapping) and item.get("dataset_name") == dataset:
            return item
    raise TriageError(f"Missing source manifest entry: {dataset}")


def _index_finqa(payload: Any) -> dict[str, tuple[int, Mapping[str, Any]]]:
    if not isinstance(payload, list):
        raise TriageError("FinQA split must be a list")
    result: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise TriageError("FinQA row is not an object")
        case_id = str(row.get("id") or "")
        if not case_id or case_id in result:
            raise TriageError(f"FinQA missing or duplicate id: {case_id}")
        result[case_id] = (index, row)
    return result


def _index_tatqa(payload: Any) -> dict[str, tuple[int, int, Mapping[str, Any], Mapping[str, Any]]]:
    if not isinstance(payload, list):
        raise TriageError("TAT-QA split must be a list")
    result: dict[str, tuple[int, int, Mapping[str, Any], Mapping[str, Any]]] = {}
    for document_index, document in enumerate(payload):
        if not isinstance(document, Mapping) or not isinstance(document.get("questions"), list):
            raise TriageError("TAT-QA document schema invalid")
        for question_index, question in enumerate(document["questions"]):
            if not isinstance(question, Mapping):
                raise TriageError("TAT-QA question schema invalid")
            case_id = str(question.get("uid") or "")
            if not case_id or case_id in result:
                raise TriageError(f"TAT-QA missing or duplicate uid: {case_id}")
            result[case_id] = (document_index, question_index, document, question)
    return result


def _finqa_table_bindings(
    document: Mapping[str, Any], steps: Sequence[tuple[str, tuple[str, ...]]]
) -> tuple[list[dict[str, Any]], bool]:
    qa = document.get("qa") if isinstance(document.get("qa"), Mapping) else {}
    table = document.get("table") if isinstance(document.get("table"), list) else []
    gold_indices: set[int] = set()
    for key in (qa.get("gold_inds") or {}):
        match = re.fullmatch(r"table_(\d+)", str(key))
        if match:
            gold_indices.add(int(match.group(1)))
    for item in qa.get("ann_table_rows") or []:
        if isinstance(item, int):
            gold_indices.add(item)

    bindings: list[dict[str, Any]] = []
    exact = True
    for operator, arguments in steps:
        if not operator.startswith("table_"):
            continue
        row_label = arguments[0] if arguments else ""
        matches = [
            row_index
            for row_index, row in enumerate(table)
            if isinstance(row, list) and row and _normalise(row[0]) == _normalise(row_label)
        ]
        source_confirmed = len(matches) == 1 and matches[0] in gold_indices
        exact = exact and source_confirmed
        row_index = matches[0] if len(matches) == 1 else None
        row_values = list(table[row_index]) if row_index is not None else []
        bindings.append(
            {
                "operator": operator,
                "program_row_label": row_label,
                "matching_table_row_indices": matches,
                "gold_table_row_indices": sorted(gold_indices),
                "source_confirmed": source_confirmed,
                "bound_row": row_values,
            }
        )
    return bindings, exact and bool(bindings)


def _finqa_contains_precomputed_total(
    document: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]], operators: Sequence[str]
) -> bool:
    if "table_sum" not in operators:
        return False
    table = document.get("table") if isinstance(document.get("table"), list) else []
    for binding in bindings:
        if binding.get("operator") != "table_sum":
            continue
        matches = binding.get("matching_table_row_indices") or []
        if len(matches) != 1:
            continue
        row_index = int(matches[0])
        headers = table[0] if table and isinstance(table[0], list) else []
        has_total_header = any(re.search(r"\btotal\b", _normalise(cell)) for cell in headers[1:])
        row = table[row_index] if row_index < len(table) and isinstance(table[row_index], list) else []
        numeric_count = sum(_is_numeric_cell(cell) for cell in row[1:])
        if has_total_header and numeric_count >= 2:
            return True
    return False


def _finqa_record(
    *,
    baseline: Mapping[str, Any],
    document_index: int,
    document: Mapping[str, Any],
    manifest_entry: Mapping[str, Any],
) -> dict[str, Any]:
    qa = document.get("qa") if isinstance(document.get("qa"), Mapping) else {}
    program = str(qa.get("program") or "")
    steps = _parse_finqa_steps(program)
    operators = [operator for operator, _arguments in steps]
    table_operators = [operator for operator in operators if operator.startswith("table_")]
    bindings, exact_binding = _finqa_table_bindings(document, steps)
    question = str(qa.get("question") or "")
    argmax_label = "table_max" in table_operators and bool(_ARGMAX_LABEL_RE.search(question))
    contains_precomputed_total = _finqa_contains_precomputed_total(
        document, bindings, table_operators
    )

    if argmax_label:
        subfamily = "TABLE_ARGMAX_LABEL"
        candidate = "SOURCE_BOUND_TABLE_ARGMAX_LABEL"
        required_operation = "argmax over a source-bound numeric series with header-label output"
        minimum_surface = "numeric-series binding + argmax label output + deterministic tie policy"
    elif set(table_operators) == {"table_average", "table_max"}:
        subfamily = "TABLE_MULTI_AGGREGATE_COMPOSITION"
        candidate = "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
        required_operation = "average and maximum over one source-bound numeric series, composed with existing subtraction"
        minimum_surface = "one source-bound numeric-series contract + average/max aggregation selectors"
    elif table_operators == ["table_average"]:
        subfamily = "TABLE_AVERAGE_NUMERIC"
        candidate = "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
        required_operation = "arithmetic mean over a source-bound numeric series"
        minimum_surface = "one source-bound numeric-series contract + average selector"
    elif table_operators == ["table_max"]:
        subfamily = "TABLE_MAX_NUMERIC"
        candidate = "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
        required_operation = "maximum scalar over a source-bound numeric series"
        minimum_surface = "one source-bound numeric-series contract + maximum selector"
    elif table_operators == ["table_min"]:
        subfamily = "TABLE_MIN_NUMERIC"
        candidate = "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
        required_operation = "minimum scalar over a source-bound numeric series"
        minimum_surface = "one source-bound numeric-series contract + minimum selector"
    elif table_operators == ["table_sum"]:
        subfamily = "TABLE_SUM_NUMERIC"
        candidate = "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION"
        required_operation = "sum over a source-bound numeric series"
        minimum_surface = "one source-bound numeric-series contract + sum selector"
    else:
        raise TriageError(f"Unexpected FinQA table-operator shape: {operators}")

    eligible = exact_binding and not contains_precomputed_total
    exclusion = ""
    binding_status = "EXACT_OFFICIAL_TABLE_ROW"
    if not exact_binding:
        eligible = False
        exclusion = "official table row cannot be bound uniquely"
        binding_status = "AMBIGUOUS_TABLE_ROW"
    elif contains_precomputed_total:
        eligible = False
        exclusion = "row mixes a precomputed total column with component columns; generic sum must fail closed"
        binding_status = "EXACT_ROW_AMBIGUOUS_AGGREGATION_RANGE"

    finqa_coordinates: list[dict[str, Any]] = []
    for binding in bindings:
        matches = binding.get("matching_table_row_indices") or []
        if len(matches) != 1:
            continue
        row_index = int(matches[0])
        bound_row = binding.get("bound_row") or []
        for column_index, raw_value in enumerate(bound_row[1:], start=1):
            if _is_numeric_cell(raw_value):
                finqa_coordinates.append(
                    {
                        "coordinate": f"finqa://dev/{baseline['case_id']}/r{row_index}c{column_index}",
                        "row_index": row_index,
                        "column_index": column_index,
                        "raw_value": str(raw_value),
                        "operator": binding.get("operator"),
                    }
                )
    binding_uniqueness = (
        "UNIQUE" if exact_binding and not contains_precomputed_total else
        "AMBIGUOUS" if bindings else "UNBOUND"
    )
    exact_oracle = binding_uniqueness == "UNIQUE" and bool(finqa_coordinates)
    eligible = exact_oracle
    if exact_oracle:
        oracle_proof = {
            "proof_status": "COMPLETE",
            "bound_source_object_ids": [f"finqa://dev/{baseline['case_id']}"],
            "bound_axis_or_section": {
                "axis": "OFFICIAL_ANNOTATED_TABLE_ROW_SERIES",
                "operators": table_operators,
            },
            "bound_member_or_value_coordinates": finqa_coordinates,
            "predicate_or_membership_rule": {"rule_type": "FINQA_NATIVE_TABLE_AGGREGATION"},
            "independently_derived_expected_count": None,
            "binding_uniqueness_status": "UNIQUE",
            "failure_reason": "",
        }
    else:
        oracle_proof = {
            "proof_status": "INCOMPLETE",
            "bound_source_object_ids": [f"finqa://dev/{baseline['case_id']}"],
            "bound_axis_or_section": None,
            "bound_member_or_value_coordinates": finqa_coordinates,
            "predicate_or_membership_rule": None,
            "independently_derived_expected_count": None,
            "binding_uniqueness_status": binding_uniqueness,
            "failure_reason": exclusion,
        }

    source_lineage = {
        "baseline_records_path": _BASELINE_RECORDS,
        "baseline_line_number": baseline.get("_baseline_line_number"),
        "official_split_path": _FINQA_SPLIT,
        "official_split_sha256": manifest_entry.get("selected_split_sha256"),
        "official_repository_commit": manifest_entry.get("resolved_git_commit"),
        "document_index": document_index,
        "document_id": document.get("id"),
        "filename": document.get("filename"),
        "gold_source_keys": sorted((qa.get("gold_inds") or {}).keys()),
        "annotated_table_rows": list(qa.get("ann_table_rows") or []),
        "annotated_text_rows": list(qa.get("ann_text_rows") or []),
        "table_bindings": bindings,
    }
    return {
        "dataset": "finqa",
        "case_id": str(baseline["case_id"]),
        "baseline_failure_detail": str(baseline["failure_detail"]),
        "official_answer_type": "numeric",
        "official_program_or_derivation_shape": " -> ".join(operator.upper() for operator in operators),
        "official_program_or_derivation": program,
        "question": question,
        "semantic_family": "FINQA_TABLE_AGGREGATION",
        "semantic_subfamily": subfamily,
        "required_operation": required_operation,
        "required_input_shape": "ordered numeric values from one explicitly bound table row, preserving headers, units and cell coordinates",
        "required_source_binding": binding_status,
        "current_blocking_boundary": (
            ["OPERATOR", "INPUT_SHAPE_CONTRACT", "SOURCE_BINDING_CONTRACT", "OUTPUT_LABEL_CONTRACT"]
            if argmax_label
            else ["OPERATOR", "INPUT_SHAPE_CONTRACT", "SOURCE_BINDING_CONTRACT"]
        ),
        "existing_contract_representable": False,
        "exact_oracle_available": exact_oracle,
        "generic_financial_document_value": (
            "LOW_UNTIL_RANGE_POLICY_IS_EXPLICIT"
            if contains_precomputed_total
            else "HIGH"
        ),
        "minimum_product_surface": minimum_surface,
        "candidate_capability": candidate,
        "candidate_type": _CANDIDATE_SPECS[candidate].candidate_type,
        "binding_ambiguity": "HIGH" if binding_uniqueness != "UNIQUE" else "LOW",
        "binding_uniqueness_status": binding_uniqueness,
        "oracle_proof": oracle_proof,
        "selection_eligibility": eligible,
        "selection_exclusion_reason": exclusion,
        "source_lineage": source_lineage,
        "diagnostics": {
            "table_operators": table_operators,
            "argmax_label_semantics": argmax_label,
            "contains_precomputed_total_and_components": contains_precomputed_total,
        },
    }


def _tatqa_source_evidence(
    document: Mapping[str, Any], question: Mapping[str, Any]
) -> dict[str, Any]:
    table_payload = document.get("table") if isinstance(document.get("table"), Mapping) else {}
    table = table_payload.get("table") if isinstance(table_payload.get("table"), list) else []
    table_cells = [str(cell) for row in table if isinstance(row, list) for cell in row]
    related_orders = {str(item) for item in question.get("rel_paragraphs") or []}
    related_paragraphs = [
        paragraph
        for paragraph in document.get("paragraphs") or []
        if isinstance(paragraph, Mapping) and str(paragraph.get("order")) in related_orders
    ]
    related_texts = [str(paragraph.get("text") or "") for paragraph in related_paragraphs]
    derivation_items = [
        item.strip()
        for item in str(question.get("derivation") or "").split("##")
        if item.strip()
    ]
    normalised_cells = [_normalise(cell) for cell in table_cells if _normalise(cell)]
    normalised_text = _normalise(" ".join(related_texts))
    item_matches: list[dict[str, Any]] = []
    for item in derivation_items:
        normalised = _normalise(item)
        table_match = any(
            normalised == cell or normalised in cell or cell in normalised
            for cell in normalised_cells
            if normalised and cell
        )
        text_match = bool(normalised and normalised in normalised_text)
        item_matches.append(
            {
                "item": item,
                "table_match": table_match,
                "related_text_match": text_match,
            }
        )
    question_normalised = _normalise(question.get("question"))
    matched_question_cells = sorted(
        {
            cell
            for cell in normalised_cells
            if len(cell) >= 4
            and not re.fullmatch(r"\d{4}", cell)
            and cell not in {"total", "change", "number"}
            and (cell in question_normalised or question_normalised in cell)
        }
    )
    return {
        "table_uid": table_payload.get("uid"),
        "table": table,
        "related_paragraphs": related_paragraphs,
        "derivation_items": derivation_items,
        "derivation_item_source_matches": item_matches,
        "question_matched_table_cells": matched_question_cells,
        "document_context_text": " ".join(
            [str(cell) for row in table if isinstance(row, list) for cell in row]
            + [str(paragraph.get("text") or "") for paragraph in document.get("paragraphs") or [] if isinstance(paragraph, Mapping)]
        ),
    }


def _tatqa_count_record(
    *,
    baseline: Mapping[str, Any],
    document_index: int,
    question_index: int,
    document: Mapping[str, Any],
    question: Mapping[str, Any],
    manifest_entry: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _tatqa_source_evidence(document, question)
    question_text = str(question.get("question") or "")
    question_norm = _normalise(question_text)
    answer_from = str(question.get("answer_from") or "")
    comparator = bool(_COMPARATOR_RE.search(question_text))
    missing_value = "provided in" in question_norm and "not in" in question_norm
    period_predicate = comparator and bool(_PERIOD_RE.search(question_text))
    text_item_count = sum(
        1
        for match in evidence["derivation_item_source_matches"]
        if match["related_text_match"] and not match["table_match"]
    )
    long_derivation_item = any(
        len(_normalise(item).split()) >= 8 for item in evidence["derivation_items"]
    )
    table = evidence["table"]
    table_uid = evidence["table_uid"]

    section_like_rows: list[int] = []
    question_tokens = set(question_norm.split())
    for row_index, row in enumerate(table):
        if not isinstance(row, list) or not row:
            continue
        label = _normalised_row_label(row[0]).rstrip(":")
        label_tokens = set(label.split())
        if (
            len(label_tokens) >= 2
            and len(label_tokens & question_tokens) / len(label_tokens) >= 0.8
            and not any(_numeric_cell_value(cell) is not None for cell in row[1:])
        ):
            section_like_rows.append(row_index)
    composite_prerequisite = (
        comparator
        and period_predicate
        and not _metric_row_candidates(table, question_text)
        and bool(section_like_rows)
    )

    if missing_value:
        subfamily = "TABLE_MISSING_VALUE_CARDINALITY"
        candidate = "SOURCE_BOUND_TABLE_MISSING_VALUE_CARDINALITY"
        required_operation = "count table members satisfying a typed missing-in-one-period and present-in-another predicate"
        required_input_shape = "explicit table range with typed present/missing cell states for two bound periods"
        proof = _missing_value_cardinality_oracle(
            table_uid=table_uid, table=table, question=question_text
        )
    elif comparator and composite_prerequisite:
        subfamily = "COMPOSITE_SECTION_AGGREGATE_PREDICATE_CARDINALITY"
        candidate = "SOURCE_BOUND_COMPOSITE_AGGREGATE_CARDINALITY"
        required_operation = "aggregate section members by period, apply a scalar predicate, then count matching periods"
        required_input_shape = "hierarchical table section plus aggregation and predicate contracts"
        proof = _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason=(
                "the question binds a non-numeric section header rather than one source value row; "
                "a separate aggregation prerequisite is required before cardinality"
            ),
            candidate_coordinates=[{"section_header_rows": section_like_rows}],
        )
    elif comparator and period_predicate:
        subfamily = "TABLE_PERIOD_PREDICATE_CARDINALITY"
        candidate = "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY"
        required_operation = "count source-bound period values satisfying an explicit deterministic scalar predicate"
        required_input_shape = "one uniquely bound metric row across uniquely bound period columns"
        proof = _period_predicate_oracle(
            table_uid=table_uid,
            table=table,
            question=question_text,
            context_text=evidence["document_context_text"],
        )
    elif comparator:
        subfamily = "TABLE_CATEGORY_PREDICATE_CARDINALITY"
        candidate = "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY"
        required_operation = "count source-bound category values satisfying an explicit deterministic scalar predicate"
        required_input_shape = "one uniquely bound period column across an explicitly bounded category range"
        proof = _category_predicate_oracle(
            table_uid=table_uid,
            table=table,
            question=question_text,
            context_text=evidence["document_context_text"],
        )
    elif text_item_count or long_derivation_item:
        subfamily = "TEXT_ENUMERATION_CARDINALITY"
        candidate = "SOURCE_BACKED_TEXT_ENUMERATION_CARDINALITY"
        required_operation = "segment a source paragraph into semantically distinct list members and count them"
        required_input_shape = "source-backed text spans with independently defined member boundaries"
        proof = _empty_oracle_proof(
            table_uid=table_uid,
            uniqueness="AMBIGUOUS",
            reason=(
                "semantic text-member boundaries are not independently encoded; "
                "the official derivation cannot be used as the sole member authority"
            ),
        )
    else:
        subfamily = "TABLE_SECTION_CARDINALITY"
        candidate = "SOURCE_BOUND_TABLE_SECTION_CARDINALITY"
        required_operation = "count members in an explicitly and uniquely bound table section or entity list"
        required_input_shape = "table section with explicit start/end or a whole-table entity range"
        proof = _section_cardinality_oracle(
            table_uid=table_uid, table=table, question=question_text
        )

    candidate_type = _CANDIDATE_SPECS[candidate].candidate_type
    binding_uniqueness = str(proof["binding_uniqueness_status"])
    exact_oracle = (
        proof["proof_status"] == "COMPLETE"
        and binding_uniqueness == "UNIQUE"
        and bool(proof["bound_member_or_value_coordinates"])
        and proof["independently_derived_expected_count"] is not None
    )
    eligible = candidate_type == "PRODUCT_CAPABILITY" and exact_oracle
    exclusion = "" if eligible else str(proof["failure_reason"] or "candidate type is excluded from product ranking")
    binding_status = {
        "UNIQUE": "UNIQUE_SOURCE_COORDINATE_BINDING",
        "AMBIGUOUS": "AMBIGUOUS_SOURCE_BINDING",
        "UNBOUND": "NO_SOURCE_BINDING",
    }[binding_uniqueness]
    ambiguity = "LOW" if binding_uniqueness == "UNIQUE" else "HIGH"

    return {
        "dataset": "tatqa",
        "case_id": str(baseline["case_id"]),
        "baseline_failure_detail": str(baseline["failure_detail"]),
        "official_answer_type": str(question.get("answer_type") or ""),
        "official_program_or_derivation_shape": f"ENUMERATION_CARDINALITY[{len(evidence['derivation_items'])}]",
        "official_program_or_derivation": str(question.get("derivation") or ""),
        "question": question_text,
        "semantic_family": "TATQA_COUNT_CARDINALITY",
        "semantic_subfamily": subfamily,
        "required_operation": required_operation,
        "required_input_shape": required_input_shape,
        "required_source_binding": binding_status,
        "current_blocking_boundary": [
            "ANSWER_TYPE_OUTPUT_CONTRACT",
            "CARDINALITY_OPERATOR",
            "INPUT_SHAPE_CONTRACT",
            "SOURCE_BINDING_CONTRACT",
        ],
        "existing_contract_representable": False,
        "exact_oracle_available": exact_oracle,
        "generic_financial_document_value": "HIGH",
        "minimum_product_surface": "source-bound collection contract + deterministic membership/predicate semantics + integer output",
        "candidate_capability": candidate,
        "candidate_type": candidate_type,
        "binding_ambiguity": ambiguity,
        "binding_uniqueness_status": binding_uniqueness,
        "oracle_proof": proof,
        "selection_eligibility": eligible,
        "selection_exclusion_reason": exclusion,
        "source_lineage": {
            "baseline_records_path": _BASELINE_RECORDS,
            "baseline_line_number": baseline.get("_baseline_line_number"),
            "official_split_path": _TATQA_SPLIT,
            "official_split_sha256": manifest_entry.get("selected_split_sha256"),
            "official_repository_commit": manifest_entry.get("resolved_git_commit"),
            "document_index": document_index,
            "question_index": question_index,
            "question_uid": question.get("uid"),
            "table_uid": table_uid,
            "answer_from": answer_from,
            "related_paragraph_orders": list(question.get("rel_paragraphs") or []),
            "related_paragraph_uids": [
                paragraph.get("uid") for paragraph in evidence["related_paragraphs"]
            ],
            "derivation_item_source_matches": evidence["derivation_item_source_matches"],
            "question_matched_table_cells": evidence["question_matched_table_cells"],
        },
        "diagnostics": {
            "comparator_present": comparator,
            "period_axis_requested": bool(_PERIOD_RE.search(question_text)),
            "missing_value_predicate": missing_value,
            "section_like_rows": section_like_rows,
            "composite_prerequisite": composite_prerequisite,
        },
    }

def _tatqa_function_record(
    *,
    baseline: Mapping[str, Any],
    document_index: int,
    question_index: int,
    document: Mapping[str, Any],
    question: Mapping[str, Any],
    manifest_entry: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _tatqa_source_evidence(document, question)
    derivation = str(question.get("derivation") or "")
    percent_literals = _PERCENT_LITERAL_RE.findall(derivation)
    if len(percent_literals) < 2:
        raise TriageError(f"Unexpected TAT-QA function derivation: {derivation}")
    is_average = "+" in derivation and bool(re.search(r"/\s*\d+\s*$", derivation))
    subfamily = (
        "PERCENT_SERIES_AVERAGE_NORMALIZATION"
        if is_average
        else "PERCENTAGE_POINT_DIFFERENCE_NORMALIZATION"
    )
    table_uid = evidence["table_uid"]
    available: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row_index, row in enumerate(evidence["table"]):
        if not isinstance(row, list):
            continue
        for column_index, cell in enumerate(row):
            key = _normalise(cell)
            if key:
                available[key].append(
                    {
                        "coordinate": _source_coordinate(table_uid, row_index, column_index),
                        "row_index": row_index,
                        "column_index": column_index,
                        "raw_value": str(cell),
                    }
                )
    used: Counter[str] = Counter()
    bound_literals: list[dict[str, Any]] = []
    literal_matches: list[bool] = []
    for literal in percent_literals:
        key = _normalise(f"{literal.lstrip('+-')}%")
        index = used[key]
        matches = available.get(key, [])
        matched = index < len(matches)
        literal_matches.append(matched)
        if matched:
            coordinate = dict(matches[index])
            coordinate["derivation_literal"] = literal
            bound_literals.append(coordinate)
            used[key] += 1
    exact_binding = bool(evidence["table"]) and all(literal_matches)
    candidate = "PERCENT_LITERAL_OPERATOR_NORMALIZATION"
    candidate_type = _CANDIDATE_SPECS[candidate].candidate_type
    uniqueness = "UNIQUE" if exact_binding else "AMBIGUOUS"
    oracle_proof = {
        "proof_status": "COMPLETE" if exact_binding else "INCOMPLETE",
        "bound_source_object_ids": [f"tatqa://table/{table_uid}"] if table_uid else [],
        "bound_axis_or_section": {
            "axis": "PERCENT_LITERALS_IN_OFFICIAL_DERIVATION",
            "scope": "MEASUREMENT_ADAPTER_ONLY",
        } if exact_binding else None,
        "bound_member_or_value_coordinates": bound_literals,
        "predicate_or_membership_rule": {
            "rule_type": "PERCENT_LITERAL_LEXICAL_NORMALIZATION_AUDIT"
        } if exact_binding else None,
        "independently_derived_expected_count": None,
        "binding_uniqueness_status": uniqueness,
        "failure_reason": "" if exact_binding else "percent literals do not map one-to-one to source table cells",
    }
    return {
        "dataset": "tatqa",
        "case_id": str(baseline["case_id"]),
        "baseline_failure_detail": str(baseline["failure_detail"]),
        "official_answer_type": str(question.get("answer_type") or ""),
        "official_program_or_derivation_shape": (
            "PERCENT_LITERAL_SERIES_AVERAGE"
            if is_average
            else "PERCENTAGE_POINT_DIFFERENCE"
        ),
        "official_program_or_derivation": derivation,
        "question": str(question.get("question") or ""),
        "semantic_family": "TATQA_FUNCTION_DERIVATION",
        "semantic_subfamily": subfamily,
        "required_operation": "preserve arithmetic operators when normalising adjacent percent literals before AST parsing",
        "required_input_shape": "source-backed percent scalar literals in an arithmetic derivation",
        "required_source_binding": (
            "UNIQUE_SOURCE_COORDINATE_BINDING" if exact_binding else "AMBIGUOUS_SOURCE_BINDING"
        ),
        "current_blocking_boundary": ["DERIVATION_LEXER_NORMALIZATION"],
        "existing_contract_representable": False,
        "exact_oracle_available": exact_binding,
        "generic_financial_document_value": "MEASUREMENT_REPAIR_ONLY",
        "minimum_product_surface": "evaluation-side percent literal normalizer repair; no product capability",
        "candidate_capability": candidate,
        "candidate_type": candidate_type,
        "binding_ambiguity": "LOW" if exact_binding else "HIGH",
        "binding_uniqueness_status": uniqueness,
        "oracle_proof": oracle_proof,
        "selection_eligibility": False,
        "selection_exclusion_reason": "measurement-adapter repair is excluded from product-capability ranking",
        "source_lineage": {
            "baseline_records_path": _BASELINE_RECORDS,
            "baseline_line_number": baseline.get("_baseline_line_number"),
            "official_split_path": _TATQA_SPLIT,
            "official_split_sha256": manifest_entry.get("selected_split_sha256"),
            "official_repository_commit": manifest_entry.get("resolved_git_commit"),
            "document_index": document_index,
            "question_index": question_index,
            "question_uid": question.get("uid"),
            "table_uid": table_uid,
            "answer_from": question.get("answer_from"),
            "percent_literals": percent_literals,
            "percent_literal_source_matches": literal_matches,
        },
        "diagnostics": {
            "percent_literal_count": len(percent_literals),
            "normalization_failure": "operator adjacency became Python call syntax",
            "candidate_type": candidate_type,
        },
    }

def _build_taxonomy(
    *,
    unsupported: Sequence[Mapping[str, Any]],
    finqa_index: Mapping[str, tuple[int, Mapping[str, Any]]],
    tatqa_index: Mapping[str, tuple[int, int, Mapping[str, Any], Mapping[str, Any]]],
    source_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    finqa_manifest = _source_manifest_entry(source_manifest, "finqa")
    tatqa_manifest = _source_manifest_entry(source_manifest, "tatqa")
    for baseline in unsupported:
        dataset = str(baseline.get("dataset"))
        case_id = str(baseline.get("case_id"))
        if dataset == "finqa":
            indexed = finqa_index.get(case_id)
            if indexed is None:
                raise TriageError(f"FinQA source row missing: {case_id}")
            document_index, document = indexed
            rows.append(
                _finqa_record(
                    baseline=baseline,
                    document_index=document_index,
                    document=document,
                    manifest_entry=finqa_manifest,
                )
            )
        elif dataset == "tatqa":
            indexed = tatqa_index.get(case_id)
            if indexed is None:
                raise TriageError(f"TAT-QA source question missing: {case_id}")
            document_index, question_index, document, question = indexed
            if baseline.get("failure_detail") == "answer_type:count":
                rows.append(
                    _tatqa_count_record(
                        baseline=baseline,
                        document_index=document_index,
                        question_index=question_index,
                        document=document,
                        question=question,
                        manifest_entry=tatqa_manifest,
                    )
                )
            elif baseline.get("failure_detail") == "function_call":
                rows.append(
                    _tatqa_function_record(
                        baseline=baseline,
                        document_index=document_index,
                        question_index=question_index,
                        document=document,
                        question=question,
                        manifest_entry=tatqa_manifest,
                    )
                )
            else:
                raise TriageError(f"Unexpected TAT-QA unsupported detail: {baseline.get('failure_detail')}")
        else:
            raise TriageError(f"Unexpected dataset: {dataset}")
    return sorted(rows, key=lambda row: (row["dataset"], row["case_id"]))


def _row_has_complete_product_proof(row: Mapping[str, Any]) -> bool:
    proof = row.get("oracle_proof")
    lineage = row.get("source_lineage")
    if not isinstance(proof, Mapping) or not isinstance(lineage, Mapping):
        return False
    return (
        row.get("candidate_type") == "PRODUCT_CAPABILITY"
        and row.get("selection_eligibility") is True
        and row.get("exact_oracle_available") is True
        and row.get("binding_uniqueness_status") == "UNIQUE"
        and proof.get("proof_status") == "COMPLETE"
        and proof.get("binding_uniqueness_status") == "UNIQUE"
        and bool(proof.get("bound_source_object_ids"))
        and bool(proof.get("bound_member_or_value_coordinates"))
        and bool(lineage.get("official_split_sha256"))
        and bool(lineage.get("official_repository_commit"))
    )


def _candidate_evaluation(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    assigned: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        assigned[str(row["candidate_capability"])].append(row)
    candidates: list[dict[str, Any]] = []
    for name, spec in sorted(_CANDIDATE_SPECS.items()):
        if spec.candidate_type not in _CANDIDATE_TYPES:
            raise TriageError(f"Unknown candidate type: {spec.candidate_type}")
        family_rows = assigned.get(name, [])
        recoverable = sorted(
            str(row["case_id"])
            for row in family_rows
            if _row_has_complete_product_proof(row)
        )
        measurement_opportunities = sorted(
            str(row["case_id"])
            for row in family_rows
            if row.get("candidate_type") == "MEASUREMENT_ADAPTER_REPAIR"
            and row.get("exact_oracle_available") is True
            and row.get("binding_uniqueness_status") == "UNIQUE"
        )
        source_backed = bool(recoverable) and all(
            _row_has_complete_product_proof(row)
            for row in family_rows
            if str(row["case_id"]) in recoverable
        )
        exact_oracle = bool(recoverable) and all(
            row.get("exact_oracle_available") is True
            for row in family_rows
            if str(row["case_id"]) in recoverable
        )
        rules = {
            "candidate_type_is_product_capability": spec.candidate_type == "PRODUCT_CAPABILITY",
            "generic_financial_document_capability": spec.generic_capability,
            "source_backed_operands_with_lineage": source_backed,
            "exact_deterministic_oracle": exact_oracle,
            "fail_closed_on_ambiguity": spec.fail_closed_design_available,
            "independently_testable_without_production_routing": spec.independently_testable,
            "projected_count_computed_from_frozen_taxonomy": True,
        }
        eligible = all(rules.values())
        rank_key = [
            -len(recoverable),
            spec.required_product_surface_rank,
            spec.binding_ambiguity_rank,
            spec.new_contract_type_count,
            name,
        ]
        candidates.append(
            {
                "candidate_name": name,
                "candidate_type": spec.candidate_type,
                "assigned_case_count": len(family_rows),
                "projected_recoverable_case_count": len(recoverable),
                "recoverable_case_ids": recoverable,
                "measurement_repair_case_count": len(measurement_opportunities),
                "measurement_repair_case_ids": measurement_opportunities,
                "eligibility_rules": rules,
                "eligible": eligible,
                "failed_rules": sorted(rule for rule, passed in rules.items() if not passed),
                "required_product_surface_rank": spec.required_product_surface_rank,
                "binding_ambiguity_rank": spec.binding_ambiguity_rank,
                "new_contract_type_count": spec.new_contract_type_count,
                "rank_key": rank_key,
                "required_contract_changes": list(spec.required_contract_changes),
                "required_product_modules": list(spec.required_product_modules),
                "required_evaluation_changes": list(spec.required_evaluation_changes),
                "explicit_non_goals": list(spec.explicit_non_goals),
            }
        )
    return candidates

def rank_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return eligible candidates in the frozen deterministic ranking order."""
    return sorted(
        (
            dict(candidate)
            for candidate in candidates
            if candidate.get("candidate_type") == "PRODUCT_CAPABILITY"
            and candidate.get("eligible")
        ),
        key=lambda candidate: tuple(candidate["rank_key"]),
    )


def select_capability(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    ranked = rank_candidates(candidates)
    return ranked[0] if ranked else None


def _representative_cases(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[str(row["semantic_family"])].append(row)
    result: dict[str, list[str]] = {}
    for family, family_rows in sorted(by_family.items()):
        chosen: list[str] = []
        by_subfamily: dict[str, list[str]] = defaultdict(list)
        for row in family_rows:
            by_subfamily[str(row["semantic_subfamily"])].append(str(row["case_id"]))
        for _subfamily, case_ids in sorted(by_subfamily.items()):
            chosen.append(sorted(case_ids)[0])
            if len(chosen) == 2:
                break
        if len(chosen) < 2:
            for case_id in sorted(str(row["case_id"]) for row in family_rows):
                if case_id not in chosen:
                    chosen.append(case_id)
                if len(chosen) == 2:
                    break
        if len(chosen) < 2:
            raise TriageError(f"Family lacks two representative cases: {family}")
        result[family] = chosen
    return result


def _apply_projected_counts(
    rows: list[dict[str, Any]], candidates: Sequence[Mapping[str, Any]]
) -> None:
    counts = {
        str(candidate["candidate_name"]): int(candidate["projected_recoverable_case_count"])
        for candidate in candidates
    }
    for row in rows:
        row["estimated_recoverable_case_count"] = counts[str(row["candidate_capability"])]


def _aggregate(
    *,
    root: Path,
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    representatives: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_inputs": {
            "baseline_records": {"path": _BASELINE_RECORDS, "sha256": _sha256(root / _BASELINE_RECORDS)},
            "baseline_report": {"path": _BASELINE_REPORT, "sha256": _sha256(root / _BASELINE_REPORT)},
            "source_manifest": {"path": _SOURCE_MANIFEST, "sha256": _sha256(root / _SOURCE_MANIFEST)},
            "finqa_split": {"path": _FINQA_SPLIT, "sha256": _sha256(root / _FINQA_SPLIT)},
            "tatqa_split": {"path": _TATQA_SPLIT, "sha256": _sha256(root / _TATQA_SPLIT)},
        },
        "case_count": len(rows),
        "unique_case_count": len({str(row["case_id"]) for row in rows}),
        "dataset_totals": _counter_dict(str(row["dataset"]) for row in rows),
        "semantic_family_totals": _counter_dict(str(row["semantic_family"]) for row in rows),
        "semantic_subfamily_totals": _counter_dict(str(row["semantic_subfamily"]) for row in rows),
        "failure_detail_totals": _counter_dict(str(row["baseline_failure_detail"]) for row in rows),
        "candidate_assignment_totals": _counter_dict(str(row["candidate_capability"]) for row in rows),
        "candidate_type_totals": _counter_dict(str(row["candidate_type"]) for row in rows),
        "binding_uniqueness_totals": _counter_dict(str(row["binding_uniqueness_status"]) for row in rows),
        "selection_eligible_totals": _counter_dict(
            str(row["candidate_capability"])
            for row in rows
            if row["selection_eligibility"]
        ),
        "binding_ambiguity_totals": _counter_dict(str(row["binding_ambiguity"]) for row in rows),
        "exact_oracle_available_count": sum(bool(row["exact_oracle_available"]) for row in rows),
        "eligible_product_case_count": sum(
            bool(row["selection_eligibility"]) and row["candidate_type"] == "PRODUCT_CAPABILITY"
            for row in rows
        ),
        "measurement_adapter_repair_summary": {
            "candidate_count": len(
                {
                    row["candidate_capability"]
                    for row in rows
                    if row["candidate_type"] == "MEASUREMENT_ADAPTER_REPAIR"
                }
            ),
            "case_count": sum(
                row["candidate_type"] == "MEASUREMENT_ADAPTER_REPAIR"
                for row in rows
            ),
            "candidates": _counter_dict(
                str(row["candidate_capability"])
                for row in rows
                if row["candidate_type"] == "MEASUREMENT_ADAPTER_REPAIR"
            ),
            "excluded_from_product_ranking": True,
        },
        "representative_cases_by_family": {
            family: list(case_ids) for family, case_ids in sorted(representatives.items())
        },
        "candidate_evaluations": list(candidates),
        "provider_call_count": 0,
        "model_call_count": 0,
        "network_call_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "active_route_authority": False,
        "shadow_promotion_authority": False,
        "production_correctness_authority": False,
    }


def _decision(
    *,
    selected: Mapping[str, Any] | None,
    candidates: Sequence[Mapping[str, Any]],
    aggregate_report: Mapping[str, Any],
) -> dict[str, Any]:
    combined = aggregate_report["datasets"]["combined"]
    base_representable = int(combined["c3_representable_count"])
    base_correct = int(combined["terminal_executed_correct_count"])
    numeric_eligible = int(combined["numeric_eligible_count"])
    product_candidates = [
        candidate
        for candidate in candidates
        if candidate["candidate_type"] == "PRODUCT_CAPABILITY"
    ]
    ranking_trace = sorted(
        (
            {
                "candidate_name": candidate["candidate_name"],
                "candidate_type": candidate["candidate_type"],
                "eligible": candidate["eligible"],
                "projected_recoverable_case_count": candidate["projected_recoverable_case_count"],
                "rank_key": candidate["rank_key"],
                "failed_rules": candidate["failed_rules"],
            }
            for candidate in product_candidates
        ),
        key=lambda item: (not item["eligible"], tuple(item["rank_key"])),
    )
    measurement_repairs = sorted(
        (
            {
                "candidate_name": candidate["candidate_name"],
                "candidate_type": candidate["candidate_type"],
                "case_count": candidate["measurement_repair_case_count"],
                "case_ids": candidate["measurement_repair_case_ids"],
                "excluded_from_product_ranking": True,
            }
            for candidate in candidates
            if candidate["candidate_type"] == "MEASUREMENT_ADAPTER_REPAIR"
        ),
        key=lambda item: item["candidate_name"],
    )
    common = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "selection_rule_scope": "PRODUCT_CAPABILITY_ONLY",
        "selection_rule_trace": ranking_trace,
        "measurement_adapter_repair_opportunities": measurement_repairs,
        "active_route_authority": False,
        "shadow_promotion_authority": False,
        "production_correctness_authority": False,
    }
    if selected is None:
        return {
            **common,
            "selected_capability": "NO_ELIGIBLE_CAPABILITY",
            "selected_candidate_type": None,
            "projected_recoverable_case_count": 0,
            "projected_combined_representable_count": base_representable,
            "projected_combined_effective_oracle_accuracy_upper_bound": {
                "label": "UPPER_BOUND_ONLY",
                "numerator": base_correct,
                "denominator": numeric_eligible,
                "value": base_correct / numeric_eligible,
            },
            "required_contract_changes": [],
            "required_product_modules": [],
            "required_evaluation_changes": [],
            "explicit_non_goals": ["No forced recommendation when eligibility rules fail."],
            "representative_case_ids": [],
        }
    if selected["candidate_type"] != "PRODUCT_CAPABILITY":
        raise TriageError("Selected candidate is not a product capability")
    recovered = int(selected["projected_recoverable_case_count"])
    return {
        **common,
        "selected_capability": selected["candidate_name"],
        "selected_candidate_type": selected["candidate_type"],
        "projected_recoverable_case_count": recovered,
        "projected_combined_representable_count": base_representable + recovered,
        "projected_combined_effective_oracle_accuracy_upper_bound": {
            "label": "UPPER_BOUND_ONLY_ASSUMES_ALL_NEWLY_REPRESENTABLE_CASES_EXECUTE_CORRECTLY",
            "numerator": base_correct + recovered,
            "denominator": numeric_eligible,
            "value": (base_correct + recovered) / numeric_eligible,
        },
        "required_contract_changes": list(selected["required_contract_changes"]),
        "required_product_modules": list(selected["required_product_modules"]),
        "required_evaluation_changes": list(selected["required_evaluation_changes"]),
        "explicit_non_goals": list(selected["explicit_non_goals"]),
        "representative_case_ids": list(selected["recoverable_case_ids"][:3]),
    }


def build_capability_decision(
    *,
    candidates: Sequence[Mapping[str, Any]],
    aggregate_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the frozen selection rule, including the no-eligible outcome."""
    return _decision(
        selected=select_capability(candidates),
        candidates=candidates,
        aggregate_report=aggregate_report,
    )


def _markdown_report(
    *, rows: Sequence[Mapping[str, Any]], aggregate: Mapping[str, Any], decision: Mapping[str, Any]
) -> str:
    fence = chr(96) * 3
    tick = chr(96)
    family_totals = aggregate["semantic_family_totals"]
    subfamily_totals = aggregate["semantic_subfamily_totals"]
    selected = decision["selected_capability"]
    projected = decision["projected_recoverable_case_count"]
    upper = decision["projected_combined_effective_oracle_accuracy_upper_bound"]
    ranked = [item for item in decision["selection_rule_trace"] if item["eligible"]]
    measurement = decision["measurement_adapter_repair_opportunities"]
    eligible_cardinality = [
        row for row in rows
        if row["semantic_family"] == "TATQA_COUNT_CARDINALITY"
        and row["selection_eligibility"]
    ]
    ineligible_cardinality = [
        row for row in rows
        if row["semantic_family"] == "TATQA_COUNT_CARDINALITY"
        and not row["selection_eligibility"]
    ]
    lines = [
        "# C3 Unsupported Operator Capability Triage V1 - Repaired",
        "",
        "## Frozen population",
        "",
        fence + "text",
        f"UNSUPPORTED_OPERATOR = {len(rows)}",
        f"FinQA = {aggregate['dataset_totals'].get('finqa', 0)}",
        f"TAT-QA = {aggregate['dataset_totals'].get('tatqa', 0)}",
        fence,
        "",
        "## Source-backed semantic families",
        "",
        "| Family | Cases |",
        "|---|---:|",
    ]
    for family, count in family_totals.items():
        lines.append(f"| {family} | {count} |")
    lines.extend(["", "| Subfamily | Cases |", "|---|---:|"])
    for family, count in subfamily_totals.items():
        lines.append(f"| {family} | {count} |")
    lines.extend([
        "",
        "## Repaired source-binding gate",
        "",
        "Every eligible cardinality case now requires:",
        "",
        fence + "text",
        "candidate_type = PRODUCT_CAPABILITY",
        "binding_uniqueness_status = UNIQUE",
        "oracle_proof.proof_status = COMPLETE",
        "non-empty source-coordinate operands/member set",
        "independently recomputed expected count",
        fence,
        "",
        f"Eligible TAT-QA cardinality cases: **{len(eligible_cardinality)}**.",
        "",
        f"Fail-closed TAT-QA cardinality cases: **{len(ineligible_cardinality)}**.",
        "",
        "Zero rows, duplicate rows or sections, mixed amount-percentage columns, unclear ranges, text segmentation and composite prerequisites do not receive exact-Oracle or product-coverage credit.",
        "",
        "## Product capability decision",
        "",
        f"Selected capability experiment: {tick}{selected}{tick}",
        "",
        f"Selected candidate type: {tick}{decision.get('selected_candidate_type')}{tick}",
        "",
        f"Projected recoverable product cases: **{projected}**.",
        "",
        f"Projected combined representable count: **{decision['projected_combined_representable_count']}**.",
        "",
        "Projected combined effective Oracle accuracy upper bound: "
        f"**{upper['numerator']}/{upper['denominator']} = {upper['value']:.6%}**.",
        "",
        "> This is an upper bound only. It is not a post-change accuracy result.",
        "",
        "## Eligible product ranking trace",
        "",
        "| Rank | Candidate | Type | Projected cases | Rank key |",
        "|---:|---|---|---:|---|",
    ])
    for index, item in enumerate(ranked, start=1):
        lines.append(
            f"| {index} | {item['candidate_name']} | {item['candidate_type']} | "
            f"{item['projected_recoverable_case_count']} | {tick}{item['rank_key']}{tick} |"
        )
    lines.extend([
        "",
        "## Measurement-adapter repair opportunities",
        "",
        "These cases remain visible but are excluded from every product rank key and product coverage metric.",
        "",
        "| Candidate | Type | Cases | Product ranking |",
        "|---|---|---:|---|",
    ])
    for item in measurement:
        lines.append(
            f"| {item['candidate_name']} | {item['candidate_type']} | {item['case_count']} | Excluded |"
        )
    lines.extend([
        "",
        "The five percent-literal cases diagnose a TAT-QA evaluation derivation-normalizer defect. This task does not repair that adapter and does not classify it as a FinDocQA product capability.",
        "",
        "## Why the raw FinQA bucket is not copied directly",
        "",
        "- one case requires argmax label output rather than a scalar maximum;",
        "- one sum case mixes a precomputed total column with component columns and fails closed;",
        "- the remaining 33 cases have unique official table-row coordinates and form the selected source-bound numeric-series aggregation experiment.",
        "",
        "## Authority boundaries",
        "",
        fence + "text",
        "active_route_authority = false",
        "shadow_promotion_authority = false",
        "production_correctness_authority = false",
        "provider/model/network calls = 0",
        "tokens = 0",
        fence,
        "",
    ])
    return chr(10).join(lines)


def build_triage(
    *,
    root: str | Path,
    baseline_records_path: str | Path = _BASELINE_RECORDS,
    baseline_report_path: str | Path = _BASELINE_REPORT,
    source_manifest_path: str | Path = _SOURCE_MANIFEST,
    finqa_split_path: str | Path = _FINQA_SPLIT,
    tatqa_split_path: str | Path = _TATQA_SPLIT,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str]:
    """Build the complete deterministic taxonomy and capability decision."""
    root_path = Path(root).resolve()
    records = _load_jsonl(root_path / baseline_records_path)
    aggregate_report = _load_json(root_path / baseline_report_path)
    source_manifest = _load_json(root_path / source_manifest_path)
    unsupported = _validate_frozen_inputs(
        root=root_path,
        records=records,
        aggregate_report=aggregate_report,
        source_manifest=source_manifest,
    )
    finqa_index = _index_finqa(_load_json(root_path / finqa_split_path))
    tatqa_index = _index_tatqa(_load_json(root_path / tatqa_split_path))
    rows = _build_taxonomy(
        unsupported=unsupported,
        finqa_index=finqa_index,
        tatqa_index=tatqa_index,
        source_manifest=source_manifest,
    )
    family_totals = _counter_dict(str(row["semantic_family"]) for row in rows)
    if family_totals != EXPECTED_FAMILIES:
        raise TriageError(f"Semantic family totals differ: {family_totals}")
    candidates = _candidate_evaluation(rows)
    _apply_projected_counts(rows, candidates)
    representatives = _representative_cases(rows)
    aggregate = _aggregate(
        root=root_path,
        rows=rows,
        candidates=candidates,
        representatives=representatives,
    )
    decision = build_capability_decision(
        candidates=candidates,
        aggregate_report=aggregate_report,
    )
    report = _markdown_report(rows=rows, aggregate=aggregate, decision=decision)
    return rows, aggregate, decision, report


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_triage_outputs(
    *,
    root: str | Path,
    output_dir: str | Path = "evaluation_artifacts/c3_unsupported_operator_triage_v1",
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    root_path = Path(root).resolve()
    rows, aggregate, decision, report = build_triage(root=root_path)
    output_path = root_path / output_dir
    output_path.mkdir(parents=True, exist_ok=True)
    jsonl = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    (output_path / "per_case_taxonomy.jsonl").write_text(jsonl, encoding="utf-8", newline="\n")
    (output_path / "aggregate_taxonomy.json").write_text(
        _canonical_json(aggregate), encoding="utf-8", newline="\n"
    )
    (output_path / "capability_decision.json").write_text(
        _canonical_json(decision), encoding="utf-8", newline="\n"
    )
    (output_path / "report.md").write_text(report, encoding="utf-8", newline="\n")
    return rows, aggregate, decision


__all__ = [
    "CandidateSpec",
    "TriageError",
    "build_capability_decision",
    "build_triage",
    "rank_candidates",
    "select_capability",
    "write_triage_outputs",
]
