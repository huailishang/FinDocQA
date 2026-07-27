"""Controlled workflow orchestration for the enhanced baseline."""

from __future__ import annotations

from dataclasses import replace
import os
from typing import Any, Dict, List, Optional, Sequence

from contracts import (
    EvidenceAssembler,
    EvidenceRetriever,
    PipelineResult,
    Question,
    QuestionClassifier,
    Solver,
    SolverResult,
    SubmissionWriter,
    Verifier,
)
from answer_contract import contract_from_question, contract_to_dict
from solvers.base import validate_submission_answer
from verification.production_integrity import assess_final_state
from verification.production_typed_evidence import build_production_typed_option_evidence
from verification.option_evidence_schema import audit_legacy_against_source_local_typed
from evidence.prompt_budget import (
    PromptBudgetEstimate,
    PromptBudgetExceeded,
    enforce_prompt_budget,
    estimate_prompt_budget,
)
from verification.correction_gate import assess_self_check_correction
from utils.llm_client import LLMProviderBudgetExhausted
from runtime_safety import (
    ProviderCallBudgetExceeded,
    current_attempt_context,
    record_pre_call_blocked,
    set_attempt_context,
)


def provider_execution_contract(solver_result: SolverResult) -> Dict[str, Any]:
    """Describe whether a result required a provider call.

    Deterministic insurance calculations are legitimate zero-call executions
    only when their typed audit says the local evidence was recognised.
    """
    metadata = dict(solver_result.metadata or {})
    audit = metadata.get("insurance_calculation_audit")
    deterministic = bool(metadata.get("deterministic_insurance_calculation"))
    orchestrated = bool(metadata.get("gap_driven_orchestrator"))
    local_complete = bool(
        (
            deterministic
            and isinstance(audit, dict)
            and audit.get("recognized") is True
        )
        or (
            orchestrated
            and metadata.get("orchestrator_final_state") == "COMPLETED"
            and metadata.get("orchestrator_answer_contract_closed") is True
        )
    )
    if deterministic or orchestrated:
        return {
            "expected_provider_call": False,
            "provider_call_count": 0,
            "token_usage": 0,
            "local_deterministic_evidence_complete": local_complete,
        }
    return {
        "expected_provider_call": True,
        "provider_call_count": metadata.get("provider_call_count"),
        "token_usage": int(metadata.get("total_tokens", 0) or 0),
        "local_deterministic_evidence_complete": False,
    }


class BlockingAnswerValidationError(ValueError):
    """Raised when an official answer cannot be safely emitted."""

    def __init__(
        self,
        qid: str,
        answer_format: str,
        answer: str,
        reason: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.qid = qid
        self.answer_format = answer_format
        self.answer = answer
        self.reason = reason
        self.metadata = dict(metadata or {})
        super().__init__(f"blocking invalid answer: qid={qid}, answer_format={answer_format!r}, answer={answer!r}, reason={reason}")


class EnhancedBaselineWorkflow:
    """Wire modules into the fixed enhanced-baseline pipeline."""

    def __init__(
        self,
        classifier: QuestionClassifier,
        retriever: EvidenceRetriever,
        assembler: EvidenceAssembler,
        solver: Solver,
        writer: Optional[SubmissionWriter] = None,
        verifier: Optional[Verifier] = None,
        fallback_solver: Optional[Solver] = None,
        self_check_verifier: Optional[Verifier] = None,
        enforce_production_integrity: bool = False,
        apply_safe_self_check_corrections: bool = False,
        self_check_correction_routes: Optional[Sequence[str]] = None,
        fallback_enabled: bool = True,
        prompt_budget_enforced: bool = False,
        prompt_budget_model: str = "qwen3.7-max",
        prompt_budget_target_total_tokens: int = 38_000,
        prompt_budget_hard_cap_tokens: int = 45_000,
        evidence_orchestrator: Optional[Any] = None,
        orchestrator_mode: str = "advisory",
    ) -> None:
        self.classifier = classifier
        self.retriever = retriever
        self.assembler = assembler
        self.solver = solver
        self.writer = writer
        self.verifier = verifier
        self.fallback_solver = fallback_solver
        self.self_check_verifier = self_check_verifier
        self.enforce_production_integrity = enforce_production_integrity
        self.fallback_enabled = bool(fallback_enabled)
        self.prompt_budget_enforced = bool(prompt_budget_enforced)
        self.prompt_budget_model = str(prompt_budget_model or "qwen3.7-max")
        self.prompt_budget_target_total_tokens = int(prompt_budget_target_total_tokens)
        self.prompt_budget_hard_cap_tokens = int(prompt_budget_hard_cap_tokens)
        self.evidence_orchestrator = evidence_orchestrator
        normalized_mode = str(orchestrator_mode or "advisory").strip().lower()
        if normalized_mode not in {"advisory", "authoritative"}:
            raise ValueError(f"unsupported orchestrator_mode: {orchestrator_mode!r}")
        self.orchestrator_mode = normalized_mode
        self.apply_safe_self_check_corrections = apply_safe_self_check_corrections
        self.self_check_correction_routes = tuple(self_check_correction_routes or (
            "regulatory_exact_clause", "question_scope_exclusion"
        ))

    def process_one(self, question: Question) -> PipelineResult:
        set_attempt_context(question.qid, "workflow_solver")
        classification = None
        candidates = []
        bundle = None
        try:
            if (
                question.answer_format == "freeform"
                and (
                    question.submission_slot_count not in {1, 2, 3, 4}
                    or (
                        str(question.raw.get("split") or "").strip().upper() == "B"
                        and len(question.submission_slot_contracts)
                        != question.submission_slot_count
                    )
                )
            ):
                raise BlockingAnswerValidationError(
                    question.qid,
                    question.answer_format,
                    "",
                    "missing_submission_slot_contract",
                    metadata={
                        "answer_format": question.answer_format,
                        "submission_slot_count": question.submission_slot_count,
                        "provider_call_count": 0,
                        "total_tokens": 0,
                        "final_state": "blocked",
                        "grounded": False,
                    },
                )
            classification = self.classifier.classify(question)
            candidates = list(self.retriever.retrieve(question, classification))
            bundle = self.assembler.assemble(question, classification, candidates)
            bundle, prompt_budget_estimate = self._prepare_prompt_budget(bundle)
            if self.prompt_budget_enforced:
                try:
                    enforce_prompt_budget(prompt_budget_estimate)
                except PromptBudgetExceeded as exc:
                    record_pre_call_blocked(
                        reason="prompt_budget_precheck_blocked",
                        model=prompt_budget_estimate.model_id,
                    )
                    raise exc
            orchestration = None
            if self.evidence_orchestrator is not None:
                orchestration = self.evidence_orchestrator.run(
                    question,
                    initial_candidates=tuple(
                        bundle.verification_candidates or bundle.candidates
                    ),
                )
            if (
                orchestration is not None
                and orchestration.final_state == "COMPLETED"
                and orchestration.answer_contract_closed
            ):
                solver_result = SolverResult(
                    qid=question.qid,
                    answer=orchestration.production_answer,
                    solver="gap_driven_orchestrator",
                    raw_output=str(orchestration.to_dict()),
                    confidence=1.0,
                    metadata={
                        "gap_driven_orchestrator": True,
                        "orchestrator_mode": self.orchestrator_mode,
                        "orchestrator_final_state": orchestration.final_state,
                        "orchestrator_answer_contract_closed": orchestration.answer_contract_closed,
                        "orchestrator_capability_id": orchestration.capability_id,
                        "orchestrator_graph_hash": orchestration.graph.get("validation", {}).get("graph_hash"),
                        "orchestrator_provider_calls": orchestration.provider_calls,
                        "orchestrator_tokens_used": orchestration.tokens_used,
                        "orchestrator_dependency_integrity": dict(
                            getattr(orchestration, "dependency_integrity", {}) or {}
                        ),
                        "provider_call_count": 0,
                        "total_tokens": 0,
                    },
                )
            elif orchestration is not None and self.orchestrator_mode == "authoritative":
                raise BlockingAnswerValidationError(
                    question.qid,
                    question.answer_format,
                    "",
                    "authoritative_orchestrator_blocked_no_solver_fallback",
                    metadata={
                        "orchestrator_mode": self.orchestrator_mode,
                        "orchestrator_final_state": orchestration.final_state,
                        "orchestrator_answer_contract_closed": orchestration.answer_contract_closed,
                        "orchestrator_block_reasons": list(
                            getattr(orchestration, "block_reasons", ()) or ()
                        ),
                        "orchestrator_dependency_integrity": dict(
                            getattr(orchestration, "dependency_integrity", {}) or {}
                        ),
                        "solver_fallback_used": False,
                        "provider_call_count": 0,
                        "total_tokens": 0,
                    },
                )
            else:
                solver_result = self.solver.solve(bundle)
                if orchestration is not None:
                    solver_result = replace(
                        solver_result,
                        metadata={
                            **dict(solver_result.metadata or {}),
                            "orchestrator_mode": self.orchestrator_mode,
                            "orchestrator_final_state": orchestration.final_state,
                            "orchestrator_answer_contract_closed": orchestration.answer_contract_closed,
                            "orchestrator_fallback_reason": "advisory_orchestrator_not_closed",
                            "solver_fallback_used": True,
                        },
                    )
            answer_contract = contract_from_question(question)
            solver_submission_answers: tuple[str, ...]
            if question.answer_format == "freeform":
                raw_submission_answers = solver_result.metadata.get("submission_answers")
                solver_submission_answers = (
                    tuple(str(value).strip() for value in raw_submission_answers)
                    if isinstance(raw_submission_answers, Sequence)
                    and not isinstance(raw_submission_answers, (str, bytes))
                    else ()
                )
                expected_slots = question.submission_slot_count
                if len(solver_submission_answers) != expected_slots:
                    parse_reason = str(
                        solver_result.metadata.get("freeform_parse_reason")
                        or "submission_slot_count_mismatch"
                    )
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, solver_result.answer,
                        parse_reason,
                        metadata={
                            "answer_format": question.answer_format,
                            "answer_contract": contract_to_dict(answer_contract),
                            "submission_answers": list(solver_submission_answers),
                            "expected_submission_slots": expected_slots,
                            "solver": solver_result.solver,
                            "solver_metadata": dict(solver_result.metadata or {}),
                            "solver_raw_output": solver_result.raw_output,
                            **provider_execution_contract(solver_result),
                        },
                    )
                slot_validations = [
                    validate_submission_answer(
                        value, question.answer_format, answer_contract=answer_contract
                    )
                    for value in solver_submission_answers
                ]
                invalid_slots = [
                    (index, validation)
                    for index, validation in enumerate(slot_validations, start=1)
                    if not validation.valid
                ]
                if invalid_slots:
                    index, invalid = invalid_slots[0]
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, solver_result.answer,
                        f"submission_slot_{index}:{invalid.reason}",
                        metadata={
                            "answer_format": question.answer_format,
                            "answer_contract": contract_to_dict(answer_contract),
                            "submission_answers": list(solver_submission_answers),
                            "solver": solver_result.solver,
                            "solver_metadata": dict(solver_result.metadata or {}),
                            **provider_execution_contract(solver_result),
                        },
                    )
                solver_submission_answers = tuple(
                    validation.answer for validation in slot_validations
                )
                solver_answer_validation = slot_validations[0]
                if solver_result.answer != solver_submission_answers[0]:
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, solver_result.answer,
                        "freeform_primary_answer_mismatch",
                        metadata={
                            "submission_answers": list(solver_submission_answers),
                            "solver_metadata": dict(solver_result.metadata or {}),
                            **provider_execution_contract(solver_result),
                        },
                    )
            else:
                solver_submission_answers = (solver_result.answer,)
                solver_answer_validation = validate_submission_answer(
                    solver_result.answer,
                    question.answer_format,
                    answer_contract=answer_contract,
                )
                if not solver_answer_validation.valid:
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, solver_result.answer,
                        f"solver_answer_contract_violation:{solver_answer_validation.reason}",
                        metadata={
                            "answer_format": question.answer_format,
                            "answer_contract": contract_to_dict(answer_contract),
                            "solver_answer_validation": solver_answer_validation.to_dict(),
                            "solver": solver_result.solver,
                            "solver_metadata": dict(solver_result.metadata or {}),
                            "solver_raw_output": solver_result.raw_output,
                            **provider_execution_contract(solver_result),
                        },
                    )
            verification = None
            final_answer = solver_result.answer

            if self.verifier is not None and question.answer_format != "freeform":
                verification = self.verifier.verify(bundle, solver_result)
                final_answer = verification.answer

            # Lane 3: option-level self-check.  Proposals remain evaluation-only
            # unless the explicitly enabled conservative correction gate accepts
            # complete exact-clause evidence for every actual option.
            correction_gate = None
            if self.self_check_verifier is not None and question.answer_format != "freeform":
                self_check = self.self_check_verifier.verify(bundle, solver_result)
                if self.apply_safe_self_check_corrections:
                    correction_gate = assess_self_check_correction(
                        domain=question.domain,
                        original_answer=final_answer,
                        self_check_metadata=self_check.metadata,
                        allowed_routes=self.self_check_correction_routes,
                    )
                    if correction_gate["applied"]:
                        final_answer = correction_gate["answer"]
                # Merge self-check metadata into verification metadata
                if verification is not None:
                    v_meta = dict(verification.metadata)
                    v_meta["self_check"] = dict(self_check.metadata)
                    v_notes = list(verification.notes)
                    v_notes.extend(self_check.notes)
                    verification = replace(
                        verification,
                        metadata=v_meta,
                        notes=v_notes,
                    )
                else:
                    verification = self_check

            typed_option_evidence = None
            typed_answer_override_applied = False
            typed_answer_before_override = final_answer
            if self.enforce_production_integrity and question.answer_format != "freeform":
                typed_option_evidence = build_production_typed_option_evidence(
                    bundle, solver_result, answer_contract=answer_contract
                )
                typed_proposal = str(
                    typed_option_evidence.get("correction_proposal")
                    or typed_option_evidence.get("typed_supported_answer")
                    or ""
                )
                if (
                    typed_option_evidence.get("trusted_for_production") is True
                    and typed_option_evidence.get("production_answer_override_allowed") is True
                    and typed_proposal
                ):
                    typed_validation = validate_submission_answer(
                        typed_proposal, question.answer_format, answer_contract=answer_contract
                    )
                    if typed_validation.valid:
                        final_answer = typed_validation.answer
                        typed_answer_override_applied = (
                            typed_validation.answer != typed_answer_before_override
                        )
                if verification is not None:
                    v_meta = dict(verification.metadata or {})
                    v_meta["typed_option_evidence"] = dict(typed_option_evidence)
                    verification = replace(verification, metadata=v_meta)

            # --- Observability metadata ---
            meta = self._build_observability_meta(question, classification, candidates, bundle, solver_result)
            meta["solver_metadata"] = dict(solver_result.metadata or {})
            meta["solver_raw_output"] = solver_result.raw_output
            meta.update(provider_execution_contract(solver_result))
            meta["answer_contract"] = contract_to_dict(answer_contract)
            meta["solver_answer_validation"] = solver_answer_validation.to_dict()
            meta["submission_answers"] = list(solver_submission_answers)
            meta["submission_slot_count"] = question.submission_slot_count
            if question.answer_format == "freeform":
                meta["freeform_verifier_skipped"] = True
                meta["freeform_option_integrity_skipped"] = True
            if verification is not None:
                verification_meta = dict(verification.metadata or {})
                meta["verification_result"] = {
                    "qid": verification.qid,
                    "answer": verification.answer,
                    "changed": verification.changed,
                    "verifier": verification.verifier,
                    "notes": list(verification.notes),
                    "metadata": verification_meta,
                }
                self_check_meta = verification_meta.get("self_check")
                if isinstance(self_check_meta, dict):
                    meta["self_check"] = dict(self_check_meta)
                elif verification.verifier == "option_self_check":
                    meta["self_check"] = verification_meta
            if correction_gate is not None:
                meta["self_check_correction_gate"] = dict(correction_gate)
            if isinstance(typed_option_evidence, dict):
                meta["typed_option_evidence"] = dict(typed_option_evidence)
                for key in (
                    "option_binding_scope_doc_ids",
                    "option_binding_scope_source",
                    "option_binding_scope_source_refs",
                    "option_binding_scope_expanded",
                    "option_binding_scope_expansion_reason",
                    "option_binding_outside_solver_docs",
                    "invalid_source_refs",
                    "lineage_valid",
                    "fail_closed",
                    "selected_answer_source_local_trusted",
                    "production_answer_basis",
                    "legacy_self_check_policy",
                ):
                    if key in typed_option_evidence:
                        meta[key] = typed_option_evidence.get(key)
                meta["legacy_vs_source_local_typed"] = audit_legacy_against_source_local_typed(
                    {"metadata": meta}
                )
            if self.enforce_production_integrity:
                meta["typed_answer_override_applied"] = typed_answer_override_applied
                meta["typed_answer_before_override"] = typed_answer_before_override
                meta["typed_answer_after_override"] = final_answer
                integrity = assess_final_state(
                    labels=classification.labels,
                    requested_docs=[str(value) for value in question.doc_ids],
                    retrieved_docs=[str(candidate.doc_id) for candidate in candidates],
                    solver_result=solver_result,
                    verification=verification,
                    typed_option_evidence=typed_option_evidence,
                    final_answer=final_answer,
                    answer_format=question.answer_format,
                    submission_answers=solver_submission_answers,
                    expected_submission_slots=question.submission_slot_count,
                )
                meta.update(integrity)
                solver_used = list(integrity.get("solver_used_doc_ids") or [])
                verifier_used = list(integrity.get("verifier_evidence_doc_ids") or [])
                authority = str(integrity.get("final_answer_authority") or "solver")
                if authority == "verifier":
                    final_used = verifier_used
                elif authority == "solver":
                    final_used = solver_used
                else:
                    final_used = []
                meta["solver_used_doc_ids"] = solver_used
                meta["verifier_used_doc_ids"] = verifier_used
                meta["final_used_doc_ids"] = list(dict.fromkeys(final_used))

                integration_blocking: list[str] = []
                is_b_profile = str(question.raw.get("split") or "").strip().upper() == "B"
                if is_b_profile:
                    if meta.get("retriever_scope_audit_source") != "retriever_call_boundary":
                        integration_blocking.append("retriever_scope_truth_unavailable")
                    if meta.get("out_of_scope_without_reason_doc_ids"):
                        integration_blocking.append("out_of_scope_without_reason")
                    if meta.get("unknown_scope_expansion_reason_doc_ids"):
                        integration_blocking.append("unknown_scope_expansion_reason")
                    allowed_used_docs = set(meta.get("retrieved_doc_ids") or []) | set(
                        (meta.get("scope_expansion_reasons") or {}).keys()
                    )
                    if set(meta["final_used_doc_ids"]) - allowed_used_docs:
                        integration_blocking.append("final_used_doc_lineage_outside_scope")
                if integration_blocking:
                    blocking_reasons = sorted(
                        set(integrity.get("blocking_reasons") or ())
                        | set(integration_blocking)
                    )
                    integrity["blocking_reasons"] = blocking_reasons
                    integrity["final_state"] = "blocked"
                    meta.update(integrity)
                    meta["integration_lineage_blocking_reasons"] = integration_blocking
                if integrity["blocking_reasons"]:
                    reason = "production_integrity:" + ",".join(integrity["blocking_reasons"])
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, final_answer, reason,
                        metadata=meta,
                    )
            else:
                meta.update({
                    "production_integrity_checked": False,
                    "final_state": "accepted",
                    "grounded": True,
                })
            if solver_result.metadata.get("llm_error", False):
                meta.update({
                    "answer_format": question.answer_format,
                    "answer_validation": "blocking_invalid",
                    "answer_validation_reason": "llm_error",
                    "raw_final_answer": final_answer,
                })
                raise BlockingAnswerValidationError(
                    question.qid, question.answer_format, final_answer, "llm_error",
                    metadata=meta,
                )

            if question.answer_format == "freeform":
                final_slot_validations = [
                    validate_submission_answer(
                        value, question.answer_format, answer_contract=answer_contract
                    )
                    for value in solver_submission_answers
                ]
                invalid_final_slots = [
                    (index, item)
                    for index, item in enumerate(final_slot_validations, start=1)
                    if not item.valid
                ]
                if invalid_final_slots:
                    index, invalid = invalid_final_slots[0]
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, final_answer,
                        f"submission_slot_{index}:{invalid.reason}",
                        metadata=meta,
                    )
                final_submission_answers = tuple(
                    item.answer for item in final_slot_validations
                )
                validation = final_slot_validations[0]
                if final_answer != final_submission_answers[0]:
                    raise BlockingAnswerValidationError(
                        question.qid, question.answer_format, final_answer,
                        "freeform_primary_answer_mismatch", metadata=meta
                    )
            else:
                validation = validate_submission_answer(
                    final_answer, question.answer_format, answer_contract=answer_contract
                )
                final_submission_answers = (validation.answer,)
            meta.update({
                "answer_format": question.answer_format,
                "answer_validation": "generated_valid" if validation.valid else "blocking_invalid",
                "answer_validation_reason": validation.reason,
                "raw_final_answer": final_answer,
                "submission_answers": list(final_submission_answers),
            })
            if not validation.valid:
                raise BlockingAnswerValidationError(
                    question.qid, question.answer_format, final_answer, validation.reason,
                    metadata=meta,
                )
            token_meta = self._extract_token_meta(solver_result)
            meta["provider_ledger_token_totals"] = {
                "prompt_tokens": int(token_meta["prompt_tokens"]),
                "completion_tokens": int(token_meta["completion_tokens"]),
                "total_tokens": int(token_meta["total_tokens"]),
            }
            meta["provider_ledger_run_id"] = os.getenv("SAFE_RUN_ID", "").strip()
            meta["provider_ledger_decision_purpose"] = os.getenv(
                "SAFE_RUN_DECISION_PURPOSE", ""
            ).strip()

            return PipelineResult(
                qid=question.qid,
                answer=validation.answer,
                classification=classification,
                solver_result=solver_result,
                verification_result=verification,
                prompt_tokens=token_meta["prompt_tokens"],
                completion_tokens=token_meta["completion_tokens"],
                total_tokens=token_meta["total_tokens"],
                fallback_used=bool(solver_result.metadata.get("composite_fallback_used", False)),
                metadata=meta,
                submission_answers=final_submission_answers,
            )
        except (BlockingAnswerValidationError, LLMProviderBudgetExhausted):
            raise
        except PromptBudgetExceeded as exc:
            raise self._blocked_without_fallback(
                question,
                exc,
                classification=classification,
                candidates=candidates,
                bundle=bundle,
                reason="prompt_budget_precheck_blocked",
            ) from exc
        except ProviderCallBudgetExceeded as exc:
            raise self._blocked_without_fallback(
                question, exc, classification=classification, candidates=candidates,
                bundle=bundle, reason="provider_call_budget_precheck_blocked",
            ) from exc
        except Exception as exc:
            if self.fallback_solver is None or not self._runtime_fallback_allowed():
                raise self._blocked_without_fallback(
                    question, exc, classification=classification, candidates=candidates,
                    bundle=bundle, reason="fallback_disabled_main_path_error",
                ) from exc
            return self._fallback(
                question, exc, classification=classification, candidates=candidates, bundle=bundle
            )

    def _prepare_prompt_budget(
        self, bundle: EvidenceBundle
    ) -> tuple[EvidenceBundle, PromptBudgetEstimate]:
        """Attach the resolved pre-provider budget estimate to the bundle."""
        model_id = str(
            os.getenv("FREETOKEN_MODEL")
            or os.getenv("LLM_MODEL_ID")
            or self.prompt_budget_model
        ).strip()
        target = self._positive_env_int(
            "SAFE_RUN_PROMPT_TARGET_TOTAL_TOKENS",
            self.prompt_budget_target_total_tokens,
        )
        hard_cap = self._positive_env_int(
            "SAFE_RUN_PROMPT_HARD_CAP_TOKENS",
            self.prompt_budget_hard_cap_tokens,
        )
        estimate = estimate_prompt_budget(
            model_id=model_id,
            rendered_context_chars=len(bundle.prompt_context),
            target_total_tokens=target,
            hard_cap_tokens=hard_cap,
        )
        metadata = dict(bundle.metadata or {})
        metadata.update({
            "prompt_budget_model": estimate.model_id,
            "prompt_rendered_context_chars": estimate.rendered_context_chars,
            "prompt_budget_context_estimated_tokens": estimate.context_estimated_tokens,
            "prompt_budget_fixed_overhead_tokens": estimate.fixed_prompt_overhead_tokens,
            "prompt_budget_estimated_tokens": estimate.prompt_estimated_tokens,
            "prompt_completion_reserve_tokens": estimate.completion_reserve_tokens,
            "prompt_estimated_total_tokens": estimate.estimated_total_tokens,
            "prompt_budget_target_total_tokens": estimate.target_total_tokens,
            "prompt_budget_hard_cap_tokens": estimate.hard_cap_tokens,
            "prompt_budget_policy_source": estimate.policy_source,
            "prompt_budget_within_target": estimate.within_target,
            "prompt_budget_within_hard_cap": estimate.within_hard_cap,
            "prompt_budget_pre_call_enforced": self.prompt_budget_enforced,
        })
        return replace(bundle, metadata=metadata), estimate

    @staticmethod
    def _positive_env_int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return int(default)
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def _build_observability_meta(
        self,
        question: Question,
        classification: ClassificationResult,
        candidates: Sequence[EvidenceCandidate],
        bundle: EvidenceBundle,
        solver_result: SolverResult,
    ) -> Dict[str, Any]:
        # Group evidence sources by doc_id
        evidence_by_doc: Dict[str, List[str]] = {}
        fallback_by_doc: Dict[str, bool] = {}
        for c in candidates:
            doc = str(c.doc_id)
            if doc not in evidence_by_doc:
                evidence_by_doc[doc] = []
                fallback_by_doc[doc] = False
            evidence_by_doc[doc].append(str(c.source))
            if c.metadata.get("fallback") == "first_page":
                fallback_by_doc[doc] = True

        requested = [str(d) for d in question.doc_ids]
        existing_docs = set(evidence_by_doc.keys())
        missing_doc_ids = [d for d in requested if d not in existing_docs]
        retrieval_fallbacks = [d for d, fb in fallback_by_doc.items() if fb]
        solver_meta = dict(solver_result.metadata or {})
        composite_fallback = bool(solver_meta.get("composite_fallback_used", False))
        calculation_incomplete = bool(solver_meta.get("calculation_incomplete", False))
        risk_label = solver_meta.get("risk_label")
        warnings = []
        if calculation_incomplete:
            warnings.append("calculation_incomplete")
        if risk_label and risk_label not in warnings:
            warnings.append(str(risk_label))
        normalized_options = ["".join(str(text).split()) for text in question.options.values()]
        duplicate_options = len(normalized_options) != len(set(normalized_options))
        if duplicate_options:
            warnings.append("duplicate_options")
        degradation = bool(
            retrieval_fallbacks
            or missing_doc_ids
            or solver_result.metadata.get("dry_run", False)
            or solver_result.metadata.get("llm_error", False)
            or composite_fallback
            or calculation_incomplete
        )

        # P6e-0: additive observability metadata. Only lengths are serialized;
        # full candidate text is never written into debug metadata.
        bundle_meta = bundle.metadata or {}
        token_budget = bundle_meta.get("token_budget")
        evidence_budget_source = bundle_meta.get("evidence_budget_source")
        candidate_length_stats = self._candidate_length_stats(candidates)
        scope_candidate_doc_ids = list(bundle_meta.get("scope_candidate_doc_ids") or [])
        retriever_requested_doc_ids = list(
            bundle_meta.get("retriever_requested_doc_ids") or []
        )
        retriever_resolved_doc_ids = list(
            bundle_meta.get("retriever_resolved_doc_ids") or []
        )
        retriever_missing_doc_ids = list(
            bundle_meta.get("retriever_missing_doc_ids") or []
        )
        retrieved_doc_ids = list(bundle_meta.get("retrieved_doc_ids") or [])
        assembler_used_doc_ids = list(bundle_meta.get("assembler_used_doc_ids") or [])
        solver_available_doc_ids = list(
            bundle_meta.get("solver_available_doc_ids") or assembler_used_doc_ids
        )
        verifier_candidate_doc_ids = list(
            bundle_meta.get("verifier_candidate_doc_ids") or []
        )
        raw_solver_used = (
            solver_meta.get("solver_used_doc_ids")
            if solver_meta.get("solver_used_doc_ids") is not None
            else solver_meta.get("used_doc_ids")
        )
        solver_used_doc_ids = list(
            dict.fromkeys(str(value) for value in raw_solver_used or [] if str(value))
        )
        # Available evidence is not proof of actual use. Verifier/final usage is
        # assigned after production-integrity selects the answer authority.
        verifier_used_doc_ids: list[str] = []
        final_used_doc_ids = list(solver_used_doc_ids)

        return {
            "domain": question.domain,
            "duplicate_options": duplicate_options,
            "doc_ids": requested,
            "classifier_labels": [l.value for l in classification.labels],
            "classifier_reasons": dict(classification.reasons),
            "solver": solver_result.solver,
            "evidence_count": len(candidates),
            "evidence_by_doc": evidence_by_doc,
            "missing_doc_ids": missing_doc_ids,
            "retrieval_fallbacks": retrieval_fallbacks,
            "estimated_tokens": bundle.estimated_tokens,
            "degraded": degradation,
            "fallback_used": composite_fallback,
            "calculation_incomplete": calculation_incomplete,
            "risk_label": risk_label,
            "warnings": warnings,
            "matched_terms": self._collect_matched_terms(candidates),
            "score_breakdown": self._collect_score_breakdown(candidates),
            # P6e-0 additive fields (do not remove/rename existing keys):
            "token_budget": token_budget,
            "evidence_budget_source": evidence_budget_source,
            "scope_candidate_doc_ids": scope_candidate_doc_ids,
            "retriever_requested_doc_ids": retriever_requested_doc_ids,
            "retriever_resolved_doc_ids": retriever_resolved_doc_ids,
            "retriever_missing_doc_ids": retriever_missing_doc_ids,
            "retriever_scope_request_source": str(
                bundle_meta.get("retriever_scope_request_source") or ""
            ),
            "retriever_scope_audit_source": str(
                bundle_meta.get("retriever_scope_audit_source") or ""
            ),
            "retriever_scope_provider_calls": int(
                bundle_meta.get("retriever_scope_provider_calls", 0) or 0
            ),
            "retrieved_doc_ids": retrieved_doc_ids,
            "assembler_used_doc_ids": assembler_used_doc_ids,
            "solver_available_doc_ids": solver_available_doc_ids,
            "verifier_candidate_doc_ids": verifier_candidate_doc_ids,
            "solver_used_doc_ids": solver_used_doc_ids,
            "verifier_used_doc_ids": verifier_used_doc_ids,
            "final_used_doc_ids": final_used_doc_ids,
            "scope_out_of_scope_doc_ids": list(bundle_meta.get("out_of_scope_doc_ids") or []),
            "scope_expansion_reasons": dict(
                bundle_meta.get("scope_expansion_reasons") or {}
            ),
            "out_of_scope_without_reason_doc_ids": list(
                bundle_meta.get("out_of_scope_without_reason_doc_ids") or []
            ),
            "unknown_scope_expansion_reason_doc_ids": list(
                bundle_meta.get("unknown_scope_expansion_reason_doc_ids") or []
            ),
            "rendered_context_chars": len(bundle.prompt_context),
            **{
                str(key): value
                for key, value in bundle_meta.items()
                if str(key).startswith("prompt_")
            },
            "actual_prompt_tokens": int(solver_meta.get("prompt_tokens", 0) or 0),
            "actual_completion_tokens": int(solver_meta.get("completion_tokens", 0) or 0),
            "actual_total_tokens": int(solver_meta.get("total_tokens", 0) or 0),
            "prompt_estimate_delta_tokens": int(
                bundle_meta.get("prompt_budget_estimated_tokens", 0) or 0
            ) - int(solver_meta.get("prompt_tokens", 0) or 0),
            "total_estimate_delta_tokens": int(
                bundle_meta.get("prompt_estimated_total_tokens", 0) or 0
            ) - int(solver_meta.get("total_tokens", 0) or 0),
            "prompt_estimate_under_actual": int(
                solver_meta.get("prompt_tokens", 0) or 0
            ) > int(bundle_meta.get("prompt_budget_estimated_tokens", 0) or 0),
            "candidate_length_stats": candidate_length_stats,
            "structure_aware": bool(bundle_meta.get("structure_aware", False)),
            "structure_enriched_candidates": int(bundle_meta.get("structure_enriched_candidates", 0) or 0),
            "structure_formula_supplement_sources": list(bundle_meta.get("structure_formula_supplement_sources", []) or []),
            "structure_formula_anchors": list(bundle_meta.get("structure_formula_anchors", []) or []),
        }

    @staticmethod
    def _candidate_length_stats(candidates: Sequence[EvidenceCandidate]) -> Dict[str, Any]:
        """Compact per-candidate length statistics. Lengths only — never text."""
        count = len(candidates)
        if count == 0:
            return {
                "count": 0,
                "text_chars_total": 0,
                "before_chars_total": 0,
                "after_chars_total": 0,
                "text_chars_max": 0,
                "before_chars_max": 0,
                "after_chars_max": 0,
            }
        text_lens = [len(c.text or "") for c in candidates]
        before_lens = [len(c.before_text or "") for c in candidates]
        after_lens = [len(c.after_text or "") for c in candidates]
        return {
            "count": count,
            "text_chars_total": sum(text_lens),
            "before_chars_total": sum(before_lens),
            "after_chars_total": sum(after_lens),
            "text_chars_max": max(text_lens),
            "before_chars_max": max(before_lens),
            "after_chars_max": max(after_lens),
        }

    @staticmethod
    def _collect_matched_terms(candidates: Sequence[EvidenceCandidate]) -> Dict[str, List[str]]:
        """Aggregate matched_terms per doc_id from candidate metadata."""
        result: Dict[str, List[str]] = {}
        seen_terms: Dict[str, set] = {}
        for c in candidates:
            doc = str(c.doc_id)
            if doc not in result:
                result[doc] = []
                seen_terms[doc] = set()
            terms = c.metadata.get("matched_terms", [])
            if isinstance(terms, list):
                for term in terms:
                    if term and term not in seen_terms[doc]:
                        seen_terms[doc].add(term)
                        result[doc].append(term)
        return result

    @staticmethod
    def _collect_score_breakdown(candidates: Sequence[EvidenceCandidate]) -> Dict[str, Dict[str, float]]:
        """Aggregate score breakdown per doc_id."""
        result: Dict[str, Dict[str, float]] = {}
        for c in candidates:
            doc = str(c.doc_id)
            if doc not in result:
                result[doc] = {"total_score": 0.0, "exact_hits": 0, "title_hits": 0, "numeric_hits": 0}
            bd = c.metadata.get("score_breakdown", {})
            if isinstance(bd, dict):
                result[doc]["total_score"] += bd.get("score", 0.0) or 0.0
                result[doc]["exact_hits"] += bd.get("exact_hits", 0) or 0
                result[doc]["title_hits"] += bd.get("title_hits", 0) or 0
                result[doc]["numeric_hits"] += bd.get("numeric_hits", 0) or 0
        return result

    def _runtime_fallback_allowed(self) -> bool:
        """Return whether runtime fallback may be used for this run.

        Paid smoke manifests can disable fallback independently from the static
        model config.  The safe runner exposes that resolved policy through an
        environment variable so the workflow enforces it at the actual fallback
        branch, not only in documentation.
        """
        disabled = os.getenv("SAFE_RUN_DISABLE_FALLBACK", "").strip().lower()
        if disabled in {"1", "true", "yes", "y", "on"}:
            return False
        enabled = os.getenv("SAFE_RUN_FALLBACK_ENABLED", "").strip().lower()
        if enabled in {"0", "false", "no", "n", "off"}:
            return False
        return bool(self.fallback_enabled)

    def _blocked_without_fallback(
        self,
        question: Question,
        exc: Exception,
        *,
        classification: Optional[ClassificationResult],
        candidates: Optional[Sequence[EvidenceCandidate]],
        bundle: Optional[EvidenceBundle] = None,
        reason: str,
    ) -> BlockingAnswerValidationError:
        classification = classification or self.classifier.classify(question)
        retained_candidates = list(candidates or [])
        evidence_by_doc: Dict[str, List[str]] = {}
        for candidate in retained_candidates:
            evidence_by_doc.setdefault(str(candidate.doc_id), []).append(str(candidate.source))
        requested = [str(doc_id) for doc_id in question.doc_ids]
        missing = [doc_id for doc_id in requested if doc_id not in evidence_by_doc]
        _ctx_qid, _ctx_stage = current_attempt_context()
        if bundle is not None:
            metadata = self._build_observability_meta(
                question,
                classification,
                retained_candidates,
                bundle,
                SolverResult(
                    qid=question.qid,
                    answer="",
                    solver="blocked_before_solver_result",
                    metadata={},
                ),
            )
        else:
            metadata = {}
        metadata.update({
            "domain": question.domain,
            "attempt_context_qid": _ctx_qid,
            "attempt_stage": _ctx_stage,
            "doc_ids": requested,
            "classifier_labels": [label.value for label in classification.labels],
            "classifier_reasons": dict(classification.reasons),
            "retrieved_docs": [str(candidate.doc_id) for candidate in retained_candidates],
            "evidence_count": len(retained_candidates),
            "evidence_by_doc": evidence_by_doc,
            "missing_doc_ids": missing,
            "fallback_used": False,
            "fallback_error": None,
            "fallback_disabled": True,
            "fallback_disabled_reason": reason,
            "error": str(exc),
            "answer_source": "error",
            "answer_validation": "blocking_invalid",
            "answer_validation_reason": reason,
            "production_integrity_checked": self.enforce_production_integrity,
            "final_state": "failed",
            "grounded": False,
        })
        return BlockingAnswerValidationError(
            question.qid, question.answer_format, "", f"{reason}: {exc}",
            metadata=metadata,
        )

    @staticmethod
    def _extract_token_meta(solver_result: SolverResult) -> Dict[str, int]:
        return {
            "prompt_tokens": solver_result.metadata.get("prompt_tokens", 0) or 0,
            "completion_tokens": solver_result.metadata.get("completion_tokens", 0) or 0,
            "total_tokens": solver_result.metadata.get("total_tokens", 0) or 0,
        }

    def process_many(self, questions: Sequence[Question]) -> List[PipelineResult]:
        results = [self.process_one(question) for question in questions]
        if self.writer is not None:
            write_checkpoint = getattr(self.writer, "write_checkpoint", None)
            write_final = getattr(self.writer, "write_final", None)
            selection_matches = getattr(
                self.writer, "selection_matches_final_contract", None
            )
            if callable(write_checkpoint):
                write_checkpoint(results)
                if (
                    callable(write_final)
                    and callable(selection_matches)
                    and selection_matches([question.qid for question in questions])
                ):
                    write_final(results)
            else:
                self.writer.write(results)
        return results

    def _fallback(
        self,
        question: Question,
        exc: Exception,
        *,
        classification: Optional[ClassificationResult] = None,
        candidates: Optional[Sequence[EvidenceCandidate]] = None,
        bundle: Optional[EvidenceBundle] = None,
    ) -> PipelineResult:
        classification = classification or self.classifier.classify(question)
        retained_candidates = list(candidates or [])
        fallback_bundle = bundle or self.assembler.assemble(
            question, classification, retained_candidates
        )
        try:
            solver_result = self.fallback_solver.solve(fallback_bundle)  # type: ignore[union-attr]
        except Exception as fallback_exc:
            raise BlockingAnswerValidationError(
                question.qid, question.answer_format, "",
                f"fallback_solver_error: {fallback_exc}; primary_error: {exc}",
                metadata={
                    "domain": question.domain,
                    "doc_ids": [str(value) for value in question.doc_ids],
                    "retrieved_docs": [str(candidate.doc_id) for candidate in retained_candidates],
                    "final_state": "failed",
                    "grounded": False,
                    "answer_source": "error",
                },
            ) from fallback_exc

        answer_contract = contract_from_question(question)
        validation = validate_submission_answer(
            solver_result.answer, question.answer_format, answer_contract=answer_contract
        )
        if not validation.valid:
            raise BlockingAnswerValidationError(
                question.qid, question.answer_format, solver_result.answer, validation.reason
            )
        token_meta = self._extract_token_meta(solver_result)
        evidence_by_doc: Dict[str, List[str]] = {}
        for candidate in retained_candidates:
            evidence_by_doc.setdefault(str(candidate.doc_id), []).append(str(candidate.source))
        requested = [str(doc_id) for doc_id in question.doc_ids]
        missing = [doc_id for doc_id in requested if doc_id not in evidence_by_doc]
        meta: Dict[str, Any] = {
            "domain": question.domain,
            "doc_ids": requested,
            "classifier_labels": [label.value for label in classification.labels],
            "classifier_reasons": dict(classification.reasons),
            "solver": solver_result.solver,
            "evidence_count": len(retained_candidates),
            "evidence_by_doc": evidence_by_doc,
            "missing_doc_ids": missing,
            "retrieval_fallbacks": [],
            "estimated_tokens": fallback_bundle.estimated_tokens,
            "degraded": True,
            "fallback_used": True,
            "error": str(exc),
            "fallback_error": None,
            "answer_format": question.answer_format,
            "answer_contract": contract_to_dict(answer_contract),
            "fallback_answer_valid": True,
            "fallback_raw_answer": solver_result.answer,
            "answer_validation": "fallback_used",
            "answer_validation_reason": validation.reason,
            "production_integrity_checked": self.enforce_production_integrity,
            "final_state": "accepted",
            "grounded": True,
            "answer_source": "fallback",
        }
        return PipelineResult(
            qid=question.qid,
            answer=validation.answer,
            classification=classification,
            solver_result=solver_result,
            prompt_tokens=token_meta["prompt_tokens"],
            completion_tokens=token_meta["completion_tokens"],
            total_tokens=token_meta["total_tokens"],
            fallback_used=True,
            error=str(exc),
            metadata=meta,
        )
