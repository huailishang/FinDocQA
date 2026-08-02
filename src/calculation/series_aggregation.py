"""Dataset-agnostic source-bound numeric-series aggregation for C3.

The module validates one immutable source series, compiles supported scalar
aggregations into the existing FormulaProgram contract, and delegates Decimal
execution to DeterministicCalculationEngine.  It never discovers evidence,
selects a source range, or infers an operation from natural language.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from calculation.contracts import (
    AggregationOutputOperation,
    AggregationSelector,
    BoundVariable,
    CalculationExecutionResult,
    DeterministicExecutionGateInput,
    ExecutionGateFact,
    FormulaProgram,
    FormulaSourceRef,
    FormulaStep,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesAggregationRequest,
    SourceBoundNumericSeriesItem,
    SourceSeriesBindingStatus,
)
from calculation.engine import DeterministicCalculationEngine


EMPTY_SERIES = "EMPTY_SERIES"
NON_NUMERIC_ITEM = "NON_NUMERIC_ITEM"
NON_FINITE_ITEM = "NON_FINITE_ITEM"
MISSING_LINEAGE = "MISSING_LINEAGE"
DUPLICATE_COORDINATE = "DUPLICATE_COORDINATE"
CROSS_SOURCE_SERIES = "CROSS_SOURCE_SERIES"
MIXED_OR_AMBIGUOUS_UNIT = "MIXED_OR_AMBIGUOUS_UNIT"
AMBIGUOUS_AGGREGATION_RANGE = "AMBIGUOUS_AGGREGATION_RANGE"
UNSUPPORTED_AGGREGATION = "UNSUPPORTED_AGGREGATION"
LABEL_OUTPUT_NOT_SUPPORTED = "LABEL_OUTPUT_NOT_SUPPORTED"
QUESTION_AGGREGATION_MISMATCH = "QUESTION_AGGREGATION_MISMATCH"
INVALID_SERIES_MEMBER = "INVALID_SERIES_MEMBER"


@dataclass(frozen=True)
class SeriesAggregationCompilationResult:
    """Auditable output of validation and deterministic compilation."""

    program: FormulaProgram | None
    bindings: Mapping[str, BoundVariable] = field(default_factory=dict)
    gate_input: DeterministicExecutionGateInput | None = None
    reasons: Sequence[str] = field(default_factory=tuple)
    selector_output_refs: Mapping[str, str] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.program is not None and not self.reasons


class SourceBoundNumericSeriesAggregationCompiler:
    """Validate and compile one explicit source-bound numeric series."""

    @staticmethod
    def _unique_reasons(reasons: Sequence[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(reason) for reason in reasons if str(reason)))

    @staticmethod
    def _non_empty_text(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @classmethod
    def _valid_source_ref(cls, value: object) -> bool:
        return (
            isinstance(value, FormulaSourceRef)
            and cls._non_empty_text(value.doc_id)
            and cls._non_empty_text(value.source)
        )

    @classmethod
    def _source_references(
        cls,
        series: SourceBoundNumericSeries,
    ) -> tuple[FormulaSourceRef, ...]:
        return tuple(
            item.source_ref
            for item in series.items
            if isinstance(item, SourceBoundNumericSeriesItem)
            and cls._valid_source_ref(item.source_ref)
        )

    def _validate_series(self, series: SourceBoundNumericSeries) -> tuple[str, ...]:
        reasons: list[str] = []
        items = tuple(series.items)
        if not items:
            reasons.append(EMPTY_SERIES)
            return tuple(reasons)

        if not all(
            self._non_empty_text(value)
            for value in (
                series.series_id,
                series.metric,
                series.entity,
                series.source_object_id,
            )
        ):
            reasons.append(MISSING_LINEAGE)
        if series.binding_status is not SourceSeriesBindingStatus.EXACT:
            reasons.append(AMBIGUOUS_AGGREGATION_RANGE)
        if (
            series.aggregation_range_explicit is not True
            or series.total_components_ambiguity is not False
        ):
            reasons.append(AMBIGUOUS_AGGREGATION_RANGE)

        coordinates: list[str] = []
        units: set[str] = set()
        dimensions: set[str] = set()
        unit_or_dimension_invalid = False
        for expected_position, item in enumerate(items):
            if not isinstance(item, SourceBoundNumericSeriesItem):
                reasons.append(INVALID_SERIES_MEMBER)
                continue

            if not isinstance(item.value, Decimal):
                reasons.append(NON_NUMERIC_ITEM)
            elif not item.value.is_finite():
                reasons.append(NON_FINITE_ITEM)

            source_ref_valid = self._valid_source_ref(item.source_ref)
            coordinate_valid = self._non_empty_text(item.source_coordinate)
            item_source_valid = self._non_empty_text(item.source_object_id)
            if not source_ref_valid or not coordinate_valid or not item_source_valid:
                reasons.append(MISSING_LINEAGE)

            if type(item.position) is not int or item.position != expected_position:
                reasons.append(AMBIGUOUS_AGGREGATION_RANGE)

            if coordinate_valid:
                coordinates.append(item.source_coordinate)
            if self._non_empty_text(item.unit):
                units.add(item.unit.strip())
            else:
                unit_or_dimension_invalid = True
            if self._non_empty_text(item.dimension):
                dimensions.add(item.dimension.strip())
            else:
                unit_or_dimension_invalid = True

            if (
                item_source_valid
                and self._non_empty_text(series.source_object_id)
                and item.source_object_id != series.source_object_id
            ):
                reasons.append(CROSS_SOURCE_SERIES)
            if (
                source_ref_valid
                and self._non_empty_text(series.source_object_id)
                and item.source_ref.source != series.source_object_id
            ):
                reasons.append(CROSS_SOURCE_SERIES)

        if len(coordinates) != len(set(coordinates)):
            reasons.append(DUPLICATE_COORDINATE)
        if (
            unit_or_dimension_invalid
            or len(units) != 1
            or len(dimensions) != 1
        ):
            reasons.append(MIXED_OR_AMBIGUOUS_UNIT)

        return self._unique_reasons(reasons)

    @staticmethod
    def _selector(value: AggregationSelector | str) -> AggregationSelector | None:
        try:
            return value if isinstance(value, AggregationSelector) else AggregationSelector(str(value))
        except ValueError:
            return None

    @staticmethod
    def _output_operation(value: AggregationOutputOperation | str) -> AggregationOutputOperation | None:
        try:
            return (
                value
                if isinstance(value, AggregationOutputOperation)
                else AggregationOutputOperation(str(value))
            )
        except ValueError:
            return None

    def _validate_selectors(
        self,
        request: SourceBoundNumericSeriesAggregationRequest,
    ) -> tuple[tuple[AggregationSelector, ...], AggregationOutputOperation | None, tuple[str, ...]]:
        reasons: list[str] = []
        selectors: list[AggregationSelector] = []
        for raw in request.selectors:
            selector = self._selector(raw)
            if selector is None:
                reasons.append(UNSUPPORTED_AGGREGATION)
                continue
            if selector not in selectors:
                selectors.append(selector)
        if not selectors:
            reasons.append(UNSUPPORTED_AGGREGATION)

        operation = self._output_operation(request.output.operation)
        if operation is None:
            reasons.append(UNSUPPORTED_AGGREGATION)
        if str(request.output.output_kind).upper() != "SCALAR":
            reasons.append(LABEL_OUTPUT_NOT_SUPPORTED)

        output_operands: list[AggregationSelector] = []
        for raw in request.output.operands:
            selector = self._selector(raw)
            if selector is None:
                reasons.append(UNSUPPORTED_AGGREGATION)
                continue
            output_operands.append(selector)
            if selector not in selectors:
                reasons.append(UNSUPPORTED_AGGREGATION)

        if operation is AggregationOutputOperation.SELECTOR and len(output_operands) != 1:
            reasons.append(UNSUPPORTED_AGGREGATION)
        if operation is AggregationOutputOperation.SUBTRACT and len(output_operands) != 2:
            reasons.append(UNSUPPORTED_AGGREGATION)

        return tuple(selectors), operation, self._unique_reasons(reasons)

    @staticmethod
    def _bindings(series: SourceBoundNumericSeries) -> dict[str, BoundVariable]:
        result: dict[str, BoundVariable] = {}
        for item in series.items:
            name = f"series_item_{item.position + 1:04d}"
            result[name] = BoundVariable(
                name=name,
                value=item.value,
                unit=item.unit,
                source_ref=item.source_ref,
                metric=series.metric,
                entity=series.entity,
                period=item.header_label,
                definition=f"source-bound series item at {item.source_coordinate}",
                confidence="exact",
                source_coordinate=item.source_coordinate,
                source_object_id=item.source_object_id,
                dimension=item.dimension,
            )
        return result

    @staticmethod
    def _compile_selector(
        selector: AggregationSelector,
        variable_names: tuple[str, ...],
        steps: list[FormulaStep],
    ) -> str:
        def next_ref() -> str:
            return f"#{len(steps) + 1}"

        if selector in {AggregationSelector.SUM, AggregationSelector.AVERAGE}:
            current = variable_names[0]
            for variable_name in variable_names[1:]:
                output = next_ref()
                steps.append(FormulaStep(output=output, op="add", args=(current, variable_name)))
                current = output
            if selector is AggregationSelector.AVERAGE:
                output = next_ref()
                steps.append(
                    FormulaStep(
                        output=output,
                        op="divide",
                        args=(current, f"const:{len(variable_names)}"),
                    )
                )
                current = output
            elif len(variable_names) == 1:
                output = next_ref()
                steps.append(FormulaStep(output=output, op="identity", args=(current,)))
                current = output
            return current

        if len(variable_names) == 1:
            output = next_ref()
            steps.append(FormulaStep(output=output, op="identity", args=(variable_names[0],)))
            return output
        output = next_ref()
        operation = "min" if selector is AggregationSelector.MINIMUM else "max"
        steps.append(FormulaStep(output=output, op=operation, args=variable_names))
        return output

    def compile(
        self,
        request: SourceBoundNumericSeriesAggregationRequest,
    ) -> SeriesAggregationCompilationResult:
        series_reasons = self._validate_series(request.series)
        selectors, output_operation, selector_reasons = self._validate_selectors(request)
        question_reasons: tuple[str, ...] = ()
        if request.question_aggregation_match.passed is not True:
            question_reasons = self._unique_reasons(
                (QUESTION_AGGREGATION_MISMATCH, *request.question_aggregation_match.reasons)
            )

        source_fact = ExecutionGateFact(
            passed=not series_reasons,
            reasons=series_reasons,
        )
        selector_fact = ExecutionGateFact(
            passed=not selector_reasons,
            reasons=selector_reasons,
        )
        question_fact = ExecutionGateFact(
            passed=request.question_aggregation_match.passed is True,
            reasons=question_reasons,
        )
        gate_input = DeterministicExecutionGateInput(
            formula_evidence=source_fact,
            semantic_binding=selector_fact,
            question_formula_match=question_fact,
        )
        compile_reasons = self._unique_reasons((*series_reasons, *selector_reasons))
        if compile_reasons or output_operation is None:
            return SeriesAggregationCompilationResult(
                program=None,
                gate_input=gate_input,
                reasons=self._unique_reasons((*compile_reasons, *question_reasons)),
            )

        bindings = self._bindings(request.series)
        variable_names = tuple(bindings)
        steps: list[FormulaStep] = []
        selector_refs: dict[str, str] = {}
        for selector in selectors:
            selector_refs[selector.value] = self._compile_selector(
                selector,
                variable_names,
                steps,
            )

        output_operands = tuple(
            self._selector(item) for item in request.output.operands
        )
        if any(item is None for item in output_operands):
            return SeriesAggregationCompilationResult(
                program=None,
                bindings=bindings,
                gate_input=gate_input,
                reasons=(UNSUPPORTED_AGGREGATION,),
                selector_output_refs=selector_refs,
            )
        typed_operands = tuple(item for item in output_operands if item is not None)
        if output_operation is AggregationOutputOperation.SELECTOR:
            output_ref = selector_refs[typed_operands[0].value]
        else:
            output_ref = f"#{len(steps) + 1}"
            steps.append(
                FormulaStep(
                    output=output_ref,
                    op="subtract",
                    args=(
                        selector_refs[typed_operands[0].value],
                        selector_refs[typed_operands[1].value],
                    ),
                )
            )

        source_refs = self._source_references(request.series)
        program = FormulaProgram(
            formula_id=f"source_series_aggregation:{request.series.series_id}",
            steps=tuple(steps),
            output_ref=output_ref,
            output_semantics=str(request.output.output_semantics or "number"),
            source_type="source_bound_numeric_series",
            source_refs=source_refs,
            metadata={
                "series_id": request.series.series_id,
                "metric": request.series.metric,
                "entity": request.series.entity,
                "source_object_id": request.series.source_object_id,
                "selectors": [selector.value for selector in selectors],
                "output_operation": output_operation.value,
                "output_operands": [selector.value for selector in typed_operands],
                "item_count": len(variable_names),
                "source_coordinates": [
                    item.source_coordinate for item in request.series.items
                ],
            },
        )
        return SeriesAggregationCompilationResult(
            program=program,
            bindings=bindings,
            gate_input=gate_input,
            reasons=question_reasons,
            selector_output_refs=selector_refs,
        )


class SourceBoundNumericSeriesAggregator:
    """Generic product API: compile, gate, and execute one aggregation request."""

    def __init__(self) -> None:
        self.compiler = SourceBoundNumericSeriesAggregationCompiler()
        self.engine = DeterministicCalculationEngine()

    def execute(
        self,
        request: SourceBoundNumericSeriesAggregationRequest,
    ) -> CalculationExecutionResult:
        compiled = self.compiler.compile(request)
        if compiled.program is None or compiled.gate_input is None:
            source_refs = self.compiler._source_references(request.series)
            reasons = tuple(compiled.reasons)
            return CalculationExecutionResult(
                ok=False,
                error=reasons[0] if reasons else UNSUPPORTED_AGGREGATION,
                gate_status="NOT_READY",
                audit_reasons=reasons,
                source_refs=source_refs,
            )
        return self.engine.execute_gated_program(
            compiled.program,
            compiled.bindings,
            compiled.gate_input,
        )


__all__ = [
    "AMBIGUOUS_AGGREGATION_RANGE",
    "CROSS_SOURCE_SERIES",
    "DUPLICATE_COORDINATE",
    "EMPTY_SERIES",
    "INVALID_SERIES_MEMBER",
    "LABEL_OUTPUT_NOT_SUPPORTED",
    "MISSING_LINEAGE",
    "MIXED_OR_AMBIGUOUS_UNIT",
    "NON_FINITE_ITEM",
    "NON_NUMERIC_ITEM",
    "QUESTION_AGGREGATION_MISMATCH",
    "SeriesAggregationCompilationResult",
    "SourceBoundNumericSeriesAggregationCompiler",
    "SourceBoundNumericSeriesAggregator",
    "UNSUPPORTED_AGGREGATION",
]
