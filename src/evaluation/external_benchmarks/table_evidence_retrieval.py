"""Provider-free question-to-table-evidence retrieval baseline.

This evaluator adapts the frozen FinQA/TAT-QA source splits to the existing
CanonicalDocument contract, runs the existing lexical document/evidence
retrievers without a gold document scope, and scores document, source-object,
and coordinate coverage after retrieval.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import socket
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from document.contracts import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from document.store import InMemoryDocumentStore
from retrieval.canonical_lexical import (
    CanonicalDocumentRetriever,
    CanonicalLexicalEvidenceRetriever,
)
from retrieval.interfaces import DocumentHit


SCHEMA_VERSION = "c3-question-table-evidence-retrieval-baseline/v1"
DOMAIN = "external_table_benchmark"
EXPECTED_MANIFEST_SHA256 = (
    "9ab30f6b0fd960cb35b8821784cd7e256abd46851364fb7057a60e398894951b"
)
EXPECTED_SOURCE_TAXONOMY_SHA256 = (
    "10a406d714f8aea1c2f06b1fe594334459c3335fd97fae3e55d68ca5a7788722"
)
EXPECTED_CASE_COUNT = 54
EXPECTED_UNIQUE_DOCUMENT_COUNT = 46
EXPECTED_DATASET_COUNTS = {"finqa": 34, "tatqa": 20}
EXPECTED_CAPABILITY_COUNTS = {
    "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION": 33,
    "SOURCE_BOUND_TABLE_ARGMAX_LABEL": 1,
    "SOURCE_BOUND_TABLE_MISSING_VALUE_CARDINALITY": 1,
    "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY": 16,
    "SOURCE_BOUND_TABLE_SECTION_CARDINALITY": 3,
}
EXPECTED_SPLIT_SHA256 = {
    "finqa": "a847fb7e0d61a3125a1e2909852df6b89f1ee64d2c5ff1bf689e332214deee51",
    "tatqa": "8da095a819af6db3c14877c6df2d4d29960e41d1a63dd1fa853507bd2a616af5",
}
EXPECTED_REPOSITORY_COMMITS = {
    "finqa": "0f16e2867befa6840783e58be38c9efb9229d742",
    "tatqa": "870accc41953dcde885aabeb963d94aabdc0fbc3",
}
TERMINAL_LAYERS = (
    "SOURCE_ADAPTER_ERROR",
    "DOCUMENT_MISS",
    "TABLE_SOURCE_MISS",
    "MEMBER_RANGE_INCOMPLETE",
    "BINDING_READY",
)
RATE_FIELDS = (
    "required_document_recall_at_1",
    "required_document_recall_at_3",
    "required_document_recall_at_5",
    "gold_table_source_recall_at_1",
    "gold_table_source_recall_at_3",
    "gold_table_source_recall_at_5",
    "gold_table_source_recall_at_15",
    "gold_table_source_recall_at_25",
    "gold_member_coordinate_micro_coverage_at_5",
)
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_COORDINATE_RE = re.compile(r"^(?P<source>.+)/r(?P<row>\d+)c(?P<column>\d+)$")


class RetrievalBaselineValidationError(ValueError):
    """Raised when frozen inputs or generated measurements are inconsistent."""


@dataclass(frozen=True)
class AdaptedCorpus:
    documents: tuple[CanonicalDocument, ...]
    store: InMemoryDocumentStore
    pages_by_source_object: Mapping[str, CanonicalPage]
    document_id_by_source_key: Mapping[tuple[str, str], str]
    source_object_count: int


@dataclass
class RecordingDocumentRetriever:
    """Spy wrapper that delegates ranking to the existing product retriever."""

    delegate: CanonicalDocumentRetriever
    calls: list[dict[str, Any]]
    name: str = "recording_canonical_document_lexical"

    def retrieve_documents(
        self,
        question: Question,
        classification: ClassificationResult,
        store: InMemoryDocumentStore,
    ) -> Sequence[DocumentHit]:
        self.calls.append(_retrieval_input_snapshot(question, classification))
        return self.delegate.retrieve_documents(question, classification, store)


@contextmanager
def deny_network() -> Iterable[dict[str, int]]:
    """Fail closed if an evaluator path attempts network access."""

    counter = {"count": 0}
    old_connect = socket.socket.connect
    old_create_connection = socket.create_connection

    def blocked(*args: Any, **kwargs: Any) -> Any:
        counter["count"] += 1
        raise AssertionError("network access is forbidden during retrieval evaluation")

    socket.socket.connect = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield counter
    finally:
        socket.socket.connect = old_connect  # type: ignore[assignment]
        socket.create_connection = old_create_connection  # type: ignore[assignment]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _as_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalBaselineValidationError(f"{field} must be an object")
    return value


def _as_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalBaselineValidationError(f"{field} must be a list")
    return value


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RetrievalBaselineValidationError(f"{field} must be an integer")
    return value


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value": (int(numerator) / int(denominator)) if denominator else 0.0,
    }


def _required_document_id(entry: Mapping[str, Any]) -> str:
    dataset = str(entry.get("dataset") or "")
    source_key = str(entry.get("source_document_key") or "")
    if dataset == "finqa" and source_key:
        return f"finqa::{source_key}"
    if dataset == "tatqa" and source_key:
        return f"tatqa::doc::{source_key}"
    raise RetrievalBaselineValidationError("manifest entry has invalid document identity")


def _gold_coordinates(entry: Mapping[str, Any]) -> tuple[str, ...]:
    coordinates: list[str] = []
    rows = _as_list(
        entry.get("bound_member_or_value_coordinates"),
        "bound_member_or_value_coordinates",
    )
    for row in rows:
        mapping = _as_mapping(row, "bound coordinate")
        for key, value in mapping.items():
            if key.endswith("coordinate"):
                text = str(value or "")
                if text:
                    coordinates.append(text)
    result = tuple(sorted(set(coordinates)))
    if not result:
        raise RetrievalBaselineValidationError(
            f"case {entry.get('case_id')} has no gold coordinates"
        )
    return result


def _expected_coordinate_raw_values(entry: Mapping[str, Any]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for row in _as_list(
        entry.get("bound_member_or_value_coordinates"),
        "bound_member_or_value_coordinates",
    ):
        mapping = _as_mapping(row, "bound coordinate")
        coordinate = str(mapping.get("coordinate") or "")
        if coordinate:
            raw_value = mapping.get("raw_value")
            if raw_value is None:
                raw_value = mapping.get("member_label")
            if raw_value is not None:
                expected[coordinate] = str(raw_value)
        for coordinate_key, raw_key in (
            ("present_coordinate", "present_raw"),
            ("missing_coordinate", "missing_raw"),
        ):
            coordinate = str(mapping.get(coordinate_key) or "")
            raw_value = mapping.get(raw_key)
            if not coordinate or raw_value is None:
                continue
            text = str(raw_value)
            prior = expected.get(coordinate)
            if prior is not None and prior != text:
                raise RetrievalBaselineValidationError(
                    f"coordinate has conflicting expected raw values: {coordinate}"
                )
            expected[coordinate] = text
    return expected


def load_and_validate_manifest(
    manifest_path: Path,
    *,
    repo_root: Path,
) -> tuple[dict[str, Any], str]:
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_MANIFEST_SHA256:
        raise RetrievalBaselineValidationError(
            f"manifest sha256 mismatch: expected {EXPECTED_MANIFEST_SHA256}, got {digest}"
        )
    payload = json.loads(raw.decode("utf-8"))
    entries = _as_list(payload.get("entries"), "manifest.entries")
    if payload.get("case_count") != EXPECTED_CASE_COUNT or len(entries) != EXPECTED_CASE_COUNT:
        raise RetrievalBaselineValidationError("manifest case count mismatch")
    if payload.get("dataset_counts") != EXPECTED_DATASET_COUNTS:
        raise RetrievalBaselineValidationError("manifest dataset counts mismatch")
    if payload.get("capability_counts") != EXPECTED_CAPABILITY_COUNTS:
        raise RetrievalBaselineValidationError("manifest capability counts mismatch")
    if payload.get("unique_document_count") != EXPECTED_UNIQUE_DOCUMENT_COUNT:
        raise RetrievalBaselineValidationError("manifest unique document count mismatch")
    if payload.get("source_taxonomy_sha256") != EXPECTED_SOURCE_TAXONOMY_SHA256:
        raise RetrievalBaselineValidationError("manifest source taxonomy hash mismatch")

    taxonomy_path = repo_root / str(payload.get("source_taxonomy_path") or "")
    if not taxonomy_path.is_file() or sha256_file(taxonomy_path) != EXPECTED_SOURCE_TAXONOMY_SHA256:
        raise RetrievalBaselineValidationError("source taxonomy file hash mismatch")

    case_keys: set[tuple[str, str]] = set()
    required_doc_ids: set[str] = set()
    dataset_counts: Counter[str] = Counter()
    capability_counts: Counter[str] = Counter()
    for dataset in ("finqa", "tatqa"):
        dataset_entries = [row for row in entries if row.get("dataset") == dataset]
        if not dataset_entries:
            raise RetrievalBaselineValidationError(f"missing {dataset} entries")
        split_path = repo_root / str(dataset_entries[0].get("official_split_path") or "")
        if not split_path.is_file() or sha256_file(split_path) != EXPECTED_SPLIT_SHA256[dataset]:
            raise RetrievalBaselineValidationError(f"{dataset} split hash mismatch")
        if any(
            row.get("official_split_path") != dataset_entries[0].get("official_split_path")
            for row in dataset_entries
        ):
            raise RetrievalBaselineValidationError(f"{dataset} split paths disagree")

    for index, raw_entry in enumerate(entries):
        entry = _as_mapping(raw_entry, f"entry[{index}]")
        dataset = str(entry.get("dataset") or "")
        case_id = str(entry.get("case_id") or "")
        if dataset not in EXPECTED_DATASET_COUNTS or not case_id:
            raise RetrievalBaselineValidationError(f"entry[{index}] invalid case identity")
        key = (dataset, case_id)
        if key in case_keys:
            raise RetrievalBaselineValidationError(f"duplicate case identity: {key}")
        case_keys.add(key)
        required_doc_ids.add(_required_document_id(entry))
        dataset_counts[dataset] += 1
        capability_counts[str(entry.get("candidate_capability") or "")] += 1
        source_ids = tuple(str(item) for item in entry.get("bound_source_object_ids") or ())
        if not source_ids or any(not item for item in source_ids):
            raise RetrievalBaselineValidationError(f"case {case_id} has no source object")
        _gold_coordinates(entry)
        if entry.get("official_repository_commit") != EXPECTED_REPOSITORY_COMMITS[dataset]:
            raise RetrievalBaselineValidationError(f"case {case_id} repository commit mismatch")
        if entry.get("official_split_sha256") != EXPECTED_SPLIT_SHA256[dataset]:
            raise RetrievalBaselineValidationError(f"case {case_id} split hash mismatch")

    if dict(sorted(dataset_counts.items())) != EXPECTED_DATASET_COUNTS:
        raise RetrievalBaselineValidationError("entry dataset counts mismatch")
    if dict(sorted(capability_counts.items())) != EXPECTED_CAPABILITY_COUNTS:
        raise RetrievalBaselineValidationError("entry capability counts mismatch")
    if len(required_doc_ids) != EXPECTED_UNIQUE_DOCUMENT_COUNT:
        raise RetrievalBaselineValidationError("derived unique document count mismatch")
    return dict(payload), digest


def _append_text(parts: list[str], text: str) -> tuple[int, int]:
    start = sum(len(item) for item in parts)
    parts.append(text)
    return start, start + len(text)


def _render_page_text(
    *,
    paragraphs_before: Sequence[Any],
    table: Sequence[Sequence[Any]],
    paragraphs_after: Sequence[Any],
    source_object_id: str,
) -> tuple[str, dict[str, tuple[int, int]], str]:
    parts: list[str] = []
    coordinate_spans: dict[str, tuple[int, int]] = {}
    for paragraph in paragraphs_before:
        text = str(paragraph or "").strip()
        if text:
            parts.append(text + "\n")
    table_start = sum(len(item) for item in parts)
    for row_index, raw_row in enumerate(table):
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes, bytearray)):
            raise RetrievalBaselineValidationError("official table row schema invalid")
        for column_index, raw_cell in enumerate(raw_row):
            if column_index:
                parts.append("\t")
            cell = str(raw_cell or "")
            start, end = _append_text(parts, cell)
            coordinate = f"{source_object_id}/r{row_index}c{column_index}"
            if coordinate in coordinate_spans:
                raise RetrievalBaselineValidationError(f"duplicate coordinate: {coordinate}")
            coordinate_spans[coordinate] = (start, end)
        parts.append("\n")
    table_end = sum(len(item) for item in parts)
    for paragraph in paragraphs_after:
        text = str(paragraph or "").strip()
        if text:
            parts.append(text + "\n")
    page_text = "".join(parts).rstrip("\n")
    table_text = page_text[table_start:table_end].rstrip("\n")
    if not page_text or not table_text:
        raise RetrievalBaselineValidationError("adapted page text/table text is empty")
    return page_text, coordinate_spans, table_text


def _canonical_page(
    *,
    dataset: str,
    document_id: str,
    page_number: int,
    source_index: int,
    source_object_id: str,
    table: Sequence[Sequence[Any]],
    paragraphs_before: Sequence[Any],
    paragraphs_after: Sequence[Any],
) -> CanonicalPage:
    page_text, coordinate_spans, table_text = _render_page_text(
        paragraphs_before=paragraphs_before,
        table=table,
        paragraphs_after=paragraphs_after,
        source_object_id=source_object_id,
    )
    lineage = SourceLineage(
        source_type=f"{dataset}_official_split",
        source_path=source_object_id,
        parser_name="official_external_table_adapter",
        parser_version="v1",
        page_number=page_number,
        source_page_index=source_index,
        metadata={"dataset": dataset, "document_id": document_id},
    )
    normalized_table = tuple(tuple(str(cell or "") for cell in row) for row in table)
    table_contract = CanonicalTable(
        table_id=source_object_id,
        page_number=page_number,
        headers=normalized_table[0] if normalized_table else (),
        rows=normalized_table[1:] if len(normalized_table) > 1 else (),
        markdown=table_text,
        lineage=lineage,
        metadata={
            "source_object_id": source_object_id,
            "coordinate_spans": {
                key: [span[0], span[1]] for key, span in sorted(coordinate_spans.items())
            },
        },
    )
    block = CanonicalBlock(
        block_id=f"{source_object_id}#page",
        page_number=page_number,
        block_type=CanonicalBlockType.TABLE,
        text=page_text,
        reading_order=0,
        table_id=source_object_id,
        lineage=lineage,
        metadata={"source_object_id": source_object_id},
    )
    return CanonicalPage(
        page_number=page_number,
        text=page_text,
        blocks=(block,),
        tables=(table_contract,),
        lineage=lineage,
        metadata={
            "dataset": dataset,
            "source_object_id": source_object_id,
            "official_source_index": source_index,
            "coordinate_spans": {
                key: [span[0], span[1]] for key, span in sorted(coordinate_spans.items())
            },
            "included_official_fields": [
                "paragraphs_before",
                "table",
                "paragraphs_after",
            ],
            "excluded_gold_fields": [
                "question",
                "answer",
                "program",
                "derivation",
                "candidate_capability",
            ],
        },
    )


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_coordinate_against_table(
    coordinate: str,
    *,
    source_object_id: str,
    table: Sequence[Sequence[Any]],
    expected_raw: str | None = None,
) -> None:
    match = _COORDINATE_RE.fullmatch(coordinate)
    if match is None or match.group("source") != source_object_id:
        raise RetrievalBaselineValidationError(f"coordinate source mismatch: {coordinate}")
    row_index = int(match.group("row"))
    column_index = int(match.group("column"))
    if row_index >= len(table):
        raise RetrievalBaselineValidationError(f"coordinate row outside table: {coordinate}")
    row = table[row_index]
    if column_index >= len(row):
        raise RetrievalBaselineValidationError(f"coordinate column outside table: {coordinate}")
    if expected_raw is not None and str(row[column_index]) != str(expected_raw):
        raise RetrievalBaselineValidationError(
            f"coordinate raw value mismatch: {coordinate}: "
            f"expected {expected_raw!r}, got {str(row[column_index])!r}"
        )


def adapt_corpus(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
) -> AdaptedCorpus:
    entries = [_as_mapping(item, "manifest entry") for item in manifest["entries"]]
    finqa_path = repo_root / str(
        next(item for item in entries if item["dataset"] == "finqa")["official_split_path"]
    )
    tatqa_path = repo_root / str(
        next(item for item in entries if item["dataset"] == "tatqa")["official_split_path"]
    )
    finqa_split = _as_list(_load_json(finqa_path), "finqa split")
    tatqa_split = _as_list(_load_json(tatqa_path), "tatqa split")

    selected_finqa_filenames = {
        str(item["source_document_key"])
        for item in entries
        if item["dataset"] == "finqa"
    }
    selected_tatqa_indexes = {
        _strict_int(item["document_index"], "tatqa document_index")
        for item in entries
        if item["dataset"] == "tatqa"
    }

    for entry in entries:
        dataset = str(entry["dataset"])
        case_id = str(entry["case_id"])
        document_index = _strict_int(entry["document_index"], "document_index")
        expected_raw_values = _expected_coordinate_raw_values(entry)
        if dataset == "finqa":
            if document_index >= len(finqa_split):
                raise RetrievalBaselineValidationError(f"FinQA index outside split: {case_id}")
            record = _as_mapping(finqa_split[document_index], "FinQA record")
            if record.get("id") != case_id:
                raise RetrievalBaselineValidationError(f"FinQA case id mismatch: {case_id}")
            if record.get("filename") != entry.get("source_document_key"):
                raise RetrievalBaselineValidationError(f"FinQA filename mismatch: {case_id}")
            if _as_mapping(record.get("qa"), "FinQA qa").get("question") != entry.get("question"):
                raise RetrievalBaselineValidationError(f"FinQA question mismatch: {case_id}")
            source_object_id = f"finqa://dev/{case_id}"
            if tuple(entry.get("bound_source_object_ids") or ()) != (source_object_id,):
                raise RetrievalBaselineValidationError(f"FinQA source object mismatch: {case_id}")
            table = _as_list(record.get("table"), "FinQA table")
        else:
            if document_index >= len(tatqa_split):
                raise RetrievalBaselineValidationError(f"TAT-QA index outside split: {case_id}")
            record = _as_mapping(tatqa_split[document_index], "TAT-QA record")
            table_record = _as_mapping(record.get("table"), "TAT-QA table record")
            table_uid = str(table_record.get("uid") or "")
            if table_uid != entry.get("table_uid"):
                raise RetrievalBaselineValidationError(f"TAT-QA table UID mismatch: {case_id}")
            questions = {
                str(item.get("uid") or ""): item
                for item in _as_list(record.get("questions"), "TAT-QA questions")
                if isinstance(item, Mapping)
            }
            question = questions.get(case_id)
            if question is None or question.get("question") != entry.get("question"):
                raise RetrievalBaselineValidationError(f"TAT-QA question mismatch: {case_id}")
            source_object_id = f"tatqa://table/{table_uid}"
            if tuple(entry.get("bound_source_object_ids") or ()) != (source_object_id,):
                raise RetrievalBaselineValidationError(f"TAT-QA source object mismatch: {case_id}")
            table = _as_list(table_record.get("table"), "TAT-QA table")

        for coordinate in _gold_coordinates(entry):
            _validate_coordinate_against_table(
                coordinate,
                source_object_id=source_object_id,
                table=table,
                expected_raw=expected_raw_values.get(coordinate),
            )

    documents: list[CanonicalDocument] = []
    pages_by_source_object: dict[str, CanonicalPage] = {}
    document_id_by_source_key: dict[tuple[str, str], str] = {}

    finqa_grouped: MutableMapping[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, raw_record in enumerate(finqa_split):
        record = _as_mapping(raw_record, "FinQA record")
        filename = str(record.get("filename") or "")
        if filename in selected_finqa_filenames:
            finqa_grouped[filename].append((index, record))

    for filename in sorted(selected_finqa_filenames):
        rows = finqa_grouped.get(filename) or []
        if not rows:
            raise RetrievalBaselineValidationError(f"FinQA document missing: {filename}")
        document_id = f"finqa::{filename}"
        pages: list[CanonicalPage] = []
        for page_number, (source_index, record) in enumerate(rows, start=1):
            case_id = str(record.get("id") or "")
            source_object_id = f"finqa://dev/{case_id}"
            page = _canonical_page(
                dataset="finqa",
                document_id=document_id,
                page_number=page_number,
                source_index=source_index,
                source_object_id=source_object_id,
                table=_as_list(record.get("table"), "FinQA table"),
                paragraphs_before=tuple(record.get("pre_text") or ()),
                paragraphs_after=tuple(record.get("post_text") or ()),
            )
            if source_object_id in pages_by_source_object:
                raise RetrievalBaselineValidationError(
                    f"duplicate source object across documents: {source_object_id}"
                )
            pages_by_source_object[source_object_id] = page
            pages.append(page)
        documents.append(
            CanonicalDocument(
                document_id=document_id,
                domain=DOMAIN,
                title=filename,
                source_type="finqa_official_dev",
                source_uri=f"finqa://document/{filename}",
                parser_name="official_external_table_adapter",
                parser_version="v1",
                pages=tuple(pages),
                metadata={
                    "dataset": "finqa",
                    "source_document_key": filename,
                    "source_object_ids": [
                        str(page.metadata["source_object_id"]) for page in pages
                    ],
                },
            )
        )
        document_id_by_source_key[("finqa", filename)] = document_id

    for document_index in sorted(selected_tatqa_indexes):
        record = _as_mapping(tatqa_split[document_index], "TAT-QA record")
        table_record = _as_mapping(record.get("table"), "TAT-QA table record")
        table_uid = str(table_record.get("uid") or "")
        source_object_id = f"tatqa://table/{table_uid}"
        document_id = f"tatqa::doc::{document_index}"
        paragraphs = sorted(
            (
                item
                for item in _as_list(record.get("paragraphs"), "TAT-QA paragraphs")
                if isinstance(item, Mapping)
            ),
            key=lambda item: (int(item.get("order") or 0), str(item.get("uid") or "")),
        )
        paragraph_texts = tuple(str(item.get("text") or "") for item in paragraphs)
        page = _canonical_page(
            dataset="tatqa",
            document_id=document_id,
            page_number=1,
            source_index=document_index,
            source_object_id=source_object_id,
            table=_as_list(table_record.get("table"), "TAT-QA table"),
            paragraphs_before=paragraph_texts,
            paragraphs_after=(),
        )
        if source_object_id in pages_by_source_object:
            raise RetrievalBaselineValidationError(
                f"duplicate source object across documents: {source_object_id}"
            )
        pages_by_source_object[source_object_id] = page
        title = next((text for text in paragraph_texts if text.strip()), f"document {document_index}")
        documents.append(
            CanonicalDocument(
                document_id=document_id,
                domain=DOMAIN,
                title=title,
                source_type="tatqa_official_dev",
                source_uri=f"tatqa://doc/{document_index}",
                parser_name="official_external_table_adapter",
                parser_version="v1",
                pages=(page,),
                metadata={
                    "dataset": "tatqa",
                    "source_document_key": str(document_index),
                    "table_uid": table_uid,
                    "source_object_ids": [source_object_id],
                },
            )
        )
        document_id_by_source_key[("tatqa", str(document_index))] = document_id

    documents.sort(key=lambda item: item.document_id)
    if len(documents) != EXPECTED_UNIQUE_DOCUMENT_COUNT:
        raise RetrievalBaselineValidationError(
            f"adapted document count mismatch: {len(documents)}"
        )
    if len({document.document_id for document in documents}) != len(documents):
        raise RetrievalBaselineValidationError("adapted document ids are not unique")

    required_source_ids = {
        str(source_id)
        for entry in entries
        for source_id in entry.get("bound_source_object_ids") or ()
    }
    for source_id in required_source_ids:
        if source_id not in pages_by_source_object:
            raise RetrievalBaselineValidationError(f"gold source object missing: {source_id}")

    store = InMemoryDocumentStore.from_documents(documents)
    return AdaptedCorpus(
        documents=tuple(documents),
        store=store,
        pages_by_source_object=dict(pages_by_source_object),
        document_id_by_source_key=dict(document_id_by_source_key),
        source_object_count=len(pages_by_source_object),
    )


def _generic_classification(question: Question) -> ClassificationResult:
    del question
    return ClassificationResult(
        labels=(QuestionLabel.DEFAULT,),
        reasons={"rule": "generic_external_table_question"},
    )


def _retrieval_input_snapshot(
    question: Question,
    classification: ClassificationResult,
) -> dict[str, Any]:
    payload = {
        "qid": str(question.qid),
        "domain": str(question.domain),
        "text_sha256": _sha256_text(question.text),
        "doc_ids": list(question.doc_ids),
        "candidate_doc_ids": list(question.candidate_doc_ids),
        "options": dict(question.options),
        "answer_format": str(question.answer_format),
        "raw": dict(question.raw),
        "classification_labels": [label.value for label in classification.labels],
        "classification_reasons": dict(classification.reasons),
    }
    payload["payload_sha256"] = _sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    return payload


def build_question(entry: Mapping[str, Any], ordinal: int) -> Question:
    return Question(
        qid=f"external-table-{ordinal:03d}",
        domain=DOMAIN,
        text=str(entry.get("question") or ""),
        options={},
        answer_format="freeform",
        doc_ids=(),
        candidate_doc_ids=(),
        raw={},
    )


def _candidate_source_object(candidate: EvidenceCandidate) -> str:
    lineage = candidate.metadata.get("lineage") if isinstance(candidate.metadata, Mapping) else None
    if not isinstance(lineage, Mapping):
        return ""
    return str(lineage.get("source_path") or "")


def _candidate_window_span(
    candidate: EvidenceCandidate,
    page: CanonicalPage,
) -> tuple[int, int] | None:
    if not candidate.text:
        return None
    first = page.text.find(candidate.text)
    if first < 0:
        return None
    if page.text.find(candidate.text, first + 1) >= 0:
        return None
    return first, first + len(candidate.text)


def _covered_coordinates(
    *,
    candidates: Sequence[EvidenceCandidate],
    pages_by_source_object: Mapping[str, CanonicalPage],
    gold_source_object_ids: Sequence[str],
    gold_coordinates: Sequence[str],
    top_k: int = 5,
) -> tuple[str, ...]:
    windows_by_source: MutableMapping[str, list[tuple[int, int]]] = defaultdict(list)
    for candidate in candidates[:top_k]:
        source_object_id = _candidate_source_object(candidate)
        if source_object_id not in gold_source_object_ids:
            continue
        page = pages_by_source_object.get(source_object_id)
        if page is None:
            continue
        span = _candidate_window_span(candidate, page)
        if span is not None:
            windows_by_source[source_object_id].append(span)

    covered: list[str] = []
    for coordinate in gold_coordinates:
        match = _COORDINATE_RE.fullmatch(coordinate)
        if match is None:
            continue
        source_object_id = match.group("source")
        page = pages_by_source_object.get(source_object_id)
        if page is None:
            continue
        coordinate_spans = page.metadata.get("coordinate_spans")
        if not isinstance(coordinate_spans, Mapping):
            continue
        raw_span = coordinate_spans.get(coordinate)
        if (
            not isinstance(raw_span, Sequence)
            or isinstance(raw_span, (str, bytes, bytearray))
            or len(raw_span) != 2
        ):
            continue
        coordinate_start, coordinate_end = int(raw_span[0]), int(raw_span[1])
        if any(
            window_start <= coordinate_start and coordinate_end <= window_end
            for window_start, window_end in windows_by_source.get(source_object_id, ())
        ):
            covered.append(coordinate)
    return tuple(sorted(set(covered)))


def classify_terminal_layer(
    *,
    adapter_ok: bool,
    document_hit_rank: int | None,
    gold_source_hit_ranks: Mapping[str, int | None],
    covered_coordinate_count: int,
    gold_coordinate_count: int,
) -> str:
    if not adapter_ok:
        return "SOURCE_ADAPTER_ERROR"
    if document_hit_rank is None or document_hit_rank > 5:
        return "DOCUMENT_MISS"
    if not any(
        rank is not None and rank <= 5 for rank in gold_source_hit_ranks.values()
    ):
        return "TABLE_SOURCE_MISS"
    if covered_coordinate_count != gold_coordinate_count:
        return "MEMBER_RANGE_INCOMPLETE"
    return "BINDING_READY"


def _document_hit_rank(hits: Sequence[DocumentHit], required_document_id: str) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if hit.document_id == required_document_id:
            return rank
    return None


def _source_hit_ranks(
    candidates: Sequence[EvidenceCandidate],
    gold_source_object_ids: Sequence[str],
) -> dict[str, int | None]:
    result: dict[str, int | None] = {source_id: None for source_id in gold_source_object_ids}
    for rank, candidate in enumerate(candidates, start=1):
        source_object_id = _candidate_source_object(candidate)
        if source_object_id in result and result[source_object_id] is None:
            result[source_object_id] = rank
    return result


def _document_hit_record(hit: DocumentHit, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "document_id": hit.document_id,
        "score": round(float(hit.score), 8),
        "retriever": hit.retriever,
        "metadata": deepcopy(dict(hit.metadata)),
    }


def _evidence_hit_record(candidate: EvidenceCandidate, rank: int) -> dict[str, Any]:
    metadata = dict(candidate.metadata or {})
    lineage = metadata.get("lineage") if isinstance(metadata.get("lineage"), Mapping) else {}
    return {
        "rank": rank,
        "document_id": candidate.doc_id,
        "source": candidate.source,
        "score": round(float(candidate.score), 8),
        "retriever": candidate.retriever,
        "source_object_id": _candidate_source_object(candidate),
        "page_number": metadata.get("page_number"),
        "matched_terms": list(metadata.get("matched_terms") or ()),
        "document_score": round(float(metadata.get("document_score") or 0.0), 8),
        "lineage": {
            "source_path": str(lineage.get("source_path") or ""),
            "page_number": lineage.get("page_number"),
            "source_page_index": lineage.get("source_page_index"),
        },
    }


def _gold_scope_audit(
    *,
    entry: Mapping[str, Any],
    calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    forbidden_exact_values = {
        str(entry.get("case_id") or ""),
        _required_document_id(entry),
        str(entry.get("filename") or ""),
        str(entry.get("table_uid") or ""),
        str(entry.get("candidate_capability") or ""),
        *[str(value) for value in entry.get("bound_source_object_ids") or ()],
        *_gold_coordinates(entry),
    }
    forbidden_exact_values.discard("")
    violations: list[str] = []
    for call_index, call in enumerate(calls):
        if call.get("doc_ids"):
            violations.append(f"call[{call_index}].doc_ids")
        if call.get("candidate_doc_ids"):
            violations.append(f"call[{call_index}].candidate_doc_ids")
        if call.get("options"):
            violations.append(f"call[{call_index}].options")
        if call.get("raw"):
            violations.append(f"call[{call_index}].raw")
        if str(call.get("qid") or "") in forbidden_exact_values:
            violations.append(f"call[{call_index}].qid")
        if any(
            str(label) in forbidden_exact_values
            for label in call.get("classification_labels") or ()
        ):
            violations.append(f"call[{call_index}].classification_labels")
        if any(
            str(value) in forbidden_exact_values
            for value in (call.get("classification_reasons") or {}).values()
        ):
            violations.append(f"call[{call_index}].classification_reasons")
    return {
        "call_count": len(calls),
        "question_text_sha256": _sha256_text(str(entry.get("question") or "")),
        "calls": [deepcopy(dict(call)) for call in calls],
        "gold_scope_injected": bool(violations),
        "violations": sorted(set(violations)),
    }


def evaluate_case(
    entry: Mapping[str, Any],
    *,
    ordinal: int,
    corpus: AdaptedCorpus,
) -> tuple[dict[str, Any], dict[str, Any]]:
    question = build_question(entry, ordinal)
    classification = _generic_classification(question)
    calls: list[dict[str, Any]] = []
    recording_retriever = RecordingDocumentRetriever(
        delegate=CanonicalDocumentRetriever(top_k=5),
        calls=calls,
    )
    document_hits = tuple(
        recording_retriever.retrieve_documents(question, classification, corpus.store)
    )
    evidence_retriever = CanonicalLexicalEvidenceRetriever(
        store=corpus.store,
        document_retriever=recording_retriever,
        top_k_per_doc=5,
    )
    evidence_candidates = tuple(evidence_retriever.retrieve(question, classification))

    required_document_id = _required_document_id(entry)
    gold_source_object_ids = tuple(
        str(item) for item in entry.get("bound_source_object_ids") or ()
    )
    gold_coordinates = _gold_coordinates(entry)
    document_rank = _document_hit_rank(document_hits, required_document_id)
    source_ranks = _source_hit_ranks(evidence_candidates, gold_source_object_ids)
    covered_coordinates = _covered_coordinates(
        candidates=evidence_candidates,
        pages_by_source_object=corpus.pages_by_source_object,
        gold_source_object_ids=gold_source_object_ids,
        gold_coordinates=gold_coordinates,
        top_k=5,
    )
    complete_member_coverage = len(covered_coordinates) == len(gold_coordinates)
    terminal_layer = classify_terminal_layer(
        adapter_ok=True,
        document_hit_rank=document_rank,
        gold_source_hit_ranks=source_ranks,
        covered_coordinate_count=len(covered_coordinates),
        gold_coordinate_count=len(gold_coordinates),
    )
    record = {
        "dataset": str(entry["dataset"]),
        "case_id": str(entry["case_id"]),
        "candidate_capability": str(entry["candidate_capability"]),
        "semantic_subfamily": str(entry["semantic_subfamily"]),
        "question_sha256": _sha256_text(str(entry["question"])),
        "required_document_id": required_document_id,
        "gold_source_object_ids": list(gold_source_object_ids),
        "gold_coordinates": list(gold_coordinates),
        "gold_coordinate_count": len(gold_coordinates),
        "document_top5": [
            _document_hit_record(hit, rank)
            for rank, hit in enumerate(document_hits[:5], start=1)
        ],
        "evidence_top25": [
            _evidence_hit_record(candidate, rank)
            for rank, candidate in enumerate(evidence_candidates[:25], start=1)
        ],
        "document_hit_rank": document_rank,
        "gold_source_hit_ranks": source_ranks,
        "covered_gold_coordinates_at_5": list(covered_coordinates),
        "complete_member_coverage_at_5": complete_member_coverage,
        "terminal_layer": terminal_layer,
    }
    audit = {
        "dataset": str(entry["dataset"]),
        "case_id": str(entry["case_id"]),
        **_gold_scope_audit(entry=entry, calls=calls),
    }
    return record, audit


def _rate_for_rank(records: Sequence[Mapping[str, Any]], field: str, top_k: int) -> dict[str, Any]:
    count = sum(
        isinstance(record.get(field), int)
        and not isinstance(record.get(field), bool)
        and int(record[field]) <= top_k
        for record in records
    )
    return _rate(count, len(records))


def _table_rate(records: Sequence[Mapping[str, Any]], top_k: int) -> dict[str, Any]:
    count = 0
    for record in records:
        ranks = _as_mapping(record.get("gold_source_hit_ranks"), "gold_source_hit_ranks")
        if any(
            isinstance(rank, int) and not isinstance(rank, bool) and rank <= top_k
            for rank in ranks.values()
        ):
            count += 1
    return _rate(count, len(records))


def aggregate_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: (str(item["dataset"]), str(item["case_id"])))
    total_coordinates = sum(int(item["gold_coordinate_count"]) for item in ordered)
    covered_coordinates = sum(len(item["covered_gold_coordinates_at_5"]) for item in ordered)
    terminal = Counter(str(item["terminal_layer"]) for item in ordered)

    def breakdown(field: str) -> dict[str, Any]:
        values = sorted({str(item[field]) for item in ordered})
        result: dict[str, Any] = {}
        for value in values:
            subset = [item for item in ordered if str(item[field]) == value]
            result[value] = {
                "case_count": len(subset),
                "required_document_recall_at_5": _rate_for_rank(
                    subset, "document_hit_rank", 5
                ),
                "gold_table_source_recall_at_5": _table_rate(subset, 5),
                "gold_member_coordinate_micro_coverage_at_5": _rate(
                    sum(len(item["covered_gold_coordinates_at_5"]) for item in subset),
                    sum(int(item["gold_coordinate_count"]) for item in subset),
                ),
                "complete_member_coverage_case_count_at_5": sum(
                    bool(item["complete_member_coverage_at_5"]) for item in subset
                ),
                "binding_ready_case_count_at_5": sum(
                    item["terminal_layer"] == "BINDING_READY" for item in subset
                ),
                "terminal_layer_counts": dict(
                    sorted(Counter(str(item["terminal_layer"]) for item in subset).items())
                ),
            }
        return result

    return {
        "required_document_recall_at_1": _rate_for_rank(ordered, "document_hit_rank", 1),
        "required_document_recall_at_3": _rate_for_rank(ordered, "document_hit_rank", 3),
        "required_document_recall_at_5": _rate_for_rank(ordered, "document_hit_rank", 5),
        "gold_table_source_recall_at_1": _table_rate(ordered, 1),
        "gold_table_source_recall_at_3": _table_rate(ordered, 3),
        "gold_table_source_recall_at_5": _table_rate(ordered, 5),
        "gold_table_source_recall_at_15": _table_rate(ordered, 15),
        "gold_table_source_recall_at_25": _table_rate(ordered, 25),
        "gold_member_coordinate_micro_coverage_at_5": _rate(
            covered_coordinates, total_coordinates
        ),
        "complete_member_coverage_case_count_at_5": sum(
            bool(item["complete_member_coverage_at_5"]) for item in ordered
        ),
        "binding_ready_case_count_at_5": terminal["BINDING_READY"],
        "terminal_layer_counts": dict(sorted(terminal.items())),
        "dataset_breakdown": breakdown("dataset"),
        "capability_breakdown": breakdown("candidate_capability"),
    }


def build_report(
    manifest_path: Path,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    manifest, manifest_sha256 = load_and_validate_manifest(
        manifest_path, repo_root=repo_root
    )
    with deny_network() as network_counter:
        corpus = adapt_corpus(manifest, repo_root=repo_root)
        records: list[dict[str, Any]] = []
        audits: list[dict[str, Any]] = []
        entries = sorted(
            (_as_mapping(item, "manifest entry") for item in manifest["entries"]),
            key=lambda item: (str(item["dataset"]), str(item["case_id"])),
        )
        for ordinal, entry in enumerate(entries, start=1):
            record, audit = evaluate_case(entry, ordinal=ordinal, corpus=corpus)
            records.append(record)
            audits.append(audit)

    aggregate = aggregate_records(records)
    report = {
        "schema_version": SCHEMA_VERSION,
        "case_count": len(records),
        "manifest_sha256": manifest_sha256,
        "source_taxonomy_sha256": EXPECTED_SOURCE_TAXONOMY_SHA256,
        "source_splits": {
            dataset: {
                "sha256": EXPECTED_SPLIT_SHA256[dataset],
                "repository_commit": EXPECTED_REPOSITORY_COMMITS[dataset],
            }
            for dataset in sorted(EXPECTED_SPLIT_SHA256)
        },
        "dataset_counts": dict(manifest["dataset_counts"]),
        "capability_counts": dict(manifest["capability_counts"]),
        "unique_document_count": len(corpus.documents),
        "adapted_source_object_count": corpus.source_object_count,
        "source_adapter_success_count": len(records),
        **aggregate,
        "per_case_records": records,
        "retriever_input_audit": audits,
        "provider_calls": 0,
        "legacy_calls": 0,
        "network_calls": int(network_counter["count"]),
        "total_tokens": 0,
        "measurement_valid": True,
        "scope_caveat": [
            "This is a question-level table-evidence retrieval baseline on frozen official FinQA/TAT-QA development cases.",
            "It is not an accuracy measurement for the local 190-document corpus or real user questions.",
            "A table or coordinate hit does not imply that the final answer is correct.",
            "BINDING_READY means evidence readiness only; it does not mean C3-N/C3-O is integrated or executed.",
            "The evaluator measures the existing retrievers and does not claim a product capability improvement.",
        ],
    }
    validate_report(report, enforce_frozen_counts=True)
    return report


def _validate_rate(value: Any, field: str) -> None:
    rate = _as_mapping(value, field)
    numerator = _strict_int(rate.get("numerator"), f"{field}.numerator")
    denominator = _strict_int(rate.get("denominator"), f"{field}.denominator")
    expected = numerator / denominator if denominator else 0.0
    if float(rate.get("value")) != expected:
        raise RetrievalBaselineValidationError(f"{field} rate value mismatch")


def validate_report(
    report: Mapping[str, Any],
    *,
    enforce_frozen_counts: bool = True,
) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise RetrievalBaselineValidationError("schema_version mismatch")
    records = _as_list(report.get("per_case_records"), "per_case_records")
    audits = _as_list(report.get("retriever_input_audit"), "retriever_input_audit")
    if len(records) != len(audits):
        raise RetrievalBaselineValidationError("record/audit count mismatch")
    if enforce_frozen_counts:
        if report.get("case_count") != EXPECTED_CASE_COUNT or len(records) != EXPECTED_CASE_COUNT:
            raise RetrievalBaselineValidationError("report case count mismatch")
        if report.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256:
            raise RetrievalBaselineValidationError("report manifest hash mismatch")
        if report.get("source_taxonomy_sha256") != EXPECTED_SOURCE_TAXONOMY_SHA256:
            raise RetrievalBaselineValidationError("report source taxonomy hash mismatch")
        if report.get("dataset_counts") != EXPECTED_DATASET_COUNTS:
            raise RetrievalBaselineValidationError("report dataset counts mismatch")
        if report.get("capability_counts") != EXPECTED_CAPABILITY_COUNTS:
            raise RetrievalBaselineValidationError("report capability counts mismatch")
        if report.get("unique_document_count") != EXPECTED_UNIQUE_DOCUMENT_COUNT:
            raise RetrievalBaselineValidationError("report document count mismatch")

    record_keys: set[tuple[str, str]] = set()
    audit_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_audit in audits:
        audit = _as_mapping(raw_audit, "retriever input audit")
        key = (str(audit.get("dataset") or ""), str(audit.get("case_id") or ""))
        if key in audit_by_key:
            raise RetrievalBaselineValidationError(f"duplicate input audit: {key}")
        audit_by_key[key] = audit
        if audit.get("gold_scope_injected") is not False or audit.get("violations"):
            raise RetrievalBaselineValidationError(f"gold scope injected: {key}")
        if audit.get("call_count") != 2:
            raise RetrievalBaselineValidationError(f"unexpected retriever call count: {key}")
        for call in _as_list(audit.get("calls"), "audit.calls"):
            call_map = _as_mapping(call, "audit call")
            if call_map.get("domain") != DOMAIN:
                raise RetrievalBaselineValidationError(f"retriever domain mismatch: {key}")
            if call_map.get("doc_ids") or call_map.get("candidate_doc_ids"):
                raise RetrievalBaselineValidationError(f"retriever scope not empty: {key}")
            if call_map.get("options") or call_map.get("raw"):
                raise RetrievalBaselineValidationError(f"retriever payload contaminated: {key}")
            if call_map.get("classification_labels") != [QuestionLabel.DEFAULT.value]:
                raise RetrievalBaselineValidationError(f"classification not generic: {key}")

    for raw_record in records:
        record = _as_mapping(raw_record, "per-case record")
        key = (str(record.get("dataset") or ""), str(record.get("case_id") or ""))
        if key in record_keys:
            raise RetrievalBaselineValidationError(f"duplicate per-case record: {key}")
        record_keys.add(key)
        if key not in audit_by_key:
            raise RetrievalBaselineValidationError(f"missing input audit: {key}")
        if not _HEX_64_RE.fullmatch(str(record.get("question_sha256") or "")):
            raise RetrievalBaselineValidationError(f"question hash invalid: {key}")
        gold_sources = _as_list(record.get("gold_source_object_ids"), "gold sources")
        gold_coordinates = _as_list(record.get("gold_coordinates"), "gold coordinates")
        if not gold_sources or not gold_coordinates:
            raise RetrievalBaselineValidationError(f"empty gold identity: {key}")
        if record.get("gold_coordinate_count") != len(gold_coordinates):
            raise RetrievalBaselineValidationError(f"gold coordinate count mismatch: {key}")
        covered = _as_list(
            record.get("covered_gold_coordinates_at_5"),
            "covered_gold_coordinates_at_5",
        )
        if len(covered) != len(set(covered)) or not set(covered) <= set(gold_coordinates):
            raise RetrievalBaselineValidationError(f"covered coordinates invalid: {key}")
        complete = len(covered) == len(gold_coordinates)
        if record.get("complete_member_coverage_at_5") is not complete:
            raise RetrievalBaselineValidationError(f"complete coverage flag mismatch: {key}")
        source_ranks = _as_mapping(record.get("gold_source_hit_ranks"), "source ranks")
        if set(source_ranks) != set(gold_sources):
            raise RetrievalBaselineValidationError(f"source rank identities mismatch: {key}")
        expected_terminal = classify_terminal_layer(
            adapter_ok=True,
            document_hit_rank=record.get("document_hit_rank"),
            gold_source_hit_ranks=source_ranks,
            covered_coordinate_count=len(covered),
            gold_coordinate_count=len(gold_coordinates),
        )
        if record.get("terminal_layer") != expected_terminal:
            raise RetrievalBaselineValidationError(f"terminal layer mismatch: {key}")
        for hit in _as_list(record.get("evidence_top25"), "evidence_top25"):
            hit_map = _as_mapping(hit, "evidence hit")
            if "text" in hit_map or "before_text" in hit_map or "after_text" in hit_map:
                raise RetrievalBaselineValidationError(f"evidence text copied into report: {key}")
            lineage = _as_mapping(hit_map.get("lineage"), "evidence lineage")
            if not lineage.get("source_path") and record.get("terminal_layer") == "BINDING_READY":
                raise RetrievalBaselineValidationError(
                    f"binding-ready record has missing lineage: {key}"
                )

    expected_aggregate = aggregate_records(records)
    for field, expected in expected_aggregate.items():
        if report.get(field) != expected:
            raise RetrievalBaselineValidationError(f"aggregate mismatch: {field}")
    for field in RATE_FIELDS:
        _validate_rate(report.get(field), field)
    if sum(int(value) for value in report["terminal_layer_counts"].values()) != len(records):
        raise RetrievalBaselineValidationError("terminal layer denominator mismatch")
    if any(layer not in TERMINAL_LAYERS for layer in report["terminal_layer_counts"]):
        raise RetrievalBaselineValidationError("unknown terminal layer")
    if report.get("source_adapter_success_count") != len(records):
        raise RetrievalBaselineValidationError("source adapter success count mismatch")
    for field in ("provider_calls", "legacy_calls", "network_calls", "total_tokens"):
        if report.get(field) != 0:
            raise RetrievalBaselineValidationError(f"{field} must be zero")
    if report.get("measurement_valid") is not True:
        raise RetrievalBaselineValidationError("measurement_valid must be true")
    caveat = report.get("scope_caveat")
    if not isinstance(caveat, list) or len(caveat) < 5:
        raise RetrievalBaselineValidationError("scope caveat incomplete")


__all__ = [
    "AdaptedCorpus",
    "DOMAIN",
    "EXPECTED_MANIFEST_SHA256",
    "RetrievalBaselineValidationError",
    "SCHEMA_VERSION",
    "TERMINAL_LAYERS",
    "adapt_corpus",
    "aggregate_records",
    "build_question",
    "build_report",
    "canonical_json_bytes",
    "classify_terminal_layer",
    "evaluate_case",
    "load_and_validate_manifest",
    "sha256_file",
    "validate_report",
]
