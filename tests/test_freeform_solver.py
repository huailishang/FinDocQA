from __future__ import annotations

import json

import pytest

from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question, QuestionLabel
from solvers.base import normalize_answer
from solvers.calculation import CalculationSolver
from solvers.freeform import parse_freeform_submission_answers
from utils.llm_client import ChatResult, ChatUsage


class FakeClient:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.call_count = 0

    def chat(self, messages, max_tokens: int = 256) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=self.content,
            model="fake-freeform",
            finish_reason=self.finish_reason,
            usage=ChatUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30, latency_ms=1.0),
            provider="fake",
        )


def _bundle(slot_count: int = 1, question_text: str = "计算结果，保留两位小数。") -> EvidenceBundle:
    question = Question(
        qid="freeform_test",
        domain="financial_reports",
        text=question_text,
        options={},
        answer_format="freeform",
        doc_ids=(),
        submission_slot_count=slot_count,
    )
    candidate = EvidenceCandidate(
        domain="financial_reports",
        doc_id="doc1",
        source="doc1#page=1",
        text="x=10，y=2。",
    )
    return EvidenceBundle(
        question=question,
        classification=ClassificationResult(labels=[QuestionLabel.CALCULATION]),
        candidates=[candidate],
        prompt_context=candidate.text,
        estimated_tokens=20,
    )


def _payload(values: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "qid": "freeform_test",
            "answers": [
                {
                    "value": value,
                    "kind": kind,
                    "formula_text": "x / y" if kind in {"number", "percentage"} else "",
                    "variables": {"x": 10, "y": 2} if kind in {"number", "percentage"} else {},
                    "computed_result": 5 if kind in {"number", "percentage"} else None,
                    "percentage_result_semantics": "display_percentage_points" if kind == "percentage" else None,
                    "evidence_refs": ["doc1"],
                }
                for value, kind in values
            ],
            "used_doc_ids": ["doc1"],
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )


def test_normalize_answer_does_not_turn_freeform_into_a() -> None:
    assert normalize_answer("12.34%", "freeform") == "12.34%"


def test_parse_freeform_percentage_preserves_value_not_a() -> None:
    parsed = parse_freeform_submission_answers(
        _payload([("12.34%", "percentage")]),
        expected_slots=1,
        question_text="答案保留两位小数。",
    )

    assert parsed.valid is True
    assert parsed.answers == ("12.34%",)


def test_parse_freeform_slot_length_mismatch_is_blocked() -> None:
    parsed = parse_freeform_submission_answers(
        _payload([("1", "number")]),
        expected_slots=2,
        question_text="分别填写两个结果。",
    )

    assert parsed.valid is False
    assert parsed.reason == "submission_slot_count_mismatch"


def test_parse_freeform_empty_slot_is_blocked() -> None:
    parsed = parse_freeform_submission_answers(
        _payload([("", "number")]),
        expected_slots=1,
        question_text="填写结果。",
    )

    assert parsed.valid is False
    assert parsed.reason == "empty_submission_slot"


def test_parse_freeform_collapsed_multislot_string_is_blocked() -> None:
    raw = json.dumps(
        {"qid": "freeform_test", "answers": "1;2", "used_doc_ids": ["doc1"]},
        ensure_ascii=False,
    )

    parsed = parse_freeform_submission_answers(raw, expected_slots=2, question_text="两个结果")

    assert parsed.valid is False
    assert parsed.reason == "answers_must_be_array"


@pytest.mark.parametrize(
    ("raw_value", "kind", "question_text", "expected"),
    [
        ("5", "number", "保留两位小数", "5.00"),
        ("5%", "percentage", "保留两位小数", "5.00%"),
        ("2026-07-01", "date", "填写日期", "2026年7月1日"),
        ("公司A > 公司B", "ordering", "按顺序填写", "公司A>公司B"),
    ],
)
def test_freeform_safe_formatting(raw_value, kind, question_text, expected) -> None:
    parsed = parse_freeform_submission_answers(
        _payload([(raw_value, kind)]),
        expected_slots=1,
        question_text=question_text,
    )

    assert parsed.valid is True
    assert parsed.answers == (expected,)


def test_calculation_solver_uses_structured_freeform_contract() -> None:
    client = FakeClient(_payload([("5", "number"), ("5%", "percentage")]))

    result = CalculationSolver(llm_client=client).solve(_bundle(slot_count=2))

    assert client.call_count == 1
    assert result.answer == "5.00"
    assert result.metadata["submission_answers"] == ["5.00", "5.00%"]
    assert result.metadata["freeform_parse_valid"] is True
    assert result.metadata["expected_submission_slots"] == 2
    assert result.metadata["freeform_binding_auditable"] is True


def test_calculation_solver_blocks_length_mismatch_without_a_fallback() -> None:
    client = FakeClient(_payload([("5", "number")]))

    result = CalculationSolver(llm_client=client).solve(_bundle(slot_count=2))

    assert result.answer == ""
    assert result.metadata["freeform_parse_valid"] is False
    assert result.metadata["freeform_parse_reason"] == "submission_slot_count_mismatch"
    assert "A" not in result.metadata.get("submission_answers", [])
