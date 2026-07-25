"""Evidence grouping, deduplication, and prompt context assembly."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question, QuestionLabel
from evidence.contract_exact_fields import ContractExactFieldEvidenceAugmenter
from evidence.prompt_budget import estimate_prompt_budget
from evidence.prompt_evidence_selection import (
    GlobalPromptEvidenceSelector,
    PromptEvidencePolicy,
)
from evidence.structured_tables import StructuredTableEvidenceAugmenter


_ALLOWED_SCOPE_EXPANSION_REASONS = frozenset({
    "corrective_retrieval",
    "structured_table_companion",
    "exact_field_companion",
    "declared_required_scope",
})


def _stable_doc_ids(values: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _candidate_doc_ids(candidates: Sequence[EvidenceCandidate]) -> list[str]:
    return _stable_doc_ids([candidate.doc_id for candidate in candidates])


def _retriever_audit_metadata(
    question: Question,
    candidates: Sequence[EvidenceCandidate],
) -> dict[str, Any]:
    """Read retriever truth without reconstructing it when the audit exists."""
    direct = getattr(candidates, "audit_metadata", None)
    if isinstance(direct, Mapping):
        return dict(direct)
    for candidate in candidates:
        metadata = dict(candidate.metadata or {})
        if metadata.get("retriever_scope_audit_source") == "retriever_call_boundary":
            return {
                key: metadata.get(key)
                for key in (
                    "scope_candidate_doc_ids",
                    "retriever_requested_doc_ids",
                    "retriever_resolved_doc_ids",
                    "retriever_missing_doc_ids",
                    "retrieved_doc_ids",
                    "retriever_scope_request_source",
                    "retriever_scope_audit_source",
                    "retriever_scope_provider_calls",
                    "scope_expansion_reasons",
                )
            }

    # Compatibility for hand-built fixtures and legacy retrievers only. The
    # source marker makes this fallback visible so production audits can fail
    # closed rather than mistaking an inferred request for retriever truth.
    effective_scope = _stable_doc_ids([*question.doc_ids, *question.candidate_doc_ids])
    observed = _candidate_doc_ids(candidates)
    return {
        "scope_candidate_doc_ids": effective_scope,
        "retriever_requested_doc_ids": effective_scope,
        "retriever_resolved_doc_ids": observed,
        "retriever_missing_doc_ids": [doc for doc in effective_scope if doc not in observed],
        "retrieved_doc_ids": observed,
        "retriever_scope_request_source": "legacy_sequence_inference",
        "retriever_scope_audit_source": "legacy_compatibility_fallback",
        "retriever_scope_provider_calls": 0,
        "scope_expansion_reasons": {},
    }


def _explicit_scope_expansion_reason(
    candidate: EvidenceCandidate,
    question: Question,
) -> str:
    explicit = str(candidate.metadata.get("scope_expansion_reason") or "").strip()
    if explicit:
        return explicit
    if str(candidate.doc_id) in {str(value) for value in question.doc_ids}:
        return "declared_required_scope"
    if candidate.retriever == "mineru_structured_table":
        return "structured_table_companion"
    if candidate.retriever == "contract_exact_field_verification":
        return "exact_field_companion"
    return ""


class GroupedEvidenceAssembler:
    """Assemble evidence by doc_id while preserving source boundaries.

    Token budgets per classification label are configurable via an optional
    ``token_budgets`` mapping passed to ``__init__``. When absent (or when a
    specific label is missing/invalid), the hard-coded defaults below are used,
    preserving the pre-P6b behavior exactly.
    """

    # Hard-coded defaults (P4/P6a baseline). Must never be lowered here.
    DEFAULT_BUDGETS: Dict[QuestionLabel, int] = {
        QuestionLabel.DEFAULT: 10000,
        QuestionLabel.FACT_LOOKUP: 10000,
        QuestionLabel.CLAUSE_LOOKUP: 20000,
        QuestionLabel.MULTI_OPTION: 35000,
        QuestionLabel.CROSS_DOC: 40000,
        QuestionLabel.CALCULATION: 50000,
    }

    def __init__(
        self,
        token_budgets: Optional[Mapping[str, Any]] = None,
        *,
        enable_prompt_evidence_compaction: bool = False,
        prompt_evidence_policy: Optional[Mapping[str, Any]] = None,
        prompt_budget_model: str = "qwen3.7-max",
        structured_table_root: Path | str | None = None,
        enable_structured_table_verification: bool = False,
        enable_structured_table_prompt_injection: bool = False,
        structured_table_max_rows_per_doc: int = 12,
        contract_exact_field_full_text_root: Path | str | None = None,
        contract_exact_field_retrieval_root: Path | str | None = None,
        enable_contract_exact_field_verification: bool = False,
        contract_exact_field_max_windows_per_doc: int = 3,
        insurance_clause_full_text_root: Path | str | None = None,
        insurance_clause_product_catalog_path: Path | str | None = None,
        insurance_clause_registry_path: Path | str | None = None,
        allow_curated_insurance_fixture_for_offline_evaluation: bool = False,
        enable_insurance_clause_verification: bool = False,
        insurance_calculation_full_text_root: Path | str | None = None,
        insurance_calculation_product_catalog_path: Path | str | None = None,
        enable_insurance_calculation_verification: bool = False,
        regulatory_data_root: Path | str | None = None,
        enable_regulatory_option_verification: bool = False,
    ) -> None:
        self._budget_by_label: Dict[QuestionLabel, int] = dict(self.DEFAULT_BUDGETS)
        self._prompt_evidence_compaction_enabled = bool(
            enable_prompt_evidence_compaction
        )
        self._prompt_evidence_policy = PromptEvidencePolicy.from_mapping(
            prompt_evidence_policy
        )
        self._prompt_evidence_selector = GlobalPromptEvidenceSelector(
            self._prompt_evidence_policy
        )
        self._prompt_budget_model = str(prompt_budget_model or "qwen3.7-max")
        self._structured_table_verification_enabled = bool(
            enable_structured_table_verification
        )
        self._structured_table_root = (
            Path(structured_table_root) if structured_table_root else None
        )
        self._structured_table_prompt_injection_enabled = bool(
            enable_structured_table_prompt_injection
        )
        self._structured_table_augmenter = (
            StructuredTableEvidenceAugmenter(
                self._structured_table_root,
                max_rows_per_doc=structured_table_max_rows_per_doc,
            )
            if self._structured_table_verification_enabled and structured_table_root
            else None
        )
        self._insurance_clause_verification_enabled = bool(
            enable_insurance_clause_verification
        )
        self._insurance_clause_full_text_root = (
            Path(insurance_clause_full_text_root)
            if insurance_clause_full_text_root else None
        )
        self._insurance_clause_product_catalog_path = (
            Path(insurance_clause_product_catalog_path)
            if insurance_clause_product_catalog_path else None
        )
        self._insurance_clause_registry_path = (
            Path(insurance_clause_registry_path)
            if insurance_clause_registry_path else None
        )
        self._allow_curated_insurance_fixture_for_offline_evaluation = bool(
            allow_curated_insurance_fixture_for_offline_evaluation
        )
        self._insurance_calculation_verification_enabled = bool(
            enable_insurance_calculation_verification
        )
        self._insurance_calculation_full_text_root = (
            Path(insurance_calculation_full_text_root)
            if insurance_calculation_full_text_root else None
        )
        self._insurance_calculation_product_catalog_path = (
            Path(insurance_calculation_product_catalog_path)
            if insurance_calculation_product_catalog_path else None
        )
        self._regulatory_option_verification_enabled = bool(
            enable_regulatory_option_verification
        )
        self._regulatory_data_root = (
            Path(regulatory_data_root) if regulatory_data_root else None
        )
        self._contract_exact_field_verification_enabled = bool(
            enable_contract_exact_field_verification
        )
        self._contract_exact_field_augmenter = (
            ContractExactFieldEvidenceAugmenter(
                full_text_root=contract_exact_field_full_text_root,
                retrieval_root=contract_exact_field_retrieval_root,
                max_windows_per_field_doc=contract_exact_field_max_windows_per_doc,
            )
            if (
                self._contract_exact_field_verification_enabled
                and contract_exact_field_full_text_root
                and contract_exact_field_retrieval_root
            )
            else None
        )
        # Track which labels were actually overridden by valid config values so
        # we can report a precise source in metadata without crashing on
        # unknown/invalid keys.
        self._overridden_labels: set = set()

        if token_budgets:
            self._apply_config_budgets(token_budgets)

        self._budget_source = "config" if self._overridden_labels else "default"

    def _apply_config_budgets(self, token_budgets: Mapping[str, Any]) -> None:
        """Merge user-supplied budget overrides into ``self._budget_by_label``.

        - Config keys are matched against ``QuestionLabel`` string values
          (e.g. ``"cross_doc"`` -> ``QuestionLabel.CROSS_DOC``).
        - Unknown keys are silently ignored (per task spec: "unknown config
          keys should be ignored ... must not crash normal runs").
        - Values are coerced to positive ``int``. Non-positive or
          non-coercible values fall back to the default for that label.
        """
        # Map QuestionLabel string values back to enum members for lookup.
        label_by_value = {label.value: label for label in QuestionLabel}
        for key, raw_value in token_budgets.items():
            label = label_by_value.get(str(key))
            if label is None:
                continue  # unknown key -> ignore
            value = self._coerce_positive_int(raw_value)
            if value is not None:
                self._budget_by_label[label] = value
                self._overridden_labels.add(label)

    @staticmethod
    def _coerce_positive_int(raw: Any) -> Optional[int]:
        """Return a positive int, or None if the value is unusable.

        Accepts ints, floats with integral value, and numeric strings. Booleans
        are rejected (``bool`` is a subclass of ``int`` but not a budget).
        """
        if isinstance(raw, bool):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def assemble(
        self,
        question: Question,
        classification: ClassificationResult,
        candidates: Sequence[EvidenceCandidate],
    ) -> EvidenceBundle:
        retrieval_audit = _retriever_audit_metadata(question, candidates)
        scope_candidate_doc_ids = _stable_doc_ids(
            retrieval_audit.get("scope_candidate_doc_ids") or ()
        )
        retriever_requested_doc_ids = _stable_doc_ids(
            retrieval_audit.get("retriever_requested_doc_ids") or ()
        )
        retriever_resolved_doc_ids = _stable_doc_ids(
            retrieval_audit.get("retriever_resolved_doc_ids") or ()
        )
        retriever_missing_doc_ids = _stable_doc_ids(
            retrieval_audit.get("retriever_missing_doc_ids") or ()
        )
        retrieved_doc_ids = _stable_doc_ids(
            retrieval_audit.get("retrieved_doc_ids") or ()
        )
        original_candidates = list(candidates)
        actual_retrieved_doc_ids = _candidate_doc_ids(original_candidates)
        original_count = len(original_candidates)

        # multi-slot candidate scope is retrieval/augmentation scope only. A local
        # replaced Question lets existing sidecar augmenters scan those docs
        # without mutating the bundle's required-doc truth.
        augmentation_question = question
        if not question.doc_ids and scope_candidate_doc_ids:
            augmentation_question = replace(
                question,
                doc_ids=tuple(scope_candidate_doc_ids),
            )

        table_audit: dict[str, Any] = {
            "enabled": False,
            "table_rows_added": 0,
        }
        verification_candidates = list(original_candidates)
        if self._structured_table_augmenter is not None:
            verification_candidates, table_audit = self._structured_table_augmenter.augment(
                augmentation_question, verification_candidates
            )
        exact_field_audit: dict[str, Any] = {
            "enabled": False,
            "candidates_added": 0,
            "fields": [],
        }
        if self._contract_exact_field_augmenter is not None:
            verification_candidates, exact_field_audit = (
                self._contract_exact_field_augmenter.augment(
                    augmentation_question, verification_candidates
                )
            )

        # Package M explicit dual view. Verification sidecar rows are not
        # visible to routers or solvers unless prompt injection is explicitly
        # enabled as a separate experiment.
        prompt_source_candidates = (
            verification_candidates
            if self._structured_table_prompt_injection_enabled
            else original_candidates
        )
        token_budget = self._select_budget(classification)
        original_prompt_context = self._render_context(
            question, prompt_source_candidates, token_budget
        )
        prompt_selection = None
        solver_candidates = list(prompt_source_candidates)
        if self._prompt_evidence_compaction_enabled:
            prompt_selection = self._prompt_evidence_selector.select(
                question,
                classification,
                prompt_source_candidates,
                scope_candidate_doc_ids=scope_candidate_doc_ids,
            )
            solver_candidates = list(prompt_selection.selected_candidates)
        prompt_context = self._render_context(question, solver_candidates, token_budget)
        prompt_budget_estimate = estimate_prompt_budget(
            model_id=self._prompt_budget_model,
            rendered_context_chars=len(prompt_context),
        )
        assembler_used_doc_ids = _candidate_doc_ids(solver_candidates)
        verifier_candidate_doc_ids = _candidate_doc_ids(verification_candidates)
        assembly_visible_doc_ids = _stable_doc_ids(
            [*assembler_used_doc_ids, *verifier_candidate_doc_ids]
        )
        out_of_scope_doc_ids = [
            doc_id for doc_id in assembly_visible_doc_ids if doc_id not in scope_candidate_doc_ids
        ]
        scope_expansion_reasons = {
            str(doc_id): str(reason)
            for doc_id, reason in dict(
                retrieval_audit.get("scope_expansion_reasons") or {}
            ).items()
            if str(doc_id) and str(reason)
        }
        candidates_by_doc: dict[str, list[EvidenceCandidate]] = defaultdict(list)
        for candidate in verification_candidates:
            candidates_by_doc[str(candidate.doc_id)].append(candidate)
        for doc_id in out_of_scope_doc_ids:
            if doc_id in scope_expansion_reasons:
                continue
            reason = next(
                (
                    _explicit_scope_expansion_reason(candidate, question)
                    for candidate in candidates_by_doc.get(doc_id, [])
                    if _explicit_scope_expansion_reason(candidate, question)
                ),
                "",
            )
            if reason:
                scope_expansion_reasons[doc_id] = reason
        unknown_scope_expansion_reason_doc_ids = sorted(
            doc_id
            for doc_id, reason in scope_expansion_reasons.items()
            if reason not in _ALLOWED_SCOPE_EXPANSION_REASONS
        )
        out_of_scope_without_reason_doc_ids = sorted(
            doc_id
            for doc_id in out_of_scope_doc_ids
            if doc_id not in scope_expansion_reasons
            or scope_expansion_reasons[doc_id] not in _ALLOWED_SCOPE_EXPANSION_REASONS
        )
        return EvidenceBundle(
            question=question,
            classification=classification,
            candidates=tuple(solver_candidates),
            prompt_context=prompt_context,
            estimated_tokens=(
                prompt_budget_estimate.prompt_estimated_tokens
                if self._prompt_evidence_compaction_enabled
                else max(1, len(prompt_context) // 2)
            ),
            metadata={
                "token_budget": token_budget,
                "prompt_evidence_compaction_enabled": self._prompt_evidence_compaction_enabled,
                "prompt_original_context_chars": len(original_prompt_context),
                "prompt_rendered_context_chars": len(prompt_context),
                "prompt_budget_model": self._prompt_budget_model,
                "prompt_budget_estimated_tokens": prompt_budget_estimate.prompt_estimated_tokens,
                "prompt_completion_reserve_tokens": prompt_budget_estimate.completion_reserve_tokens,
                "prompt_estimated_total_tokens": prompt_budget_estimate.estimated_total_tokens,
                "prompt_budget_policy_source": prompt_budget_estimate.policy_source,
                "prompt_budget_within_target": prompt_budget_estimate.within_target,
                "prompt_budget_within_hard_cap": prompt_budget_estimate.within_hard_cap,
                **(prompt_selection.to_metadata() if prompt_selection is not None else {}),
                "evidence_budget_source": self._budget_source,
                "scope_candidate_doc_ids": scope_candidate_doc_ids,
                "retriever_requested_doc_ids": retriever_requested_doc_ids,
                "retriever_resolved_doc_ids": retriever_resolved_doc_ids,
                "retriever_missing_doc_ids": retriever_missing_doc_ids,
                "retrieved_doc_ids": retrieved_doc_ids,
                "retriever_returned_doc_ids": actual_retrieved_doc_ids,
                "retriever_scope_request_source": retrieval_audit.get(
                    "retriever_scope_request_source", ""
                ),
                "retriever_scope_audit_source": retrieval_audit.get(
                    "retriever_scope_audit_source", ""
                ),
                "retriever_scope_provider_calls": int(
                    retrieval_audit.get("retriever_scope_provider_calls", 0) or 0
                ),
                "assembler_used_doc_ids": assembler_used_doc_ids,
                "solver_available_doc_ids": assembler_used_doc_ids,
                "verifier_candidate_doc_ids": verifier_candidate_doc_ids,
                "out_of_scope_doc_ids": sorted(out_of_scope_doc_ids),
                "scope_expansion_reasons": scope_expansion_reasons,
                "out_of_scope_without_reason_doc_ids": out_of_scope_without_reason_doc_ids,
                "unknown_scope_expansion_reason_doc_ids": unknown_scope_expansion_reason_doc_ids,
                "augmentation_scope_source": (
                    "candidate_scope" if augmentation_question is not question else "declared_scope"
                ),
                "structured_table_evidence": table_audit,
                "structured_table_verification_enabled": self._structured_table_verification_enabled,
                "structured_table_root": (
                    str(self._structured_table_root)
                    if self._structured_table_root is not None
                    else ""
                ),
                "structured_table_prompt_injection_enabled": self._structured_table_prompt_injection_enabled,
                "structured_table_pre_count": original_count,
                "structured_table_verification_post_count": len(verification_candidates),
                "structured_table_solver_candidate_count": len(solver_candidates),
                "structured_table_prompt_candidate_count": len(solver_candidates),
                "contract_exact_field_evidence": exact_field_audit,
                "contract_exact_field_verification_enabled": self._contract_exact_field_verification_enabled,
                "contract_exact_field_verification_post_count": len(verification_candidates),
                "insurance_clause_verification_enabled": self._insurance_clause_verification_enabled,
                "insurance_clause_full_text_root": (
                    str(self._insurance_clause_full_text_root)
                    if self._insurance_clause_full_text_root is not None else ""
                ),
                "insurance_clause_product_catalog_path": (
                    str(self._insurance_clause_product_catalog_path)
                    if self._insurance_clause_product_catalog_path is not None else ""
                ),
                "insurance_clause_registry_path": (
                    str(self._insurance_clause_registry_path)
                    if self._insurance_clause_registry_path is not None else ""
                ),
                "allow_curated_insurance_fixture_for_offline_evaluation": (
                    self._allow_curated_insurance_fixture_for_offline_evaluation
                ),
                "insurance_clause_prompt_injection_enabled": False,
                "insurance_calculation_verification_enabled": self._insurance_calculation_verification_enabled,
                "insurance_calculation_full_text_root": (
                    str(self._insurance_calculation_full_text_root)
                    if self._insurance_calculation_full_text_root is not None else ""
                ),
                "insurance_calculation_product_catalog_path": (
                    str(self._insurance_calculation_product_catalog_path)
                    if self._insurance_calculation_product_catalog_path is not None else ""
                ),
                "insurance_calculation_prompt_injection_enabled": False,
                "regulatory_option_verification_enabled": self._regulatory_option_verification_enabled,
                "regulatory_data_root": (
                    str(self._regulatory_data_root)
                    if self._regulatory_data_root is not None else ""
                ),
                "regulatory_option_prompt_injection_enabled": False,
            },
            verification_candidates=tuple(verification_candidates),
        )

    def _select_budget(self, classification: ClassificationResult) -> int:
        budget = self._budget_by_label[QuestionLabel.DEFAULT]
        for label in classification.labels:
            budget = max(budget, self._budget_by_label.get(label, budget))
        return budget

    def _render_context(
        self, question: Question, candidates: Sequence[EvidenceCandidate], token_budget: int
    ) -> str:
        by_doc: dict[str, List[EvidenceCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_doc[candidate.doc_id].append(candidate)

        parts = [f"[QUESTION] {question.text}", "[OPTIONS]"]
        parts.extend(f"{key}. {value}" for key, value in sorted(question.options.items()))
        parts.append("[EVIDENCE]")

        max_chars = token_budget * 2
        # Required-document truth remains ``question.doc_ids``.  For multi-slot
        # questions that declare no doc_ids, render only the documents actually
        # retrieved by the candidate-scope resolver; this is retrieval scope,
        # not a claim that every candidate is required by the question.
        render_doc_ids = [str(value) for value in question.doc_ids]
        if not render_doc_ids:
            render_doc_ids = list(dict.fromkeys(str(candidate.doc_id) for candidate in candidates))
        for doc_id in render_doc_ids:
            parts.append(f"\n[DOC {doc_id}]")
            doc_candidates = by_doc.get(str(doc_id), [])
            if not doc_candidates:
                parts.append("No retrieved evidence for this document.")
                continue
            for idx, candidate in enumerate(doc_candidates, start=1):
                block = (
                    f"[SOURCE {idx}] {candidate.source}\n"
                    f"{candidate.before_text}\n{candidate.text}\n{candidate.after_text}"
                )
                parts.append(block.strip())
                if len("\n".join(parts)) >= max_chars:
                    break
        return "\n\n".join(parts)[:max_chars]
