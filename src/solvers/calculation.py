"""Calculation solver with formula extraction → Python eval → option matching.

The solver avoids LLM mental arithmetic. It extracts formula and values from
evidence, runs Python-side calculation, then asks the LLM to match the result
against options.
"""

from __future__ import annotations

import ast
import json
import keyword
import math
import os
import re
import subprocess
import sys
import textwrap
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from calculation import (
    BoundVariable,
    DeterministicCalculationEngine,
    DeterministicExecutionGateInput,
    FormulaProgram,
    SourceBoundNumericSeriesAggregator,
)
from contracts import EvidenceBundle, SolverResult
from evidence.c3_numeric_series_binding import SourceBoundSumSeriesBinder
from solvers.base import (
    candidate_doc_lineage,
    candidate_doc_ids,
    conservative_used_doc_lineage,
    extract_declared_used_doc_ids,
    normalize_answer,
    normalize_solver_evidence_refs,
    render_question,
)
from solvers.freeform import (
    decimal_scale_from_question,
    format_decimal_for_submission,
    parse_finite_decimal,
    parse_freeform_submission_answers,
)
from utils.llm_client import OpenAICompatibleClient, LLMClientUnavailable, chat_with_fallback
from runtime_safety import set_attempt_context
from verification.calculation_grounding import build_calculation_grounding, build_option_evaluations_from_conditions
from verification.insurance_calculation_compiler import InsuranceCalculationCompiler


_FREEFORM_MODEL_PYTHON_TOLERANCE = Decimal("0.000001")
_PERCENTAGE_RESULT_SEMANTICS = {"ratio", "display_percentage_points"}


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


class CalculationSolver:
    name = "calculation"

    def __init__(self, llm_client: Optional[OpenAICompatibleClient] = None,
                 fallback_llm_client: Optional[OpenAICompatibleClient] = None) -> None:
        self.llm_client = llm_client
        self.fallback_llm_client = fallback_llm_client

    def solve_deterministic_gated(
        self,
        bundle: EvidenceBundle,
        program: FormulaProgram,
        bindings: Mapping[str, BoundVariable],
        gate_input: DeterministicExecutionGateInput,
    ) -> SolverResult:
        """Convert an explicitly authorized C3-D execution into a solver result.

        This adapter deliberately does not infer a formula or binding from the
        bundle and never delegates to ``solve``.  Production routing and any
        recovery policy remain the caller's responsibility.
        """
        execution = DeterministicCalculationEngine().execute_gated_program(
            program,
            bindings,
            gate_input,
        )
        source_lineage = [source_ref.to_dict() for source_ref in execution.source_refs]
        metadata: dict[str, Any] = {
            "provider_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "legacy_execution_invoked": False,
            "formula_program": program.to_dict(),
            "result_trace": [dict(step) for step in execution.trace],
            "source_lineage": source_lineage,
            "source_refs": source_lineage,
            "gate_status": execution.gate_status,
            "audit_reasons": list(execution.audit_reasons),
        }
        if execution.ok:
            metadata.update(
                {
                    "answer_source": "c3_deterministic_gate",
                    "computation_status": "completed",
                }
            )
            return SolverResult(
                qid=bundle.question.qid,
                answer=str(execution.value),
                solver=self.name,
                confidence=1.0,
                metadata=metadata,
            )

        metadata.update(
            {
                "answer_source": (
                    "c3_deterministic_execution_not_ready"
                    if execution.error == "deterministic_execution_not_ready"
                    else "c3_deterministic_execution_failed"
                ),
                "computation_status": (
                    "blocked"
                    if execution.error == "deterministic_execution_not_ready"
                    else "failed"
                ),
                "error": execution.error,
            }
        )
        return SolverResult(
            qid=bundle.question.qid,
            answer="",
            solver=self.name,
            raw_output=execution.error,
            confidence=0.0,
            metadata=metadata,
        )

    @staticmethod
    def _source_bound_sum_series_eligible(bundle: EvidenceBundle) -> bool:
        """Restrict the deterministic SUM branch to ordinary open-QA input.

        Formal AFAC/B freeform contracts, option questions, and other solver
        lanes retain their historical behavior even if their evidence happens
        to resemble a structured numeric series.
        """
        question = bundle.question
        raw = question.raw if isinstance(question.raw, Mapping) else {}
        return bool(
            question.answer_format == "freeform"
            and not question.options
            and str(raw.get("_input_adapter") or "") == "canonical_question_v1"
            and str(raw.get("split") or "").strip().upper() != "B"
            and question.submission_slot_count is None
            and not question.submission_slot_contracts
        )

    @staticmethod
    def _source_bound_sum_series_metadata(
        *,
        binding: Any,
        execution: Any,
        computation_status: str,
        answer_source: str,
        computation_complete: bool,
    ) -> dict[str, Any]:
        source_refs = tuple(execution.source_refs or binding.source_refs or ())
        source_lineage = [source_ref.to_dict() for source_ref in source_refs]
        used_doc_ids = list(
            dict.fromkeys(
                str(source_ref.doc_id)
                for source_ref in source_refs
                if str(source_ref.doc_id).strip()
            )
        )
        return {
            "answer_source": answer_source,
            "computation_status": computation_status,
            "computation_complete": computation_complete,
            "computation_performed": bool(execution.ok),
            "computation_grounded": bool(execution.ok),
            "provider_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "legacy_execution_invoked": False,
            "request_contract": "SourceBoundNumericSeriesAggregationRequest",
            "source_bound_sum_series_request": binding.to_dict().get("request"),
            "binding_trace": [dict(step) for step in binding.trace],
            "binding_reasons": list(binding.reasons),
            "binding_metadata": dict(binding.metadata or {}),
            "result_trace": [dict(step) for step in execution.trace],
            "source_lineage": source_lineage,
            "source_refs": source_lineage,
            "solver_source_refs": source_lineage,
            "solver_used_doc_ids": used_doc_ids,
            "used_doc_ids": used_doc_ids,
            "solver_lineage_source": "c3_source_bound_sum_series",
            "used_docs_source": "c3_source_bound_sum_series",
            "source_lineage_complete": bool(source_lineage and used_doc_ids),
            "gate_status": execution.gate_status,
            "audit_reasons": list(execution.audit_reasons),
            "ungrounded": not computation_complete,
        }

    def _solve_source_bound_sum_series(
        self, bundle: EvidenceBundle
    ) -> SolverResult | None:
        if not self._source_bound_sum_series_eligible(bundle):
            return None

        try:
            binding = SourceBoundSumSeriesBinder().bind(bundle)
        except Exception as exc:
            return SolverResult(
                qid=bundle.question.qid,
                answer="",
                solver=self.name,
                raw_output=str(exc),
                confidence=0.0,
                metadata={
                    "answer_source": "c3_source_bound_sum_series_binding_failed",
                    "computation_status": "failed",
                    "computation_complete": False,
                    "computation_performed": False,
                    "computation_grounded": False,
                    "provider_call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "legacy_execution_invoked": False,
                    "request_contract": "SourceBoundNumericSeriesAggregationRequest",
                    "binding_trace": [],
                    "result_trace": [],
                    "source_lineage": [],
                    "source_refs": [],
                    "solver_source_refs": [],
                    "solver_used_doc_ids": [],
                    "solver_lineage_source": "c3_source_bound_sum_series",
                    "gate_status": "",
                    "audit_reasons": ["source_bound_sum_series_binding_exception"],
                    "error": str(exc),
                    "ungrounded": True,
                },
            )

        if not binding.ready or binding.request is None:
            return None

        try:
            execution = SourceBoundNumericSeriesAggregator().execute(binding.request)
        except Exception as exc:
            metadata = {
                "answer_source": "c3_source_bound_sum_series_execution_failed",
                "computation_status": "failed",
                "computation_complete": False,
                "computation_performed": False,
                "computation_grounded": False,
                "provider_call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "legacy_execution_invoked": False,
                "request_contract": "SourceBoundNumericSeriesAggregationRequest",
                "source_bound_sum_series_request": binding.to_dict().get("request"),
                "binding_trace": [dict(step) for step in binding.trace],
                "binding_reasons": list(binding.reasons),
                "binding_metadata": dict(binding.metadata or {}),
                "result_trace": [],
                "source_lineage": [item.to_dict() for item in binding.source_refs],
                "source_refs": [item.to_dict() for item in binding.source_refs],
                "solver_source_refs": [item.to_dict() for item in binding.source_refs],
                "solver_used_doc_ids": list(
                    dict.fromkeys(
                        str(item.doc_id)
                        for item in binding.source_refs
                        if str(item.doc_id).strip()
                    )
                ),
                "solver_lineage_source": "c3_source_bound_sum_series",
                "gate_status": "",
                "audit_reasons": ["source_bound_sum_series_execution_exception"],
                "error": str(exc),
                "ungrounded": True,
            }
            metadata["used_doc_ids"] = list(metadata["solver_used_doc_ids"])
            metadata["used_docs_source"] = "c3_source_bound_sum_series"
            return SolverResult(
                qid=bundle.question.qid,
                answer="",
                solver=self.name,
                raw_output=str(exc),
                confidence=0.0,
                metadata=metadata,
            )

        if not execution.ok:
            metadata = self._source_bound_sum_series_metadata(
                binding=binding,
                execution=execution,
                computation_status="failed",
                answer_source="c3_source_bound_sum_series_execution_failed",
                computation_complete=False,
            )
            metadata["error"] = execution.error
            return SolverResult(
                qid=bundle.question.qid,
                answer="",
                solver=self.name,
                raw_output=execution.error,
                confidence=0.0,
                metadata=metadata,
            )

        answer = str(execution.value)
        metadata = self._source_bound_sum_series_metadata(
            binding=binding,
            execution=execution,
            computation_status="completed",
            answer_source="c3_source_bound_sum_series",
            computation_complete=True,
        )
        metadata.update(
            {
                "submission_answers": [answer],
                "expected_submission_slots": 1,
            }
        )
        return SolverResult(
            qid=bundle.question.qid,
            answer=answer,
            solver=self.name,
            confidence=1.0,
            metadata=metadata,
        )

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        source_bound_sum = self._solve_source_bound_sum_series(bundle)
        if source_bound_sum is not None:
            return source_bound_sum
        if bundle.question.answer_format == "freeform":
            return self._solve_freeform(bundle)
        deterministic = self._solve_insurance_calculation(bundle)
        if deterministic is not None:
            return deterministic
        if self.llm_client is None:
            return self._dry_run(bundle)
        if os.getenv("SAFE_RUN_CALCULATION_CONTRACT", "").strip().lower() == "one-call":
            return self._solve_one_call(bundle)
        return self._solve_with_python(bundle)

    def _build_freeform_prompt(self, bundle: EvidenceBundle, *, slot_count: int) -> str:
        documents = "\n\n".join(
            f"[DOC:{candidate.doc_id}]\n{candidate.text}"
            for candidate in bundle.candidates
        )
        slot_contract_json = json.dumps(
            [dict(item) for item in bundle.question.submission_slot_contracts],
            ensure_ascii=False,
            indent=2,
        )
        return textwrap.dedent(f"""
        你是金融长文档计算与结构化答案求解器。当前题目不是选择题。
        只能输出一个 JSON 对象，不要输出 Markdown、解释段落或 A/AB 等选项字母答案。

        题目ID：{bundle.question.qid}
        题目：{bundle.question.text}
        独立答案槽数量：{slot_count}
        权威逐槽合同（kind、精度、单位和百分比语义必须逐槽严格遵守）：
        {slot_contract_json}

        证据：
        {documents}

        JSON 必须满足：
        {{
          "qid": "{bundle.question.qid}",
          "answers": [
            {{
              "value": "最终提交值",
              "kind": "number|percentage|percentage_point|date|ordering|text",
              "formula_text": "公式；非计算型可为空",
              "variables": {{"变量名": 数值}},
              "computed_result": "公式计算出的原始数值或结构化结果",
              "percentage_result_semantics": "ratio|display_percentage_points；仅 percentage 必填",
              "evidence_refs": ["实际使用的doc_id"]
            }}
          ],
          "used_doc_ids": ["实际使用的doc_id"],
          "confidence": 0到1
        }}

        强制要求：
        1. answers 数组长度必须恰好为 {slot_count}。
        2. 每个答案槽独立存在，禁止使用分号、逗号把多个答案拼进一个 value。
        3. percentage 必须带 %；中文日期使用 YYYY年M月D日；排序使用半角 > 且两侧无空格。
        4. 题目要求保留两位小数时，number/percentage 必须保留两位。
        5. percentage 的公式结果若为比例值（0.1234 表示 12.34%），填写 ratio；若公式结果本身就是百分数显示值（12.34 表示 12.34%），填写 display_percentage_points。禁止省略或猜测。
        6. 对可计算答案给出 formula_text、variables、computed_result；computed_result 必须是公式原始结果，不得写最终格式化字符串；不得输出选择题字母。
        """).strip()

    def _solve_freeform(self, bundle: EvidenceBundle) -> SolverResult:
        slot_count = bundle.question.submission_slot_count
        slot_contracts = tuple(
            dict(item) for item in bundle.question.submission_slot_contracts
        )
        requires_authoritative_contract = (
            str(bundle.question.raw.get("split") or "").strip().upper() == "B"
        )
        generic_single_freeform = bool(
            not requires_authoritative_contract
            and slot_count is None
            and not slot_contracts
            and not bundle.question.options
        )
        if generic_single_freeform:
            # Real user queries do not carry competition submission-slot metadata.
            # Treat one natural-language question as one answer value while keeping
            # AFAC/B strict slot contracts unchanged.
            slot_count = 1
        if (
            slot_count not in {1, 2, 3, 4}
            or (requires_authoritative_contract and len(slot_contracts) != slot_count)
        ):
            return SolverResult(
                bundle.question.qid,
                "",
                self.name,
                "missing or invalid submission slot contract",
                confidence=0.0,
                metadata={
                    "freeform_contract": "multi_slot_structured_v1",
                    "freeform_parse_valid": False,
                    "freeform_parse_reason": "missing_submission_slot_contract",
                    "submission_answers": [],
                    "expected_submission_slots": slot_count,
                    "provider_call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "computation_complete": False,
                    "freeform_binding_auditable": False,
                    "answer_source": "blocked_freeform_contract",
                    "ungrounded": True,
                    "failure_class": "MODEL_OUTPUT_INVALID",
                    "evidence_failure_class": "MODEL_OUTPUT_INVALID",
                    "used_doc_ids": [],
                    "used_docs_source": "missing_submission_slot_contract",
                },
            )
        if self.llm_client is None and self.fallback_llm_client is None:
            return SolverResult(
                bundle.question.qid,
                "",
                self.name,
                "No LLM client configured.",
                confidence=0.0,
                metadata={
                    "llm_error": True,
                    "freeform_contract": "multi_slot_structured_v1",
                    "freeform_parse_valid": False,
                    "freeform_parse_reason": "llm_client_unavailable",
                    "submission_answers": [],
                    "expected_submission_slots": slot_count,
                    "provider_call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "computation_complete": False,
                    "freeform_binding_auditable": False,
                    "answer_source": "error",
                    "ungrounded": True,
                    "failure_class": "PROVIDER_ERROR",
                    "evidence_failure_class": "PROVIDER_ERROR",
                    "used_doc_ids": [],
                    "used_docs_source": "unknown",
                },
            )

        stage = "calculation_freeform_structured"
        set_attempt_context(bundle.question.qid, stage)
        prompt = self._build_freeform_prompt(bundle, slot_count=slot_count)
        try:
            response = chat_with_fallback(
                self.llm_client,
                self.fallback_llm_client,
                [{"role": "user", "content": prompt}],
                max_tokens=2048,
            )
        except LLMClientUnavailable as exc:
            return SolverResult(
                bundle.question.qid,
                "",
                self.name,
                str(exc),
                confidence=0.0,
                metadata={
                    "llm_error": True,
                    "freeform_contract": "multi_slot_structured_v1",
                    "freeform_parse_valid": False,
                    "freeform_parse_reason": "llm_client_unavailable",
                    "submission_answers": [],
                    "expected_submission_slots": slot_count,
                    "provider_call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "computation_complete": False,
                    "freeform_binding_auditable": False,
                    "answer_source": "error",
                    "ungrounded": True,
                    "failure_class": "PROVIDER_ERROR",
                    "evidence_failure_class": "PROVIDER_ERROR",
                    "used_doc_ids": [],
                    "used_docs_source": "unknown",
                },
            )

        parsed = parse_freeform_submission_answers(
            response.content,
            expected_slots=slot_count,
            question_text=bundle.question.text,
            expected_slot_contracts=slot_contracts,
        )
        used_doc_normalization = normalize_solver_evidence_refs(parsed.used_doc_ids, bundle)
        if parsed.used_doc_ids:
            used_doc_ids = list(used_doc_normalization["normalized_refs"])
            used_docs_source = (
                "freeform_explicit_model_declaration_normalized"
                if used_doc_normalization["all_resolved"]
                and used_doc_normalization["lineage_complete"]
                else "freeform_explicit_model_declaration_unresolved"
            )
            used_doc_lineage_valid = bool(
                used_doc_normalization["all_resolved"]
                and used_doc_normalization["lineage_complete"]
            )
        else:
            used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)
            used_doc_lineage_valid = False
            if used_doc_ids:
                inferred_lineage = {
                    doc_id: candidate_doc_lineage(bundle, doc_id)
                    for doc_id in used_doc_ids
                }
                inferred_lineage_complete = all(
                    any(str(item.get("source") or "").strip() for item in inferred_lineage[doc_id])
                    for doc_id in used_doc_ids
                )
                used_doc_lineage_valid = inferred_lineage_complete
                used_doc_normalization = {
                    "raw_refs": [],
                    "normalized_refs": list(used_doc_ids),
                    "resolutions": [],
                    "lineage_by_doc": inferred_lineage,
                    "all_resolved": inferred_lineage_complete,
                    "lineage_complete": inferred_lineage_complete,
                    "failure_class": None if inferred_lineage_complete else "LINEAGE_LOST",
                    "resolution_source": "single_document_prompt_context",
                }

        bindings: list[dict[str, Any]] = []
        all_slot_formats_valid = bool(parsed.valid)
        all_slot_results_match = bool(parsed.valid)
        all_slot_bindings_valid = bool(parsed.valid)
        aggregate_binding_reasons: list[str] = []
        question_scale = decimal_scale_from_question(bundle.question.text)
        for index, item in enumerate(parsed.answer_items, start=1):
            expected_slot_contract = (
                slot_contracts[index - 1] if index <= len(slot_contracts) else {}
            )
            kind = str(item.get("kind") or "text")
            expected_kind = str(
                expected_slot_contract.get("expected_kind") or kind
            ).strip().lower()
            value = str(item.get("value") or "")
            format_valid = item.get("format_valid") is True
            format_reason = str(item.get("format_reason") or "")
            formula_text = str(item.get("formula_text") or "").strip()
            raw_variables = item.get("variables")
            variables: Dict[str, float] = {}
            if isinstance(raw_variables, Mapping):
                for name, raw_value in raw_variables.items():
                    try:
                        variables[str(name)] = float(raw_value)
                    except (TypeError, ValueError):
                        continue

            evidence_refs_raw = item.get("evidence_refs")
            evidence_ref_normalization = normalize_solver_evidence_refs(
                evidence_refs_raw
                if isinstance(evidence_refs_raw, Sequence)
                and not isinstance(evidence_refs_raw, (str, bytes))
                else (),
                bundle,
            )
            evidence_refs = list(evidence_ref_normalization["normalized_refs"])
            evidence_lineage_valid = bool(
                evidence_ref_normalization["all_resolved"]
                and evidence_ref_normalization["lineage_complete"]
            )

            deterministic_result = None
            calculation_error = None
            python_code = None
            formula_execution_valid: bool | None = None
            submitted_numeric = None
            model_numeric = None
            python_numeric = None
            python_display_numeric = None
            submitted_vs_python_match: bool | None = None
            model_vs_python_match: bool | None = None
            rounded_expected_value = None
            explicit_scale = expected_slot_contract.get("expected_decimal_places")
            comparison_scale = (
                explicit_scale
                if isinstance(explicit_scale, int) and not isinstance(explicit_scale, bool)
                else question_scale
            )
            contract_percentage_semantics = str(
                expected_slot_contract.get("percentage_result_semantics") or ""
            ).strip().lower()
            model_percentage_semantics = str(
                item.get("percentage_result_semantics") or ""
            ).strip().lower()
            percentage_semantics = (
                contract_percentage_semantics or model_percentage_semantics
            )
            blocking_reasons: list[str] = []

            if not format_valid:
                blocking_reasons.append("freeform_kind_validation_failed")
            if kind != expected_kind:
                blocking_reasons.append("freeform_model_kind_mismatch")
            if (
                expected_kind == "percentage"
                and contract_percentage_semantics
                and model_percentage_semantics
                and model_percentage_semantics != contract_percentage_semantics
            ):
                blocking_reasons.append("freeform_percentage_semantics_mismatch")
            lineage_failure_class = evidence_ref_normalization.get("failure_class")
            if lineage_failure_class == "MISSING_EVIDENCE":
                blocking_reasons.append("freeform_slot_binding_missing")
            elif lineage_failure_class == "LINEAGE_REF_FORMAT_MISMATCH":
                blocking_reasons.append("freeform_evidence_ref_format_mismatch")
            elif lineage_failure_class == "LINEAGE_LOST":
                blocking_reasons.append("freeform_evidence_lineage_lost")

            if expected_kind in {"number", "percentage", "percentage_point"}:
                submitted_decimal = parse_finite_decimal(
                    value, percentage=(expected_kind == "percentage")
                )
                submitted_numeric = (
                    str(submitted_decimal) if submitted_decimal is not None else None
                )
                model_decimal = _finite_decimal(item.get("computed_result"))
                model_numeric = str(model_decimal) if model_decimal is not None else None

                formula_normalization = self.normalize_formula_variables(
                    formula_text, variables
                )
                if formula_normalization.get("status") != "normalized":
                    formula_execution_valid = False
                    calculation_error = str(
                        formula_normalization.get("reason")
                        or "formula_variable_normalization_failed"
                    )
                    blocking_reasons.append("freeform_formula_execution_failed")
                else:
                    execution_formula = str(formula_normalization["normalized_formula"])
                    execution_variables = dict(formula_normalization["normalized_variables"])
                    prepared, validation_error = self._prepare_formula(
                        execution_formula, execution_variables
                    )
                    if validation_error:
                        formula_execution_valid = False
                        calculation_error = validation_error
                        blocking_reasons.append("freeform_formula_execution_failed")
                    else:
                        python_code = self._build_eval_code(prepared, execution_variables)
                        deterministic_result, calculation_error = self._run_python(python_code)
                        formula_execution_valid = bool(
                            deterministic_result is not None and not calculation_error
                        )
                        if not formula_execution_valid:
                            blocking_reasons.append("freeform_formula_execution_failed")

                python_decimal = _finite_decimal(deterministic_result)
                python_numeric = str(python_decimal) if python_decimal is not None else None
                if formula_execution_valid and python_decimal is not None:
                    if expected_kind == "percentage":
                        if percentage_semantics not in _PERCENTAGE_RESULT_SEMANTICS:
                            blocking_reasons.append(
                                "freeform_percentage_semantics_ambiguous"
                            )
                        expected_display_decimal = (
                            python_decimal * Decimal("100")
                            if percentage_semantics == "ratio"
                            else python_decimal
                        )
                    else:
                        expected_display_decimal = python_decimal

                    if not (
                        expected_kind == "percentage"
                        and percentage_semantics not in _PERCENTAGE_RESULT_SEMANTICS
                    ):
                        python_display_numeric = str(expected_display_decimal)
                        rounded_expected_value = format_decimal_for_submission(
                            expected_display_decimal,
                            scale=comparison_scale,
                            percentage=(expected_kind == "percentage"),
                            preserve_scale=None,
                        )
                        if comparison_scale is not None:
                            submitted_vs_python_match = bool(
                                format_valid and value == rounded_expected_value
                            )
                        else:
                            submitted_vs_python_match = bool(
                                format_valid
                                and submitted_decimal is not None
                                and abs(submitted_decimal - expected_display_decimal)
                                <= _FREEFORM_MODEL_PYTHON_TOLERANCE
                            )
                        if not submitted_vs_python_match:
                            blocking_reasons.append(
                                "freeform_submission_result_mismatch"
                            )

                    model_vs_python_match = bool(
                        model_decimal is not None
                        and abs(model_decimal - python_decimal)
                        <= _FREEFORM_MODEL_PYTHON_TOLERANCE
                    )
                    if not model_vs_python_match:
                        blocking_reasons.append(
                            "freeform_model_python_result_mismatch"
                        )
                else:
                    submitted_vs_python_match = False
                    model_vs_python_match = False

            slot_results_match = bool(
                expected_kind not in {"number", "percentage", "percentage_point"}
                or (
                    submitted_vs_python_match is True
                    and model_vs_python_match is True
                    and formula_execution_valid is True
                )
            )
            binding_valid = bool(
                format_valid
                and evidence_refs
                and evidence_lineage_valid
                and slot_results_match
                and not blocking_reasons
            )
            all_slot_formats_valid = all_slot_formats_valid and format_valid
            all_slot_results_match = all_slot_results_match and slot_results_match
            all_slot_bindings_valid = all_slot_bindings_valid and binding_valid
            aggregate_binding_reasons.extend(blocking_reasons)
            bindings.append({
                "slot": index,
                "value": value,
                "kind": kind,
                "expected_kind": expected_kind,
                "model_kind_matches_expected": kind == expected_kind,
                "expected_slot_contract": dict(expected_slot_contract),
                "format_valid": format_valid,
                "format_reason": format_reason,
                "formula_text": formula_text,
                "variables": variables,
                "formula_variable_normalization": (
                    formula_normalization
                    if expected_kind in {"number", "percentage", "percentage_point"}
                    else None
                ),
                "normalized_formula": (
                    formula_normalization.get("normalized_formula")
                    if expected_kind in {"number", "percentage", "percentage_point"}
                    else None
                ),
                "normalized_variables": (
                    formula_normalization.get("normalized_variables")
                    if expected_kind in {"number", "percentage", "percentage_point"}
                    else None
                ),
                "formula_variable_map": (
                    formula_normalization.get("original_to_safe")
                    if expected_kind in {"number", "percentage", "percentage_point"}
                    else None
                ),
                "formula_variable_reverse_map": (
                    formula_normalization.get("safe_to_original")
                    if expected_kind in {"number", "percentage", "percentage_point"}
                    else None
                ),
                "model_computed_result": item.get("computed_result"),
                "deterministic_result": deterministic_result,
                "python_code": python_code,
                "calculation_error": calculation_error,
                "formula_execution_valid": formula_execution_valid,
                "evidence_refs_raw": list(evidence_ref_normalization["raw_refs"]),
                "evidence_refs": evidence_refs,
                "evidence_ref_normalization": evidence_ref_normalization,
                "evidence_lineage": evidence_ref_normalization["lineage_by_doc"],
                "lineage_failure_class": lineage_failure_class,
                "submitted_numeric_value": submitted_numeric,
                "model_computed_numeric_value": model_numeric,
                "python_deterministic_numeric_value": python_numeric,
                "python_display_numeric_value": python_display_numeric,
                "comparison_scale": comparison_scale,
                "comparison_tolerance": str(_FREEFORM_MODEL_PYTHON_TOLERANCE),
                "rounded_expected_value": rounded_expected_value,
                "percentage_result_semantics": percentage_semantics or None,
                "model_percentage_result_semantics": model_percentage_semantics or None,
                "submitted_vs_python_match": submitted_vs_python_match,
                "model_vs_python_match": model_vs_python_match,
                "binding_valid": binding_valid,
                "binding_auditable": binding_valid,
                "blocking_reasons": sorted(set(blocking_reasons)),
            })

        truncated = response.finish_reason == "length"
        aggregate_binding_reasons = sorted(set(aggregate_binding_reasons))
        lineage_failures = [
            str(used_doc_normalization.get("failure_class") or ""),
            *[
                str(binding.get("lineage_failure_class") or "")
                for binding in bindings
            ],
        ]
        if not parsed.valid or truncated:
            failure_class = "MODEL_OUTPUT_INVALID"
        elif "LINEAGE_REF_FORMAT_MISMATCH" in lineage_failures:
            failure_class = "LINEAGE_REF_FORMAT_MISMATCH"
        elif "LINEAGE_LOST" in lineage_failures:
            failure_class = "LINEAGE_LOST"
        elif "MISSING_EVIDENCE" in lineage_failures:
            failure_class = "MISSING_EVIDENCE"
        elif not all_slot_bindings_valid:
            failure_class = "BINDING_FAILED"
        else:
            failure_class = None
        complete = bool(
            parsed.valid
            and all_slot_bindings_valid
            and used_doc_lineage_valid
            and not truncated
        )
        if not complete and failure_class is None:
            failure_class = "BINDING_FAILED"
        metadata = {
            "model": response.model,
            "provider": response.provider,
            "provider_call_count": 1,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "latency_ms": response.usage.latency_ms,
            "finish_reason": response.finish_reason,
            "truncation_risk": truncated,
            "freeform_contract": "multi_slot_structured_v2",
            "freeform_parse_valid": parsed.valid,
            "freeform_parse_reason": parsed.reason,
            "freeform_parse": parsed.to_dict(),
            "submission_answers": list(parsed.answers) if parsed.valid else [],
            "submission_answer_kinds": list(parsed.kinds),
            "expected_submission_slots": slot_count,
            "freeform_answer_bindings": bindings,
            "freeform_slot_bindings": bindings,
            "freeform_all_slot_formats_valid": all_slot_formats_valid,
            "freeform_all_slot_results_match": all_slot_results_match,
            "freeform_all_slot_bindings_valid": all_slot_bindings_valid,
            "freeform_binding_blocking_reasons": aggregate_binding_reasons,
            "freeform_binding_auditable": all_slot_bindings_valid,
            "computation_complete": complete,
            "computation_grounded": complete,
            "computation_status": "complete" if complete else "blocked",
            "answer_source": "freeform_structured" if parsed.valid else "blocked_freeform_parse",
            "ungrounded": not complete,
            "failure_class": failure_class,
            "evidence_failure_class": failure_class,
            "used_doc_ids_raw": list(used_doc_normalization["raw_refs"]),
            "used_doc_ids": used_doc_ids,
            "used_docs_source": used_docs_source,
            "used_doc_ref_normalization": used_doc_normalization,
            "used_doc_lineage": used_doc_normalization["lineage_by_doc"],
            "used_doc_lineage_valid": used_doc_lineage_valid,
        }
        answer = parsed.answers[0] if parsed.valid else ""
        return SolverResult(
            bundle.question.qid,
            answer,
            self.name,
            str(response.content or ""),
            confidence=parsed.confidence if complete else 0.0,
            metadata=metadata,
        )

    def _solve_insurance_calculation(self, bundle: EvidenceBundle) -> SolverResult | None:
        """Use the production deterministic insurance compiler when configured.

        The route is semantic and source-driven.  It does not inspect fixed
        dataset identifiers or evaluator answers, and it performs no provider
        call.  Recognised-but-incomplete calculations remain fail-closed rather
        than falling back to a model guess.
        """
        metadata = dict(bundle.metadata or {})
        if metadata.get("insurance_calculation_verification_enabled") is not True:
            return None
        if bundle.question.domain != "insurance":
            return None
        full_text_root = str(metadata.get("insurance_calculation_full_text_root") or "").strip()
        catalog_path = str(metadata.get("insurance_calculation_product_catalog_path") or "").strip()
        if not full_text_root or not catalog_path:
            return None
        compiler = InsuranceCalculationCompiler(
            full_text_root,
            product_catalog_path=catalog_path,
        )
        audit = compiler.compile(bundle.question)
        if audit.get("recognized") is not True:
            return None
        option_evaluations = list(audit.get("option_evaluations") or [])
        judgments = {}
        for item in option_evaluations:
            label = str(item.get("option") or "").upper()
            verdict = str(item.get("verdict") or "unresolved")
            judgments[label] = (
                "supported" if verdict == "true"
                else "contradicted" if verdict == "false"
                else "unresolved"
            )
        answer = str(audit.get("candidate_answer") or "")
        unique = audit.get("option_match_unique") is True
        calculation_grounding = dict(audit.get("calculation_grounding") or {})
        raw = json.dumps(
            {
                "route": "deterministic_insurance_calculation",
                "answer": answer,
                "calculation_kind": audit.get("calculation_kind"),
                "computed_result": audit.get("computed_result"),
                "option_match_candidates": audit.get("option_match_candidates"),
                "option_match_unique": unique,
            },
            ensure_ascii=False,
        )
        return SolverResult(
            bundle.question.qid,
            answer,
            self.name,
            raw,
            confidence=1.0 if unique else 0.0,
            metadata={
                "deterministic_insurance_calculation": True,
                "insurance_calculation_audit": audit,
                "calculation_kind": audit.get("calculation_kind"),
                "formula_extracted": bool(audit.get("formula")),
                "extracted_formula": audit.get("formula"),
                "extracted_values": dict(audit.get("variables") or {}),
                "computed_result": audit.get("computed_result"),
                "option_evaluations": option_evaluations,
                "judgments": judgments,
                "calculation_grounding": calculation_grounding,
                "computation_performed": audit.get("computed_result") is not None,
                "computation_grounded": audit.get("computation_complete") is True,
                "computation_complete": audit.get("computation_complete") is True,
                "computation_status": "complete" if audit.get("computation_complete") else "blocked",
                "match_based_on_computation": unique,
                "no_unique_option_match": not unique,
                "answer_source": "deterministic_insurance_calculation" if unique else "deterministic_non_unique_block",
                "ungrounded": not unique,
                "used_doc_ids": list(audit.get("used_doc_ids") or []),
                "used_docs_source": "deterministic_insurance_calculation_lineage",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0,
                "truncation_risk": False,
                "structured_parse_failed": False,
                "missing_option_judgments": [
                    label for label, judgment in judgments.items()
                    if judgment == "unresolved"
                ],
            },
        )

    @staticmethod
    def _parse_one_call_payload(raw: str) -> Dict[str, Any]:
        text = str(raw or "").strip()
        candidates = [text]
        fence = chr(96) * 3
        pattern = re.escape(fence) + r"(?:json)?\s*(\{.*?\})\s*" + re.escape(fence)
        fenced = re.search(pattern, text, re.I | re.S)
        if fenced:
            candidates.insert(0, fenced.group(1))
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            candidates.append(text[first:last + 1])
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    def _build_one_call_prompt(self, bundle: EvidenceBundle) -> str:
        options = "\n".join(f"{label}. {text}" for label, text in bundle.question.options.items())
        docs = "\n\n".join(
            f"[DOC:{candidate.doc_id}]\n{candidate.text}"
            for candidate in bundle.candidates
        )
        return textwrap.dedent(f"""
        你是金融计算题求解器。必须在一次响应中完成公式提取、变量绑定、计算和选项判断。
        仅输出一个 JSON 对象，不要输出 Markdown。

        问题：{bundle.question.text}
        选项：
        {options}
        答案格式：{bundle.question.answer_format}

        证据：
        {docs}

        JSON 必须包含：
        {{
          "qid": "{bundle.question.qid}",
          "answer": "A/AB/...",
          "formula_text": "可执行或可审计公式",
          "variables": {{"变量名": 数值}},
          "computed_result": 数值或对象,
          "option_evaluations": [
            {{"option":"A","verdict":"supported|contradicted|unresolved","evidence_refs":["doc_id"],"calculation_refs":["computed_result"]}}
          ],
          "used_doc_ids": ["实际使用的doc_id"],
          "confidence": 0到1
        }}
        对所有选项都给出 option_evaluations。缺证据时使用 unresolved，不得猜测。
        """).strip()

    def _solve_one_call(self, bundle: EvidenceBundle) -> SolverResult:
        stage = "calculation_one_call"
        set_attempt_context(bundle.question.qid, stage)
        prompt = self._build_one_call_prompt(bundle)
        try:
            response = chat_with_fallback(
                self.llm_client,
                self.fallback_llm_client,
                [{"role": "user", "content": prompt}],
                max_tokens=1536,
            )
        except LLMClientUnavailable as exc:
            used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)
            return SolverResult(
                bundle.question.qid, "A", self.name, str(exc), confidence=0.0,
                metadata={
                    "llm_error": True,
                    "calculation_contract": "one-call",
                    "calculation_phase": stage,
                    "formula_extracted": False,
                    "computation_complete": False,
                    "computation_grounded": False,
                    "answer_source": "error",
                    "ungrounded": True,
                    "used_doc_ids": used_doc_ids,
                    "used_docs_source": used_docs_source,
                },
            )

        raw = str(response.content or "")
        payload = self._parse_one_call_payload(raw)
        declared_docs = [str(value) for value in payload.get("used_doc_ids", []) if str(value).strip()] if payload else []
        allowed_docs = set(candidate_doc_ids(bundle))
        used_doc_ids = [value for value in declared_docs if value in allowed_docs]
        if used_doc_ids:
            used_docs_source = "one_call_explicit_model_declaration"
        else:
            used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)

        formula_text = str(payload.get("formula_text") or "").strip() if payload else ""
        raw_variables = payload.get("variables") if payload else {}
        variables: Dict[str, float] = {}
        if isinstance(raw_variables, Mapping):
            for name, value in raw_variables.items():
                try:
                    variables[str(name)] = float(value)
                except (TypeError, ValueError):
                    continue
        computed = None
        calc_error = None
        python_code = None
        if formula_text and variables:
            prepared, validation_error = self._prepare_formula(formula_text, variables)
            if validation_error:
                calc_error = validation_error
            else:
                python_code = self._build_eval_code(prepared, variables)
                computed, calc_error = self._run_python(python_code) if python_code else (None, "no evaluable code")
        else:
            calc_error = "one_call_formula_or_variables_missing"

        answer = normalize_answer(str(payload.get("answer") or raw), bundle.question.answer_format)
        option_evaluations = payload.get("option_evaluations") if isinstance(payload.get("option_evaluations"), list) else []
        formula_extracted = bool(formula_text)
        computation_complete = computed is not None and not calc_error
        calculation_grounding = build_calculation_grounding(
            formula_text=formula_text,
            formula_source_refs=used_doc_ids,
            variables=variables,
            variable_source_refs={name: used_doc_ids for name in variables},
            unit_normalization="model_declared_values_checked_by_python_when_formula_is_executable",
            deterministic_result=computed if computed is not None else payload.get("computed_result"),
            option_evaluations=option_evaluations,
            unresolved_variables=[] if computation_complete else ["formula_or_variables"],
            used_material_variables=sorted(variables),
            unused_material_variables=[],
            coverage_gap=not computation_complete,
            computation_complete=computation_complete,
        )
        confidence_value = payload.get("confidence", 0.0) if payload else 0.0
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = 0.0
        if not computation_complete:
            confidence = min(confidence, 0.25)
        metadata = {
            "model": response.model,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "latency_ms": response.usage.latency_ms,
            "calculation_contract": "one-call",
            "provider_call_stages_expected": [stage],
            "calculation_phase": stage,
            "one_call_payload_parsed": bool(payload),
            "formula_extracted": formula_extracted,
            "extracted_formula": formula_text or None,
            "extracted_values": variables,
            "python_code": python_code,
            "computed_result": computed if computed is not None else payload.get("computed_result"),
            "calc_error": calc_error,
            "option_evaluations": option_evaluations,
            "calculation_grounding": calculation_grounding,
            "computation_performed": computed is not None,
            "computation_grounded": computation_complete,
            "computation_complete": computation_complete,
            "computation_status": "complete" if computation_complete else "failed",
            "match_based_on_computation": computation_complete,
            "answer_source": "one_call_computation" if computation_complete else "one_call_model_proposal",
            "ungrounded": not computation_complete,
            "truncation_risk": response.finish_reason == "length",
            "finish_reason": response.finish_reason,
            "output_chars": len(raw),
            "used_doc_ids": used_doc_ids,
            "used_docs_source": used_docs_source,
        }
        return SolverResult(
            bundle.question.qid, answer, self.name, raw,
            confidence=max(0.0, min(confidence, 1.0)), metadata=metadata,
        )

    def _dry_run(self, bundle: EvidenceBundle) -> SolverResult:
        used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)
        prompt = self._build_prompt(bundle, formula=None, computed=None)
        return SolverResult(
            bundle.question.qid, "A", self.name, "DRY_RUN_NO_LLM_CLIENT",
            confidence=0.0,
            metadata={
                "dry_run": True,
                "prompt_preview": prompt[:1200],
                "formula_extracted": False,
                "computation_performed": False,
                "computation_grounded": False,
                "computation_complete": False,
                "computation_status": "not_attempted",
                "match_based_on_computation": False,
                "answer_source": "dry_run",
                "ungrounded": True,
                # P7E: truncation observability for parity with other solvers.
                "truncation_risk": False,
                "output_chars": 0,
                "used_doc_ids": used_doc_ids,
                "used_docs_source": used_docs_source,
            },
        )

    def _solve_with_python(self, bundle: EvidenceBundle) -> SolverResult:
        """Two-step: extract formula(s), compute, then match."""
        used_doc_ids, used_docs_source = conservative_used_doc_lineage(bundle)
        prompt1 = self._build_extract_prompt(bundle)
        extraction_stage = "calculation_formula_extraction"
        option_matching_stage = "calculation_option_matching"
        set_attempt_context(bundle.question.qid, extraction_stage)
        try:
            result1 = chat_with_fallback(self.llm_client, self.fallback_llm_client,
                [{"role": "user", "content": prompt1}], max_tokens=1024)
        except LLMClientUnavailable as exc:
            return SolverResult(
                bundle.question.qid, "A", self.name, str(exc),
                confidence=0.0,
                metadata={
                    "llm_error": True,
                    "calculation_phase": "extract",
                    "formula_extracted": False,
                    "computation_performed": False,
                    "computation_grounded": False,
                    "computation_complete": False,
                    "computation_status": "failed",
                    "match_based_on_computation": False,
                    "answer_source": "error",
                    "ungrounded": True,
                    # P7E: truncation observability for parity with other solvers.
                    "truncation_risk": False,
                    "output_chars": 0,
                    "used_doc_ids": used_doc_ids,
                    "used_docs_source": used_docs_source,
                },
            )

        raw1 = result1.content
        declared_used_docs = extract_declared_used_doc_ids(raw1, bundle)
        if declared_used_docs:
            used_doc_ids = declared_used_docs
            used_docs_source = "explicit_model_declaration"
        values, value_parse_errors = self._extract_values_with_errors(raw1)

        # Try multi-formula extraction first (for ranking/comparison questions).
        # D-R3: extracted labels are audit labels, not executable variable names.
        # Normalize labels into safe internal symbols before they enter Python
        # assignments; keep the original labels in metadata for review.
        raw_multi_formulas = self._extract_multi_formulas(raw1)
        multi_formulas, formula_label_map = self._normalize_multi_formula_labels(raw_multi_formulas)
        formula_label_originals = {safe: original for original, safe in formula_label_map.items()}
        computed_values: Dict[str, float] = {}
        computed_values_by_label: Dict[str, float] = {}
        multi_formula_error: Optional[str] = None
        multi_formula_used = False
        # Per-original-label status so ranking/partial-evaluation failures are visible:
        # {"产品A": "ok", "合计": "<error>"}. Required by P7B-0/D-R3 audit.
        multi_formula_status: Dict[str, str] = {}
        alias_bindings: Dict[str, str] = {}
        alias_bindings_by_formula: Dict[str, Dict[str, str]] = {}

        if multi_formulas:
            multi_formula_used = True
            for safe_label, formula in multi_formulas.items():
                original_label = formula_label_originals.get(safe_label, safe_label)
                contextual_aliases, contextual_bindings = self._contextual_alias_values(
                    original_label, values, computed_values, formula_label_originals
                )
                if contextual_bindings:
                    alias_bindings_by_formula[original_label] = dict(contextual_bindings)
                    alias_bindings_by_formula[safe_label] = dict(contextual_bindings)
                alias_bindings.update(contextual_bindings)
                # Inject previously computed values as variables so later formulas
                # (e.g. 合计) can reference earlier product results by name.
                merged_values = {
                    **values,
                    **computed_values,
                    **self._computed_aliases(computed_values, formula_label_originals),
                    **contextual_aliases,
                }
                # P7B-3/D-R3: deterministically reject formulas that reference
                # symbols not defined in 变量: / computed_values / aliases, so the
                # failure is observable with a clear reason instead of a raw
                # subprocess NameError.
                prepared_formula, validation_error = self._prepare_formula(formula, merged_values)
                if validation_error:
                    multi_formula_error = validation_error
                    multi_formula_status[original_label] = validation_error
                    continue
                code = self._build_eval_code(prepared_formula, merged_values)
                if code:
                    val, err = self._run_python(code)
                    if err:
                        multi_formula_error = err
                        multi_formula_status[original_label] = err
                    else:
                        computed_values[safe_label] = val
                        computed_values_by_label[original_label] = val
                        multi_formula_status[original_label] = "ok"
                else:
                    multi_formula_status[original_label] = "no evaluable code"

        # Fallback to single-formula path if multi-formula failed or not found
        formula = self._extract_formula(raw1) if not computed_values else None
        computed: Optional[float] = None
        calc_error: Optional[str] = None
        eval_code = None

        if not computed_values:
            if formula:
                # P7B-3: deterministically reject formulas referencing undefined
                # symbols before spawning a Python subprocess. This converts the
                # dominant P7B-2 failure (NameError on e.g. 赔付比例) into a clear,
                # observable calc_error without a wasted process and without
                # changing the downstream grounding semantics (still ungrounded).
                prepared_formula, validation_error = self._prepare_formula(formula, values)
                if validation_error:
                    calc_error = validation_error
                else:
                    eval_code = self._build_eval_code(prepared_formula, values)
                    computed, calc_error = self._run_python(eval_code) if eval_code else (None, "no evaluable code")
            else:
                calc_error = "no formula extracted"

        # Determine extraction mode
        extraction_mode = "multi_formula" if multi_formula_used and computed_values else "single_formula"

        # ── P7B-1 explicit grounding / completeness state ──────────────
        # P7B-0 made the structural P7A failures observable. P7B-1 makes the
        # solver's grounding state *explicit and complete* so Evaluator/Reviewer
        # can reject or route follow-up work without an LLM-in-the-loop judgment:
        #   * zero-computation answers are never presented as grounded;
        #   * partial multi-formula evaluation is never treated as complete;
        #   * the match step declares whether it used real computed results.
        formula_extracted = bool(formula) or bool(multi_formulas)
        computation_performed = computed is not None or bool(computed_values)
        computation_grounded = computation_performed

        # Completeness: did computation cover everything that was extracted?
        if multi_formula_used and multi_formulas:
            computation_complete = len(computed_values) == len(multi_formulas)
        else:
            computation_complete = computation_performed

        # Whether the match step had real computed results to match against.
        match_based_on_computation = computed is not None or bool(computed_values)
        if computed_values_by_label and "合计" not in computed_values_by_label and self._should_synthesize_insurance_total(computed_values_by_label):
            computed_values_by_label["合计"] = float(sum(computed_values_by_label.values()))
        display_computed_values = computed_values_by_label if computed_values_by_label else computed_values

        # Explicit answer provenance + ungrounded marker.
        if not computation_grounded:
            # No Python computation backs the answer (no formula, or eval failed).
            # The match LLM may still emit a text-guess letter for pipeline/score
            # compatibility, but it is explicitly NOT a grounded calculation.
            answer_source = "llm_text_guess"
            ungrounded = True
        elif not computation_complete:
            # Some computation happened (e.g. multi-formula partial), but it does
            # not cover every extracted formula — do not treat it as complete.
            answer_source = "computation_partial"
            ungrounded = False
        else:
            answer_source = "computation"
            ungrounded = False

        # Confidence reflects completeness, not just presence of any math:
        #   complete computation -> 1.0
        #   partial computation  -> 0.5  (visible incompleteness)
        #   zero computation     -> 0.0
        confidence = (
            1.0 if computation_complete
            else (0.5 if computation_grounded else 0.0)
        )

        # ── Lane 4: computation status and variable alias map ──────────
        # Derive a single-status string from the existing boolean flags so
        # the diagnostic module does not need to re-derive it.
        if not computation_grounded:
            computation_status = "not_attempted" if not formula_extracted else "failed"
        elif not computation_complete:
            computation_status = "partial"
        else:
            computation_status = "complete"

        # Variable alias map: traceability from evidence text → variable name.
        # This maps each extracted variable name back to the value used in the
        # computation, so a Reviewer can verify the variable assignment without
        # re-reading the extraction prompt output.
        variable_alias_map: Dict[str, Dict[str, Any]] = {}
        for var_name, var_value in values.items():
            variable_alias_map[var_name] = {
                "value": var_value,
                "type": "float",
                "source": "extraction_llm",
                "note": "",
            }
        for err_var, err_msg in (value_parse_errors or {}).items():
            if err_var not in variable_alias_map:
                variable_alias_map[err_var] = {
                    "value": None,
                    "type": "error",
                    "source": "extraction_llm",
                    "note": err_msg,
                }

        formulas_by_label = {
            formula_label_originals.get(safe_label, safe_label): formula_text
            for safe_label, formula_text in (multi_formulas or {}).items()
        }
        used_material_variables, unused_material_variables = self._material_variable_coverage(
            formulas=(list(multi_formulas.values()) if multi_formulas else ([formula] if formula else [])),
            extracted_values=values,
            computed_values=computed_values,
            alias_bindings=alias_bindings,
            formulas_by_label=formulas_by_label if formulas_by_label else None,
            alias_bindings_by_formula=alias_bindings_by_formula if alias_bindings_by_formula else None,
        )
        non_material_variables: list[str] = []
        non_material_variable_reasons: Dict[str, str] = {}

        calc_meta: Dict[str, Any] = {
            "model": result1.model,
            "prompt_tokens": result1.usage.prompt_tokens,
            "completion_tokens": result1.usage.completion_tokens,
            "total_tokens": result1.usage.total_tokens,
            "latency_ms": result1.usage.latency_ms,
            "calculation_phase": "extract",
            "provider_call_stages_expected": [extraction_stage, option_matching_stage],
            "first_call_stage": extraction_stage,
            "second_call_stage": option_matching_stage,
            "extracted_formula": formula,
            "extracted_formulas": raw_multi_formulas if raw_multi_formulas else None,
            "normalized_extracted_formulas": multi_formulas if multi_formulas else None,
            "formula_label_map": formula_label_map if formula_label_map else None,
            "variable_alias_bindings": alias_bindings if alias_bindings else None,
            "variable_alias_bindings_by_formula": alias_bindings_by_formula if alias_bindings_by_formula else None,
            "extracted_values": values,
            "value_parse_errors": value_parse_errors or None,
            "python_code": eval_code,
            "computed_result": computed if computed is not None else (list(display_computed_values.values())[0] if display_computed_values else None),
            "calc_error": calc_error,
            "computed_values": display_computed_values if display_computed_values else None,
            "internal_computed_values": computed_values if computed_values and computed_values != display_computed_values else None,
            "multi_formula_used": multi_formula_used,
            "multi_formula_error": multi_formula_error,
            "extraction_mode": extraction_mode,
            "formula_extracted": formula_extracted,
            "computation_performed": computation_performed,
            "computation_grounded": computation_grounded,
            "computation_complete": computation_complete,
            # Lane 4: unified computation status string.
            "computation_status": computation_status,
            # Lane 4: variable alias map for traceability.
            "variable_alias_map": variable_alias_map,
            "used_material_variables": used_material_variables,
            "unused_material_variables": unused_material_variables,
            "non_material_variables": non_material_variables,
            "non_material_variable_reasons": non_material_variable_reasons,
            "unresolved_material_variables": sorted(str(name) for name in (value_parse_errors or {}).keys()),
            "material_variable_coverage": {
                "used": used_material_variables,
                "unused": unused_material_variables,
                "non_material": non_material_variables,
                "non_material_reasons": non_material_variable_reasons,
                "unresolved": sorted(str(name) for name in (value_parse_errors or {}).keys()),
                "alias_bindings_by_formula": alias_bindings_by_formula if alias_bindings_by_formula else None,
            },
            "match_based_on_computation": match_based_on_computation,
            "answer_source": answer_source,
            "ungrounded": ungrounded,
            "multi_formula_expected_count": len(multi_formulas),
            "multi_formula_evaluated_count": len(computed_values),
            "multi_formula_status": multi_formula_status if multi_formulas else None,
            # P7E: truncation observability. The extract LLM call is where
            # truncation could hide formula/variable definitions and silently
            # degrade computation. Expose it so a truncated extraction is never
            # mistaken for a clean grounded calculation.
            "truncation_risk": result1.finish_reason == "length",
            "extract_finish_reason": result1.finish_reason,
            "extract_output_chars": len(raw1 or ""),
            "output_chars": len(raw1 or ""),
            "used_doc_ids": used_doc_ids,
            "used_docs_source": used_docs_source,
        }

        # B-R2: a structure-aware formula supplement may already provide a
        # decisive percentage clause even when the extraction LLM fails to emit
        # an executable formula. Match the unique option containing the same
        # percentage anchor instead of returning an ungrounded text guess.
        clause_match = self._match_supplemented_percentage_option(bundle)
        if not computation_grounded and clause_match is not None:
            option, anchor, source = clause_match
            calc_meta.update({
                "answer_source": "grounded_clause_match",
                "ungrounded": False,
                "deterministic_clause_match": True,
                "deterministic_clause_anchor": anchor,
                "deterministic_clause_source": source,
                "match_based_on_computation": False,
                "calculation_phase": "clause_match",
            })
            return SolverResult(
                bundle.question.qid,
                option,
                self.name,
                f"deterministic clause match: {anchor} -> {option}",
                confidence=1.0,
                metadata=calc_meta,
            )

        # Step 2: match computed result against options.
        # Even when computation is absent (zero-computation guard, P7B-1 goal 1)
        # the match LLM still runs so a text-guess letter is preserved for
        # pipeline/score compatibility — but the result is explicitly marked
        # ungrounded via answer_source / ungrounded / confidence above, never as
        # a grounded calculation result.
        prompt2 = self._build_prompt(bundle, formula=formula, computed=computed,
                                     raw_extract=raw1, computed_values=display_computed_values)
        set_attempt_context(bundle.question.qid, option_matching_stage)
        try:
            result2 = chat_with_fallback(self.llm_client, self.fallback_llm_client,
                                         [{"role": "user", "content": prompt2}], max_tokens=512)
        except LLMClientUnavailable as exc:
            calc_meta["match_error"] = str(exc)
            calc_meta["calculation_phase"] = "match"
            calc_meta["answer_source"] = "error"
            calc_meta["ungrounded"] = True
            calc_meta["computation_complete"] = False
            return SolverResult(bundle.question.qid, "A", self.name, str(exc),
                                confidence=0.0, metadata=calc_meta)

        answer = normalize_answer(result2.content, bundle.question.answer_format)
        match_text = str(result2.content or "")
        match_truncated = result2.finish_reason == "length"
        match_explicit = self._match_output_has_explicit_answer(match_text, bundle.question.answer_format)
        match_output_untrusted = bool(match_truncated or not match_explicit)
        no_unique_option_match = any(
            marker in match_text.lower()
            for marker in (
                "没有任何一个选项", "无任何一个选项", "没有选项",
                "无法唯一", "不能唯一", "none of the options",
                "no option uniquely", "cannot uniquely",
            )
        ) or (match_output_untrusted and not computation_complete)
        deterministic_result = computed if computed is not None else display_computed_values
        option_evaluations = build_option_evaluations_from_conditions(
            deterministic_result=deterministic_result,
            option_conditions=bundle.question.options,
            evidence_refs=used_doc_ids,
            calculation_refs=["computed_result"],
        )
        if not computation_complete:
            unresolved_reason = calc_error or multi_formula_error or "computation incomplete"
            option_evaluations = [
                {
                    **item,
                    "verdict": "unresolved",
                    "evaluated_value": None,
                    "unresolved_reason": unresolved_reason,
                }
                for item in option_evaluations
            ]
        unresolved_variables = []
        if not computation_complete:
            unresolved_variables.extend(sorted(str(name) for name in (value_parse_errors or {}).keys()))
            if not unresolved_variables:
                unresolved_variables.append("formula_or_option_match")
        calculation_grounding = build_calculation_grounding(
            formula_text=formula or "; ".join(str(value) for value in (raw_multi_formulas or {}).values()),
            formula_source_refs=used_doc_ids,
            variables=values,
            variable_source_refs={name: used_doc_ids for name in values},
            unit_normalization="percent_literals_normalized_to_decimal_when_present",
            deterministic_result=deterministic_result,
            option_evaluations=option_evaluations,
            unresolved_variables=unresolved_variables,
            used_material_variables=used_material_variables,
            unused_material_variables=unused_material_variables,
            coverage_gap=False,
            computation_complete=computation_complete,
        )
        grounding_option_match = calculation_grounding.get("option_match") if isinstance(calculation_grounding, Mapping) else None
        grounding_unique = bool(calculation_grounding.get("option_match_unique")) if isinstance(calculation_grounding, Mapping) else False
        match_output_ignored_by_grounding = bool(computation_complete and grounding_unique and grounding_option_match)
        if match_output_ignored_by_grounding:
            answer = str(grounding_option_match)
        elif match_output_untrusted:
            calc_meta["answer_source"] = "unsupported_guess_truncated" if match_truncated else "unsupported_guess"
            calc_meta["ungrounded"] = True
            confidence = 0.0

        calc_meta.update({
            "calculation_phase": "match",
            "match_raw": result2.content,
            "match_prompt_tokens": result2.usage.prompt_tokens,
            "match_completion_tokens": result2.usage.completion_tokens,
            "match_tokens": result2.usage.total_tokens,
            "prompt_tokens": result1.usage.prompt_tokens + result2.usage.prompt_tokens,
            "completion_tokens": result1.usage.completion_tokens + result2.usage.completion_tokens,
            "total_tokens": result1.usage.total_tokens + result2.usage.total_tokens,
            # P7E: match-phase truncation observability. A truncated match output
            # may produce an invalid or partial answer letter that is not backed
            # by the computed result.
            "match_finish_reason": result2.finish_reason,
            "match_truncation_risk": bool(result2.finish_reason == "length" and not match_output_ignored_by_grounding),
            "match_output_explicit_answer": match_explicit,
            "match_output_untrusted": match_output_untrusted,
            "match_output_ignored_by_grounding": match_output_ignored_by_grounding,
            "no_unique_option_match": no_unique_option_match or not calculation_grounding.get("option_match_unique", False),
            "calculation_grounding": calculation_grounding,
            "match_output_chars": len(result2.content or ""),
        })
        return SolverResult(bundle.question.qid, answer, self.name, result2.content,
                            confidence=confidence, metadata=calc_meta)

    # ── extraction ──────────────────────────────────────────────────

    def _build_extract_prompt(self, bundle: EvidenceBundle) -> str:
        available_docs = ", ".join(candidate_doc_ids(bundle)) or "无"
        return (
            "你是金融计算题公式提取器。你的任务是从证据中提取出计算公式和数值，\n"
            f"第一行必须输出：使用文档：<实际用于公式和变量的文档ID，逗号分隔>。可用文档ID：{available_docs}。"
            "用于 Python 计算。不要直接计算，只提取公式和数值。\n\n"
            f"{render_question(bundle)}\n\n"
            f"证据：\n{bundle.prompt_context}\n\n"
            "【输出格式规则——必须严格遵守】\n"
            "1. 如果题目涉及多个产品/文档的比较或排序，为每个产品输出一行公式，用方括号标注产品名：\n"
            "公式[产品A]：a + b * c\n"
            "公式[产品B]：d - e\n"
            "公式[产品C]：f * g\n"
            "公式[合计]：产品A + 产品B + 产品C\n\n"
            "2. 如果只有一个计算，直接输出：\n"
            "公式：a + b - c\n\n"
            "3. 然后输出变量（变量名用英文或拼音）：\n"
            "变量：\n"
            "a = 100000\n"
            "b = 20000\n"
            "c = 0.75\n\n"
            "4. 最后输出数值说明：\n"
            "数值说明：a=保费, b=收益, c=分配比例\n\n"
            "【重要】\n"
            "- 第一行必须以'公式'开头。\n"
            "- 表达式只允许字母、数字、+-*/()%. 和空格。不要用反引号或LaTeX。\n"
            "- 变量名用英文或拼音，不要用中文。\n"
            "- 百分比直接写，如100%或75%，不要转换。\n"
            "- 公式中引用的每一个变量都必须在下面的'变量：'区块中定义，或直接写成具体数值。\n"
            "  不要引用未定义的名称（例如不要在公式里写'赔付比例'却在变量区不赋值）。\n"
            "- 多产品题中，'合计'等汇总公式只能引用前面已列出的产品名作为变量。\n"
            "- 函数名必须用 Python 小写形式：max、min、abs。不要写 MAX/MIN/ABS。\n"
            "- 多项求和直接写成 a + b + c；不要输出 SUM(...) 或 sum(...)。\n"
            "- 若题干或证据明确给出‘自付金额/个人自付/自费金额’，该数值视为医保报销后的净额，"
            "不得再次减去医保报销金额；只有明确写‘医疗总费用/原始费用’时才扣减医保报销。\n"
            "- 多成员、多保险产品题必须先用已定义变量计算每位成员净自付和家庭净自付总额，"
            "再分别计算每个保险产品赔付；除非条款明确约定，不能用一个保险产品的赔付额去冲减另一个产品的赔付基数。\n"
            "- 汇总公式必须引用前面方括号中的精确标签；不要临时创造‘总实际赔付’等未定义符号。"
        )

    @staticmethod
    def _extract_formula(text: str) -> Optional[str]:
        """Extract a Python-evaluable formula after '公式：' or '公式:'.

        Handles LLM outputs that may include:
        - markdown list markers (*, -), backticks, LaTeX $...$ delimiters
        - \\times notation
        - Chinese variable names (Python 3 supports Unicode identifiers)
        - comma (for max/min functions)
        - assignment form (var = expr) — extracts the expr part
        """
        for line in text.splitlines():
            stripped = line.strip()
            for prefix in ("公式：", "公式:"):
                idx = stripped.find(prefix)
                if idx >= 0:
                    expr = stripped[idx + len(prefix):].strip()
                    # Remove markdown list markers only at line start (* or - followed by space)
                    expr = re.sub(r"^[\*\-]\s+", "", expr)
                    # Remove backticks and $ delimiters, but NOT * (multiplication)
                    expr = expr.replace("`", "").replace("$", "").strip()
                    # Remove markdown bold **...** but keep standalone *
                    expr = re.sub(r"\*\*(.+?)\*\*", r"\1", expr)
                    # Convert LaTeX operators to Python
                    expr = expr.replace("\\times", "*").replace("\\div", "/")
                    expr = expr.replace("\\cdot", "*").replace("\\frac", "")
                    # Remove remaining backslash commands
                    expr = re.sub(r"\\[a-zA-Z]+", "", expr).strip()
                    # Remove trailing punctuation
                    expr = expr.rstrip("。.,，")
                    # Handle assignment form: "var = expr" -> take expr
                    if "=" in expr and not expr.startswith("="):
                        parts = expr.split("=", 1)
                        # Only treat as assignment if left side is a simple var name
                        left = parts[0].strip()
                        if re.match(r"^[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*$", left):
                            expr = parts[1].strip()
                    # P7B-3: normalize uppercase math functions to Python builtins
                    # (MAX->max, etc.) so formulas are executable, not NameError-prone.
                    expr = CalculationSolver._normalize_function_names(expr)
                    # Safety: allow letters (incl. Chinese), digits, operators, parens, comma, dot, space, underscore
                    # Use regex for Unicode-safe check
                    if expr and re.match(r"^[a-zA-Z0-9_\u4e00-\u9fff+\-*/().%, ]+$", expr):
                        return expr
        return None

    @staticmethod
    def _extract_multi_formulas(text: str) -> Dict[str, str]:
        """Extract multiple labeled formulas like '公式[产品名]：expr'.

        Returns dict {label: formula_expr}. Only returns if 2+ formulas found.
        Falls back to empty dict (triggering single-formula path) otherwise.
        """
        formulas: Dict[str, str] = {}
        # Match: 公式[label]：expr  or  公式[label]: expr
        pattern = re.compile(r"公式[【\[\"'](.+?)[】\]\"']\s*[:：]\s*(.+)")
        for line in text.splitlines():
            stripped = line.strip()
            m = pattern.search(stripped)
            if m:
                label = m.group(1).strip()
                expr = m.group(2).strip()
                # Clean up the expression (same logic as _extract_formula)
                expr = re.sub(r"^[\*\-]\s+", "", expr)
                expr = expr.replace("`", "").replace("$", "").strip()
                expr = re.sub(r"\*\*(.+?)\*\*", r"\1", expr)
                expr = expr.replace("\\times", "*").replace("\\div", "/")
                expr = expr.replace("\\cdot", "*").replace("\\frac", "")
                expr = re.sub(r"\\[a-zA-Z]+", "", expr).strip()
                expr = expr.rstrip("。.,，")
                # Handle assignment form
                if "=" in expr and not expr.startswith("="):
                    parts = expr.split("=", 1)
                    left = parts[0].strip()
                    if re.match(r"^[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*$", left):
                        expr = parts[1].strip()
                # P7B-3: normalize uppercase math functions to Python builtins.
                expr = CalculationSolver._normalize_function_names(expr)
                # Safety check
                if expr and re.match(r"^[a-zA-Z0-9_\u4e00-\u9fff+\-*/().%, ]+$", expr):
                    formulas[label] = expr
        # Only return if 2+ formulas found; otherwise use single-formula path
        return formulas if len(formulas) >= 2 else {}

    @staticmethod
    def _safe_formula_label(label: str, used: set[str] | None = None) -> str:
        """Return a safe internal Python identifier for an audit formula label.

        D-R3: labels such as "2024" are metadata labels, not executable
        assignment targets.  This helper preserves the original label in the
        caller's map while generating a qid-agnostic internal symbol.
        """
        used = used if used is not None else set()
        raw = str(label or "formula").strip()
        compact = re.sub(r"\s+", "_", raw)
        compact = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]", "_", compact).strip("_")
        if not compact:
            compact = "formula"
        if compact[0].isdigit():
            compact = f"formula_{compact}"
        if keyword.iskeyword(compact):
            compact = f"formula_{compact}"
        if not compact.isidentifier():
            compact = "formula_" + re.sub(r"\W+", "_", compact, flags=re.UNICODE).strip("_")
        candidate = compact
        idx = 2
        while candidate in used:
            candidate = f"{compact}_{idx}"
            idx += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def _replace_formula_label_refs(formula: str, label_map: Mapping[str, str]) -> str:
        """Replace exact audit-label references with safe internal symbols.

        Replacement is longest-label-first and uses temporary placeholders so a
        generated safe alias can never be rewritten by a later raw-label rule.
        This matters for collisions such as a-b -> a_b while another raw
        variable is already named a_b. Numeric/year-only labels remain
        literals rather than symbolic references.
        """
        result = str(formula or "")
        ident = r"A-Za-z0-9_\u4e00-\u9fff"
        placeholders: Dict[str, str] = {}
        ordered = sorted(
            ((str(original), str(safe)) for original, safe in label_map.items()),
            key=lambda item: (-len(item[0]), item[0]),
        )
        placeholder_index = 0
        for original, safe in ordered:
            if re.fullmatch(r"\d+(?:\.\d+)?", original):
                continue
            placeholder = f"__afac_formula_ref_{placeholder_index}__"
            while (
                placeholder in result
                or placeholder in label_map
                or placeholder in label_map.values()
            ):
                placeholder_index += 1
                placeholder = f"__afac_formula_ref_{placeholder_index}__"
            placeholder_index += 1

            payout_pattern = rf"(?<![{ident}]){re.escape(original)}赔付(?![{ident}])"
            result, payout_count = re.subn(payout_pattern, placeholder, result)
            pattern = rf"(?<![{ident}]){re.escape(original)}(?![{ident}])"
            result, direct_count = re.subn(pattern, placeholder, result)
            if payout_count or direct_count:
                placeholders[placeholder] = safe

        for placeholder, safe in placeholders.items():
            result = result.replace(placeholder, safe)
        return result

    @staticmethod
    def normalize_formula_variables(
        formula_text: str,
        variables: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize business variable labels before AST validation/execution.

        Original and safe labels are both retained for audit. Safe aliases are
        generated deterministically; unresolved or ambiguous labels fail closed.
        """
        formula = str(formula_text or "").strip()
        base_payload = {
            "normalized_formula": formula,
            "normalized_variables": {},
            "original_to_safe": {},
            "safe_to_original": {},
            "collisions": [],
            "ambiguous_refs": [],
            "unresolved_refs": [],
        }
        if not formula:
            return {**base_payload, "status": "blocked", "reason": "formula_missing"}
        if not isinstance(variables, Mapping) or not variables:
            return {**base_payload, "status": "blocked", "reason": "variables_missing"}

        numeric_variables: Dict[str, float] = {}
        canonical_sources: Dict[str, str] = {}
        ambiguous_refs: list[str] = []
        invalid_values: list[str] = []
        for raw_name, raw_value in variables.items():
            original = str(raw_name)
            canonical = original.strip()
            if not canonical:
                ambiguous_refs.append(original)
                continue
            previous = canonical_sources.get(canonical)
            if previous is not None and previous != original:
                ambiguous_refs.extend([previous, original])
                continue
            canonical_sources[canonical] = original
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                invalid_values.append(canonical)
                continue
            if not math.isfinite(numeric_value):
                invalid_values.append(canonical)
                continue
            numeric_variables[canonical] = numeric_value

        if ambiguous_refs:
            return {
                **base_payload,
                "status": "blocked",
                "reason": "ambiguous_variable_labels",
                "ambiguous_refs": sorted(set(ambiguous_refs)),
            }
        if invalid_values or len(numeric_variables) != len(variables):
            return {
                **base_payload,
                "status": "blocked",
                "reason": "non_finite_or_non_numeric_variable",
                "unresolved_refs": sorted(set(invalid_values)),
            }

        used: set[str] = set()
        original_to_safe: Dict[str, str] = {}
        base_groups: Dict[str, list[str]] = {}
        for original in sorted(numeric_variables):
            base = CalculationSolver._safe_formula_label(original, set())
            base_groups.setdefault(base, []).append(original)
            original_to_safe[original] = CalculationSolver._safe_formula_label(original, used)
        safe_to_original = {safe: original for original, safe in original_to_safe.items()}
        collisions = [
            {"safe_base": base, "original_labels": labels}
            for base, labels in sorted(base_groups.items())
            if len(labels) > 1
        ]
        normalized_variables = {
            original_to_safe[original]: numeric_variables[original]
            for original in sorted(numeric_variables)
        }
        normalized_formula = CalculationSolver._replace_formula_label_refs(
            formula, original_to_safe
        )
        audit = {
            "normalized_formula": normalized_formula,
            "normalized_variables": normalized_variables,
            "original_to_safe": original_to_safe,
            "safe_to_original": safe_to_original,
            "collisions": collisions,
            "ambiguous_refs": [],
            "unresolved_refs": [],
        }
        normalized_for_ast = CalculationSolver._normalize_function_names(
            CalculationSolver._normalize_percent(normalized_formula)
        )
        try:
            tree = ast.parse(normalized_for_ast, mode="eval")
        except SyntaxError as exc:
            return {
                **audit,
                "status": "blocked",
                "reason": f"normalized_formula_syntax_error:{exc.msg}",
            }

        referenced = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        unresolved = sorted(
            referenced - set(normalized_variables) - CalculationSolver._ALLOWED_BUILTINS
        )
        if unresolved:
            return {
                **audit,
                "status": "blocked",
                "reason": "undefined_symbol_after_normalization",
                "unresolved_refs": unresolved,
            }
        return {**audit, "status": "normalized", "reason": "ok"}

    @staticmethod
    def _normalize_multi_formula_labels(formulas: Mapping[str, str]) -> tuple[Dict[str, str], Dict[str, str]]:
        """Normalize multi-formula labels into safe internal symbols.

        Returns (normalized_formulas, original_to_safe_label_map). Original
        labels remain available for audit metadata and human-readable reports.
        """
        if not formulas:
            return {}, {}
        used: set[str] = set()
        label_map = {str(label): CalculationSolver._safe_formula_label(str(label), used) for label in formulas}
        normalized: Dict[str, str] = {}
        for original, formula in formulas.items():
            safe = label_map[str(original)]
            normalized[safe] = CalculationSolver._replace_formula_label_refs(str(formula), label_map)
        return normalized, label_map

    @staticmethod
    def _computed_aliases(
        computed_values: Dict[str, float],
        formula_label_originals: Mapping[str, str] | None = None,
    ) -> Dict[str, float]:
        """Expose stable aliases for computed labels and common insurance names."""
        aliases: Dict[str, float] = {}
        originals = dict(formula_label_originals or {})
        for safe_label, value in computed_values.items():
            label = str(originals.get(safe_label, safe_label))
            compact = re.sub(r"\s+", "", label).lower()
            aliases[safe_label] = value
            aliases[f"{safe_label}_value"] = value
            if "益生保" in compact or "eshengbao" in compact or "e生保" in label:
                aliases.update({
                    "e_sheng_bao_pei_fu": value,
                    "eshengbao_peifu": value,
                    "e_sheng_pay": value,
                    "e生保赔付": value,
                    "益生保赔付": value,
                })
            if "太保" in compact or "cpic" in compact:
                aliases.update({"tai_bao_pei_fu": value, "taibao_peifu": value, "太保赔付": value})
        return aliases

    @staticmethod
    def _should_synthesize_insurance_total(computed_values_by_label: Mapping[str, float]) -> bool:
        labels = [str(label) for label in computed_values_by_label.keys()]
        if len(labels) < 2:
            return False
        product_markers = ("家财", "财产", "e生保", "益生保", "太保", "医疗")
        return any(any(marker in label for marker in product_markers) for label in labels)

    @staticmethod
    def _label_alias_markers(label: str) -> tuple[str, ...]:
        compact = re.sub(r"\s+", "", str(label or "")).lower()
        markers = {compact} if compact else set()
        if "e生保" in compact or "益生保" in compact or "esheng" in compact:
            markers.update({"suffix:e", "eshengbao", "e_sheng", "esheng", "e生保", "益生保"})
        if "太保" in compact or "cpic" in compact or "taibao" in compact:
            markers.update({"suffix:t", "taibao", "tai_bao", "太保", "cpic"})
        if "家财" in compact or "财产" in compact or "property" in compact or "prop" in compact:
            markers.update({"prop", "property", "home", "house", "家财", "家财险", "财产"})
        if "cmcc" in compact or "中国移动" in compact or "chinamobile" in compact:
            markers.update({"cmcc", "中国移动", "chinamobile", "mobile"})
        if "catl" in compact or "宁德时代" in compact:
            markers.update({"catl", "宁德时代"})
        return tuple(sorted(markers, key=len, reverse=True))

    @staticmethod
    def _bind_unique_alias_by_rules(
        values: Mapping[str, float],
        *,
        alias: str,
        metric_tokens: Sequence[str],
        required_tokens: Sequence[str] = (),
        context_tokens: Sequence[str] = (),
    ) -> tuple[float | None, str | None]:
        """Bind an alias to one extracted variable only when the match is unique."""
        candidates: list[tuple[str, float]] = []
        metric_norm = [re.sub(r"[^0-9a-zA-Z一-鿿]+", "", str(x).lower()) for x in metric_tokens]
        required_norm = [re.sub(r"[^0-9a-zA-Z一-鿿]+", "", str(x).lower()) for x in required_tokens]
        raw_context_tokens = [str(x).lower() for x in context_tokens if str(x)]
        context_norm = [
            re.sub(r"[^0-9a-zA-Z一-鿿]+", "", token)
            for token in raw_context_tokens
            if not token.startswith("suffix:")
        ]
        suffix_context_tokens = [token.split(":", 1)[1] for token in raw_context_tokens if token.startswith("suffix:")]
        for name, value in values.items():
            source = str(name)
            source_lower = source.lower()
            norm = re.sub(r"[^0-9a-zA-Z一-鿿]+", "", source_lower)
            metric_ok = any(token and token in norm for token in metric_norm)
            required_ok = all(token in norm for token in required_norm)
            suffix_ok = any(re.search(rf"(^|[^0-9a-zA-Z]){re.escape(token)}($|[^0-9a-zA-Z])", source_lower) for token in suffix_context_tokens)
            context_ok = not raw_context_tokens or suffix_ok or any(token and token in norm for token in context_norm)
            if metric_ok and required_ok and context_ok:
                candidates.append((source, value))
        if len(candidates) == 1:
            return candidates[0][1], candidates[0][0]
        return None, None

    @staticmethod
    def _contextual_alias_values(
        label: str,
        values: Mapping[str, float],
        computed_values: Mapping[str, float] | None = None,
        formula_label_originals: Mapping[str, str] | None = None,
    ) -> tuple[Dict[str, float], Dict[str, str]]:
        """Bind generic variables to evidence-extracted entity-specific values.

        The binding is conservative and auditable: aliases are created only when
        an extracted variable name contains both the current entity/product label
        and the generic metric suffix.  It does not invent missing values.
        """
        aliases: Dict[str, float] = {}
        bindings: Dict[str, str] = {}
        compact_label = re.sub(r"\s+", "", str(label or ""))
        if not compact_label:
            return aliases, bindings
        generic_specs = (
            ("赔付比例", ("赔付比例",)),
            ("免赔额余额", ("免赔额余额",)),
            ("免赔额", ("免赔额",)),
            ("赔付额", ("赔付额",)),
            ("赔付", ("赔付",)),
            ("revenue_2024", ("2024营业收入", "营业收入2024", "2024 revenue", "revenue_2024")),
            ("revenue_2025", ("2025营业收入", "营业收入2025", "2025 revenue", "revenue_2025")),
            ("rd_expense", ("研发费用", "rd_expense", "r&d expense", "research and development expense")),
            ("net_profit_attributable", ("归母净利润", "归属于母公司股东的净利润", "attributable net profit", "net_profit_attributable")),
        )
        for generic, markers in generic_specs:
            if generic in values:
                continue
            matches = []
            for name, value in values.items():
                source_name = str(name)
                compact_source = re.sub(r"\s+", "", source_name).lower()
                entity_ok = compact_label.lower() in compact_source
                marker_ok = any(re.sub(r"\s+", "", marker).lower() in compact_source for marker in markers)
                # Some report variables, such as 宁德时代归母净利润, may be needed
                # while the current calculation label is another entity. Bind them
                # only when the metric marker is globally unique in extracted values.
                if entity_ok and marker_ok:
                    matches.append((source_name, value))
            if not matches and generic == "net_profit_attributable":
                global_matches = []
                for name, value in values.items():
                    source_name = str(name)
                    compact_source = re.sub(r"\s+", "", source_name).lower()
                    if any(re.sub(r"\s+", "", marker).lower() in compact_source for marker in markers):
                        global_matches.append((source_name, value))
                matches = global_matches
            if len(matches) == 1:
                source, value = matches[0]
                aliases[generic] = value
                bindings[generic] = source


        # R4 product-suffixed aliases. LLM extraction often emits generic
        # formulas with 赔付比例 / 免赔额余额 while variables carry product suffixes
        # such as 赔付比例_e_unsettled or 免赔额余额_t. Bind only when the current
        # formula label supplies product context and the extracted source is unique.
        label_markers = CalculationSolver._label_alias_markers(label)
        product_alias_specs = (
            ("赔付比例", ("赔付比例", "ratio", "rate", "payratio", "claimratio"), ()),
            ("免赔额余额", ("免赔额余额", "deductibleremaining", "remainingdeductible", "deductible"), ()),
            ("免赔额", ("免赔额", "deductible"), ()),
            ("实际损失", ("实际损失", "actual_loss", "loss"), ()),
            ("保险金额", ("保险金额", "insurance_amount", "insuredamount", "coverageamount"), ()),
        )
        for alias, metric_tokens, required_tokens in product_alias_specs:
            if alias in values or alias in aliases:
                continue
            value, source = CalculationSolver._bind_unique_alias_by_rules(
                values,
                alias=alias,
                metric_tokens=metric_tokens,
                required_tokens=required_tokens,
                context_tokens=label_markers,
            )
            if source is not None:
                aliases[alias] = value
                bindings[alias] = source

        # D-R8 bilingual insurance aliases.  Some real extraction outputs use
        # Chinese symbols inside formulas while the variable table uses stable
        # English names.  Bind only by unique metric/context rules and keep the
        # selected source variable in alias_bindings_by_formula for audit.
        insurance_global_alias_specs = (
            ("医疗费用", ("医疗费用", "medical_expense", "medicalexpense", "medical"), ()),
            ("其他途径补偿", ("其他途径补偿", "其他补偿", "other_compensation", "compensation"), ()),
            ("其他补偿", ("其他补偿", "other_compensation", "compensation"), ()),
            ("医保报销", ("医保报销", "medical_reimburse", "reimburse", "social"), ()),
        )
        for alias, metric_tokens, required_tokens in insurance_global_alias_specs:
            if alias in values or alias in aliases:
                continue
            value, source = CalculationSolver._bind_unique_alias_by_rules(
                values,
                alias=alias,
                metric_tokens=metric_tokens,
                required_tokens=required_tokens,
                context_tokens=(),
            )
            if source is not None:
                aliases[alias] = value
                bindings[alias] = source


        # D-R3 financial variable aliases. These are evidence-bound because the
        # source must be an extracted variable name carrying the matching metric
        # (and, where present, the requested year/period). The rules are generic
        # and do not patch qid answers.
        financial_rules = {
            "rd_expense": (("rd", "研发", "researchdevelopment", "r_d"), ()),
            "revenue_2024": (("revenue", "营业收入", "营收"), ("2024",)),
            "revenue_2025": (("revenue", "营业收入", "营收"), ("2025",)),
            "net_profit_attributable": (("attributable", "归母", "归属于", "netprofit"), ()),
            "total_cash_dividend": (("dividend", "现金分红", "分红"), ()),
            "total_shares": (("shares", "股本", "股数"), ()),
        }
        for alias, (metric_tokens, required_tokens) in financial_rules.items():
            if alias in values or alias in aliases:
                continue
            # First prefer current label/entity context, then fall back to a
            # globally unique metric/year match. Both paths are unique-match only.
            value, source = CalculationSolver._bind_unique_alias_by_rules(
                values,
                alias=alias,
                metric_tokens=metric_tokens,
                required_tokens=required_tokens,
                context_tokens=label_markers,
            )
            if source is None:
                value, source = CalculationSolver._bind_unique_alias_by_rules(
                    values,
                    alias=alias,
                    metric_tokens=metric_tokens,
                    required_tokens=required_tokens,
                    context_tokens=(),
                )
            if source is not None:
                aliases[alias] = value
                bindings[alias] = source
        # Computed product labels can serve as "<label>赔付" only when the label
        # itself matches; this supports later formulas such as 太保 depending on
        # e生保赔付 without hard-coding qids or answers.
        for alias, value in CalculationSolver._computed_aliases(dict(computed_values or {}), formula_label_originals).items():
            aliases.setdefault(alias, value)
        return aliases, bindings

    @staticmethod
    def _extract_values_with_errors(text: str) -> tuple[Dict[str, float], Dict[str, str]]:
        """Extract base and derived variables with deterministic dependency resolution."""
        values: Dict[str, float] = {}
        errors: Dict[str, str] = {}
        expressions: Dict[str, str] = {}
        in_vars = False
        name_pattern = re.compile(r"^[a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*$")
        number_pattern = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?$")
        expression_pattern = re.compile(r"^[a-zA-Z0-9_一-鿿+\-*/().%, ]+$")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("变量：") or stripped.startswith("变量:"):
                in_vars = True
                continue
            if in_vars and stripped.startswith("数值"):
                break
            if in_vars and "=" in stripped and not stripped.startswith("#"):
                left, right = stripped.split("=", 1)
                var_name = left.strip().split()[0] if left.strip() else ""
                if not var_name or not name_pattern.fullmatch(var_name):
                    errors[var_name or "<empty>"] = "invalid variable name"
                    continue
                raw_value = right.split("#", 1)[0].strip()
                raw_value = raw_value.replace(",", "").replace("，", "")
                token = raw_value.split()[0] if raw_value else ""
                if token and number_pattern.fullmatch(token):
                    is_percent = token.endswith("%")
                    numeric_token = token[:-1] if is_percent else token
                    value = float(numeric_token)
                    values[var_name] = value / 100.0 if is_percent else value
                    continue
                if raw_value and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)%+", raw_value):
                    errors[var_name] = f"invalid numeric value: {raw_value}"
                    continue
                if raw_value and expression_pattern.fullmatch(raw_value):
                    expressions[var_name] = raw_value
                    continue
                errors[var_name] = f"invalid numeric value: {raw_value or '<empty>'}"

        # Resolve expression-valued variables in dependency order. Multiple
        # passes make the outcome independent of the order emitted by the LLM.
        pending = dict(expressions)
        while pending:
            progressed = False
            for var_name, expr in list(pending.items()):
                prepared, validation_error = CalculationSolver._prepare_formula(expr, values)
                if validation_error:
                    if validation_error.startswith("undefined symbol(s):"):
                        continue
                    errors[var_name] = validation_error
                    del pending[var_name]
                    progressed = True
                    continue
                code = CalculationSolver._build_eval_code(prepared, values)
                result, run_error = CalculationSolver._run_python(code) if code else (None, "no evaluable code")
                if run_error:
                    errors[var_name] = run_error
                else:
                    values[var_name] = result
                del pending[var_name]
                progressed = True
            if not progressed:
                for var_name, expr in pending.items():
                    _, validation_error = CalculationSolver._prepare_formula(expr, values)
                    errors[var_name] = validation_error or "unresolved derived variable dependency"
                break
        return values, errors

    @staticmethod
    def _match_supplemented_percentage_option(
        bundle: EvidenceBundle,
    ) -> Optional[tuple[str, str, str]]:
        """Return a unique option matched by a bounded supplemented percentage clause."""
        matches: list[tuple[str, str, str]] = []
        for candidate in bundle.candidates:
            if not candidate.metadata.get("structure_formula_supplement"):
                continue
            anchor = str(candidate.metadata.get("structure_formula_anchor") or "").strip()
            if not anchor or anchor not in candidate.text:
                continue
            option_keys = [
                key for key, option_text in bundle.question.options.items()
                if anchor in str(option_text)
            ]
            if len(option_keys) == 1:
                matches.append((option_keys[0], anchor, candidate.source))
        unique = {(option, anchor, source) for option, anchor, source in matches}
        return next(iter(unique)) if len(unique) == 1 else None

    @staticmethod
    def _match_output_has_explicit_answer(raw: str, answer_format: str) -> bool:
        text = (raw or "").strip().upper()
        allowed = "AB" if answer_format == "tf" else "ABCD"
        explicit = re.findall(r"(?:FINAL\s+ANSWER|最终答案|答案)\s*[:：]?\s*([ABCD]+)", text)
        answer_lines = re.findall(r"(?m)^\s*([ABCD]+)\s*[。.!！]?\s*$", text)
        if not explicit and answer_lines and any(marker in text for marker in ("对比选项", "分析选项", "选项：", "OPTIONS")):
            return False
        candidates = explicit or answer_lines
        if not candidates:
            return False
        token = candidates[-1]
        if answer_format in ("mcq", "tf"):
            return len([letter for letter in token if letter in allowed]) == 1
        if answer_format == "multi":
            return bool({letter for letter in token if letter in allowed})
        return False

    @staticmethod
    def _formula_symbols(formula: str) -> set[str]:
        """Return variable-like symbols referenced by a formula expression.

        The helper is intentionally best-effort and non-blocking: syntax errors
        return an empty set because _prepare_formula remains the authoritative
        validator. It exists to keep material-variable coverage keys stable even
        when a formula uses no variables or extraction returned extra variables.
        """
        if not formula:
            return set()
        try:
            normalized = CalculationSolver._normalize_percent(formula)
            normalized = CalculationSolver._normalize_function_names(normalized)
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError:
            return set()
        return {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id not in CalculationSolver._ALLOWED_BUILTINS
        }

    @staticmethod
    def _material_variable_coverage(
        *,
        formulas: Sequence[str | None],
        extracted_values: Mapping[str, Any],
        computed_values: Mapping[str, Any] | None = None,
        alias_bindings: Mapping[str, str] | None = None,
        formulas_by_label: Mapping[str, str | None] | None = None,
        alias_bindings_by_formula: Mapping[str, Mapping[str, str]] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Compute stable used/unused material-variable lists.

        This is qid-agnostic bookkeeping. Extracted variables that are not
        referenced by any emitted formula are represented as an explicit empty
        or non-empty list instead of relying on an optional local variable.
        """
        extracted = {str(name) for name in (extracted_values or {}).keys()}
        computed_labels = {str(name) for name in (computed_values or {}).keys()}
        referenced: set[str] = set()
        label_symbols: Dict[str, set[str]] = {}
        if formulas_by_label:
            for label, formula_text in formulas_by_label.items():
                symbols = CalculationSolver._formula_symbols(str(formula_text or "")) if formula_text else set()
                label_symbols[str(label)] = set(symbols)
                referenced.update(symbols)
        else:
            for formula in formulas:
                if formula:
                    referenced.update(CalculationSolver._formula_symbols(str(formula)))

        # Backward-compatible global alias-source coverage. This is still useful
        # for single-formula cases, but multi-product formulas need scoped alias
        # bindings below so one product's aliases do not overwrite another's.
        for alias, source in (alias_bindings or {}).items():
            if str(alias) in referenced and str(source) in extracted:
                referenced.add(str(source))

        # R6: formula-scoped alias-source coverage. The same generic alias
        # (e.g. 赔付比例 / 免赔额余额) can legitimately refer to different
        # product-specific source variables in different formula labels. Count
        # each scoped source as used only when that formula references the alias.
        for label, scoped_bindings in (alias_bindings_by_formula or {}).items():
            symbols = label_symbols.get(str(label))
            if symbols is None:
                continue
            for alias, source in scoped_bindings.items():
                if str(alias) in symbols and str(source) in extracted:
                    referenced.add(str(source))

        used = sorted(extracted & referenced)
        unused = sorted(extracted - referenced - computed_labels)
        return used, unused

    @staticmethod
    def _extract_values(text: str) -> Dict[str, float]:
        """Backward-compatible values-only extraction helper."""
        values, _ = CalculationSolver._extract_values_with_errors(text)
        return values

    @staticmethod
    def _normalize_percent(expr: str) -> str:
        """Convert percentage literals to decimals: 100% -> 1.0, 75% -> 0.75."""
        return re.sub(r"(\d+\.?\d*)\s*%", lambda m: str(float(m.group(1)) / 100), expr)

    # Math functions the LLM may emit in uppercase that map to Python builtins.
    # Word-boundary substitution so MAXIMUM / MAXIMIZE are not touched.
    _FUNCTION_CASE_MAP = (("MAX", "max"), ("MIN", "min"), ("ABS", "abs"), ("SUM", "sum"))

    @staticmethod
    def _normalize_function_names(expr: str) -> str:
        """Normalize uppercase math function names to Python builtins.

        ``MAX(a, b)`` -> ``max(a, b)``, etc. Word-boundary regex ensures only
        whole-word matches change (``MAXIMUM`` is left alone). This makes
        formulas executable instead of raising an uppercase-function NameError.
        """
        for upper, lower in CalculationSolver._FUNCTION_CASE_MAP:
            expr = re.sub(r"\b" + upper + r"\b", lower, expr)
        return expr

    # Builtins permitted inside formula expressions (a safe subset of Python's
    # builtins; arbitrary builtins are intentionally not whitelisted).
    _ALLOWED_BUILTINS = {"max", "min", "abs", "sum", "round", "pow"}


    @staticmethod
    def _prepare_formula(formula: str, available_names) -> tuple[Optional[str], Optional[str]]:
        """Normalize and strictly validate an untrusted formula before execution."""
        normalized = CalculationSolver._normalize_percent(formula)
        normalized = CalculationSolver._normalize_function_names(normalized)
        try:
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError as exc:
            return None, f"invalid formula syntax: {exc.msg}"

        class SumTransformer(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call):
                node = self.generic_visit(node)
                if isinstance(node.func, ast.Name) and node.func.id == "sum":
                    if node.keywords or len(node.args) < 2:
                        return node
                    expr = node.args[0]
                    for arg in node.args[1:]:
                        expr = ast.BinOp(left=expr, op=ast.Add(), right=arg)
                    return ast.copy_location(expr, node)
                return node

        tree = SumTransformer().visit(tree)
        ast.fix_missing_locations(tree)
        allowed_binary = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
        allowed_unary = (ast.UAdd, ast.USub)
        allowed_calls = {"max", "min", "abs", "round", "pow"}
        defined = set(available_names)

        for node in ast.walk(tree):
            if isinstance(node, (ast.Expression, ast.Load)):
                continue
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    return None, "unsafe formula: non-numeric constant"
                continue
            if isinstance(node, ast.Name):
                if node.id not in defined and node.id not in allowed_calls:
                    return None, f"undefined symbol(s): {node.id}"
                continue
            if isinstance(node, ast.BinOp):
                if not isinstance(node.op, allowed_binary):
                    return None, f"unsafe formula node: {type(node.op).__name__}"
                continue
            if isinstance(node, allowed_binary):
                continue
            if isinstance(node, ast.UnaryOp):
                if not isinstance(node.op, allowed_unary):
                    return None, f"unsafe formula node: {type(node.op).__name__}"
                continue
            if isinstance(node, allowed_unary):
                continue
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in allowed_calls:
                    return None, "unsafe formula: function call not allowed"
                if node.keywords:
                    return None, "unsafe formula: keyword arguments not allowed"
                continue
            return None, f"unsafe formula node: {type(node).__name__}"

        try:
            return ast.unparse(tree.body), None
        except Exception as exc:
            return None, f"invalid formula: {type(exc).__name__}"

    @staticmethod
    def _find_undefined_symbols(formula: str, available_names) -> List[str]:
        """Deterministically detect identifiers in ``formula`` that are not
        defined in ``available_names`` and not in the allowed builtin set.

        Returns a sorted list of undefined symbol names. An empty list means the
        formula is safe to evaluate (every referenced name is defined). On a
        syntax error the formula is left to the normal eval path (returns empty)
        so the subprocess reports the precise error rather than a guess here.

        Percentage literals (``75%``) and uppercase function names (``MAX``) are
        normalized before parsing so they are not mistaken for undefined names.
        """
        try:
            normalized = CalculationSolver._normalize_percent(formula)
            normalized = CalculationSolver._normalize_function_names(normalized)
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError:
            return []
        defined = set(available_names) | CalculationSolver._ALLOWED_BUILTINS
        referenced = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        return sorted(referenced - defined)

    @staticmethod
    def _build_eval_code(formula: str, values: Dict[str, float]) -> str:
        # Normalize percentage literals in formula (e.g. 100% -> 1.0)
        formula = CalculationSolver._normalize_percent(formula)
        assignments = "\n".join(f"{k} = {v}" for k, v in sorted(values.items()))
        # Use textwrap to avoid indentation issues
        return textwrap.dedent(f"""\
{assignments}
result = {formula}
print(result)
""")

    @staticmethod
    def _run_python(code: str) -> tuple[Optional[float], Optional[str]]:
        """Run Python code and return (result, error)."""
        try:
            proc = subprocess.run(
                [sys.executable or "python", "-c", code],
                capture_output=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                return None, (proc.stderr or "").strip()[:200]
            out = (proc.stdout or "").strip()
            try:
                value = float(out)
            except ValueError:
                return None, f"non-numeric output: {out[:100]}"
            if not math.isfinite(value):
                return None, f"non-finite output: {out[:100]}"
            return value, None
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except FileNotFoundError:
            return None, "python not found"
        except OSError as e:
            return None, str(e)[:200]

    # ── final matching ──────────────────────────────────────────────

    def _build_prompt(
        self,
        bundle: EvidenceBundle,
        formula: Optional[str],
        computed: Optional[float],
        raw_extract: str = "",
        computed_values: Optional[Dict[str, float]] = None,
    ) -> str:
        calc_section = ""
        if computed_values:
            # Multi-formula mode: show all computed values with labels
            lines = [f"  {label}: {val}" for label, val in computed_values.items()]
            calc_section = (
                f"\n[Python 计算结果（多产品）]\n"
                + "\n".join(lines)
                + "\n\n"
            )
        elif formula and computed is not None:
            calc_section = (
                f"\n[Python 计算结果]\n"
                f"公式：{formula}\n"
                f"计算结果：{computed}\n\n"
            )
        elif raw_extract:
            calc_section = f"\n[公式提取结果]\n{raw_extract}\n\n"

        return (
            "你是金融计算题答题器。以下问题是计算题，需要你根据给出的计算结果选择对应选项。\n"
            f"{calc_section}"
            f"请从以下选项中选出与计算结果匹配的答案。不要重新计算，不要质疑计算结果。\n\n"
            f"{render_question(bundle)}\n\n"
            "最终答案："
        )
