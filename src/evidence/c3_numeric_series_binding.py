"""Fail-closed binding of structured-table rows into one C3-M SUM request.

This module constructs a source-bound request only.  It does not execute C3-M,
change solver routing, call a model, or infer missing table rows.  A request is
returned only when the question expresses an unambiguous SUM intent and the
structured-table candidates independently prove one complete, contiguous table
range with one label column and one uniquely selected numeric column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from calculation import (
    AggregationOutputOperation,
    AggregationSelector,
    ExecutionGateFact,
    FormulaSourceRef,
    SeriesAggregationOutputSpec,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesAggregationRequest,
    SourceBoundNumericSeriesItem,
    SourceSeriesBindingStatus,
)
from contracts import EvidenceBundle, EvidenceCandidate


SCHEMA_VERSION = "c3-source-bound-sum-series-binder/v1"
RANGE_PROOF_VERSION = "structured-table-range/v1"

QUESTION_SUM_INTENT_MISSING = "QUESTION_SUM_INTENT_MISSING"
QUESTION_AGGREGATION_AMBIGUOUS = "QUESTION_AGGREGATION_AMBIGUOUS"
EMPTY_CANDIDATES = "EMPTY_CANDIDATES"
NON_STRUCTURED_TABLE_CANDIDATE = "NON_STRUCTURED_TABLE_CANDIDATE"
MALFORMED_CANDIDATE_METADATA = "MALFORMED_CANDIDATE_METADATA"
CROSS_DOCUMENT_TABLE = "CROSS_DOCUMENT_TABLE"
CROSS_PAGE_TABLE = "CROSS_PAGE_TABLE"
CROSS_TABLE = "CROSS_TABLE"
SOURCE_OBJECT_MISMATCH = "SOURCE_OBJECT_MISMATCH"
TABLE_SOURCE_IDENTITY_MISMATCH = "TABLE_SOURCE_IDENTITY_MISMATCH"
ROW_SOURCE_IDENTITY_MISMATCH = "ROW_SOURCE_IDENTITY_MISMATCH"
DUPLICATE_CANONICAL_SOURCE = "DUPLICATE_CANONICAL_SOURCE"
RANGE_PROOF_MISSING = "RANGE_PROOF_MISSING"
RANGE_PROOF_INCONSISTENT = "RANGE_PROOF_INCONSISTENT"
RANGE_PROOF_DIGEST_MISMATCH = "RANGE_PROOF_DIGEST_MISMATCH"
ROW_INDEX_GAP = "ROW_INDEX_GAP"
ROW_INDEX_DUPLICATE = "ROW_INDEX_DUPLICATE"
ROW_SPAN_START_INVALID = "ROW_SPAN_START_INVALID"
ROW_SPAN_END_INVALID = "ROW_SPAN_END_INVALID"
ROW_COUNT_MISMATCH = "ROW_COUNT_MISMATCH"
CANDIDATE_SET_INCOMPLETE = "CANDIDATE_SET_INCOMPLETE"
HEADER_SCHEMA_INCONSISTENT = "HEADER_SCHEMA_INCONSISTENT"
COLUMN_COUNT_MISMATCH = "COLUMN_COUNT_MISMATCH"
NUMERIC_COLUMN_MISSING = "NUMERIC_COLUMN_MISSING"
NUMERIC_COLUMN_AMBIGUOUS = "NUMERIC_COLUMN_AMBIGUOUS"
LABEL_COLUMN_MISSING = "LABEL_COLUMN_MISSING"
LABEL_COLUMN_AMBIGUOUS = "LABEL_COLUMN_AMBIGUOUS"
LABEL_VALUE_EMPTY = "LABEL_VALUE_EMPTY"
NUMERIC_VALUE_EMPTY = "NUMERIC_VALUE_EMPTY"
NUMERIC_VALUE_INVALID = "NUMERIC_VALUE_INVALID"
NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
MIXED_UNITS = "MIXED_UNITS"
MIXED_DIMENSIONS = "MIXED_DIMENSIONS"
PERCENT_NOT_SUPPORTED = "PERCENT_NOT_SUPPORTED"
SUMMARY_DETAIL_CONFLICT = "SUMMARY_DETAIL_CONFLICT"
METRIC_HEADER_MISMATCH = "METRIC_HEADER_MISMATCH"

_SUM_TERMS = ("合计", "总和", "共计", "求和", "之和", "总计")
_CONFLICTING_AGGREGATION_TERMS = (
    "平均",
    "均值",
    "最大",
    "最高",
    "最小",
    "最低",
    "相差",
    "差额",
    "增长率",
    "增幅",
    "占比",
    "比例",
)
_SUMMARY_LABELS = ("total", "合计", "总计", "小计", "gross")
_SUPPORTED_AMOUNT_UNITS = ("亿元", "万元", "元")
_UNIT_RE = re.compile(r"(亿元|万元|元|%|％)")
_TRAILING_UNIT_RE = re.compile(r"(亿元|万元|元|%|％)\s*$")
_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_GENERIC_HEADER_TOKENS = ("数值", "值", "金额", "数据")
_METRIC_QUALIFIERS = (
    "本期",
    "上期",
    "当期",
    "同期",
    "本年",
    "上年",
    "当年",
    "累计",
    "期初",
    "期末",
)


def _unique_reasons(reasons: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for reason in reasons if reason))


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _header_unit(header: str) -> str:
    matches = _UNIT_RE.findall(str(header or ""))
    normalized = ["%" if value == "％" else value for value in matches]
    return normalized[0] if len(set(normalized)) == 1 and normalized else ""


def _header_metric(header: str) -> str:
    value = str(header or "")
    value = re.sub(r"[（(][^）)]*(?:亿元|万元|元|%|％)[^）)]*[）)]", "", value)
    value = _UNIT_RE.sub("", value)
    value = re.sub(r"[\s_\-—:：/（）()\[\]【】]", "", value)
    return value.strip()


def _metric_base(metric: str) -> str:
    value = _compact(metric)
    for qualifier in _METRIC_QUALIFIERS:
        value = value.replace(qualifier, "")
    return value


def _metric_matches_question(metric: str, question_text: str) -> bool:
    metric_compact = _compact(metric)
    question_compact = _compact(question_text)
    if not metric_compact:
        return False
    if metric_compact in question_compact:
        return True
    base = _metric_base(metric)
    return len(base) >= 2 and base in question_compact


@dataclass(frozen=True)
class _ParsedNumber:
    status: str
    value: Decimal | None = None
    unit: str = ""
    dimension: str = ""


def _parse_number(raw: object, header_unit: str) -> _ParsedNumber:
    if not isinstance(raw, str):
        return _ParsedNumber(NUMERIC_VALUE_INVALID)
    text = raw.strip()
    if not text:
        return _ParsedNumber(NUMERIC_VALUE_EMPTY)

    negative_parentheses = False
    if text.startswith("(") or text.endswith(")"):
        if not (text.startswith("(") and text.endswith(")")):
            return _ParsedNumber(NUMERIC_VALUE_INVALID)
        negative_parentheses = True
        text = text[1:-1].strip()

    cell_unit = ""
    unit_match = _TRAILING_UNIT_RE.search(text)
    if unit_match:
        cell_unit = "%" if unit_match.group(1) == "％" else unit_match.group(1)
        text = text[: unit_match.start()].strip()

    normalized = text.replace(",", "").strip()
    if normalized.upper() in {
        "NAN",
        "+NAN",
        "-NAN",
        "INF",
        "+INF",
        "-INF",
        "INFINITY",
        "+INFINITY",
        "-INFINITY",
    }:
        return _ParsedNumber(NON_FINITE_NUMBER)
    if not _DECIMAL_RE.fullmatch(normalized):
        return _ParsedNumber(NUMERIC_VALUE_INVALID)
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return _ParsedNumber(NUMERIC_VALUE_INVALID)
    if not value.is_finite():
        return _ParsedNumber(NON_FINITE_NUMBER)
    if negative_parentheses:
        value = -abs(value)

    normalized_header_unit = "%" if header_unit == "％" else header_unit
    if cell_unit and normalized_header_unit and cell_unit != normalized_header_unit:
        return _ParsedNumber(MIXED_UNITS)
    unit = cell_unit or normalized_header_unit or "number"
    if unit in _SUPPORTED_AMOUNT_UNITS:
        dimension = "currency"
    elif unit == "%":
        dimension = "percentage"
    else:
        dimension = "number"
    return _ParsedNumber("OK", value=value, unit=unit, dimension=dimension)


def _range_digest(
    *,
    doc_id: str,
    page_idx: int,
    table_index: int,
    headers: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "proof_version": RANGE_PROOF_VERSION,
        "doc_id": doc_id,
        "page_idx": page_idx,
        "table_index": table_index,
        "headers": list(headers),
        "rows": [
            {
                "row_index": row["row_index"],
                "canonical_source": row["canonical_source"],
                "cell_texts": list(row["cell_texts"]),
            }
            for row in rows
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SourceBoundSumSeriesBindingResult:
    ready: bool
    request: SourceBoundNumericSeriesAggregationRequest | None = None
    reasons: tuple[str, ...] = ()
    trace: tuple[Mapping[str, object], ...] = ()
    source_refs: tuple[FormulaSourceRef, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "request": (
                {
                    "series": self.request.series.to_dict(),
                    "selectors": [
                        selector.value
                        if isinstance(selector, AggregationSelector)
                        else str(selector)
                        for selector in self.request.selectors
                    ],
                    "output": {
                        "operation": (
                            self.request.output.operation.value
                            if isinstance(
                                self.request.output.operation,
                                AggregationOutputOperation,
                            )
                            else str(self.request.output.operation)
                        ),
                        "operands": [
                            operand.value
                            if isinstance(operand, AggregationSelector)
                            else str(operand)
                            for operand in self.request.output.operands
                        ],
                        "output_kind": self.request.output.output_kind,
                        "output_semantics": self.request.output.output_semantics,
                    },
                    "question_aggregation_match": {
                        "passed": self.request.question_aggregation_match.passed,
                        "reasons": list(
                            self.request.question_aggregation_match.reasons
                        ),
                    },
                }
                if self.request is not None
                else None
            ),
            "reasons": list(self.reasons),
            "trace": [dict(item) for item in self.trace],
            "source_refs": [item.to_dict() for item in self.source_refs],
            "metadata": dict(self.metadata),
        }


class SourceBoundSumSeriesBinder:
    """Construct one fail-closed SUM request from structured-table evidence."""

    @staticmethod
    def _failure(
        reasons: Sequence[str],
        *,
        trace: Sequence[Mapping[str, object]] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> SourceBoundSumSeriesBindingResult:
        return SourceBoundSumSeriesBindingResult(
            ready=False,
            request=None,
            reasons=_unique_reasons(reasons),
            trace=tuple(dict(item) for item in trace),
            source_refs=(),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _intent(question_text: str) -> tuple[bool, tuple[str, ...]]:
        compact = _compact(question_text)
        has_sum = any(term in compact for term in _SUM_TERMS)
        conflicts = tuple(
            term for term in _CONFLICTING_AGGREGATION_TERMS if term in compact
        )
        reasons: list[str] = []
        if not has_sum:
            reasons.append(QUESTION_SUM_INTENT_MISSING)
        if has_sum and conflicts:
            reasons.append(QUESTION_AGGREGATION_AMBIGUOUS)
        return not reasons, tuple(reasons)

    def bind(self, bundle: EvidenceBundle) -> SourceBoundSumSeriesBindingResult:
        intent_ready, intent_reasons = self._intent(bundle.question.text)
        trace: list[Mapping[str, object]] = [
            {
                "stage": "question_intent",
                "ready": intent_ready,
                "question": bundle.question.text,
                "reasons": list(intent_reasons),
            }
        ]
        if not intent_ready:
            return self._failure(intent_reasons, trace=trace)

        candidates = tuple(bundle.candidates or ())
        if not candidates:
            return self._failure((EMPTY_CANDIDATES,), trace=trace)

        normalized_rows: list[dict[str, Any]] = []
        metadata_errors: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, EvidenceCandidate):
                metadata_errors.append(MALFORMED_CANDIDATE_METADATA)
                continue
            metadata = candidate.metadata
            if not isinstance(metadata, Mapping):
                metadata_errors.append(MALFORMED_CANDIDATE_METADATA)
                continue
            if (
                metadata.get("structured_table_evidence") is not True
                or metadata.get("source_kind") != "mineru_structured_table"
            ):
                metadata_errors.append(NON_STRUCTURED_TABLE_CANDIDATE)
                continue

            doc_id = metadata.get("doc_id")
            page_idx = metadata.get("page_idx")
            table_index = metadata.get("table_index")
            row_index = metadata.get("row_index")
            headers = metadata.get("headers")
            cell_texts = metadata.get("cell_texts")
            mineru_json_source = metadata.get("mineru_json_source")
            canonical_source = metadata.get("canonical_source")
            table_source_object_id = metadata.get("table_source_object_id")
            if not (
                isinstance(doc_id, str)
                and doc_id
                and type(page_idx) is int
                and page_idx >= 0
                and type(table_index) is int
                and table_index >= 0
                and type(row_index) is int
                and row_index >= 0
                and _is_sequence(headers)
                and _is_sequence(cell_texts)
                and isinstance(mineru_json_source, str)
                and mineru_json_source
                and isinstance(canonical_source, str)
                and canonical_source
                and isinstance(table_source_object_id, str)
                and table_source_object_id
            ):
                metadata_errors.append(MALFORMED_CANDIDATE_METADATA)
                continue
            if not all(isinstance(value, str) for value in headers) or not all(
                isinstance(value, str) for value in cell_texts
            ):
                metadata_errors.append(MALFORMED_CANDIDATE_METADATA)
                continue

            expected_table_source_object_id = (
                f"{mineru_json_source}#page_idx={page_idx}"
                f"&table_index={table_index}"
            )
            expected_canonical_source = (
                f"{expected_table_source_object_id}&row_index={row_index}"
            )
            if candidate.doc_id != doc_id:
                metadata_errors.append(SOURCE_OBJECT_MISMATCH)
            if table_source_object_id != expected_table_source_object_id:
                metadata_errors.extend(
                    (TABLE_SOURCE_IDENTITY_MISMATCH, SOURCE_OBJECT_MISMATCH)
                )
            if canonical_source != expected_canonical_source:
                metadata_errors.extend(
                    (ROW_SOURCE_IDENTITY_MISMATCH, SOURCE_OBJECT_MISMATCH)
                )
            if candidate.source != expected_canonical_source:
                metadata_errors.extend(
                    (ROW_SOURCE_IDENTITY_MISMATCH, SOURCE_OBJECT_MISMATCH)
                )

            proof_keys = (
                "table_data_row_count",
                "row_span_start",
                "row_span_end_exclusive",
                "row_span_complete",
                "row_span_start_explicit",
                "table_row_indices",
                "table_row_sources",
                "table_range_digest",
                "table_range_proof_version",
            )
            proof = {key: metadata.get(key) for key in proof_keys}
            normalized_rows.append(
                {
                    "candidate": candidate,
                    "metadata": metadata,
                    "doc_id": doc_id,
                    "page_idx": page_idx,
                    "table_index": table_index,
                    "row_index": row_index,
                    "headers": tuple(headers),
                    "cell_texts": tuple(cell_texts),
                    "mineru_json_source": mineru_json_source,
                    "raw_canonical_source": canonical_source,
                    "raw_table_source_object_id": table_source_object_id,
                    "canonical_source": expected_canonical_source,
                    "table_source_object_id": expected_table_source_object_id,
                    "proof": proof,
                }
            )

        if normalized_rows:
            if len({row["doc_id"] for row in normalized_rows}) != 1:
                metadata_errors.append(CROSS_DOCUMENT_TABLE)
            if len({row["page_idx"] for row in normalized_rows}) != 1:
                metadata_errors.append(CROSS_PAGE_TABLE)
            if len({row["table_index"] for row in normalized_rows}) != 1:
                metadata_errors.append(CROSS_TABLE)
            raw_sources = [
                row["raw_canonical_source"] for row in normalized_rows
            ]
            if len(raw_sources) != len(set(raw_sources)):
                metadata_errors.append(DUPLICATE_CANONICAL_SOURCE)
            raw_row_indices = [row["row_index"] for row in normalized_rows]
            if len(raw_row_indices) != len(set(raw_row_indices)):
                metadata_errors.append(ROW_INDEX_DUPLICATE)

        if metadata_errors:
            trace.append(
                {
                    "stage": "candidate_contract",
                    "ready": False,
                    "reasons": list(_unique_reasons(metadata_errors)),
                }
            )
            return self._failure(metadata_errors, trace=trace)
        if not normalized_rows:
            return self._failure((EMPTY_CANDIDATES,), trace=trace)

        uniqueness_reasons: list[str] = []
        if len({row["doc_id"] for row in normalized_rows}) != 1:
            uniqueness_reasons.append(CROSS_DOCUMENT_TABLE)
        if len({row["page_idx"] for row in normalized_rows}) != 1:
            uniqueness_reasons.append(CROSS_PAGE_TABLE)
        if len({row["table_index"] for row in normalized_rows}) != 1:
            uniqueness_reasons.append(CROSS_TABLE)
        if len({row["table_source_object_id"] for row in normalized_rows}) != 1:
            uniqueness_reasons.append(SOURCE_OBJECT_MISMATCH)
        canonical_sources = [row["canonical_source"] for row in normalized_rows]
        if len(canonical_sources) != len(set(canonical_sources)):
            uniqueness_reasons.append(DUPLICATE_CANONICAL_SOURCE)
        row_indices = [row["row_index"] for row in normalized_rows]
        if len(row_indices) != len(set(row_indices)):
            uniqueness_reasons.append(ROW_INDEX_DUPLICATE)
        if uniqueness_reasons:
            trace.append(
                {
                    "stage": "single_table_uniqueness",
                    "ready": False,
                    "reasons": list(_unique_reasons(uniqueness_reasons)),
                }
            )
            return self._failure(uniqueness_reasons, trace=trace)

        first_proof = normalized_rows[0]["proof"]
        proof_reasons: list[str] = []
        if any(row["proof"] != first_proof for row in normalized_rows[1:]):
            proof_reasons.append(RANGE_PROOF_INCONSISTENT)

        count = first_proof.get("table_data_row_count")
        start = first_proof.get("row_span_start")
        end = first_proof.get("row_span_end_exclusive")
        complete = first_proof.get("row_span_complete")
        start_explicit = first_proof.get("row_span_start_explicit")
        manifest_indices = first_proof.get("table_row_indices")
        manifest_sources = first_proof.get("table_row_sources")
        digest = first_proof.get("table_range_digest")
        proof_version = first_proof.get("table_range_proof_version")
        if not (
            type(count) is int
            and count > 0
            and type(start) is int
            and start >= 0
            and type(end) is int
            and end > start
            and complete is True
            and type(start_explicit) is bool
            and _is_sequence(manifest_indices)
            and _is_sequence(manifest_sources)
            and isinstance(digest, str)
            and len(digest) == 64
            and proof_version == RANGE_PROOF_VERSION
        ):
            proof_reasons.append(RANGE_PROOF_MISSING)
        else:
            if start != 0 and start_explicit is not True:
                proof_reasons.append(ROW_SPAN_START_INVALID)
            if count != end - start:
                proof_reasons.append(ROW_SPAN_END_INVALID)
                proof_reasons.append(ROW_COUNT_MISMATCH)
            if not all(type(value) is int for value in manifest_indices):
                proof_reasons.append(RANGE_PROOF_INCONSISTENT)
            if not all(
                isinstance(value, str) and value for value in manifest_sources
            ):
                proof_reasons.append(RANGE_PROOF_INCONSISTENT)
            expected_indices = tuple(range(start, end))
            if tuple(manifest_indices) != expected_indices:
                proof_reasons.append(ROW_INDEX_GAP)
            sorted_rows = sorted(normalized_rows, key=lambda row: row["row_index"])
            actual_indices = tuple(row["row_index"] for row in sorted_rows)
            actual_sources = tuple(row["canonical_source"] for row in sorted_rows)
            if actual_indices != expected_indices:
                proof_reasons.append(ROW_INDEX_GAP)
                proof_reasons.append(CANDIDATE_SET_INCOMPLETE)
            if len(sorted_rows) != count:
                proof_reasons.append(ROW_COUNT_MISMATCH)
                proof_reasons.append(CANDIDATE_SET_INCOMPLETE)
            if actual_sources != tuple(manifest_sources):
                proof_reasons.append(CANDIDATE_SET_INCOMPLETE)
            recomputed_digest = _range_digest(
                doc_id=sorted_rows[0]["doc_id"],
                page_idx=sorted_rows[0]["page_idx"],
                table_index=sorted_rows[0]["table_index"],
                headers=sorted_rows[0]["headers"],
                rows=sorted_rows,
            )
            if recomputed_digest != digest:
                proof_reasons.append(RANGE_PROOF_DIGEST_MISMATCH)

        if proof_reasons:
            trace.append(
                {
                    "stage": "complete_range_proof",
                    "ready": False,
                    "reasons": list(_unique_reasons(proof_reasons)),
                }
            )
            return self._failure(proof_reasons, trace=trace)

        rows = sorted(normalized_rows, key=lambda row: row["row_index"])
        header_sets = {row["headers"] for row in rows}
        if len(header_sets) != 1:
            return self._failure(
                (HEADER_SCHEMA_INCONSISTENT,), trace=trace
            )
        headers = rows[0]["headers"]
        if not headers or any(len(row["cell_texts"]) != len(headers) for row in rows):
            return self._failure((COLUMN_COUNT_MISMATCH,), trace=trace)

        parsed_columns: dict[int, tuple[_ParsedNumber, ...]] = {}
        valid_numeric_columns: list[int] = []
        numeric_signal_columns: list[int] = []
        for column_index, header in enumerate(headers):
            unit = _header_unit(header)
            parsed = tuple(
                _parse_number(row["cell_texts"][column_index], unit) for row in rows
            )
            parsed_columns[column_index] = parsed
            has_numeric_signal = bool(unit) or any(
                item.status in {
                    "OK",
                    NON_FINITE_NUMBER,
                    MIXED_UNITS,
                    NUMERIC_VALUE_EMPTY,
                }
                for item in parsed
            )
            if has_numeric_signal:
                numeric_signal_columns.append(column_index)
            if parsed and all(item.status == "OK" for item in parsed):
                valid_numeric_columns.append(column_index)

        if not valid_numeric_columns:
            column_reasons: list[str] = []
            for column_index in numeric_signal_columns:
                column_reasons.extend(
                    item.status
                    for item in parsed_columns[column_index]
                    if item.status != "OK"
                )
            if not column_reasons:
                column_reasons.append(NUMERIC_COLUMN_MISSING)
            return self._failure(column_reasons, trace=trace)

        matched_numeric_columns = [
            column_index
            for column_index in valid_numeric_columns
            if _metric_matches_question(
                _header_metric(headers[column_index]), bundle.question.text
            )
        ]
        if len(valid_numeric_columns) == 1:
            numeric_column = valid_numeric_columns[0]
            if numeric_column not in matched_numeric_columns:
                return self._failure((METRIC_HEADER_MISMATCH,), trace=trace)
        elif len(matched_numeric_columns) == 1:
            numeric_column = matched_numeric_columns[0]
        else:
            return self._failure((NUMERIC_COLUMN_AMBIGUOUS,), trace=trace)

        non_numeric_columns = [
            index for index in range(len(headers)) if index not in valid_numeric_columns
        ]
        if not non_numeric_columns:
            return self._failure((LABEL_COLUMN_MISSING,), trace=trace)
        if len(non_numeric_columns) != 1:
            return self._failure((LABEL_COLUMN_AMBIGUOUS,), trace=trace)
        label_column = non_numeric_columns[0]
        labels = tuple(row["cell_texts"][label_column].strip() for row in rows)
        if any(not label for label in labels):
            return self._failure((LABEL_VALUE_EMPTY,), trace=trace)

        parsed_values = parsed_columns[numeric_column]
        units = {item.unit for item in parsed_values}
        dimensions = {item.dimension for item in parsed_values}
        if "%" in units or "percentage" in dimensions:
            return self._failure((PERCENT_NOT_SUPPORTED,), trace=trace)
        if len(units) != 1:
            return self._failure((MIXED_UNITS,), trace=trace)
        if len(dimensions) != 1:
            return self._failure((MIXED_DIMENSIONS,), trace=trace)

        summary_flags = tuple(
            any(token in _compact(label) for token in _SUMMARY_LABELS)
            for label in labels
        )
        if any(summary_flags) and not all(summary_flags):
            return self._failure((SUMMARY_DETAIL_CONFLICT,), trace=trace)

        metric = _header_metric(headers[numeric_column])
        if not metric or metric in _GENERIC_HEADER_TOKENS:
            if not _metric_matches_question(metric, bundle.question.text):
                return self._failure((METRIC_HEADER_MISMATCH,), trace=trace)
        entity = _header_metric(headers[label_column]) or headers[label_column].strip()
        source_object_id = rows[0]["table_source_object_id"]
        source_refs: list[FormulaSourceRef] = []
        items: list[SourceBoundNumericSeriesItem] = []
        for position, (row, label, parsed) in enumerate(
            zip(rows, labels, parsed_values)
        ):
            if parsed.value is None:
                return self._failure((NUMERIC_VALUE_INVALID,), trace=trace)
            coordinate = (
                f"{row['canonical_source']}&column_index={numeric_column}"
            )
            source_ref = FormulaSourceRef(
                doc_id=row["doc_id"],
                page_number=row["page_idx"] + 1,
                source=source_object_id,
                block_id=(
                    f"table-{row['table_index']}-row-{row['row_index']}"
                    f"-column-{numeric_column}"
                ),
                excerpt=(
                    f"{headers[label_column]}={label} | "
                    f"{headers[numeric_column]}={row['cell_texts'][numeric_column]}"
                ),
            )
            source_refs.append(source_ref)
            items.append(
                SourceBoundNumericSeriesItem(
                    position=position,
                    value=parsed.value,
                    unit=parsed.unit,
                    dimension=parsed.dimension,
                    source_ref=source_ref,
                    source_coordinate=coordinate,
                    source_object_id=source_object_id,
                    header_label=label,
                )
            )

        series_seed = (
            f"{source_object_id}|{metric}|{start}|{end}|{numeric_column}"
        )
        series_id = "sum-series-" + hashlib.sha256(
            series_seed.encode("utf-8")
        ).hexdigest()[:16]
        series = SourceBoundNumericSeries(
            series_id=series_id,
            items=tuple(items),
            metric=metric,
            entity=entity,
            source_object_id=source_object_id,
            binding_status=SourceSeriesBindingStatus.EXACT,
            aggregation_range_explicit=True,
            total_components_ambiguity=False,
        )
        request = SourceBoundNumericSeriesAggregationRequest(
            series=series,
            selectors=(AggregationSelector.SUM,),
            output=SeriesAggregationOutputSpec(
                operation=AggregationOutputOperation.SELECTOR,
                operands=(AggregationSelector.SUM,),
                output_kind="SCALAR",
                output_semantics="number",
            ),
            question_aggregation_match=ExecutionGateFact(
                True, reasons=("explicit_sum_intent",)
            ),
        )
        trace.extend(
            [
                {
                    "stage": "complete_range_proof",
                    "ready": True,
                    "row_span_start": start,
                    "row_span_end_exclusive": end,
                    "row_count": count,
                    "table_range_digest": digest,
                },
                {
                    "stage": "column_binding",
                    "ready": True,
                    "label_column_index": label_column,
                    "numeric_column_index": numeric_column,
                    "label_header": headers[label_column],
                    "numeric_header": headers[numeric_column],
                    "unit": next(iter(units)),
                    "dimension": next(iter(dimensions)),
                },
                {
                    "stage": "request_construction",
                    "ready": True,
                    "request_contract": "SourceBoundNumericSeriesAggregationRequest",
                    "selector": AggregationSelector.SUM.value,
                    "item_count": len(items),
                },
            ]
        )
        return SourceBoundSumSeriesBindingResult(
            ready=True,
            request=request,
            reasons=(),
            trace=tuple(trace),
            source_refs=tuple(source_refs),
            metadata={
                "schema_version": SCHEMA_VERSION,
                "request_contract": "SourceBoundNumericSeriesAggregationRequest",
                "doc_id": rows[0]["doc_id"],
                "page_idx": rows[0]["page_idx"],
                "table_index": rows[0]["table_index"],
                "source_object_id": source_object_id,
                "row_span_start": start,
                "row_span_end_exclusive": end,
                "row_count": count,
                "label_column_index": label_column,
                "numeric_column_index": numeric_column,
                "metric": metric,
                "entity": entity,
                "unit": next(iter(units)),
                "dimension": next(iter(dimensions)),
                "provider_calls": 0,
                "legacy_calls": 0,
                "network_calls": 0,
                "total_tokens": 0,
            },
        )


__all__ = [
    "CANDIDATE_SET_INCOMPLETE",
    "COLUMN_COUNT_MISMATCH",
    "CROSS_DOCUMENT_TABLE",
    "CROSS_PAGE_TABLE",
    "CROSS_TABLE",
    "DUPLICATE_CANONICAL_SOURCE",
    "EMPTY_CANDIDATES",
    "HEADER_SCHEMA_INCONSISTENT",
    "LABEL_COLUMN_AMBIGUOUS",
    "LABEL_COLUMN_MISSING",
    "LABEL_VALUE_EMPTY",
    "MALFORMED_CANDIDATE_METADATA",
    "METRIC_HEADER_MISMATCH",
    "MIXED_DIMENSIONS",
    "MIXED_UNITS",
    "NON_FINITE_NUMBER",
    "NON_STRUCTURED_TABLE_CANDIDATE",
    "NUMERIC_COLUMN_AMBIGUOUS",
    "NUMERIC_COLUMN_MISSING",
    "NUMERIC_VALUE_EMPTY",
    "NUMERIC_VALUE_INVALID",
    "PERCENT_NOT_SUPPORTED",
    "QUESTION_AGGREGATION_AMBIGUOUS",
    "QUESTION_SUM_INTENT_MISSING",
    "RANGE_PROOF_DIGEST_MISMATCH",
    "RANGE_PROOF_INCONSISTENT",
    "RANGE_PROOF_MISSING",
    "ROW_COUNT_MISMATCH",
    "ROW_INDEX_DUPLICATE",
    "ROW_INDEX_GAP",
    "ROW_SPAN_END_INVALID",
    "ROW_SPAN_START_INVALID",
    "ROW_SOURCE_IDENTITY_MISMATCH",
    "SOURCE_OBJECT_MISMATCH",
    "TABLE_SOURCE_IDENTITY_MISMATCH",
    "SourceBoundSumSeriesBinder",
    "SourceBoundSumSeriesBindingResult",
    "SUMMARY_DETAIL_CONFLICT",
]
