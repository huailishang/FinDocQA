from __future__ import annotations

"""Fail-closed deterministic recovery for duplicated multi-slot visible answers.

This module never derives new facts.  It can only split one already-visible answer
string into exact substrings when every structural, reasoning, source-audit, and
authoritative slot-contract check passes.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


SlotValidator = Callable[[Sequence[str]], tuple[bool, Sequence[Mapping[str, Any]], Sequence[str]]]


@dataclass(frozen=True)
class MultiSlotRecoveryResult:
    status: str
    recovered_answers: tuple[str, ...] = ()
    delimiter: str = ""
    reason: str = ""
    checks: Mapping[str, bool] = field(default_factory=dict)
    slot_validations: tuple[Mapping[str, Any], ...] = ()
    normalized_answers: tuple[str, ...] = ()

    @property
    def recovered(self) -> bool:
        return self.status == "RECOVERED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "recovered": self.recovered,
            "recovered_answers": list(self.recovered_answers),
            "delimiter": self.delimiter,
            "reason": self.reason,
            "checks": dict(self.checks),
            "slot_validations": [dict(row) for row in self.slot_validations],
            "normalized_answers": list(self.normalized_answers),
        }


def _source_audit_pass(source_audit: Mapping[str, Any]) -> bool:
    usage = dict(source_audit.get("usage") or {})
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or 0)
    semantic = source_audit.get("semantic_binding_pass")
    if semantic is None:
        semantic = (source_audit.get("semantic_binding_audit") or {}).get("valid")
    return all(
        (
            str(source_audit.get("provider_status") or "").upper() == "COMPLETED",
            bool(source_audit.get("result_reusable", True)),
            bool(source_audit.get("reasoning_contract_pass")),
            bool(source_audit.get("reasoning_self_contained_pass")),
            bool(source_audit.get("lineage_pass") or source_audit.get("binding_auditable")),
            bool(semantic),
            total > 0,
            prompt + completion == total,
        )
    )


def recover_duplicated_multislot_answers(
    *,
    visible_answers: Sequence[Any],
    expected_slots: int,
    slot_contracts: Sequence[Mapping[str, Any]],
    allowed_delimiters: Sequence[str],
    reasoning_summary: str,
    source_audit: Mapping[str, Any],
    slot_validator: SlotValidator,
) -> MultiSlotRecoveryResult:
    """Recover duplicated multi-slot output only when no inference is required.

    `slot_validator` must be the caller's authoritative answer validator for the
    question/contracts.  Recovery is deliberately not qid-aware.
    """

    answers = tuple(str(value or "").strip() for value in visible_answers)
    contracts = tuple(dict(item) for item in slot_contracts)
    reasoning = str(reasoning_summary or "").strip()
    delimiters = tuple(str(item) for item in allowed_delimiters if str(item))

    base_checks = {
        "expected_slots_at_least_2": int(expected_slots) >= 2,
        "visible_answer_count_matches": len(answers) == int(expected_slots),
        "slot_contract_count_matches": len(contracts) == int(expected_slots),
        "all_visible_answers_nonempty": bool(answers) and all(answers),
        "all_visible_answers_identical": bool(answers) and len(set(answers)) == 1,
        "reasoning_nonempty": bool(reasoning),
        "source_audit_reusable": _source_audit_pass(source_audit),
    }

    if int(expected_slots) < 2:
        return MultiSlotRecoveryResult("FAIL", reason="single_slot_not_recoverable", checks=base_checks)
    if len(answers) != int(expected_slots):
        return MultiSlotRecoveryResult("FAIL", reason="visible_answer_count_mismatch", checks=base_checks)
    if len(contracts) != int(expected_slots):
        return MultiSlotRecoveryResult("FAIL", reason="slot_contract_count_mismatch", checks=base_checks)
    if not answers or any(not value for value in answers):
        return MultiSlotRecoveryResult("FAIL", reason="empty_visible_answer", checks=base_checks)
    if len(set(answers)) != 1:
        # A normal legal multi-slot output must never be rewritten by recovery.
        valid, validations, normalized = slot_validator(answers)
        normal_checks = dict(base_checks)
        normal_checks["ordinary_multislot_already_valid"] = bool(valid)
        return MultiSlotRecoveryResult(
            "NO_RECOVERY" if valid else "FAIL",
            reason="ordinary_multislot_already_valid" if valid else "visible_answers_not_identical",
            checks=normal_checks,
            slot_validations=tuple(dict(row) for row in validations),
            normalized_answers=tuple(str(value) for value in normalized),
        )
    if not reasoning:
        return MultiSlotRecoveryResult("FAIL", reason="missing_reasoning", checks=base_checks)
    if not _source_audit_pass(source_audit):
        return MultiSlotRecoveryResult("FAIL", reason="source_audit_not_reusable", checks=base_checks)

    combined = answers[0]
    candidates: list[tuple[str, tuple[str, ...]]] = []
    for delimiter in delimiters:
        parts = tuple(part.strip() for part in combined.split(delimiter))
        if len(parts) == int(expected_slots) and all(parts):
            candidates.append((delimiter, parts))
    if not candidates:
        return MultiSlotRecoveryResult("FAIL", reason="no_allowed_delimiter_yields_exact_slots", checks=base_checks)

    failure_reason = "authoritative_slot_validation_failed"
    for delimiter, parts in candidates:
        # Exact-substring proof: only trimming around delimiter is allowed.
        substring_pass = all(part in combined for part in parts)
        rejoin_pass = delimiter.join(parts) == combined
        reasoning_pass = all(part in reasoning for part in parts)
        valid, validations, normalized = slot_validator(parts)
        per_slot_valid = (
            bool(valid)
            and len(validations) == int(expected_slots)
            and all(bool(dict(row).get("valid")) for row in validations)
            and len(normalized) == int(expected_slots)
        )
        no_value_change = tuple(str(value).strip() for value in normalized) == parts
        checks = {
            **base_checks,
            "allowed_delimiter_found": True,
            "split_exact_slot_count": len(parts) == int(expected_slots),
            "split_parts_nonempty": all(parts),
            "all_recovered_values_direct_substrings": substring_pass,
            "rejoin_equals_original_visible_answer": rejoin_pass,
            "authoritative_slot_validator_pass": per_slot_valid,
            "validator_does_not_change_values": no_value_change,
            "reasoning_covers_every_recovered_slot": reasoning_pass,
        }
        if all(checks.values()):
            return MultiSlotRecoveryResult(
                "RECOVERED",
                recovered_answers=parts,
                delimiter=delimiter,
                reason="duplicated_combined_visible_answer_split_without_inference",
                checks=checks,
                slot_validations=tuple(dict(row) for row in validations),
                normalized_answers=tuple(str(value) for value in normalized),
            )
        if not reasoning_pass:
            failure_reason = "reasoning_does_not_cover_all_recovered_slots"
        elif not per_slot_valid:
            failure_reason = "authoritative_slot_validation_failed"
        elif not no_value_change:
            failure_reason = "slot_validation_requires_value_change"
        elif not substring_pass or not rejoin_pass:
            failure_reason = "recovery_would_modify_visible_value"

    return MultiSlotRecoveryResult("FAIL", reason=failure_reason, checks=checks)
