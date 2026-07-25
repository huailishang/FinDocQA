"""Fail-closed answer contract checks shared by replacement gates."""
from __future__ import annotations

from typing import Any, Mapping

from answer_contract import (
    contract_from_answer_format,
    contract_from_mapping,
    contract_to_dict,
    normalize_answer_candidate,
    validate_answer_against_contract,
)
from contracts import QuestionAnswerContract


def assess_replacement_answer_contract(
    *,
    baseline_answer: str,
    proposed_answer: str,
    correction_answer: str = "",
    answer_format: str | None = None,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract = contract_from_mapping(answer_contract)
    if contract is None and answer_format:
        contract = contract_from_answer_format(answer_format)
    if contract is None or contract.answer_format == "unknown":
        return {
            "contract": None,
            "valid": False,
            "block_reasons": ["unknown_answer_contract"],
            "validations": {},
        }

    validations: dict[str, dict[str, Any]] = {}
    block_reasons: list[str] = []
    for name, value, reason in (
        ("baseline", baseline_answer, "baseline_answer_contract_violation"),
        ("proposed", proposed_answer, "proposed_answer_contract_violation"),
        ("correction", correction_answer, "correction_answer_contract_violation"),
    ):
        if name == "correction" and not correction_answer:
            continue
        result = validate_answer_against_contract(value, contract)
        validations[name] = result.to_dict()
        if not result.valid:
            block_reasons.append(reason)
    return {
        "contract": contract_to_dict(contract),
        "valid": not block_reasons,
        "block_reasons": block_reasons,
        "validations": validations,
    }


def resolve_replacement_answer_contract(
    *,
    baseline_answer: str,
    proposed_answer: str,
    correction_answer: str = "",
    answer_format: str | None = None,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a replacement answer and explicitly fall back to the baseline.

    Contract failures never produce an empty answer and never choose a letter by
    position.  A natural-language answer is normalized only when one explicit,
    contract-valid answer clause is present.
    """
    contract = contract_from_mapping(answer_contract)
    if contract is None and answer_format:
        contract = contract_from_answer_format(answer_format)
    if contract is None or contract.answer_format == "unknown":
        return {
            "effective_answer": baseline_answer,
            "replacement_allowed": False,
            "fallback_to_baseline": True,
            "reason": "unknown_answer_contract",
            "contract": None,
            "validations": {},
        }

    baseline = validate_answer_against_contract(baseline_answer, contract)
    proposed = normalize_answer_candidate(proposed_answer, contract)
    correction = (
        normalize_answer_candidate(correction_answer, contract)
        if correction_answer
        else None
    )
    validations = {
        "baseline": baseline.to_dict(),
        "proposed": proposed.to_dict(),
    }
    if correction is not None:
        validations["correction"] = correction.to_dict()

    if not baseline.valid:
        return {
            "effective_answer": baseline.answer,
            "replacement_allowed": False,
            "fallback_to_baseline": False,
            "reason": "baseline_answer_contract_violation",
            "contract": contract_to_dict(contract),
            "validations": validations,
        }
    if not proposed.valid:
        return {
            "effective_answer": baseline.answer,
            "replacement_allowed": False,
            "fallback_to_baseline": True,
            "reason": "proposed_answer_contract_violation",
            "contract": contract_to_dict(contract),
            "validations": validations,
        }
    if correction is not None and not correction.valid:
        return {
            "effective_answer": baseline.answer,
            "replacement_allowed": False,
            "fallback_to_baseline": True,
            "reason": "correction_answer_contract_violation",
            "contract": contract_to_dict(contract),
            "validations": validations,
        }
    if correction is not None and correction.answer != proposed.answer:
        return {
            "effective_answer": baseline.answer,
            "replacement_allowed": False,
            "fallback_to_baseline": True,
            "reason": "correction_answer_mismatch",
            "contract": contract_to_dict(contract),
            "validations": validations,
        }
    return {
        "effective_answer": proposed.answer,
        "replacement_allowed": proposed.answer != baseline.answer,
        "fallback_to_baseline": False,
        "reason": "contract_valid_replacement" if proposed.answer != baseline.answer else "baseline_preserve",
        "contract": contract_to_dict(contract),
        "validations": validations,
    }
