"""Dataset-agnostic source-bound table predicate-cardinality execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from calculation.contracts import (
    CalculationExecutionResult,
    ExecutionGateFact,
    FormulaSourceRef,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesItem,
    SourceBoundTablePredicateCardinalityRequest,
    SourceSeriesBindingStatus,
    TablePredicateOperator,
)

EMPTY_COLLECTION = "EMPTY_COLLECTION"
INVALID_COLLECTION_MEMBER = "INVALID_COLLECTION_MEMBER"
NON_NUMERIC_ITEM = "NON_NUMERIC_ITEM"
NON_FINITE_ITEM = "NON_FINITE_ITEM"
MISSING_LINEAGE = "MISSING_LINEAGE"
DUPLICATE_COORDINATE = "DUPLICATE_COORDINATE"
CROSS_SOURCE_COLLECTION = "CROSS_SOURCE_COLLECTION"
MIXED_OR_AMBIGUOUS_UNIT = "MIXED_OR_AMBIGUOUS_UNIT"
AMBIGUOUS_COLLECTION_RANGE = "AMBIGUOUS_COLLECTION_RANGE"
INVALID_THRESHOLD = "INVALID_THRESHOLD"
THRESHOLD_UNIT_MISMATCH = "THRESHOLD_UNIT_MISMATCH"
UNSUPPORTED_PREDICATE = "UNSUPPORTED_PREDICATE"
QUESTION_PREDICATE_MISMATCH = "QUESTION_PREDICATE_MISMATCH"


@dataclass(frozen=True)
class PredicateCardinalityValidationResult:
    """Auditable validation output before deterministic comparison."""

    ready: bool
    reasons: Sequence[str] = field(default_factory=tuple)
    source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)
    metadata: Mapping[str, object] = field(default_factory=dict)


class SourceBoundTablePredicateCardinalityCounter:
    """Validate, compare, count, and trace one explicit source-bound collection."""

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

    @staticmethod
    def _unique(reasons: Sequence[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(reason for reason in reasons if reason))

    def validate(
        self,
        request: SourceBoundTablePredicateCardinalityRequest,
    ) -> PredicateCardinalityValidationResult:
        reasons: list[str] = []
        collection = request.collection
        if not isinstance(collection, SourceBoundNumericSeries):
            return PredicateCardinalityValidationResult(
                ready=False,
                reasons=(INVALID_COLLECTION_MEMBER,),
            )

        items = tuple(collection.items)
        if not items:
            return PredicateCardinalityValidationResult(
                ready=False,
                reasons=(EMPTY_COLLECTION,),
            )

        if not all(
            self._non_empty_text(value)
            for value in (
                collection.series_id,
                collection.metric,
                collection.entity,
                collection.source_object_id,
            )
        ):
            reasons.append(MISSING_LINEAGE)
        if collection.binding_status is not SourceSeriesBindingStatus.EXACT:
            reasons.append(AMBIGUOUS_COLLECTION_RANGE)
        if (
            collection.aggregation_range_explicit is not True
            or collection.total_components_ambiguity is not False
        ):
            reasons.append(AMBIGUOUS_COLLECTION_RANGE)

        coordinates: list[str] = []
        units: set[str] = set()
        dimensions: set[str] = set()
        source_refs: list[FormulaSourceRef] = []
        invalid_unit_or_dimension = False
        for expected_position, item in enumerate(items):
            if not isinstance(item, SourceBoundNumericSeriesItem):
                reasons.append(INVALID_COLLECTION_MEMBER)
                continue
            if type(item.position) is not int or item.position != expected_position:
                reasons.append(AMBIGUOUS_COLLECTION_RANGE)
            if not isinstance(item.value, Decimal):
                reasons.append(NON_NUMERIC_ITEM)
            elif not item.value.is_finite():
                reasons.append(NON_FINITE_ITEM)

            coordinate_valid = self._non_empty_text(item.source_coordinate)
            source_object_valid = self._non_empty_text(item.source_object_id)
            source_ref_valid = self._valid_source_ref(item.source_ref)
            if not coordinate_valid or not source_object_valid or not source_ref_valid:
                reasons.append(MISSING_LINEAGE)
            if coordinate_valid:
                coordinates.append(item.source_coordinate)
            if source_ref_valid:
                source_refs.append(item.source_ref)

            if self._non_empty_text(item.unit):
                units.add(item.unit.strip())
            else:
                invalid_unit_or_dimension = True
            if self._non_empty_text(item.dimension):
                dimensions.add(item.dimension.strip())
            else:
                invalid_unit_or_dimension = True

            if (
                source_object_valid
                and self._non_empty_text(collection.source_object_id)
                and item.source_object_id != collection.source_object_id
            ):
                reasons.append(CROSS_SOURCE_COLLECTION)
            if (
                source_ref_valid
                and self._non_empty_text(collection.source_object_id)
                and item.source_ref.source != collection.source_object_id
            ):
                reasons.append(CROSS_SOURCE_COLLECTION)

        if len(coordinates) != len(set(coordinates)):
            reasons.append(DUPLICATE_COORDINATE)
        if (
            invalid_unit_or_dimension
            or len(units) != 1
            or len(dimensions) != 1
        ):
            reasons.append(MIXED_OR_AMBIGUOUS_UNIT)

        if not isinstance(request.threshold, Decimal) or not request.threshold.is_finite():
            reasons.append(INVALID_THRESHOLD)
        if (
            not self._non_empty_text(request.threshold_unit)
            or not self._non_empty_text(request.threshold_dimension)
        ):
            reasons.append(THRESHOLD_UNIT_MISMATCH)
        elif (
            len(units) != 1
            or len(dimensions) != 1
            or request.threshold_unit.strip() not in units
            or request.threshold_dimension.strip() not in dimensions
        ):
            reasons.append(THRESHOLD_UNIT_MISMATCH)

        if not isinstance(request.operator, TablePredicateOperator):
            reasons.append(UNSUPPORTED_PREDICATE)
        question_match = request.question_predicate_match
        if (
            not isinstance(question_match, ExecutionGateFact)
            or question_match.passed is not True
        ):
            reasons.append(QUESTION_PREDICATE_MISMATCH)

        unique_reasons = self._unique(reasons)
        return PredicateCardinalityValidationResult(
            ready=not unique_reasons,
            reasons=unique_reasons,
            source_refs=tuple(source_refs),
            metadata={
                "member_count": len(items),
                "unit": next(iter(units)) if len(units) == 1 else "",
                "dimension": next(iter(dimensions)) if len(dimensions) == 1 else "",
            },
        )

    def execute(
        self,
        request: SourceBoundTablePredicateCardinalityRequest,
    ) -> CalculationExecutionResult:
        validation = self.validate(request)
        if not validation.ready:
            return CalculationExecutionResult(
                ok=False,
                value=None,
                error=validation.reasons[0] if validation.reasons else INVALID_COLLECTION_MEMBER,
                gate_status="NOT_READY",
                audit_reasons=tuple(validation.reasons),
                source_refs=tuple(validation.source_refs),
            )

        operator = request.operator
        assert isinstance(operator, TablePredicateOperator)
        threshold = request.threshold
        trace: list[dict[str, object]] = []
        matched_count = 0
        for item in request.collection.items:
            assert isinstance(item, SourceBoundNumericSeriesItem)
            matched = (
                item.value > threshold
                if operator is TablePredicateOperator.GREATER_THAN
                else item.value < threshold
            )
            if matched:
                matched_count += 1
            trace.append(
                {
                    "trace_type": "predicate_comparison",
                    "position": item.position,
                    "member_label": item.header_label,
                    "source_coordinate": item.source_coordinate,
                    "source_object_id": item.source_object_id,
                    "value": str(item.value),
                    "unit": item.unit,
                    "dimension": item.dimension,
                    "operator": ">" if operator is TablePredicateOperator.GREATER_THAN else "<",
                    "threshold": str(threshold),
                    "threshold_unit": request.threshold_unit,
                    "threshold_dimension": request.threshold_dimension,
                    "matched": matched,
                }
            )
        trace.append(
            {
                "trace_type": "predicate_cardinality_summary",
                "collection_id": request.collection.series_id,
                "total_member_count": len(request.collection.items),
                "matched_count": matched_count,
                "operator": ">" if operator is TablePredicateOperator.GREATER_THAN else "<",
                "threshold": str(threshold),
                "threshold_unit": request.threshold_unit,
                "threshold_dimension": request.threshold_dimension,
            }
        )
        return CalculationExecutionResult(
            ok=True,
            value=matched_count,
            display_value=str(matched_count),
            trace=tuple(trace),
            gate_status="PASS",
            audit_reasons=(),
            source_refs=tuple(validation.source_refs),
        )


__all__ = [
    "AMBIGUOUS_COLLECTION_RANGE",
    "CROSS_SOURCE_COLLECTION",
    "DUPLICATE_COORDINATE",
    "EMPTY_COLLECTION",
    "INVALID_COLLECTION_MEMBER",
    "INVALID_THRESHOLD",
    "MISSING_LINEAGE",
    "MIXED_OR_AMBIGUOUS_UNIT",
    "NON_FINITE_ITEM",
    "NON_NUMERIC_ITEM",
    "PredicateCardinalityValidationResult",
    "QUESTION_PREDICATE_MISMATCH",
    "SourceBoundTablePredicateCardinalityCounter",
    "THRESHOLD_UNIT_MISMATCH",
    "UNSUPPORTED_PREDICATE",
]
