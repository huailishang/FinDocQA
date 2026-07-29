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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["value"] = str(self.value)
        return payload


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
