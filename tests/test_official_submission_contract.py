from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.factory import PipelineFactory
from answer_contract import normalize_answer_format
from solvers.freeform import parse_freeform_submission_answers
from submission_contract import validate_result_ledger_tokens, validate_token_triplet
from utils.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def _payload(value: str, kind: str, *, semantics: str | None = None) -> str:
    item = {
        "value": value,
        "kind": kind,
        "formula_text": "x / y",
        "variables": {"x": 1, "y": 2},
        "computed_result": 0.5,
        "evidence_refs": ["doc1"],
    }
    if semantics is not None:
        item["percentage_result_semantics"] = semantics
    return json.dumps(
        {
            "qid": "q",
            "answers": [item],
            "used_doc_ids": ["doc1"],
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )


def _contract(kind: str, *, scale: int | None = 2, semantics: str | None = None):
    return ({
        "qid": "q",
        "slot_index": 1,
        "expected_kind": kind,
        "expected_decimal_places": scale,
        "percent_suffix_required": kind == "percentage",
        "unit_must_be_omitted": True,
        "percentage_result_semantics": semantics,
        "ordering_delimiter": ">" if kind == "ordering" else None,
        "date_format": "YYYY年M月D日" if kind == "date" else None,
        "allowed_input_unit_suffixes": [],
        "contract_source": "test",
    },)




def test_extraction_type_is_an_official_freeform_alias() -> None:
    assert normalize_answer_format("抽取题") == "freeform"
    assert normalize_answer_format("extraction") == "freeform"


@pytest.mark.parametrize(
    ("expected_kind", "spoof_kind", "value"),
    [
        ("percentage", "text", "50.00%"),
        ("date", "text", "2026年7月1日"),
        ("ordering", "text", "甲>乙"),
        ("number", "text", "10.00"),
        ("percentage_point", "number", "5.00"),
    ],
)
def test_model_kind_spoof_is_blocked(expected_kind, spoof_kind, value) -> None:
    parsed = parse_freeform_submission_answers(
        _payload(value, spoof_kind),
        expected_slots=1,
        question_text="填写答案。",
        expected_slot_contracts=_contract(expected_kind),
    )

    assert parsed.valid is False
    assert parsed.reason.startswith("freeform_model_kind_mismatch")
    assert parsed.slot_validations[0]["model_kind_matches_expected"] is False


def test_b_number_default_two_decimals_is_authoritative() -> None:
    parsed = parse_freeform_submission_answers(
        _payload("5", "number"),
        expected_slots=1,
        question_text="答案只填写数字。",
        expected_slot_contracts=_contract("number", scale=2),
    )

    assert parsed.valid is True
    assert parsed.answers == ("5.00",)


def test_percentage_and_percentage_point_are_distinct() -> None:
    percentage = parse_freeform_submission_answers(
        _payload("5%", "percentage", semantics="display_percentage_points"),
        expected_slots=1,
        question_text="填写百分数。",
        expected_slot_contracts=_contract(
            "percentage", scale=2, semantics="display_percentage_points"
        ),
    )
    point = parse_freeform_submission_answers(
        _payload("5", "percentage_point"),
        expected_slots=1,
        question_text="填写提高的百分点，不带单位。",
        expected_slot_contracts=_contract(
            "percentage_point", scale=2, semantics="display_percentage_points"
        ),
    )

    assert percentage.valid is True
    assert percentage.answers == ("5.00%",)
    assert point.valid is True
    assert point.answers == ("5.00",)


@pytest.mark.parametrize(
    ("prompt", "completion", "total"),
    [
        (True, 2, 3),
        (1.0, 2, 3),
        ("1", 2, 3),
        (-1, 2, 1),
        (1, 2, 4),
    ],
)
def test_token_triplet_rejects_non_integer_negative_or_bad_equation(
    prompt, completion, total
) -> None:
    with pytest.raises(ValueError):
        validate_token_triplet(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )


def test_result_and_provider_ledger_tokens_must_match() -> None:
    with pytest.raises(ValueError, match="ledger/result token mismatch"):
        validate_result_ledger_tokens(
            qid="q",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            metadata={
                "provider_ledger_token_totals": {
                    "prompt_tokens": 10,
                    "completion_tokens": 6,
                    "total_tokens": 16,
                }
            },
        )
