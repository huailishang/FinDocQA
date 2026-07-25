"""CalculationSolver deterministic helpers (no LLM call).

Covers task card §2.4: multi-formula extraction, value extraction, percentage
normalization, eval-code building, and offline Python execution. All helpers
are static and require no API key or network.
"""

from __future__ import annotations

from solvers.calculation import CalculationSolver


# ── formula extraction ──────────────────────────────────────────────────


def test_extract_formula_simple():
    assert CalculationSolver._extract_formula("公式：a + b - c") == "a + b - c"


def test_extract_formula_strips_markdown_and_delimiters():
    assert CalculationSolver._extract_formula("公式：`a * b`") == "a * b"
    assert CalculationSolver._extract_formula("公式：$a + b$") == "a + b"
    assert CalculationSolver._extract_formula("公式：**a + b**") == "a + b"


def test_extract_formula_returns_none_when_absent():
    assert CalculationSolver._extract_formula("没有公式行") is None


# ── multi-formula extraction ────────────────────────────────────────────


def test_extract_multi_formulas_two_or_more():
    text = "公式[产品A]：a + b\n公式[产品B]：c - d\n公式[合计]：产品A + 产品B"
    f = CalculationSolver._extract_multi_formulas(text)
    assert f == {"产品A": "a + b", "产品B": "c - d", "合计": "产品A + 产品B"}


def test_extract_multi_formulas_single_returns_empty():
    """A single labeled formula falls back to the single-formula path."""
    text = "公式[产品A]：a + b\n变量：\na = 1"
    assert CalculationSolver._extract_multi_formulas(text) == {}


# ── value extraction ────────────────────────────────────────────────────


def test_extract_values_basic():
    text = "公式：a + b\n变量：\na = 100\nb = 200\n数值说明：a=保费"
    v = CalculationSolver._extract_values(text)
    assert v == {"a": 100.0, "b": 200.0}


def test_extract_values_handles_thousands_separator():
    text = "变量：\na = 1,000\nb = 2,500"
    v = CalculationSolver._extract_values(text)
    assert v == {"a": 1000.0, "b": 2500.0}


# ── percentage + eval code + offline execution ──────────────────────────


def test_normalize_percent():
    assert CalculationSolver._normalize_percent("75%") == "0.75"
    assert CalculationSolver._normalize_percent("100%") == "1.0"
    assert CalculationSolver._normalize_percent("a + 75%") == "a + 0.75"


def test_build_eval_code_injects_values_and_formula():
    code = CalculationSolver._build_eval_code("a + b", {"a": 1, "b": 2})
    assert "a = 1" in code
    assert "b = 2" in code
    assert "result = a + b" in code
    assert "print(result)" in code


def test_run_python_simple_offline():
    """Offline subprocess execution of a trivial formula (no network/API)."""
    val, err = CalculationSolver._run_python("result = 2 + 3\nprint(result)")
    assert err is None
    assert val == 5.0


def test_run_python_reports_error_on_bad_code():
    val, err = CalculationSolver._run_python("result = 1 / 0\nprint(result)")
    assert val is None
    assert err is not None  # ZeroDivision captured as stderr
