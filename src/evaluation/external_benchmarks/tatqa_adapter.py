"""Adapter for the official TAT-QA development split in Oracle-program mode."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping

from calculation import (
    ExecutionGateFact,
    FormulaSourceRef,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesItem,
    SourceBoundTableMember,
    SourceBoundTableMemberCollection,
    SourceBoundTablePredicateCardinalityRequest,
    SourceBoundTableSectionCardinalityRequest,
    SourceSeriesBindingStatus,
    TablePredicateOperator,
    TableSectionAxisType,
)
from evaluation.external_benchmarks.contracts import (
    OracleCase,
    OracleLabel,
    OracleRuntime,
    RuntimeVariable,
    TerminalClassification,
)


_SUPPORTED_SCALES = {"", "thousand", "million", "billion", "percent"}
_PERCENT_LITERAL = re.compile(r"(?<![\w.])([+-]?\d[\d,]*(?:\.\d+)?)\s*%")


def _prepare_expression(derivation: str) -> str:
    text = str(derivation or "").strip()
    if not text:
        raise ValueError("derivation_missing")
    text = text.replace("$", "").replace("€", "").replace("£", "")
    text = re.sub(
        r"\(\s*(\d[\d,]*(?:\.\d+)?)\s*\)",
        lambda match: f"(-{match.group(1)})",
        text,
    )
    text = text.replace(",", "").replace("[", "(").replace("]", ")")
    text = text.replace("−", "-").replace("–", "-").replace("^", "**")
    text = _PERCENT_LITERAL.sub(lambda match: f"({match.group(1).replace(',', '')}/100)", text)
    return " ".join(text.split())


def _validate_ast(node: ast.AST) -> tuple[TerminalClassification | None, str]:
    for item in ast.walk(node):
        if isinstance(item, ast.Compare):
            return TerminalClassification.UNSUPPORTED_OPERATOR, "comparison"
        if isinstance(item, ast.Call):
            return TerminalClassification.UNSUPPORTED_OPERATOR, "function_call"
        if isinstance(item, ast.Name):
            return TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA, f"name:{item.id}"
        if isinstance(item, ast.Constant):
            if isinstance(item.value, bool) or not isinstance(item.value, (int, float)):
                return TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT, "non_numeric_constant"
        if isinstance(item, ast.BinOp) and not isinstance(
            item.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
        ):
            return TerminalClassification.UNSUPPORTED_OPERATOR, type(item.op).__name__
        if isinstance(item, ast.UnaryOp) and not isinstance(item.op, (ast.UAdd, ast.USub)):
            return TerminalClassification.UNSUPPORTED_OPERATOR, type(item.op).__name__
        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Pow):
            exponent = item.right
            if not isinstance(exponent, ast.Constant) or isinstance(exponent.value, bool):
                return TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT, "dynamic_exponent"
            value = Decimal(str(exponent.value))
            if value != value.to_integral_value() or abs(value) > 100:
                return TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT, "unsupported_exponent"
    return None, ""


def _constant_value(node: ast.AST) -> Decimal | None:
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _constant_value(node.operand)
        if value is None:
            return None
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _constant_value(node.left), _constant_value(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow) and right == right.to_integral_value():
                return left ** int(right)
        except ArithmeticError:
            return None
    return None


def _add_term_count(node: ast.AST) -> int:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_term_count(node.left) + _add_term_count(node.right)
    return 1


def _is_average_division(node: ast.BinOp) -> bool:
    denominator = _constant_value(node.right)
    if denominator is None or denominator != denominator.to_integral_value():
        return False
    count = int(denominator)
    return 2 <= count <= 20 and _add_term_count(node.left) == count


def _output_multiplier(tree: ast.Expression, scale: str) -> str:
    if scale != "percent":
        return "1"
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if not _is_average_division(node):
                return "100"
    return "1"


class _LiteralVariableTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.variables: list[RuntimeVariable] = []

    def visit_Constant(self, node: ast.Constant) -> ast.AST:  # noqa: N802
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return node
        name = f"v{len(self.variables)}"
        self.variables.append(RuntimeVariable(name=name, value=str(Decimal(str(node.value)))))
        return ast.copy_location(ast.Name(id=name, ctx=ast.Load()), node)


def _runtime_from_derivation(
    *, case_id: str, question: str, derivation: str, scale: str
) -> tuple[OracleRuntime | None, TerminalClassification | None, str, bool]:
    if scale not in _SUPPORTED_SCALES:
        return None, TerminalClassification.UNSUPPORTED_SCALE_OR_UNIT, f"scale:{scale}", False
    inline_scales = sorted(
        set(re.findall(r"(?i)\b(?:hundred|thousand|million|billion)\b", derivation))
    )
    if inline_scales:
        return (
            None,
            TerminalClassification.UNSUPPORTED_SCALE_OR_UNIT,
            "inline_scale:" + ",".join(item.lower() for item in inline_scales),
            False,
        )
    try:
        prepared = _prepare_expression(derivation)
    except ValueError as exc:
        return None, TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA, str(exc), False
    try:
        tree = ast.parse(prepared, mode="eval")
    except SyntaxError as exc:
        return None, TerminalClassification.ADAPTER_PARSE_ERROR, f"syntax:{exc.msg}", False
    terminal, detail = _validate_ast(tree)
    if terminal is not None:
        return None, terminal, detail, True
    output_multiplier = _output_multiplier(tree, scale)
    transformer = _LiteralVariableTransformer()
    transformed = transformer.visit(tree)
    ast.fix_missing_locations(transformed)
    expression = ast.unparse(transformed.body)
    runtime = OracleRuntime(
        dataset="tatqa",
        case_id=case_id,
        question=question,
        expression=expression,
        variables=tuple(transformer.variables),
        source_id=f"tatqa://dev/{case_id}",
        native_program=derivation,
        scale=scale,
        output_multiplier=output_multiplier,
    )
    return runtime, None, "", True




@dataclass(frozen=True)
class TATQAPredicateCardinalityOracleRuntime(OracleRuntime):
    """Evaluation-only runtime carrying one generic predicate-cardinality request."""

    predicate_request: SourceBoundTablePredicateCardinalityRequest | None = None
    oracle_axis: str = ""


@dataclass(frozen=True)
class TATQASectionCardinalityOracleRuntime(OracleRuntime):
    """Evaluation-only runtime carrying one generic section-cardinality request."""

    section_request: SourceBoundTableSectionCardinalityRequest | None = None
    oracle_axis: str = ""


def _default_predicate_taxonomy_path(dataset_path: Path) -> Path:
    resolved = dataset_path.resolve()
    if len(resolved.parents) < 4:
        return Path("evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl")
    return (
        resolved.parents[3]
        / "c3_unsupported_operator_triage_v1"
        / "per_case_taxonomy.jsonl"
    )


def _accepted_predicate_proofs(path: Path) -> dict[str, Mapping[str, Any]]:
    if not path.is_file():
        return {}
    selected: dict[str, Mapping[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        proof = row.get("oracle_proof") or {}
        if (
            row.get("candidate_capability")
            == "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY"
            and row.get("candidate_type") == "PRODUCT_CAPABILITY"
            and row.get("selection_eligibility") is True
            and row.get("binding_uniqueness_status") == "UNIQUE"
            and proof.get("proof_status") == "COMPLETE"
            and proof.get("binding_uniqueness_status") == "UNIQUE"
        ):
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or not case_id or case_id in selected:
                raise ValueError("predicate taxonomy contains missing or duplicate case id")
            selected[case_id] = proof
    return selected


def _accepted_section_proofs(path: Path) -> dict[str, Mapping[str, Any]]:
    if not path.is_file():
        return {}
    selected: dict[str, Mapping[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        proof = row.get("oracle_proof") or {}
        if (
            row.get("candidate_capability")
            == "SOURCE_BOUND_TABLE_SECTION_CARDINALITY"
            and row.get("candidate_type") == "PRODUCT_CAPABILITY"
            and row.get("selection_eligibility") is True
            and row.get("binding_uniqueness_status") == "UNIQUE"
            and proof.get("proof_status") == "COMPLETE"
            and proof.get("binding_uniqueness_status") == "UNIQUE"
        ):
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or not case_id or case_id in selected:
                raise ValueError("section taxonomy contains missing or duplicate case id")
            selected[case_id] = proof
    return selected


def _table_decimal(raw: object) -> Decimal:
    if not isinstance(raw, str):
        raise ValueError("predicate table cell must be text")
    text = raw.strip().replace("$", "").replace("€", "").replace("£", "")
    text = text.replace(",", "").replace("−", "-").replace("–", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1].strip()
    if not text or text in {"—", "-"}:
        raise ValueError("predicate table cell is not numeric")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("predicate table cell is not numeric") from exc
    if not value.is_finite():
        raise ValueError("predicate table cell is not finite")
    return value


def _predicate_operator(raw: object) -> TablePredicateOperator:
    if raw == ">":
        return TablePredicateOperator.GREATER_THAN
    if raw == "<":
        return TablePredicateOperator.LESS_THAN
    raise ValueError("predicate operator is unsupported")


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strict_axis_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"predicate axis {field_name} must be an integer")
    return value


def _table_row(table: list[Any], row_index: int, detail: str) -> list[Any]:
    if row_index < 0 or row_index >= len(table):
        raise ValueError(f"predicate {detail} row outside official table")
    row = table[row_index]
    if not isinstance(row, list) or not row:
        raise ValueError(f"predicate {detail} row schema invalid")
    return row


def _member_axis_coordinates(
    members: list[Any],
) -> tuple[list[Mapping[str, Any]], list[int], list[int]]:
    normalized: list[Mapping[str, Any]] = []
    row_indexes: list[int] = []
    column_indexes: list[int] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise ValueError("predicate member schema invalid")
        row_index = _strict_axis_int(member.get("row_index"), "member row_index")
        column_index = _strict_axis_int(
            member.get("column_index"), "member column_index"
        )
        normalized.append(member)
        row_indexes.append(row_index)
        column_indexes.append(column_index)
    return normalized, row_indexes, column_indexes


def _normalized_section_heading(value: object, field_name: str) -> str:
    if not _non_empty_text(value):
        raise ValueError(f"predicate {field_name} must be non-empty text")
    return value.strip().rstrip(":").strip().casefold()


def _normalized_row_label(row: list[Any], detail: str) -> str:
    if not row or not _non_empty_text(row[0]):
        raise ValueError(f"predicate {detail} row label must be non-empty text")
    return row[0].strip().casefold()


def _is_total_label(label: str) -> bool:
    return label.startswith("total ")


def _finite_table_value_at(
    table: list[Any],
    row_index: int,
    column_index: int,
    detail: str,
) -> Decimal:
    row = _table_row(table, row_index, detail)
    if column_index < 0 or column_index >= len(row):
        raise ValueError(f"predicate {detail} column outside official table")
    return _table_decimal(row[column_index])


def _derive_single_period_complete_rows(
    *,
    table: list[Any],
    axis_column: int,
    range_rule: object,
) -> list[int]:
    supported_rule = "all numeric detail rows before the unique Total row"
    if not isinstance(range_rule, str) or range_rule != supported_rule:
        raise ValueError("predicate single-period range_rule unsupported")
    if not table:
        raise ValueError("predicate single-period table is empty")
    header = _table_row(table, 0, "single-period header")
    if axis_column < 0 or axis_column >= len(header):
        raise ValueError("predicate single-period axis column outside header")

    total_rows: list[int] = []
    for row_index in range(1, len(table)):
        row = _table_row(table, row_index, "single-period scan")
        label = _normalized_row_label(row, "single-period scan")
        if _is_total_label(label):
            _finite_table_value_at(
                table,
                row_index,
                axis_column,
                "single-period total",
            )
            total_rows.append(row_index)
    if len(total_rows) != 1:
        raise ValueError("predicate single-period Total boundary is not unique")

    total_row = total_rows[0]
    if total_row <= 1:
        raise ValueError("predicate single-period detail range is empty")
    expected_rows = list(range(1, total_row))
    for row_index in expected_rows:
        row = _table_row(table, row_index, "single-period detail")
        if _is_total_label(_normalized_row_label(row, "single-period detail")):
            raise ValueError("predicate single-period detail includes Total row")
        _finite_table_value_at(
            table,
            row_index,
            axis_column,
            "single-period detail",
        )
    return expected_rows


def _derive_bound_section_complete_rows(
    *,
    table: list[Any],
    axis_column: int,
    section_phrase: object,
) -> list[int]:
    normalized_section = _normalized_section_heading(
        section_phrase,
        "section_phrase",
    )
    heading_rows: list[int] = []
    for row_index, raw_row in enumerate(table):
        if not isinstance(raw_row, list) or not raw_row:
            continue
        if not _non_empty_text(raw_row[0]):
            continue
        if _normalized_section_heading(
            raw_row[0],
            "section heading",
        ) == normalized_section:
            heading_rows.append(row_index)
    if not heading_rows:
        raise ValueError("predicate section heading mismatch")
    if len(heading_rows) != 1:
        raise ValueError("predicate section heading is ambiguous")

    start_row = heading_rows[0] + 1
    if start_row >= len(table):
        raise ValueError("predicate bound section has no detail rows")
    expected_rows: list[int] = []
    for row_index in range(start_row, len(table)):
        row = _table_row(table, row_index, "bound-section scan")
        label = _normalized_row_label(row, "bound-section scan")
        if _is_total_label(label):
            if normalized_section not in label:
                raise ValueError("predicate section Total row does not match section")
            _finite_table_value_at(
                table,
                row_index,
                axis_column,
                "bound-section total",
            )
            if not expected_rows:
                raise ValueError("predicate bound section has no numeric detail rows")
            return expected_rows
        _finite_table_value_at(
            table,
            row_index,
            axis_column,
            "bound-section detail",
        )
        expected_rows.append(row_index)
    raise ValueError("predicate bound section Total boundary missing")


def _validate_predicate_axis_binding(
    *,
    table: list[Any],
    axis_info: Mapping[str, Any],
    members: list[Any],
) -> str:
    axis = axis_info.get("axis")
    normalized_members, member_rows, member_columns = _member_axis_coordinates(
        members
    )

    if axis == "ROW_ACROSS_PERIOD_COLUMNS":
        axis_row = _strict_axis_int(axis_info.get("row_index"), "row_index")
        row = _table_row(table, axis_row, "axis")
        row_label = axis_info.get("row_label")
        if not _non_empty_text(row_label):
            raise ValueError("predicate axis row_label must be non-empty text")
        if not isinstance(row[0], str) or row[0] != row_label:
            raise ValueError("predicate axis row label mismatch")

        period_labels = axis_info.get("period_labels")
        if (
            not isinstance(period_labels, list)
            or not period_labels
            or any(not _non_empty_text(label) for label in period_labels)
        ):
            raise ValueError("predicate axis period_labels must be non-empty text list")
        if len(period_labels) != len(normalized_members):
            raise ValueError("predicate axis period/member count mismatch")
        if member_rows != [axis_row] * len(normalized_members):
            raise ValueError("predicate member row does not match axis row")
        if len(member_columns) != len(set(member_columns)):
            raise ValueError("predicate row-axis member columns are duplicated")
        expected_columns = list(range(1, len(row)))
        if member_columns != expected_columns:
            raise ValueError("predicate row-axis members do not cover the full row")

        member_periods: list[str] = []
        for member, column_index in zip(normalized_members, member_columns):
            if column_index < 0 or column_index >= len(row):
                raise ValueError("predicate row-axis member column outside official row")
            period_label = member.get("period_label")
            if not _non_empty_text(period_label):
                raise ValueError("predicate member period_label must be non-empty text")
            member_periods.append(period_label)
            if not any(
                isinstance(header_row, list)
                and column_index < len(header_row)
                and isinstance(header_row[column_index], str)
                and period_label in header_row[column_index]
                for header_row in table[:axis_row]
            ):
                raise ValueError("predicate period label does not match table header")
        if member_periods != period_labels:
            raise ValueError("predicate member periods do not match axis period order")
        return row_label

    if axis not in {
        "CATEGORY_ROWS_IN_SINGLE_PERIOD_COLUMN",
        "CATEGORY_ROWS_IN_BOUND_SECTION",
    }:
        raise ValueError("predicate axis unsupported")

    axis_column = _strict_axis_int(axis_info.get("column_index"), "column_index")
    start_row = _strict_axis_int(axis_info.get("start_row"), "start_row")
    end_row = _strict_axis_int(
        axis_info.get("end_row_exclusive"), "end_row_exclusive"
    )
    if not (0 <= start_row < end_row <= len(table)):
        raise ValueError("predicate axis row range invalid")

    if axis == "CATEGORY_ROWS_IN_SINGLE_PERIOD_COLUMN":
        official_rows = _derive_single_period_complete_rows(
            table=table,
            axis_column=axis_column,
            range_rule=axis_info.get("range_rule"),
        )
        metric = axis
    else:
        section_phrase = axis_info.get("section_phrase")
        official_rows = _derive_bound_section_complete_rows(
            table=table,
            axis_column=axis_column,
            section_phrase=section_phrase,
        )
        metric = section_phrase.strip()

    expected_start = official_rows[0]
    expected_end = official_rows[-1] + 1
    if start_row != expected_start or end_row != expected_end:
        raise ValueError("predicate axis range does not match official complete range")
    if member_rows != official_rows:
        raise ValueError("predicate category members do not cover official complete range")
    if len(normalized_members) != len(official_rows):
        raise ValueError("predicate category member count does not match official range")
    if member_columns != [axis_column] * len(normalized_members):
        raise ValueError("predicate category member column does not match axis column")

    for member, row_index in zip(normalized_members, official_rows):
        row = _table_row(table, row_index, "category member")
        if axis_column < 0 or axis_column >= len(row):
            raise ValueError("predicate axis column outside official table")
        member_label = member.get("member_label")
        if not _non_empty_text(member_label):
            raise ValueError("predicate member_label must be non-empty text")
        if not isinstance(row[0], str) or row[0] != member_label:
            raise ValueError("predicate category label mismatch")

    return metric


def _predicate_runtime_from_proof(
    *,
    table_payload: Mapping[str, Any],
    question_row: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> TATQAPredicateCardinalityOracleRuntime:
    table_uid = table_payload.get("uid")
    table = table_payload.get("table")
    if not isinstance(table_uid, str) or not table_uid or not isinstance(table, list):
        raise ValueError("predicate table schema invalid")
    source_object_id = f"tatqa://table/{table_uid}"
    source_ids = proof.get("bound_source_object_ids")
    if source_ids != [source_object_id]:
        raise ValueError("predicate source object mismatch")

    axis_info = proof.get("bound_axis_or_section")
    members = proof.get("bound_member_or_value_coordinates")
    rule = proof.get("predicate_or_membership_rule")
    if not isinstance(axis_info, Mapping) or not isinstance(members, list) or not members:
        raise ValueError("predicate bound collection missing")
    if not isinstance(rule, Mapping) or rule.get("rule_type") != "SCALAR_PREDICATE_CARDINALITY":
        raise ValueError("predicate rule missing")

    axis = axis_info.get("axis")
    metric = _validate_predicate_axis_binding(
        table=table,
        axis_info=axis_info,
        members=members,
    )
    unit = rule.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("predicate unit missing")
    threshold_raw = rule.get("threshold_in_source_units", rule.get("threshold"))
    try:
        threshold = Decimal(str(threshold_raw))
    except InvalidOperation as exc:
        raise ValueError("predicate threshold invalid") from exc
    if not threshold.is_finite():
        raise ValueError("predicate threshold invalid")
    operator = _predicate_operator(rule.get("operator"))

    items: list[SourceBoundNumericSeriesItem] = []
    values: list[Decimal] = []
    for position, member in enumerate(members):
        if not isinstance(member, Mapping):
            raise ValueError("predicate member schema invalid")
        row_index = member.get("row_index")
        column_index = member.get("column_index")
        if type(row_index) is not int or type(column_index) is not int:
            raise ValueError("predicate coordinate indexes invalid")
        try:
            row = table[row_index]
            raw_cell = row[column_index]
        except (IndexError, TypeError) as exc:
            raise ValueError("predicate coordinate outside official table") from exc
        if raw_cell != member.get("raw_value"):
            raise ValueError("predicate raw cell mismatch")
        value = _table_decimal(raw_cell)
        try:
            proof_value = Decimal(str(member.get("numeric_value")))
        except InvalidOperation as exc:
            raise ValueError("predicate proof numeric value invalid") from exc
        if value != proof_value:
            raise ValueError("predicate numeric value mismatch")
        coordinate = f"{source_object_id}/r{row_index}c{column_index}"
        if coordinate != member.get("coordinate"):
            raise ValueError("predicate source coordinate mismatch")

        member_label = member.get("member_label")
        period_label = member.get("period_label")
        label = period_label if axis == "ROW_ACROSS_PERIOD_COLUMNS" else member_label
        if not _non_empty_text(label):
            raise ValueError("predicate member label missing after axis validation")

        source_ref = FormulaSourceRef(
            doc_id=table_uid,
            page_number=None,
            source=source_object_id,
            block_id="table",
            excerpt=raw_cell,
        )
        items.append(
            SourceBoundNumericSeriesItem(
                position=position,
                value=value,
                unit=unit.strip(),
                dimension="currency",
                source_ref=source_ref,
                source_coordinate=coordinate,
                source_object_id=source_object_id,
                header_label=label,
            )
        )
        values.append(value)

    matched_count = sum(
        value > threshold
        if operator is TablePredicateOperator.GREATER_THAN
        else value < threshold
        for value in values
    )
    expected_count = proof.get("independently_derived_expected_count")
    if type(expected_count) is not int or matched_count != expected_count:
        raise ValueError("predicate independent count mismatch")

    axis_signature = ":".join(
        str(axis_info.get(name, ""))
        for name in (
            "axis",
            "row_index",
            "column_index",
            "start_row",
            "end_row_exclusive",
        )
    )
    collection = SourceBoundNumericSeries(
        series_id=f"table_predicate_collection:{table_uid}:{axis_signature}",
        items=tuple(items),
        metric=metric,
        entity=source_object_id,
        source_object_id=source_object_id,
        binding_status=SourceSeriesBindingStatus.EXACT,
        aggregation_range_explicit=True,
        total_components_ambiguity=False,
    )
    predicate_request = SourceBoundTablePredicateCardinalityRequest(
        collection=collection,
        operator=operator,
        threshold=threshold,
        threshold_unit=unit.strip(),
        threshold_dimension="currency",
        question_predicate_match=ExecutionGateFact(True),
    )
    case_id = question_row.get("uid")
    question = question_row.get("question")
    if not isinstance(case_id, str) or not case_id or not isinstance(question, str):
        raise ValueError("predicate question schema invalid")
    return TATQAPredicateCardinalityOracleRuntime(
        dataset="tatqa",
        case_id=case_id,
        question=question,
        expression="source_bound_table_predicate_cardinality",
        variables=(),
        source_id=source_object_id,
        native_program="source_bound_table_predicate_cardinality",
        scale=str(question_row.get("scale") or "").lower().strip(),
        output_multiplier="1",
        predicate_request=predicate_request,
        oracle_axis=str(axis),
    )


def _is_section_boundary_label(label: str, normalized_section: str) -> bool:
    return (
        label == "total"
        or label.startswith("total ")
        or (label.startswith("gross ") and normalized_section in label)
    )


def _validate_section_member_binding(
    *,
    table: list[Any],
    source_object_id: str,
    axis_info: Mapping[str, Any],
    members: list[Any],
    rule: Mapping[str, Any],
) -> tuple[TableSectionAxisType, list[Mapping[str, Any]], list[int]]:
    axis = axis_info.get("axis")
    normalized_members, member_rows, member_columns = _member_axis_coordinates(members)
    if member_columns != [0] * len(normalized_members):
        raise ValueError("section members must be bound to column zero")

    if axis == TableSectionAxisType.ROWS_IN_BOUND_SECTION.value:
        if rule.get("rule_type") != "SECTION_MEMBER_CARDINALITY":
            raise ValueError("section membership rule missing")
        if rule.get("exclude_boundary_and_subtotal_rows") is not True:
            raise ValueError("section boundary exclusion must be explicit")
        normalized_section = _normalized_section_heading(
            axis_info.get("section_phrase"),
            "section_phrase",
        )
        heading_rows = [
            row_index
            for row_index, raw_row in enumerate(table)
            if isinstance(raw_row, list)
            and raw_row
            and _non_empty_text(raw_row[0])
            and _normalized_section_heading(raw_row[0], "section heading")
            == normalized_section
        ]
        if not heading_rows:
            raise ValueError("section heading mismatch")
        if len(heading_rows) != 1:
            raise ValueError("section heading is ambiguous")
        official_start = heading_rows[0] + 1
        boundary_rows: list[int] = []
        for row_index in range(official_start, len(table)):
            label = _normalized_row_label(
                _table_row(table, row_index, "section scan"),
                "section scan",
            )
            if _is_section_boundary_label(label, normalized_section):
                boundary_rows.append(row_index)
        if not boundary_rows:
            raise ValueError("section summary boundary missing")
        if len(boundary_rows) != 1:
            raise ValueError("section summary boundary is ambiguous")
        official_end = boundary_rows[0]
        if official_start >= official_end:
            raise ValueError("section member range is empty")
        official_rows = list(range(official_start, official_end))
        axis_type = TableSectionAxisType.ROWS_IN_BOUND_SECTION
    elif axis == TableSectionAxisType.WHOLE_TABLE_ENTITY_ROWS.value:
        if rule.get("rule_type") != "WHOLE_TABLE_ENTITY_CARDINALITY":
            raise ValueError("whole-table membership rule missing")
        header_row = _strict_axis_int(axis_info.get("header_row"), "header_row")
        if header_row != 0:
            raise ValueError("whole-table header row must be zero")
        header = _table_row(table, header_row, "whole-table header")
        official_start = header_row + 1
        official_end = len(table)
        if official_start >= official_end:
            raise ValueError("whole-table entity range is empty")
        official_rows = list(range(official_start, official_end))
        labels: list[str] = []
        for row_index in official_rows:
            row = _table_row(table, row_index, "whole-table entity")
            if len(row) != len(header):
                raise ValueError("whole-table entity row structure is incomplete")
            label = _normalized_row_label(row, "whole-table entity")
            if label == "total" or label.startswith("total ") or label.startswith("gross "):
                raise ValueError("whole-table entity range includes summary row")
            labels.append(label)
        if len(labels) != len(set(labels)):
            raise ValueError("whole-table entity labels are duplicated")
        axis_type = TableSectionAxisType.WHOLE_TABLE_ENTITY_ROWS
    else:
        raise ValueError("section axis unsupported")

    start_row = _strict_axis_int(axis_info.get("start_row"), "start_row")
    end_row = _strict_axis_int(
        axis_info.get("end_row_exclusive"),
        "end_row_exclusive",
    )
    if start_row != official_start or end_row != official_end:
        raise ValueError("section range does not match official complete range")
    if member_rows != official_rows:
        raise ValueError("section members do not cover official complete range")
    if len(normalized_members) != len(official_rows):
        raise ValueError("section member count does not match official range")

    seen_coordinates: set[str] = set()
    seen_labels: set[str] = set()
    for member, row_index in zip(normalized_members, official_rows):
        row = _table_row(table, row_index, "section member")
        member_label = member.get("member_label")
        if not _non_empty_text(member_label):
            raise ValueError("section member label must be non-empty text")
        if not isinstance(row[0], str) or row[0] != member_label:
            raise ValueError("section member label mismatch")
        coordinate = f"{source_object_id}/r{row_index}c0"
        if member.get("coordinate") != coordinate:
            raise ValueError("section source coordinate mismatch")
        if coordinate in seen_coordinates:
            raise ValueError("section source coordinate duplicated")
        if member_label in seen_labels:
            raise ValueError("section member label duplicated")
        seen_coordinates.add(coordinate)
        seen_labels.add(member_label)
    return axis_type, normalized_members, official_rows


def _section_cardinality_runtime_from_proof(
    *,
    table_payload: Mapping[str, Any],
    question_row: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> TATQASectionCardinalityOracleRuntime:
    table_uid = table_payload.get("uid")
    table = table_payload.get("table")
    if not isinstance(table_uid, str) or not table_uid or not isinstance(table, list):
        raise ValueError("section table schema invalid")
    source_object_id = f"tatqa://table/{table_uid}"
    if proof.get("bound_source_object_ids") != [source_object_id]:
        raise ValueError("section source object mismatch")

    axis_info = proof.get("bound_axis_or_section")
    members = proof.get("bound_member_or_value_coordinates")
    rule = proof.get("predicate_or_membership_rule")
    if not isinstance(axis_info, Mapping) or not isinstance(members, list) or not members:
        raise ValueError("section bound collection missing")
    if not isinstance(rule, Mapping):
        raise ValueError("section membership rule missing")

    axis_type, normalized_members, official_rows = _validate_section_member_binding(
        table=table,
        source_object_id=source_object_id,
        axis_info=axis_info,
        members=members,
        rule=rule,
    )
    expected_count = proof.get("independently_derived_expected_count")
    if type(expected_count) is not int or expected_count != len(official_rows):
        raise ValueError("section independent count mismatch")

    bound_members: list[SourceBoundTableMember] = []
    for position, (member, row_index) in enumerate(
        zip(normalized_members, official_rows)
    ):
        member_label = member.get("member_label")
        assert isinstance(member_label, str)
        coordinate = f"{source_object_id}/r{row_index}c0"
        bound_members.append(
            SourceBoundTableMember(
                position=position,
                member_label=member_label,
                source_ref=FormulaSourceRef(
                    doc_id=table_uid,
                    page_number=None,
                    source=source_object_id,
                    block_id="table",
                    excerpt=member_label,
                ),
                source_coordinate=coordinate,
                source_object_id=source_object_id,
            )
        )

    axis_signature = ":".join(
        str(axis_info.get(name, ""))
        for name in (
            "axis",
            "header_row",
            "section_phrase",
            "start_row",
            "end_row_exclusive",
        )
    )
    collection = SourceBoundTableMemberCollection(
        collection_id=f"table_section_collection:{table_uid}:{axis_signature}",
        members=tuple(bound_members),
        source_object_id=source_object_id,
        axis_type=axis_type,
        binding_status=SourceSeriesBindingStatus.EXACT,
        range_explicit=True,
        boundary_rows_excluded=True,
    )
    request = SourceBoundTableSectionCardinalityRequest(
        collection=collection,
        question_cardinality_match=ExecutionGateFact(True),
    )
    case_id = question_row.get("uid")
    question = question_row.get("question")
    if not isinstance(case_id, str) or not case_id or not isinstance(question, str):
        raise ValueError("section question schema invalid")
    return TATQASectionCardinalityOracleRuntime(
        dataset="tatqa",
        case_id=case_id,
        question=question,
        expression="source_bound_table_section_cardinality",
        variables=(),
        source_id=source_object_id,
        native_program="source_bound_table_section_cardinality",
        scale=str(question_row.get("scale") or "").lower().strip(),
        output_multiplier="1",
        section_request=request,
        oracle_axis=axis_type.value,
    )


def load_tatqa_cases(
    path: str | Path,
    *,
    enable_predicate_cardinality: bool = True,
    enable_section_cardinality: bool = True,
    predicate_taxonomy_path: str | Path | None = None,
) -> tuple[OracleCase, ...]:
    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    taxonomy_path = (
        Path(predicate_taxonomy_path)
        if predicate_taxonomy_path is not None
        else _default_predicate_taxonomy_path(source_path)
    )
    accepted_predicates = (
        _accepted_predicate_proofs(taxonomy_path)
        if enable_predicate_cardinality
        else {}
    )
    accepted_sections = (
        _accepted_section_proofs(taxonomy_path)
        if enable_section_cardinality
        else {}
    )
    if not isinstance(payload, list):
        raise ValueError("TAT-QA development split must be a list")
    cases: list[OracleCase] = []
    seen: set[str] = set()
    for document in payload:
        if not isinstance(document, Mapping) or not isinstance(document.get("questions"), list):
            raise ValueError("TAT-QA document schema invalid")
        table_payload = document.get("table")
        if not isinstance(table_payload, Mapping):
            raise ValueError("TAT-QA table schema invalid")
        for question_row in document["questions"]:
            if not isinstance(question_row, Mapping):
                raise ValueError("TAT-QA question schema invalid")
            case_id = str(question_row.get("uid") or "").strip()
            if not case_id or case_id in seen:
                raise ValueError(f"TAT-QA missing or duplicate id:{case_id}")
            seen.add(case_id)
            question = str(question_row.get("question") or "")
            answer_type = str(question_row.get("answer_type") or "")
            scale = str(question_row.get("scale") or "").lower().strip()
            runtime: OracleRuntime | None = None
            terminal: TerminalClassification | None = None
            detail = ""
            parsed = False
            numeric_eligible = answer_type in {"arithmetic", "count"}
            if answer_type == "arithmetic":
                runtime, terminal, detail, parsed = _runtime_from_derivation(
                    case_id=case_id,
                    question=question,
                    derivation=str(question_row.get("derivation") or ""),
                    scale=scale,
                )
            elif answer_type == "count":
                predicate_proof = accepted_predicates.get(case_id)
                section_proof = accepted_sections.get(case_id)
                if predicate_proof is not None and section_proof is not None:
                    raise ValueError("count case selected by multiple capabilities")
                if predicate_proof is not None:
                    try:
                        runtime = _predicate_runtime_from_proof(
                            table_payload=table_payload,
                            question_row=question_row,
                            proof=predicate_proof,
                        )
                        parsed = True
                    except ValueError as exc:
                        terminal = TerminalClassification.ADAPTER_PARSE_ERROR
                        detail = str(exc)
                        parsed = True
                elif section_proof is not None:
                    try:
                        runtime = _section_cardinality_runtime_from_proof(
                            table_payload=table_payload,
                            question_row=question_row,
                            proof=section_proof,
                        )
                        parsed = True
                    except ValueError as exc:
                        terminal = TerminalClassification.ADAPTER_PARSE_ERROR
                        detail = str(exc)
                        parsed = True
                else:
                    terminal = TerminalClassification.UNSUPPORTED_OPERATOR
                    detail = "answer_type:count"
                    parsed = bool(question_row.get("derivation"))
            else:
                terminal = TerminalClassification.INELIGIBLE_NON_NUMERIC
                detail = f"answer_type:{answer_type or 'missing'}"
            cases.append(
                OracleCase(
                    dataset="tatqa",
                    case_id=case_id,
                    question=question,
                    numeric_eligible=numeric_eligible,
                    runtime=runtime,
                    label=OracleLabel(
                        answer=question_row.get("answer"),
                        scale=scale,
                        answer_type=answer_type,
                        native_context={
                            "answer": question_row.get("answer"),
                            "answer_type": answer_type,
                            "answer_from": question_row.get("answer_from", ""),
                            "scale": scale,
                            "derivation": question_row.get("derivation", ""),
                        },
                    ),
                    preclassified=terminal,
                    failure_detail=detail,
                    parsed_program_schema=parsed,
                )
            )
    return tuple(cases)


__all__ = [
    "TATQAPredicateCardinalityOracleRuntime",
    "TATQASectionCardinalityOracleRuntime",
    "load_tatqa_cases",
]
