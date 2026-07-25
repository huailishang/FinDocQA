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
            result = chat_with_fallback(self.llm_client, self.fallback_llm_client,
                                        [{"role": "user", "content": prompt}], max_tokens=128)
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
        return (
            "你是金融长文档选择题答题器。只能根据给定证据回答，不要凭常识补充。\n"
            "如果证据不足，也必须选择最可能的答案。\n\n"
            f"{render_question(bundle)}\n\n"
            f"证据：\n{bundle.prompt_context}\n\n"
            f"{answer_format_instruction(bundle.question.answer_format)}"
        )