"""Compile a material formula into a small whitelisted FormulaProgram."""
from __future__ import annotations

import ast
import re
from decimal import Decimal
from typing import Mapping

from calculation.contracts import BoundVariable, FormulaEvidence, FormulaProgram, FormulaStep


_PERCENT_LITERAL_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*[%％]")


def normalize_expression(expression: str) -> str:
    """Normalize common formula glyphs without changing business semantics."""
    value = str(expression or "").strip()
    value = value.replace("（", "(").replace("）", ")")
    value = value.replace("×", "*").replace("÷", "/").replace("·", "*")
    value = value.replace("^", "**")
    value = value.replace("MAX", "max").replace("MIN", "min").replace("ABS", "abs")
    value = _PERCENT_LITERAL_RE.sub(lambda match: str(Decimal(match.group(1)) / Decimal("100")), value)
    return value.strip().rstrip("。；;，,")


class SafeFormulaCompiler:
    """AST compiler that permits arithmetic plus a tiny function whitelist.

    It produces a FormulaProgram; it never executes source code and never uses
    ``eval``/``exec``/subprocess.
    """

    _BINARY_OPS = {
        ast.Add: "add",
        ast.Sub: "subtract",
        ast.Mult: "multiply",
        ast.Div: "divide",
        ast.Pow: "power",
    }
    _ALLOWED_FUNCTIONS = {"min", "max", "abs"}

    @classmethod
    def _parse(cls, expression: str) -> ast.Expression:
        normalized = normalize_expression(expression)
        try:
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"invalid_formula_syntax:{exc.msg}") from exc
        return tree

    @classmethod
    def referenced_symbols(cls, expression: str) -> tuple[str, ...]:
        tree = cls._parse(expression)
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id not in cls._ALLOWED_FUNCTIONS
        }
        return tuple(sorted(names))

    def compile(
        self,
        evidence: FormulaEvidence,
        bindings: Mapping[str, BoundVariable],
    ) -> FormulaProgram:
        tree = self._parse(evidence.normalized_expression)
        steps: list[FormulaStep] = []
        step_counter = 0

        def next_ref() -> str:
            nonlocal step_counter
            step_counter += 1
            return f"#{step_counter}"

        def compile_node(node: ast.AST) -> str:
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    raise ValueError("non_numeric_constant")
                return f"const:{Decimal(str(node.value))}"
            if isinstance(node, ast.Name):
                if node.id not in bindings:
                    raise ValueError(f"missing_variable_binding:{node.id}")
                return node.id
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand = compile_node(node.operand)
                if isinstance(node.op, ast.UAdd):
                    return operand
                output = next_ref()
                steps.append(FormulaStep(output, "multiply", ("const:-1", operand)))
                return output
            if isinstance(node, ast.BinOp):
                op = self._BINARY_OPS.get(type(node.op))
                if op is None:
                    raise ValueError(f"operator_not_allowed:{type(node.op).__name__}")
                left = compile_node(node.left)
                right = compile_node(node.right)
                output = next_ref()
                steps.append(FormulaStep(output, op, (left, right)))
                return output
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise ValueError("function_not_allowed")
                function = node.func.id
                if function not in self._ALLOWED_FUNCTIONS:
                    raise ValueError(f"function_not_allowed:{function}")
                if node.keywords:
                    raise ValueError("function_keywords_not_allowed")
                args = tuple(compile_node(arg) for arg in node.args)
                if function in {"min", "max"} and len(args) < 2:
                    raise ValueError(f"function_arity_invalid:{function}")
                if function == "abs" and len(args) != 1:
                    raise ValueError("function_arity_invalid:abs")
                output = next_ref()
                steps.append(FormulaStep(output, function, args))
                return output
            raise ValueError(f"formula_node_not_allowed:{type(node).__name__}")

        output_ref = compile_node(tree.body)
        if output_ref.startswith("const:") or output_ref in bindings:
            output = next_ref()
            steps.append(FormulaStep(output, "identity", (output_ref,)))
            output_ref = output

        return FormulaProgram(
            formula_id=str(evidence.metadata.get("formula_id") or "material_formula"),
            steps=tuple(steps),
            output_ref=output_ref,
            output_semantics=str(evidence.metadata.get("output_semantics") or "number"),
            source_type="material_formula",
            source_refs=tuple(evidence.source_refs),
            metadata={"raw_formula": evidence.raw_formula, "normalized_expression": normalize_expression(evidence.normalized_expression)},
        )
