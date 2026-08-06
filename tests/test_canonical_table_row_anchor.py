from __future__ import annotations

from collections.abc import Sequence

from contracts import ClassificationResult, Question, QuestionLabel
from document import CanonicalDocument, CanonicalPage, CanonicalTable, SourceLineage
from document.store import InMemoryDocumentStore
from retrieval.canonical_lexical import CanonicalLexicalEvidenceRetriever


SOURCE_OBJECT_ID = "fixture://table/revenue"


def _page_with_table(
    rows: Sequence[Sequence[str]],
    *,
    prefix: str,
    spans_mode: str = "valid",
) -> CanonicalPage:
    headers = ("Metric", "2024", "2023")
    parts = [prefix]
    coordinate_spans: dict[str, list[int] | object] = {}

    def append_row(row_index: int, cells: Sequence[str]) -> None:
        for column_index, cell in enumerate(cells):
            if column_index:
                parts.append("\t")
            start = sum(len(part) for part in parts)
            parts.append(str(cell))
            end = sum(len(part) for part in parts)
            coordinate_spans[
                f"{SOURCE_OBJECT_ID}/r{row_index}c{column_index}"
            ] = [start, end]
        parts.append("\n")

    append_row(0, headers)
    for row_index, row in enumerate(rows, start=1):
        append_row(row_index, row)

    if spans_mode == "missing":
        page_metadata: dict[str, object] = {}
    else:
        if spans_mode == "invalid":
            coordinate_spans[f"{SOURCE_OBJECT_ID}/r1c1"] = [0, "invalid"]
        page_metadata = {"coordinate_spans": coordinate_spans}

    lineage = SourceLineage(
        source_type="fixture",
        source_path=SOURCE_OBJECT_ID,
        parser_name="fixture",
        parser_version="1",
        page_number=7,
        source_page_index=6,
    )
    table = CanonicalTable(
        table_id=SOURCE_OBJECT_ID,
        page_number=7,
        headers=headers,
        rows=tuple(tuple(str(cell) for cell in row) for row in rows),
        lineage=lineage,
        metadata={"source_object_id": SOURCE_OBJECT_ID},
    )
    return CanonicalPage(
        page_number=7,
        text="".join(parts).rstrip("\n"),
        blocks=(),
        tables=(table,),
        lineage=lineage,
        metadata=page_metadata,
    )


def _retrieve(
    page: CanonicalPage,
    question_text: str,
) -> object:
    document = CanonicalDocument(
        document_id="doc1",
        domain="financial_reports",
        title="Fixture annual report",
        source_type="fixture",
        source_uri="fixture://doc1",
        parser_name="fixture",
        parser_version="1",
        pages=(page,),
    )
    retriever = CanonicalLexicalEvidenceRetriever(
        store=InMemoryDocumentStore.from_documents([document]),
        top_k_per_doc=1,
        window_chars=90,
        context_flank_chars=30,
    )
    question = Question(
        qid="generic-fixture",
        domain="financial_reports",
        text=question_text,
        options={},
        answer_format="free_text",
        doc_ids=("doc1",),
    )
    candidates = retriever.retrieve(
        question,
        ClassificationResult(labels=(QuestionLabel.CALCULATION,)),
    )
    assert len(candidates) == 1
    return candidates[0]


def test_unique_positive_row_label_anchors_complete_original_row_and_lineage() -> None:
    page = _page_with_table(
        (
            ("net sales", "100", "90"),
            ("operating profit", "20", "18"),
        ),
        prefix="Narrative net sales discussion appears before the table. " * 8,
    )

    candidate = _retrieve(page, "What were net sales?")

    assert candidate.text == "net sales\t100\t90"
    assert page.text.count(candidate.text) == 1
    assert candidate.metadata["lineage"] == {
        "source_path": SOURCE_OBJECT_ID,
        "page_number": 7,
        "source_page_index": 6,
    }
    anchor = candidate.metadata["table_row_anchor"]
    assert anchor["row_index"] == 1
    assert anchor["row_label"] == "net sales"
    assert anchor["coordinate_ids"] == [
        f"{SOURCE_OBJECT_ID}/r1c0",
        f"{SOURCE_OBJECT_ID}/r1c1",
        f"{SOURCE_OBJECT_ID}/r1c2",
    ]
    assert "gold" not in repr(candidate.metadata).lower()


def test_tied_highest_row_labels_fail_closed_to_original_window() -> None:
    page = _page_with_table(
        (
            ("net sales domestic", "60", "55"),
            ("net sales international", "40", "35"),
        ),
        prefix="Narrative net sales discussion appears before the table. " * 8,
    )

    candidate = _retrieve(page, "What were net sales?")

    assert "table_row_anchor" not in candidate.metadata
    assert candidate.text.startswith("Narrative net sales")
    assert "net sales domestic\t60\t55" not in candidate.text


def test_no_positive_row_label_match_fails_closed_to_original_window() -> None:
    page = _page_with_table(
        (
            ("net sales", "100", "90"),
            ("operating profit", "20", "18"),
        ),
        prefix="Narrative cash flow discussion appears before the table. " * 8,
    )

    candidate = _retrieve(page, "What was cash flow?")

    assert "table_row_anchor" not in candidate.metadata
    assert candidate.text.startswith("Narrative cash flow")


def test_missing_coordinate_spans_fail_closed_to_original_window() -> None:
    page = _page_with_table(
        (("net sales", "100", "90"),),
        prefix="Narrative net sales discussion appears before the table. " * 8,
        spans_mode="missing",
    )

    candidate = _retrieve(page, "What were net sales?")

    assert "table_row_anchor" not in candidate.metadata
    assert candidate.text.startswith("Narrative net sales")


def test_invalid_coordinate_span_fails_closed_to_original_window() -> None:
    page = _page_with_table(
        (("net sales", "100", "90"),),
        prefix="Narrative net sales discussion appears before the table. " * 8,
        spans_mode="invalid",
    )

    candidate = _retrieve(page, "What were net sales?")

    assert "table_row_anchor" not in candidate.metadata
    assert candidate.text.startswith("Narrative net sales")


def test_label_only_section_row_fails_closed_to_original_window() -> None:
    page = _page_with_table(
        (
            ("Alternative investments", "", ""),
            ("Private equity funds", "25", "20"),
        ),
        prefix="Narrative alternative investments discussion appears before the table. " * 8,
    )

    candidate = _retrieve(page, "How many components are under alternative investments?")

    assert "table_row_anchor" not in candidate.metadata
    assert candidate.text.startswith("Narrative alternative investments")
