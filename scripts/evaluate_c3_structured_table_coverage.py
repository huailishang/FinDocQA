"""Measure structured-table evidence coverage over a frozen MinerU manifest.

This evaluator is intentionally read-only with respect to product code and corpus
files.  It calls the existing ``load_structured_table_rows_with_audit`` loader
for every manifest document, preserves the loader audit, validates row source
identity independently, and emits a deterministic machine report.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evidence.structured_tables import (  # noqa: E402
    StructuredTableRow,
    load_structured_table_rows_with_audit,
)


SCHEMA_VERSION = "c3-structured-table-coverage-baseline/v1"
EXPECTED_MANIFEST_SHA256 = (
    "b4189049478125eae4ec4c3fc2406e16de791f44e35778578ce297a463a1678a"
)
EXPECTED_DOCUMENT_COUNT = 190
EXPECTED_DOMAIN_COUNTS = {
    "financial_contracts": 14,
    "financial_reports": 10,
    "insurance": 16,
    "regulatory": 130,
    "research": 20,
}
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT
    / "evaluation_artifacts"
    / "c3_structured_table_coverage_baseline_v1"
    / "corpus_manifest.json"
)
TERMINAL_STATES = (
    "CONTENT_LIST_READ_ERROR",
    "NO_TABLE_ELEMENTS",
    "TABLES_SEEN_NONE_LOADABLE",
    "TABLES_LOADED_NO_ROWS",
    "ROWS_LOADED_IDENTITY_INCOMPLETE",
    "ROWS_LOADED_IDENTITY_COMPLETE",
)
IDENTITY_FIELDS = (
    "domain",
    "doc_id",
    "page_idx",
    "table_index",
    "row_index",
    "canonical_source",
    "mineru_json_source",
    "table_source_object_id",
    "table_data_row_count",
    "row_span_start",
    "row_span_end_exclusive",
    "row_span_complete",
    "table_row_indices",
    "table_row_sources",
    "table_range_digest",
)
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


class CoverageValidationError(ValueError):
    """Raised when the frozen corpus or generated report is inconsistent."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoverageValidationError(f"{field} must be an integer")
    return value


def _as_sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CoverageValidationError(f"{field} must be a sequence")
    return value


def load_and_validate_manifest(
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], str]:
    manifest_path = Path(manifest_path)
    raw_bytes = manifest_path.read_bytes()
    manifest_sha256 = _sha256_bytes(raw_bytes)
    if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise CoverageValidationError(
            "manifest sha256 mismatch: "
            f"expected {EXPECTED_MANIFEST_SHA256}, got {manifest_sha256}"
        )

    payload = json.loads(raw_bytes.decode("utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise CoverageValidationError("manifest entries must be a list")
    if payload.get("document_count") != EXPECTED_DOCUMENT_COUNT:
        raise CoverageValidationError(
            f"manifest document_count must be {EXPECTED_DOCUMENT_COUNT}"
        )
    if len(entries) != EXPECTED_DOCUMENT_COUNT:
        raise CoverageValidationError(
            f"manifest entry count must be {EXPECTED_DOCUMENT_COUNT}"
        )

    path_keys: set[str] = set()
    identity_keys: set[tuple[str, str]] = set()
    domain_counts: Counter[str] = Counter()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise CoverageValidationError(f"manifest entry {index} is not an object")
        domain = str(entry.get("domain") or "")
        doc_id = str(entry.get("doc_id") or "")
        manifest_path_value = str(entry.get("path") or "")
        expected_sha256 = str(entry.get("sha256") or "")
        if not domain or not doc_id or not manifest_path_value:
            raise CoverageValidationError(f"manifest entry {index} has empty identity")
        if not _HEX_64_RE.fullmatch(expected_sha256):
            raise CoverageValidationError(
                f"manifest entry {index} has invalid file sha256"
            )
        if manifest_path_value in path_keys:
            raise CoverageValidationError(
                f"duplicate manifest path: {manifest_path_value}"
            )
        identity_key = (domain, doc_id)
        if identity_key in identity_keys:
            raise CoverageValidationError(
                f"duplicate manifest domain/doc_id: {domain}/{doc_id}"
            )
        path_keys.add(manifest_path_value)
        identity_keys.add(identity_key)
        domain_counts[domain] += 1

        file_path = repo_root / manifest_path_value
        if not file_path.is_file():
            raise CoverageValidationError(
                f"manifest file missing: {manifest_path_value}"
            )
        actual_sha256 = _sha256_file(file_path)
        if actual_sha256 != expected_sha256:
            raise CoverageValidationError(
                "manifest file sha256 mismatch: "
                f"{manifest_path_value}: expected {expected_sha256}, got {actual_sha256}"
            )

    if dict(sorted(domain_counts.items())) != EXPECTED_DOMAIN_COUNTS:
        raise CoverageValidationError(
            "manifest domain counts mismatch: "
            f"expected {EXPECTED_DOMAIN_COUNTS}, got {dict(sorted(domain_counts.items()))}"
        )
    return dict(payload), manifest_sha256


def _identity_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    projection = {field: row.get(field) for field in IDENTITY_FIELDS}
    projection["headers"] = list(row.get("headers") or [])
    projection["cell_texts"] = list(row.get("cell_texts") or [])
    return projection


def _row_to_dict(row: StructuredTableRow | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, StructuredTableRow):
        return _identity_projection(row.to_dict())
    return _identity_projection(row)


def compact_identity_tables(
    rows: Sequence[StructuredTableRow | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Store table-invariant identity once instead of once per loaded row."""
    projected = [_row_to_dict(row) for row in rows]
    groups: dict[tuple[str, str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in projected:
        key = (
            str(row.get("domain") or ""),
            str(row.get("doc_id") or ""),
            int(row.get("page_idx") or 0),
            int(row.get("table_index") or 0),
        )
        groups[key].append(row)

    compact: list[dict[str, Any]] = []
    common_fields = (
        "domain",
        "doc_id",
        "page_idx",
        "table_index",
        "mineru_json_source",
        "table_source_object_id",
        "table_data_row_count",
        "row_span_start",
        "row_span_end_exclusive",
        "row_span_complete",
        "table_row_indices",
        "table_row_sources",
        "table_range_digest",
        "headers",
    )
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda item: int(item["row_index"]))
        first = ordered[0]
        table = {field: first.get(field) for field in common_fields}
        table["table_row_indices"] = list(first.get("table_row_indices") or [])
        table["table_row_sources"] = list(first.get("table_row_sources") or [])
        table["headers"] = list(first.get("headers") or [])
        table["rows"] = [
            {
                "row_index": row.get("row_index"),
                "canonical_source": row.get("canonical_source"),
                "cell_texts": list(row.get("cell_texts") or []),
            }
            for row in ordered
        ]
        compact.append(table)
    return compact


def expand_identity_tables(
    tables: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    common_fields = (
        "domain",
        "doc_id",
        "page_idx",
        "table_index",
        "mineru_json_source",
        "table_source_object_id",
        "table_data_row_count",
        "row_span_start",
        "row_span_end_exclusive",
        "row_span_complete",
        "table_row_indices",
        "table_row_sources",
        "table_range_digest",
        "headers",
    )
    for table_position, table in enumerate(tables):
        if not isinstance(table, Mapping):
            raise CoverageValidationError(
                f"identity table {table_position} is not an object"
            )
        table_rows = table.get("rows")
        if not isinstance(table_rows, list):
            raise CoverageValidationError(
                f"identity table {table_position} rows must be a list"
            )
        common = {field: table.get(field) for field in common_fields}
        for row_position, row in enumerate(table_rows):
            if not isinstance(row, Mapping):
                raise CoverageValidationError(
                    f"identity table {table_position} row {row_position} is not an object"
                )
            rows.append(
                {
                    **common,
                    "row_index": row.get("row_index"),
                    "canonical_source": row.get("canonical_source"),
                    "cell_texts": list(row.get("cell_texts") or []),
                }
            )
    return rows


def _expected_range_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    ordered = sorted(rows, key=lambda item: int(item["row_index"]))
    first = ordered[0]
    payload = {
        "proof_version": "structured-table-range/v1",
        "doc_id": str(first["doc_id"]),
        "page_idx": int(first["page_idx"]),
        "table_index": int(first["table_index"]),
        "headers": list(first.get("headers") or []),
        "rows": [
            {
                "row_index": int(row["row_index"]),
                "canonical_source": str(row["canonical_source"]),
                "cell_texts": list(row.get("cell_texts") or []),
            }
            for row in ordered
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


def row_identity_errors(
    rows: Sequence[StructuredTableRow | Mapping[str, Any]],
) -> list[str]:
    """Return independent source/range identity errors for loaded rows."""
    projected = [_row_to_dict(row) for row in rows]
    errors: list[str] = []
    groups: dict[tuple[str, str, int, int], list[dict[str, Any]]] = defaultdict(list)

    for position, row in enumerate(projected):
        for field in IDENTITY_FIELDS:
            value = row.get(field)
            if value is None or value == "" or value == []:
                errors.append(f"row[{position}].{field}:missing")
        try:
            domain = str(row["domain"])
            doc_id = str(row["doc_id"])
            page_idx = _as_int(row["page_idx"], "page_idx")
            table_index = _as_int(row["table_index"], "table_index")
            row_index = _as_int(row["row_index"], "row_index")
        except (KeyError, CoverageValidationError) as exc:
            errors.append(f"row[{position}].identity:{exc}")
            continue
        if page_idx < 0 or table_index < 0 or row_index < 0 or not domain or not doc_id:
            errors.append(f"row[{position}].identity:invalid_coordinate")
            continue

        mineru_source = str(row.get("mineru_json_source") or "")
        canonical_source = str(row.get("canonical_source") or "")
        expected_canonical = (
            f"{mineru_source}#page_idx={page_idx}"
            f"&table_index={table_index}&row_index={row_index}"
        )
        if canonical_source != expected_canonical:
            errors.append(f"row[{position}].canonical_source:mismatch")
        expected_table_source = (
            f"{mineru_source}#page_idx={page_idx}&table_index={table_index}"
        )
        if str(row.get("table_source_object_id") or "") != expected_table_source:
            errors.append(f"row[{position}].table_source_object_id:mismatch")
        if row.get("row_span_complete") is not True:
            errors.append(f"row[{position}].row_span_complete:not_true")
        digest = str(row.get("table_range_digest") or "")
        if not _HEX_64_RE.fullmatch(digest):
            errors.append(f"row[{position}].table_range_digest:invalid")
        groups[(domain, doc_id, page_idx, table_index)].append(row)

    for group_key, group_rows in sorted(groups.items()):
        ordered = sorted(group_rows, key=lambda item: int(item["row_index"]))
        indices = [int(row["row_index"]) for row in ordered]
        first = ordered[0]
        try:
            span_start = _as_int(first.get("row_span_start"), "row_span_start")
            span_end = _as_int(
                first.get("row_span_end_exclusive"), "row_span_end_exclusive"
            )
            data_row_count = _as_int(
                first.get("table_data_row_count"), "table_data_row_count"
            )
            declared_indices = [
                _as_int(value, "table_row_indices")
                for value in _as_sequence(
                    first.get("table_row_indices"), "table_row_indices"
                )
            ]
            declared_sources = [
                str(value)
                for value in _as_sequence(
                    first.get("table_row_sources"), "table_row_sources"
                )
            ]
        except CoverageValidationError as exc:
            errors.append(f"table{group_key}:{exc}")
            continue

        expected_indices = list(range(span_start, span_end))
        expected_sources = [str(row["canonical_source"]) for row in ordered]
        expected_digest = _expected_range_digest(ordered)
        if indices != expected_indices:
            errors.append(f"table{group_key}.row_span:does_not_cover_rows")
        if declared_indices != indices:
            errors.append(f"table{group_key}.table_row_indices:mismatch")
        if declared_sources != expected_sources:
            errors.append(f"table{group_key}.table_row_sources:mismatch")
        if data_row_count != len(ordered):
            errors.append(f"table{group_key}.table_data_row_count:mismatch")
        if str(first.get("table_range_digest") or "") != expected_digest:
            errors.append(f"table{group_key}.table_range_digest:mismatch")

        invariant_fields = (
            "mineru_json_source",
            "table_source_object_id",
            "table_data_row_count",
            "row_span_start",
            "row_span_end_exclusive",
            "row_span_complete",
            "table_row_indices",
            "table_row_sources",
            "table_range_digest",
            "headers",
        )
        for row in ordered[1:]:
            for field in invariant_fields:
                if row.get(field) != first.get(field):
                    errors.append(f"table{group_key}.{field}:not_invariant")
    return sorted(set(errors))


def classify_terminal_state(
    audit: Mapping[str, Any],
    rows: Sequence[StructuredTableRow | Mapping[str, Any]],
) -> tuple[str, bool, list[str]]:
    read_error = str(audit.get("read_error") or "")
    tables_seen = int(audit.get("tables_seen") or 0)
    tables_loaded = int(audit.get("tables_loaded") or 0)
    rows_loaded = len(rows)
    if read_error:
        return "CONTENT_LIST_READ_ERROR", False, []
    if tables_seen == 0:
        return "NO_TABLE_ELEMENTS", False, []
    if tables_loaded == 0:
        return "TABLES_SEEN_NONE_LOADABLE", False, []
    if rows_loaded == 0:
        return "TABLES_LOADED_NO_ROWS", False, []
    errors = row_identity_errors(rows)
    if errors:
        return "ROWS_LOADED_IDENTITY_INCOMPLETE", False, errors
    return "ROWS_LOADED_IDENTITY_COMPLETE", True, []


def _normalise_audit(audit: Mapping[str, Any], rows_loaded: int) -> dict[str, Any]:
    unsupported = audit.get("unsupported_table_layouts") or []
    table_layouts = audit.get("table_layouts") or []
    return {
        "source_found": bool(audit.get("source_found")),
        "source": str(audit.get("source") or ""),
        "read_error": str(audit.get("read_error") or ""),
        "tables_seen": int(audit.get("tables_seen") or 0),
        "tables_loaded": int(audit.get("tables_loaded") or 0),
        "rows_loaded": int(audit.get("rows_loaded") or rows_loaded),
        "unsupported_table_layouts": list(unsupported),
        "table_layouts": list(table_layouts),
    }


def _aggregate_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    terminal_counts: Counter[str] = Counter()
    unsupported_issue_counts: Counter[str] = Counter()
    read_error_counts: Counter[str] = Counter()
    domain_records: dict[str, list[Mapping[str, Any]]] = defaultdict(list)

    documents_with_table_elements = 0
    documents_with_loaded_tables = 0
    documents_with_loaded_rows = 0
    documents_with_complete_row_identity = 0
    total_tables_seen = 0
    total_tables_loaded = 0
    total_rows_loaded = 0
    unsupported_layout_count = 0

    for record in records:
        terminal_counts[str(record["terminal_state"])] += 1
        domain_records[str(record["domain"])].append(record)
        audit = record["audit"]
        tables_seen = int(audit.get("tables_seen") or 0)
        tables_loaded = int(audit.get("tables_loaded") or 0)
        rows_loaded = int(record.get("rows_loaded") or 0)
        total_tables_seen += tables_seen
        total_tables_loaded += tables_loaded
        total_rows_loaded += rows_loaded
        documents_with_table_elements += int(tables_seen > 0)
        documents_with_loaded_tables += int(tables_loaded > 0)
        documents_with_loaded_rows += int(rows_loaded > 0)
        documents_with_complete_row_identity += int(
            record.get("identity_complete") is True
        )
        unsupported = audit.get("unsupported_table_layouts") or []
        unsupported_layout_count += len(unsupported)
        for issue_record in unsupported:
            for issue in issue_record.get("issues") or []:
                unsupported_issue_counts[str(issue)] += 1
        read_error = str(audit.get("read_error") or "")
        if read_error:
            read_error_counts[read_error] += 1

    domain_breakdown: dict[str, Any] = {}
    for domain in sorted(domain_records):
        items = domain_records[domain]
        domain_breakdown[domain] = {
            "document_count": len(items),
            "terminal_state_counts": dict(
                sorted(Counter(str(item["terminal_state"]) for item in items).items())
            ),
            "documents_with_table_elements": sum(
                int(int(item["audit"].get("tables_seen") or 0) > 0)
                for item in items
            ),
            "documents_with_loaded_tables": sum(
                int(int(item["audit"].get("tables_loaded") or 0) > 0)
                for item in items
            ),
            "documents_with_loaded_rows": sum(
                int(int(item.get("rows_loaded") or 0) > 0) for item in items
            ),
            "documents_with_complete_row_identity": sum(
                int(item.get("identity_complete") is True) for item in items
            ),
            "total_tables_seen": sum(
                int(item["audit"].get("tables_seen") or 0) for item in items
            ),
            "total_tables_loaded": sum(
                int(item["audit"].get("tables_loaded") or 0) for item in items
            ),
            "total_rows_loaded": sum(
                int(item.get("rows_loaded") or 0) for item in items
            ),
            "unsupported_layout_count": sum(
                len(item["audit"].get("unsupported_table_layouts") or [])
                for item in items
            ),
        }

    top_failure_issues = [
        {"issue": issue, "count": count}
        for issue, count in sorted(
            unsupported_issue_counts.items(), key=lambda item: (-item[1], item[0])
        )[:5]
    ]
    return {
        "terminal_state_counts": dict(sorted(terminal_counts.items())),
        "documents_with_table_elements": documents_with_table_elements,
        "documents_with_loaded_tables": documents_with_loaded_tables,
        "documents_with_loaded_rows": documents_with_loaded_rows,
        "documents_with_complete_row_identity": (
            documents_with_complete_row_identity
        ),
        "total_tables_seen": total_tables_seen,
        "total_tables_loaded": total_tables_loaded,
        "total_rows_loaded": total_rows_loaded,
        "unsupported_layout_count": unsupported_layout_count,
        "unsupported_issue_counts": dict(sorted(unsupported_issue_counts.items())),
        "read_error_counts": dict(sorted(read_error_counts.items())),
        "domain_breakdown": domain_breakdown,
        "top_failure_issues": top_failure_issues,
    }


def validate_report(
    report: Mapping[str, Any],
    *,
    enforce_frozen_corpus: bool = True,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise CoverageValidationError("unexpected report schema_version")
    if report.get("measurement_valid") is not True:
        raise CoverageValidationError("measurement_valid must be true")
    records = report.get("per_document_records")
    if not isinstance(records, list):
        raise CoverageValidationError("per_document_records must be a list")
    if report.get("document_count") != len(records):
        raise CoverageValidationError("document_count does not match records")

    expected_entries: list[Mapping[str, Any]] | None = None
    if enforce_frozen_corpus:
        manifest, manifest_sha256 = load_and_validate_manifest(
            manifest_path, repo_root=repo_root
        )
        if report.get("manifest_sha256") != manifest_sha256:
            raise CoverageValidationError("report manifest_sha256 mismatch")
        expected_entries = list(manifest["entries"])
        if len(records) != len(expected_entries):
            raise CoverageValidationError("report does not cover the frozen denominator")

    path_keys: set[str] = set()
    identity_keys: set[tuple[str, str]] = set()
    domain_counts: Counter[str] = Counter()
    for position, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise CoverageValidationError(f"record {position} is not an object")
        domain = str(record.get("domain") or "")
        doc_id = str(record.get("doc_id") or "")
        record_manifest_path = str(record.get("manifest_path") or "")
        manifest_file_sha256 = str(record.get("manifest_file_sha256") or "")
        if not domain or not doc_id or not record_manifest_path:
            raise CoverageValidationError(f"record {position} has empty identity")
        if not _HEX_64_RE.fullmatch(manifest_file_sha256):
            raise CoverageValidationError(
                f"record {position} has invalid manifest_file_sha256"
            )
        if record_manifest_path in path_keys:
            raise CoverageValidationError(
                f"duplicate record path: {record_manifest_path}"
            )
        if (domain, doc_id) in identity_keys:
            raise CoverageValidationError(
                f"duplicate record identity: {domain}/{doc_id}"
            )
        path_keys.add(record_manifest_path)
        identity_keys.add((domain, doc_id))
        domain_counts[domain] += 1

        if expected_entries is not None:
            expected = expected_entries[position]
            expected_identity = (
                str(expected["domain"]),
                str(expected["doc_id"]),
                str(expected["path"]),
                str(expected["sha256"]),
            )
            observed_identity = (
                domain,
                doc_id,
                record_manifest_path,
                manifest_file_sha256,
            )
            if observed_identity != expected_identity:
                raise CoverageValidationError(
                    f"record {position} does not match frozen manifest entry"
                )

        audit = record.get("audit")
        identity_tables = record.get("identity_tables")
        if not isinstance(audit, Mapping) or not isinstance(identity_tables, list):
            raise CoverageValidationError(
                f"record {position} lacks raw audit/identity tables"
            )
        rows = expand_identity_tables(identity_tables)
        audit_source = str(audit.get("source") or "")
        if audit_source and audit_source != record_manifest_path:
            raise CoverageValidationError(f"record {position} audit source mismatch")
        if int(audit.get("rows_loaded") or 0) != len(rows):
            raise CoverageValidationError(f"record {position} audit rows_loaded mismatch")
        for row_position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise CoverageValidationError(
                    f"record {position} row {row_position} is not an object"
                )
            if str(row.get("domain") or "") != domain:
                raise CoverageValidationError(
                    f"record {position} row {row_position} domain mismatch"
                )
            if str(row.get("doc_id") or "") != doc_id:
                raise CoverageValidationError(
                    f"record {position} row {row_position} doc_id mismatch"
                )
            if str(row.get("mineru_json_source") or "") != record_manifest_path:
                raise CoverageValidationError(
                    f"record {position} row {row_position} source mismatch"
                )

        derived_state, identity_complete, identity_errors = classify_terminal_state(
            audit, rows
        )
        if record.get("terminal_state") != derived_state:
            raise CoverageValidationError(
                f"record {position} terminal_state mismatch: "
                f"expected {derived_state}, got {record.get('terminal_state')}"
            )
        if record.get("identity_complete") is not identity_complete:
            raise CoverageValidationError(
                f"record {position} identity_complete mismatch"
            )
        if list(record.get("identity_errors") or []) != identity_errors:
            raise CoverageValidationError(
                f"record {position} identity_errors mismatch"
            )
        if int(record.get("tables_seen") or 0) != int(audit.get("tables_seen") or 0):
            raise CoverageValidationError(f"record {position} tables_seen mismatch")
        if int(record.get("tables_loaded") or 0) != int(
            audit.get("tables_loaded") or 0
        ):
            raise CoverageValidationError(f"record {position} tables_loaded mismatch")
        if int(record.get("rows_loaded") or 0) != len(rows):
            raise CoverageValidationError(f"record {position} rows_loaded mismatch")
        if int(record.get("unsupported_table_layouts") or 0) != len(
            audit.get("unsupported_table_layouts") or []
        ):
            raise CoverageValidationError(
                f"record {position} unsupported layout count mismatch"
            )
        if str(record.get("read_error") or "") != str(
            audit.get("read_error") or ""
        ):
            raise CoverageValidationError(f"record {position} read_error mismatch")

    derived = _aggregate_records(records)
    for field, value in derived.items():
        if report.get(field) != value:
            raise CoverageValidationError(f"aggregate mismatch: {field}")
    if sum(report["terminal_state_counts"].values()) != len(records):
        raise CoverageValidationError("terminal_state_counts are not complete")
    if set(report["terminal_state_counts"]) - set(TERMINAL_STATES):
        raise CoverageValidationError("unknown terminal state in report")
    observed_domain_counts = dict(sorted(domain_counts.items()))
    if observed_domain_counts != report.get("domain_counts"):
        raise CoverageValidationError("domain_counts mismatch")
    if enforce_frozen_corpus and observed_domain_counts != EXPECTED_DOMAIN_COUNTS:
        raise CoverageValidationError("frozen domain_counts mismatch")
    for field in (
        "provider_calls",
        "legacy_calls",
        "network_calls",
        "total_tokens",
    ):
        if report.get(field) != 0:
            raise CoverageValidationError(f"{field} must be zero")
    scope_caveat = report.get("scope_caveat")
    if not isinstance(scope_caveat, list) or len(scope_caveat) < 4:
        raise CoverageValidationError("scope_caveat must preserve all four boundaries")


def build_report(
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    structured_root: Path | None = None,
    loader: Callable[
        [Path, str, str],
        tuple[Sequence[StructuredTableRow], Mapping[str, Any]],
    ] = load_structured_table_rows_with_audit,
) -> dict[str, Any]:
    manifest, manifest_sha256 = load_and_validate_manifest(
        manifest_path, repo_root=repo_root
    )
    structured_root = structured_root or repo_root / "data" / "processed_mineru"
    records: list[dict[str, Any]] = []

    for entry in manifest["entries"]:
        domain = str(entry["domain"])
        doc_id = str(entry["doc_id"])
        rows_raw, audit_raw = loader(Path(structured_root), domain, doc_id)
        rows = [_row_to_dict(row) for row in rows_raw]
        audit = _normalise_audit(audit_raw, len(rows))
        state, identity_complete, identity_errors = classify_terminal_state(
            audit, rows
        )
        identity_tables = compact_identity_tables(rows)
        records.append(
            {
                "domain": domain,
                "doc_id": doc_id,
                "manifest_path": str(entry["path"]),
                "manifest_file_sha256": str(entry["sha256"]),
                "terminal_state": state,
                "tables_seen": audit["tables_seen"],
                "tables_loaded": audit["tables_loaded"],
                "rows_loaded": len(rows),
                "unsupported_table_layouts": len(
                    audit["unsupported_table_layouts"]
                ),
                "read_error": audit["read_error"],
                "identity_complete": identity_complete,
                "identity_errors": identity_errors,
                "audit": audit,
                "identity_tables": identity_tables,
            }
        )

    aggregate = _aggregate_records(records)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "document_count": len(records),
        "manifest_sha256": manifest_sha256,
        "domain_counts": dict(
            sorted(Counter(record["domain"] for record in records).items())
        ),
        **aggregate,
        "per_document_records": records,
        "provider_calls": 0,
        "legacy_calls": 0,
        "network_calls": 0,
        "total_tokens": 0,
        "measurement_valid": True,
        "scope_caveat": [
            "This is document-level structured evidence supply, not question-level retrieval recall.",
            "Loaded table rows do not guarantee that a natural-language question can bind to them.",
            "This measurement does not mean C3-N or C3-O is connected to the normal pipeline.",
            "This measurement is not overall FinDocQA answer accuracy.",
        ],
    }
    validate_report(
        report,
        repo_root=repo_root,
        manifest_path=manifest_path,
    )
    return report


def write_report(report: Mapping[str, Any], output_path: Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_bytes(report)
    output_path.write_bytes(payload)
    return _sha256_bytes(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / "evaluation_artifacts"
        / "c3_structured_table_coverage_baseline_v1"
        / "report.json",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing report instead of scanning the corpus.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.validate_only:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        validate_report(report)
        print(f"VALID report={args.output}")
        print(f"sha256={_sha256_file(args.output)}")
        return 0

    report = build_report(args.manifest)
    report_sha256 = write_report(report, args.output)
    print(f"VALID document_count={report['document_count']}")
    print(f"manifest_sha256={report['manifest_sha256']}")
    print(f"terminal_state_counts={json.dumps(report['terminal_state_counts'], ensure_ascii=False, sort_keys=True)}")
    print(f"documents_with_table_elements={report['documents_with_table_elements']}")
    print(f"documents_with_loaded_tables={report['documents_with_loaded_tables']}")
    print(f"documents_with_loaded_rows={report['documents_with_loaded_rows']}")
    print(
        "documents_with_complete_row_identity="
        f"{report['documents_with_complete_row_identity']}"
    )
    print(f"total_tables_seen={report['total_tables_seen']}")
    print(f"total_tables_loaded={report['total_tables_loaded']}")
    print(f"total_rows_loaded={report['total_rows_loaded']}")
    print(f"unsupported_layout_count={report['unsupported_layout_count']}")
    print(f"top_failure_issues={json.dumps(report['top_failure_issues'], ensure_ascii=False, sort_keys=True)}")
    print("provider_calls=0 legacy_calls=0 network_calls=0 total_tokens=0")
    print(f"report={args.output}")
    print(f"report_sha256={report_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
