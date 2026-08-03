"""Dataset-agnostic source-bound table section/entity cardinality execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from calculation.contracts import (
    CalculationExecutionResult,
    ExecutionGateFact,
    FormulaSourceRef,
    SourceBoundTableMember,
    SourceBoundTableMemberCollection,
    SourceBoundTableSectionCardinalityRequest,
    SourceSeriesBindingStatus,
    TableSectionAxisType,
)

EMPTY_COLLECTION = "EMPTY_COLLECTION"
INVALID_COLLECTION = "INVALID_COLLECTION"
INVALID_COLLECTION_MEMBER = "INVALID_COLLECTION_MEMBER"
MISSING_LINEAGE = "MISSING_LINEAGE"
DUPLICATE_COORDINATE = "DUPLICATE_COORDINATE"
CROSS_SOURCE_COLLECTION = "CROSS_SOURCE_COLLECTION"
AMBIGUOUS_COLLECTION_RANGE = "AMBIGUOUS_COLLECTION_RANGE"
UNSUPPORTED_AXIS_TYPE = "UNSUPPORTED_AXIS_TYPE"
QUESTION_CARDINALITY_MISMATCH = "QUESTION_CARDINALITY_MISMATCH"


@dataclass(frozen=True)
class SectionCardinalityValidationResult:
    """Auditable validation output before deterministic member counting."""

    ready: bool
    reasons: Sequence[str] = field(default_factory=tuple)
    source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)
    metadata: Mapping[str, object] = field(default_factory=dict)


class SourceBoundTableSectionCardinalityCounter:
    """Validate and count one explicit source-bound table member collection."""

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
        request: SourceBoundTableSectionCardinalityRequest,
    ) -> SectionCardinalityValidationResult:
        if not isinstance(request, SourceBoundTableSectionCardinalityRequest):
            return SectionCardinalityValidationResult(
                ready=False,
                reasons=(INVALID_COLLECTION,),
            )

        collection = request.collection
        if not isinstance(collection, SourceBoundTableMemberCollection):
            return SectionCardinalityValidationResult(
                ready=False,
                reasons=(INVALID_COLLECTION,),
            )

        members_raw = collection.members
        if not isinstance(members_raw, tuple):
            return SectionCardinalityValidationResult(
                ready=False,
                reasons=(INVALID_COLLECTION_MEMBER,),
            )
        members = members_raw
        if not members:
            return SectionCardinalityValidationResult(
                ready=False,
                reasons=(EMPTY_COLLECTION,),
            )

        reasons: list[str] = []
        if not self._non_empty_text(collection.collection_id):
            reasons.append(MISSING_LINEAGE)
        if not self._non_empty_text(collection.source_object_id):
            reasons.append(MISSING_LINEAGE)
        if not isinstance(collection.axis_type, TableSectionAxisType):
            reasons.append(UNSUPPORTED_AXIS_TYPE)
        if collection.binding_status is not SourceSeriesBindingStatus.EXACT:
            reasons.append(AMBIGUOUS_COLLECTION_RANGE)
        if (
            collection.range_explicit is not True
            or collection.boundary_rows_excluded is not True
        ):
            reasons.append(AMBIGUOUS_COLLECTION_RANGE)

        coordinates: list[str] = []
        source_refs: list[FormulaSourceRef] = []
        for expected_position, member in enumerate(members):
            if not isinstance(member, SourceBoundTableMember):
                reasons.append(INVALID_COLLECTION_MEMBER)
                continue
            if type(member.position) is not int or member.position != expected_position:
                reasons.append(AMBIGUOUS_COLLECTION_RANGE)
            if not self._non_empty_text(member.member_label):
                reasons.append(INVALID_COLLECTION_MEMBER)

            coordinate_valid = self._non_empty_text(member.source_coordinate)
            source_object_valid = self._non_empty_text(member.source_object_id)
            source_ref_valid = self._valid_source_ref(member.source_ref)
            if not coordinate_valid or not source_object_valid or not source_ref_valid:
                reasons.append(MISSING_LINEAGE)
            if coordinate_valid:
                coordinates.append(member.source_coordinate)
            if source_ref_valid:
                source_refs.append(member.source_ref)

            if (
                source_object_valid
                and self._non_empty_text(collection.source_object_id)
                and member.source_object_id != collection.source_object_id
            ):
                reasons.append(CROSS_SOURCE_COLLECTION)
            if (
                source_ref_valid
                and self._non_empty_text(collection.source_object_id)
                and member.source_ref.source != collection.source_object_id
            ):
                reasons.append(CROSS_SOURCE_COLLECTION)

        if len(coordinates) != len(set(coordinates)):
            reasons.append(DUPLICATE_COORDINATE)

        question_match = request.question_cardinality_match
        if (
            not isinstance(question_match, ExecutionGateFact)
            or question_match.passed is not True
        ):
            reasons.append(QUESTION_CARDINALITY_MISMATCH)

        unique_reasons = self._unique(reasons)
        return SectionCardinalityValidationResult(
            ready=not unique_reasons,
            reasons=unique_reasons,
            source_refs=tuple(source_refs),
            metadata={
                "member_count": len(members),
                "axis_type": (
                    collection.axis_type.value
                    if isinstance(collection.axis_type, TableSectionAxisType)
                    else ""
                ),
            },
        )

    def execute(
        self,
        request: SourceBoundTableSectionCardinalityRequest,
    ) -> CalculationExecutionResult:
        validation = self.validate(request)
        if not validation.ready:
            return CalculationExecutionResult(
                ok=False,
                value=None,
                error=validation.reasons[0] if validation.reasons else INVALID_COLLECTION,
                formula_program=None,
                gate_status="NOT_READY",
                audit_reasons=tuple(validation.reasons),
                source_refs=tuple(validation.source_refs),
            )

        collection = request.collection
        assert isinstance(collection, SourceBoundTableMemberCollection)
        assert isinstance(collection.axis_type, TableSectionAxisType)
        trace: list[dict[str, object]] = []
        for member in collection.members:
            assert isinstance(member, SourceBoundTableMember)
            trace.append(
                {
                    "trace_type": "table_member",
                    "position": member.position,
                    "member_label": member.member_label,
                    "source_coordinate": member.source_coordinate,
                    "source_object_id": member.source_object_id,
                    "axis_type": collection.axis_type.value,
                }
            )
        member_count = len(collection.members)
        trace.append(
            {
                "trace_type": "section_cardinality_summary",
                "collection_id": collection.collection_id,
                "axis_type": collection.axis_type.value,
                "member_count": member_count,
            }
        )
        return CalculationExecutionResult(
            ok=True,
            value=member_count,
            display_value=str(member_count),
            trace=tuple(trace),
            formula_program=None,
            gate_status="PASS",
            audit_reasons=(),
            source_refs=tuple(validation.source_refs),
        )


__all__ = [
    "AMBIGUOUS_COLLECTION_RANGE",
    "CROSS_SOURCE_COLLECTION",
    "DUPLICATE_COORDINATE",
    "EMPTY_COLLECTION",
    "INVALID_COLLECTION",
    "INVALID_COLLECTION_MEMBER",
    "MISSING_LINEAGE",
    "QUESTION_CARDINALITY_MISMATCH",
    "SectionCardinalityValidationResult",
    "SourceBoundTableSectionCardinalityCounter",
    "UNSUPPORTED_AXIS_TYPE",
]
