"""Production-path integrity enforcement for workflow and artifact writes."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from answer_contract import contract_from_answer_format, validate_answer_against_contract
from contracts import ClassificationResult, PipelineResult, Question, QuestionLabel, SolverResult, VerificationResult
from verification.runtime_integrity import authoritative_records, validate_runtime_record
from verification.calculation_grounding import integrity_blocking_reasons
from verification.dual_lineage import (
    accepted_final_state,
    final_answer_authority as choose_final_answer_authority,
    solver_lineage,
    verifier_lineage,
)


class ProductionIntegrityError(ValueError):
    """Raised when an unsafe result would otherwise reach a final artifact."""


def _selected_option_letters(answer: str) -> set[str]:
    return {char for char in str(answer or "").upper() if "A" <= char <= "Z"}




def _normalize_answer(answer: str) -> str:
    letters = sorted({char for char in str(answer or "").upper() if "A" <= char <= "Z"})
    return "".join(letters)

def _assess_option_gate(original_answer: str, option_verdicts: Mapping[str, Any]) -> dict[str, list[str]]:
    """Classify option self-check verdicts into blocking and non-blocking groups.

    Missing evidence for an unselected option is normal and must not block the
    whole answer. Blocking is limited to evidence states that can make the
    selected answer unsafe: selected options lacking support, selected options
    contradicted by evidence, unselected options supported by evidence, and
    special unresolved/manual-block verdicts.
    """
    selected = _selected_option_letters(original_answer)
    selected_unresolved: list[str] = []
    selected_contradicted: list[str] = []
    unselected_supported: list[str] = []
    benign_unselected_missing: list[str] = []
    special_unresolved: list[str] = []
    special_routes = {"manual_block", "opaque_option_text", "strict_compound_safety"}
    for raw_letter, raw_verdict in option_verdicts.items():
        letter = str(raw_letter).upper()
        verdict = raw_verdict if isinstance(raw_verdict, Mapping) else {}
        status = str(verdict.get("status") or "").lower()
        route = str(verdict.get("claim_route") or verdict.get("false_positive_type") or "")
        if letter in selected:
            if status in {"missing", "unresolved"}:
                selected_unresolved.append(letter)
            elif status == "contradicted":
                selected_contradicted.append(letter)
        else:
            if status == "supported":
                unselected_supported.append(letter)
            elif status in {"missing", ""}:
                benign_unselected_missing.append(letter)
            elif status == "unresolved" and route in special_routes:
                special_unresolved.append(letter)
            elif status == "unresolved":
                benign_unselected_missing.append(letter)
    return {
        "selected_unresolved": sorted(set(selected_unresolved)),
        "selected_contradicted": sorted(set(selected_contradicted)),
        "unselected_supported": sorted(set(unselected_supported)),
        "benign_unselected_missing": sorted(set(benign_unselected_missing)),
        "special_unresolved": sorted(set(special_unresolved)),
    }


def _option_match_letters(value: Any) -> set[str]:
    if isinstance(value, str):
        return _selected_option_letters(value)
    if isinstance(value, (list, tuple, set)):
        return {str(item).upper() for item in value if len(str(item)) == 1 and "A" <= str(item).upper() <= "Z"}
    return set()


def _calculation_grounding_clean(grounding: Mapping[str, Any] | None) -> bool:
    if not isinstance(grounding, Mapping) or not grounding:
        return False
    return bool(
        grounding.get("calculation_complete") is True
        and grounding.get("option_match_unique") is True
        and not grounding.get("coverage_gap")
        and not grounding.get("unresolved_variables")
        and not grounding.get("unused_material_variables")
    )


def _grounding_supported_selected_options(answer: str, grounding: Mapping[str, Any] | None) -> set[str]:
    """Return selected options directly supported by computation grounding.

    This is intentionally narrow: it only trusts computation grounding when the
    calculation payload is complete, uniquely mapped to options, and each
    selected option has a true option_evaluation with source/calc refs.
    """
    selected = _selected_option_letters(answer)
    if not selected or not _calculation_grounding_clean(grounding):
        return set()
    matched = _option_match_letters(grounding.get("option_match"))
    if not selected <= matched:
        return set()
    supported: set[str] = set()
    raw_evaluations = grounding.get("option_evaluations", [])
    evaluations = raw_evaluations if isinstance(raw_evaluations, Sequence) and not isinstance(raw_evaluations, (str, bytes)) else []
    for raw_slot in evaluations:
        if not isinstance(raw_slot, Mapping):
            continue
        option = str(raw_slot.get("option") or raw_slot.get("option_label") or "").upper()
        if option not in selected:
            continue
        verdict = str(raw_slot.get("verdict") or raw_slot.get("status") or "").lower()
        has_refs = bool(raw_slot.get("evidence_refs") or raw_slot.get("calculation_refs"))
        if verdict in {"true", "supported"} and has_refs:
            supported.add(option)
    return supported


def _assess_freeform_final_state(
    *,
    requested_docs: Sequence[str],
    retrieved_docs: Sequence[str],
    solver_result: SolverResult,
    final_answer: str,
    submission_answers: Sequence[str],
    expected_submission_slots: int | None,
    generic_open_qa: bool = False,
) -> dict[str, Any]:
    solver_meta = dict(solver_result.metadata or {})
    solver_channel = solver_lineage(solver_meta)
    used = list(solver_channel.doc_ids)
    retrieved = sorted({str(value) for value in retrieved_docs})
    required = sorted({str(value) for value in requested_docs})
    answers = tuple(str(value).strip() for value in submission_answers)
    if not answers:
        raw_answers = solver_meta.get("submission_answers")
        if isinstance(raw_answers, Sequence) and not isinstance(raw_answers, (str, bytes)):
            answers = tuple(str(value).strip() for value in raw_answers)
    expected = expected_submission_slots
    if expected is None:
        raw_expected = solver_meta.get("expected_submission_slots")
        try:
            expected = int(raw_expected) if raw_expected is not None else None
        except (TypeError, ValueError):
            expected = None

    contract = contract_from_answer_format("freeform")
    slot_validations = [
        validate_answer_against_contract(answer, contract).to_dict()
        for answer in answers
    ]
    finish_reason = str(solver_meta.get("finish_reason") or "")
    truncation_risk = bool(
        solver_meta.get("truncation_risk", False) or finish_reason == "length"
    )
    llm_error = bool(solver_meta.get("llm_error", False))
    answer_source = str(solver_meta.get("answer_source") or "generated")
    unsafe_answer_source = answer_source in {
        "error",
        "blocked_freeform_contract",
        "blocked_freeform_parse",
        "unsupported_guess",
        "unsupported_guess_truncated",
        "dry_run",
    }
    structured_freeform_metadata = any(
        key in solver_meta
        for key in (
            "freeform_parse_valid",
            "freeform_slot_bindings",
            "freeform_all_slot_bindings_valid",
            "freeform_binding_auditable",
        )
    )
    parse_valid = (
        solver_meta.get("freeform_parse_valid") is True
        if structured_freeform_metadata
        else bool(generic_open_qa)
    )
    raw_slot_bindings = solver_meta.get("freeform_slot_bindings")
    slot_bindings = [
        dict(item)
        for item in raw_slot_bindings
        if isinstance(item, Mapping)
    ] if isinstance(raw_slot_bindings, Sequence) and not isinstance(raw_slot_bindings, (str, bytes)) else []
    all_slot_formats_valid = (
        solver_meta.get("freeform_all_slot_formats_valid") is True
        if structured_freeform_metadata
        else bool(generic_open_qa and all(item.get("valid") for item in slot_validations))
    )
    all_slot_results_match = (
        solver_meta.get("freeform_all_slot_results_match") is True
        if structured_freeform_metadata
        else bool(generic_open_qa)
    )
    all_slot_bindings_valid = (
        solver_meta.get("freeform_all_slot_bindings_valid") is True
        if structured_freeform_metadata
        else bool(generic_open_qa and retrieved)
    )
    binding_auditable = bool(
        (
            solver_meta.get("freeform_binding_auditable") is True
            and all_slot_bindings_valid
        )
        if structured_freeform_metadata
        else (generic_open_qa and retrieved)
    )
    raw_binding_reasons = solver_meta.get("freeform_binding_blocking_reasons")
    binding_blocking_reasons = [
        str(value)
        for value in raw_binding_reasons
        if str(value).strip()
    ] if isinstance(raw_binding_reasons, Sequence) and not isinstance(raw_binding_reasons, (str, bytes)) else []
    calculation_complete = (
        solver_meta.get("computation_complete") is True
        if structured_freeform_metadata or str(solver_result.solver) == "calculation"
        else bool(generic_open_qa)
    )

    blocking_reasons: list[str] = []
    if expected not in {1, 2, 3, 4}:
        blocking_reasons.append("missing_submission_slot_contract")
    elif len(answers) != expected:
        blocking_reasons.append("submission_slot_count_mismatch")
    if any(not answer for answer in answers):
        blocking_reasons.append("empty_submission_slot")
    if any(not validation.get("valid") for validation in slot_validations):
        blocking_reasons.append("freeform_format_validation_failed")
    if answers and final_answer != answers[0]:
        blocking_reasons.append("freeform_primary_answer_mismatch")
    if not parse_valid:
        blocking_reasons.append("freeform_parse_invalid")
    if not all_slot_formats_valid:
        blocking_reasons.append("freeform_kind_validation_failed")
    if not all_slot_results_match:
        blocking_reasons.append("freeform_slot_result_binding_failed")
    if not binding_auditable:
        blocking_reasons.append("freeform_binding_not_auditable")
    blocking_reasons.extend(binding_blocking_reasons)
    if not calculation_complete:
        blocking_reasons.append("calculation_incomplete")
    if truncation_risk:
        blocking_reasons.append("truncation_risk")
    if llm_error:
        blocking_reasons.append("llm_error")
    if generic_open_qa and not retrieved:
        blocking_reasons.append("missing_evidence")
    precise_lineage_required = bool(
        not generic_open_qa
        or str(solver_result.solver) in {"calculation", "cross_doc"}
    )
    if precise_lineage_required and not solver_channel.complete:
        blocking_reasons.append("used_doc_lineage_unknown")
    if used and not set(used) <= set(retrieved):
        blocking_reasons.append("used_doc_lineage_outside_retrieval")
    if unsafe_answer_source:
        blocking_reasons.append("unsafe_answer_source")

    blocking_reasons = sorted(set(blocking_reasons))
    final_state = "blocked" if blocking_reasons else "accepted"
    return {
        "production_integrity_checked": True,
        "production_integrity_path": "generic_open_qa" if generic_open_qa else "freeform",
        "required_docs": required,
        "retrieved_docs": retrieved,
        "used_docs": used,
        "used_docs_source": solver_channel.source,
        "usage_lineage_known": solver_channel.complete,
        "solver_used_doc_ids": list(solver_channel.doc_ids),
        "solver_source_refs": [dict(item) for item in solver_channel.source_refs],
        "solver_lineage_source": solver_channel.source,
        "solver_lineage_complete": solver_channel.complete,
        "solver_lineage_errors": list(solver_channel.errors),
        "verifier_evidence_doc_ids": [],
        "verifier_source_refs": [],
        "verifier_lineage_source": "not_applicable_freeform",
        "verifier_lineage_complete": True,
        "verifier_lineage_errors": [],
        "final_answer_authority": "solver",
        "solver_answer": str(solver_result.answer or ""),
        "integrity_final_answer": final_answer,
        "missing_required_docs": sorted(set(required) - set(retrieved)),
        "unused_required_docs": sorted(set(required) - set(used)),
        "cross_document_complete": True,
        "cross_document_retrieval_complete": True,
        "cross_document_usage_complete": True,
        "cross_document_usage_warning": False,
        "option_evidence_source": "not_applicable_freeform",
        "typed_option_evidence_trusted": False,
        "typed_option_evidence_fail_closed": False,
        "typed_option_evidence": None,
        "legacy_self_check_overridden": False,
        "legacy_option_gate": {
            "selected_unresolved": [],
            "selected_contradicted": [],
            "unselected_supported": [],
            "benign_unselected_missing": [],
            "special_unresolved": [],
        },
        "legacy_correction_proposal": "",
        "legacy_correction_differs": False,
        "unresolved_options": [],
        "selected_unresolved_options": [],
        "selected_contradicted_options": [],
        "unselected_supported_options": [],
        "benign_unselected_missing_options": [],
        "special_unresolved_options": [],
        "correction_proposal": "",
        "correction_differs": False,
        "correction_gate_required": False,
        "correction_reconcile_required": False,
        "option_evidence_review_required": False,
        "option_evidence_unresolved_hard": False,
        "option_integrity_issues": [],
        "calculation_complete": calculation_complete,
        "calculation_grounding": None,
        "calculation_grounding_blocking_reasons": [],
        "unused_material_variables": [],
        "no_unique_option_match": False,
        "finish_reason": finish_reason,
        "truncation_risk": truncation_risk,
        "truncation_hard_block": truncation_risk,
        "extraction_truncation_hard": truncation_risk,
        "match_truncated": False,
        "supported_by_calculation_grounding": [],
        "answer_source": answer_source,
        "unsafe_answer_source": unsafe_answer_source,
        "freeform_submission_answers": list(answers),
        "freeform_expected_submission_slots": expected,
        "freeform_slot_validations": slot_validations,
        "freeform_parse_valid": parse_valid,
        "freeform_slot_bindings": slot_bindings,
        "freeform_all_slot_formats_valid": all_slot_formats_valid,
        "freeform_all_slot_results_match": all_slot_results_match,
        "freeform_all_slot_bindings_valid": all_slot_bindings_valid,
        "freeform_binding_blocking_reasons": sorted(set(binding_blocking_reasons)),
        "freeform_binding_auditable": binding_auditable,
        "blocking_reasons": blocking_reasons,
        "final_state": final_state,
        "grounded": final_state == "accepted",
    }


def assess_final_state(
    *,
    labels: Sequence[QuestionLabel],
    requested_docs: Sequence[str],
    retrieved_docs: Sequence[str],
    solver_result: SolverResult,
    verification: VerificationResult | None,
    typed_option_evidence: Mapping[str, Any] | None = None,
    final_answer: str | None = None,
    answer_format: str = "",
    submission_answers: Sequence[str] = (),
    expected_submission_slots: int | None = None,
    generic_open_qa: bool = False,
) -> dict[str, Any]:
    """Derive production final-state metadata from real workflow objects."""
    effective_answer = str(final_answer if final_answer is not None else solver_result.answer or "")
    if str(answer_format or "").strip().lower() == "freeform":
        return _assess_freeform_final_state(
            requested_docs=requested_docs,
            retrieved_docs=retrieved_docs,
            solver_result=solver_result,
            final_answer=effective_answer,
            submission_answers=submission_answers,
            expected_submission_slots=expected_submission_slots,
            generic_open_qa=generic_open_qa,
        )

    requested = [str(value) for value in requested_docs]
    retrieved = sorted({str(value) for value in retrieved_docs})
    solver_meta = dict(solver_result.metadata or {})
    effective_answer = str(final_answer if final_answer is not None else solver_result.answer or "")
    solver_channel = solver_lineage(solver_meta)
    used = list(solver_channel.doc_ids)
    used_source = solver_channel.source
    usage_lineage_known = solver_channel.complete

    required = sorted(set(requested))
    missing_docs = sorted(set(required) - set(retrieved))
    unused_docs = sorted(set(required) - set(used))
    cross_doc = QuestionLabel.CROSS_DOC in set(labels) or len(required) > 1
    cross_document_retrieval_complete = not cross_doc or not missing_docs
    cross_document_usage_complete = not cross_doc or set(required) == set(used)
    cross_document_complete = cross_document_retrieval_complete and cross_document_usage_complete
    cross_document_usage_warning = bool(
        cross_doc and cross_document_retrieval_complete and usage_lineage_known and unused_docs
    )

    unresolved_options: list[str] = []
    selected_unresolved_options: list[str] = []
    selected_contradicted_options: list[str] = []
    unselected_supported_options: list[str] = []
    benign_unselected_missing_options: list[str] = []
    special_unresolved_options: list[str] = []
    option_issues: list[str] = []
    self_check = None
    if verification is not None:
        self_check = dict(verification.metadata or {}).get("self_check")
    correction_proposal = ""
    correction_differs = False
    correction_gate_required = False
    if isinstance(self_check, Mapping):
        verdicts = self_check.get("option_verdicts", {})
        if isinstance(verdicts, Mapping):
            gate = _assess_option_gate(effective_answer, verdicts)
            selected_unresolved_options = gate["selected_unresolved"]
            selected_contradicted_options = gate["selected_contradicted"]
            unselected_supported_options = gate["unselected_supported"]
            benign_unselected_missing_options = gate["benign_unselected_missing"]
            special_unresolved_options = gate["special_unresolved"]
            unresolved_options = sorted(set(
                selected_unresolved_options
                + selected_contradicted_options
                + unselected_supported_options
                + special_unresolved_options
            ))
        raw_proposal = self_check.get("correction_proposal")
        if raw_proposal is not None:
            correction_proposal = str(raw_proposal)
        correction_differs = bool(self_check.get("correction_differs", False))
        if correction_proposal:
            correction_differs = _normalize_answer(correction_proposal) != _normalize_answer(effective_answer)
        correction_gate_required = correction_differs
        raw_issues = self_check.get("issues", [])
        if isinstance(raw_issues, list):
            option_issues = [str(value) for value in raw_issues]

    legacy_option_gate = {
        "selected_unresolved": list(selected_unresolved_options),
        "selected_contradicted": list(selected_contradicted_options),
        "unselected_supported": list(unselected_supported_options),
        "benign_unselected_missing": list(benign_unselected_missing_options),
        "special_unresolved": list(special_unresolved_options),
    }
    legacy_correction_proposal = correction_proposal
    legacy_correction_differs = correction_differs
    typed_map = dict(typed_option_evidence or {}) if isinstance(typed_option_evidence, Mapping) else {}
    typed_verdicts = typed_map.get("option_verdicts")
    typed_authoritative = bool(
        typed_map.get("trusted_for_production") is True
        and isinstance(typed_verdicts, Mapping)
        and typed_verdicts
    )
    typed_fail_closed = bool(
        typed_map.get("fail_closed_on_untrusted") is True
        and isinstance(typed_verdicts, Mapping)
    )
    option_evidence_source = "legacy_self_check" if isinstance(self_check, Mapping) else "none"
    if typed_authoritative or typed_fail_closed:
        typed_gate = _assess_option_gate(effective_answer, typed_verdicts)
        selected_unresolved_options = typed_gate["selected_unresolved"]
        selected_contradicted_options = typed_gate["selected_contradicted"]
        unselected_supported_options = typed_gate["unselected_supported"]
        benign_unselected_missing_options = typed_gate["benign_unselected_missing"]
        special_unresolved_options = typed_gate["special_unresolved"]
        unresolved_options = sorted(set(
            selected_unresolved_options
            + selected_contradicted_options
            + unselected_supported_options
            + special_unresolved_options
        ))
        typed_proposal = typed_map.get("correction_proposal") or typed_map.get("typed_supported_answer")
        correction_proposal = str(typed_proposal or "")
        correction_differs = bool(
            correction_proposal
            and _normalize_answer(correction_proposal) != _normalize_answer(effective_answer)
        )
        correction_gate_required = correction_differs
        option_issues = [str(value) for value in typed_map.get("trust_failures", [])]
        option_evidence_source = "typed_option_evidence"

    calculation_grounding = solver_meta.get("calculation_grounding")
    calculation_grounding_map = calculation_grounding if isinstance(calculation_grounding, Mapping) else None
    calculation_grounding_blocking_reasons = integrity_blocking_reasons(calculation_grounding_map)
    calculation_complete = not bool(
        solver_meta.get("calculation_incomplete", False)
        or solver_meta.get("computation_complete") is False
        or "calculation_incomplete" in calculation_grounding_blocking_reasons
    )

    answer_source = str(solver_meta.get("answer_source") or "generated")
    verifier_channel = verifier_lineage(
        typed_map,
        defined_option_labels=tuple(str(label).upper() for label in (typed_verdicts or {})),
    )
    answer_authority = choose_final_answer_authority(
        solver_answer=str(solver_result.answer or ""),
        final_answer=effective_answer,
        answer_source=answer_source,
        solver=solver_channel,
        verifier=verifier_channel,
        typed_option_evidence=typed_map,
    )
    supported_by_calculation_grounding = _grounding_supported_selected_options(
        effective_answer, calculation_grounding_map,
    ) if answer_source == "computation" else set()
    if supported_by_calculation_grounding:
        selected_unresolved_options = [
            option for option in selected_unresolved_options
            if option not in supported_by_calculation_grounding
        ]
        unresolved_options = sorted(set(
            selected_unresolved_options
            + selected_contradicted_options
            + unselected_supported_options
            + special_unresolved_options
        ))

    # Calculation acceptance must account for semantic completeness, not only
    # whether the extracted Python expressions happened to execute. Critical
    # deductible / ratio / cap variables that were extracted but never used are
    # a strong signal that the formula omitted material business constraints.
    # When canonical calculation_grounding exists, it is the source of truth:
    # legacy formula-text scanning cannot understand product-scoped aliases.
    unused_material_variables: list[str] = []
    if calculation_grounding_map is not None:
        raw_unused = calculation_grounding_map.get("unused_material_variables", [])
        if isinstance(raw_unused, (list, tuple, set)):
            unused_material_variables = sorted({str(value) for value in raw_unused})
    else:
        formulas_raw = solver_meta.get("extracted_formulas")
        formula_parts: list[str] = []
        if isinstance(formulas_raw, Mapping):
            formula_parts.extend(str(value) for value in formulas_raw.values())
        elif isinstance(formulas_raw, (list, tuple, set)):
            formula_parts.extend(str(value) for value in formulas_raw)
        single_formula = solver_meta.get("extracted_formula")
        if single_formula:
            formula_parts.append(str(single_formula))
        formula_text = " ".join(formula_parts)
        extracted_values = solver_meta.get("extracted_values")
        material_markers = (
            "免赔", "比例", "上限", "限额", "补偿", "报销",
            "deductible", "ratio", "rate", "cap", "limit",
            "reimburse", "compensation",
        )
        if isinstance(extracted_values, Mapping) and formula_text:
            for raw_name in extracted_values:
                name = str(raw_name)
                lowered = name.lower()
                if any(marker in lowered for marker in material_markers) and name not in formula_text:
                    unused_material_variables.append(name)

    no_unique_option_match = bool(
        solver_meta.get("no_unique_option_match", False)
        or "no_unique_option_match" in calculation_grounding_blocking_reasons
        or "zero_option_match" in calculation_grounding_blocking_reasons
        or "multi_option_match" in calculation_grounding_blocking_reasons
    )
    finish_reason = str(solver_meta.get("finish_reason") or "")
    extract_truncated = bool(solver_meta.get("truncation_risk", False) or solver_meta.get("extract_finish_reason") == "length" or finish_reason == "length")
    match_output_ignored_by_grounding = bool(solver_meta.get("match_output_ignored_by_grounding", False))
    match_truncated = bool(
        not match_output_ignored_by_grounding
        and (solver_meta.get("match_truncation_risk", False) or solver_meta.get("match_finish_reason") == "length")
    )
    extraction_truncation_hard = bool(
        extract_truncated and not (
            answer_source == "computation"
            and _calculation_grounding_clean(calculation_grounding_map)
            and not calculation_grounding_blocking_reasons
        )
    )
    truncation_risk = bool(extract_truncated or match_truncated)
    truncation_hard_block = bool(match_truncated or extraction_truncation_hard)
    llm_error = bool(solver_meta.get("llm_error", False))
    unsafe_answer_source = answer_source in {
        "error", "unsupported_guess", "unsupported_guess_truncated", "dry_run"
    }

    blocking_reasons: list[str] = []
    if typed_fail_closed and not typed_authoritative:
        blocking_reasons.append("typed_option_evidence_untrusted")
    option_evidence_review_required = bool(selected_unresolved_options and retrieved)
    option_evidence_unresolved_hard = bool(
        selected_contradicted_options
        or unselected_supported_options
        or special_unresolved_options
        or (selected_unresolved_options and not retrieved)
    )
    correction_reconcile_required = bool(correction_gate_required)
    if answer_authority == "solver" and cross_doc and not solver_channel.complete:
        blocking_reasons.append("used_doc_lineage_unknown")
    if answer_authority == "verifier" and not verifier_channel.complete:
        blocking_reasons.append("verifier_evidence_lineage_incomplete")
    if answer_authority == "baseline_fallback":
        blocking_reasons.append("baseline_fallback_not_production_pass")
    if cross_doc and not cross_document_retrieval_complete:
        blocking_reasons.append("cross_document_incomplete")
    if option_evidence_unresolved_hard:
        blocking_reasons.append("option_evidence_unresolved")
    if option_evidence_review_required:
        blocking_reasons.append("option_evidence_review_required")
    if correction_reconcile_required:
        blocking_reasons.append("correction_reconcile_required")
    if not calculation_complete:
        blocking_reasons.append("calculation_incomplete")
    if truncation_hard_block:
        blocking_reasons.append("truncation_risk")
    if no_unique_option_match:
        blocking_reasons.append("no_unique_option_match")
    for reason in calculation_grounding_blocking_reasons:
        if reason != "no_unique_option_match":
            blocking_reasons.append(reason)
    if unused_material_variables:
        blocking_reasons.append("unused_material_variables")
    if llm_error:
        blocking_reasons.append("llm_error")
    if unsafe_answer_source:
        blocking_reasons.append("unsafe_answer_source")

    blocking_reasons = sorted(set(blocking_reasons))
    if answer_authority == "baseline_fallback":
        final_state = "BASELINE_FALLBACK_UNTRUSTED"
    elif blocking_reasons:
        final_state = "blocked"
    elif answer_authority == "verifier":
        final_state = "accepted_by_verifier_evidence"
    else:
        final_state = "accepted"

    return {
        "production_integrity_checked": True,
        "required_docs": required,
        "retrieved_docs": retrieved,
        # Legacy solver-only aliases retained for compatibility.  Verifier
        # evidence is never written into these fields.
        "used_docs": used,
        "used_docs_source": used_source,
        "usage_lineage_known": usage_lineage_known,
        "solver_used_doc_ids": list(solver_channel.doc_ids),
        "solver_source_refs": [dict(item) for item in solver_channel.source_refs],
        "solver_lineage_source": solver_channel.source,
        "solver_lineage_complete": solver_channel.complete,
        "solver_lineage_errors": list(solver_channel.errors),
        "verifier_evidence_doc_ids": list(verifier_channel.doc_ids),
        "verifier_source_refs": [dict(item) for item in verifier_channel.source_refs],
        "verifier_lineage_source": verifier_channel.source,
        "verifier_lineage_complete": verifier_channel.complete,
        "verifier_lineage_errors": list(verifier_channel.errors),
        "final_answer_authority": answer_authority,
        "solver_answer": str(solver_result.answer or ""),
        "integrity_final_answer": effective_answer,
        "missing_required_docs": missing_docs,
        "unused_required_docs": unused_docs,
        "cross_document_complete": cross_document_complete,
        "cross_document_retrieval_complete": cross_document_retrieval_complete,
        "cross_document_usage_complete": cross_document_usage_complete,
        "cross_document_usage_warning": cross_document_usage_warning,
        "option_evidence_source": option_evidence_source,
        "typed_option_evidence_trusted": typed_authoritative,
        "typed_option_evidence_fail_closed": typed_fail_closed,
        "typed_option_evidence": typed_map or None,
        "legacy_self_check_overridden": bool(typed_authoritative and isinstance(self_check, Mapping)),
        "legacy_option_gate": legacy_option_gate,
        "legacy_correction_proposal": legacy_correction_proposal,
        "legacy_correction_differs": legacy_correction_differs,
        "unresolved_options": sorted(unresolved_options),
        "selected_unresolved_options": sorted(selected_unresolved_options),
        "selected_contradicted_options": sorted(selected_contradicted_options),
        "unselected_supported_options": sorted(unselected_supported_options),
        "benign_unselected_missing_options": sorted(benign_unselected_missing_options),
        "special_unresolved_options": sorted(special_unresolved_options),
        "correction_proposal": correction_proposal,
        "correction_differs": correction_differs,
        "correction_gate_required": correction_gate_required,
        "correction_reconcile_required": correction_reconcile_required,
        "option_evidence_review_required": option_evidence_review_required,
        "option_evidence_unresolved_hard": option_evidence_unresolved_hard,
        "option_integrity_issues": option_issues,
        "calculation_complete": calculation_complete,
        "calculation_grounding": calculation_grounding if isinstance(calculation_grounding, Mapping) else None,
        "calculation_grounding_blocking_reasons": calculation_grounding_blocking_reasons,
        "unused_material_variables": sorted(unused_material_variables),
        "no_unique_option_match": no_unique_option_match,
        "finish_reason": finish_reason,
        "truncation_risk": truncation_risk,
        "truncation_hard_block": truncation_hard_block,
        "extraction_truncation_hard": extraction_truncation_hard,
        "match_truncated": match_truncated,
        "supported_by_calculation_grounding": sorted(supported_by_calculation_grounding),
        "answer_source": answer_source,
        "unsafe_answer_source": unsafe_answer_source,
        "blocking_reasons": blocking_reasons,
        "final_state": final_state,
        "grounded": accepted_final_state(final_state),
    }



def failed_result_from_blocking(question: Question, exc: Any) -> PipelineResult:
    """Convert a blocking exception into an auditable non-submittable result."""
    meta = dict(getattr(exc, "metadata", {}) or {})
    raw_labels = meta.get("classifier_labels", [])
    labels: list[QuestionLabel] = []
    for value in raw_labels if isinstance(raw_labels, list) else []:
        try:
            labels.append(QuestionLabel(value))
        except ValueError:
            labels.append(QuestionLabel.DEFAULT)
    if not labels:
        labels = [QuestionLabel.DEFAULT]
    reason = str(getattr(exc, "reason", exc))
    attempted_answer = str(getattr(exc, "answer", ""))
    meta.update({
        "domain": meta.get("domain", question.domain),
        "doc_ids": list(meta.get("doc_ids") or [str(value) for value in question.doc_ids]),
        "answer_format": question.answer_format,
        "attempted_answer": attempted_answer,
        "blocking_reason": reason,
        "blocking_reasons": list(meta.get("blocking_reasons") or [reason]),
        "answer_validation": "blocking_invalid",
        "answer_validation_reason": reason,
        "final_state": "failed",
        "grounded": False,
        "ungrounded": True,
        "answer_source": "error",
        "authoritative": True,
        "attempt_id": str(meta.get("attempt_id") or f"{question.qid}:failed"),
    })
    preserved_solver_meta = dict(meta.get("solver_metadata") or {})
    preserved_solver_meta.update({
        "answer_source": preserved_solver_meta.get("answer_source", "error"),
        "ungrounded": True,
        "blocking_reason": reason,
    })
    solver_result = SolverResult(
        qid=question.qid,
        answer=attempted_answer,
        solver=str(meta.get("solver") or "blocked"),
        raw_output=str(meta.get("solver_raw_output") or ""),
        confidence=0.0,
        metadata=preserved_solver_meta,
    )
    verification_result = None
    preserved_verification = meta.get("verification_result")
    if isinstance(preserved_verification, Mapping):
        verification_result = VerificationResult(
            qid=str(preserved_verification.get("qid") or question.qid),
            answer=str(preserved_verification.get("answer") or attempted_answer),
            changed=bool(preserved_verification.get("changed", False)),
            verifier=str(preserved_verification.get("verifier") or "blocked_verification"),
            notes=[str(value) for value in preserved_verification.get("notes", [])],
            metadata=dict(preserved_verification.get("metadata") or {}),
        )
    return PipelineResult(
        qid=question.qid,
        answer="",
        classification=ClassificationResult(labels=labels),
        solver_result=solver_result,
        verification_result=verification_result,
        fallback_used=False,
        error=reason,
        metadata=meta,
    )

def runtime_record_from_result(result: PipelineResult, *, authoritative: bool = True) -> dict[str, Any]:
    """Build the normalized runtime record consumed before artifact writes."""
    meta = dict(result.metadata or {})
    solver_meta = dict(result.solver_result.metadata or {})
    source = str(
        meta.get("answer_source")
        or solver_meta.get("answer_source")
        or ("fallback" if result.fallback_used else "generated")
    )
    finish_reason = str(meta.get("finish_reason") or solver_meta.get("finish_reason") or "stop")
    truncation = bool(meta.get("truncation_risk", False) or solver_meta.get("truncation_risk", False))
    if finish_reason == "length":
        truncation = True
    final_state = str(meta.get("final_state") or "accepted")
    error = result.error
    if error is None:
        error = meta.get("fallback_error") if source == "fallback" else meta.get("error")
    ungrounded = bool(meta.get("ungrounded", False) or meta.get("grounded") is False)
    if source in {"error", "unsupported_guess", "unsupported_guess_truncated", "dry_run"}:
        ungrounded = True
    if final_state in {"blocked", "failed", "degraded"}:
        ungrounded = True
    return {
        "qid": result.qid,
        "answer_source": source,
        "fallback_used": bool(result.fallback_used or meta.get("fallback_used", False)),
        "finish_reason": finish_reason,
        "truncation_risk": truncation,
        "ungrounded": ungrounded,
        "error": error,
        "authoritative": authoritative,
        "completed": error is None and accepted_final_state(final_state),
        "attempt_id": str(meta.get("attempt_id") or f"{result.qid}:current"),
        "replaces_attempt_id": meta.get("replaces_attempt_id"),
    }


def validate_results_before_write(
    results: Sequence[PipelineResult],
    *,
    allow_failed: bool = False,
) -> None:
    """Validate runtime records before writes; optionally retain failed diagnostics."""
    records = [runtime_record_from_result(result) for result in results]
    authoritative_records(records)
    failures: list[str] = []
    for record in records:
        issues = validate_runtime_record(record)
        if issues:
            failures.append(f"{record['qid']}:{','.join(issues)}")
        if not record["completed"] and not allow_failed:
            failures.append(f"{record['qid']}:not_completed")
    if failures:
        raise ProductionIntegrityError("artifact_write_blocked " + "; ".join(failures))


def attach_integrity_metadata(result: PipelineResult, integrity: Mapping[str, Any]) -> PipelineResult:
    meta = dict(result.metadata or {})
    meta.update(dict(integrity))
    return replace(result, metadata=meta)
