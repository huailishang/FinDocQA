"""Deterministic FormulaProgram executor for C3."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping

from calculation.compiler import SafeFormulaCompiler
from calculation.contracts import (
    BoundVariable,
    CalculationExecutionResult,
    FormulaGateStatus,
    FormulaProgram,
)
from calculation.material import FormulaEvidenceGate, LocalContextVariableBinder, MaterialFormulaExtractor
from calculation.registry import BuiltinFormulaRegistry
from contracts import EvidenceCandidate


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _percentage_text(value: Decimal) -> str:
    return f"{_decimal_text(value * Decimal('100'))}%"


class DeterministicCalculationEngine:
    """Execute only explicit FormulaProgram operations with Decimal arithmetic."""

    _NUMERIC_OPS = {"identity", "add", "subtract", "multiply", "divide", "min", "max", "abs", "power"}

    def __init__(self) -> None:
        self.registry = BuiltinFormulaRegistry()
        self.extractor = MaterialFormulaExtractor()
        self.binder = LocalContextVariableBinder()
        self.gate = FormulaEvidenceGate()
        self.compiler = SafeFormulaCompiler()

    @staticmethod
    def _resolve(
        ref: str,
        bindings: Mapping[str, BoundVariable],
        computed: Mapping[str, Any],
    ) -> Any:
        if ref.startswith("const:"):
            try:
                return Decimal(ref.split(":", 1)[1])
            except InvalidOperation as exc:
                raise ValueError(f"invalid_constant:{ref}") from exc
        if ref in computed:
            return computed[ref]
        if ref in bindings:
            return bindings[ref].value
        raise ValueError(f"unresolved_operand:{ref}")

    @staticmethod
    def _display(value: Any, semantics: str) -> str:
        if semantics in {"ranking_asc", "ranking_desc"}:
            if not isinstance(value, tuple):
                return str(value)
            separator = "<" if semantics == "ranking_asc" else ">"
            return separator.join(name for name, _numeric in value)
        if not isinstance(value, Decimal):
            return str(value)
        if semantics == "ratio":
            return _percentage_text(value)
        if semantics == "percentage_point":
            return f"{_decimal_text(value * Decimal('100'))}个百分点"
        return _decimal_text(value)

    def execute_program(
        self,
        program: FormulaProgram,
        bindings: Mapping[str, BoundVariable],
        *,
        gate_status: str = "",
        audit_reasons: tuple[str, ...] = (),
    ) -> CalculationExecutionResult:
        computed: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        try:
            with localcontext() as context:
                context.prec = 34
                for step in program.steps:
                    args = tuple(self._resolve(ref, bindings, computed) for ref in step.args)
                    if step.op == "identity":
                        value = args[0]
                    elif step.op == "add":
                        value = args[0] + args[1]
                    elif step.op == "subtract":
                        value = args[0] - args[1]
                    elif step.op == "multiply":
                        value = args[0] * args[1]
                    elif step.op == "divide":
                        if args[1] == 0:
                            return CalculationExecutionResult(
                                ok=False,
                                error="division_by_zero",
                                trace=tuple(trace),
                                formula_program=program,
                                gate_status=gate_status,
                                audit_reasons=audit_reasons,
                                source_refs=tuple(program.source_refs),
                            )
                        value = args[0] / args[1]
                    elif step.op == "min":
                        value = min(args)
                    elif step.op == "max":
                        value = max(args)
                    elif step.op == "abs":
                        value = abs(args[0])
                    elif step.op == "power":
                        exponent = args[1]
                        if not isinstance(exponent, Decimal) or exponent != exponent.to_integral_value():
                            raise ValueError("non_integer_power_not_supported")
                        if abs(exponent) > Decimal("100"):
                            raise ValueError("power_exponent_out_of_range")
                        value = args[0] ** int(exponent)
                    elif step.op == "sort_desc":
                        pairs = [(name, self._resolve(name, bindings, computed)) for name in step.args]
                        value = tuple(sorted(pairs, key=lambda item: item[1], reverse=True))
                    elif step.op == "sort_asc":
                        pairs = [(name, self._resolve(name, bindings, computed)) for name in step.args]
                        value = tuple(sorted(pairs, key=lambda item: item[1]))
                    else:
                        raise ValueError(f"operation_not_allowed:{step.op}")
                    computed[step.output] = value
                    trace.append({
                        "step": step.output,
                        "op": step.op,
                        "args": list(step.args),
                        "resolved_args": [str(item) for item in args],
                        "result": str(value),
                    })
        except (ArithmeticError, ValueError) as exc:
            return CalculationExecutionResult(
                ok=False,
                error=str(exc),
                trace=tuple(trace),
                formula_program=program,
                gate_status=gate_status,
                audit_reasons=audit_reasons,
                source_refs=tuple(program.source_refs),
            )

        value = computed.get(program.output_ref)
        return CalculationExecutionResult(
            ok=True,
            value=value,
            display_value=self._display(value, program.output_semantics),
            trace=tuple(trace),
            formula_program=program,
            gate_status=gate_status,
            audit_reasons=audit_reasons,
            source_refs=tuple(program.source_refs),
        )

    def execute_builtin(
        self,
        formula_id: str,
        bindings: Mapping[str, BoundVariable],
    ) -> CalculationExecutionResult:
        variable_names = tuple(bindings) if formula_id in {"ranking_asc", "ranking_desc"} else ()
        try:
            program = self.registry.build(formula_id, variable_names=variable_names)
        except ValueError as exc:
            return CalculationExecutionResult(ok=False, error=str(exc))
        required = {
            ref
            for step in program.steps
            for ref in step.args
            if not ref.startswith("#") and not ref.startswith("const:")
        }
        missing = tuple(sorted(required - set(bindings)))
        if missing:
            return CalculationExecutionResult(ok=False, error=f"missing_variable_binding:{','.join(missing)}", formula_program=program)
        return self.execute_program(program, bindings)

    def execute_material_candidate(self, candidate: EvidenceCandidate) -> CalculationExecutionResult:
        formulas = self.extractor.extract_from_candidate(candidate)
        if not formulas:
            return CalculationExecutionResult(ok=False, error="material_formula_not_found")
        if len(formulas) != 1:
            return CalculationExecutionResult(ok=False, error="multiple_material_formulas_need_selection")
        evidence = formulas[0]
        bindings = self.binder.bind(evidence)
        gate = self.gate.evaluate(evidence, bindings)
        if gate.status is not FormulaGateStatus.PASS:
            return CalculationExecutionResult(
                ok=False,
                error="formula_evidence_not_ready",
                gate_status=gate.status.value,
                audit_reasons=tuple(gate.reasons),
                source_refs=tuple(evidence.source_refs),
            )
        try:
            program = self.compiler.compile(evidence, bindings)
        except ValueError as exc:
            return CalculationExecutionResult(
                ok=False,
                error=str(exc),
                gate_status=FormulaGateStatus.FAIL.value,
                audit_reasons=("formula_compile_failed",),
                source_refs=tuple(evidence.source_refs),
            )
        return self.execute_program(
            program,
            bindings,
            gate_status=gate.status.value,
            audit_reasons=tuple(gate.reasons),
        )
