"""Runtime result integrity checks for evaluation and checkpoint artifacts."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


REQUIRED_RUNTIME_FIELDS = {
    "qid", "answer_source", "fallback_used", "finish_reason",
    "truncation_risk", "ungrounded", "error", "authoritative",
}


def validate_runtime_record(record: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    missing = sorted(REQUIRED_RUNTIME_FIELDS - set(record))
    if missing:
        issues.append(f"missing_fields:{','.join(missing)}")
        return issues

    source = str(record.get("answer_source") or "")
    error = record.get("error")
    fallback = bool(record.get("fallback_used"))
    finish_reason = str(record.get("finish_reason") or "")
    truncation = bool(record.get("truncation_risk"))
    ungrounded = bool(record.get("ungrounded"))

    if error and source not in {"error", "fallback"}:
        issues.append("error_masquerades_as_normal_answer")
    if fallback and source != "fallback":
        issues.append("fallback_not_visible_in_answer_source")
    if finish_reason == "length" and not truncation:
        issues.append("length_finish_without_truncation_risk")
    if source in {"error", "unsupported_guess", "unsupported_guess_truncated"} and not ungrounded:
        issues.append("unsafe_answer_source_not_marked_ungrounded")
    if error and bool(record.get("completed", False)):
        issues.append("failed_record_counted_completed")
    return issues


def authoritative_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Return exactly one authoritative record per qid or raise on ambiguity."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("qid") or "")].append(record)
    result: dict[str, Mapping[str, Any]] = {}
    for qid, rows in grouped.items():
        selected = [row for row in rows if bool(row.get("authoritative"))]
        if len(selected) != 1:
            raise ValueError(f"qid={qid} authoritative_count={len(selected)}")
        result[qid] = selected[0]
    return result


def merge_checkpoint_records(
    existing: Iterable[Mapping[str, Any]],
    rerun: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Preserve completed authoritative rows unless a traced rerun replaces them."""
    merged = {str(row["qid"]): row for row in authoritative_records(existing).values()}
    for row in rerun:
        qid = str(row.get("qid") or "")
        if qid in merged:
            if row.get("replaces_attempt_id") != merged[qid].get("attempt_id"):
                raise ValueError(f"qid={qid} rerun_missing_replacement_lineage")
        merged[qid] = row
    authoritative_records(merged.values())
    return list(merged.values())
