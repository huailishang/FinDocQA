"""Cross-document solver with per-document extraction before comparison.

For cross-document questions, the solver first extracts key information from each
document independently, then compares across documents to answer.

Stage 5 P5 — truncation cleanup
-------------------------------
``finish_reason=length`` was observed repeatedly on cross-doc cases
in several cross-document cases under the old max-token ceiling
ceiling and open-ended analysis prompt. Two changes address it without touching
any other module:

1. Output budget raised to ``1024`` — enough for one short sentence per
   document plus the final answer line, still far below ``cross_doc_tokens``.
2. Prompt rewritten to a compact answer-first format: one sentence per
   document, one comparison sentence, then an explicit ``最终答案:`` line.

The answer is parsed preferentially from the labelled ``最终答案:`` line so
option letters mentioned inside per-document summaries are not mistaken for the
final answer. Whole-text ``normalize_answer`` remains the fallback.
"""

from __future__ import annotations

import re
from typing import Optional

from contracts import EvidenceBundle, SolverResult
from solvers.base import candidate_doc_ids, normalize_answer, render_question
from utils.llm_client import OpenAICompatibleClient, LLMClientUnavailable, chat_with_fallback


# Match an explicit final-answer line, e.g. "最终答案: AC" / "最終答案：A".
_ANSWER_LINE_RE = re.compile(r"最终答案\s*[:：]\s*([ABCD]+)", re.IGNORECASE)
_DOC_SUMMARY_RE = re.compile(
    r"^\s*-?\s*文档\s+([^:：\s]+)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)

# Output budget. The previous 320-token ceiling forced truncation mid-analysis.
# 1024 leaves room for a compact per-document summary plus the answer line.
_MAX_TOKENS = 1024

_PROMPT_STYLE = "compact_cross_doc_v2"


class CrossDocSolver:
    name = "cross_doc"

    def __init__(self, llm_client: Optional[OpenAICompatibleClient] = None,
                 fallback_llm_client: Optional[OpenAICompatibleClient] = None) -> None:
        self.llm_client = llm_client
        self.fallback_llm_client = fallback_llm_client

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        prompt = self._build_prompt(bundle)
        if self.llm_client is None:
            return SolverResult(
                bundle.question.qid, "A", self.name, "DRY_RUN_NO_LLM_CLIENT",
                metadata={
                    "dry_run": True,
                    "prompt_preview": prompt[:1200],
                    # explicit answer provenance.
                    "answer_source": "dry_run",
                    "ungrounded": True,
                    "truncation_risk": False,
                    "output_chars": 0,
                    "used_doc_ids": [],
                    "used_docs_source": "dry_run_no_usage_proof",
                },
            )
        try:
            result = chat_with_fallback(self.llm_client, self.fallback_llm_client,
                                        [{"role": "user", "content": prompt}], max_tokens=_MAX_TOKENS)
        except LLMClientUnavailable as exc:
            return SolverResult(
                bundle.question.qid, "A", self.name, str(exc),
                metadata={
                    "llm_error": True,
                    "prompt_preview": prompt[:1200],
                    # explicit answer provenance for the error/fallback path.
                    "answer_source": "error",
                    "ungrounded": True,
                    "truncation_risk": False,
                    "output_chars": 0,
                    "used_doc_ids": [],
                    "used_docs_source": "llm_error_no_usage_proof",
                },
            )
        answer = self._extract_answer(result.content, bundle.question.answer_format)
        used_doc_ids = self._extract_used_doc_ids(result.content, bundle)
        truncation_risk = result.finish_reason == "length"
        return SolverResult(
            bundle.question.qid, answer, self.name, result.content,
            metadata={
                "model": result.model,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
                "latency_ms": result.usage.latency_ms,
                "finish_reason": result.finish_reason,
                "prompt_style": _PROMPT_STYLE,
                "output_chars": len(result.content or ""),
                # explicit answer provenance + truncation observability.
                "answer_source": "generated",
                "ungrounded": False,
                "truncation_risk": truncation_risk,
                "used_doc_ids": used_doc_ids,
                "used_docs_source": "explicit_cross_doc_summary" if used_doc_ids else "unknown",
            },
        )

    @staticmethod
    def _extract_used_doc_ids(raw: str, bundle: EvidenceBundle) -> list[str]:
        available = set(candidate_doc_ids(bundle))
        found = []
        seen = set()
        for value in _DOC_SUMMARY_RE.findall(raw or ""):
            doc_id = str(value).strip()
            if doc_id in available and doc_id not in seen:
                seen.add(doc_id)
                found.append(doc_id)
        return found

    @staticmethod
    def _extract_answer(raw: str, answer_format: str) -> str:
        """Prefer the explicit ``最终答案:`` line; fall back to whole-text scan.

        Parsing the labelled line avoids mistaking option letters mentioned in
        per-document summaries for the actual answer.
        """
        m = _ANSWER_LINE_RE.search(raw)
        if m:
            letters = m.group(1).upper()
            if answer_format == "multi":
                return "".join(sorted(set(letters))) or "A"
            if answer_format == "tf":
                return letters[0] if letters and letters[0] in "AB" else "A"
            return letters[0] if letters else "A"
        return normalize_answer(raw, answer_format)

    def _build_prompt(self, bundle: EvidenceBundle) -> str:
        # Identify doc_ids from the evidence bundle, falling back to the question.
        if bundle.candidates:
            doc_ids = sorted(set(c.doc_id for c in bundle.candidates))
        else:
            doc_ids = list(bundle.question.doc_ids)
        doc_headers = "\n".join(f"- 文档 {d}" for d in doc_ids)

        return (
            "你是金融跨文档比较题答题器。问题涉及多个文档，输出必须紧凑，避免冗长分析导致截断。\n\n"
            "规则：\n"
            "1. 对每个文档独立提取要点——不要混淆不同文档的数据、假设或口径。\n"
            "2. 每个文档只用一句话概括关键事实（数值、年份、条件、否定词）。\n"
            "3. 所有文档概括完之后，再给出一句话比较结论。\n"
            "4. 最后单独一行给出最终答案字母，多选按字母顺序排列（例如 AC）。\n\n"
            "输出格式（严格遵循，保持紧凑，不要展开长段分析）：\n"
            "文档要点:\n"
            "- 文档 <doc_id>: <一句话>\n"
            "- 文档 <doc_id>: <一句话>\n"
            "比较结论: <一句话>\n"
            "最终答案: <字母>\n\n"
            f"涉及的文档：\n{doc_headers}\n\n"
            f"{render_question(bundle)}\n\n"
            f"证据（按文档分组，不要混淆）：\n{bundle.prompt_context}\n\n"
            "请按上述格式输出："
        )
