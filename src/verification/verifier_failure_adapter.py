"""Deterministic P13 verifier -> P14 failure-signal adapter.

This module is intentionally a translation boundary only.  It does not choose
recovery actions, arbitrate composite failures, retrieve more evidence, or call
a provider.  The downstream P14D integration layer can consume the normalized
signals without treating every UNRESOLVED verdict as missing evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from .atom_evidence_verifier import AtomVerdict, REFUTE, SUPPORT, UNRESOLVED
from .claim_atoms import ClaimAtom
from .claim_verifier import (
    CLAIM_REFUTED,
    CLAIM_SUPPORTED,
    CLAIM_UNRESOLVED,
    ClaimVerificationResult,
)


MISSING_EVIDENCE = "MISSING_EVIDENCE"
LINEAGE_LOST = "LINEAGE_LOST"
BINDING_FAILED = "BINDING_FAILED"
UNKNOWN_FAILURE = "UNKNOWN_FAILURE"
MULTIPLE_FAILURE_SIGNALS = "MULTIPLE_FAILURE_SIGNALS"
NO_FAILURE = ""

_MISSING_REASONS = {
    "EMPTY_EVIDENCE",
    "NO_EVIDENCE_CANDIDATES",
}
_LINEAGE_REASONS = {
    "LINEAGE_INCOMPLETE",
}
_BINDING_REASONS = {
    "SUBJECT_MISMATCH_OR_MISSING",
    "OBJECT_OR_METRIC_MISSING",
    "TIME_SCOPE_MISSING_OR_MISMATCH",
    "CONDITION_NOT_ESTABLISHED",
    "EXCEPTION_NOT_ESTABLISHED",
    "SUBJECT_SCOPE_AMBIGUOUS_ACROSS_DOCS",
    "CROSS_DOC_FRANKENSTEIN_BLOCKED",
    "METRIC_LOCAL_ANCHOR_MISSING",
    "METRIC_LOCAL_PROPOSITION_NOT_FOUND",
    "METRIC_LOCAL_VALUE_MISSING",
    "METRIC_LOCAL_MULTIPLE_VALUES_AMBIGUOUS",
    "NUMERIC_VALUE_MISSING",
    "UNIT_INCOMPATIBLE",
    "UNIT_OR_VALUE_UNRESOLVED",
}
_UNKNOWN_REASONS = {
    "SCOPE_CONFIDENCE_LOW",
    "SEMANTIC_SCOPE_INCOMPLETE",
    "TEXTUAL_ENTAILMENT_NOT_DETERMINISTIC",
    "CLAIM_SEMANTIC_ANCHORS_INSUFFICIENT",
    "CONFLICTING_AUDITABLE_EVIDENCE",
    "EVIDENCE_INSUFFICIENT",
    "CLAIM_VALUE_NON_NUMERIC",
    "NUMERIC_RELATION_UNRESOLVED",
    "NEGATIVE_POLARITY_NOT_EXPLICIT",
}


@dataclass(frozen=True)
class FailureSignalAdapterResult:
    """Normalized, audit-friendly verifier failure envelope."""

    verdict: str
    failure_code: str
    failure_signals: tuple[str, ...]
    reason_codes: tuple[str, ...]
    scope_confidence: str
    evidence_available: bool
    evidence_count: int
    evidence_refs: tuple[str, ...]
    raw_evidence_refs: tuple[str, ...]
    used_doc_ids: tuple[str, ...]
    binding_auditable: bool
    adapter_reason: str
    execution_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "failure_signals",
            "reason_codes",
            "evidence_refs",
            "raw_evidence_refs",
            "used_doc_ids",
        ):
            payload[key] = list(payload[key])
        return payload


def adapt_atom_verdict(
    atom: ClaimAtom,
    verdict: AtomVerdict,
    *,
    evidence_count: int | None = None,
    raw_evidence_refs: Sequence[str] = (),
    used_doc_ids: Sequence[str] = (),
) -> FailureSignalAdapterResult:
    """Translate one atom verdict into normalized failure signals.

    ``evidence_count`` is deliberately explicit.  A non-empty selected evidence
    set must not become MISSING_EVIDENCE merely because the verifier returned
    UNRESOLVED or because a bound lineage field is empty.
    """
    normalized_count = _normalize_evidence_count(
        evidence_count,
        verdict.evidence_refs,
        raw_evidence_refs,
        used_doc_ids,
    )
    evidence_refs = _unique(verdict.evidence_refs)
    raw_refs = _unique(raw_evidence_refs)
    doc_ids = _unique(used_doc_ids)
    evidence_available = _evidence_available(
        evidence_count=evidence_count,
        normalized_count=normalized_count,
        evidence_refs=evidence_refs,
        raw_evidence_refs=raw_refs,
        used_doc_ids=doc_ids,
    )
    reasons = _unique(verdict.reason_codes)
    scope_confidence = str(atom.scope_confidence or "UNKNOWN").upper()

    if verdict.verdict in {SUPPORT, REFUTE}:
        return _result(
            verdict=verdict.verdict,
            failure_signals=(),
            reason_codes=reasons,
            scope_confidence=scope_confidence,
            evidence_available=evidence_available,
            evidence_count=normalized_count,
            evidence_refs=evidence_refs,
            raw_evidence_refs=raw_refs,
            used_doc_ids=doc_ids,
            binding_auditable=verdict.binding_auditable,
            adapter_reason="authoritative_verdict_no_failure_signal",
        )

    if verdict.verdict != UNRESOLVED:
        return _result(
            verdict=verdict.verdict,
            failure_signals=(UNKNOWN_FAILURE,),
            reason_codes=reasons,
            scope_confidence=scope_confidence,
            evidence_available=evidence_available,
            evidence_count=normalized_count,
            evidence_refs=evidence_refs,
            raw_evidence_refs=raw_refs,
            used_doc_ids=doc_ids,
            binding_auditable=verdict.binding_auditable,
            adapter_reason="unknown_verdict_fail_closed",
        )

    reason_set = set(reasons)

    # Direct evidence absence has the strongest and narrowest meaning.  An
    # explicit count of zero is authoritative.  EMPTY_EVIDENCE also means the
    # supplied candidate carried no usable evidence content.  A contradictory
    # "NO_EVIDENCE_CANDIDATES" reason with a positive explicit count is not
    # guessed through; it fails closed to UNKNOWN_FAILURE.
    contradictory_no_candidate = (
        evidence_count is not None
        and evidence_count > 0
        and "NO_EVIDENCE_CANDIDATES" in reason_set
    )
    direct_missing = (
        evidence_count == 0
        or "EMPTY_EVIDENCE" in reason_set
        or ("NO_EVIDENCE_CANDIDATES" in reason_set and not contradictory_no_candidate)
    )
    if direct_missing:
        return _result(
            verdict=UNRESOLVED,
            failure_signals=(MISSING_EVIDENCE,),
            reason_codes=reasons,
            scope_confidence=scope_confidence,
            evidence_available=False,
            evidence_count=normalized_count,
            evidence_refs=evidence_refs,
            raw_evidence_refs=raw_refs,
            used_doc_ids=doc_ids,
            binding_auditable=False,
            adapter_reason="direct_structured_evidence_absence",
        )

    signals: list[str] = []
    adapter_reasons: list[str] = []
    if contradictory_no_candidate:
        signals.append(UNKNOWN_FAILURE)
        adapter_reasons.append("contradictory_evidence_metadata_fail_closed")

    # Do not arbitrate composite causes here.  Preserve every applicable class
    # and let P14D decide how simultaneous lineage/binding/unknown signals should
    # affect recovery.
    if evidence_available and reason_set.intersection(_LINEAGE_REASONS):
        signals.append(LINEAGE_LOST)
        adapter_reasons.append("evidence_exists_but_lineage_incomplete")
    if evidence_available and reason_set.intersection(_BINDING_REASONS):
        signals.append(BINDING_FAILED)
        adapter_reasons.append("evidence_exists_but_claim_binding_not_closed")

    known_reasons = _MISSING_REASONS | _LINEAGE_REASONS | _BINDING_REASONS | _UNKNOWN_REASONS
    has_unknown_semantics = bool(reason_set.intersection(_UNKNOWN_REASONS))
    has_unmapped_reason = bool(reason_set - known_reasons)
    if has_unknown_semantics:
        signals.append(UNKNOWN_FAILURE)
        adapter_reasons.append("semantic_or_verifier_uncertainty_not_retrieval_failure")
    if has_unmapped_reason:
        signals.append(UNKNOWN_FAILURE)
        adapter_reasons.append("unmapped_reason_fail_closed_to_unknown")
    if not signals:
        signals.append(UNKNOWN_FAILURE)
        adapter_reasons.append("unresolved_without_structured_root_cause")

    return _result(
        verdict=UNRESOLVED,
        failure_signals=signals,
        reason_codes=reasons,
        scope_confidence=scope_confidence,
        evidence_available=evidence_available,
        evidence_count=normalized_count,
        evidence_refs=evidence_refs,
        raw_evidence_refs=raw_refs,
        used_doc_ids=doc_ids,
        binding_auditable=False,
        adapter_reason=";".join(dict.fromkeys(adapter_reasons)),
    )


def adapt_claim_verdict(
    claim: ClaimVerificationResult,
    *,
    evidence_count: int | None = None,
    raw_evidence_refs: Sequence[str] = (),
    used_doc_ids: Sequence[str] = (),
) -> FailureSignalAdapterResult:
    """Translate a claim result without performing composite arbitration.

    If a claim is already conclusively supported/refuted, no failure is emitted.
    For an unresolved claim, atom-level signals are preserved.  Multiple signal
    classes are returned as-is for P14D instead of choosing a recovery winner.
    """
    verdict = str(claim.aggregate_verdict or "")
    all_reasons = _unique(
        reason
        for atom_verdict in claim.atom_verdicts
        for reason in atom_verdict.reason_codes
    )
    evidence_refs = _unique(
        ref
        for atom_verdict in claim.atom_verdicts
        for ref in atom_verdict.evidence_refs
    )
    raw_refs = _unique(raw_evidence_refs)
    doc_ids = _unique(used_doc_ids)
    normalized_count = _normalize_evidence_count(evidence_count, evidence_refs, raw_refs, doc_ids)
    evidence_available = _evidence_available(
        evidence_count=evidence_count,
        normalized_count=normalized_count,
        evidence_refs=evidence_refs,
        raw_evidence_refs=raw_refs,
        used_doc_ids=doc_ids,
    )
    scope_confidence = _claim_scope_confidence(claim.atoms)

    if verdict in {CLAIM_SUPPORTED, CLAIM_REFUTED}:
        return _result(
            verdict=verdict,
            failure_signals=(),
            reason_codes=all_reasons,
            scope_confidence=scope_confidence,
            evidence_available=evidence_available,
            evidence_count=normalized_count,
            evidence_refs=evidence_refs,
            raw_evidence_refs=raw_refs,
            used_doc_ids=doc_ids,
            binding_auditable=bool(claim.atom_verdicts)
            and all(row.binding_auditable for row in claim.atom_verdicts if row.verdict in {SUPPORT, REFUTE}),
            adapter_reason="conclusive_claim_verdict_no_failure_signal",
        )

    if verdict != CLAIM_UNRESOLVED:
        return _result(
            verdict=verdict,
            failure_signals=(UNKNOWN_FAILURE,),
            reason_codes=all_reasons,
            scope_confidence=scope_confidence,
            evidence_available=evidence_available,
            evidence_count=normalized_count,
            evidence_refs=evidence_refs,
            raw_evidence_refs=raw_refs,
            used_doc_ids=doc_ids,
            binding_auditable=False,
            adapter_reason="unknown_claim_verdict_fail_closed",
        )

    atom_results = [
        adapt_atom_verdict(
            atom,
            atom_verdict,
            evidence_count=evidence_count,
            raw_evidence_refs=raw_refs,
            used_doc_ids=doc_ids,
        )
        for atom, atom_verdict in zip(claim.atoms, claim.atom_verdicts)
        if atom_verdict.verdict == UNRESOLVED
    ]
    signals = _ordered_signals(
        signal
        for result in atom_results
        for signal in result.failure_signals
        if signal
    )
    if not signals:
        signals = (UNKNOWN_FAILURE,)

    if len(signals) == 1:
        failure_code = signals[0]
        adapter_reason = "single_claim_failure_signal"
    else:
        failure_code = MULTIPLE_FAILURE_SIGNALS
        adapter_reason = "multiple_atom_failure_signals_deferred_to_p14d"

    return FailureSignalAdapterResult(
        verdict=verdict,
        failure_code=failure_code,
        failure_signals=signals,
        reason_codes=all_reasons,
        scope_confidence=scope_confidence,
        evidence_available=evidence_available,
        evidence_count=normalized_count,
        evidence_refs=evidence_refs,
        raw_evidence_refs=raw_refs,
        used_doc_ids=doc_ids,
        binding_auditable=False,
        adapter_reason=adapter_reason,
        execution_authorized=False,
    )


def _result(
    *,
    verdict: str,
    failure_signals: Sequence[str],
    reason_codes: Sequence[str],
    scope_confidence: str,
    evidence_available: bool,
    evidence_count: int,
    evidence_refs: Sequence[str],
    raw_evidence_refs: Sequence[str],
    used_doc_ids: Sequence[str],
    binding_auditable: bool,
    adapter_reason: str,
) -> FailureSignalAdapterResult:
    signals = _ordered_signals(failure_signals)
    failure_code = signals[0] if len(signals) == 1 else MULTIPLE_FAILURE_SIGNALS if signals else NO_FAILURE
    return FailureSignalAdapterResult(
        verdict=str(verdict or ""),
        failure_code=failure_code,
        failure_signals=signals,
        reason_codes=_unique(reason_codes),
        scope_confidence=str(scope_confidence or "UNKNOWN").upper(),
        evidence_available=bool(evidence_available),
        evidence_count=max(0, int(evidence_count)),
        evidence_refs=_unique(evidence_refs),
        raw_evidence_refs=_unique(raw_evidence_refs),
        used_doc_ids=_unique(used_doc_ids),
        binding_auditable=bool(binding_auditable),
        adapter_reason=str(adapter_reason or ""),
        execution_authorized=False,
    )


def _normalize_evidence_count(
    evidence_count: int | None,
    evidence_refs: Sequence[str],
    raw_evidence_refs: Sequence[str],
    used_doc_ids: Sequence[str],
) -> int:
    if evidence_count is not None:
        return max(0, int(evidence_count))
    # Count is metadata, not a fabricated retrieval result.  If the caller does
    # not know the selected-window count, preserve the strongest observable
    # lower bound from references/doc ids.
    return max(len(_unique(evidence_refs)), len(_unique(raw_evidence_refs)), len(_unique(used_doc_ids)))


def _evidence_available(
    *,
    evidence_count: int | None,
    normalized_count: int,
    evidence_refs: Sequence[str],
    raw_evidence_refs: Sequence[str],
    used_doc_ids: Sequence[str],
) -> bool:
    if evidence_count is not None:
        return int(evidence_count) > 0
    return bool(normalized_count or evidence_refs or raw_evidence_refs or used_doc_ids)


def _claim_scope_confidence(atoms: Sequence[ClaimAtom]) -> str:
    values = {str(atom.scope_confidence or "UNKNOWN").upper() for atom in atoms}
    if not values:
        return "UNKNOWN"
    if len(values) == 1:
        return next(iter(values))
    if "LOW" in values:
        return "LOW"
    return "MIXED"


def _ordered_signals(values: Iterable[str]) -> tuple[str, ...]:
    order = {
        MISSING_EVIDENCE: 0,
        LINEAGE_LOST: 1,
        BINDING_FAILED: 2,
        UNKNOWN_FAILURE: 3,
    }
    unique = _unique(values)
    return tuple(sorted(unique, key=lambda value: (order.get(value, 99), value)))


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "BINDING_FAILED",
    "FailureSignalAdapterResult",
    "LINEAGE_LOST",
    "MISSING_EVIDENCE",
    "MULTIPLE_FAILURE_SIGNALS",
    "NO_FAILURE",
    "UNKNOWN_FAILURE",
    "adapt_atom_verdict",
    "adapt_claim_verdict",
]
