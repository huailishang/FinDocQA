"""Evaluation and reliability contracts for FinDocQA."""

from evaluation.contracts import (
    EvaluationCase,
    EvaluationObservation,
    EvaluationResult,
    GateStatus,
    MetricKind,
    MetricResult,
    MetricSeverity,
)
from evaluation.gates import ReliabilityGate, ReliabilityGateDecision
from evaluation.profiles import ReliabilityProfile

_C3_PIPELINE_EXPORTS = {
    "C3PipelineRun",
    "C3_PIPELINE_MODULE_ID",
    "C3_PIPELINE_REQUIRED_INVARIANTS",
    "build_c3_pipeline_reliability_profile",
    "build_erroneous_allow_sentinel",
    "run_c3_pipeline_case",
    "run_c3_pipeline_rows",
}


def __getattr__(name: str):
    """Load the C3 pipeline evaluation harness lazily to avoid solver import cycles."""

    if name in _C3_PIPELINE_EXPORTS:
        from evaluation import c3_pipeline_profile

        return getattr(c3_pipeline_profile, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "C3PipelineRun",
    "C3_PIPELINE_MODULE_ID",
    "C3_PIPELINE_REQUIRED_INVARIANTS",
    "build_c3_pipeline_reliability_profile",
    "build_erroneous_allow_sentinel",
    "run_c3_pipeline_case",
    "run_c3_pipeline_rows",
    "EvaluationCase",
    "EvaluationObservation",
    "EvaluationResult",
    "GateStatus",
    "MetricKind",
    "MetricResult",
    "MetricSeverity",
    "ReliabilityGate",
    "ReliabilityGateDecision",
    "ReliabilityProfile",
]
