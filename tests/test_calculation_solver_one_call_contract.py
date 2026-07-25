from __future__ import annotations

import json

from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question, QuestionLabel
from solvers.calculation import CalculationSolver
from utils.llm_client import ChatResult, ChatUsage


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.call_count = 0

    def chat(self, messages, max_tokens: int = 256) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=self.content,
            model="fake-one-call",
            finish_reason="stop",
            usage=ChatUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, latency_ms=1.0),
            provider="fake",
        )


def _bundle() -> EvidenceBundle:
    question = Question(
        qid="calc_one_call",
        domain="financial_reports",
        text="a为100，b为200，a+b等于多少？",
        options={"A": "200", "B": "300", "C": "400", "D": "500"},
        answer_format="mcq",
        doc_ids=["doc1"],
    )
    candidate = EvidenceCandidate(
        domain="financial_reports",
        doc_id="doc1",
        source="doc1#page=1",
        text="a=100，b=200。",
    )
    return EvidenceBundle(
        question=question,
        classification=ClassificationResult([QuestionLabel.CALCULATION]),
        candidates=[candidate],
        prompt_context=candidate.text,
        estimated_tokens=100,
    )


def test_one_call_contract_uses_exactly_one_provider_call(monkeypatch):
    payload = {
        "qid": "calc_one_call",
        "answer": "B",
        "formula_text": "a + b",
        "variables": {"a": 100, "b": 200},
        "computed_result": 300,
        "option_evaluations": [
            {"option": "A", "verdict": "contradicted", "evidence_refs": ["doc1"], "calculation_refs": ["computed_result"]},
            {"option": "B", "verdict": "supported", "evidence_refs": ["doc1"], "calculation_refs": ["computed_result"]},
            {"option": "C", "verdict": "contradicted", "evidence_refs": ["doc1"], "calculation_refs": ["computed_result"]},
            {"option": "D", "verdict": "contradicted", "evidence_refs": ["doc1"], "calculation_refs": ["computed_result"]},
        ],
        "used_doc_ids": ["doc1"],
        "confidence": 1.0,
    }
    client = FakeClient(json.dumps(payload, ensure_ascii=False))
    monkeypatch.setenv("SAFE_RUN_CALCULATION_CONTRACT", "one-call")
    result = CalculationSolver(llm_client=client).solve(_bundle())
    assert client.call_count == 1
    assert result.answer == "B"
    assert result.metadata["calculation_contract"] == "one-call"
    assert result.metadata["computation_complete"] is True
    assert result.metadata["computed_result"] == 300.0
    assert result.metadata["provider_call_stages_expected"] == ["calculation_one_call"]


def test_one_call_contract_keeps_malformed_payload_ungrounded(monkeypatch):
    client = FakeClient("答案可能是B，但没有按JSON输出")
    monkeypatch.setenv("SAFE_RUN_CALCULATION_CONTRACT", "one-call")
    result = CalculationSolver(llm_client=client).solve(_bundle())
    assert client.call_count == 1
    assert result.metadata["one_call_payload_parsed"] is False
    assert result.metadata["computation_complete"] is False
    assert result.metadata["ungrounded"] is True
    assert result.confidence <= 0.25


def test_two_call_path_remains_default_when_contract_not_enabled(monkeypatch):
    first = {
        "qid": "calc_one_call",
        "answer": "B",
        "formula_text": "a+b",
        "variables": {"a": 100, "b": 200},
        "computed_result": 300,
        "option_evaluations": [],
        "used_doc_ids": ["doc1"],
        "confidence": 1.0,
    }
    client = FakeClient(json.dumps(first))
    monkeypatch.delenv("SAFE_RUN_CALCULATION_CONTRACT", raising=False)
    # The legacy path asks for a second response. FakeClient permits repeated
    # responses, so the assertion proves the routing distinction.
    CalculationSolver(llm_client=client).solve(_bundle())
    assert client.call_count == 2
