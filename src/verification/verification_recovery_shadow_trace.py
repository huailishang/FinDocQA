"""P14D shadow integration: verifier -> failure -> arbitration -> recovery plan.

This module only wires already-approved P14B/P14A/P14C semantics.  It is
side-effect free: no retrieval, rebind, re-verification, recompute, provider
retry, answer mutation, or production execution is performed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from verification.atom_evidence_verifier import AtomVerdict
from verification.claim_atoms import ClaimAtom
from verification.composite_failure_arbitrator import arbitrate_failures
from verification.failure_taxonomy import FailureRecord
from verification.recovery_orchestrator import build_bounded_recovery_plan
from verification.recovery_policy import RecoveryAction
from verification.verifier_failure_adapter import FailureSignalAdapterResult, adapt_atom_verdict


@dataclass(frozen=True)
class VerificationRecoveryShadowTrace:
    trace_id: str
    verifier_verdict: str
    failure_signals: tuple[str, ...]
    all_observed_failures: tuple[FailureRecord, ...]
    primary_failure: FailureRecord | None
    secondary_failures: tuple[FailureRecord, ...]
    arbitration_reason: str
    recommended_action: str
    max_recovery_steps: int
    requires_evaluator: bool
    provider_retry_allowed: bool
    corrective_retrieval_allowed: bool
    execution_authorized: bool
    terminal_stop: bool
    stop_reason: str
    adapter_reason: str
    provider_calls: int = 0
    recovery_execution: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failure_signals"] = list(self.failure_signals)
        payload["all_observed_failures"] = [row.to_dict() for row in self.all_observed_failures]
        payload["primary_failure"] = self.primary_failure.to_dict() if self.primary_failure else None
        payload["secondary_failures"] = [row.to_dict() for row in self.secondary_failures]
        return payload


def _adapter_observations(adapter: FailureSignalAdapterResult) -> list[dict[str, Any]]:
    """Convert P14B signals into narrow P14A observations without adding causes."""
    rows: list[dict[str, Any]] = []
    for signal in adapter.failure_signals:
        rows.append({
            "failure_code": signal,
            "stage": "verifier_failure_adapter",
            "reason": adapter.adapter_reason,
            "evidence_refs": list(adapter.evidence_refs),
            "used_doc_ids": list(adapter.used_doc_ids),
        })
    return rows


def _arbitration_context(adapter: FailureSignalAdapterResult) -> dict[str, Any]:
    return {
        "evidence_available": adapter.evidence_available,
        "evidence_count": adapter.evidence_count,
        "evidence_refs": list(adapter.evidence_refs),
        "raw_evidence_refs": list(adapter.raw_evidence_refs),
        "used_doc_ids": list(adapter.used_doc_ids),
        "binding_auditable": adapter.binding_auditable,
    }


def build_shadow_trace_from_adapter(
    adapter: FailureSignalAdapterResult,
    *,
    additional_observations: Sequence[Mapping[str, Any] | FailureRecord] = (),
    trace_id: str = "",
) -> VerificationRecoveryShadowTrace:
    """Run the approved P14B -> P14A -> P14C chain in shadow mode.

    ``additional_observations`` carries non-verifier failures already observed by
    surrounding stages, such as provider, answer-contract, calculation, budget,
    or runtime-integrity failures.  They are classified/arbitrated by P14A and
    cannot authorize execution.
    """
    observations: list[Mapping[str, Any] | FailureRecord] = [*_adapter_observations(adapter), *additional_observations]

    # SUPPORT/REFUTE legitimately produce no P14B failure signal.  With no other
    # failure observation there is nothing to arbitrate or recover; preserve a
    # complete no-op trace instead of fabricating UNKNOWN_FAILURE.
    if not observations:
        return VerificationRecoveryShadowTrace(
            trace_id=trace_id,
            verifier_verdict=adapter.verdict,
            failure_signals=adapter.failure_signals,
            all_observed_failures=(),
            primary_failure=None,
            secondary_failures=(),
            arbitration_reason="conclusive verifier verdict produced no failure signal",
            recommended_action=RecoveryAction.NO_ACTION.value,
            max_recovery_steps=0,
            requires_evaluator=False,
            provider_retry_allowed=False,
            corrective_retrieval_allowed=False,
            execution_authorized=False,
            terminal_stop=False,
            stop_reason="",
            adapter_reason=adapter.adapter_reason,
            provider_calls=0,
            recovery_execution=0,
        )

    decision = arbitrate_failures(observations, context=_arbitration_context(adapter))
    plan = build_bounded_recovery_plan(decision)
    return VerificationRecoveryShadowTrace(
        trace_id=trace_id,
        verifier_verdict=adapter.verdict,
        failure_signals=adapter.failure_signals,
        all_observed_failures=decision.observed_failures,
        primary_failure=decision.primary_failure,
        secondary_failures=decision.secondary_failures,
        arbitration_reason=decision.arbitration_reason,
        recommended_action=plan.recommended_action.value,
        max_recovery_steps=plan.max_recovery_steps,
        requires_evaluator=plan.requires_evaluator,
        provider_retry_allowed=plan.provider_retry_allowed,
        corrective_retrieval_allowed=plan.corrective_retrieval_allowed,
        execution_authorized=plan.execution_authorized,
        terminal_stop=plan.terminal_stop,
        stop_reason=plan.stop_reason,
        adapter_reason=adapter.adapter_reason,
        provider_calls=0,
        recovery_execution=0,
    )


def build_atom_shadow_trace(
    atom: ClaimAtom,
    verdict: AtomVerdict,
    *,
    evidence_count: int | None = None,
    raw_evidence_refs: Sequence[str] = (),
    used_doc_ids: Sequence[str] = (),
    additional_observations: Sequence[Mapping[str, Any] | FailureRecord] = (),
    trace_id: str = "",
) -> VerificationRecoveryShadowTrace:
    """Public P14D atom entrypoint; first step is always the P14B adapter."""
    adapter = adapt_atom_verdict(
        atom,
        verdict,
        evidence_count=evidence_count,
        raw_evidence_refs=raw_evidence_refs,
        used_doc_ids=used_doc_ids,
    )
    return build_shadow_trace_from_adapter(
        adapter,
        additional_observations=additional_observations,
        trace_id=trace_id,
    )


__all__ = [
    "VerificationRecoveryShadowTrace",
    "build_shadow_trace_from_adapter",
    "build_atom_shadow_trace",
]
