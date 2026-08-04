from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "evaluate_c3_structured_table_coverage.py"
SPEC = importlib.util.spec_from_file_location("c3_structured_table_coverage", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)

MANIFEST_PATH = (
    REPO_ROOT
    / "evaluation_artifacts"
    / "c3_structured_table_coverage_baseline_v1"
    / "corpus_manifest.json"
)


def _complete_rows() -> list[dict[str, object]]:
    source = "data/processed_mineru/research/doc/auto/doc_content_list_v2.json"
    rows: list[dict[str, object]] = []
    for row_index, cells in enumerate((("A", "10"), ("B", "20"))):
        canonical = (
            f"{source}#page_idx=3&table_index=1&row_index={row_index}"
        )
        rows.append(
            {
                "domain": "research",
                "doc_id": "doc",
                "page_idx": 3,
                "table_index": 1,
                "row_index": row_index,
                "canonical_source": canonical,
                "mineru_json_source": source,
                "table_source_object_id": (
                    f"{source}#page_idx=3&table_index=1"
                ),
                "table_data_row_count": 2,
                "row_span_start": 0,
                "row_span_end_exclusive": 2,
                "row_span_complete": True,
                "table_row_indices": [0, 1],
                "table_row_sources": [],
                "table_range_digest": "0" * 64,
                "headers": ["项目", "金额"],
                "cell_texts": list(cells),
            }
        )
    sources = [str(row["canonical_source"]) for row in rows]
    for row in rows:
        row["table_row_sources"] = sources
    digest = coverage._expected_range_digest(rows)
    for row in rows:
        row["table_range_digest"] = digest
    return rows


def _record(
    *,
    state: str,
    audit: dict[str, object],
    rows: list[dict[str, object]],
    identity_complete: bool,
    identity_errors: list[str] | None = None,
) -> dict[str, object]:
    return {
        "domain": "research",
        "doc_id": "doc",
        "manifest_path": "data/processed_mineru/research/doc/auto/doc_content_list_v2.json",
        "manifest_file_sha256": "a" * 64,
        "terminal_state": state,
        "tables_seen": int(audit.get("tables_seen") or 0),
        "tables_loaded": int(audit.get("tables_loaded") or 0),
        "rows_loaded": len(rows),
        "unsupported_table_layouts": len(
            audit.get("unsupported_table_layouts") or []
        ),
        "read_error": str(audit.get("read_error") or ""),
        "identity_complete": identity_complete,
        "identity_errors": identity_errors or [],
        "audit": audit,
        "identity_tables": coverage.compact_identity_tables(rows),
    }


def _report_for_record(record: dict[str, object]) -> dict[str, object]:
    aggregate = coverage._aggregate_records([record])
    return {
        "schema_version": coverage.SCHEMA_VERSION,
        "document_count": 1,
        "manifest_sha256": coverage.EXPECTED_MANIFEST_SHA256,
        "domain_counts": {"research": 1},
        **aggregate,
        "per_document_records": [record],
        "provider_calls": 0,
        "legacy_calls": 0,
        "network_calls": 0,
        "total_tokens": 0,
        "measurement_valid": True,
        "scope_caveat": ["a", "b", "c", "d"],
    }


def test_frozen_manifest_is_complete_and_hash_stable() -> None:
    manifest, digest = coverage.load_and_validate_manifest(MANIFEST_PATH)
    assert digest == coverage.EXPECTED_MANIFEST_SHA256
    assert manifest["document_count"] == 190
    assert len(manifest["entries"]) == 190


def test_build_report_calls_loader_for_all_190_manifest_documents() -> None:
    calls: list[tuple[str, str]] = []

    def fake_loader(
        _root: Path, domain: str, doc_id: str
    ) -> tuple[tuple[object, ...], dict[str, object]]:
        calls.append((domain, doc_id))
        return (), {
            "source_found": True,
            "tables_seen": 0,
            "tables_loaded": 0,
            "rows_loaded": 0,
            "unsupported_table_layouts": [],
            "table_layouts": [],
        }

    report = coverage.build_report(MANIFEST_PATH, loader=fake_loader)
    assert len(calls) == 190
    assert len(set(calls)) == 190
    assert report["document_count"] == 190
    assert report["terminal_state_counts"] == {"NO_TABLE_ELEMENTS": 190}
    assert report["provider_calls"] == 0
    assert report["network_calls"] == 0


@pytest.mark.parametrize(
    ("audit", "rows", "expected"),
    [
        (
            {
                "read_error": "JSONDecodeError",
                "tables_seen": 0,
                "tables_loaded": 0,
            },
            [],
            "CONTENT_LIST_READ_ERROR",
        ),
        (
            {"tables_seen": 0, "tables_loaded": 0},
            [],
            "NO_TABLE_ELEMENTS",
        ),
        (
            {"tables_seen": 2, "tables_loaded": 0},
            [],
            "TABLES_SEEN_NONE_LOADABLE",
        ),
        (
            {"tables_seen": 2, "tables_loaded": 1},
            [],
            "TABLES_LOADED_NO_ROWS",
        ),
    ],
)
def test_terminal_state_precedence(
    audit: dict[str, object], rows: list[dict[str, object]], expected: str
) -> None:
    state, identity_complete, errors = coverage.classify_terminal_state(audit, rows)
    assert state == expected
    assert identity_complete is False
    assert errors == []


def test_complete_identity_is_accepted() -> None:
    state, identity_complete, errors = coverage.classify_terminal_state(
        {"tables_seen": 1, "tables_loaded": 1}, _complete_rows()
    )
    assert state == "ROWS_LOADED_IDENTITY_COMPLETE"
    assert identity_complete is True
    assert errors == []


@pytest.mark.parametrize(
    ("field", "value", "expected_fragment"),
    [
        ("canonical_source", "", "canonical_source"),
        ("table_source_object_id", "tampered", "table_source_object_id"),
        ("table_range_digest", "", "table_range_digest"),
    ],
)
def test_identity_tampering_is_fail_closed(
    field: str, value: object, expected_fragment: str
) -> None:
    rows = _complete_rows()
    rows[0][field] = value
    state, identity_complete, errors = coverage.classify_terminal_state(
        {"tables_seen": 1, "tables_loaded": 1}, rows
    )
    assert state == "ROWS_LOADED_IDENTITY_INCOMPLETE"
    assert identity_complete is False
    assert any(expected_fragment in error for error in errors)


def test_validate_report_rejects_terminal_state_tampering() -> None:
    rows = _complete_rows()
    audit = {
        "source_found": True,
        "source": str(rows[0]["mineru_json_source"]),
        "read_error": "",
        "tables_seen": 1,
        "tables_loaded": 1,
        "rows_loaded": 2,
        "unsupported_table_layouts": [],
        "table_layouts": [],
    }
    report = _report_for_record(
        _record(
            state="ROWS_LOADED_IDENTITY_COMPLETE",
            audit=audit,
            rows=rows,
            identity_complete=True,
        )
    )
    coverage.validate_report(report, enforce_frozen_corpus=False)

    tampered = deepcopy(report)
    tampered["per_document_records"][0]["terminal_state"] = "NO_TABLE_ELEMENTS"
    with pytest.raises(coverage.CoverageValidationError, match="terminal_state"):
        coverage.validate_report(tampered, enforce_frozen_corpus=False)


def test_validate_report_rejects_aggregate_tampering() -> None:
    audit = {
        "source_found": True,
        "source": "data/processed_mineru/research/doc/auto/doc_content_list_v2.json",
        "read_error": "",
        "tables_seen": 0,
        "tables_loaded": 0,
        "rows_loaded": 0,
        "unsupported_table_layouts": [],
        "table_layouts": [],
    }
    report = _report_for_record(
        _record(
            state="NO_TABLE_ELEMENTS",
            audit=audit,
            rows=[],
            identity_complete=False,
        )
    )
    coverage.validate_report(report, enforce_frozen_corpus=False)

    tampered = deepcopy(report)
    tampered["documents_with_table_elements"] = 1
    with pytest.raises(coverage.CoverageValidationError, match="aggregate"):
        coverage.validate_report(tampered, enforce_frozen_corpus=False)


def test_canonical_report_bytes_are_deterministic() -> None:
    payload = {"b": [2, 1], "a": {"中文": True}}
    assert coverage._canonical_json_bytes(payload) == coverage._canonical_json_bytes(
        deepcopy(payload)
    )
