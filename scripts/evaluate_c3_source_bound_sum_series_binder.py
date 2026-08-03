#!/usr/bin/env python3
"""Evaluate the generic C3-P source-bound SUM-series Binder offline."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from calculation import (
    SourceBoundNumericSeriesAggregationRequest,
    SourceBoundNumericSeriesAggregator,
)
from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    QuestionLabel,
)
from evidence.c3_numeric_series_binding import (
    CANDIDATE_SET_INCOMPLETE,
    CROSS_DOCUMENT_TABLE,
    CROSS_PAGE_TABLE,
    CROSS_TABLE,
    DUPLICATE_CANONICAL_SOURCE,
    EMPTY_CANDIDATES,
    LABEL_COLUMN_AMBIGUOUS,
    LABEL_VALUE_EMPTY,
    MALFORMED_CANDIDATE_METADATA,
    METRIC_HEADER_MISMATCH,
    MIXED_UNITS,
    NON_FINITE_NUMBER,
    NON_STRUCTURED_TABLE_CANDIDATE,
    NUMERIC_COLUMN_AMBIGUOUS,
    NUMERIC_COLUMN_MISSING,
    NUMERIC_VALUE_EMPTY,
    NUMERIC_VALUE_INVALID,
    PERCENT_NOT_SUPPORTED,
    QUESTION_AGGREGATION_AMBIGUOUS,
    QUESTION_SUM_INTENT_MISSING,
    RANGE_PROOF_DIGEST_MISMATCH,
    RANGE_PROOF_INCONSISTENT,
    RANGE_PROOF_MISSING,
    ROW_COUNT_MISMATCH,
    ROW_INDEX_DUPLICATE,
    ROW_INDEX_GAP,
    ROW_SPAN_START_INVALID,
    ROW_SOURCE_IDENTITY_MISMATCH,
    SOURCE_OBJECT_MISMATCH,
    SUMMARY_DETAIL_CONFLICT,
    TABLE_SOURCE_IDENTITY_MISMATCH,
    SourceBoundSumSeriesBinder,
)


SCHEMA_VERSION = "c3-source-bound-sum-series-binder-evaluation/v1"
PROOF_VERSION = "structured-table-range/v1"
REPORT_PATH = Path(
    "evaluation_artifacts/c3_source_bound_sum_series_binder_v1/report.json"
)


def _proof_digest(
    *,
    doc_id: str,
    page_idx: int,
    table_index: int,
    headers: Sequence[str],
    row_indices: Sequence[int],
    row_sources: Sequence[str],
    cells: Sequence[Sequence[str]],
) -> str:
    payload = {
        "proof_version": PROOF_VERSION,
        "doc_id": doc_id,
        "page_idx": page_idx,
        "table_index": table_index,
        "headers": list(headers),
        "rows": [
            {
                "row_index": row_index,
                "canonical_source": source,
                "cell_texts": list(row_cells),
            }
            for row_index, source, row_cells in zip(
                row_indices, row_sources, cells
            )
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


def _bundle(
    *,
    question_text: str = "三个部门利润合计是多少？",
    headers: tuple[str, ...] = ("部门", "利润（万元）"),
    cells: tuple[tuple[str, ...], ...] = (
        ("一部", "10"),
        ("二部", "20"),
        ("三部", "30"),
    ),
    doc_id: str = "local-binder-doc",
    page_idx: int = 0,
    table_index: int = 0,
) -> EvidenceBundle:
    row_indices = tuple(range(len(cells)))
    table_source = (
        f"data/local/{doc_id}_content_list_v2.json"
        f"#page_idx={page_idx}&table_index={table_index}"
    )
    row_sources = tuple(
        f"{table_source}&row_index={row_index}" for row_index in row_indices
    )
    digest = _proof_digest(
        doc_id=doc_id,
        page_idx=page_idx,
        table_index=table_index,
        headers=headers,
        row_indices=row_indices,
        row_sources=row_sources,
        cells=cells,
    )
    candidates: list[EvidenceCandidate] = []
    for row_index, source, row_cells in zip(row_indices, row_sources, cells):
        metadata = {
            "domain": "financial_report",
            "doc_id": doc_id,
            "page_idx": page_idx,
            "table_index": table_index,
            "row_index": row_index,
            "headers": list(headers),
            "cell_texts": list(row_cells),
            "canonical_source": source,
            "mineru_json_source": f"data/local/{doc_id}_content_list_v2.json",
            "source_kind": "mineru_structured_table",
            "structured_table_evidence": True,
            "table_source_object_id": table_source,
            "table_data_row_count": len(cells),
            "row_span_start": 0,
            "row_span_end_exclusive": len(cells),
            "row_span_complete": True,
            "row_span_start_explicit": True,
            "table_row_indices": list(row_indices),
            "table_row_sources": list(row_sources),
            "table_range_digest": digest,
            "table_range_proof_version": PROOF_VERSION,
        }
        candidates.append(
            EvidenceCandidate(
                domain="financial_report",
                doc_id=doc_id,
                source=source,
                text=" | ".join(
                    f"{header}={value}"
                    for header, value in zip(headers, row_cells)
                ),
                retriever="mineru_structured_table",
                metadata=metadata,
            )
        )
    return EvidenceBundle(
        question=Question(
            qid="local-source-bound-sum-series",
            domain="financial_report",
            text=question_text,
            options={},
            answer_format="number",
            doc_ids=(doc_id,),
        ),
        classification=ClassificationResult(
            labels=(QuestionLabel.CALCULATION,)
        ),
        candidates=tuple(candidates),
        prompt_context="",
        estimated_tokens=0,
    )


def _question(bundle: EvidenceBundle, text: str) -> EvidenceBundle:
    return replace(bundle, question=replace(bundle.question, text=text))


def _candidate(
    bundle: EvidenceBundle,
    index: int,
    *,
    metadata_updates: Mapping[str, object] | None = None,
    metadata_remove: Sequence[str] = (),
    candidate_updates: Mapping[str, object] | None = None,
) -> EvidenceBundle:
    candidates = list(bundle.candidates)
    original = candidates[index]
    metadata = deepcopy(dict(original.metadata))
    metadata.update(dict(metadata_updates or {}))
    for key in metadata_remove:
        metadata.pop(key, None)
    candidates[index] = replace(
        original,
        metadata=metadata,
        **dict(candidate_updates or {}),
    )
    return replace(bundle, candidates=tuple(candidates))


def _all_proof(bundle: EvidenceBundle, **updates: object) -> EvidenceBundle:
    result = bundle
    for index in range(len(result.candidates)):
        result = _candidate(result, index, metadata_updates=updates)
    return result


def _coordinated_row_source_attack(bundle: EvidenceBundle) -> EvidenceBundle:
    candidates = list(bundle.candidates)
    attacked_sources = [candidate.source for candidate in candidates]
    attacked_sources[1] = (
        str(candidates[1].metadata["table_source_object_id"])
        + "&row_index=99"
    )
    headers = tuple(candidates[0].metadata["headers"])
    cells = tuple(
        tuple(candidate.metadata["cell_texts"]) for candidate in candidates
    )
    row_indices = tuple(
        int(candidate.metadata["row_index"]) for candidate in candidates
    )
    digest = _proof_digest(
        doc_id=str(candidates[0].metadata["doc_id"]),
        page_idx=int(candidates[0].metadata["page_idx"]),
        table_index=int(candidates[0].metadata["table_index"]),
        headers=headers,
        row_indices=row_indices,
        row_sources=tuple(attacked_sources),
        cells=cells,
    )
    result = bundle
    for index in range(len(candidates)):
        updates: dict[str, object] = {
            "table_row_sources": list(attacked_sources),
            "table_range_digest": digest,
        }
        candidate_updates: dict[str, object] = {}
        if index == 1:
            updates["canonical_source"] = attacked_sources[index]
            candidate_updates["source"] = attacked_sources[index]
        result = _candidate(
            result,
            index,
            metadata_updates=updates,
            candidate_updates=candidate_updates,
        )
    return result


def _coordinated_table_source_attack(bundle: EvidenceBundle) -> EvidenceBundle:
    candidates = list(bundle.candidates)
    wrong_table_source = (
        "data/local/other_content_list_v2.json#page_idx=9&table_index=8"
    )
    attacked_sources = tuple(
        f"{wrong_table_source}&row_index={candidate.metadata['row_index']}"
        for candidate in candidates
    )
    headers = tuple(candidates[0].metadata["headers"])
    cells = tuple(
        tuple(candidate.metadata["cell_texts"]) for candidate in candidates
    )
    row_indices = tuple(
        int(candidate.metadata["row_index"]) for candidate in candidates
    )
    digest = _proof_digest(
        doc_id=str(candidates[0].metadata["doc_id"]),
        page_idx=int(candidates[0].metadata["page_idx"]),
        table_index=int(candidates[0].metadata["table_index"]),
        headers=headers,
        row_indices=row_indices,
        row_sources=attacked_sources,
        cells=cells,
    )
    result = bundle
    for index, source in enumerate(attacked_sources):
        result = _candidate(
            result,
            index,
            metadata_updates={
                "table_source_object_id": wrong_table_source,
                "canonical_source": source,
                "table_row_sources": list(attacked_sources),
                "table_range_digest": digest,
            },
            candidate_updates={"source": source},
        )
    return result


def _candidate_source_only_mismatch(bundle: EvidenceBundle) -> EvidenceBundle:
    wrong_source = (
        str(bundle.candidates[1].metadata["table_source_object_id"])
        + "&row_index=99"
    )
    return _candidate(
        bundle,
        1,
        candidate_updates={"source": wrong_source},
    )


def _negative_cases() -> tuple[tuple[str, EvidenceBundle, str], ...]:
    base = _bundle()
    count_small_sources = [candidate.source for candidate in base.candidates[:2]]
    count_large_sources = [candidate.source for candidate in base.candidates] + [
        base.candidates[0].metadata["table_source_object_id"] + "&row_index=3"
    ]

    cross_doc = _candidate(
        base,
        1,
        metadata_updates={"doc_id": "other-doc"},
        candidate_updates={"doc_id": "other-doc"},
    )
    duplicate_source = _candidate(
        base,
        1,
        metadata_updates={"canonical_source": base.candidates[0].source},
        candidate_updates={"source": base.candidates[0].source},
    )
    different_object = "data/local/other.json#page_idx=0&table_index=0"
    different_source = different_object + "&row_index=1"
    source_mismatch = _candidate(
        base,
        1,
        metadata_updates={
            "table_source_object_id": different_object,
            "canonical_source": different_source,
        },
        candidate_updates={"source": different_source},
    )
    missing_proof = base
    for index in range(len(base.candidates)):
        missing_proof = _candidate(
            missing_proof,
            index,
            metadata_remove=("table_range_digest",),
        )
    truncated = replace(base, candidates=base.candidates[:2])
    coordinated_truncation = _all_proof(
        truncated,
        table_data_row_count=2,
        row_span_end_exclusive=2,
        table_row_indices=[0, 1],
        table_row_sources=[candidate.source for candidate in truncated.candidates],
    )
    malformed = _candidate(base, 0, metadata_updates={"headers": "bad"})
    non_structured = _candidate(
        base, 0, metadata_updates={"structured_table_evidence": False}
    )

    return (
        (
            "no_sum_intent",
            _question(base, "三个部门利润分别是多少？"),
            QUESTION_SUM_INTENT_MISSING,
        ),
        (
            "sum_average_conflict",
            _question(base, "三个部门利润平均值和合计是多少？"),
            QUESTION_AGGREGATION_AMBIGUOUS,
        ),
        ("empty_candidates", replace(base, candidates=()), EMPTY_CANDIDATES),
        ("non_structured", non_structured, NON_STRUCTURED_TABLE_CANDIDATE),
        ("cross_document", cross_doc, CROSS_DOCUMENT_TABLE),
        (
            "cross_page",
            _candidate(base, 1, metadata_updates={"page_idx": 1}),
            CROSS_PAGE_TABLE,
        ),
        (
            "cross_table",
            _candidate(base, 1, metadata_updates={"table_index": 1}),
            CROSS_TABLE,
        ),
        (
            "row_gap",
            replace(base, candidates=(base.candidates[0], base.candidates[2])),
            ROW_INDEX_GAP,
        ),
        (
            "row_duplicate",
            replace(
                base,
                candidates=(
                    base.candidates[0],
                    base.candidates[0],
                    base.candidates[2],
                ),
            ),
            ROW_INDEX_DUPLICATE,
        ),
        (
            "wrong_start",
            _all_proof(
                base,
                row_span_start=1,
                row_span_end_exclusive=4,
                row_span_start_explicit=False,
                table_row_indices=[1, 2, 3],
            ),
            ROW_SPAN_START_INVALID,
        ),
        (
            "count_smaller",
            _all_proof(
                base,
                table_data_row_count=2,
                row_span_end_exclusive=2,
                table_row_indices=[0, 1],
                table_row_sources=count_small_sources,
            ),
            ROW_COUNT_MISMATCH,
        ),
        (
            "count_larger",
            _all_proof(
                base,
                table_data_row_count=4,
                row_span_end_exclusive=4,
                table_row_indices=[0, 1, 2, 3],
                table_row_sources=count_large_sources,
            ),
            ROW_COUNT_MISMATCH,
        ),
        ("missing_proof", missing_proof, RANGE_PROOF_MISSING),
        (
            "two_numeric_columns",
            _bundle(
                question_text="各部门金额合计是多少？",
                headers=("部门", "收入（万元）", "利润（万元）"),
                cells=(
                    ("一部", "100", "10"),
                    ("二部", "200", "20"),
                    ("三部", "300", "30"),
                ),
            ),
            NUMERIC_COLUMN_AMBIGUOUS,
        ),
        (
            "no_numeric_column",
            _bundle(
                question_text="各部门金额合计是多少？",
                headers=("部门", "说明"),
                cells=(
                    ("一部", "良好"),
                    ("二部", "一般"),
                    ("三部", "较好"),
                ),
            ),
            NUMERIC_COLUMN_MISSING,
        ),
        (
            "empty_label",
            _bundle(cells=(("一部", "10"), ("", "20"), ("三部", "30"))),
            LABEL_VALUE_EMPTY,
        ),
        (
            "empty_numeric",
            _bundle(cells=(("一部", "10"), ("二部", ""), ("三部", "30"))),
            NUMERIC_VALUE_EMPTY,
        ),
        (
            "non_finite",
            _bundle(cells=(("一部", "10"), ("二部", "NaN"), ("三部", "30"))),
            NON_FINITE_NUMBER,
        ),
        (
            "mixed_units",
            _bundle(
                headers=("部门", "利润"),
                cells=(
                    ("一部", "10万元"),
                    ("二部", "20元"),
                    ("三部", "30万元"),
                ),
            ),
            MIXED_UNITS,
        ),
        (
            "amount_percent",
            _bundle(
                headers=("部门", "利润"),
                cells=(
                    ("一部", "10万元"),
                    ("二部", "20%"),
                    ("三部", "30万元"),
                ),
            ),
            PERCENT_NOT_SUPPORTED,
        ),
        ("duplicate_source", duplicate_source, DUPLICATE_CANONICAL_SOURCE),
        ("source_object_mismatch", source_mismatch, SOURCE_OBJECT_MISMATCH),
        (
            "coordinated_row_source_attack",
            _coordinated_row_source_attack(base),
            ROW_SOURCE_IDENTITY_MISMATCH,
        ),
        (
            "coordinated_table_source_attack",
            _coordinated_table_source_attack(base),
            TABLE_SOURCE_IDENTITY_MISMATCH,
        ),
        (
            "candidate_source_only_mismatch",
            _candidate_source_only_mismatch(base),
            ROW_SOURCE_IDENTITY_MISMATCH,
        ),
        (
            "summary_detail_conflict",
            _bundle(cells=(("一部", "10"), ("二部", "20"), ("合计", "30"))),
            SUMMARY_DETAIL_CONFLICT,
        ),
        ("malformed_metadata", malformed, MALFORMED_CANDIDATE_METADATA),
        (
            "metric_mismatch",
            _question(base, "三个部门收入合计是多少？"),
            METRIC_HEADER_MISMATCH,
        ),
        (
            "coordinated_truncation",
            coordinated_truncation,
            RANGE_PROOF_DIGEST_MISMATCH,
        ),
        (
            "inconsistent_proof",
            _candidate(
                base,
                1,
                metadata_updates={"row_span_end_exclusive": 99},
            ),
            RANGE_PROOF_INCONSISTENT,
        ),
        (
            "label_column_ambiguous",
            _bundle(
                headers=("部门", "区域", "利润（万元）"),
                cells=(
                    ("一部", "东区", "10"),
                    ("二部", "西区", "20"),
                    ("三部", "南区", "30"),
                ),
            ),
            LABEL_COLUMN_AMBIGUOUS,
        ),
        (
            "invalid_numeric",
            _bundle(cells=(("一部", "10"), ("二部", "二十"), ("三部", "30"))),
            NUMERIC_VALUE_INVALID,
        ),
        (
            "candidate_set_incomplete",
            replace(base, candidates=base.candidates[:1]),
            CANDIDATE_SET_INCOMPLETE,
        ),
    )


@contextmanager
def _deny_network() -> Iterable[dict[str, int]]:
    counter = {"count": 0}
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect

    def blocked_create_connection(*args, **kwargs):
        counter["count"] += 1
        raise RuntimeError("network_disabled_for_c3_binder_evaluation")

    def blocked_connect(self, *args, **kwargs):
        counter["count"] += 1
        raise RuntimeError("network_disabled_for_c3_binder_evaluation")

    socket.create_connection = blocked_create_connection
    socket.socket.connect = blocked_connect
    try:
        yield counter
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect


def build_report() -> dict[str, Any]:
    binder = SourceBoundSumSeriesBinder()
    aggregator = SourceBoundNumericSeriesAggregator()
    positive_specs = (
        (
            "amount_sum",
            _bundle(),
            Decimal("60"),
        ),
        (
            "thousands_separator",
            _bundle(
                question_text="项目成本总和是多少？",
                headers=("项目", "成本（元）"),
                cells=(
                    ("A", "1,000"),
                    ("B", "2,500"),
                    ("C", "500"),
                ),
            ),
            Decimal("4000"),
        ),
        (
            "parenthesized_negative",
            _bundle(
                question_text="各区域净变动共计多少？",
                headers=("区域", "净变动（万元）"),
                cells=(("东区", "20"), ("西区", "(5)"), ("南区", "10")),
            ),
            Decimal("25"),
        ),
    )

    positive_records: list[dict[str, Any]] = []
    negative_records: list[dict[str, Any]] = []
    binding_success_count = 0
    c3_execution_correct_count = 0
    source_lineage_complete = True

    with _deny_network() as network_counter:
        for case_name, bundle, expected in positive_specs:
            binding = binder.bind(bundle)
            execution = (
                aggregator.execute(binding.request)
                if binding.ready and binding.request is not None
                else None
            )
            binding_success = bool(
                binding.ready
                and isinstance(
                    binding.request,
                    SourceBoundNumericSeriesAggregationRequest,
                )
            )
            execution_correct = bool(
                execution is not None
                and execution.ok
                and execution.value == expected
            )
            lineage_complete = bool(
                binding.request is not None
                and len(binding.request.series.items) == len(bundle.candidates)
                and len(binding.source_refs) == len(bundle.candidates)
                and all(
                    item.source_ref is not None
                    and item.source_coordinate
                    and item.source_object_id
                    and item.header_label
                    for item in binding.request.series.items
                )
                and len(
                    {
                        item.source_coordinate
                        for item in binding.request.series.items
                    }
                )
                == len(bundle.candidates)
            )
            binding_success_count += int(binding_success)
            c3_execution_correct_count += int(execution_correct)
            source_lineage_complete = source_lineage_complete and lineage_complete
            positive_records.append(
                {
                    "case": case_name,
                    "question": bundle.question.text,
                    "ready": binding.ready,
                    "reasons": list(binding.reasons),
                    "request_contract": (
                        type(binding.request).__name__
                        if binding.request is not None
                        else ""
                    ),
                    "request": (
                        binding.to_dict()["request"]
                        if binding.request is not None
                        else None
                    ),
                    "expected": str(expected),
                    "execution_ok": execution.ok if execution is not None else False,
                    "execution_value": (
                        str(execution.value) if execution is not None else ""
                    ),
                    "execution_trace": (
                        [dict(item) for item in execution.trace]
                        if execution is not None
                        else []
                    ),
                    "source_lineage_complete": lineage_complete,
                    "source_refs": [
                        item.to_dict() for item in binding.source_refs
                    ],
                    "binding_trace": [dict(item) for item in binding.trace],
                }
            )

        for case_name, bundle, expected_reason in _negative_cases():
            first = binder.bind(bundle)
            second = binder.bind(bundle)
            stable = first.to_dict() == second.to_dict()
            rejected = bool(
                not first.ready
                and first.request is None
                and expected_reason in first.reasons
            )
            negative_records.append(
                {
                    "case": case_name,
                    "expected_reason": expected_reason,
                    "ready": first.ready,
                    "request_is_none": first.request is None,
                    "reasons": list(first.reasons),
                    "stable": stable,
                    "rejected_as_expected": rejected,
                    "trace": [dict(item) for item in first.trace],
                }
            )

    binding_failure_count = sum(
        int(record["rejected_as_expected"]) for record in negative_records
    )
    stable_failure_count = sum(int(record["stable"]) for record in negative_records)
    provider_calls = 0
    legacy_calls = 0
    total_tokens = 0
    network_calls = int(network_counter["count"])
    measurement_valid = bool(
        binding_success_count == len(positive_specs) == 3
        and c3_execution_correct_count == 3
        and source_lineage_complete
        and len(negative_records) >= 25
        and binding_failure_count == len(negative_records)
        and stable_failure_count == len(negative_records)
        and provider_calls == 0
        and legacy_calls == 0
        and network_calls == 0
        and total_tokens == 0
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "positive_cases": positive_records,
        "negative_cases": negative_records,
        "binding_success_count": binding_success_count,
        "binding_failure_count": binding_failure_count,
        "stable_failure_count": stable_failure_count,
        "c3_execution_correct_count": c3_execution_correct_count,
        "request_contract": "SourceBoundNumericSeriesAggregationRequest",
        "source_lineage_complete": source_lineage_complete,
        "provider_calls": provider_calls,
        "legacy_calls": legacy_calls,
        "network_calls": network_calls,
        "total_tokens": total_tokens,
        "measurement_valid": measurement_valid,
        "stage_boundary": {
            "request_construction_only": True,
            "normal_pipeline_integrated": False,
            "shadow_integrated": False,
            "production_routing_integrated": False,
            "supported_selector": "SUM",
        },
    }
    return json.loads(json.dumps(report, ensure_ascii=False))


def validate_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected binder evaluation schema")
    positives = report.get("positive_cases")
    negatives = report.get("negative_cases")
    if not isinstance(positives, list) or len(positives) != 3:
        raise ValueError("positive case count mismatch")
    if not isinstance(negatives, list) or len(negatives) < 25:
        raise ValueError("negative case count below frozen minimum")
    if report.get("binding_success_count") != 3:
        raise ValueError("binding success count mismatch")
    if report.get("c3_execution_correct_count") != 3:
        raise ValueError("C3-M execution correctness mismatch")
    if report.get("binding_failure_count") != len(negatives):
        raise ValueError("not all negative cases failed closed")
    if report.get("stable_failure_count") != len(negatives):
        raise ValueError("negative reasons are not stable")
    if report.get("source_lineage_complete") is not True:
        raise ValueError("source lineage incomplete")
    if report.get("request_contract") != (
        "SourceBoundNumericSeriesAggregationRequest"
    ):
        raise ValueError("unexpected request contract")
    if any(
        int(report.get(key, -1)) != 0
        for key in (
            "provider_calls",
            "legacy_calls",
            "network_calls",
            "total_tokens",
        )
    ):
        raise ValueError("offline zero-call invariant violated")
    boundary = report.get("stage_boundary")
    if not isinstance(boundary, Mapping) or boundary != {
        "request_construction_only": True,
        "normal_pipeline_integrated": False,
        "shadow_integrated": False,
        "production_routing_integrated": False,
        "supported_selector": "SUM",
    }:
        raise ValueError("stage boundary mismatch")
    if report.get("measurement_valid") is not True:
        raise ValueError("binder measurement invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(REPORT_PATH.as_posix()))
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_report()
    validate_report(report)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "C3_SOURCE_BOUND_SUM_SERIES_BINDER",
            f"measurement_valid={str(report['measurement_valid']).lower()}",
            f"positive={report['binding_success_count']}",
            f"negative={report['binding_failure_count']}",
            f"c3_correct={report['c3_execution_correct_count']}",
            f"lineage_complete={str(report['source_lineage_complete']).lower()}",
            f"provider_calls={report['provider_calls']}",
            f"legacy_calls={report['legacy_calls']}",
            f"network_calls={report['network_calls']}",
            f"tokens={report['total_tokens']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
