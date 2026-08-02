from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from pathlib import Path

import pytest

from calculation import (
    AggregationOutputOperation,
    AggregationSelector,
    ExecutionGateFact,
    FormulaSourceRef,
    SeriesAggregationOutputSpec,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesAggregationCompiler,
    SourceBoundNumericSeriesAggregationRequest,
    SourceBoundNumericSeriesAggregator,
    SourceBoundNumericSeriesItem,
    SourceSeriesBindingStatus,
)
from calculation.series_aggregation import (
    AMBIGUOUS_AGGREGATION_RANGE,
    CROSS_SOURCE_SERIES,
    DUPLICATE_COORDINATE,
    EMPTY_SERIES,
    INVALID_SERIES_MEMBER,
    LABEL_OUTPUT_NOT_SUPPORTED,
    MISSING_LINEAGE,
    MIXED_OR_AMBIGUOUS_UNIT,
    NON_FINITE_ITEM,
    NON_NUMERIC_ITEM,
    QUESTION_AGGREGATION_MISMATCH,
    UNSUPPORTED_AGGREGATION,
)


def _item(
    position: int,
    value: Decimal | object,
    *,
    unit: str = "million",
    dimension: str = "currency",
    coordinate: str | None = None,
    source_object_id: str = "document://demo/table/1",
    source_ref: FormulaSourceRef | None | object = ...,
) -> SourceBoundNumericSeriesItem:
    if source_ref is ...:
        source_ref = FormulaSourceRef(
            doc_id="document-demo",
            page_number=1,
            source=source_object_id,
            block_id="table-1",
            excerpt=str(value),
        )
    return SourceBoundNumericSeriesItem(
        position=position,
        value=value,  # type: ignore[arg-type]
        unit=unit,
        dimension=dimension,
        source_ref=source_ref,  # type: ignore[arg-type]
        source_coordinate=coordinate or f"document://demo/table/1/r1c{position + 1}",
        source_object_id=source_object_id,
        header_label=str(2024 - position),
    )


def _series(
    values: tuple[Decimal | object, ...] = (
        Decimal("2.5"),
        Decimal("-1.25"),
        Decimal("4.75"),
    ),
    **kwargs: object,
) -> SourceBoundNumericSeries:
    return SourceBoundNumericSeries(
        series_id=str(kwargs.pop("series_id", "series-demo")),
        items=kwargs.pop("items", tuple(_item(index, value) for index, value in enumerate(values))),  # type: ignore[arg-type]
        metric=str(kwargs.pop("metric", "operating profit")),
        entity=str(kwargs.pop("entity", "business unit")),
        source_object_id=str(kwargs.pop("source_object_id", "document://demo/table/1")),
        binding_status=kwargs.pop("binding_status", SourceSeriesBindingStatus.EXACT),  # type: ignore[arg-type]
        aggregation_range_explicit=bool(kwargs.pop("aggregation_range_explicit", True)),
        total_components_ambiguity=bool(kwargs.pop("total_components_ambiguity", False)),
    )


def _request(
    selector: AggregationSelector | str,
    *,
    series: SourceBoundNumericSeries | None = None,
    question_match: bool | None = True,
    output_kind: str = "SCALAR",
) -> SourceBoundNumericSeriesAggregationRequest:
    return SourceBoundNumericSeriesAggregationRequest(
        series=series or _series(),
        selectors=(selector,),
        output=SeriesAggregationOutputSpec(
            operation=AggregationOutputOperation.SELECTOR,
            operands=(selector,),
            output_kind=output_kind,
        ),
        question_aggregation_match=ExecutionGateFact(question_match),
    )


def _execute(selector: AggregationSelector, values: tuple[Decimal, ...]) -> Decimal:
    result = SourceBoundNumericSeriesAggregator().execute(
        _request(selector, series=_series(values))
    )
    assert result.ok is True
    assert isinstance(result.value, Decimal)
    assert result.gate_status == "PASS"
    assert len(result.source_refs) == len(values)
    return result.value


@pytest.mark.parametrize(
    ("selector", "values", "expected"),
    [
        (AggregationSelector.SUM, (Decimal("2.5"), Decimal("-1.25"), Decimal("4.75")), Decimal("6.00")),
        (AggregationSelector.AVERAGE, (Decimal("2.5"), Decimal("-1.25"), Decimal("4.75")), Decimal("2.00")),
        (AggregationSelector.MINIMUM, (Decimal("2.5"), Decimal("-1.25"), Decimal("4.75")), Decimal("-1.25")),
        (AggregationSelector.MAXIMUM, (Decimal("2.5"), Decimal("-1.25"), Decimal("4.75")), Decimal("4.75")),
        (AggregationSelector.AVERAGE, (Decimal("0.275"), Decimal("0.279"), Decimal("0.230")), Decimal("0.2613333333333333333333333333333333")),
    ],
)
def test_supported_decimal_aggregations(
    selector: AggregationSelector,
    values: tuple[Decimal, ...],
    expected: Decimal,
) -> None:
    unit = "percent" if all(abs(value) < 1 for value in values) else "million"
    dimension = "ratio" if unit == "percent" else "currency"
    series = _series(
        values,
        items=tuple(
            _item(index, value, unit=unit, dimension=dimension)
            for index, value in enumerate(values)
        ),
    )
    result = SourceBoundNumericSeriesAggregator().execute(_request(selector, series=series))

    assert result.ok is True
    assert result.value == expected
    assert [ref.source for ref in result.source_refs] == [series.source_object_id] * len(values)


def test_multi_selector_reuses_one_series_and_composes_with_subtraction() -> None:
    values = (Decimal("0.114"), Decimal("0.078"), Decimal("0.074"))
    series = _series(
        values,
        items=tuple(
            _item(index, value, unit="percent", dimension="ratio")
            for index, value in enumerate(values)
        ),
    )
    request = SourceBoundNumericSeriesAggregationRequest(
        series=series,
        selectors=(AggregationSelector.AVERAGE, AggregationSelector.MAXIMUM),
        output=SeriesAggregationOutputSpec(
            operation=AggregationOutputOperation.SUBTRACT,
            operands=(AggregationSelector.MAXIMUM, AggregationSelector.AVERAGE),
        ),
        question_aggregation_match=ExecutionGateFact(True),
    )
    compiler = SourceBoundNumericSeriesAggregationCompiler()
    first = compiler.compile(request)
    second = compiler.compile(request)

    assert first.program is not None
    assert first.program.to_dict() == second.program.to_dict()
    assert list(first.bindings) == ["series_item_0001", "series_item_0002", "series_item_0003"]
    assert first.selector_output_refs == second.selector_output_refs
    assert first.program.metadata["source_coordinates"] == [
        item.source_coordinate for item in series.items
    ]

    result = SourceBoundNumericSeriesAggregator().execute(request)
    assert result.ok is True
    assert result.value == Decimal("0.02533333333333333333333333333333333")
    assert [step["op"] for step in result.trace] == ["add", "add", "divide", "max", "subtract"]
    assert result.trace[-1]["args"] == ["#4", "#3"]


def test_contracts_are_immutable_and_sequences_are_frozen_to_tuples() -> None:
    mutable_items = [_item(0, Decimal("7"))]
    series = _series(items=mutable_items)
    request = SourceBoundNumericSeriesAggregationRequest(
        series=series,
        selectors=[AggregationSelector.SUM],
        output=SeriesAggregationOutputSpec(
            operation=AggregationOutputOperation.SELECTOR,
            operands=[AggregationSelector.SUM],
        ),
        question_aggregation_match=ExecutionGateFact(True),
    )

    mutable_items.append(_item(1, Decimal("8")))
    assert isinstance(series.items, tuple)
    assert len(series.items) == 1
    assert isinstance(request.selectors, tuple)
    assert isinstance(request.output.operands, tuple)
    with pytest.raises(FrozenInstanceError):
        series.metric = "changed"  # type: ignore[misc]


def test_one_item_series_is_supported_for_all_selectors() -> None:
    for selector in AggregationSelector:
        assert _execute(selector, (Decimal("7.25"),)) == Decimal("7.25")


def test_empty_series_fails_closed() -> None:
    result = SourceBoundNumericSeriesAggregator().execute(
        _request(AggregationSelector.SUM, series=_series(items=()))
    )
    assert result.ok is False
    assert result.error == EMPTY_SERIES
    assert result.audit_reasons == (EMPTY_SERIES,)


@pytest.mark.parametrize(
    ("series", "reason"),
    [
        (_series(items=(_item(0, Decimal("1"), source_ref=None),)), MISSING_LINEAGE),
        (_series(items=(_item(0, Decimal("1"), coordinate="same"), _item(1, Decimal("2"), coordinate="same"))), DUPLICATE_COORDINATE),
        (_series(items=(_item(0, Decimal("1")), _item(1, Decimal("2"), source_object_id="document://other/table/9"))), CROSS_SOURCE_SERIES),
        (_series(items=(_item(0, Decimal("1"), unit="million"), _item(1, Decimal("2"), unit="percent", dimension="ratio"))), MIXED_OR_AMBIGUOUS_UNIT),
        (_series(values=("abc",)), NON_NUMERIC_ITEM),
        (_series(values=(Decimal("NaN"),)), NON_FINITE_ITEM),
        (_series(binding_status=SourceSeriesBindingStatus.AMBIGUOUS), AMBIGUOUS_AGGREGATION_RANGE),
        (_series(aggregation_range_explicit=False), AMBIGUOUS_AGGREGATION_RANGE),
        (_series(total_components_ambiguity=True), AMBIGUOUS_AGGREGATION_RANGE),
    ],
)
def test_invalid_series_conditions_fail_closed(
    series: SourceBoundNumericSeries,
    reason: str,
) -> None:
    result = SourceBoundNumericSeriesAggregator().execute(
        _request(AggregationSelector.SUM, series=series)
    )
    assert result.ok is False
    assert reason in result.audit_reasons


def test_unsupported_selector_and_label_output_fail_closed() -> None:
    unsupported = SourceBoundNumericSeriesAggregator().execute(_request("MEDIAN"))
    label = SourceBoundNumericSeriesAggregator().execute(
        _request(AggregationSelector.MAXIMUM, output_kind="LABEL")
    )

    assert unsupported.ok is False
    assert UNSUPPORTED_AGGREGATION in unsupported.audit_reasons
    assert label.ok is False
    assert LABEL_OUTPUT_NOT_SUPPORTED in label.audit_reasons


@pytest.mark.parametrize("question_match", [False, None])
def test_false_or_unknown_question_match_blocks_execution(question_match: bool | None) -> None:
    result = SourceBoundNumericSeriesAggregator().execute(
        _request(AggregationSelector.AVERAGE, question_match=question_match)
    )

    assert result.ok is False
    assert result.error == "deterministic_execution_not_ready"
    assert QUESTION_AGGREGATION_MISMATCH in result.audit_reasons
    assert result.gate_status == "NOT_READY"


def test_binding_and_trace_preserve_source_lineage() -> None:
    request = _request(AggregationSelector.SUM)
    compiled = SourceBoundNumericSeriesAggregationCompiler().compile(request)
    assert compiled.program is not None

    for variable, item in zip(compiled.bindings.values(), request.series.items, strict=True):
        assert variable.source_ref == item.source_ref
        assert variable.source_coordinate == item.source_coordinate
        assert variable.source_object_id == item.source_object_id
        assert variable.period == item.header_label
        assert variable.dimension == item.dimension
    result = SourceBoundNumericSeriesAggregator().execute(request)
    assert result.ok is True
    assert result.formula_program is not None
    assert result.formula_program.metadata["source_coordinates"] == [
        item.source_coordinate for item in request.series.items
    ]
    assert result.trace[0]["args"] == ["series_item_0001", "series_item_0002"]


def test_product_contract_has_no_benchmark_or_expected_output_fields() -> None:
    product_types = (
        SourceBoundNumericSeriesItem,
        SourceBoundNumericSeries,
        SeriesAggregationOutputSpec,
        SourceBoundNumericSeriesAggregationRequest,
    )
    field_names = {item.name.lower() for product_type in product_types for item in fields(product_type)}
    forbidden = {"benchmark", "dataset", "case_id", "qid", "gold", "answer", "expected_output"}
    assert not (field_names & forbidden)

    source = Path("src/calculation/series_aggregation.py").read_text(encoding="utf-8").lower()
    assert "finqa" not in source
    assert "tatqa" not in source


def _assert_malformed_series_fails_closed(
    series: SourceBoundNumericSeries,
    expected_reason: str,
) -> None:
    request = _request(AggregationSelector.SUM, series=series)

    compiled = SourceBoundNumericSeriesAggregationCompiler().compile(request)
    result = SourceBoundNumericSeriesAggregator().execute(request)

    assert compiled.program is None
    assert expected_reason in compiled.reasons
    assert result.ok is False
    assert result.value is None
    assert result.formula_program is None
    assert result.gate_status == "NOT_READY"
    assert expected_reason in result.audit_reasons


@pytest.mark.parametrize(
    ("series", "expected_reason"),
    [
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), source_ref=object()),),
            ),
            MISSING_LINEAGE,
        ),
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), source_ref={}),),
            ),
            MISSING_LINEAGE,
        ),
        (
            replace(_series(), items=(object(),)),
            INVALID_SERIES_MEMBER,
        ),
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), position=True),),
            ),
            AMBIGUOUS_AGGREGATION_RANGE,
        ),
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), position=0.0),),
            ),
            AMBIGUOUS_AGGREGATION_RANGE,
        ),
        (
            replace(_series(), binding_status="EXACT"),
            AMBIGUOUS_AGGREGATION_RANGE,
        ),
        (
            replace(_series(), aggregation_range_explicit="true"),
            AMBIGUOUS_AGGREGATION_RANGE,
        ),
        (
            replace(_series(), total_components_ambiguity=0),
            AMBIGUOUS_AGGREGATION_RANGE,
        ),
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), unit=None),),
            ),
            MIXED_OR_AMBIGUOUS_UNIT,
        ),
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), dimension=None),),
            ),
            MIXED_OR_AMBIGUOUS_UNIT,
        ),
        (
            replace(
                _series(),
                items=(replace(_item(0, Decimal("1")), source_coordinate=None),),
            ),
            MISSING_LINEAGE,
        ),
        (replace(_series(), series_id=None), MISSING_LINEAGE),
        (replace(_series(), metric=None), MISSING_LINEAGE),
        (replace(_series(), entity=None), MISSING_LINEAGE),
        (replace(_series(), source_object_id=None), MISSING_LINEAGE),
    ],
    ids=(
        "source-ref-object",
        "source-ref-dict",
        "plain-object-member",
        "boolean-position",
        "float-position",
        "string-binding-status",
        "string-range-flag",
        "numeric-ambiguity-flag",
        "none-unit",
        "none-dimension",
        "none-coordinate",
        "none-series-id",
        "none-metric",
        "none-entity",
        "none-series-source-object",
    ),
)
def test_malformed_series_inputs_fail_closed_without_exception_escape(
    series: SourceBoundNumericSeries,
    expected_reason: str,
) -> None:
    _assert_malformed_series_fails_closed(series, expected_reason)


def test_none_and_non_string_member_lineage_fails_closed() -> None:
    valid_ref = FormulaSourceRef(
        doc_id="document-demo",
        page_number=1,
        source="document://demo/table/1",
        block_id="table-1",
    )
    malformed = (
        replace(_item(0, Decimal("1")), source_object_id=None),
        replace(_item(0, Decimal("1")), source_ref=replace(valid_ref, doc_id=None)),
        replace(_item(0, Decimal("1")), source_ref=replace(valid_ref, source=None)),
        replace(_item(0, Decimal("1")), unit=1),
        replace(_item(0, Decimal("1")), dimension=object()),
        replace(_item(0, Decimal("1")), source_coordinate=7),
    )

    for item in malformed:
        _assert_malformed_series_fails_closed(
            replace(_series(), items=(item,)),
            MISSING_LINEAGE
            if item.source_object_id is None
            or not isinstance(item.source_coordinate, str)
            or not isinstance(item.source_ref, FormulaSourceRef)
            or not isinstance(item.source_ref.doc_id, str)
            or not isinstance(item.source_ref.source, str)
            else MIXED_OR_AMBIGUOUS_UNIT,
        )


def test_non_string_series_lineage_fields_fail_closed() -> None:
    for field_name, value in (
        ("series_id", 1),
        ("metric", object()),
        ("entity", False),
        ("source_object_id", []),
    ):
        _assert_malformed_series_fails_closed(
            replace(_series(), **{field_name: value}),
            MISSING_LINEAGE,
        )
