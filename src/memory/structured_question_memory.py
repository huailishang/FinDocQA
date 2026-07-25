"""Compact L0-L3 question memory with independently verified protected hashes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from contracts import Question
from evidence_completion.requirement_graph import EvidenceRequirementGraph, NodeType

SCHEMA_VERSION = "structured_question_memory_v2_minimal_protected"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _tokens(value: Any) -> int:
    return max(1, (len(_canonical(value)) + 3) // 4)


def _deep_copy(value: Any) -> Any:
    return json.loads(_canonical(value))


@dataclass(frozen=True)
class StructuredQuestionMemory:
    schema_version: str
    qid: str
    layers: Mapping[str, Any]
    protected_hashes: Mapping[str, str]
    protected_hashes_before: Mapping[str, str]
    protected_hashes_after: Mapping[str, str]
    independently_verified: bool
    protected_token_breakdown: Mapping[str, int]
    before_tokens: int
    after_tokens: int
    token_budget: int
    compression_triggered: bool
    trace_truncated: bool
    protected_over_budget: bool
    within_total_budget: bool
    compression_stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _short_ref(value: Any, size: int = 14) -> str:
    return _hash(value)[:size]


def _minimal_fact(node: Mapping[str, Any]) -> dict[str, Any]:
    resolved = dict(node.get("resolved_fields") or {})
    metadata = dict(node.get("metadata") or {})
    source = str((node.get("source_refs") or [""])[0])
    return {
        "original_fact_id": metadata.get("fact_id") or node.get("node_id"),
        "requirement_ref": _short_ref(resolved.get("requirement_id")),
        "atom_id": resolved.get("atom_id"),
        "fact_type": resolved.get("fact_type"),
        "entity": resolved.get("entity"),
        "role": resolved.get("role"),
        "period_or_date": resolved.get("period_or_date"),
        "metric_or_field": resolved.get("metric_or_field"),
        "value": resolved.get("value"),
        "unit": resolved.get("unit"),
        "doc_id": resolved.get("doc_id"),
        "source_ref": _short_ref(source),
        "source_anchor": resolved.get("source_anchor"),
        "source_span_sha256": resolved.get("source_span_sha256"),
        "canonical_verified": metadata.get("canonical_verified") is True,
    }


def _minimal_rule(node: Mapping[str, Any], fact_refs: Mapping[str, str]) -> dict[str, Any]:
    resolved = dict(node.get("resolved_fields") or {})
    return {
        "tool_ref": _short_ref(node.get("node_id")),
        "option_label": node.get("option_label"),
        "requirement_ref": _short_ref(resolved.get("requirement_id")),
        "tool": resolved.get("tool"),
        "formula_or_rule": resolved.get("formula_or_rule"),
        "source_fact_refs": [
            fact_refs.get(str(fact_id), _short_ref(fact_id))
            for fact_id in (resolved.get("source_fact_ids") or node.get("dependencies") or ())
        ],
        "result": resolved.get("result"),
        "status": node.get("status"),
    }


def _hashes(l0: Mapping[str, Any], l1: Mapping[str, Any]) -> dict[str, str]:
    return {
        "question_hash": _hash(l0["question"]),
        "options_hash": _hash(l0["options"]),
        "answer_contract_hash": _hash(l0["answer_contract"]),
        "accepted_facts_hash": _hash(l1["accepted_exact_facts"]),
        "formulas_and_rules_hash": _hash(l1["formulas_and_rules"]),
        "canonical_sources_hash": _hash(l1["canonical_sources"]),
    }


def build_structured_question_memory(
    question: Question,
    graph: EvidenceRequirementGraph,
    *,
    transitions: Sequence[Mapping[str, Any]] = (),
    rejected_evidence: Sequence[Mapping[str, Any]] = (),
    intermediate_summaries: Sequence[Mapping[str, Any]] = (),
    token_budget: int = 2400,
    compression_trigger_ratio: float = 0.70,
) -> StructuredQuestionMemory:
    nodes = [node.to_dict() for node in graph.nodes]
    fact_rows = [
        _minimal_fact(node)
        for node in nodes
        if node["node_type"] == NodeType.EVIDENCE_FACT.value
        and node["status"] == "ACCEPTED"
    ]
    deduped_fact_rows = list({str(row["original_fact_id"]): row for row in fact_rows}.values())
    fact_refs = {
        str(row["original_fact_id"]): _short_ref(row["original_fact_id"])
        for row in deduped_fact_rows
    }
    facts = [
        {
            **{key: value for key, value in row.items() if key != "original_fact_id"},
            "fact_ref": fact_refs[str(row["original_fact_id"])],
        }
        for row in deduped_fact_rows
    ]
    rule_rows = [
        _minimal_rule(node, fact_refs)
        for node in nodes
        if node["node_type"] == NodeType.TOOL_EXECUTION.value
    ]
    rules = list({str(row["tool_ref"]): row for row in rule_rows}.values())
    sources = list({
        str(row.get("source_ref") or ""): {
            "source_ref": row.get("source_ref"),
            "doc_id": row.get("doc_id"),
            "source_anchor": row.get("source_anchor"),
            "source_span_sha256": row.get("source_span_sha256"),
        }
        for row in facts
        if row.get("source_ref")
    }.values())
    contract_node = next(
        (
            node for node in nodes
            if node["node_type"] == NodeType.ANSWER_CONTRACT.value
        ),
        {},
    )
    contract_resolved = dict(contract_node.get("resolved_fields") or {})
    l0 = {
        "question": question.text,
        "options": {str(key): str(value) for key, value in question.options.items()},
        "answer_contract": {
            "answer_format": question.answer_format,
            "allowed_labels": list(contract_resolved.get("allowed_labels") or question.options),
            "canonical_order": list(contract_resolved.get("canonical_order") or question.options),
            "production_answer": contract_resolved.get("production_answer"),
            "status": contract_node.get("status"),
        },
        "declared_doc_ids": [str(doc) for doc in question.doc_ids],
    }
    l1 = {
        "accepted_exact_facts": facts,
        "formulas_and_rules": rules,
        "canonical_sources": sources,
    }
    unresolved = [
        {
            "node_ref": _short_ref(node.get("node_id")),
            "node_type": node.get("node_type"),
            "option_label": node.get("option_label"),
            "missing_fields": list(node.get("missing_fields") or ()),
            "conflicts": list(node.get("conflicts") or ()),
        }
        for node in nodes
        if node.get("node_type") in {
            NodeType.CLAIM_ATOM.value,
            NodeType.CONDITION.value,
            NodeType.ANSWER_CONTRACT.value,
            NodeType.OPTION_DECISION.value,
        }
        and node.get("status") not in {
            "RESOLVED", "SUPPORTED", "CONTRADICTED", "ACCEPTED", "COMPLETED"
        }
    ]
    l2 = {
        "active_unresolved_nodes": unresolved,
        "next_requests": [
            row for row in unresolved
            if row["node_type"] in {
                NodeType.CLAIM_ATOM.value,
                NodeType.CONDITION.value,
            }
        ],
        "stop_reasons": list(dict.fromkeys(
            str(row.get("reason") or "")
            for row in transitions
            if row.get("to_state") == "BLOCKED"
        )),
    }
    compact_transitions = [
        {
            "sequence": row.get("sequence"),
            "from_state": row.get("from_state"),
            "to_state": row.get("to_state"),
            "reason": row.get("reason"),
            "active_gap_ids": list(row.get("active_gap_ids") or ())[:12],
            "new_fact_ids": list(row.get("new_fact_ids") or ())[:12],
            "rejected_fact_ids": list(row.get("rejected_fact_ids") or ())[:12],
        }
        for row in transitions
    ]
    compact_rejected = [
        {
            "hit_id": ((row.get("hit") or {}).get("hit_id")),
            "requirement_id": ((row.get("hit") or {}).get("requirement_id")),
            "grade": row.get("grade"),
            "reasons": list(row.get("reasons") or ()),
        }
        for row in rejected_evidence
    ]
    l3 = {
        "transition_trace": compact_transitions,
        "rejected_evidence": compact_rejected,
        "intermediate_summaries": [dict(row) for row in intermediate_summaries],
    }

    before_protected = _deep_copy({"L0_immutable": l0, "L1_protected": l1})
    before_hashes = _hashes(
        before_protected["L0_immutable"], before_protected["L1_protected"]
    )
    raw_layers = {
        "L0_immutable": l0,
        "L1_protected": l1,
        "L2_active": l2,
        "L3_compressible": l3,
    }
    before = _tokens(raw_layers)
    limit = max(1, int(token_budget))
    trigger = before > int(limit * float(compression_trigger_ratio))
    protected = {"L0_immutable": l0, "L1_protected": l1, "L2_active": l2}
    protected_tokens = _tokens(protected)
    available = max(limit - protected_tokens, 0)
    traces = list(l3["transition_trace"])
    rejected = list(l3["rejected_evidence"])
    summaries = list(l3["intermediate_summaries"])
    truncated = False
    if trigger:
        while _tokens({
            "transition_trace": traces,
            "rejected_evidence": rejected,
            "intermediate_summaries": summaries,
        }) > available:
            if rejected:
                rejected.pop()
            elif summaries:
                summaries.pop()
            elif len(traces) > 1:
                traces.pop(0)
            elif traces:
                traces.clear()
            else:
                break
            truncated = True
    layers = {
        **protected,
        "L3_compressible": {
            "transition_trace": traces,
            "rejected_evidence": rejected,
            "intermediate_summaries": summaries,
        },
    }
    after_protected = _deep_copy({
        "L0_immutable": layers["L0_immutable"],
        "L1_protected": layers["L1_protected"],
    })
    after_hashes = _hashes(
        after_protected["L0_immutable"], after_protected["L1_protected"]
    )
    independently_verified = before_hashes == after_hashes
    after = _tokens(layers)
    protected_over = protected_tokens > limit
    within = after <= limit
    reason = (
        "protected_memory_exceeds_budget" if protected_over
        else "l3_truncated_to_budget" if truncated
        else "within_budget_after_compression" if trigger
        else "below_compression_threshold"
    )
    return StructuredQuestionMemory(
        schema_version=SCHEMA_VERSION,
        qid=question.qid,
        layers=layers,
        protected_hashes=after_hashes,
        protected_hashes_before=before_hashes,
        protected_hashes_after=after_hashes,
        independently_verified=independently_verified,
        protected_token_breakdown={
            "L0_immutable": _tokens(l0),
            "L1_protected": _tokens(l1),
            "L2_active": _tokens(l2),
            "protected_total": protected_tokens,
        },
        before_tokens=before,
        after_tokens=after,
        token_budget=limit,
        compression_triggered=trigger,
        trace_truncated=truncated,
        protected_over_budget=protected_over,
        within_total_budget=within,
        compression_stop_reason=reason,
    )
