"""Offline metrics for caller-labelled deterministic calculation cases.

This module deliberately measures supplied labels only.  It does not inspect a
question, invoke a model, or change calculation routing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class LocalBenchmarkCase:
    """One labelled local case with expected and observed boolean outcomes."""

    label: str
    expected_formula_evidence: bool
    observed_formula_evidence: bool
    expected_formula_gate: bool
    observed_formula_gate: bool
    expected_semantic_binding: bool
    observed_semantic_binding: bool
    expected_unit_normalization: bool
    observed_unit_normalization: bool
    expected_execution: bool
    observed_execution: bool
    expected_lineage_complete: bool
    observed_lineage_complete: bool


@dataclass(frozen=True)
class BenchmarkRate:
    """An auditable fraction; ``value`` is zero when ``denominator`` is zero."""

    numerator: int
    denominator: int
    value: Decimal


@dataclass(frozen=True)
class LocalBenchmarkResult:
    """All required C3-E accuracy and Formula Gate error-rate measurements."""

    case_count: int
    formula_evidence_accuracy: BenchmarkRate
    formula_gate_accuracy: BenchmarkRate
    formula_gate_false_pass_rate: BenchmarkRate
    formula_gate_false_reject_rate: BenchmarkRate
    semantic_binding_accuracy: BenchmarkRate
    unit_normalization_accuracy: BenchmarkRate
    execution_accuracy: BenchmarkRate
    lineage_completeness_rate: BenchmarkRate


class LocalBenchmarkEvaluator:
    """Compute deterministic metrics from non-empty caller-supplied labels."""

    @staticmethod
    def evaluate(cases: Sequence[LocalBenchmarkCase]) -> LocalBenchmarkResult:
        if not cases:
            raise ValueError("local benchmark requires at least one labelled case")

        total = len(cases)
        accuracy = lambda expected, observed: _rate(
            sum(expected(case) == observed(case) for case in cases), total
        )
        expected_failures = [case for case in cases if not case.expected_formula_gate]
        expected_passes = [case for case in cases if case.expected_formula_gate]

        return LocalBenchmarkResult(
            case_count=total,
            formula_evidence_accuracy=accuracy(
                lambda case: case.expected_formula_evidence,
                lambda case: case.observed_formula_evidence,
            ),
            formula_gate_accuracy=accuracy(
                lambda case: case.expected_formula_gate,
                lambda case: case.observed_formula_gate,
            ),
            formula_gate_false_pass_rate=_rate(
                sum(case.observed_formula_gate for case in expected_failures),
                len(expected_failures),
            ),
            formula_gate_false_reject_rate=_rate(
                sum(not case.observed_formula_gate for case in expected_passes),
                len(expected_passes),
            ),
            semantic_binding_accuracy=accuracy(
                lambda case: case.expected_semantic_binding,
                lambda case: case.observed_semantic_binding,
            ),
            unit_normalization_accuracy=accuracy(
                lambda case: case.expected_unit_normalization,
                lambda case: case.observed_unit_normalization,
            ),
            execution_accuracy=accuracy(
                lambda case: case.expected_execution,
                lambda case: case.observed_execution,
            ),
            lineage_completeness_rate=accuracy(
                lambda case: case.expected_lineage_complete,
                lambda case: case.observed_lineage_complete,
            ),
        )


def _rate(numerator: int, denominator: int) -> BenchmarkRate:
    value = Decimal(0) if denominator == 0 else Decimal(numerator) / Decimal(denominator)
    return BenchmarkRate(numerator=numerator, denominator=denominator, value=value)
