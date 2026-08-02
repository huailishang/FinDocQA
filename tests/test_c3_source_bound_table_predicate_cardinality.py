from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import pytest

from calculation import (
    ExecutionGateFact,
    FormulaSourceRef,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesItem,
    SourceBoundTablePredicateCardinalityCounter,
    SourceBoundTablePredicateCardinalityRequest,
    SourceSeriesBindingStatus,
    TablePredicateOperator,
)
from calculation.predicate_cardinality import (
    AMBIGUOUS_COLLECTION_RANGE,
    CROSS_SOURCE_COLLECTION,
    DUPLICATE_COORDINATE,
    EMPTY_COLLECTION,
    INVALID_COLLECTION_MEMBER,
    INVALID_THRESHOLD,
    MISSING_LINEAGE,
    MIXED_OR_AMBIGUOUS_UNIT,
    NON_FINITE_ITEM,
    NON_NUMERIC_ITEM,
    QUESTION_PREDICATE_MISMATCH,
    THRESHOLD_UNIT_MISMATCH,
    UNSUPPORTED_PREDICATE,
)

SOURCE = "document://demo/table/1"


def _item(
    position: int,
    value: Decimal | object,
    *,
    unit: str = "million",
    dimension: str = "currency",
    coordinate: str | None = None,
    source_object_id: str = SOURCE,
    source_ref: object = ...,
    label: str | None = None,
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
        source_coordinate=coordinate or f"{SOURCE}/r1c{position + 1}",
        source_object_id=source_object_id,
        header_label=label or f"member-{position + 1}",
    )


def _collection(
    values: tuple[Decimal | object, ...] = (
        Decimal("25"),
        Decimal("50"),
        Decimal("75"),
    ),
    **kwargs: object,
) -> SourceBoundNumericSeries:
    items = kwargs.pop(
        "items",
        tuple(_item(index, value) for index, value in enumerate(values)),
    )
    return SourceBoundNumericSeries(
        series_id=kwargs.pop("series_id", "collection-demo"),  # type: ignore[arg-type]
        items=items,  # type: ignore[arg-type]
        metric=kwargs.pop("metric", "revenue"),  # type: ignore[arg-type]
        entity=kwargs.pop("entity", "business unit"),  # type: ignore[arg-type]
        source_object_id=kwargs.pop("source_object_id", SOURCE),  # type: ignore[arg-type]
        binding_status=kwargs.pop(
            "binding_status", SourceSeriesBindingStatus.EXACT
        ),  # type: ignore[arg-type]
        aggregation_range_explicit=kwargs.pop(
            "aggregation_range_explicit", True
        ),  # type: ignore[arg-type]
        total_components_ambiguity=kwargs.pop(
            "total_components_ambiguity", False
        ),  # type: ignore[arg-type]
    )


def _request(
    operator: TablePredicateOperator | object = TablePredicateOperator.GREATER_THAN,
    *,
    collection: SourceBoundNumericSeries | object | None = None,
    threshold: Decimal | object = Decimal("50"),
    threshold_unit: object = "million",
    threshold_dimension: object = "currency",
    question_match: bool | None = True,
) -> SourceBoundTablePredicateCardinalityRequest:
    return SourceBoundTablePredicateCardinalityRequest(
        collection=collection if collection is not None else _collection(),  # type: ignore[arg-type]
        operator=operator,  # type: ignore[arg-type]
        threshold=threshold,  # type: ignore[arg-type]
        threshold_unit=threshold_unit,  # type: ignore[arg-type]
        threshold_dimension=threshold_dimension,  # type: ignore[arg-type]
        question_predicate_match=ExecutionGateFact(question_match),
    )


def _blocked(
    request: SourceBoundTablePredicateCardinalityRequest,
    reason: str,
) -> None:
    result = SourceBoundTablePredicateCardinalityCounter().execute(request)
    assert result.ok is False
    assert result.value is None
    assert result.formula_program is None
    assert result.gate_status == "NOT_READY"
    assert reason in result.audit_reasons


@pytest.mark.parametrize(
    ("operator", "values", "threshold", "expected"),
    [
        (TablePredicateOperator.GREATER_THAN, (Decimal("25"),), Decimal("20"), 1),
        (TablePredicateOperator.GREATER_THAN, (Decimal("25"), Decimal("50")), Decimal("50"), 0),
        (TablePredicateOperator.GREATER_THAN, (Decimal("25"), Decimal("50"), Decimal("75")), Decimal("50"), 1),
        (TablePredicateOperator.LESS_THAN, (Decimal("25"), Decimal("50"), Decimal("75")), Decimal("50"), 1),
        (TablePredicateOperator.GREATER_THAN, (Decimal("-3.5"), Decimal("-1"), Decimal("0.1")), Decimal("-2"), 2),
        (TablePredicateOperator.LESS_THAN, (Decimal("-3.5"), Decimal("-1"), Decimal("0.1")), Decimal("1"), 3),
    ],
)
def test_strict_predicates_return_deterministic_non_negative_integer(
    operator: TablePredicateOperator,
    values: tuple[Decimal, ...],
    threshold: Decimal,
    expected: int,
) -> None:
    result = SourceBoundTablePredicateCardinalityCounter().execute(
        _request(operator, collection=_collection(values), threshold=threshold)
    )
    assert result.ok is True
    assert result.value == expected
    assert type(result.value) is int
    assert result.value >= 0
    assert result.display_value == str(expected)
    assert result.gate_status == "PASS"
    assert len(result.trace) == len(values) + 1
    assert [row["position"] for row in result.trace[:-1]] == list(range(len(values)))
    assert result.trace[-1]["total_member_count"] == len(values)
    assert result.trace[-1]["matched_count"] == expected


def test_fifteen_member_thousand_collection_has_stable_source_trace() -> None:
    values = tuple(Decimal(str(value)) for value in range(1, 16))
    items = tuple(
        _item(index, value, unit="thousand", label=f"category-{index + 1}")
        for index, value in enumerate(values)
    )
    request = _request(
        collection=_collection(items=items),
        threshold=Decimal("10"),
        threshold_unit="thousand",
    )
    first = SourceBoundTablePredicateCardinalityCounter().execute(request)
    second = SourceBoundTablePredicateCardinalityCounter().execute(request)

    assert first.ok is True
    assert first.value == 5
    assert first.to_dict() == second.to_dict()
    assert [row["source_coordinate"] for row in first.trace[:-1]] == [
        item.source_coordinate for item in items
    ]
    assert [row["member_label"] for row in first.trace[:-1]] == [
        item.header_label for item in items
    ]
    assert all(row["threshold"] == "10" for row in first.trace[:-1])
    assert all(row["operator"] == ">" for row in first.trace[:-1])


def test_threshold_equal_to_member_is_not_a_strict_match() -> None:
    greater = SourceBoundTablePredicateCardinalityCounter().execute(
        _request(
            TablePredicateOperator.GREATER_THAN,
            collection=_collection((Decimal("50"),)),
            threshold=Decimal("50"),
        )
    )
    less = SourceBoundTablePredicateCardinalityCounter().execute(
        _request(
            TablePredicateOperator.LESS_THAN,
            collection=_collection((Decimal("50"),)),
            threshold=Decimal("50"),
        )
    )
    assert greater.value == 0
    assert less.value == 0


@pytest.mark.parametrize(
    ("predicate_request", "reason"),
    [
        (_request(collection=_collection(items=())), EMPTY_COLLECTION),
        (_request(collection=object()), INVALID_COLLECTION_MEMBER),
        (_request(collection=_collection(items=(object(),))), INVALID_COLLECTION_MEMBER),
        (_request(collection=_collection(values=("abc",))), NON_NUMERIC_ITEM),
        (_request(collection=_collection(values=(Decimal("NaN"),))), NON_FINITE_ITEM),
        (_request(collection=_collection(items=(_item(0, Decimal("1"), source_ref=object()),))), MISSING_LINEAGE),
        (_request(collection=_collection(items=(_item(0, Decimal("1"), source_ref={}),))), MISSING_LINEAGE),
        (_request(collection=replace(_collection(), series_id=None)), MISSING_LINEAGE),
        (_request(collection=replace(_collection(), metric=None)), MISSING_LINEAGE),
        (_request(collection=replace(_collection(), entity=None)), MISSING_LINEAGE),
        (_request(collection=replace(_collection(), source_object_id=None)), MISSING_LINEAGE),
        (_request(collection=_collection(items=(replace(_item(0, Decimal("1")), source_coordinate=None),))), MISSING_LINEAGE),
        (_request(collection=_collection(items=(replace(_item(0, Decimal("1")), source_object_id=None),))), MISSING_LINEAGE),
        (_request(collection=_collection(items=(_item(0, Decimal("1"), coordinate="same"), _item(1, Decimal("2"), coordinate="same")))), DUPLICATE_COORDINATE),
        (_request(collection=_collection(items=(_item(0, Decimal("1")), _item(1, Decimal("2"), source_object_id="document://other/table/2")))), CROSS_SOURCE_COLLECTION),
        (_request(collection=_collection(items=(_item(0, Decimal("1"), unit="million"), _item(1, Decimal("2"), unit="thousand")))), MIXED_OR_AMBIGUOUS_UNIT),
        (_request(collection=_collection(items=(replace(_item(0, Decimal("1")), position=True),))), AMBIGUOUS_COLLECTION_RANGE),
        (_request(collection=replace(_collection(), binding_status="EXACT")), AMBIGUOUS_COLLECTION_RANGE),
        (_request(collection=replace(_collection(), aggregation_range_explicit="true")), AMBIGUOUS_COLLECTION_RANGE),
        (_request(collection=replace(_collection(), total_components_ambiguity=0)), AMBIGUOUS_COLLECTION_RANGE),
        (_request(threshold=None), INVALID_THRESHOLD),
        (_request(threshold=Decimal("NaN")), INVALID_THRESHOLD),
        (_request(threshold=Decimal("Infinity")), INVALID_THRESHOLD),
        (_request(threshold_unit="thousand"), THRESHOLD_UNIT_MISMATCH),
        (_request(threshold_dimension="ratio"), THRESHOLD_UNIT_MISMATCH),
        (_request(threshold_unit=None), THRESHOLD_UNIT_MISMATCH),
        (_request(operator=">"), UNSUPPORTED_PREDICATE),
        (_request(operator=">="), UNSUPPORTED_PREDICATE),
        (_request(operator="<="), UNSUPPORTED_PREDICATE),
        (_request(operator="GREATER_THAN"), UNSUPPORTED_PREDICATE),
        (_request(operator=None), UNSUPPORTED_PREDICATE),
        (_request(question_match=False), QUESTION_PREDICATE_MISMATCH),
        (_request(question_match=None), QUESTION_PREDICATE_MISMATCH),
    ],
)
def test_invalid_contracts_fail_closed_without_exception_escape(
    predicate_request: SourceBoundTablePredicateCardinalityRequest,
    reason: str,
) -> None:
    _blocked(predicate_request, reason)


def test_product_contract_has_no_dataset_case_or_answer_fields() -> None:
    names = {
        item.name.lower()
        for contract in (
            SourceBoundNumericSeriesItem,
            SourceBoundNumericSeries,
            SourceBoundTablePredicateCardinalityRequest,
        )
        for item in fields(contract)
    }
    forbidden = {"dataset", "benchmark", "case_id", "qid", "gold", "answer", "expected"}
    assert not names.intersection(forbidden)

    source = Path("src/calculation/predicate_cardinality.py").read_text(
        encoding="utf-8"
    ).lower()
    assert "tatqa" not in source
    assert "finqa" not in source
    assert "case_id" not in source
    assert "expected_count" not in source
    assert "except exception" not in source
