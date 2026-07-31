"""Thin compatibility adapter from existing E1-E4 result objects to EvaluationResult."""
from __future__ import annotations

from typing import Any, Mapping

from evaluation.contracts import EvaluationResult, MetricResult, MetricSeverity


def adapt_layer_result(
    *,
    module_id: str,
    case_id: str,
    layer_result: Any,
    thresholds: Mapping[str, float | int],
    comparisons: Mapping[str, str] | None = None,
    severities: Mapping[str, MetricSeverity | str] | None = None,
) -> EvaluationResult:
    """Map selected numeric fields from an existing E1-E4 result into core metrics.

    The adapter intentionally reads only ``to_dict()`` and configured metric names,
    so existing layer result classes do not need to depend on the new core.
    """

    if not hasattr(layer_result, "to_dict") or not callable(layer_result.to_dict):
        raise TypeError("layer_result must expose to_dict()")

    payload = dict(layer_result.to_dict())
    comparisons = dict(comparisons or {})
    severities = dict(severities or {})
    metrics: list[MetricResult] = []

    for metric_name, threshold in thresholds.items():
        raw_value = payload.get(metric_name)
        if raw_value is not None and not isinstance(raw_value, (int, float)):
            raise TypeError(f"layer metric must be numeric or None: {metric_name}")
        severity = MetricSeverity(severities.get(metric_name, MetricSeverity.WARNING))
        metrics.append(
            MetricResult.threshold_metric(
                metric_name,
                value=raw_value,
                threshold=threshold,
                comparison=comparisons.get(metric_name, ">="),
                severity=severity,
                details={"source_result_type": type(layer_result).__name__},
            )
        )

    return EvaluationResult(
        case_id=case_id,
        module_id=module_id,
        metrics=tuple(metrics),
        diagnostics={
            "source_result_type": type(layer_result).__name__,
            "source_result": payload,
        },
    )
