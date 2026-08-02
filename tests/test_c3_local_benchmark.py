from decimal import Decimal

import pytest

from calculation import LocalBenchmarkCase, LocalBenchmarkEvaluator


def _case(label: str, **changes: bool) -> LocalBenchmarkCase:
    values = {
        "expected_formula_evidence": True, "observed_formula_evidence": True,
        "expected_formula_gate": True, "observed_formula_gate": True,
        "expected_semantic_binding": True, "observed_semantic_binding": True,
        "expected_unit_normalization": True, "observed_unit_normalization": True,
        "expected_execution": True, "observed_execution": True,
        "expected_lineage_complete": True, "observed_lineage_complete": True,
    }
    values.update(changes)
    return LocalBenchmarkCase(label=label, **values)


def test_local_benchmark_computes_all_accuracy_and_formula_gate_error_rates() -> None:
    result = LocalBenchmarkEvaluator.evaluate((
        _case("clean-pass"),
        _case(
            "false-pass",
            expected_formula_gate=False,
            observed_formula_gate=True,
            observed_formula_evidence=False,
            observed_semantic_binding=False,
        ),
        _case(
            "false-reject",
            observed_formula_gate=False,
            observed_unit_normalization=False,
            observed_execution=False,
            observed_lineage_complete=False,
        ),
    ))

    assert result.case_count == 3
    assert result.formula_evidence_accuracy.value == Decimal("2") / Decimal("3")
    assert result.formula_gate_accuracy.value == Decimal("1") / Decimal("3")
    assert result.formula_gate_false_pass_rate.value == Decimal("1")
    assert result.formula_gate_false_pass_rate.denominator == 1
    assert result.formula_gate_false_reject_rate.value == Decimal("1") / Decimal("2")
    assert result.formula_gate_false_reject_rate.denominator == 2
    assert result.semantic_binding_accuracy.value == Decimal("2") / Decimal("3")
    assert result.unit_normalization_accuracy.value == Decimal("2") / Decimal("3")
    assert result.execution_accuracy.value == Decimal("2") / Decimal("3")
    assert result.lineage_completeness_rate.value == Decimal("2") / Decimal("3")


def test_local_benchmark_rejects_empty_case_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        LocalBenchmarkEvaluator.evaluate(())


def test_formula_gate_zero_denominators_are_explicit_zero_rates() -> None:
    result = LocalBenchmarkEvaluator.evaluate((_case("only-expected-pass"),))

    assert result.formula_gate_false_pass_rate == result.formula_gate_false_pass_rate.__class__(0, 0, Decimal(0))
    assert result.formula_gate_false_reject_rate == result.formula_gate_false_reject_rate.__class__(0, 1, Decimal(0))
