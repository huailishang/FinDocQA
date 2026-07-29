"""Direct short-answer solver."""

from __future__ import annotations

from typing import Optional

from contracts import EvidenceBundle, SolverResult
from solvers.base import conservative_used_doc_lineage, answer_format_instruction, dry_run_answer, normalize_answer, render_question
from utils.llm_client import OpenAICompatibleClient, LLMClientUnavailable, chat_with_fallback


class DirectSolver:
    name = "direct"

    def __init__(self, llm_client: Optional[OpenAICompatibleClient] = None,
                 fallback_llm_client: Optional[OpenAICompatibleClient] = None) -> None:
        self.llm_client = llm_client
        self.fallback_llm_client = fallback_llm_client

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)
        prompt = self._build_prompt(bundle)
        if self.llm_client is None:
            return SolverResult(
                qid=bundle.question.qid,
                answer=dry_run_answer(bundle.question.answer_format),
                solver=self.name,
                raw_output="DRY_RUN_NO_LLM_CLIENT",
                metadata={
                    "dry_run": True,
                    "prompt_preview": prompt[:1200],
                    # P7E: explicit answer provenance so dry-run is never mistaken
                    # for a grounded/generated answer.
                    "answer_source": "dry_run",
                    "ungrounded": True,
                    "truncation_risk": False,
                    "output_chars": 0,
                    "used_doc_ids": used_doc_ids,
                    "used_docs_source": used_docs_source,
                },
            )
        try:
            result = chat_with_fallback(
                self.llm_client,
                self.fallback_llm_client,
                [{"role": "user", "content": prompt}],
                max_tokens=self._max_tokens(bundle),
            )
        except LLMClientUnavailable as exc:
            return SolverResult(
                qid=bundle.question.qid,
                answer=dry_run_answer(bundle.question.answer_format),
                solver=self.name,
                raw_output=str(exc),
                metadata={
                    "llm_error": True,
                    "prompt_preview": prompt[:1200],
                    # P7E: explicit answer provenance for the error/fallback path.
                    "answer_source": "error",
                    "ungrounded": True,
                    "truncation_risk": False,
                    "output_chars": 0,
                    "used_doc_ids": used_doc_ids,
                    "used_docs_source": used_docs_source,
                },
            )
        answer = normalize_answer(result.content, bundle.question.answer_format)
        truncation_risk = result.finish_reason == "length"
        return SolverResult(
            qid=bundle.question.qid,
            answer=answer,
            solver=self.name,
            raw_output=result.content,
            metadata={
                "model": result.model,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
                "latency_ms": result.usage.latency_ms, "finish_reason": result.finish_reason,
                # P7E: explicit answer provenance + truncation observability so a
                # truncated direct answer is never silently treated as clean.
                "answer_source": "generated",
                "ungrounded": False,
                "truncation_risk": truncation_risk,
                "output_chars": len(result.content or ""),
                "used_doc_ids": used_doc_ids,
                "used_docs_source": used_docs_source,
            },
        )

    def _build_prompt(self, bundle: EvidenceBundle) -> str:
        if bundle.question.options:
            role_and_policy = (
                "你是金融长文档选择题答题器。只能根据给定证据回答，不要凭常识补充。\n"
                "如果证据不足，也必须按题目合同选择最可能的答案。"
            )
        else:
            role_and_policy = (
                "你是金融长文档问答助手。只能根据给定证据回答，不要凭常识补充。\n"
                "问题没有预设选项；请按问题本身回答。证据不足时明确说明无法从现有证据确认，不要编造答案。"
            )
        shape_instruction = self._shape_instruction(bundle)
        return (
            f"{role_and_policy}\n\n"
            f"{render_question(bundle)}\n\n"
            f"证据：\n{bundle.prompt_context}\n\n"
            f"{answer_format_instruction(bundle.question.answer_format)}"
            f"{shape_instruction}"
        )

    @staticmethod
    def _answer_shape(bundle: EvidenceBundle) -> str:
        understanding = bundle.question.raw.get("_query_understanding")
        if isinstance(understanding, dict):
            return str(understanding.get("answer_shape") or "").strip()
        return ""

    @classmethod
    def _max_tokens(cls, bundle: EvidenceBundle) -> int:
        if bundle.question.options:
            return 128
        return 384 if cls._answer_shape(bundle) == "long_text" else 256

    @classmethod
    def _shape_instruction(cls, bundle: EvidenceBundle) -> str:
        if bundle.question.options:
            return ""
        shape = cls._answer_shape(bundle)
        if shape == "number":
            return "\n数值问题必须同时保留必要单位、百分号或日期口径。"
        if shape == "boolean":
            return "\n先明确回答是/否或成立/不成立，再用一句证据说明。"
        if shape == "ordered_list":
            return "\n按问题要求给出有序列表，并保持排序依据一致。"
        if shape == "long_text":
            return "\n先给结论，再用少量关键事实解释原因；每个事实必须能在给定证据中找到依据，整体尽量控制在300字以内。"
        return ""
