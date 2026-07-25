"""Independent solver and verifier evidence-lineage channels.

A solver declaration describes documents the model says it used.  A verifier
lineage describes authoritative sources used by deterministic typed evidence.
The two channels are intentionally never merged: verifier sources cannot repair
or impersonate missing solver usage, and solver candidates cannot certify a
verifier decision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


UNKNOWN_SOLVER_SOURCES = {
    "",
    "unknown",
    "dry_run_no_usage_proof",
    "llm_error_no_usage_proof",
}


@dataclass(frozen=True)
class LineageChannel:
    doc_ids: tuple[str, ...]
    source_refs: tuple[dict[str, Any], ...]
    source: str
    complete: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["doc_ids"] = list(self.doc_ids)
        payload["source_refs"] = [dict(item) for item in self.source_refs]
        payload["errors"] = list(self.errors)
        return payload


def _stable_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(sorted({str(item) for item in value if str(item).strip()}))


def _normalise_ref(value: Any, *, channel: str) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        payload = {str(key): item for key, item in value.items()}
        doc_id = str(payload.get("doc_id") or "").strip()
        evidence_ref = str(
            payload.get("evidence_ref")
            or payload.get("canonical_source")
            or payload.get("source_relpath")
            or payload.get("source")
            or ""
        ).strip()
        if not doc_id and not evidence_ref:
            return None
        payload["doc_id"] = doc_id
        payload["channel"] = channel
        if evidence_ref:
            payload["evidence_ref"] = evidence_ref
        return payload
    text = str(value or "").strip()
    if not text:
        return None
    return {
        "doc_id": "",
        "evidence_ref": text,
        "channel": channel,
    }


def _dedupe_refs(values: Sequence[Any], *, channel: str) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        item = _normalise_ref(value, channel=channel)
        if not item:
            continue
        key = (
            str(item.get("doc_id") or ""),
            str(item.get("evidence_ref") or ""),
            str(item.get("source_sha256") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def solver_lineage(metadata: Mapping[str, Any] | None) -> LineageChannel:
    """Extract only model/solver-declared usage lineage."""
    meta = dict(metadata or {})
    doc_ids = _stable_strings(
        meta.get("solver_used_doc_ids")
        if meta.get("solver_used_doc_ids") is not None
        else meta.get("used_doc_ids")
    )
    source = str(
        meta.get("solver_lineage_source")
        or meta.get("used_docs_source")
        or "unknown"
    )
    raw_refs = meta.get("solver_source_refs")
    if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, (str, bytes, bytearray)):
        refs = _dedupe_refs(raw_refs, channel="solver")
    else:
        refs = tuple(
            {
                "doc_id": doc_id,
                "evidence_ref": doc_id,
                "channel": "solver",
                "source": source,
            }
            for doc_id in doc_ids
        )
    errors: list[str] = []
    if source.lower() in UNKNOWN_SOLVER_SOURCES:
        errors.append("solver_usage_source_unknown")
    if not doc_ids:
        errors.append("solver_used_doc_ids_missing")
    complete = not errors
    return LineageChannel(doc_ids, refs, source, complete, tuple(errors))


def _verdict_refs(verdict: Mapping[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key in (
        "source_refs",
        "resolved_evidence_refs",
        "evidence_refs",
        "canonical_sources",
    ):
        raw = verdict.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            values.extend(raw)
        elif raw:
            values.append(raw)
    if not values and verdict.get("canonical_source"):
        values.append(verdict["canonical_source"])
    return values


def verifier_lineage(
    typed_option_evidence: Mapping[str, Any] | None,
    *,
    defined_option_labels: Sequence[str] = (),
) -> LineageChannel:
    """Extract only deterministic verifier evidence lineage.

    Completeness requires a trusted all-option contract and at least one source
    reference for every defined option.  The verifier's declared document list
    is recorded separately from the solver channel.
    """
    typed = dict(typed_option_evidence or {})
    verdicts = typed.get("option_verdicts")
    verdict_map = dict(verdicts) if isinstance(verdicts, Mapping) else {}
    labels = tuple(
        sorted(
            {str(label).upper() for label in defined_option_labels if str(label).strip()}
            or {str(label).upper() for label in verdict_map if str(label).strip()}
        )
    )
    doc_ids = _stable_strings(
        typed.get("verifier_evidence_doc_ids")
        if typed.get("verifier_evidence_doc_ids") is not None
        else typed.get("used_doc_ids")
    )
    raw_refs: list[Any] = []
    top_refs = typed.get("verifier_source_refs")
    if isinstance(top_refs, Sequence) and not isinstance(top_refs, (str, bytes, bytearray)):
        raw_refs.extend(top_refs)
    for label in labels:
        verdict = verdict_map.get(label)
        if isinstance(verdict, Mapping):
            raw_refs.extend(_verdict_refs(verdict))
    refs = _dedupe_refs(raw_refs, channel="verifier")
    inferred_doc_ids = {
        str(item.get("doc_id") or "").strip()
        for item in refs
        if str(item.get("doc_id") or "").strip()
    }
    if inferred_doc_ids:
        doc_ids = tuple(sorted(set(doc_ids) | inferred_doc_ids))

    errors: list[str] = []
    if typed.get("trusted_for_production") is not True:
        errors.append("typed_option_evidence_untrusted")
    if not verdict_map:
        errors.append("verifier_option_verdicts_missing")
    unresolved = typed.get("unresolved_after_typed")
    if isinstance(unresolved, Sequence) and not isinstance(unresolved, (str, bytes, bytearray)) and list(unresolved):
        errors.append("verifier_unresolved_options")
    for label in labels:
        verdict = verdict_map.get(label)
        if not isinstance(verdict, Mapping):
            errors.append(f"verifier_option_{label}_missing")
            continue
        status = str(verdict.get("status") or verdict.get("factual_status") or "").lower()
        if status not in {"supported", "contradicted", "not_supported", "not_applicable", "scope_excluded"}:
            errors.append(f"verifier_option_{label}_unresolved")
        if not _verdict_refs(verdict):
            errors.append(f"verifier_option_{label}_source_refs_missing")
    if not doc_ids:
        errors.append("verifier_evidence_doc_ids_missing")
    if not refs:
        errors.append("verifier_source_refs_missing")
    if typed.get("declared_document_lineage_complete") is False:
        errors.append("verifier_declared_document_lineage_incomplete")
    complete = not errors
    source = str(typed.get("domain_evidence_provider") or "typed_option_evidence")
    return LineageChannel(doc_ids, refs, source, complete, tuple(sorted(set(errors))))


def final_answer_authority(
    *,
    solver_answer: str,
    final_answer: str,
    answer_source: str,
    solver: LineageChannel,
    verifier: LineageChannel,
    typed_option_evidence: Mapping[str, Any] | None,
) -> str:
    """Choose one non-overlapping authority channel for the emitted answer."""
    source = str(answer_source or "").lower()
    if source in {"baseline", "baseline_fallback", "inherited_baseline", "baseline_preserve"}:
        return "baseline_fallback"
    typed = dict(typed_option_evidence or {})
    typed_answer = str(
        typed.get("correction_proposal")
        or typed.get("typed_supported_answer")
        or ""
    )
    if (
        verifier.complete
        and typed_answer
        and str(final_answer or "") == typed_answer
        and (
            str(final_answer or "") != str(solver_answer or "")
            or not solver.complete
        )
    ):
        return "verifier"
    return "solver"


def accepted_final_state(value: Any) -> bool:
    return str(value or "").lower() in {"accepted", "accepted_by_verifier_evidence"}
