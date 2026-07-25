"""Deterministic composite-solver helpers (P7D-C offline foundation).

These helpers support a future composite solver that must verify each option
against computed numeric values (multi-select-plus-calculation) or against
values extracted from multiple documents (cross-document-plus-calculation).

They are pure, deterministic, and use only the Python standard library. They
do NOT call any LLM, retrieve any evidence, or spawn any subprocess. The
``safe_eval_numeric`` helper reuses the ``CalculationSolver`` philosophy of a
restricted ``ast`` whitelist so undefined symbols are rejected before eval.

See ``docs/p7d-workstream-c-implementation.md`` for the design notes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


# ── numeric parsing ───────────────────────────────────────────────────


# Chinese magnitude suffixes that may appear in financial text.
_MAGNITUDE_SUFFIX = {"万": 1e4, "亿": 1e8}


def parse_numeric_value(text: str) -> Optional[float]:
    """Parse a numeric value from a Chinese/English string.

    Handles:
    - plain numbers: ``"100"``, ``"200.5"``
    - percentages: ``"75%"`` -> ``0.75``
    - magnitude suffixes: ``"3万"`` -> ``30000``, ``"1.5亿"`` -> ``1.5e8``
    - thousands separators: ``"100,000"`` -> ``100000``
    - leading/trailing currency/unit markers: ``"100元"``, ``"人民币100"``

    Returns ``None`` if the string does not contain a recognizable number.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    # Strip common currency/unit prefixes and suffixes.
    s = re.sub(r"^(人民币|￥|¥|\$|RMB)\s*", "", s)
    s = re.sub(r"\s*(元|块钱|人民币)$", "", s)
    # Remove thousands separators (both ASCII and full-width).
    s = s.replace(",", "").replace("，", "")
    # Percentage.
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    # Magnitude suffix (万/亿). Only one suffix is expected at the end.
    for suffix, mult in _MAGNITUDE_SUFFIX.items():
        if s.endswith(suffix):
            base = s[: -len(suffix)].strip()
            try:
                return float(base) * mult
            except ValueError:
                return None
    # Plain number.
    try:
        return float(s)
    except ValueError:
        return None


# ── restricted evaluation ─────────────────────────────────────────────


# Builtins permitted inside composite option expressions. A safe subset of
# Python's builtins; arbitrary builtins are intentionally not whitelisted.
_ALLOWED_BUILTINS = {"max", "min", "abs", "sum", "round", "pow"}


def safe_eval_numeric(expr: str, variables: Dict[str, float]) -> tuple[Optional[float], Optional[str]]:
    """Evaluate a numeric expression against a variable mapping.

    Returns ``(value, None)`` on success or ``(None, error_message)`` on
    failure. The expression may only reference names in ``variables`` or the
    allowed builtin set. Undefined symbols produce a clear
    ``"undefined symbol(s): X"`` error instead of a ``NameError``.

    This mirrors ``CalculationSolver._prepare_formula`` so a composite solver
    can reuse the same deterministic rejection semantics without depending
    on the live solver class.
    """
    if not expr or not expr.strip():
        return None, "empty expression"
    # Normalize percentage literals: 75% -> 0.75
    normalized = re.sub(r"(\d+\.?\d*)\s*%", lambda m: str(float(m.group(1)) / 100), expr)
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        return None, f"invalid syntax: {exc.msg}"

    allowed_binary = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
    allowed_unary = (ast.UAdd, ast.USub)
    allowed_compare = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
    defined = set(variables)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                return None, "non-numeric constant"
            continue
        if isinstance(node, ast.Name):
            if node.id not in defined and node.id not in _ALLOWED_BUILTINS:
                return None, f"undefined symbol(s): {node.id}"
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, allowed_binary):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, allowed_unary):
            continue
        if isinstance(node, ast.Compare):
            # Allow numeric comparisons (==, !=, <, <=, >, >=) so option
            # conditions like "cv_甲 > 10000" can be evaluated deterministically.
            if any(not isinstance(op, allowed_compare) for op in node.ops):
                return None, "unsafe comparison operator"
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_BUILTINS:
                return None, "function call not allowed"
            if node.keywords:
                return None, "keyword arguments not allowed"
            continue
        if isinstance(node, allowed_binary) or isinstance(node, allowed_unary):
            continue
        if isinstance(node, allowed_compare):
            continue
        return None, f"unsafe node: {type(node).__name__}"

    try:
        value = eval(compile(tree, "<composite>", "eval"), {"__builtins__": {}}, dict(variables))
    except Exception as exc:
        return None, f"eval error: {type(exc).__name__}"
    # Comparison results are bools; allow them so option conditions work.
    if isinstance(value, bool):
        return float(value), None
    if not isinstance(value, (int, float)):
        return None, "non-numeric result"
    return float(value), None


# ── per-option verification (multi-select + calculation) ──────────────


@dataclass(frozen=True)
class OptionCheck:
    """A single option's computation requirement.

    Attributes:
        option_key: the option letter, e.g. ``"A"``.
        label: a human-readable label, e.g. ``"甲"``.
        condition: a Python expression, e.g. ``"cv_甲 > 10000"``.
        variables: the variable mapping used to evaluate ``condition``.
    """

    option_key: str
    label: str
    condition: str
    variables: Dict[str, float]


@dataclass(frozen=True)
class OptionCheckResult:
    """The deterministic result of checking one option against computed values.

    Attributes:
        option_key: the option letter checked.
        label: the human-readable label.
        condition: the expression that was evaluated.
        supported: True iff the condition evaluated to a truthy value with no
            error and no undefined symbol. A falsy value or any error means
            the option is NOT supported.
        error: None on success, otherwise the error message.
        computed_value: the numeric result of the condition (None on error).
        formula_required: True (always, for parity with the task card schema).
        variables_required: the variable names referenced by the condition.
        computed_value_available: True iff the condition produced a value.
        option_matched: True iff ``supported`` is True.
        unsupported_option: True iff ``supported`` is False.
    """

    option_key: str
    label: str
    condition: str
    supported: bool
    error: Optional[str]
    computed_value: Optional[float]
    formula_required: bool
    variables_required: List[str]
    computed_value_available: bool
    option_matched: bool
    unsupported_option: bool


def _extract_referenced_names(expr: str) -> List[str]:
    """Return the sorted list of variable names referenced in ``expr``."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return []
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in _ALLOWED_BUILTINS}
    return sorted(names)


def check_option_against_values(check: OptionCheck) -> OptionCheckResult:
    """Evaluate one option's condition deterministically.

    ``supported`` is True if and only if:
    - the expression evaluates without error,
    - no undefined symbol is referenced,
    - the result is truthy.

    Any error, undefined symbol, or falsy result means the option is NOT
    supported. This is the per-option analogue of
    ``CalculationSolver``'s extract -> Python eval -> match path, applied to
    each option independently rather than to the whole question.
    """
    referenced = _extract_referenced_names(check.condition)
    value, err = safe_eval_numeric(check.condition, check.variables)
    if err is not None:
        return OptionCheckResult(
            option_key=check.option_key,
            label=check.label,
            condition=check.condition,
            supported=False,
            error=err,
            computed_value=None,
            formula_required=True,
            variables_required=referenced,
            computed_value_available=False,
            option_matched=False,
            unsupported_option=True,
        )
    supported = bool(value) and value > 0
    return OptionCheckResult(
        option_key=check.option_key,
        label=check.label,
        condition=check.condition,
        supported=supported,
        error=None,
        computed_value=value,
        formula_required=True,
        variables_required=referenced,
        computed_value_available=True,
        option_matched=supported,
        unsupported_option=not supported,
    )


# ── cross-document support (cross-doc + calculation) ──────────────────


@dataclass(frozen=True)
class DocumentValue:
    """A numeric value extracted from one document.

    Attributes:
        doc_id: the source document id.
        label: a human-readable label for the value (e.g. product name).
        value: the numeric value.
    """

    doc_id: str
    label: str
    value: float


@dataclass(frozen=True)
class CrossDocSupportResult:
    """Deterministic cross-document support metadata.

    Attributes:
        required_doc_ids: the doc ids the question references.
        present_doc_ids: doc ids for which at least one value was extracted.
        missing_doc_ids: required doc ids with no extracted value.
        values_by_doc: mapping doc_id -> list of DocumentValue.
        used_values_from_multiple_docs: True iff values came from >= 2 docs.
        degraded: True iff any required doc is missing.
        computation_complete: True iff all required docs have values.
    """

    required_doc_ids: List[str]
    present_doc_ids: List[str]
    missing_doc_ids: List[str]
    values_by_doc: Dict[str, List[DocumentValue]]
    used_values_from_multiple_docs: bool
    degraded: bool
    computation_complete: bool


def build_cross_doc_support(
    required_doc_ids: Sequence[str],
    values: Sequence[DocumentValue],
) -> CrossDocSupportResult:
    """Build cross-document support metadata from extracted values.

    This is a pure function: it only groups the provided values by doc_id and
    compares against the required set. It does not call any LLM or retrieve
    any evidence.

    ``degraded`` is True when a required document has no extracted value —
    the composite solver cannot complete a cross-document comparison without
    all documents present.
    """
    required = sorted(set(str(d) for d in required_doc_ids))
    by_doc: Dict[str, List[DocumentValue]] = {}
    for v in values:
        by_doc.setdefault(v.doc_id, []).append(v)
    present = sorted(by_doc.keys())
    missing = [d for d in required if d not in by_doc]
    return CrossDocSupportResult(
        required_doc_ids=required,
        present_doc_ids=present,
        missing_doc_ids=missing,
        values_by_doc=by_doc,
        used_values_from_multiple_docs=len(by_doc) >= 2,
        degraded=bool(missing),
        computation_complete=not missing,
    )
