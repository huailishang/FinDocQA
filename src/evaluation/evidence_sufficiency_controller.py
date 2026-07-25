"""Evidence sufficiency and contract-aware answer controller for AG-R12.

The controller is deliberately deterministic and bounded. It does not retrieve
by itself; it classifies actionable evidence gaps, maps each gap to one local
corrective action, and decides whether the evidence state is sufficient to form
an answer under the question's answer contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from answer_contract import validate_answer_against_contract
from contracts import Question

MAX_ZERO_API_CORRECTIVE_ROUNDS = 2
UNIQUE_ANSWER_FORMATS = {"mcq", "single", "tf", "boolean", "judge"}
TF_FORMATS = {"tf", "boolean", "judge"}


class GapType(str, Enum):
    MISSING_REQUIRED_DOC = "MISSING_REQUIRED_DOC"
    UNRESOLVED_OPTION = "UNRESOLVED_OPTION"
    MISSING_SEMANTIC_ATOM = "MISSING_SEMANTIC_ATOM"
    MISSING_FORMULA_OPERAND = "MISSING_FORMULA_OPERAND"
    CONTRACT_CONFLICT = "CONTRACT_CONFLICT"
    SINGLE_CHOICE_MULTIPLE_SUPPORTED = "SINGLE_CHOICE_MULTIPLE_SUPPORTED"
    TF_NOT_UNIQUE = "TF_NOT_UNIQUE"
    MULTI_OPTION_NOT_CLOSED = "MULTI_OPTION_NOT_CLOSED"


ACTION_BY_GAP = {
    GapType.MISSING_REQUIRED_DOC: "DOC_SPECIFIC_RETRIEVAL",
    GapType.UNRESOLVED_OPTION: "OPTION_SPECIFIC_CORRECTIVE_RETRIEVAL",
    GapType.MISSING_SEMANTIC_ATOM: "QUERY_REWRITE_PARENT_TABLE_SECTION_EXPANSION",
    GapType.MISSING_FORMULA_OPERAND: "FORMULA_OPERAND_SPECIFIC_RETRIEVAL",
    GapType.CONTRACT_CONFLICT: "CONTRADICTION_ORIENTED_VERIFICATION",
    GapType.SINGLE_CHOICE_MULTIPLE_SUPPORTED: "CONTRADICTION_ORIENTED_VERIFICATION",
    GapType.TF_NOT_UNIQUE: "CONTRADICTION_ORIENTED_VERIFICATION",
    GapType.MULTI_OPTION_NOT_CLOSED: "OPTION_SPECIFIC_CORRECTIVE_RETRIEVAL",
}


@dataclass(frozen=True)
class SufficiencyGap:
    gap_type: str
    option_labels: tuple[str, ...]
    doc_ids: tuple[str, ...]
    details: Mapping[str, Any]
    action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _status(verdict: Mapping[str, Any]) -> str:
    return str(verdict.get("status") or "unresolved").lower()


def _missing_formula_operands(verdict: Mapping[str, Any]) -> tuple[str, ...]:
    missing: list[str] = []
    for value in verdict.get("missing_atoms") or []:
        token = str(value)
        lowered = token.lower()
        if "operand" in lowered or "formula" in lowered:
            missing.append(token)
    derived = dict(verdict.get("derived_option_evidence") or {})
    diagnostics = dict(derived.get("diagnostics") or {})
    variables = dict(derived.get("variables") or {})
    for container in (derived, diagnostics, variables):
        for key, value in container.items():
            lowered = str(key).lower()
            if ("operand" in lowered or "formula" in lowered) and value in (None, "", False, [], {}):
                missing.append(str(key))
        for key in ("missing_operand_ids", "missing_operands", "missing_formula_operands"):
            raw = container.get(key)
            if isinstance(raw, (list, tuple, set)):
                missing.extend(str(item) for item in raw if str(item))
    return tuple(dict.fromkeys(missing))


def assess_evidence_sufficiency(
    *,
    question: Question,
    verdicts: Mapping[str, Mapping[str, Any]],
    required_doc_coverage: Mapping[str, Any],
    semantic_completeness: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify all actionable gaps before final-answer synthesis."""
    gaps: list[SufficiencyGap] = []
    labels = tuple(str(label) for label in question.options)
    statuses = {label: _status(dict(verdicts.get(label) or {})) for label in labels}
    supported = tuple(label for label in labels if statuses[label] == "supported")
    unresolved = tuple(label for label in labels if statuses[label] not in {"supported", "contradicted"})
    fmt = str(question.answer_format or "multi").lower()

    missing_docs = tuple(str(value) for value in required_doc_coverage.get("missing_required_doc_ids") or [])
    if missing_docs:
        gaps.append(SufficiencyGap(
            GapType.MISSING_REQUIRED_DOC.value,
            labels,
            missing_docs,
            {"missing_required_doc_ids": list(missing_docs)},
            ACTION_BY_GAP[GapType.MISSING_REQUIRED_DOC],
        ))

    for label in unresolved:
        gaps.append(SufficiencyGap(
            GapType.UNRESOLVED_OPTION.value,
            (label,),
            tuple(),
            {"status": statuses[label]},
            ACTION_BY_GAP[GapType.UNRESOLVED_OPTION],
        ))

    for label in labels:
        row = dict(semantic_completeness.get(label) or {})
        if statuses[label] in {"supported", "contradicted"} and row.get("full_semantic_atoms_bound") is not True:
            gaps.append(SufficiencyGap(
                GapType.MISSING_SEMANTIC_ATOM.value,
                (label,),
                tuple(),
                {"semantic": row},
                ACTION_BY_GAP[GapType.MISSING_SEMANTIC_ATOM],
            ))
        formula_missing = _missing_formula_operands(dict(verdicts.get(label) or {}))
        if formula_missing:
            gaps.append(SufficiencyGap(
                GapType.MISSING_FORMULA_OPERAND.value,
                (label,),
                tuple(),
                {"missing_formula_operands": list(formula_missing)},
                ACTION_BY_GAP[GapType.MISSING_FORMULA_OPERAND],
            ))

    if fmt in UNIQUE_ANSWER_FORMATS:
        if len(supported) > 1:
            gap_type = GapType.TF_NOT_UNIQUE if fmt in TF_FORMATS else GapType.SINGLE_CHOICE_MULTIPLE_SUPPORTED
            gaps.append(SufficiencyGap(
                gap_type.value,
                supported,
                tuple(),
                {"supported_labels": list(supported), "supported_count": len(supported)},
                ACTION_BY_GAP[gap_type],
            ))
        elif len(supported) != 1 and not unresolved:
            gap_type = GapType.TF_NOT_UNIQUE if fmt in TF_FORMATS else GapType.CONTRACT_CONFLICT
            gaps.append(SufficiencyGap(
                gap_type.value,
                labels,
                tuple(),
                {"supported_labels": list(supported), "supported_count": len(supported)},
                ACTION_BY_GAP[gap_type],
            ))
    else:
        if unresolved:
            gaps.append(SufficiencyGap(
                GapType.MULTI_OPTION_NOT_CLOSED.value,
                unresolved,
                tuple(),
                {"unresolved_labels": list(unresolved)},
                ACTION_BY_GAP[GapType.MULTI_OPTION_NOT_CLOSED],
            ))
        if not supported and not unresolved:
            gaps.append(SufficiencyGap(
                GapType.CONTRACT_CONFLICT.value,
                labels,
                tuple(),
                {"supported_labels": [], "reason": "multi_answer_empty"},
                ACTION_BY_GAP[GapType.CONTRACT_CONFLICT],
            ))

    # Deduplicate semantically equivalent gaps while preserving deterministic order.
    deduped: list[SufficiencyGap] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for gap in gaps:
        key = (gap.gap_type, gap.option_labels, gap.doc_ids)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(gap)

    all_option_closure = bool(labels) and not unresolved and all(
        (dict(verdicts.get(label) or {}).get("trusted_for_option_gate") is True)
        for label in labels
    )
    return {
        "schema_version": "evidence_sufficiency_controller_v1",
        "qid": question.qid,
        "answer_format": fmt,
        "supported_labels": list(supported),
        "unresolved_labels": list(unresolved),
        "all_option_closure": all_option_closure,
        "question_required_docs_covered": required_doc_coverage.get("question_required_docs_covered") is True,
        "gap_count": len(deduped),
        "gaps": [gap.to_dict() for gap in deduped],
        "answer_ready_by_evidence": len(deduped) == 0 and all_option_closure,
    }


def selected_actions(sufficiency: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for gap in sufficiency.get("gaps") or []:
        action = str(gap.get("action") or "")
        labels = tuple(str(value) for value in gap.get("option_labels") or [])
        docs = tuple(str(value) for value in gap.get("doc_ids") or [])
        key = (action, labels, docs)
        if not action or key in seen:
            continue
        seen.add(key)
        rows.append({
            "action": action,
            "gap_type": str(gap.get("gap_type") or ""),
            "option_labels": list(labels),
            "doc_ids": list(docs),
            "details": dict(gap.get("details") or {}),
        })
    return rows


def contract_aware_answer(
    *,
    question: Question,
    verdicts: Mapping[str, Mapping[str, Any]],
    sufficiency: Mapping[str, Any],
) -> dict[str, Any]:
    """Form an answer only after the evidence-sufficiency gate is closed."""
    labels = tuple(str(label) for label in question.options)
    supported = [label for label in labels if _status(dict(verdicts.get(label) or {})) == "supported"]
    fmt = str(question.answer_format or "multi").lower()
    candidate = "".join(supported)
    evidence_ready = sufficiency.get("answer_ready_by_evidence") is True

    if fmt in UNIQUE_ANSWER_FORMATS:
        exact_one = len(supported) == 1
    else:
        exact_one = True
    if not evidence_ready or not exact_one:
        return {
            "answer_ready": False,
            "answer": "",
            "candidate_supported_answer": candidate,
            "answer_contract_valid": False,
            "answer_contract_result": {
                "valid": False,
                "reason": "evidence_sufficiency_gate_not_closed",
            },
            "answer_formed_after_sufficiency_gate": False,
            "stop_reason": "BLOCKED_EVIDENCE_SUFFICIENCY",
        }

    validation = validate_answer_against_contract(candidate, question.answer_contract).to_dict()
    if validation.get("valid") is not True:
        return {
            "answer_ready": False,
            "answer": "",
            "candidate_supported_answer": candidate,
            "answer_contract_valid": False,
            "answer_contract_result": validation,
            "answer_formed_after_sufficiency_gate": False,
            "stop_reason": "BLOCKED_CONTRACT_CONFLICT",
        }
    return {
        "answer_ready": True,
        "answer": candidate,
        "candidate_supported_answer": candidate,
        "answer_contract_valid": True,
        "answer_contract_result": validation,
        "answer_formed_after_sufficiency_gate": True,
        "stop_reason": "ANSWER_READY",
    }


def stop_reason_after_round(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    new_source_count: int,
    round_index: int,
    max_rounds: int = MAX_ZERO_API_CORRECTIVE_ROUNDS,
) -> str:
    if after.get("answer_ready_by_evidence") is True:
        return "ANSWER_READY"
    before_count = int(before.get("gap_count") or 0)
    after_count = int(after.get("gap_count") or 0)
    if new_source_count <= 0 or after_count >= before_count:
        return "NO_PROGRESS_BLOCKED"
    if round_index >= max_rounds:
        return "MAX_ROUNDS_BLOCKED"
    return "CONTINUE"


__all__ = [
    "MAX_ZERO_API_CORRECTIVE_ROUNDS",
    "UNIQUE_ANSWER_FORMATS",
    "GapType",
    "ACTION_BY_GAP",
    "assess_evidence_sufficiency",
    "selected_actions",
    "contract_aware_answer",
    "stop_reason_after_round",
]
