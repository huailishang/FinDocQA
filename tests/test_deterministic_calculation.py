"""C3 deterministic calculation core tests (offline, zero API)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from calculation import (
    BoundVariable,
    BuiltinFormulaRegistry,
    DeterministicCalculationEngine,
    FormulaEvidence,
    FormulaEvidenceGate,
    FormulaGateStatus,
    FormulaSourceRef,
    LocalContextVariableBinder,
    MaterialFormulaExtractor,
    SafeFormulaCompiler,
    normalize_value,
)
from contracts import EvidenceCandidate


def _source(page: int = 18) -> FormulaSourceRef:
    return FormulaSourceRef(doc_id="insurance_demo", page_number=page, source="page_0018.md")


def _binding(name: str, value: str, unit: str = "") -> BoundVariable:
    return BoundVariable(name=name, value=Decimal(value), unit=unit, source_ref=_source())


def test_builtin_growth_rate_program_executes_with_decimal():
    engine = DeterministicCalculationEngine()
    result = engine.execute_builtin(
        "growth_rate",
        {"current": _binding("current", "120"), "previous": _binding("previous", "100")},
    )
    assert result.ok is True
    assert result.value == Decimal("0.2")
    assert result.display_value == "20%"


def test_builtin_percentage_point_change_is_not_growth_rate():
    engine = DeterministicCalculationEngine()
    result = engine.execute_builtin(
        "percentage_point_change",
        {
            "current_rate": _binding("current_rate", "0.032", "ratio"),
            "previous_rate": _binding("previous_rate", "0.027", "ratio"),
        },
    )
    assert result.value == Decimal("0.005")
    assert result.display_value == "0.5个百分点"


def test_builtin_difference_and_ratio():
    engine = DeterministicCalculationEngine()
    diff = engine.execute_builtin(
        "difference",
        {"left": _binding("left", "20"), "right": _binding("right", "15")},
    )
    ratio = engine.execute_builtin(
        "ratio",
        {"part": _binding("part", "30"), "whole": _binding("whole", "120")},
    )
    assert diff.value == Decimal("5")
    assert ratio.value == Decimal("0.25")
    assert ratio.display_value == "25%"


def test_builtin_division_by_zero_fails_closed():
    engine = DeterministicCalculationEngine()
    result = engine.execute_builtin(
        "growth_rate",
        {"current": _binding("current", "120"), "previous": _binding("previous", "0")},
    )
    assert result.ok is False
    assert result.error == "division_by_zero"


def test_builtin_formula_registry_detects_five_core_intents():
    registry = BuiltinFormulaRegistry()
    assert registry.detect("2024年营业收入同比增长多少？") == "growth_rate"
    assert registry.detect("A公司和B公司的净利润差额是多少？") == "difference"
    assert registry.detect("研发支出占营业收入的比例是多少？") == "ratio"
    assert registry.detect("不良率从2.7%升至3.2%，提高多少个百分点？") == "percentage_point_change"
    assert registry.detect("三家公司净利润从高到低排序") == "ranking_desc"


def test_normalize_value_handles_amount_and_percentage_units():
    assert normalize_value("2", "亿元") == Decimal("200000000")
    assert normalize_value("15", "万元") == Decimal("150000")
    assert normalize_value("80", "%") == Decimal("0.8")


def test_formula_gate_passes_complete_formula_with_lineage_and_bindings():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = (expense - deductible) * ratio",
        normalized_expression="(expense - deductible) * ratio",
        context_text="赔付金额 = (expense - deductible) * ratio",
        source_refs=(_source(),),
    )
    gate = FormulaEvidenceGate().evaluate(
        evidence,
        {
            "expense": _binding("expense", "20000", "元"),
            "deductible": _binding("deductible", "10000", "元"),
            "ratio": _binding("ratio", "0.8", "ratio"),
        },
    )
    assert gate.status is FormulaGateStatus.PASS
    assert gate.reasons == ()


def test_formula_gate_rejects_unbalanced_or_truncated_formula():
    gate = FormulaEvidenceGate()
    bad = FormulaEvidence(
        raw_formula="赔付金额 = (expense - deductible *",
        normalized_expression="(expense - deductible *",
        source_refs=(_source(),),
    )
    result = gate.evaluate(bad, {"expense": _binding("expense", "1"), "deductible": _binding("deductible", "1")})
    assert result.status is FormulaGateStatus.FAIL
    assert "unbalanced_delimiters" in result.reasons


def test_formula_gate_reviews_missing_variable_binding():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        source_refs=(_source(),),
    )
    result = FormulaEvidenceGate().evaluate(evidence, {"expense": _binding("expense", "100")})
    assert result.status is FormulaGateStatus.REVIEW
    assert "missing_variable_binding:ratio" in result.reasons


def test_formula_gate_reviews_text_constraint_not_encoded_in_expression():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        context_text="赔付金额 = expense * ratio。每次赔付最高不超过责任限额。",
        conditions=("每次赔付最高不超过责任限额",),
        source_refs=(_source(),),
    )
    result = FormulaEvidenceGate().evaluate(
        evidence,
        {"expense": _binding("expense", "100"), "ratio": _binding("ratio", "0.8")},
    )
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_not_compiled:min" in result.reasons


def test_formula_gate_allows_explicit_min_constraint():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense * ratio, limit)",
        normalized_expression="min(expense * ratio, limit)",
        context_text="赔付金额最高不超过责任限额。",
        conditions=("赔付金额最高不超过责任限额",),
        source_refs=(_source(),),
    )
    result = FormulaEvidenceGate().evaluate(
        evidence,
        {
            "expense": _binding("expense", "100"),
            "ratio": _binding("ratio", "0.8"),
            "limit": _binding("limit", "60"),
        },
    )
    assert result.status is FormulaGateStatus.PASS


def test_formula_gate_reviews_unresolved_table_reference():
    evidence = FormulaEvidence(
        raw_formula="给付金额 = basic_amount * ratio",
        normalized_expression="basic_amount * ratio",
        context_text="给付比例按下表确定。给付金额 = basic_amount * ratio",
        source_refs=(_source(),),
    )
    result = FormulaEvidenceGate().evaluate(
        evidence,
        {"basic_amount": _binding("basic_amount", "100000"), "ratio": _binding("ratio", "1.2")},
    )
    assert result.status is FormulaGateStatus.REVIEW
    assert "linked_table_missing" in result.reasons


def test_safe_formula_compiler_and_executor_support_min():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min((expense - deductible) * ratio, limit)",
        normalized_expression="min((expense - deductible) * ratio, limit)",
        source_refs=(_source(),),
    )
    bindings = {
        "expense": _binding("expense", "20000"),
        "deductible": _binding("deductible", "10000"),
        "ratio": _binding("ratio", "0.8"),
        "limit": _binding("limit", "6000"),
    }
    program = SafeFormulaCompiler().compile(evidence, bindings)
    result = DeterministicCalculationEngine().execute_program(program, bindings)
    assert result.ok is True
    assert result.value == Decimal("6000")
    assert any(step.op == "min" for step in program.steps)


def test_safe_formula_compiler_rejects_unknown_function():
    evidence = FormulaEvidence(
        raw_formula="x = dangerous(a)",
        normalized_expression="dangerous(a)",
        source_refs=(_source(),),
    )
    with pytest.raises(ValueError, match="function_not_allowed"):
        SafeFormulaCompiler().compile(evidence, {"a": _binding("a", "1")})


def test_local_context_variable_binder_preserves_units_and_lineage():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = (expense - deductible) * ratio",
        normalized_expression="(expense - deductible) * ratio",
        context_text="expense = 2万元\ndeductible = 1万元\nratio = 80%",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    assert bindings["expense"].value == Decimal("20000")
    assert bindings["deductible"].value == Decimal("10000")
    assert bindings["ratio"].value == Decimal("0.8")
    assert bindings["expense"].source_ref.page_number == 18


def test_material_formula_extractor_keeps_before_after_context_and_lineage():
    candidate = EvidenceCandidate(
        domain="insurance",
        doc_id="insurance_demo",
        source="page_0018.md",
        text="赔付金额 = (expense - deductible) * ratio",
        before_text="其中 deductible 为免赔额。",
        after_text="每次赔付最高不超过责任限额。",
        metadata={"page_number": 18},
    )
    formulas = MaterialFormulaExtractor().extract_from_candidate(candidate)
    assert len(formulas) == 1
    formula = formulas[0]
    assert formula.normalized_expression == "(expense - deductible) * ratio"
    assert "免赔额" in formula.context_text
    assert "责任限额" in formula.context_text
    assert formula.source_refs[0].doc_id == "insurance_demo"
    assert formula.source_refs[0].page_number == 18


def test_material_formula_engine_blocks_incomplete_formula_instead_of_calculating():
    candidate = EvidenceCandidate(
        domain="insurance",
        doc_id="insurance_demo",
        source="page_0018.md",
        text="赔付金额 = expense * ratio",
        after_text="每次赔付最高不超过责任限额。",
        metadata={"page_number": 18},
    )
    result = DeterministicCalculationEngine().execute_material_candidate(candidate)
    assert result.ok is False
    assert result.gate_status == FormulaGateStatus.REVIEW.value
    assert "constraint_not_compiled:min" in result.audit_reasons


def test_material_formula_engine_executes_complete_local_formula():
    candidate = EvidenceCandidate(
        domain="insurance",
        doc_id="insurance_demo",
        source="page_0018.md",
        text="赔付金额 = min((expense - deductible) * ratio, limit)\nexpense = 2万元\ndeductible = 1万元\nratio = 80%\nlimit = 6000元",
        metadata={"page_number": 18},
    )
    result = DeterministicCalculationEngine().execute_material_candidate(candidate)
    assert result.ok is True
    assert result.value == Decimal("6000")
    assert result.gate_status == FormulaGateStatus.PASS.value
    assert result.source_refs[0].page_number == 18


def test_registry_does_not_treat_plain_limit_question_as_ranking():
    registry = BuiltinFormulaRegistry()
    assert registry.detect("该产品最高赔付限额是多少？") is None


def test_safe_formula_compiler_rejects_attribute_access():
    evidence = FormulaEvidence(
        raw_formula="x = a.__class__",
        normalized_expression="a.__class__",
        source_refs=(_source(),),
    )
    with pytest.raises(ValueError, match="formula_node_not_allowed"):
        SafeFormulaCompiler().compile(evidence, {"a": _binding("a", "1")})


def test_executor_rejects_unbounded_power():
    evidence = FormulaEvidence(
        raw_formula="x = a ** 101",
        normalized_expression="a ** 101",
        source_refs=(_source(),),
    )
    bindings = {"a": _binding("a", "2")}
    program = SafeFormulaCompiler().compile(evidence, bindings)
    result = DeterministicCalculationEngine().execute_program(program, bindings)
    assert result.ok is False
    assert result.error == "power_exponent_out_of_range"


# ── C3 V1-R1 evaluator false-pass regressions ─────────────────────────


def test_wrong_min_constraint_target_must_not_pass():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense * ratio, deductible)",
        normalized_expression="min(expense * ratio, deductible)",
        context_text=(
            "expense = 100元\nratio = 80%\ndeductible = 10元\nlimit = 60元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


def test_arithmetic_wrapped_min_constraint_target_must_review():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense, limit * 2)",
        normalized_expression="min(expense, limit * 2)",
        context_text=(
            "expense = 100元\nlimit = 60元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


def test_wrong_max_constraint_target_must_not_pass():
    evidence = FormulaEvidence(
        raw_formula="保底金额 = max(base_amount, deductible)",
        normalized_expression="max(base_amount, deductible)",
        context_text=(
            "base_amount = 100元\ndeductible = 10元\nfloor = 120元\n"
            "保底金额不得低于最低限额 floor"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:max:floor" in result.reasons


def test_arithmetic_wrapped_max_constraint_target_must_review():
    evidence = FormulaEvidence(
        raw_formula="保底金额 = max(base, floor / 2)",
        normalized_expression="max(base, floor / 2)",
        context_text="base = 100元\nfloor = 120元\n保底金额不得低于最低限额 floor",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:max:floor" in result.reasons


def test_nested_min_constraint_target_must_review():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense, max(limit, other))",
        normalized_expression="min(expense, max(limit, other))",
        context_text=(
            "expense = 100元\nlimit = 60元\nother = 80元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


def test_direct_root_min_constraint_target_passes():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense, limit)",
        normalized_expression="min(expense, limit)",
        context_text="expense = 100元\nlimit = 60元\n每次赔付不得超过责任限额 limit",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.PASS


def test_direct_root_max_constraint_target_passes():
    evidence = FormulaEvidence(
        raw_formula="保底金额 = max(base, floor)",
        normalized_expression="max(base, floor)",
        context_text="base = 100元\nfloor = 120元\n保底金额不得低于最低限额 floor",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.PASS


def test_direct_root_min_with_wrong_target_must_review():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense, deductible)",
        normalized_expression="min(expense, deductible)",
        context_text=(
            "expense = 100元\ndeductible = 10元\nlimit = 60元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


# ── C3 V1-R2 executor self-check round 1 adversarial cases ──────────


def test_non_root_min_cannot_prove_final_output_upper_bound():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense, limit) + fee",
        normalized_expression="min(expense, limit) + fee",
        context_text=(
            "expense = 100元\nlimit = 60元\nfee = 5元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


def test_nested_min_under_governing_max_cannot_prove_upper_bound():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = max(min(expense, limit), other)",
        normalized_expression="max(min(expense, limit), other)",
        context_text=(
            "expense = 100元\nlimit = 60元\nother = 80元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


def test_constraint_target_in_non_governing_nested_branch_must_review():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(max(expense, limit), cap)",
        normalized_expression="min(max(expense, limit), cap)",
        context_text=(
            "expense = 100元\nlimit = 60元\ncap = 50元\n"
            "每次赔付不得超过责任限额 limit"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_not_bound:min:limit" in result.reasons


def test_duplicate_conflicting_local_variable_must_review():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        context_text="expense = 100元\nratio = 80%\nexpense = 1000元",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert "expense" not in bindings
    assert result.status is FormulaGateStatus.REVIEW
    assert "ambiguous_variable_binding:expense" in result.reasons


def test_duplicate_identical_local_variable_may_deduplicate():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        context_text="expense = 100元\nratio = 80%\nexpense = 100元",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert bindings["expense"].value == Decimal("100")
    assert result.status is FormulaGateStatus.PASS


def test_ranking_low_to_high_executes_ascending():
    registry = BuiltinFormulaRegistry()
    assert registry.detect("三家公司净利润从低到高排序") == "ranking_asc"
    result = DeterministicCalculationEngine().execute_builtin(
        "ranking_asc",
        {"A": _binding("A", "10"), "B": _binding("B", "30"), "C": _binding("C", "20")},
    )
    assert result.ok is True
    assert result.display_value == "A>C>B".replace(">", "<")


def test_ranking_high_to_low_executes_descending():
    registry = BuiltinFormulaRegistry()
    assert registry.detect("三家公司净利润从高到低排序") == "ranking_desc"
    result = DeterministicCalculationEngine().execute_builtin(
        "ranking_desc",
        {"A": _binding("A", "10"), "B": _binding("B", "30"), "C": _binding("C", "20")},
    )
    assert result.ok is True
    assert result.display_value == "B>C>A"


def test_bound_variable_without_lineage_must_review():
    evidence = FormulaEvidence(
        raw_formula="x = a + b",
        normalized_expression="a + b",
        source_refs=(_source(),),
    )
    result = FormulaEvidenceGate().evaluate(
        evidence,
        {
            "a": BoundVariable(name="a", value=Decimal("1"), source_ref=None),
            "b": _binding("b", "2"),
        },
    )
    assert result.status is FormulaGateStatus.REVIEW
    assert "variable_lineage_missing:a" in result.reasons


def test_blank_formula_source_ref_must_review():
    evidence = FormulaEvidence(
        raw_formula="x = a + b",
        normalized_expression="a + b",
        source_refs=(FormulaSourceRef(doc_id="", page_number=None, source=""),),
    )
    result = FormulaEvidenceGate().evaluate(
        evidence,
        {"a": _binding("a", "1"), "b": _binding("b", "2")},
    )
    assert result.status is FormulaGateStatus.REVIEW
    assert "source_lineage_invalid" in result.reasons


# ── Executor self-check round 1 adversarial cases ─────────────────────


def test_unresolved_multiple_limit_candidates_must_review():
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = min(expense, daily_limit)",
        normalized_expression="min(expense, daily_limit)",
        context_text=(
            "expense = 100元\ndaily_limit = 60元\nannual_limit = 1000元\n"
            "赔付金额不得超过责任限额"
        ),
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert result.status is FormulaGateStatus.REVIEW
    assert "constraint_target_ambiguous:min" in result.reasons


def test_duplicate_equivalent_amounts_across_units_may_deduplicate():
    evidence = FormulaEvidence(
        raw_formula="x = expense * ratio",
        normalized_expression="expense * ratio",
        context_text="expense = 1万元\nexpense = 10000元\nratio = 80%",
        source_refs=(_source(),),
    )
    bindings = LocalContextVariableBinder().bind(evidence)
    result = FormulaEvidenceGate().evaluate(evidence, bindings)
    assert bindings["expense"].value == Decimal("10000")
    assert result.status is FormulaGateStatus.PASS


def test_blank_bound_variable_source_ref_must_review():
    evidence = FormulaEvidence(
        raw_formula="x = a + b",
        normalized_expression="a + b",
        source_refs=(_source(),),
    )
    blank = FormulaSourceRef(doc_id="", page_number=None, source="")
    result = FormulaEvidenceGate().evaluate(
        evidence,
        {
            "a": BoundVariable(name="a", value=Decimal("1"), source_ref=blank),
            "b": _binding("b", "2"),
        },
    )
    assert result.status is FormulaGateStatus.REVIEW
    assert "variable_lineage_invalid:a" in result.reasons


def test_ranking_without_direction_fails_closed():
    registry = BuiltinFormulaRegistry()
    assert registry.detect("三家公司净利润排序") is None
    result = DeterministicCalculationEngine().execute_builtin(
        "ranking",
        {"A": _binding("A", "10"), "B": _binding("B", "30")},
    )
    assert result.ok is False
    assert result.error == "ranking_direction_required"
