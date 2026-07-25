"""Minimal shared kernel for Package AG domain vertical adapters.

This module deliberately contains no domain semantics and no question IDs.
It only models source spans, decisive-field provenance, answer-contract
outcomes and candidate dossiers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from answer_contract import validate_answer_against_contract
from contracts import QuestionAnswerContract

OPTION_LABELS = tuple("ABCD")
CLOSED_STATES = {"supported", "contradicted"}


def canonical_answer(value: Any) -> str:
    return "".join(label for label in OPTION_LABELS if label in set(str(value or "").upper()))


@dataclass(frozen=True)
class SourceSpanRef:
    source_doc_id: str
    source_path: str
    source_span: str
    source_span_sha256: str
    source_anchor: str = ""
    span_reproduced_locally: bool = False

    @classmethod
    def build(
        cls,
        *,
        source_doc_id: str,
        source_path: str,
        source_span: str,
        source_anchor: str = "",
        verified_local_extraction: bool = False,
    ) -> "SourceSpanRef":
        path = Path(source_path)
        span = str(source_span or "")
        reproduced = bool(verified_local_extraction and path.is_file() and span)
        if not reproduced and path.is_file() and span:
            try:
                reproduced = span in path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                reproduced = False
        return cls(
            source_doc_id=str(source_doc_id or ""),
            source_path=str(path),
            source_span=span,
            source_span_sha256=hashlib.sha256(span.encode("utf-8")).hexdigest() if span else "",
            source_anchor=str(source_anchor or ""),
            span_reproduced_locally=reproduced,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisiveFieldProvenance:
    field_name: str
    field_value: Any
    source_doc_id: str
    source_path: str
    source_text: str
    start_char: int
    end_char: int
    source_text_sha256: str
    extraction_rule: str
    valid: bool

    @classmethod
    def locate(
        cls,
        *,
        field_name: str,
        field_value: Any,
        source: SourceSpanRef,
        source_text: str,
        extraction_rule: str,
    ) -> "DecisiveFieldProvenance":
        needle = str(source_text or "")
        start = source.source_span.find(needle) if needle else -1
        end = start + len(needle) if start >= 0 else -1
        valid = bool(
            needle
            and start >= 0
            and source.source_span[start:end] == needle
            and source.span_reproduced_locally
            and source.source_doc_id
        )
        return cls(
            field_name=str(field_name),
            field_value=field_value,
            source_doc_id=source.source_doc_id,
            source_path=source.source_path,
            source_text=needle,
            start_char=start,
            end_char=end,
            source_text_sha256=hashlib.sha256(needle.encode("utf-8")).hexdigest() if needle else "",
            extraction_rule=str(extraction_rule),
            valid=valid,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnswerContractDecision:
    answer_format: str
    baseline_answer: str
    production_answer: str
    added_options: str
    removed_options: str
    lane: str
    contract_valid: bool
    decision: str
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateDossier:
    qid: str
    domain: str
    lane: str
    baseline_answer: str
    production_answer: str
    changed_options: str
    status: str
    evidence: tuple[Mapping[str, Any], ...]
    blockers: tuple[str, ...]
    answer_contract: Mapping[str, Any]
    production_capability: str
    evaluator_oracle_status: str = "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_answer_contract(
    *,
    baseline_answer: str,
    production_answer: str,
    contract: QuestionAnswerContract,
    option_statuses: Mapping[str, str],
    all_options_closed: bool,
    domain_lane_prefix: str,
    extra_blockers: Sequence[str] = (),
) -> AnswerContractDecision:
    baseline = canonical_answer(baseline_answer)
    production = canonical_answer(production_answer)
    added = "".join(label for label in OPTION_LABELS if label in production and label not in baseline)
    removed = "".join(label for label in OPTION_LABELS if label in baseline and label not in production)
    validation = validate_answer_against_contract(production, contract) if production else None
    blockers = [str(value) for value in extra_blockers if value]
    lane = "NONE"
    decision = "FAIL_CLOSED"

    if not production:
        blockers.append("empty_production_answer")
    elif validation is None or not validation.valid:
        blockers.append("answer_contract_invalid")
    if not all_options_closed:
        blockers.append("option_slots_not_closed")
    if production == baseline:
        blockers.append("no_baseline_delta")

    if not blockers:
        if contract.answer_format == "multi" and added and not removed:
            lane = f"{domain_lane_prefix}-M"
            decision = "MULTI_DIRECT_ADDITION"
        elif contract.answer_format in {"mcq", "tf"} and len(production) == 1 and len(baseline) == 1:
            old_status = option_statuses.get(baseline, "unresolved")
            new_status = option_statuses.get(production, "unresolved")
            if old_status == "contradicted" and new_status == "supported":
                lane = f"{domain_lane_prefix}-S"
                decision = "ANSWER_CONTRACT_PAIRED_REPLACEMENT"
            else:
                blockers.append("paired_replacement_requires_old_wrong_new_right")
        else:
            blockers.append("unsupported_delta_shape")

    if blockers:
        decision = "FAIL_CLOSED"
        lane = "NONE"
    return AnswerContractDecision(
        answer_format=contract.answer_format,
        baseline_answer=baseline,
        production_answer=production,
        added_options=added,
        removed_options=removed,
        lane=lane,
        contract_valid=bool(validation and validation.valid),
        decision=decision,
        blocking_reasons=tuple(dict.fromkeys(blockers)),
    )


def source_audit(sources: Sequence[SourceSpanRef]) -> dict[str, Any]:
    return {
        "source_count": len(sources),
        "all_paths_exist": all(Path(row.source_path).is_file() for row in sources),
        "all_spans_nonempty": all(bool(row.source_span) for row in sources),
        "all_spans_reproduced_locally": all(row.span_reproduced_locally for row in sources),
        "all_hashes_present": all(bool(row.source_span_sha256) for row in sources),
    }


def provenance_audit(rows: Sequence[DecisiveFieldProvenance]) -> dict[str, Any]:
    return {
        "field_count": len(rows),
        "valid_count": sum(1 for row in rows if row.valid),
        "invalid_count": sum(1 for row in rows if not row.valid),
        "all_offsets_valid": all(row.valid for row in rows),
    }
