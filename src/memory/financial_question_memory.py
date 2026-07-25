"""Budgeted, deterministic memory compaction for financial questions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from contracts import Question

SCHEMA_VERSION = "financial_question_memory_v2"
DEFAULT_MEMORY_TOKEN_BUDGET = 1600
DEFAULT_COMPRESSION_TRIGGER_RATIO = 0.70
DEFAULT_PROTECTED_RESERVE_TOKENS = 900


@dataclass(frozen=True)
class FinancialQuestionMemory:
    schema_version: str
    qid: str
    protected_memory: Mapping[str, Any]
    memory_layers: Mapping[str, Any]
    compressed_reading_trace: tuple[Mapping[str, Any], ...]
    next_evidence_requests: tuple[Mapping[str, Any], ...]
    memory_token_estimate: int
    memory_before_tokens: int
    memory_after_tokens: int
    memory_token_budget: int
    compression_trigger_ratio: float
    protected_reserve_tokens: int
    protected_allocation_tokens: int
    l3_available_tokens: int
    compression_triggered: bool
    reading_trace_token_reduction: float
    trace_truncated: bool
    protected_over_budget: bool
    within_total_budget: bool
    compression_stop_reason: str
    budget_exhausted: bool
    protected_hashes: Mapping[str, str]
    unsafe_option_labels: tuple[str, ...]
    no_request_reasons: Mapping[str, str]
    reading_trace_reason: str
    final_answer: str
    final_sufficiency: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _estimate_tokens(value: Any) -> int:
    return max(1, (len(_canonical_json(value)) + 3) // 4)


def _compact_trace_row(row: Mapping[str, Any], *, snip: bool = False) -> dict[str, Any]:
    hit = row.get("hit") if isinstance(row.get("hit"), Mapping) else row
    metadata = hit.get("metadata") if isinstance(hit, Mapping) else {}
    candidate = metadata.get("candidate_fact") if isinstance(metadata, Mapping) else {}
    local_window = str((hit or {}).get("local_window") or "")
    if snip and len(local_window) > 240:
        local_window = local_window[:120] + " … " + local_window[-100:]
    return {
        "doc_id": (hit or {}).get("doc_id"),
        "source": (hit or {}).get("source"),
        "round": (hit or {}).get("round"),
        "matched_terms": list((hit or {}).get("matched_terms") or []),
        "grade": row.get("grade") or (metadata or {}).get("grade"),
        "reasons": list(row.get("reasons") or []),
        "local_window": local_window,
        "candidate_identity": {
            "entity": (candidate or {}).get("entity"),
            "metric": (candidate or {}).get("metric"),
            "period": (candidate or {}).get("period"),
            "unit": (candidate or {}).get("unit"),
        },
    }


def _deduplicate(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        compact = _compact_trace_row(row)
        key = _canonical_json(compact)
        if key in seen:
            continue
        seen.add(key)
        output.append(compact)
    return output


def _merge_adjacent_same_source(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if merged and item.get("source") == merged[-1].get("source") and item.get("grade") == merged[-1].get("grade"):
            previous = merged[-1]
            previous["merged_hit_count"] = int(previous.get("merged_hit_count") or 1) + 1
            previous["matched_terms"] = list(dict.fromkeys(
                [*previous.get("matched_terms", []), *item.get("matched_terms", [])]
            ))
            continue
        item["merged_hit_count"] = int(item.get("merged_hit_count") or 1)
        merged.append(item)
    return merged


def _compress_trace(
    rows: Sequence[Mapping[str, Any]],
    *,
    allowed_tokens: int,
) -> tuple[list[dict[str, Any]], bool]:
    compact = _deduplicate(rows)
    compact = [row for row in compact if str(row.get("grade") or "").lower() != "incorrect"]
    compact = _merge_adjacent_same_source(compact)
    compact = [_compact_trace_row(row, snip=True) for row in compact]
    for row in compact:
        if str(row.get("grade") or "").lower() == "ambiguous":
            row["local_window"] = ""
            row["ambiguous_index_only"] = True
    exhausted = False
    while compact and _estimate_tokens(compact) > max(allowed_tokens, 0):
        compact.pop()
        exhausted = True
    return compact, exhausted


def build_financial_question_memory(
    question: Question,
    *,
    claim_specs: Mapping[str, Mapping[str, Any]],
    evidence_by_option: Mapping[str, Mapping[str, Any]],
    sufficiency_by_option: Mapping[str, Mapping[str, Any]],
    targeted_audits: Mapping[str, Mapping[str, Any]],
    reading_trace: Sequence[Mapping[str, Any]] = (),
    answer_contract: Mapping[str, Any] | None = None,
    final_answer: str = "",
    final_sufficiency: Mapping[str, Any] | None = None,
    memory_token_budget: int = DEFAULT_MEMORY_TOKEN_BUDGET,
    compression_trigger_ratio: float = DEFAULT_COMPRESSION_TRIGGER_RATIO,
    protected_fact_reserve: int = DEFAULT_PROTECTED_RESERVE_TOKENS,
) -> FinancialQuestionMemory:
    option_memory: dict[str, Any] = {}
    next_requests: list[Mapping[str, Any]] = []
    no_request_reasons: dict[str, str] = {}
    unsafe_labels: list[str] = []
    active_gaps: dict[str, Any] = {}
    accepted_facts_for_hash: dict[str, Any] = {}
    formulas_for_hash: dict[str, Any] = {}
    sources_for_hash: dict[str, Any] = {}

    for label, option_text in sorted(question.options.items()):
        label = str(label)
        spec = dict(claim_specs.get(label) or {})
        evidence = dict(evidence_by_option.get(label) or {})
        sufficiency = dict(sufficiency_by_option.get(label) or {})
        completion = dict(targeted_audits.get(label) or {})
        facts = [
            {
                "doc_id": fact.get("doc_id"),
                "metric": fact.get("metric"),
                "period": fact.get("period_scope"),
                "value": fact.get("value"),
                "unit": fact.get("unit"),
                "fact_state": fact.get("fact_state"),
                "canonical_source": fact.get("canonical_source"),
            }
            for fact in evidence.get("source_facts") or []
            if isinstance(fact, Mapping)
        ]
        missing = list(sufficiency.get("missing_atoms") or [])
        conflicts = list(sufficiency.get("conflicting_atoms") or [])
        safe = sufficiency.get("safe_to_override") is True
        if not safe:
            unsafe_labels.append(label)
        requests = [
            request for request in completion.get("requests") or []
            if isinstance(request, Mapping)
        ]
        next_requests.extend(requests)
        if not safe and not requests:
            classified = completion.get("classified_gaps") or []
            semantic = [
                str(gap.get("atom") or "") for gap in classified
                if isinstance(gap, Mapping) and gap.get("retrievable") is False
            ]
            reason = str(completion.get("stopped_reason") or "")
            no_request_reasons[label] = reason or (
                "semantic_gap_no_retrieval:" + ",".join(semantic)
                if semantic else "no_retrievable_evidence_gap"
            )
        option_memory[label] = {
            "option_text": str(option_text),
            "claim_ast": spec,
            "exact_facts": facts,
            "formula": evidence.get("formula_or_aggregation"),
            "policy_stage": spec.get("policy_stage"),
            "missing_atoms": missing,
            "conflicting_atoms": conflicts,
            "canonical_sources": sufficiency.get("source_lineage") or [],
            "safe_to_override": safe,
            "completion_status": completion.get("resolution_status"),
        }
        active_gaps[label] = {
            "missing_atoms": missing,
            "conflicting_atoms": conflicts,
            "classified_gaps": completion.get("classified_gaps") or [],
            "next_requests": requests,
            "stopped_reason": completion.get("stopped_reason"),
        }
        accepted_facts_for_hash[label] = completion.get("accepted_facts") or facts
        formulas_for_hash[label] = evidence.get("formula_or_aggregation")
        sources_for_hash[label] = sufficiency.get("source_lineage") or []

    l0 = {
        "question": question.text,
        "options": {str(label): str(text) for label, text in question.options.items()},
        "answer_contract": dict(answer_contract or {}),
        "declared_doc_ids": [str(doc_id) for doc_id in question.doc_ids],
    }
    l1 = {"option_memory": option_memory}
    l2 = {
        "unsafe_option_labels": unsafe_labels,
        "active_gaps": active_gaps,
        "next_evidence_requests": next_requests,
        "no_request_reasons": no_request_reasons,
    }
    raw_trace = [_compact_trace_row(row) for row in reading_trace if isinstance(row, Mapping)]
    before_layers = {"L0_immutable": l0, "L1_protected_facts": l1, "L2_active_gaps": l2, "L3_reading_trace": raw_trace}
    before_tokens = _estimate_tokens(before_layers)
    trigger = before_tokens > int(memory_token_budget * compression_trigger_ratio)
    compressed_trace = _deduplicate(raw_trace)
    protected_tokens = _estimate_tokens({"L0_immutable": l0, "L1_protected_facts": l1, "L2_active_gaps": l2})
    protected_allocation = max(protected_tokens, max(int(protected_fact_reserve), 0))
    l3_available = max(memory_token_budget - protected_allocation, 0)
    trace_truncated = False
    if trigger:
        compressed_trace, trace_truncated = _compress_trace(
            raw_trace, allowed_tokens=l3_available
        )
    after_layers = {"L0_immutable": l0, "L1_protected_facts": l1, "L2_active_gaps": l2, "L3_reading_trace": compressed_trace}
    after_tokens = _estimate_tokens(after_layers)
    protected_over_budget = protected_tokens > memory_token_budget
    within_total_budget = after_tokens <= memory_token_budget
    exhausted = not within_total_budget
    compression_stop_reason = (
        "protected_memory_exceeds_budget" if protected_over_budget
        else "trace_truncated_to_reserve_budget" if trace_truncated
        else "within_budget_after_compression" if trigger
        else "below_compression_threshold"
    )
    trace_before_tokens = _estimate_tokens(raw_trace) if raw_trace else 0
    trace_after_tokens = _estimate_tokens(compressed_trace) if compressed_trace else 0
    reduction = (
        (trace_before_tokens - trace_after_tokens) / trace_before_tokens
        if trace_before_tokens else 0.0
    )
    protected_hashes = {
        "question_hash": _hash(l0["question"]),
        "options_hash": _hash(l0["options"]),
        "claim_ast_hash": _hash({label: value.get("claim_ast") for label, value in option_memory.items()}),
        "accepted_facts_hash": _hash(accepted_facts_for_hash),
        "formula_hash": _hash(formulas_for_hash),
        "canonical_sources_hash": _hash(sources_for_hash),
        "final_answer_hash": _hash(final_answer),
        "final_sufficiency_hash": _hash(dict(final_sufficiency or {})),
    }
    protected = {**l0, **l1}
    reading_reason = (
        "compressed_by_budget" if trigger and raw_trace
        else "no_reading_trace" if not raw_trace
        else "below_compression_threshold"
    )
    return FinancialQuestionMemory(
        schema_version=SCHEMA_VERSION,
        qid=question.qid,
        protected_memory=protected,
        memory_layers=after_layers,
        compressed_reading_trace=tuple(compressed_trace),
        next_evidence_requests=tuple(next_requests),
        memory_token_estimate=after_tokens,
        memory_before_tokens=before_tokens,
        memory_after_tokens=after_tokens,
        memory_token_budget=memory_token_budget,
        compression_trigger_ratio=compression_trigger_ratio,
        protected_reserve_tokens=protected_fact_reserve,
        protected_allocation_tokens=protected_allocation,
        l3_available_tokens=l3_available,
        compression_triggered=trigger,
        reading_trace_token_reduction=round(reduction, 6),
        trace_truncated=trace_truncated,
        protected_over_budget=protected_over_budget,
        within_total_budget=within_total_budget,
        compression_stop_reason=compression_stop_reason,
        budget_exhausted=exhausted,
        protected_hashes=protected_hashes,
        unsafe_option_labels=tuple(unsafe_labels),
        no_request_reasons=no_request_reasons,
        reading_trace_reason=reading_reason,
        final_answer=final_answer,
        final_sufficiency=dict(final_sufficiency or {}),
    )
