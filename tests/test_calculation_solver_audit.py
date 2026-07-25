"""P7B-0 calculation solver audit — offline flow tests with a fake LLM client.

These tests expose the structural failures identified in the P7A gold-set
report (case_001, case_004, case_013):

1. formula extraction failure must be visible in metadata;
2. Python evaluation success must populate computed metadata;
3. answer matching must not silently succeed when there is no formula or
   computed result;
4. multi-formula/ranking style questions must expose that all required
   formulas were or were not evaluated;
5. existing known-good calculation examples must continue to pass.

All tests run fully offline: a ``FakeLLMClient`` returns canned
``ChatResult`` payloads, so no API key, network, or model is required.
"""

from __future__ import annotations

from typing import List, Sequence

from contracts import (
    ClassificationResult,
    EvidenceBundle,
    Question,
    QuestionLabel,
)
from solvers.calculation import CalculationSolver
from utils.llm_client import ChatResult, ChatUsage


# ── test doubles ────────────────────────────────────────────────────────


class FakeLLMClient:
    """Offline fake returning canned responses in call order.

    ``chat_with_fallback`` calls ``client.chat(messages, max_tokens=...)``,
    so this duck-types the real ``OpenAICompatibleClient`` without a network.
    """

    def __init__(self, responses: Sequence[ChatResult]) -> None:
        self._responses: List[ChatResult] = list(responses)
        self._idx = 0
        self.call_count = 0

    def chat(self, messages, max_tokens: int = 256) -> ChatResult:
        self.call_count += 1
        if self._idx >= len(self._responses):
            resp = self._responses[-1]
        else:
            resp = self._responses[self._idx]
            self._idx += 1
        return resp


def _chat(content: str) -> ChatResult:
    return ChatResult(
        content=content,
        model="fake-model",
        finish_reason="stop",
        usage=ChatUsage(),
    )


def _make_bundle(qid: str = "calc_test", answer_format: str = "mcq") -> EvidenceBundle:
    question = Question(
        qid=qid,
        domain="test",
        text="下列哪项计算结果正确？",
        options={"A": "10000", "B": "20000", "C": "30000", "D": "40000"},
        answer_format=answer_format,
        doc_ids=["doc1"],
    )
    return EvidenceBundle(
        question=question,
        classification=ClassificationResult(labels=[QuestionLabel.CALCULATION]),
        candidates=[],
        prompt_context="证据：保费 100，收益 200。",
        estimated_tokens=100,
    )


# ── 1. formula extraction failure visible in metadata ──────────────────


def test_formula_extraction_failure_visible_in_metadata():
    """case_001 / case_004 pattern: LLM returns prose, no formula line.

    The solver must surface that no formula was extracted and no computation
    happened via explicit metadata flags.
    """
    extract_response = (
        "本题被定义为多项选择题，需要分析各选项的合理性。\n"
        "选项A看起来比较合理，因为保单条款中提到...\n"
        "没有提取到公式。"
    )
    fake = FakeLLMClient([_chat(extract_response), _chat("A")])
    solver = CalculationSolver(llm_client=fake)

    result = solver.solve(_make_bundle(qid="case_001_like"))
    meta = result.metadata

    assert meta["formula_extracted"] is False
    assert meta["computation_performed"] is False
    assert meta["computation_grounded"] is False
    assert meta["extracted_formula"] is None
    assert meta["calc_error"] == "no formula extracted"


# ── 2. Python evaluation success populates computed metadata ────────────


def test_python_evaluation_success_populates_computed_metadata():
    """Known-good single-formula path: formula + values -> computed result."""
    extract_response = (
        "公式：a * b\n"
        "变量：\n"
        "a = 100\n"
        "b = 200\n"
        "数值说明：a=保费, b=倍数"
    )
    fake = FakeLLMClient([_chat(extract_response), _chat("B")])
    solver = CalculationSolver(llm_client=fake)

    result = solver.solve(_make_bundle())
    meta = result.metadata

    assert meta["formula_extracted"] is True
    assert meta["computation_performed"] is True
    assert meta["computation_grounded"] is True
    assert meta["extracted_formula"] == "a * b"
    assert meta["computed_result"] == 20000.0
    assert result.confidence == 1.0
    # The matched answer is propagated from the second LLM call.
    assert result.answer == "B"


# ── 3. answer matching must not silently succeed without computation ────


def test_answer_not_silently_succeeding_without_computation():
    """An answer is still emitted, but it must be flagged as ungrounded.

    This makes the P7A "answer emitted with zero intermediate computation"
    failure observable and testable rather than a silent success.
    """
    extract_response = "无法提取公式，仅凭文本判断选项 B 较为接近。"
    fake = FakeLLMClient([_chat(extract_response), _chat("B")])
    solver = CalculationSolver(llm_client=fake)

    result = solver.solve(_make_bundle(qid="case_004_like"))
    meta = result.metadata

    # The answer letter is unchanged (matching still ran) ...
    assert result.answer == "B"
    assert meta["calculation_phase"] == "match"
    # ... but it is explicitly flagged as ungrounded / zero-confidence.
    assert meta["computation_grounded"] is False
    assert meta["computation_performed"] is False
    assert result.confidence == 0.0


# ── 4. multi-formula partial evaluation is exposed ──────────────────────


def test_multi_formula_partial_evaluation_exposed():
    """case_013 pattern: ranking question evaluated only partially.

    Three formulas are extracted (产品A, 产品B, 合计). The 合计 formula
    references an undefined variable so it fails, while 产品A and 产品B
    evaluate successfully. The metadata must expose the per-label status
    and the expected vs evaluated counts.
    """
    extract_response = (
        "公式[产品A]：a * b\n"
        "公式[产品B]：c * d\n"
        "公式[合计]：产品A + 产品B + x\n"
        "变量：\n"
        "a = 100\n"
        "b = 200\n"
        "c = 50\n"
        "d = 300\n"
        "数值说明：各产品计算"
    )
    fake = FakeLLMClient([_chat(extract_response), _chat("A")])
    solver = CalculationSolver(llm_client=fake)

    result = solver.solve(_make_bundle(qid="case_013_like"))
    meta = result.metadata

    assert meta["multi_formula_used"] is True
    assert meta["extraction_mode"] == "multi_formula"
    assert meta["multi_formula_expected_count"] == 3
    # Only 产品A (20000) and 产品B (15000) evaluated; 合计 failed.
    assert meta["multi_formula_evaluated_count"] == 2
    status = meta["multi_formula_status"]
    assert status["产品A"] == "ok"
    assert status["产品B"] == "ok"
    assert status["合计"] != "ok"
    assert meta["multi_formula_error"] is not None
    # Partial computation still counts as grounded (some math happened),
    # but the incompleteness is visible via the count delta.
    assert meta["computation_grounded"] is True
    assert meta["computed_values"] == {"产品A": 20000.0, "产品B": 15000.0}


def test_multi_formula_complete_evaluation_exposed():
    """Contrast case: all required formulas evaluate successfully."""
    extract_response = (
        "公式[产品A]：a * b\n"
        "公式[产品B]：c * d\n"
        "公式[合计]：产品A + 产品B\n"
        "变量：\n"
        "a = 100\n"
        "b = 200\n"
        "c = 50\n"
        "d = 300\n"
        "数值说明：各产品计算"
    )
    fake = FakeLLMClient([_chat(extract_response), _chat("C")])
    solver = CalculationSolver(llm_client=fake)

    result = solver.solve(_make_bundle())
    meta = result.metadata

    assert meta["multi_formula_expected_count"] == 3
    assert meta["multi_formula_evaluated_count"] == 3
    status = meta["multi_formula_status"]
    assert status["产品A"] == "ok"
    assert status["产品B"] == "ok"
    assert status["合计"] == "ok"
    assert meta["multi_formula_error"] is None
    assert meta["computed_values"]["合计"] == 35000.0


# ── 5. known-good examples continue to pass ─────────────────────────────


def test_known_good_single_formula_still_passes():
    """Regression guard: the pre-existing happy path still produces a
    grounded answer with the correct computed result."""
    extract_response = (
        "公式：a + b - c\n"
        "变量：\n"
        "a = 100000\n"
        "b = 20000\n"
        "c = 5000\n"
        "数值说明：a=保费, b=收益, c=扣减"
    )
    fake = FakeLLMClient([_chat(extract_response), _chat("A")])
    solver = CalculationSolver(llm_client=fake)

    result = solver.solve(_make_bundle())
    meta = result.metadata

    assert meta["computed_result"] == 115000.0
    assert meta["computation_grounded"] is True
    assert result.confidence == 1.0
    assert result.answer == "A"


def test_dry_run_flags_ungrounded():
    """The no-LLM dry-run path must also surface explicit ungrounded flags."""
    solver = CalculationSolver(llm_client=None)
    result = solver.solve(_make_bundle())

    assert result.metadata["dry_run"] is True
    assert result.metadata["formula_extracted"] is False
    assert result.metadata["computation_performed"] is False
    assert result.metadata["computation_grounded"] is False
    assert result.confidence == 0.0
