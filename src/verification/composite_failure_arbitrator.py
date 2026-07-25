"""Composite failure arbitration for BB-P0-14A.

The arbitrator preserves every observed failure and selects one primary failure
using safety and causal precedence.  It is shadow-only: no retrieval, rebind,
recompute, provider retry, answer mutation, or production recovery is executed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from verification.failure_taxonomy import FailureClass, FailureRecord, observe_failure_records


_TERMINAL_SAFETY_ORDER: tuple[FailureClass, ...] = (
    FailureClass.RUNTIME_INTEGRITY_FAILED,
    FailureClass.BUDGET_BLOCKED,
    FailureClass.PROVIDER_ERROR,
)

# Causal order is intentionally about upstream/root-cause position, not enum or
# string ordering.  ANSWER_CONTRACT_FAILED is a downstream symptom and therefore
# ranks below evidence/lineage/binding/output failures.
_CAUSAL_ORDER: tuple[FailureClass, ...] = (
    FailureClass.LINEAGE_LOST,
    FailureClass.CALCULATION_BINDING_FAILED,
    FailureClass.BINDING_FAILED,
    FailureClass.EMPTY_VISIBLE_OUTPUT,
    FailureClass.MODEL_OUTPUT_INVALID,
    FailureClass.MISSING_EVIDENCE,
    FailureClass.ANSWER_CONTRACT_FAILED,
    FailureClass.UNKNOWN_FAILURE,
)


@dataclass(frozen=True)
class CompositeFailureDecision:
    observed_failures: tuple[FailureRecord, ...]
    primary_failure: FailureRecord
    secondary_failures: tuple[FailureRecord, ...]
    arbitration_reason: str
    terminal_stop: bool
    evaluator_escalation_required: bool
    recovery_execution_authorized: bool = False
    provider_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_failures"] = [item.to_dict() for item in self.observed_failures]
        payload["primary_failure"] = self.primary_failure.to_dict()
        payload["secondary_failures"] = [item.to_dict() for item in self.secondary_failures]
        return payload


def _dedupe_failures(records: Iterable[FailureRecord]) -> tuple[FailureRecord, ...]:
    """Deduplicate identical classes while retaining the strongest audit record."""
    by_class: dict[FailureClass, FailureRecord] = {}
    for record in records:
        existing = by_class.get(record.failure_class)
        if existing is None:
            by_class[record.failure_class] = record
            continue
        # Prefer the record carrying more concrete evidence and matched signals.
        current_score = len(record.evidence_refs) + len(record.matched_signals)
        previous_score = len(existing.evidence_refs) + len(existing.matched_signals)
        if current_score > previous_score:
            by_class[record.failure_class] = record
    return tuple(by_class.values())


def _normalize_observations(
    observations: Sequence[FailureRecord | Mapping[str, Any]],
) -> tuple[FailureRecord, ...]:
    records: list[FailureRecord] = []
    for observation in observations:
        if isinstance(observation, FailureRecord):
            records.append(observation)
        elif isinstance(observation, Mapping):
            # A structured observation may legitimately contain several failure
            # signals.  Preserve all detected classes instead of using P12's
            # single-class compatibility classifier.
            records.extend(observe_failure_records(observation))
        else:
            raise TypeError(f"unsupported failure observation: {type(observation)!r}")
    return _dedupe_failures(records)


def _ranked_first(classes: set[FailureClass], order: Sequence[FailureClass]) -> FailureClass | None:
    return next((failure_class for failure_class in order if failure_class in classes), None)


def _missing_vs_binding_primary(
    failures: Mapping[FailureClass, FailureRecord],
    context: Mapping[str, Any],
) -> tuple[FailureClass | None, str]:
    """Resolve MISSING_EVIDENCE + BINDING_FAILED only from structured facts."""
    has_missing = FailureClass.MISSING_EVIDENCE in failures
    has_binding = FailureClass.BINDING_FAILED in failures
    if not (has_missing and has_binding):
        return None, ""

    evidence_available = context.get("evidence_available")
    evidence_count_raw = context.get("evidence_count")
    evidence_count: int | None = None
    try:
        if evidence_count_raw is not None:
            evidence_count = int(evidence_count_raw)
    except (TypeError, ValueError):
        evidence_count = None

    raw_refs = tuple(str(value) for value in (context.get("raw_evidence_refs") or context.get("evidence_refs") or []) if str(value))
    canonical_refs = tuple(str(value) for value in (context.get("used_doc_ids") or []) if str(value))
    binding_auditable = context.get("binding_auditable")

    if evidence_available is False or evidence_count == 0:
        return (
            FailureClass.MISSING_EVIDENCE,
            "structured evidence state proves no decisive evidence is available; missing evidence precedes binding",
        )

    if evidence_available is True or (evidence_count is not None and evidence_count > 0) or raw_refs or canonical_refs:
        if binding_auditable is False or raw_refs or canonical_refs:
            return (
                FailureClass.BINDING_FAILED,
                "structured evidence/ref state proves evidence exists but decisive binding is not closed",
            )

    return (
        FailureClass.UNKNOWN_FAILURE,
        "missing-evidence and binding signals conflict without enough structured evidence state to identify the first failure",
    )


def arbitrate_failures(
    observations: Sequence[FailureRecord | Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
) -> CompositeFailureDecision:
    """Choose primary/secondary failures without losing composite evidence.

    ``context`` is restricted to structured arbitration facts such as
    ``evidence_available``, ``evidence_count``, refs, and binding audit state.
    It is never used to infer a business answer.
    """
    observed = _normalize_observations(observations)
    if not observed:
        observed = observe_failure_records({"reason": "no structured failure observation supplied"})

    by_class = {record.failure_class: record for record in observed}
    classes = set(by_class)

    terminal_primary = _ranked_first(classes, _TERMINAL_SAFETY_ORDER)
    if terminal_primary is not None:
        primary_class = terminal_primary
        reason = (
            f"terminal safety precedence selected {primary_class.value}; all other observed failures are retained as secondary"
        )
        terminal_stop = True
        evaluator_escalation = primary_class is FailureClass.RUNTIME_INTEGRITY_FAILED
    else:
        special_primary, special_reason = _missing_vs_binding_primary(by_class, context or {})
        if special_primary is not None:
            primary_class = special_primary
            reason = special_reason
            terminal_stop = primary_class is FailureClass.UNKNOWN_FAILURE
            evaluator_escalation = primary_class is FailureClass.UNKNOWN_FAILURE
            if primary_class is FailureClass.UNKNOWN_FAILURE and primary_class not in by_class:
                unknown = observe_failure_records({"reason": special_reason})[0]
                observed = _dedupe_failures((*observed, unknown))
                by_class = {record.failure_class: record for record in observed}
        else:
            primary_class = _ranked_first(classes, _CAUSAL_ORDER) or FailureClass.UNKNOWN_FAILURE
            if primary_class not in by_class:
                unknown = observe_failure_records({"reason": "no deterministic composite primary could be selected"})[0]
                observed = _dedupe_failures((*observed, unknown))
                by_class = {record.failure_class: record for record in observed}
            reason = (
                f"causal precedence selected {primary_class.value}; downstream symptoms remain secondary"
            )
            terminal_stop = primary_class is FailureClass.UNKNOWN_FAILURE
            evaluator_escalation = primary_class is FailureClass.UNKNOWN_FAILURE

    primary = by_class[primary_class]
    secondaries = tuple(record for record in observed if record.failure_class is not primary_class)
    return CompositeFailureDecision(
        observed_failures=observed,
        primary_failure=primary,
        secondary_failures=secondaries,
        arbitration_reason=reason,
        terminal_stop=terminal_stop,
        evaluator_escalation_required=evaluator_escalation,
        recovery_execution_authorized=False,
        provider_calls=0,
    )


__all__ = [
    "CompositeFailureDecision",
    "arbitrate_failures",
]
