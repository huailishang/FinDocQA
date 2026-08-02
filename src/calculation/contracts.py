"""Reusable contracts for C3 deterministic calculation.

The core deliberately separates four concerns:

1. material/formula evidence;
2. variable binding with lineage;
3. a small auditable formula program;
4. deterministic execution result and trace.

No benchmark id, provider response, or expected answer belongs in these types.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence


class FormulaGateStatus(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


@dataclass(frozen=True)
class FormulaSourceRef:
    doc_id: str
    page_number: int | None
    source: str
    block_id: str = ""
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoundVariable:
    name: str
    value: Decimal
    unit: str = ""
    source_ref: FormulaSourceRef | None = None
    metric: str = ""
    entity: str = ""
    period: str = ""
    definition: str = ""
    confidence: str = "exact"
    source_coordinate: str = ""
    source_object_id: str = ""
    dimension: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["value"] = str(self.value)
        return payload


class SemanticBindingStatus(str, Enum):
    """Terminal outcomes for deterministic semantic variable binding."""

    BOUND = "bound"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INCOMPATIBLE_UNIT = "incompatible_unit"
    LINEAGE_INVALID = "lineage_invalid"


@dataclass(frozen=True)
class SemanticBindingRequest:
    """The dimensions a formula variable must match without inference."""

    name: str
    metric: str
    entity: str
    period: str
    unit: str
    document_id: str


@dataclass(frozen=True)
class SemanticBindingCandidate:
    """One candidate value with its semantic dimensions and immutable lineage."""

    value: str | int | float | Decimal
    metric: str
    entity: str
    period: str
    unit: str
    document_id: str
    source_ref: FormulaSourceRef | None


@dataclass(frozen=True)
class SemanticBindingResult:
    """Auditable result; unresolved states are ordinary data, not exceptions."""

    status: SemanticBindingStatus
    bound: BoundVariable | None = None
    reasons: Sequence[str] = field(default_factory=tuple)
    candidate_count: int = 0


@dataclass(frozen=True)
class ExecutionGateFact:
    """One explicit PASS fact required before deterministic execution."""

    passed: bool | None
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DeterministicExecutionGateInput:
    """The three independent facts required to authorize FormulaProgram execution."""

    formula_evidence: ExecutionGateFact
    semantic_binding: ExecutionGateFact
    question_formula_match: ExecutionGateFact


@dataclass(frozen=True)
class DeterministicExecutionGateResult:
    """Auditable decision for the deterministic execution boundary."""

    ready: bool
    failed_gates: Sequence[str] = field(default_factory=tuple)
    reasons: Sequence[str] = field(default_factory=tuple)

class SourceSeriesBindingStatus(str, Enum):
    """Binding status for one immutable source-backed numeric series."""

    EXACT = "EXACT"
    AMBIGUOUS = "AMBIGUOUS"
    UNBOUND = "UNBOUND"


class AggregationSelector(str, Enum):
    """Supported scalar aggregation selectors."""

    AVERAGE = "AVERAGE"
    MINIMUM = "MINIMUM"
    MAXIMUM = "MAXIMUM"
    SUM = "SUM"


class AggregationOutputOperation(str, Enum):
    """How compiled aggregation outputs form the final scalar result."""

    SELECTOR = "SELECTOR"
    SUBTRACT = "SUBTRACT"


@dataclass(frozen=True)
class SourceBoundNumericSeriesItem:
    """One ordered numeric fact with immutable source lineage."""

    position: int
    value: Decimal
    unit: str
    dimension: str
    source_ref: FormulaSourceRef | None
    source_coordinate: str
    source_object_id: str
    header_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["value"] = str(self.value)
        return payload


@dataclass(frozen=True)
class SourceBoundNumericSeries:
    """A reusable ordered numeric series bound to one explicit source object."""

    series_id: str
    items: Sequence[SourceBoundNumericSeriesItem]
    metric: str
    entity: str
    source_object_id: str
    binding_status: SourceSeriesBindingStatus
    aggregation_range_explicit: bool
    total_components_ambiguity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "items": [item.to_dict() for item in self.items],
            "metric": self.metric,
            "entity": self.entity,
            "source_object_id": self.source_object_id,
            "binding_status": self.binding_status.value,
            "aggregation_range_explicit": self.aggregation_range_explicit,
            "total_components_ambiguity": self.total_components_ambiguity,
        }


@dataclass(frozen=True)
class SeriesAggregationOutputSpec:
    """Final scalar output built from one or two selector outputs."""

    operation: AggregationOutputOperation | str
    operands: Sequence[AggregationSelector | str]
    output_kind: str = "SCALAR"
    output_semantics: str = "number"

    def __post_init__(self) -> None:
        object.__setattr__(self, "operands", tuple(self.operands))


@dataclass(frozen=True)
class SourceBoundNumericSeriesAggregationRequest:
    """Dataset-agnostic request for deterministic source-series aggregation."""

    series: SourceBoundNumericSeries
    selectors: Sequence[AggregationSelector | str]
    output: SeriesAggregationOutputSpec
    question_aggregation_match: ExecutionGateFact

    def __post_init__(self) -> None:
        object.__setattr__(self, "selectors", tuple(self.selectors))




class TablePredicateOperator(str, Enum):
    """Strict scalar predicates supported by table-cardinality counting."""

    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"


@dataclass(frozen=True)
class SourceBoundTablePredicateCardinalityRequest:
    """Dataset-agnostic request to count members satisfying one strict predicate."""

    collection: SourceBoundNumericSeries
    operator: TablePredicateOperator | str
    threshold: Decimal
    threshold_unit: str
    threshold_dimension: str
    question_predicate_match: ExecutionGateFact


@dataclass(frozen=True)
class FormulaEvidence:
    raw_formula: str
    normalized_expression: str
    context_text: str = ""
    variable_definitions: Mapping[str, str] = field(default_factory=dict)
    conditions: Sequence[str] = field(default_factory=tuple)
    source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)
    linked_table_refs: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_formula": self.raw_formula,
            "normalized_expression": self.normalized_expression,
            "context_text": self.context_text,
            "variable_definitions": dict(self.variable_definitions),
            "conditions": list(self.conditions),
            "source_refs": [item.to_dict() for item in self.source_refs],
            "linked_table_refs": list(self.linked_table_refs),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class FormulaGateResult:
    status: FormulaGateStatus
    reasons: Sequence[str] = field(default_factory=tuple)
    referenced_variables: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reasons": list(self.reasons),
            "referenced_variables": list(self.referenced_variables),
        }


@dataclass(frozen=True)
class FormulaStep:
    output: str
    op: str
    args: Sequence[str]

    def to_dict(self) -> dict[str, Any]:
        return {"output": self.output, "op": self.op, "args": list(self.args)}


@dataclass(frozen=True)
class FormulaProgram:
    formula_id: str
    steps: Sequence[FormulaStep]
    output_ref: str
    output_semantics: str = "number"
    source_type: str = "builtin"
    source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "steps": [item.to_dict() for item in self.steps],
            "output_ref": self.output_ref,
            "output_semantics": self.output_semantics,
            "source_type": self.source_type,
            "source_refs": [item.to_dict() for item in self.source_refs],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CalculationExecutionResult:
    ok: bool
    value: Any = None
    display_value: str = ""
    error: str = ""
    trace: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    formula_program: FormulaProgram | None = None
    gate_status: str = ""
    audit_reasons: Sequence[str] = field(default_factory=tuple)
    source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        value = self.value
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        return {
            "ok": self.ok,
            "value": value,
            "display_value": self.display_value,
            "error": self.error,
            "trace": [dict(item) for item in self.trace],
            "formula_program": self.formula_program.to_dict() if self.formula_program else None,
            "gate_status": self.gate_status,
            "audit_reasons": list(self.audit_reasons),
            "source_refs": [item.to_dict() for item in self.source_refs],
        }
