"""Compact evidence packs and enforce a pre-call hard budget for preview models."""
from __future__ import annotations

import json
from typing import Any, Mapping


def _top_evidence_spans(retrieval: Mapping[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("child_hits", "parent_or_table_contexts"):
        for hit in retrieval.get(key) or []:
            text = str(hit.get("text") or hit.get("span") or hit.get("content") or "").strip()
            if not text:
                continue
            row = {
                "doc_id": hit.get("doc_id"),
                "source_path": hit.get("source_path") or hit.get("path"),
                "page": hit.get("page") or hit.get("page_number"),
                "text": text[:1800],
            }
            if row not in rows:
                rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


def compress_preview_pack(pack: Mapping[str, Any], *, max_spans: int = 6) -> dict[str, Any]:
    explorer = dict(pack.get("qwen37_max_explorer") or {})
    request = dict(pack.get("evidence_request") or {})
    retrieval = dict(pack.get("opportunity_specific_crag") or {})
    binding = dict(pack.get("canonical_binding") or {})
    generic = dict(pack.get("generic_all_option_adjudication") or {})
    local = dict(pack.get("local_closure") or {})
    unresolved_labels = list(generic.get("unresolved_labels") or [])
    if not unresolved_labels:
        unresolved_labels = list((pack.get("strict_route_verification") or {}).get("unresolved_labels") or [])
    typed_binding_summary = {
        "required_entity": binding.get("required_entity") or request.get("required_entity"),
        "required_document": binding.get("required_document") or request.get("required_document_raw") or request.get("required_doc_ids"),
        "required_metric_or_clause": binding.get("required_metric_or_clause") or request.get("required_metric_or_clause"),
        "required_period_or_date_role": binding.get("required_period_or_date_role") or request.get("required_period_or_date_role"),
        "required_value_or_threshold": binding.get("required_value_or_threshold") or request.get("required_value_or_threshold"),
        "required_unit_or_basis": binding.get("required_unit_or_basis") or request.get("required_unit_or_basis"),
        "condition_or_exception": binding.get("condition_or_exception") or request.get("condition_or_exception"),
        "direction_or_negation": binding.get("direction_or_negation") or request.get("direction_or_negation"),
    }
    return {
        "qid": pack.get("qid"),
        "question": pack.get("question"),
        "options": pack.get("options"),
        "baseline_answer": pack.get("baseline_answer"),
        "max_disagreement": {
            "proposed_answer": explorer.get("proposed_answer"),
            "changed_labels": explorer.get("changed_labels"),
            "why_baseline_may_be_wrong": explorer.get("why_baseline_may_be_wrong"),
            "uncertainty": explorer.get("uncertainty"),
        },
        "evidence_needed": explorer.get("evidence_needed") or request.get("evidence_needed"),
        "top_canonical_evidence_spans": _top_evidence_spans(retrieval, limit=max_spans),
        "typed_binding_summary": typed_binding_summary,
        "unresolved_labels": unresolved_labels,
        "exact_unresolved_reason": local.get("method") or local.get("status") or "LOCAL_UNRESOLVED",
    }


def serialized_chars(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def estimate_prompt_tokens(payload: Mapping[str, Any], *, wrapper_chars: int = 700) -> int:
    chars = serialized_chars(payload) + int(wrapper_chars)
    return max(1, (chars + 1) // 2)


def preview_budget_decision(*, payload: Mapping[str, Any], used_tokens: int, hard_cap_tokens: int, max_completion_tokens: int) -> dict[str, Any]:
    estimated_prompt_tokens = max(1, (serialized_chars(payload) + 701) // 2)
    projected_total = int(used_tokens) + estimated_prompt_tokens + int(max_completion_tokens)
    return {
        "used_tokens": int(used_tokens),
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "max_completion_tokens": int(max_completion_tokens),
        "projected_stage_total_tokens": projected_total,
        "hard_cap_tokens": int(hard_cap_tokens),
        "allowed": projected_total <= int(hard_cap_tokens),
        "budget_semantics": "HARD_CAP_PRE_CALL_ESTIMATE",
    }
