"""Offline interpretation helpers for controlled D/H API smoke guardrails."""
from __future__ import annotations

from typing import Any, Mapping


def interpret_dh_smoke_guardrail_failure(
    *,
    run_summary: Mapping[str, Any],
    post_run_integrity_summary: Mapping[str, Any],
    circuit_breaker: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify a failed D/H smoke by guardrail failure class.

    This is deliberately answer-agnostic. It reasons about runtime controls:
    fallback use, per-qid provider call budget, and circuit-breaker state.
    """
    classes: list[str] = []
    if bool(run_summary.get("fallback_occurred")):
        classes.append("fallback_occurred_under_controlled_smoke")
    if post_run_integrity_summary.get("per_qid_provider_call_budget_ok") is False:
        classes.append("per_qid_provider_call_budget_exceeded")
    if bool(circuit_breaker.get("triggered")):
        classes.append("circuit_breaker_triggered")
    if any(str(case.get("error") or "") for case in run_summary.get("case_results", []) or []):
        classes.append("main_path_runtime_error")
    return {
        "guardrail_failure": bool(classes),
        "failure_classes": sorted(dict.fromkeys(classes)),
        "attempted_qids": list(run_summary.get("attempted_qids", []) or []),
        "unrun_qids": list(run_summary.get("unrun_qids", []) or []),
        "answer_agnostic": True,
    }
