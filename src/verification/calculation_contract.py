"""Offline validators for calculation solver smoke contracts."""
from __future__ import annotations

from typing import Any, Mapping

ONE_CALL_REQUIRED_FIELDS = {
    "qid",
    "answer",
    "formula_text",
    "variables",
    "computed_result",
    "option_evaluations",
    "calculation_grounding",
    "used_doc_ids",
    "confidence",
}

OPTION_EVALUATION_REQUIRED_FIELDS = {
    "option",
    "verdict",
    "evidence_refs",
    "calculation_refs",
}

TWO_CALL_STAGE_NAMES = (
    "calculation_formula_extraction",
    "calculation_option_matching",
)


def validate_one_call_output_schema(schema: Mapping[str, Any]) -> list[str]:
    """Return schema errors for the proposed one-call calculation output."""
    errors: list[str] = []
    missing = sorted(ONE_CALL_REQUIRED_FIELDS - set(schema.keys()))
    if missing:
        errors.append("missing_one_call_fields:" + ",".join(missing))
    option_evaluations = schema.get("option_evaluations")
    if not isinstance(option_evaluations, list) or not option_evaluations:
        errors.append("option_evaluations_required_nonempty_list")
    else:
        for idx, item in enumerate(option_evaluations):
            if not isinstance(item, Mapping):
                errors.append(f"option_evaluations[{idx}]_not_object")
                continue
            missing_option = sorted(OPTION_EVALUATION_REQUIRED_FIELDS - set(item.keys()))
            if missing_option:
                errors.append(f"option_evaluations[{idx}]_missing:" + ",".join(missing_option))
    grounding = schema.get("calculation_grounding")
    if not isinstance(grounding, Mapping):
        errors.append("calculation_grounding_required_object")
    else:
        for field in ("formula_extracted", "computation_complete", "answer_source", "ungrounded"):
            if field not in grounding:
                errors.append("calculation_grounding_missing:" + field)
    return errors


def validate_two_call_contract(contract: Mapping[str, Any]) -> list[str]:
    """Return contract errors for a two-call calculation smoke design."""
    errors: list[str] = []
    stages = contract.get("stages")
    if not isinstance(stages, Mapping):
        return ["stages_required_object"]
    for stage in TWO_CALL_STAGE_NAMES:
        payload = stages.get(stage)
        if not isinstance(payload, Mapping):
            errors.append("missing_stage:" + stage)
            continue
        if int(payload.get("completed_call_budget", -1)) != 1:
            errors.append("stage_completed_call_budget_must_be_1:" + stage)
        expected_outputs = payload.get("expected_outputs")
        if not isinstance(expected_outputs, list) or not expected_outputs:
            errors.append("stage_expected_outputs_required:" + stage)
    if int(contract.get("per_qid_completed_call_budget", -1)) != 2:
        errors.append("per_qid_completed_call_budget_must_be_2")
    if int(contract.get("fallback_calls", -1)) != 0:
        errors.append("fallback_calls_must_be_0")
    if int(contract.get("retry_calls", -1)) != 0:
        errors.append("retry_calls_must_be_0")
    if not bool(contract.get("pre_call_blocked_circuit_breaker", False)):
        errors.append("pre_call_blocked_circuit_breaker_required")
    if not bool(contract.get("stage_level_ledger_required", False)):
        errors.append("stage_level_ledger_required")
    return errors


def validate_next_smoke_manifest_contract(manifest: Mapping[str, Any]) -> list[str]:
    """Validate contract fields required before a future controlled smoke."""
    errors: list[str] = []
    contract = str(manifest.get("calculation_contract") or "")
    if contract not in {"one-call", "two-call"}:
        errors.append("calculation_contract_must_be_one-call_or_two-call")
        return errors
    failure_policy = manifest.get("failure_policy")
    if not isinstance(failure_policy, Mapping):
        errors.append("failure_policy_required")
    else:
        if int(failure_policy.get("fallback_calls", -1)) != 0:
            errors.append("fallback_calls_must_be_0")
        if int(failure_policy.get("retry_calls", -1)) != 0:
            errors.append("retry_calls_must_be_0")
    if contract == "two-call":
        budgets = manifest.get("stage_call_budgets")
        if not isinstance(budgets, Mapping):
            errors.append("stage_call_budgets_required_for_two-call")
        else:
            for stage in TWO_CALL_STAGE_NAMES:
                if int(budgets.get(stage, -1)) != 1:
                    errors.append("stage_budget_must_be_1:" + stage)
        if int(manifest.get("per_qid_completed_call_budget", -1)) != 2:
            errors.append("per_qid_completed_call_budget_must_be_2_for_two-call")
    if contract == "one-call" and int(manifest.get("per_qid_completed_call_budget", -1)) != 1:
        errors.append("per_qid_completed_call_budget_must_be_1_for_one-call")
    return errors
