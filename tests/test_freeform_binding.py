from __future__ import annotations

import json
from typing import Any

import pytest

from agent.classifier import RuleBasedQuestionClassifier
from agent.workflow import BlockingAnswerValidationError, EnhancedBaselineWorkflow
from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question, QuestionLabel
from evidence.assembler import GroupedEvidenceAssembler
from solvers.calculation import CalculationSolver
from solvers.freeform import parse_freeform_submission_answers
from utils.llm_client import ChatResult, ChatUsage


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.call_count = 0

    def chat(self, messages, max_tokens: int = 256) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=self.content,
            model="fake-r1-r1",
            finish_reason="stop",
            usage=ChatUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            provider="fake",
        )


class FixtureRetriever:
    def retrieve(self, question, classification):
        return [
            EvidenceCandidate(
                domain=question.domain,
                doc_id="doc1",
                source="doc1#page=1",
                text="x=1，y=3。",
            )
        ]


def _item(
    value: str,
    kind: str,
    *,
    formula: str = "",
    variables: dict[str, Any] | None = None,
    computed: Any = None,
    percentage_semantics: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "value": value,
        "kind": kind,
        "formula_text": formula,
        "variables": variables or {},
        "computed_result": computed,
        "evidence_refs": ["doc1"],
    }
    if percentage_semantics is not None:
        item["percentage_result_semantics"] = percentage_semantics
    return item


def _payload(items: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "qid": "binding_test",
            "answers": items,
            "used_doc_ids": ["doc1"],
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )


def _bundle(question_text: str, slot_count: int = 1) -> EvidenceBundle:
    question = Question(
        qid="binding_test",
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
        text="x=1，y=3。",
    )
    return EvidenceBundle(
        question=question,
        classification=ClassificationResult(labels=[QuestionLabel.CALCULATION]),
        candidates=[candidate],
        prompt_context=candidate.text,
        estimated_tokens=20,
    )


def _workflow(client: FakeClient) -> EnhancedBaselineWorkflow:
    return EnhancedBaselineWorkflow(
        classifier=RuleBasedQuestionClassifier(),
        retriever=FixtureRetriever(),
        assembler=GroupedEvidenceAssembler(),
        solver=CalculationSolver(llm_client=client),
        verifier=None,
        self_check_verifier=None,
        fallback_solver=None,
        enforce_production_integrity=True,
    )


@pytest.mark.parametrize(
    ("value", "kind", "expected_reason"),
    [
        ("不是百分数", "percentage", "invalid_freeform_percentage"),
        ("明天", "date", "invalid_freeform_date"),
        ("2026年2月30日", "date", "invalid_freeform_date"),
        ("2026年2-28", "date", "invalid_freeform_date"),
        ("2026.2.28", "date", "invalid_freeform_date"),
        ("甲公司乙公司", "ordering", "invalid_freeform_ordering"),
        ("A>>B", "ordering", "invalid_freeform_ordering"),
        ("12%", "number", "invalid_freeform_number"),
        ("NaN", "number", "invalid_freeform_number"),
        ("Infinity", "number", "invalid_freeform_number"),
    ],
)
def test_kind_specific_invalid_values_fail_closed(value, kind, expected_reason) -> None:
    parsed = parse_freeform_submission_answers(
        _payload([_item(value, kind)]),
        expected_slots=1,
        question_text="填写答案。",
    )

    assert parsed.valid is False
    assert parsed.reason == expected_reason
    assert parsed.slot_validations[0]["valid"] is False
    assert parsed.slot_validations[0]["reason"] == expected_reason


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        ("1,234.50", "number", "1234.50"),
        ("12.5％", "percentage", "12.5%"),
        ("2026/2/28", "date", "2026年2月28日"),
        ("甲公司 ＞ 乙公司", "ordering", "甲公司>乙公司"),
    ],
)
def test_kind_specific_valid_values_are_normalized(value, kind, expected) -> None:
    parsed = parse_freeform_submission_answers(
        _payload([_item(value, kind)]),
        expected_slots=1,
        question_text="填写答案。",
    )

    assert parsed.valid is True
    assert parsed.answers == (expected,)
    assert parsed.slot_validations[0]["valid"] is True


def test_submission_python_mismatch_is_blocked() -> None:
    client = FakeClient(
        _payload([_item("99.99", "number", formula="x + 1", variables={"x": 1}, computed=2)])
    )

    result = CalculationSolver(llm_client=client).solve(_bundle("结果保留两位小数。"))

    binding = result.metadata["freeform_slot_bindings"][0]
    assert binding["python_deterministic_numeric_value"] == "2.0"
    assert binding["submitted_vs_python_match"] is False
    assert "freeform_submission_result_mismatch" in binding["blocking_reasons"]
    assert result.metadata["freeform_all_slot_results_match"] is False

    with pytest.raises(BlockingAnswerValidationError, match="freeform_submission_result_mismatch"):
        _workflow(client).process_one(_bundle("结果保留两位小数。").question)


def test_model_python_mismatch_is_blocked() -> None:
    client = FakeClient(
        _payload([_item("2.00", "number", formula="x + 1", variables={"x": 1}, computed=99)])
    )

    result = CalculationSolver(llm_client=client).solve(_bundle("结果保留两位小数。"))

    binding = result.metadata["freeform_slot_bindings"][0]
    assert binding["submitted_vs_python_match"] is True
    assert binding["model_vs_python_match"] is False
    assert "freeform_model_python_result_mismatch" in binding["blocking_reasons"]


def test_rounding_match_passes() -> None:
    client = FakeClient(
        _payload([_item("0.33", "number", formula="x / y", variables={"x": 1, "y": 3}, computed=1 / 3)])
    )

    result = _workflow(client).process_one(_bundle("结果保留两位小数。").question)

    assert result.answer == "0.33"
    binding = result.metadata["freeform_slot_bindings"][0]
    assert binding["rounded_expected_value"] == "0.33"
    assert binding["submitted_vs_python_match"] is True
    assert binding["model_vs_python_match"] is True
    assert result.metadata["freeform_all_slot_bindings_valid"] is True


def test_rounding_mismatch_is_blocked() -> None:
    client = FakeClient(
        _payload([_item("0.34", "number", formula="x / y", variables={"x": 1, "y": 3}, computed=1 / 3)])
    )

    with pytest.raises(BlockingAnswerValidationError, match="freeform_submission_result_mismatch"):
        _workflow(client).process_one(_bundle("结果保留两位小数。").question)


def test_without_explicit_scale_coarse_rounding_is_blocked() -> None:
    client = FakeClient(
        _payload([_item("0.3", "number", formula="x / y", variables={"x": 1, "y": 3}, computed=1 / 3)])
    )

    with pytest.raises(BlockingAnswerValidationError, match="freeform_submission_result_mismatch"):
        _workflow(client).process_one(_bundle("填写计算结果。").question)


def test_percentage_ratio_semantics_are_explicit_and_bound() -> None:
    client = FakeClient(
        _payload([
            _item(
                "33.33%",
                "percentage",
                formula="x / y",
                variables={"x": 1, "y": 3},
                computed=1 / 3,
                percentage_semantics="ratio",
            )
        ])
    )

    result = _workflow(client).process_one(_bundle("结果保留两位小数。").question)

    binding = result.metadata["freeform_slot_bindings"][0]
    assert binding["percentage_result_semantics"] == "ratio"
    assert binding["rounded_expected_value"] == "33.33%"
    assert binding["submitted_vs_python_match"] is True


def test_percentage_without_semantics_fails_closed() -> None:
    client = FakeClient(
        _payload([
            _item("5.00%", "percentage", formula="x + 4", variables={"x": 1}, computed=5)
        ])
    )

    result = CalculationSolver(llm_client=client).solve(_bundle("结果保留两位小数。"))

    binding = result.metadata["freeform_slot_bindings"][0]
    assert "freeform_percentage_semantics_ambiguous" in binding["blocking_reasons"]
    assert binding["binding_valid"] is False


def test_multislot_second_slot_mismatch_blocks_but_retains_all_audits() -> None:
    items = [
        _item("2.00", "number", formula="a + 1", variables={"a": 1}, computed=2),
        _item("99.00", "number", formula="b + 1", variables={"b": 2}, computed=3),
        _item("4.00", "number", formula="c + 1", variables={"c": 3}, computed=4),
    ]
    client = FakeClient(_payload(items))

    result = CalculationSolver(llm_client=client).solve(_bundle("三个结果均保留两位小数。", slot_count=3))

    bindings = result.metadata["freeform_slot_bindings"]
    assert len(bindings) == 3
    assert bindings[0]["binding_valid"] is True
    assert bindings[1]["binding_valid"] is False
    assert bindings[2]["binding_valid"] is True
    assert "freeform_submission_result_mismatch" in bindings[1]["blocking_reasons"]
    assert result.metadata["freeform_all_slot_bindings_valid"] is False

    with pytest.raises(BlockingAnswerValidationError, match="freeform_submission_result_mismatch"):
        _workflow(client).process_one(_bundle("三个结果均保留两位小数。", slot_count=3).question)
