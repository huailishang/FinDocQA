from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation.multislot_recovery import recover_duplicated_multislot_answers


def good_audit() -> dict[str, Any]:
    return {
        "provider_status": "COMPLETED",
        "result_reusable": True,
        "reasoning_contract_pass": True,
        "reasoning_self_contained_pass": True,
        "lineage_pass": True,
        "semantic_binding_pass": True,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def simple_validator(expected: Sequence[str]):
    def validate(values: Sequence[str]):
        values = tuple(str(v) for v in values)
        valid = values == tuple(expected)
        rows = [{"slot": i + 1, "valid": valid} for i in range(len(values))]
        return valid, rows, list(values)

    return validate


def contracts(n: int) -> list[dict[str, Any]]:
    return [{"slot_index": i + 1, "expected_kind": "text"} for i in range(n)]


def test_raw_answers_not_identical_and_invalid_fails():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A", "B"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A B", source_audit=good_audit(),
        slot_validator=simple_validator(["X", "Y"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "visible_answers_not_identical"


def test_split_count_mismatch_fails():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A；B；C", "A；B；C"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A B C", source_audit=good_audit(),
        slot_validator=simple_validator(["A", "B"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "no_allowed_delimiter_yields_exact_slots"


def test_invalid_slot_kind_fails_authoritative_validation():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A；bad", "A；bad"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A bad", source_audit=good_audit(),
        slot_validator=simple_validator(["A", "19.75"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "authoritative_slot_validation_failed"


def test_reasoning_missing_one_recovered_slot_fails():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A；19.75", "A；19.75"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A only", source_audit=good_audit(),
        slot_validator=simple_validator(["A", "19.75"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "reasoning_does_not_cover_all_recovered_slots"


def test_recovery_requiring_numeric_rewrite_fails():
    def normalizing_validator(values: Sequence[str]):
        rows = [{"slot": 1, "valid": True}, {"slot": 2, "valid": True}]
        return True, rows, [str(values[0]), "19.75"]

    result = recover_duplicated_multislot_answers(
        visible_answers=["A；19.7", "A；19.7"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A 19.7", source_audit=good_audit(),
        slot_validator=normalizing_validator,
    )
    assert result.status == "FAIL"
    assert result.reason == "slot_validation_requires_value_change"


def test_single_slot_fails():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A"], expected_slots=1, slot_contracts=contracts(1), allowed_delimiters=["；"],
        reasoning_summary="A", source_audit=good_audit(), slot_validator=simple_validator(["A"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "single_slot_not_recoverable"


def test_ordinary_valid_multislot_is_no_recovery():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A", "19.75"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A 19.75", source_audit=good_audit(),
        slot_validator=simple_validator(["A", "19.75"]),
    )
    assert result.status == "NO_RECOVERY"
    assert result.reason == "ordinary_multislot_already_valid"


def test_no_legal_delimiter_fails():
    result = recover_duplicated_multislot_answers(
        visible_answers=["A|19.75", "A|19.75"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；", ";"], reasoning_summary="A 19.75", source_audit=good_audit(),
        slot_validator=simple_validator(["A", "19.75"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "no_allowed_delimiter_yields_exact_slots"


def test_source_audit_must_be_reusable():
    audit = good_audit()
    audit["lineage_pass"] = False
    result = recover_duplicated_multislot_answers(
        visible_answers=["A；19.75", "A；19.75"], expected_slots=2, slot_contracts=contracts(2),
        allowed_delimiters=["；"], reasoning_summary="A 19.75", source_audit=audit,
        slot_validator=simple_validator(["A", "19.75"]),
    )
    assert result.status == "FAIL"
    assert result.reason == "source_audit_not_reusable"
