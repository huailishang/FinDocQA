"""Map FinDocQA evidence-sufficiency judgments to Platform validation-result-v0.1.

The adapter deliberately returns a plain serializable mapping instead of importing
agent-runtime-platform. FinDocQA keeps its domain validation model and only emits
the stable cross-repository contract at the integration edge.
"""
from __future__ import annotations

from typing import Any, Mapping


PLATFORM_VALIDATION_SCHEMA_VERSION = "validation-result-v0.1"
VALIDATOR_ID = "findocqa.evidence_sufficiency"


def adapt_evidence_sufficiency_to_platform(
    sufficiency: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate one real evidence-sufficiency result without re-validating it."""

    answer_ready = sufficiency.get("answer_ready_by_evidence") is True
    gap_count = int(sufficiency.get("gap_count") or 0)

    if answer_ready:
        verdict = "PASS"
    elif gap_count > 0:
        verdict = "BLOCKED"
    else:
        verdict = "INDETERMINATE"

    findings: list[dict[str, Any]] = []
    top_level_refs: list[dict[str, str | None]] = []
    seen_refs: set[tuple[str, str, str | None]] = set()

    for gap in sufficiency.get("gaps") or []:
        if not isinstance(gap, Mapping):
            continue
        refs = _gap_evidence_refs(gap)
        findings.append(
            {
                "code": str(gap.get("gap_type") or "evidence_gap"),
                "message": str(gap.get("action") or gap.get("gap_type") or "evidence gap"),
                "severity": None,
                "evidence_refs": refs,
            }
        )
        for ref in refs:
            key = (str(ref["ref_type"]), str(ref["ref"]), ref.get("locator"))
            if key not in seen_refs:
                seen_refs.add(key)
                top_level_refs.append(ref)

    limitations: list[str] = []
    if sufficiency.get("question_required_docs_covered") is not True:
        limitations.append("required_documents_not_fully_covered")
    if sufficiency.get("all_option_closure") is not True:
        limitations.append("option_evidence_not_fully_closed")

    return {
        "schema_version": PLATFORM_VALIDATION_SCHEMA_VERSION,
        "verdict": verdict,
        "validator_id": VALIDATOR_ID,
        "validator_version": str(sufficiency.get("schema_version") or "unknown"),
        "findings": findings,
        "evidence_refs": top_level_refs,
        "limitations": limitations,
    }


def _gap_evidence_refs(gap: Mapping[str, Any]) -> list[dict[str, str | None]]:
    return [
        {
            "ref_type": "document",
            "ref": str(doc_id),
            "locator": None,
        }
        for doc_id in (gap.get("doc_ids") or [])
        if str(doc_id)
    ]
