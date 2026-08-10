from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from evaluation.external_benchmarks.financebench_adapter import (
    FINANCEBENCH_LICENSE_ID,
    FORBIDDEN_RUNTIME_KEYS,
    RESEARCH_ONLY_USE_SCOPE,
    FinanceBenchCase,
    load_financebench_cases,
)
from question.adapter import CanonicalQuestionAdapter


def _synthetic_row(*, case_id: str = "fb-synthetic-001") -> dict[str, Any]:
    return {
        "financebench_id": case_id,
        "question": "What is the synthetic reporting metric?",
        "answer": "Synthetic gold answer",
        "dataset_subset_label": "SYNTHETIC_TEST_ONLY",
        "evidence": [
            {
                "doc_name": "SYNTHETIC_REPORT_2025",
                "evidence_page_num": 0,
                "evidence_text": "Synthetic evidence excerpt.",
                "evidence_text_full_page": "Synthetic full-page evidence text.",
            }
        ],
        "justification": "Synthetic evaluation-only justification.",
        "question_type": "synthetic-type",
        "question_reasoning": "synthetic",
        "domain_question_num": "synthetic-1",
        "company": "Synthetic Company",
        "doc_name": "SYNTHETIC_REPORT_2025",
    }


def _write_jsonl(path: Path, rows: list[Any]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _load_one(tmp_path: Path, row: dict[str, Any] | None = None) -> FinanceBenchCase:
    source = _write_jsonl(tmp_path / "financebench.synthetic.jsonl", [row or _synthetic_row()])
    cases = load_financebench_cases(source)
    assert len(cases) == 1
    return cases[0]


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert FORBIDDEN_RUNTIME_KEYS.isdisjoint(value)
        for nested in value.values():
            _assert_no_forbidden_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_forbidden_keys(nested)


def test_valid_synthetic_row_loads_typed_case(tmp_path: Path) -> None:
    case = _load_one(tmp_path)

    assert isinstance(case, FinanceBenchCase)
    assert case.case_id == "fb-synthetic-001"
    assert case.question_type == "synthetic-type"
    assert case.company == "Synthetic Company"
    assert case.use_scope == RESEARCH_ONLY_USE_SCOPE
    assert case.license_id == FINANCEBENCH_LICENSE_ID


def test_runtime_payload_is_canonical_question_compatible(tmp_path: Path) -> None:
    case = _load_one(tmp_path)

    question = CanonicalQuestionAdapter().adapt(case.runtime_question_payload)

    assert question.qid == case.case_id
    assert question.domain == "financial_reports"
    assert question.text == case.question
    assert question.answer_format == "freeform"
    assert question.candidate_doc_ids == (case.document.doc_name,)


def test_runtime_payload_and_canonical_raw_have_zero_gold_leakage(tmp_path: Path) -> None:
    case = _load_one(tmp_path)
    question = CanonicalQuestionAdapter().adapt(case.runtime_question_payload)

    _assert_no_forbidden_keys(dict(case.runtime_question_payload))
    _assert_no_forbidden_keys(dict(question.raw))


def test_gold_answer_justification_and_evidence_are_retained_only_in_label(tmp_path: Path) -> None:
    row = _synthetic_row()
    case = _load_one(tmp_path, row)

    assert case.gold_label.answer == row["answer"]
    assert case.gold_label.justification == row["justification"]
    assert len(case.gold_label.evidence) == 1
    assert case.gold_label.evidence[0].evidence_text == row["evidence"][0]["evidence_text"]
    assert "answer" not in case.runtime_question_payload
    assert "justification" not in case.runtime_question_payload
    assert "evidence" not in case.runtime_question_payload


def test_evidence_page_number_remains_zero_indexed(tmp_path: Path) -> None:
    row = _synthetic_row()
    row["evidence"][0]["evidence_page_num"] = 0

    case = _load_one(tmp_path, row)

    assert case.gold_label.evidence[0].page_num == 0


def test_duplicate_qid_fails_closed(tmp_path: Path) -> None:
    row = _synthetic_row()
    source = _write_jsonl(tmp_path / "duplicate.jsonl", [row, deepcopy(row)])

    with pytest.raises(ValueError, match="duplicate FinanceBench financebench_id"):
        load_financebench_cases(source)


def test_missing_required_field_fails_closed(tmp_path: Path) -> None:
    row = _synthetic_row()
    del row["answer"]
    source = _write_jsonl(tmp_path / "missing.jsonl", [row])

    with pytest.raises(ValueError, match="missing required field: answer"):
        load_financebench_cases(source)


def test_invalid_evidence_schema_fails_closed(tmp_path: Path) -> None:
    row = _synthetic_row()
    row["evidence"][0]["evidence_page_num"] = "0"
    source = _write_jsonl(tmp_path / "bad-evidence.jsonl", [row])

    with pytest.raises(ValueError, match="evidence_page_num must be a non-negative integer"):
        load_financebench_cases(source)


def test_non_research_use_scope_fails_closed(tmp_path: Path) -> None:
    source = _write_jsonl(tmp_path / "scope.jsonl", [_synthetic_row()])

    with pytest.raises(ValueError, match="restricted to RESEARCH_ONLY_NONCOMMERCIAL"):
        load_financebench_cases(source, use_scope="COMMERCIAL")


def test_document_ref_uses_expected_pdf_relative_path(tmp_path: Path) -> None:
    case = _load_one(tmp_path)

    assert case.document.doc_name == "SYNTHETIC_REPORT_2025"
    assert case.document.pdf_relative_path == "pdfs/SYNTHETIC_REPORT_2025.pdf"


def test_non_object_jsonl_row_fails_closed(tmp_path: Path) -> None:
    source = _write_jsonl(tmp_path / "non-object.jsonl", [["not", "an", "object"]])

    with pytest.raises(ValueError, match="must be an object"):
        load_financebench_cases(source)


def test_null_justification_is_preserved_as_gold_only(tmp_path: Path) -> None:
    row = _synthetic_row()
    row["justification"] = None

    case = _load_one(tmp_path, row)

    assert case.gold_label.justification is None
    assert "justification" not in case.runtime_question_payload
