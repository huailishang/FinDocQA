"""Generic fail-closed reliability gate shared by all evaluation modules."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from evaluation.contracts import (
    EvaluationResult,
    GateStatus,
    MetricKind,
    MetricResult,
    MetricSeverity,
    _jsonable,
)
from evaluation.profiles import ReliabilityProfile

_STATUS_RANK = {
    GateStatus.PASS: 0,
    GateStatus.REVIEW: 1,
    GateStatus.FAIL: 2,
}


@dataclass(frozen=True)
class ReliabilityGateDecision:
    """Explainable aggregate gate decision for one module reliability profile."""

    module_id: str
    status: GateStatus
    reasons: tuple[str, ...] = ()
    evaluated_cases: int = 0

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


class ReliabilityGate:
    """Evaluate module-agnostic metrics and critical invariants against a profile."""

    def evaluate(
        self,
        results: Sequence[EvaluationResult],
        profile: ReliabilityProfile,
    ) -> ReliabilityGateDecision:
        normalized = tuple(results)
        reasons: list[str] = []
        status = GateStatus.PASS

        if not normalized:
            return ReliabilityGateDecision(
                module_id=profile.module_id,
                status=GateStatus.FAIL,
                reasons=("evaluation_results_missing",),
                evaluated_cases=0,
            )

        invariant_names = set(profile.required_invariants)
        metric_names = set(profile.required_metrics)

        for result in normalized:
            if result.module_id != profile.module_id:
                status = GateStatus.FAIL
                reasons.append(
                    f"module_mismatch:{result.case_id}:{result.module_id}!={profile.module_id}"
                )
            if result.gate_status is GateStatus.FAIL:
                status = GateStatus.FAIL
                reasons.append(f"case_gate_failed:{result.case_id}")
            elif result.gate_status is GateStatus.REVIEW:
                status = self._max_status(status, GateStatus.REVIEW)
                reasons.append(f"case_gate_review:{result.case_id}")

            for invariant_name in profile.required_invariants:
                matches = self._matching(
                    result.metrics,
                    invariant_name,
                    kind=MetricKind.INVARIANT,
                )
                if invariant_name in result.violations:
                    status = GateStatus.FAIL
                    reasons.append(f"critical_invariant_failed:{invariant_name}")
                    reasons.append(
                        f"critical_invariant_failed:{result.case_id}:{invariant_name}"
                    )
                if not matches:
                    status = GateStatus.FAIL
                    reasons.append(f"required_invariant_missing:{invariant_name}")
                    reasons.append(
                        f"required_invariant_missing:{result.case_id}:{invariant_name}"
                    )
                    continue
                if any(metric.passed is not True for metric in matches):
                    status = GateStatus.FAIL
                    reasons.append(f"critical_invariant_failed:{invariant_name}")
                    reasons.append(
                        f"critical_invariant_failed:{result.case_id}:{invariant_name}"
                    )

            for metric_name in profile.required_metrics:
                matches = self._matching(
                    result.metrics,
                    metric_name,
                    kind=MetricKind.METRIC,
                )
                if not matches:
                    status = self._max_status(
                        status,
                        profile.policy_status("missing_required_metric", GateStatus.REVIEW),
                    )
                    reasons.append(f"required_metric_missing:{metric_name}")
                    reasons.append(f"required_metric_missing:{result.case_id}:{metric_name}")
                    continue
                for metric in matches:
                    if metric.passed is True:
                        continue
                    failure_status = (
                        GateStatus.FAIL
                        if metric.severity is MetricSeverity.CRITICAL
                        else profile.policy_status("metric_failure", GateStatus.REVIEW)
                    )
                    status = self._max_status(status, failure_status)
                    reasons.append(f"metric_failed:{metric_name}")
                    reasons.append(f"metric_failed:{result.case_id}:{metric_name}")

            # Additional critical checks remain fail-closed even when not named
            # as explicit profile requirements.
            for metric in result.metrics:
                is_required = (
                    metric.kind is MetricKind.INVARIANT
                    and metric.metric_name in invariant_names
                ) or (
                    metric.kind is MetricKind.METRIC
                    and metric.metric_name in metric_names
                )
                if is_required or metric.severity is not MetricSeverity.CRITICAL:
                    continue
                if metric.passed is not True:
                    status = GateStatus.FAIL
                    prefix = (
                        "critical_invariant_failed"
                        if metric.kind is MetricKind.INVARIANT
                        else "critical_metric_failed"
                    )
                    reasons.append(f"{prefix}:{metric.metric_name}")
                    reasons.append(f"{prefix}:{result.case_id}:{metric.metric_name}")

        return ReliabilityGateDecision(
            module_id=profile.module_id,
            status=status,
            reasons=self._dedupe(reasons),
            evaluated_cases=len(normalized),
        )

    @staticmethod
    def _matching(
        metrics: Iterable[MetricResult],
        metric_name: str,
        *,
        kind: MetricKind | None = None,
    ) -> tuple[MetricResult, ...]:
        return tuple(
            metric
            for metric in metrics
            if metric.metric_name == metric_name and (kind is None or metric.kind is kind)
        )

    @staticmethod
    def _max_status(left: GateStatus, right: GateStatus) -> GateStatus:
        return left if _STATUS_RANK[left] >= _STATUS_RANK[right] else right

    @staticmethod
    def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value))
