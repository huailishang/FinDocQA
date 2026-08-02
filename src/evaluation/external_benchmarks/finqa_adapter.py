"""Adapter for the official FinQA development split in Oracle-program mode."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from calculation import (
    AggregationOutputOperation,
    AggregationSelector,
    ExecutionGateFact,
    FormulaSourceRef,
    SeriesAggregationOutputSpec,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesAggregationRequest,
    SourceBoundNumericSeriesItem,
    SourceSeriesBindingStatus,
)
from evaluation.external_benchmarks.contracts import (
    OracleCase,
    OracleLabel,
    OracleRuntime,
    RuntimeVariable,
    TerminalClassification,
)


_SUPPORTED = {
    "add": "+",
    "subtract": "-",
    "multiply": "*",
    "divide": "/",
    "exp": "**",
}
_TABLE_SELECTOR = {
    "table_average": AggregationSelector.AVERAGE,
    "table_min": AggregationSelector.MINIMUM,
    "table_max": AggregationSelector.MAXIMUM,
    "table_sum": AggregationSelector.SUM,
}
_TABLE_OPERATORS = set(_TABLE_SELECTOR)
_STEP_RE = re.compile(r"\s*([A-Za-z_]+)\(([^()]*)\)\s*(?:,\s*|$)")
_LABEL_OUTPUT_RE = re.compile(
    r"\b(?:in\s+)?(?:what|which)\s+year\b|\bwhat\s+year\b|\bwhich\s+year\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class FinQASeriesOracleRuntime(OracleRuntime):
    """Evaluation-only runtime carrying a generic product aggregation request."""

    aggregation_request: SourceBoundNumericSeriesAggregationRequest | None = None
    official_table_program: str = ""


def _normalise(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return " ".join(re.sub(r"[^a-z0-9%$.-]+", " ", text).split())


def _numeric_value(raw: str) -> Decimal:
    text = str(raw).strip().replace("$", "").replace(",", "")
    if text.startswith("const_"):
        text = text[len("const_") :]
        if text == "m1":
            text = "-1"
    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"non_numeric_argument:{raw}") from exc
    return value / Decimal("100") if percent else value


def _table_numeric_value(raw: Any) -> Decimal:
    text = str(raw or "").strip()
    if not text or text in {"-", "–", "—"}:
        raise ValueError(f"non_numeric_table_item:{raw}")
    percent = "%" in text
    cleaned = text.replace("$", "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace(",", "").replace("–", "-").replace("—", "-")
    cleaned = cleaned.strip()
    parenthetical_negative = cleaned.startswith("(")
    if parenthetical_negative:
        cleaned = cleaned[1:]
    match = re.match(r"\s*(-?)\s*(\d+(?:\.\d+)?)", cleaned)
    if match is None:
        raise ValueError(f"non_numeric_table_item:{raw}")
    sign, number = match.groups()
    value = Decimal(number)
    if sign == "-" or parenthetical_negative:
        value = -value
    return value / Decimal("100") if percent else value


def _parse_steps(program: str) -> tuple[tuple[str, str, str], ...]:
    text = str(program or "").strip()
    if not text:
        raise ValueError("program_missing")
    rows: list[tuple[str, str, str]] = []
    position = 0
    while position < len(text):
        match = _STEP_RE.match(text, position)
        if match is None:
            raise ValueError(f"program_schema_at:{position}")
        operator, body = match.groups()
        args = [item.strip() for item in body.split(",")]
        if len(args) != 2 or not all(args):
            raise ValueError(f"operator_arity:{operator}")
        rows.append((operator, args[0], args[1]))
        position = match.end()
    return tuple(rows)


def _referenced_variable_names(expression: str) -> frozenset[str]:
    """Return exact runtime symbols referenced by a generated arithmetic expression."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("final_expression_syntax") from exc
    names = frozenset(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))
    if not names or any(re.fullmatch(r"v\d+", name) is None for name in names):
        raise ValueError("final_expression_symbols")
    return names


def _runtime_from_program(
    *, case_id: str, question: str, program: str
) -> tuple[OracleRuntime | None, TerminalClassification | None, str, bool]:
    try:
        steps = _parse_steps(program)
    except ValueError as exc:
        return None, TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA, str(exc), False

    operators = {row[0] for row in steps}
    if "greater" in operators:
        return None, TerminalClassification.INELIGIBLE_NON_NUMERIC, "operator:greater", True
    unsupported = sorted(operators - set(_SUPPORTED))
    if unsupported:
        detail = ",".join(unsupported)
        terminal = (
            TerminalClassification.UNSUPPORTED_OPERATOR
            if any(item in _TABLE_OPERATORS or item not in _SUPPORTED for item in unsupported)
            else TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA
        )
        return None, terminal, f"operators:{detail}", True

    variables: list[RuntimeVariable] = []
    expressions: list[str] = []

    def resolve(argument: str, *, step_index: int) -> str:
        if argument.startswith("#"):
            try:
                index = int(argument[1:])
            except ValueError as exc:
                raise ValueError(f"invalid_reference:{argument}") from exc
            if index < 0 or index >= step_index:
                raise ValueError(f"forward_or_missing_reference:{argument}")
            return expressions[index]
        value = _numeric_value(argument)
        name = f"v{len(variables)}"
        variables.append(RuntimeVariable(name=name, value=str(value)))
        return name

    try:
        for index, (operator, raw_left, raw_right) in enumerate(steps):
            left = resolve(raw_left, step_index=index)
            right = resolve(raw_right, step_index=index)
            if operator == "exp" and not raw_right.startswith("#"):
                exponent = _numeric_value(raw_right)
                if exponent != exponent.to_integral_value() or abs(exponent) > 100:
                    return (
                        None,
                        TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT,
                        f"unsupported_exponent:{raw_right}",
                        True,
                    )
            expressions.append(f"({left} {_SUPPORTED[operator]} {right})")
    except ValueError as exc:
        return None, TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT, str(exc), True

    final_expression = expressions[-1]
    try:
        referenced_names = _referenced_variable_names(final_expression)
    except ValueError as exc:
        return None, TerminalClassification.ADAPTER_PARSE_ERROR, str(exc), True
    projected_variables = tuple(
        variable for variable in variables if variable.name in referenced_names
    )
    projected_names = {variable.name for variable in projected_variables}
    if len(projected_variables) != len(projected_names) or projected_names != set(
        referenced_names
    ):
        return (
            None,
            TerminalClassification.ADAPTER_PARSE_ERROR,
            "final_expression_variable_projection_failed",
            True,
        )

    runtime = OracleRuntime(
        dataset="finqa",
        case_id=case_id,
        question=question,
        expression=final_expression,
        variables=projected_variables,
        source_id=f"finqa://dev/{case_id}",
        native_program=program,
        scale="",
    )
    return runtime, None, "", True


def _gold_table_rows(qa: Mapping[str, Any]) -> set[int]:
    result: set[int] = set()
    for key in (qa.get("gold_inds") or {}):
        match = re.fullmatch(r"table_(\d+)", str(key))
        if match:
            result.add(int(match.group(1)))
    for value in qa.get("ann_table_rows") or []:
        if isinstance(value, int):
            result.add(value)
    return result


def _annotation_matches(
    qa: Mapping[str, Any],
    program_steps: Sequence[tuple[str, str, str]],
) -> bool:
    annotations = qa.get("steps")
    if not isinstance(annotations, list) or len(annotations) != len(program_steps):
        return False
    prefixes = {
        "table_average": ("average", "avg"),
        "table_min": ("min", "minimum"),
        "table_max": ("max", "maximum"),
        "table_sum": ("sum",),
        "subtract": ("minus", "subtract"),
    }
    for annotation, (operator, left, right) in zip(annotations, program_steps, strict=True):
        if not isinstance(annotation, Mapping):
            return False
        operation = str(annotation.get("op") or "").lower()
        if not any(operation.startswith(prefix) for prefix in prefixes.get(operator, ())):
            return False
        if _normalise(annotation.get("arg1")) != _normalise(left):
            return False
        if _normalise(annotation.get("arg2")) != _normalise(right):
            return False
    return True


def _infer_unit_dimension(
    *, table: Sequence[Sequence[Any]], row: Sequence[Any], question: str
) -> tuple[str, str]:
    values = [str(value or "") for value in row[1:]]
    if values and all("%" in value for value in values):
        return "percent", "ratio"
    context = _normalise(
        " ".join(
            [question, *[str(value) for value in (table[0] if table else ())], str(row[0])]
        )
    )
    if "thousands of barrels per day" in context:
        return "thousand_barrels_per_day", "volume_rate"
    if "expected life in years" in context:
        return "year", "duration"
    if "million" in context:
        return "million", "amount"
    if "thousand" in context:
        return "thousand", "amount"
    if any("$" in value for value in values):
        return "currency", "amount"
    return "number", "number"


def _contains_total_components_ambiguity(
    table: Sequence[Sequence[Any]],
    *,
    row_index: int,
    selectors: Sequence[AggregationSelector],
) -> bool:
    if AggregationSelector.SUM not in selectors or not table:
        return False
    header = table[0]
    row = table[row_index]
    has_total_header = any(re.search(r"\btotal\b", _normalise(cell)) for cell in header[1:])
    numeric_count = 0
    for cell in row[1:]:
        try:
            _table_numeric_value(cell)
            numeric_count += 1
        except ValueError:
            pass
    return has_total_header and numeric_count >= 2


def _resolve_table_output(
    steps: Sequence[tuple[str, str, str]],
) -> tuple[tuple[AggregationSelector, ...], SeriesAggregationOutputSpec] | None:
    selectors: list[AggregationSelector] = []
    step_selectors: dict[int, AggregationSelector] = {}
    for index, (operator, _left, _right) in enumerate(steps):
        selector = _TABLE_SELECTOR.get(operator)
        if selector is not None:
            step_selectors[index] = selector
            if selector not in selectors:
                selectors.append(selector)
        elif operator != "subtract":
            return None
    if not step_selectors:
        return None
    final_operator, final_left, final_right = steps[-1]
    if final_operator in _TABLE_SELECTOR:
        selector = _TABLE_SELECTOR[final_operator]
        return tuple(selectors), SeriesAggregationOutputSpec(
            operation=AggregationOutputOperation.SELECTOR,
            operands=(selector,),
        )
    if final_operator != "subtract":
        return None

    def referenced_selector(value: str) -> AggregationSelector | None:
        if not value.startswith("#"):
            return None
        try:
            index = int(value[1:])
        except ValueError:
            return None
        return step_selectors.get(index)

    left_selector = referenced_selector(final_left)
    right_selector = referenced_selector(final_right)
    if left_selector is None or right_selector is None:
        return None
    return tuple(selectors), SeriesAggregationOutputSpec(
        operation=AggregationOutputOperation.SUBTRACT,
        operands=(left_selector, right_selector),
    )


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _native_arithmetic_program(
    request: SourceBoundNumericSeriesAggregationRequest,
) -> str:
    """Build a source-derived arithmetic program for native scorer parity only."""

    values = tuple(item.value for item in request.series.items)
    steps: list[str] = []
    selector_refs: dict[AggregationSelector, str] = {}

    def append(operator: str, left: str, right: str) -> str:
        index = len(steps)
        steps.append(f"{operator}({left}, {right})")
        return f"#{index}"

    for raw_selector in request.selectors:
        selector = (
            raw_selector
            if isinstance(raw_selector, AggregationSelector)
            else AggregationSelector(str(raw_selector))
        )
        if selector in {AggregationSelector.SUM, AggregationSelector.AVERAGE}:
            current = _decimal_text(values[0])
            if len(values) == 1:
                current = append("add", current, "const_0")
            else:
                for value in values[1:]:
                    current = append("add", current, _decimal_text(value))
            if selector is AggregationSelector.AVERAGE:
                current = append("divide", current, str(len(values)))
            selector_refs[selector] = current
        elif selector is AggregationSelector.MINIMUM:
            selector_refs[selector] = append(
                "add", _decimal_text(min(values)), "const_0"
            )
        else:
            selector_refs[selector] = append(
                "add", _decimal_text(max(values)), "const_0"
            )

    output_operation = (
        request.output.operation
        if isinstance(request.output.operation, AggregationOutputOperation)
        else AggregationOutputOperation(str(request.output.operation))
    )
    operands = tuple(
        item if isinstance(item, AggregationSelector) else AggregationSelector(str(item))
        for item in request.output.operands
    )
    if output_operation is AggregationOutputOperation.SUBTRACT:
        append("subtract", selector_refs[operands[0]], selector_refs[operands[1]])
    elif not steps:
        append("add", selector_refs[operands[0]], "const_0")
    return ", ".join(steps)


def _series_runtime_from_document(
    *,
    case_id: str,
    question: str,
    program: str,
    table: Sequence[Sequence[Any]],
    qa: Mapping[str, Any],
) -> tuple[FinQASeriesOracleRuntime | None, TerminalClassification | None, str, bool]:
    try:
        steps = _parse_steps(program)
    except ValueError as exc:
        return None, TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA, str(exc), False
    if not any(operator in _TABLE_OPERATORS for operator, _left, _right in steps):
        return None, None, "", True
    if not table or len(table) < 2:
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "AMBIGUOUS_AGGREGATION_RANGE", True
    resolved = _resolve_table_output(steps)
    if resolved is None:
        return None, TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA, "unsupported_table_composition", True
    selectors, output = resolved

    table_steps = [step for step in steps if step[0] in _TABLE_OPERATORS]
    row_labels = {_normalise(left) for _operator, left, _right in table_steps}
    if len(row_labels) != 1 or any(_normalise(right) != "none" for _operator, _left, right in table_steps):
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "AMBIGUOUS_AGGREGATION_RANGE", True
    row_label = table_steps[0][1]
    matches = [
        index
        for index, row in enumerate(table)
        if row and _normalise(row[0]) == _normalise(row_label)
    ]
    gold_rows = _gold_table_rows(qa)
    if len(matches) != 1 or matches[0] not in gold_rows or matches[0] == 0:
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "AMBIGUOUS_AGGREGATION_RANGE", True
    row_index = matches[0]
    row = table[row_index]
    if len(row) < 2:
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "EMPTY_SERIES", True
    if _LABEL_OUTPUT_RE.search(question) and any(
        selector in {AggregationSelector.MINIMUM, AggregationSelector.MAXIMUM}
        for selector in selectors
    ):
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "LABEL_OUTPUT_NOT_SUPPORTED", True
    if _contains_total_components_ambiguity(
        table,
        row_index=row_index,
        selectors=selectors,
    ):
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "AMBIGUOUS_AGGREGATION_RANGE", True
    if not _annotation_matches(qa, steps):
        return None, TerminalClassification.UNSUPPORTED_OPERATOR, "QUESTION_AGGREGATION_MISMATCH", True

    source_object_id = f"finqa://dev/{case_id}/table"
    doc_id = f"finqa-{case_id}"
    unit, dimension = _infer_unit_dimension(table=table, row=row, question=question)
    header = table[0]
    items: list[SourceBoundNumericSeriesItem] = []
    try:
        for position, raw_value in enumerate(row[1:]):
            column_index = position + 1
            coordinate = f"{source_object_id}/r{row_index}c{column_index}"
            header_label = str(header[column_index]) if column_index < len(header) else ""
            items.append(
                SourceBoundNumericSeriesItem(
                    position=position,
                    value=_table_numeric_value(raw_value),
                    unit=unit,
                    dimension=dimension,
                    source_ref=FormulaSourceRef(
                        doc_id=doc_id,
                        page_number=None,
                        source=source_object_id,
                        block_id=f"table-row-{row_index}",
                        excerpt=f"{header_label}: {raw_value}",
                    ),
                    source_coordinate=coordinate,
                    source_object_id=source_object_id,
                    header_label=header_label,
                )
            )
    except ValueError as exc:
        return None, TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT, str(exc), True

    series = SourceBoundNumericSeries(
        series_id=f"{source_object_id}/row/{row_index}",
        items=tuple(items),
        metric=str(row[0]),
        entity=case_id,
        source_object_id=source_object_id,
        binding_status=SourceSeriesBindingStatus.EXACT,
        aggregation_range_explicit=True,
        total_components_ambiguity=False,
    )
    request = SourceBoundNumericSeriesAggregationRequest(
        series=series,
        selectors=selectors,
        output=output,
        question_aggregation_match=ExecutionGateFact(
            True,
            ("official_program_and_operation_annotation_agree",),
        ),
    )
    runtime = FinQASeriesOracleRuntime(
        dataset="finqa",
        case_id=case_id,
        question=question,
        expression="",
        variables=(),
        source_id=f"finqa://dev/{case_id}",
        native_program=_native_arithmetic_program(request),
        scale="",
        aggregation_request=request,
        official_table_program=program,
    )
    return runtime, None, "", True


def load_finqa_cases(
    path: str | Path,
    *,
    enable_series_aggregation: bool = True,
) -> tuple[OracleCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("FinQA development split must be a list")
    cases: list[OracleCase] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, Mapping) or not isinstance(row.get("qa"), Mapping):
            raise ValueError("FinQA document schema invalid")
        case_id = str(row.get("id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError(f"FinQA missing or duplicate id:{case_id}")
        seen.add(case_id)
        qa = row["qa"]
        question = str(qa.get("question") or "")
        program = str(qa.get("program") or "")
        table = row.get("table") if isinstance(row.get("table"), list) else []
        if enable_series_aggregation:
            series_runtime, series_terminal, series_detail, series_parsed = _series_runtime_from_document(
                case_id=case_id,
                question=question,
                program=program,
                table=table,
                qa=qa,
            )
        else:
            series_runtime, series_terminal, series_detail, series_parsed = (
                None,
                None,
                "",
                True,
            )
        if series_runtime is not None or series_terminal is not None:
            runtime: OracleRuntime | None = series_runtime
            preclassified = series_terminal
            detail = series_detail
            parsed = series_parsed
        else:
            runtime, preclassified, detail, parsed = _runtime_from_program(
                case_id=case_id,
                question=question,
                program=program,
            )
        numeric_eligible = preclassified is not TerminalClassification.INELIGIBLE_NON_NUMERIC
        cases.append(
            OracleCase(
                dataset="finqa",
                case_id=case_id,
                question=question,
                numeric_eligible=numeric_eligible,
                runtime=runtime,
                label=OracleLabel(
                    answer=qa.get("exe_ans"),
                    answer_type="numeric" if numeric_eligible else "boolean",
                    native_context={"table": table},
                ),
                preclassified=preclassified,
                failure_detail=detail,
                parsed_program_schema=parsed,
            )
        )
    return tuple(cases)


__all__ = ["FinQASeriesOracleRuntime", "load_finqa_cases"]
