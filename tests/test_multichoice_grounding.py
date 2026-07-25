"""MultiChoice option-level grounding + truncation observability (P6k Step 2).

Covers the P6k behavior additions to ``MultiChoiceSolver``:

- ``_missing_option_judgments``: options without an explicit
  【支持/反驳/不确定】 tag (grounding gap, often caused by truncation);
- ``_truncation_risk``: ``finish_reason == "length"`` flag;
- ``solve()`` metadata wiring: ``judged_options``, ``missing_option_judgments``,
  ``option_coverage``, ``truncation_risk``, ``prompt_style`` + corresponding
  warnings;
- the narrowed ``max_tokens`` budget (2048 -> 3072) is passed to the client.

All tests are offline: a tiny in-memory stub client replaces the real LLM, so
no network, no API key, no full dataset, and no ``output/`` writes.
"""

from __future__ import annotations

from contracts import ClassificationResult, EvidenceBundle, Question, QuestionLabel
from solvers.multi_choice import MultiChoiceSolver
from utils.llm_client import ChatResult, ChatUsage

OPTS = {"A": "选项甲", "B": "选项乙", "C": "选项丙", "D": "选项丁"}


class _StubClient:
    """Minimal stand-in for OpenAICompatibleClient.chat()."""

    def __init__(self, content: str, finish_reason: str = "stop", total_tokens: int = 100) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self._total_tokens = total_tokens
        self.last_max_tokens: int | None = None

    def chat(self, messages, max_tokens: int = 2048) -> ChatResult:
        self.last_max_tokens = max_tokens
        return ChatResult(
            content=self._content,
            model="stub",
            finish_reason=self._finish_reason,
            usage=ChatUsage(
                prompt_tokens=50,
                completion_tokens=10,
                total_tokens=self._total_tokens,
                latency_ms=1.0,
            ),
        )


def _bundle() -> EvidenceBundle:
    q = Question(
        qid="q1",
        domain="insurance",
        text="题目",
        options=OPTS,
        answer_format="multi",
        doc_ids=["1"],
    )
    cls = ClassificationResult(labels=[QuestionLabel.MULTI_OPTION])
    return EvidenceBundle(
        question=q, classification=cls, candidates=[], prompt_context="证据", estimated_tokens=10
    )


# ── static helpers ──────────────────────────────────────────────────────


def test_missing_option_judgments_all_judged():
    text = "A: 【支持】理由\nB: 【反驳】理由\nC: 【不确定】理由\nD: 【支持】理由"
    assert MultiChoiceSolver._missing_option_judgments(text, OPTS) == []


def test_missing_option_judgments_some_missing():
    text = "A: 【支持】理由\nB: 【反驳】理由"
    assert MultiChoiceSolver._missing_option_judgments(text, OPTS) == ["C", "D"]


def test_missing_option_judgments_none_judged():
    assert MultiChoiceSolver._missing_option_judgments("无关文本无标签", OPTS) == ["A", "B", "C", "D"]


def test_truncation_risk_flags_length_only():
    assert MultiChoiceSolver._truncation_risk("length") is True
    assert MultiChoiceSolver._truncation_risk("stop") is False
    assert MultiChoiceSolver._truncation_risk("") is False


# ── solve() metadata wiring (offline stub client) ───────────────────────


def test_solve_exposes_full_grounding_metadata():
    content = "A: 【支持】理由\nB: 【反驳】理由\nC: 【不确定】理由\nD: 【支持】理由"
    solver = MultiChoiceSolver(llm_client=_StubClient(content))
    res = solver.solve(_bundle())
    assert res.answer == "AD"  # only 支持 options selected
    m = res.metadata
    assert m["judged_options"] == ["A", "B", "C", "D"]
    assert m["missing_option_judgments"] == []
    assert m["option_coverage"] == "4/4"
    assert m["truncation_risk"] is False
    assert m["prompt_style"] == "compact_per_option_v2"
    # No grounding/truncation warnings when everything is complete.
    assert "missing_option_judgments" not in m.get("warnings", [])
    assert "truncation_risk" not in m.get("warnings", [])


def test_solve_flags_truncation_and_missing_options():
    # Only A and B judged; C/D omitted (simulating output cut off mid-analysis).
    content = "A: 【支持】理由\nB: 【反驳】理由"
    solver = MultiChoiceSolver(llm_client=_StubClient(content, finish_reason="length"))
    res = solver.solve(_bundle())
    m = res.metadata
    assert res.answer == "A"  # only A is 支持; B is 反驳; C/D default 不确定
    assert m["missing_option_judgments"] == ["C", "D"]
    assert m["option_coverage"] == "2/4"
    assert m["truncation_risk"] is True
    assert "truncation_risk" in m["warnings"]
    assert "missing_option_judgments" in m["warnings"]


def test_solve_passes_narrow_max_token_budget():
    """P6k: multi-choice output budget raised to 4096 (narrow max-token change)."""
    content = "A: 【支持】理由\nB: 【反驳】理由\nC: 【不确定】理由\nD: 【支持】理由"
    stub = _StubClient(content)
    solver = MultiChoiceSolver(llm_client=stub)
    solver.solve(_bundle())
    assert stub.last_max_tokens == 4096
