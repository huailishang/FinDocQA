from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

import pytest

from calculation import (
    AggregationSelector,
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
    COLUMN_COUNT_MISMATCH,
    CROSS_DOCUMENT_TABLE,
    CROSS_PAGE_TABLE,
    CROSS_TABLE,
    DUPLICATE_CANONICAL_SOURCE,
    EMPTY_CANDIDATES,
    HEADER_SCHEMA_INCONSISTENT,
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
from evidence.structured_tables import load_structured_table_rows
from scripts.evaluate_c3_source_bound_sum_series_binder import (
    SCHEMA_VERSION as EVALUATION_SCHEMA_VERSION,
    build_report,
    validate_report,
)


ROOT = Path(__file__).resolve().parents[1]
PROOF_VERSION = "structured-table-range/v1"


def _proof_digest(
    *,
    doc_id: str,
    page_idx: int,
    table_index: int,
    headers: tuple[str, ...],
    row_indices: tuple[int, ...],
    row_sources: tuple[str, ...],
    cells: tuple[tuple[str, ...], ...],
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
            for row_index, source, row_cells in zip(row_indices, row_sources, cells)
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
    doc_id: str = "local-doc",
    page_idx: int = 0,
    table_index: int = 0,
    row_span_start: int = 0,
) -> EvidenceBundle:
    row_indices = tuple(row_span_start + index for index in range(len(cells)))
    source_object = (
        f"data/local/{doc_id}_content_list_v2.json"
        f"#page_idx={page_idx}&table_index={table_index}"
    )
    row_sources = tuple(
        f"{source_object}&row_index={row_index}" for row_index in row_indices
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
            "table_source_object_id": source_object,
            "table_data_row_count": len(cells),
            "row_span_start": row_span_start,
            "row_span_end_exclusive": row_span_start + len(cells),
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
                    f"{header}={value}" for header, value in zip(headers, row_cells)
                ),
                retriever="mineru_structured_table",
                metadata=metadata,
            )
        )
    question = Question(
        qid="local-sum-binding",
        domain="financial_report",
        text=question_text,
        options={},
        answer_format="number",
        doc_ids=(doc_id,),
    )
    return EvidenceBundle(
        question=question,
        classification=ClassificationResult(labels=(QuestionLabel.CALCULATION,)),
        candidates=tuple(candidates),
        prompt_context="",
        estimated_tokens=0,
    )


def _replace_question(bundle: EvidenceBundle, text: str) -> EvidenceBundle:
    return replace(bundle, question=replace(bundle.question, text=text))


def _replace_candidate(
    bundle: EvidenceBundle,
    index: int,
    *,
    metadata_updates: dict | None = None,
    metadata_remove: tuple[str, ...] = (),
    candidate_updates: dict | None = None,
) -> EvidenceBundle:
    candidates = list(bundle.candidates)
    candidate = candidates[index]
    metadata = deepcopy(dict(candidate.metadata))
    metadata.update(metadata_updates or {})
    for key in metadata_remove:
        metadata.pop(key, None)
    candidates[index] = replace(
        candidate,
        metadata=metadata,
        **dict(candidate_updates or {}),
    )
    return replace(bundle, candidates=tuple(candidates))


def _replace_all_proof(bundle: EvidenceBundle, **updates: object) -> EvidenceBundle:
    result = bundle
    for index in range(len(result.candidates)):
        result = _replace_candidate(result, index, metadata_updates=dict(updates))
    return result


def _rebuild_bundle(
    bundle: EvidenceBundle,
    *,
    question_text: str | None = None,
    headers: tuple[str, ...] | None = None,
    cells: tuple[tuple[str, ...], ...] | None = None,
) -> EvidenceBundle:
    first = bundle.candidates[0].metadata
    return _bundle(
        question_text=question_text or bundle.question.text,
        headers=headers or tuple(first["headers"]),
        cells=cells or tuple(tuple(item.metadata["cell_texts"]) for item in bundle.candidates),
        doc_id=str(first["doc_id"]),
        page_idx=int(first["page_idx"]),
        table_index=int(first["table_index"]),
        row_span_start=int(first["row_span_start"]),
    )


@pytest.mark.parametrize(
    ("question_text", "headers", "cells", "expected", "unit", "dimension"),
    [
        (
            "三个部门利润合计是多少？",
            ("部门", "利润（万元）"),
            (("一部", "10"), ("二部", "20"), ("三部", "30")),
            Decimal("60"),
            "万元",
            "currency",
        ),
        (
            "项目成本总和是多少？",
            ("项目", "成本（元）"),
            (("A", "1,000"), ("B", "2,500"), ("C", "500")),
            Decimal("4000"),
            "元",
            "currency",
        ),
        (
            "各区域净变动共计多少？",
            ("区域", "净变动（万元）"),
            (("东区", "20"), ("西区", "(5)"), ("南区", "10")),
            Decimal("25"),
            "万元",
            "currency",
        ),
    ],
)
def test_positive_cases_bind_existing_request_and_execute_c3_m(
    question_text: str,
    headers: tuple[str, ...],
    cells: tuple[tuple[str, ...], ...],
    expected: Decimal,
    unit: str,
    dimension: str,
) -> None:
    bundle = _bundle(question_text=question_text, headers=headers, cells=cells)

    binding = SourceBoundSumSeriesBinder().bind(bundle)

    assert binding.ready is True
    assert binding.reasons == ()
    assert isinstance(binding.request, SourceBoundNumericSeriesAggregationRequest)
    assert binding.request.selectors == (AggregationSelector.SUM,)
    assert binding.request.question_aggregation_match.passed is True
    assert binding.metadata["provider_calls"] == 0
    assert binding.metadata["legacy_calls"] == 0
    assert binding.metadata["network_calls"] == 0
    assert binding.metadata["total_tokens"] == 0
    assert [item.position for item in binding.request.series.items] == [0, 1, 2]
    assert [item.header_label for item in binding.request.series.items] == [
        row[0] for row in cells
    ]
    assert all(item.unit == unit for item in binding.request.series.items)
    assert all(item.dimension == dimension for item in binding.request.series.items)
    assert len({item.source_coordinate for item in binding.request.series.items}) == 3
    assert all(
        item.source_coordinate.startswith(candidate.source + "&column_index=")
        for item, candidate in zip(binding.request.series.items, bundle.candidates)
    )
    assert tuple(item.source_ref for item in binding.request.series.items) == binding.source_refs
    assert all(ref.source == binding.request.series.source_object_id for ref in binding.source_refs)
    assert [step["stage"] for step in binding.trace] == [
        "question_intent",
        "complete_range_proof",
        "column_binding",
        "request_construction",
    ]

    execution = SourceBoundNumericSeriesAggregator().execute(binding.request)

    assert execution.ok is True
    assert execution.value == expected
    assert execution.trace
    assert len(execution.source_refs) == 3
    assert execution.gate_status == "PASS"


def _case_no_sum(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_question(bundle, "三个部门利润分别是多少？")


def _case_sum_avg(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_question(bundle, "三个部门利润平均值和合计是多少？")


def _case_empty(bundle: EvidenceBundle) -> EvidenceBundle:
    return replace(bundle, candidates=())


def _case_non_structured(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(
        bundle, 0, metadata_updates={"structured_table_evidence": False}
    )


def _case_cross_document(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(
        bundle,
        1,
        metadata_updates={"doc_id": "other-doc"},
        candidate_updates={"doc_id": "other-doc"},
    )


def _case_cross_page(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(bundle, 1, metadata_updates={"page_idx": 1})


def _case_cross_table(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(bundle, 1, metadata_updates={"table_index": 1})


def _case_row_gap(bundle: EvidenceBundle) -> EvidenceBundle:
    return replace(bundle, candidates=(bundle.candidates[0], bundle.candidates[2]))


def _case_duplicate_row(bundle: EvidenceBundle) -> EvidenceBundle:
    return replace(
        bundle,
        candidates=(bundle.candidates[0], bundle.candidates[0], bundle.candidates[2]),
    )


def _case_start_wrong(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_all_proof(
        bundle,
        row_span_start=1,
        row_span_end_exclusive=4,
        row_span_start_explicit=False,
        table_row_indices=[1, 2, 3],
    )


def _case_count_small(bundle: EvidenceBundle) -> EvidenceBundle:
    sources = [candidate.source for candidate in bundle.candidates[:2]]
    return _replace_all_proof(
        bundle,
        table_data_row_count=2,
        row_span_end_exclusive=2,
        table_row_indices=[0, 1],
        table_row_sources=sources,
    )


def _case_count_large(bundle: EvidenceBundle) -> EvidenceBundle:
    source_object = bundle.candidates[0].metadata["table_source_object_id"]
    sources = [candidate.source for candidate in bundle.candidates] + [
        f"{source_object}&row_index=3"
    ]
    return _replace_all_proof(
        bundle,
        table_data_row_count=4,
        row_span_end_exclusive=4,
        table_row_indices=[0, 1, 2, 3],
        table_row_sources=sources,
    )


def _case_missing_proof(bundle: EvidenceBundle) -> EvidenceBundle:
    result = bundle
    for index in range(len(result.candidates)):
        result = _replace_candidate(
            result, index, metadata_remove=("table_range_digest",)
        )
    return result


def _case_two_numeric_ambiguous(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        question_text="各部门金额合计是多少？",
        headers=("部门", "收入（万元）", "利润（万元）"),
        cells=(
            ("一部", "100", "10"),
            ("二部", "200", "20"),
            ("三部", "300", "30"),
        ),
    )


def _case_no_numeric(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        question_text="各部门金额合计是多少？",
        headers=("部门", "说明"),
        cells=(("一部", "良好"), ("二部", "一般"), ("三部", "较好")),
    )


def _case_label_empty(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        cells=(("一部", "10"), ("", "20"), ("三部", "30"))
    )


def _case_numeric_empty(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        cells=(("一部", "10"), ("二部", ""), ("三部", "30"))
    )


def _case_non_finite(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        cells=(("一部", "10"), ("二部", "NaN"), ("三部", "30"))
    )


def _case_mixed_units(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        headers=("部门", "利润"),
        cells=(("一部", "10万元"), ("二部", "20元"), ("三部", "30万元")),
    )


def _case_amount_percent(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        headers=("部门", "利润"),
        cells=(("一部", "10万元"), ("二部", "20%"), ("三部", "30万元")),
    )


def _case_duplicate_source(bundle: EvidenceBundle) -> EvidenceBundle:
    first_source = bundle.candidates[0].source
    return _replace_candidate(
        bundle,
        1,
        metadata_updates={"canonical_source": first_source},
        candidate_updates={"source": first_source},
    )


def _case_source_object_mismatch(bundle: EvidenceBundle) -> EvidenceBundle:
    different = "data/local/other.json#page_idx=0&table_index=0"
    source = different + "&row_index=1"
    return _replace_candidate(
        bundle,
        1,
        metadata_updates={
            "table_source_object_id": different,
            "canonical_source": source,
        },
        candidate_updates={"source": source},
    )


def _case_coordinated_row_source_attack(bundle: EvidenceBundle) -> EvidenceBundle:
    candidates = list(bundle.candidates)
    attacked_sources = [candidate.source for candidate in candidates]
    attacked_sources[1] = (
        str(candidates[1].metadata["table_source_object_id"]) + "&row_index=99"
    )
    headers = tuple(candidates[0].metadata["headers"])
    cells = tuple(tuple(candidate.metadata["cell_texts"]) for candidate in candidates)
    row_indices = tuple(int(candidate.metadata["row_index"]) for candidate in candidates)
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
        updates = {
            "table_row_sources": list(attacked_sources),
            "table_range_digest": digest,
        }
        candidate_updates: dict[str, str] = {}
        if index == 1:
            updates["canonical_source"] = attacked_sources[index]
            candidate_updates["source"] = attacked_sources[index]
        result = _replace_candidate(
            result,
            index,
            metadata_updates=updates,
            candidate_updates=candidate_updates,
        )
    return result


def _case_coordinated_table_source_attack(bundle: EvidenceBundle) -> EvidenceBundle:
    candidates = list(bundle.candidates)
    wrong_table_source = (
        "data/local/other_content_list_v2.json#page_idx=9&table_index=8"
    )
    attacked_sources = tuple(
        f"{wrong_table_source}&row_index={candidate.metadata['row_index']}"
        for candidate in candidates
    )
    headers = tuple(candidates[0].metadata["headers"])
    cells = tuple(tuple(candidate.metadata["cell_texts"]) for candidate in candidates)
    row_indices = tuple(int(candidate.metadata["row_index"]) for candidate in candidates)
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
        result = _replace_candidate(
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


def _case_candidate_source_only_mismatch(bundle: EvidenceBundle) -> EvidenceBundle:
    wrong_source = (
        str(bundle.candidates[1].metadata["table_source_object_id"])
        + "&row_index=99"
    )
    return _replace_candidate(
        bundle,
        1,
        candidate_updates={"source": wrong_source},
    )


def _case_summary_detail(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        cells=(("一部", "10"), ("二部", "20"), ("合计", "30"))
    )


def _case_malformed_metadata(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(bundle, 0, metadata_updates={"headers": "部门,利润"})


def _case_metric_mismatch(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_question(bundle, "三个部门收入合计是多少？")


def _case_coordinated_truncation(bundle: EvidenceBundle) -> EvidenceBundle:
    truncated = replace(bundle, candidates=bundle.candidates[:2])
    sources = [candidate.source for candidate in truncated.candidates]
    return _replace_all_proof(
        truncated,
        table_data_row_count=2,
        row_span_end_exclusive=2,
        table_row_indices=[0, 1],
        table_row_sources=sources,
        # The independent full-table digest is deliberately not rewritten.
    )


def _case_inconsistent_proof(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(
        bundle, 1, metadata_updates={"row_span_end_exclusive": 99}
    )


def _case_header_inconsistent(bundle: EvidenceBundle) -> EvidenceBundle:
    return _replace_candidate(
        bundle, 1, metadata_updates={"headers": ["部门", "收入（万元）"]}
    )


def _case_column_count(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        cells=(("一部", "10"), ("二部",), ("三部", "30"))
    )


def _case_label_ambiguous(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        headers=("部门", "区域", "利润（万元）"),
        cells=(
            ("一部", "东区", "10"),
            ("二部", "西区", "20"),
            ("三部", "南区", "30"),
        ),
    )


def _case_invalid_numeric(bundle: EvidenceBundle) -> EvidenceBundle:
    return _bundle(
        cells=(("一部", "10"), ("二部", "二十"), ("三部", "30"))
    )


_NEGATIVE_CASES: tuple[
    tuple[str, Callable[[EvidenceBundle], EvidenceBundle], str], ...
] = (
    ("no_sum_intent", _case_no_sum, QUESTION_SUM_INTENT_MISSING),
    ("sum_and_average", _case_sum_avg, QUESTION_AGGREGATION_AMBIGUOUS),
    ("empty_candidates", _case_empty, EMPTY_CANDIDATES),
    ("non_structured", _case_non_structured, NON_STRUCTURED_TABLE_CANDIDATE),
    ("cross_document", _case_cross_document, CROSS_DOCUMENT_TABLE),
    ("cross_page", _case_cross_page, CROSS_PAGE_TABLE),
    ("cross_table", _case_cross_table, CROSS_TABLE),
    ("row_gap", _case_row_gap, ROW_INDEX_GAP),
    ("row_duplicate", _case_duplicate_row, ROW_INDEX_DUPLICATE),
    ("wrong_start", _case_start_wrong, ROW_SPAN_START_INVALID),
    ("declared_count_small", _case_count_small, ROW_COUNT_MISMATCH),
    ("declared_count_large", _case_count_large, ROW_COUNT_MISMATCH),
    ("missing_range_proof", _case_missing_proof, RANGE_PROOF_MISSING),
    ("two_numeric_columns", _case_two_numeric_ambiguous, NUMERIC_COLUMN_AMBIGUOUS),
    ("no_numeric_column", _case_no_numeric, NUMERIC_COLUMN_MISSING),
    ("empty_label", _case_label_empty, LABEL_VALUE_EMPTY),
    ("empty_numeric", _case_numeric_empty, NUMERIC_VALUE_EMPTY),
    ("non_finite", _case_non_finite, NON_FINITE_NUMBER),
    ("mixed_amount_units", _case_mixed_units, MIXED_UNITS),
    ("amount_percentage_mixed", _case_amount_percent, PERCENT_NOT_SUPPORTED),
    ("duplicate_canonical_source", _case_duplicate_source, DUPLICATE_CANONICAL_SOURCE),
    ("source_object_mismatch", _case_source_object_mismatch, SOURCE_OBJECT_MISMATCH),
    (
        "coordinated_row_source_attack",
        _case_coordinated_row_source_attack,
        ROW_SOURCE_IDENTITY_MISMATCH,
    ),
    (
        "coordinated_table_source_attack",
        _case_coordinated_table_source_attack,
        TABLE_SOURCE_IDENTITY_MISMATCH,
    ),
    (
        "candidate_source_only_mismatch",
        _case_candidate_source_only_mismatch,
        ROW_SOURCE_IDENTITY_MISMATCH,
    ),
    ("detail_and_total", _case_summary_detail, SUMMARY_DETAIL_CONFLICT),
    ("malformed_metadata", _case_malformed_metadata, MALFORMED_CANDIDATE_METADATA),
    ("metric_header_mismatch", _case_metric_mismatch, METRIC_HEADER_MISMATCH),
    (
        "coordinated_truncation",
        _case_coordinated_truncation,
        RANGE_PROOF_DIGEST_MISMATCH,
    ),
    ("inconsistent_proof", _case_inconsistent_proof, RANGE_PROOF_INCONSISTENT),
    ("header_inconsistent", _case_header_inconsistent, HEADER_SCHEMA_INCONSISTENT),
    ("column_count_mismatch", _case_column_count, COLUMN_COUNT_MISMATCH),
    ("label_column_ambiguous", _case_label_ambiguous, LABEL_COLUMN_AMBIGUOUS),
    ("invalid_numeric", _case_invalid_numeric, NUMERIC_VALUE_INVALID),
)


@pytest.mark.parametrize(
    ("case_name", "mutation", "expected_reason"),
    _NEGATIVE_CASES,
    ids=[case[0] for case in _NEGATIVE_CASES],
)
def test_fail_closed_matrix_is_stable(
    case_name: str,
    mutation: Callable[[EvidenceBundle], EvidenceBundle],
    expected_reason: str,
) -> None:
    bundle = mutation(_bundle())
    binder = SourceBoundSumSeriesBinder()

    first = binder.bind(bundle)
    second = binder.bind(bundle)

    assert first.ready is False, case_name
    assert first.request is None, case_name
    assert expected_reason in first.reasons, (case_name, first.reasons)
    assert first.reasons == second.reasons, case_name
    assert first.to_dict() == second.to_dict(), case_name


def test_fail_closed_matrix_covers_at_least_twenty_five_categories() -> None:
    assert len(_NEGATIVE_CASES) >= 25
    assert len({case_name for case_name, _mutation, _reason in _NEGATIVE_CASES}) == len(
        _NEGATIVE_CASES
    )
    required = {
        QUESTION_SUM_INTENT_MISSING,
        QUESTION_AGGREGATION_AMBIGUOUS,
        EMPTY_CANDIDATES,
        NON_STRUCTURED_TABLE_CANDIDATE,
        CROSS_DOCUMENT_TABLE,
        CROSS_PAGE_TABLE,
        CROSS_TABLE,
        ROW_INDEX_GAP,
        ROW_INDEX_DUPLICATE,
        ROW_SPAN_START_INVALID,
        ROW_COUNT_MISMATCH,
        RANGE_PROOF_MISSING,
        NUMERIC_COLUMN_AMBIGUOUS,
        NUMERIC_COLUMN_MISSING,
        LABEL_VALUE_EMPTY,
        NUMERIC_VALUE_EMPTY,
        NON_FINITE_NUMBER,
        MIXED_UNITS,
        PERCENT_NOT_SUPPORTED,
        DUPLICATE_CANONICAL_SOURCE,
        ROW_SOURCE_IDENTITY_MISMATCH,
        SOURCE_OBJECT_MISMATCH,
        SUMMARY_DETAIL_CONFLICT,
        TABLE_SOURCE_IDENTITY_MISMATCH,
        MALFORMED_CANDIDATE_METADATA,
        METRIC_HEADER_MISMATCH,
        RANGE_PROOF_DIGEST_MISMATCH,
    }
    assert required <= {reason for _name, _mutation, reason in _NEGATIVE_CASES}


def test_structured_table_loader_emits_independent_complete_range_facts(
    tmp_path: Path,
) -> None:
    document_root = tmp_path / "financial_report" / "proof-doc" / "auto"
    document_root.mkdir(parents=True)
    content_list = [
        {
            "type": "table",
            "page_idx": 0,
            "table_body": (
                "<table><tr><th>部门</th><th>利润（万元）</th></tr>"
                "<tr><td>一部</td><td>10</td></tr>"
                "<tr><td>二部</td><td>20</td></tr>"
                "<tr><td>三部</td><td>30</td></tr></table>"
            ),
        }
    ]
    (document_root / "proof-doc_content_list_v2.json").write_text(
        json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
    )

    rows = load_structured_table_rows(
        tmp_path, "financial_report", "proof-doc"
    )

    assert len(rows) == 3
    assert [row.row_index for row in rows] == [0, 1, 2]
    assert all(row.table_data_row_count == 3 for row in rows)
    assert all(row.row_span_start == 0 for row in rows)
    assert all(row.row_span_end_exclusive == 3 for row in rows)
    assert all(row.row_span_complete is True for row in rows)
    assert all(row.row_span_start_explicit is True for row in rows)
    assert all(row.table_row_indices == (0, 1, 2) for row in rows)
    assert all(
        row.table_row_sources == tuple(item.canonical_source for item in rows)
        for row in rows
    )
    assert len({row.table_range_digest for row in rows}) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", rows[0].table_range_digest)
    assert all(
        row.table_source_object_id
        == (
            f"{row.mineru_json_source}#page_idx={row.page_idx}"
            f"&table_index={row.table_index}"
        )
        for row in rows
    )
    assert all(
        row.canonical_source
        == f"{row.table_source_object_id}&row_index={row.row_index}"
        for row in rows
    )

    candidates = tuple(
        EvidenceCandidate(
            domain=row.domain,
            doc_id=row.doc_id,
            source=row.canonical_source,
            text=row.normalized_row_text,
            retriever="mineru_structured_table",
            metadata={
                **row.to_dict(),
                "source_kind": "mineru_structured_table",
                "structured_table_evidence": True,
            },
        )
        for row in rows
    )
    bundle = EvidenceBundle(
        question=Question(
            qid="real-loader-chain",
            domain="financial_report",
            text="三个部门利润合计是多少？",
            options={},
            answer_format="number",
            doc_ids=("proof-doc",),
        ),
        classification=ClassificationResult(labels=(QuestionLabel.CALCULATION,)),
        candidates=candidates,
        prompt_context="",
        estimated_tokens=0,
    )

    binding = SourceBoundSumSeriesBinder().bind(bundle)

    assert binding.ready is True
    assert binding.request is not None
    assert all(
        item.source_object_id == rows[0].table_source_object_id
        and item.source_ref is not None
        and item.source_ref.source == rows[0].table_source_object_id
        and item.source_coordinate
        == f"{row.canonical_source}&column_index=1"
        for item, row in zip(binding.request.series.items, rows)
    )
    execution = SourceBoundNumericSeriesAggregator().execute(binding.request)
    assert execution.ok is True
    assert execution.value == Decimal("60")


def test_binder_source_has_no_routing_provider_benchmark_or_broad_exception_logic() -> None:
    source = (ROOT / "src/evidence/c3_numeric_series_binding.py").read_text(
        encoding="utf-8"
    )

    assert "CalculationSolver" not in source
    assert "RoutedSolver" not in source
    assert "EnhancedBaselineWorkflow" not in source
    assert "C3ShadowObserver" not in source
    assert "OpenAI" not in source
    assert "requests." not in source
    assert "urllib" not in source
    assert "taxonomy" not in source.lower()
    assert "case_id" not in source
    assert "except Exception" not in source
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        source,
        re.IGNORECASE,
    )


def test_binder_does_not_execute_c3_m_during_request_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden_execute(self, request):
        nonlocal calls
        calls += 1
        raise AssertionError("binder must not execute C3-M")

    monkeypatch.setattr(
        SourceBoundNumericSeriesAggregator, "execute", forbidden_execute
    )

    result = SourceBoundSumSeriesBinder().bind(_bundle())

    assert result.ready is True
    assert calls == 0


def test_machine_report_is_deterministic_complete_and_stage_bounded() -> None:
    first = build_report()
    second = build_report()

    validate_report(first)
    assert first == second
    assert first["schema_version"] == EVALUATION_SCHEMA_VERSION
    assert first["binding_success_count"] == 3
    assert first["c3_execution_correct_count"] == 3
    assert first["binding_failure_count"] >= 25
    assert first["binding_failure_count"] == len(first["negative_cases"])
    assert first["stable_failure_count"] == len(first["negative_cases"])
    assert first["source_lineage_complete"] is True
    assert [case["execution_value"] for case in first["positive_cases"]] == [
        "60",
        "4000",
        "25",
    ]
    assert all(case["ready"] is True for case in first["positive_cases"])
    assert all(
        case["rejected_as_expected"] is True
        and case["stable"] is True
        and case["request_is_none"] is True
        for case in first["negative_cases"]
    )
    assert first["stage_boundary"] == {
        "request_construction_only": True,
        "normal_pipeline_integrated": False,
        "shadow_integrated": False,
        "production_routing_integrated": False,
        "supported_selector": "SUM",
    }
    assert first["provider_calls"] == 0
    assert first["legacy_calls"] == 0
    assert first["network_calls"] == 0
    assert first["total_tokens"] == 0
    assert first["measurement_valid"] is True


def test_evaluation_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_c3_source_bound_sum_series_binder.py",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "measurement_valid=true" in completed.stdout
    assert "positive=3" in completed.stdout
    written = json.loads(output.read_text(encoding="utf-8"))
    validate_report(written)
    assert written == build_report()
