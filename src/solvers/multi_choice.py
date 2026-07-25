"""Multi-choice solver with two-step option-by-option judgment.

Step 1: LLM outputs per-option judgment (【支持】/【反驳】/【不确定】) with reasons.
Step 2: Code programmatically selects only 【支持】 options as the final answer.

This eliminates self-contradiction: the model cannot override its own per-option
judgments with a "最终答案" line, because the answer is derived from judgments
by code, not from the model's final line.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from contracts import EvidenceBundle, SolverResult
from solvers.base import candidate_doc_ids, conservative_used_doc_lineage, extract_declared_used_doc_ids, render_question
from utils.llm_client import OpenAICompatibleClient, LLMClientUnavailable, chat_with_fallback


# Output budget. P6k: raised from 2048 to 4096 to reduce option-judgment
# truncation observed on long multi-choice questions (P6j found 6 truncated
# multi_choice rows with finish_reason=length). Narrow max-token change only;
# no other generation parameter is altered.
_MAX_TOKENS = 4096

# Prompt style tag for auditability.
_PROMPT_STYLE = "compact_per_option_v2"


class MultiChoiceSolver:
    name = "multi_choice"

    def __init__(self, llm_client: Optional[OpenAICompatibleClient] = None,
                 fallback_llm_client: Optional[OpenAICompatibleClient] = None) -> None:
        self.llm_client = llm_client
        self.fallback_llm_client = fallback_llm_client

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)
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
                    "used_doc_ids": used_doc_ids,
                    "used_docs_source": used_docs_source,
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
                    "used_doc_ids": used_doc_ids,
                    "used_docs_source": used_docs_source,
                },
            )

        declared_used_docs = extract_declared_used_doc_ids(result.content, bundle)
        if declared_used_docs:
            used_doc_ids = declared_used_docs
            used_docs_source = "explicit_model_declaration"

        # Step 2: programmatically select 【支持】 options from the model output
        judgments = self._parse_judgments(result.content, bundle.question.options)
        supported = sorted(k for k, v in judgments.items() if v == "支持")

        # Detect parse failure: if no option has an explicit judgment found
        parsed_any = any(v != "不确定" or self._has_explicit_tag(result.content, k) for k, v in judgments.items())
        structured_parse_failed = not parsed_any

        # P6k: option-level grounding coverage + truncation-risk exposure.
        # An option without an explicit 【tag】 line (often caused by truncation)
        # defaults to 不确定 and can never be selected, so the answer is
        # necessarily incomplete. Exposing this in metadata lets the evaluator
        # audit grounding gaps without re-reading the raw model output.
        missing_option_judgments = self._missing_option_judgments(
            result.content, bundle.question.options
        )
        judged_options = sorted(
            k for k in bundle.question.options if k not in missing_option_judgments
        )
        option_count = len(bundle.question.options)
        option_coverage = f"{len(judged_options)}/{option_count}" if option_count else "0/0"
        truncation_risk = self._truncation_risk(result.finish_reason)

        # Build metadata
        meta: Dict[str, object] = {
            "model": result.model,
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
            "latency_ms": result.usage.latency_ms,
            "finish_reason": result.finish_reason,
            "judgments": judgments,
            "selected_from_judgments": supported,
            "structured_parse_failed": structured_parse_failed,
            "no_supported_options": len(supported) == 0,
            "method": "two_step_programmatic",
            # P6k grounding/truncation observability
            "prompt_style": _PROMPT_STYLE,
            "judged_options": judged_options,
            "missing_option_judgments": missing_option_judgments,
            "option_coverage": option_coverage,
            "truncation_risk": truncation_risk,
            "used_doc_ids": used_doc_ids,
            "used_docs_source": used_docs_source,
        }

        # Determine answer with explicit fallback
        warnings = []

        # Truncation guard: if output was cut off, judgments may be incomplete
        if result.finish_reason == "length":
            warnings.append("truncated_output")
            meta["truncated"] = True

        # explicit answer provenance. The dominant risk pattern
        # (a truncated multi-choice case) is "truncation + no supported options -> fallback A".
        # That fallback answer must never be presented as a clean generated
        # answer. We tag it as an unsupported-option guess, and when truncation
        # is also present, as a truncated unsupported-option guess so the
        # Evaluator/Reviewer can isolate it without re-reading raw output.
        # The answer letter itself is unchanged (pipeline/score compatibility).
        if supported:
            answer = "".join(supported)
            answer_source = "generated"
            ungrounded = False
        else:
            # Emergency fallback: no option marked 支持
            answer = "A"
            meta["fallback_answer"] = "A"
            warnings.append("no_supported_options_fallback")
            if truncation_risk:
                answer_source = "unsupported_guess_truncated"
                warnings.append("unsupported_guess_truncated")
                # high-risk marker for the a truncated multi-choice case-style pattern so it is
                # never silently promoted as a clean baseline answer.
                meta["high_risk"] = True
                meta["answer_accepted_despite_truncation"] = True
            else:
                answer_source = "unsupported_guess"
            ungrounded = True
            if structured_parse_failed:
                warnings.append("structured_parse_failed")
        if missing_option_judgments:
            warnings.append("missing_option_judgments")
        if truncation_risk:
            warnings.append("truncation_risk")

        meta["answer_source"] = answer_source
        meta["ungrounded"] = ungrounded

        if warnings:
            meta["warnings"] = warnings

        return SolverResult(
            bundle.question.qid, answer, self.name, result.content,
            metadata=meta,
        )

    def _build_prompt(self, bundle: EvidenceBundle) -> str:
        available_docs = ", ".join(candidate_doc_ids(bundle)) or "无"
        # P6k: compacted instruction. The per-option 【支持/反驳/不确定】 contract
        # is preserved; the four redundant example lines were folded into a single
        # format spec to leave more of the output budget for actual judgments and
        # to require explicit coverage of every option (reduces omission/truncation
        # risk on long-context questions).
        return (
            "你是金融多选题答题器。多选题无部分分，必须对每个选项逐项判断，不要遗漏。\n"
            f"第一行必须输出：使用文档：<实际用于判断的文档ID，逗号分隔>。可用文档ID：{available_docs}。"
            "每行一个选项，格式：字母: 【支持|反驳|不确定】一句话理由。\n"
            "【支持】=证据明确支持；【反驳】=证据明确否定；【不确定】=证据不足。"
            "注意否定词（不/除外/不得）、年份、金额单位。\n\n"
            f"{render_question(bundle)}\n\n"
            f"证据：\n{bundle.prompt_context}\n\n"
            "逐项判断（每行一个选项，勿省略）："
        )

    @staticmethod
    def _parse_judgments(text: str, options: Dict[str, str]) -> Dict[str, str]:
        """Parse per-option judgments from model output.

        Looks for lines like 'A: 【支持】理由' and extracts the judgment tag.
        If an option carries multiple explicit tags in the same raw output
        (e.g. the model self-corrects an earlier judgment), the LAST explicit
        tag is used, so self-corrections are honored instead of discarded.
        Only explicit 【支持】/【反驳】/【不确定】 tags are parsed; untagged prose
        is never inferred. Returns dict like {'A': '支持', 'B': '反驳', ...}.
        Default judgment is '不确定' if no explicit tag is found.
        When multiple tags exist for one option (e.g. B first 【反驳】 then
        【支持】), uses the LAST explicit tag. This is required because the
        model sometimes self-corrects during its reasoning (P6e-4).
        Returns dict like {'A': '支持', 'B': '反驳', ...}.
        Default judgment is '不确定' if not found.
        """
        judgments: Dict[str, str] = {}
        for opt_key in options:
            # Accept both the required tagged form (A: 【支持】...) and a
            # concise final self-check form (A: 支持 ...).  The latter appears
            # when the model revises an earlier judgment near the end of a long
            # answer.  Anchoring at line start avoids matching ordinary prose.
            pattern = rf"(?m)^\s*{opt_key}\s*[:：]\s*(?:【\s*)?(支持|反驳|不确定)(?:\s*】)?"
            matches = re.findall(pattern, text)
            judgments[opt_key] = matches[-1] if matches else "不确定"
        return judgments

    @staticmethod
    def _has_explicit_tag(text: str, opt_key: str) -> bool:
        """Check if an option has an explicit 【tag】 in the model output."""
        pattern = rf"{opt_key}\s*[:：]\s*【(支持|反驳|不确定)】"
        return bool(re.search(pattern, text))

    @staticmethod
    def _missing_option_judgments(text: str, options: Dict[str, str]) -> list:
        """Return option keys that lack an explicit 【tag】 in the model output.

        Used to detect grounding gaps where truncation or omission prevented
        the model from judging an option.
        """
        missing = []
        for opt_key in options:
            pattern = rf"{opt_key}\s*[:：]\s*【(支持|反驳|不确定)】"
            if not re.search(pattern, text):
                missing.append(opt_key)
        return sorted(missing)

    @staticmethod
    def _truncation_risk(finish_reason: str) -> bool:
        """Return True if the output was truncated (finish_reason=length)."""
        return finish_reason == "length"
