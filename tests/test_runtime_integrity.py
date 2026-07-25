from __future__ import annotations

import pytest

from verification.runtime_integrity import (
    authoritative_records,
    merge_checkpoint_records,
    validate_runtime_record,
)


def record(**overrides):
    base = {
        "qid": "q1", "answer_source": "generated", "fallback_used": False,
        "finish_reason": "stop", "truncation_risk": False,
        "ungrounded": False, "error": None, "authoritative": True,
        "completed": True, "attempt_id": "a1",
    }
    base.update(overrides)
    return base


def test_api_error_cannot_masquerade_as_completed_answer():
    issues = validate_runtime_record(record(error="timeout"))
    assert "error_masquerades_as_normal_answer" in issues
    assert "failed_record_counted_completed" in issues


def test_fallback_and_truncation_must_be_visible():
    assert "fallback_not_visible_in_answer_source" in validate_runtime_record(record(fallback_used=True))
    assert "length_finish_without_truncation_risk" in validate_runtime_record(record(finish_reason="length"))


def test_safe_fallback_record_is_explicit():
    assert validate_runtime_record(record(answer_source="fallback", fallback_used=True, degraded=True)) == []


def test_error_source_must_be_ungrounded():
    assert "unsafe_answer_source_not_marked_ungrounded" in validate_runtime_record(
        record(answer_source="error", error="api unavailable", completed=False)
    )


def test_one_qid_has_one_authoritative_result():
    with pytest.raises(ValueError, match="authoritative_count=2"):
        authoritative_records([record(attempt_id="a1"), record(attempt_id="a2")])


def test_checkpoint_preserves_completed_row_without_explicit_lineage():
    old = record(attempt_id="a1")
    with pytest.raises(ValueError, match="rerun_missing_replacement_lineage"):
        merge_checkpoint_records([old], [record(attempt_id="a2")])


def test_traced_rerun_replaces_once_and_remains_authoritative():
    old = record(attempt_id="a1")
    new = record(attempt_id="a2", replaces_attempt_id="a1")
    merged = merge_checkpoint_records([old], [new])
    assert len(merged) == 1
    assert merged[0]["attempt_id"] == "a2"
