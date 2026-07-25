from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence


class RetrievalQuality(str, Enum):
    CORRECT = "CORRECT"
    AMBIGUOUS = "AMBIGUOUS"
    INCORRECT = "INCORRECT"


class OperatorType(str, Enum):
    O1_EXACT_FIELD = "O1_EXACT_FIELD"
    O1_NUMERIC_COMPARISON = "O1_NUMERIC_COMPARISON"
    O1_FORMULA_OR_CALCULATION = "O1_FORMULA_OR_CALCULATION"
    O1_DATE_OR_DEADLINE = "O1_DATE_OR_DEADLINE"
    O2_CROSS_DOCUMENT_OBJECTIVE = "O2_CROSS_DOCUMENT_OBJECTIVE"
    O2_SCOPE_ABSENCE = "O2_SCOPE_ABSENCE"
    O3_SEMANTIC_PROPOSITION = "O3_SEMANTIC_PROPOSITION"
    O4_ANNOTATION_SENSITIVE = "O4_ANNOTATION_SENSITIVE"


@dataclass(frozen=True)
class RequiredAtom:
    name: str
    kind: str
    required: bool = True


@dataclass(frozen=True)
class RetrievalEvidence:
    entity_bound: bool = False
    document_bound: bool = False
    metric_bound: bool = False
    year_or_period_bound: bool = False
    value_or_formula_bound: bool = False
    condition_bound: bool = False
    comparison_operand_count: int = 0
    required_comparison_operands: int = 0
    wrong_entity: bool = False
    wrong_document: bool = False
    wrong_year: bool = False
    wrong_field: bool = False


@dataclass(frozen=True)
class RetrievalQualityDecision:
    quality: RetrievalQuality
    missing_atoms: tuple[str, ...]
    corrective_actions: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["quality"] = self.quality.value
        return payload


def grade_real_retrieval_quality(required_atoms: Sequence[RequiredAtom], evidence: RetrievalEvidence) -> RetrievalQualityDecision:
    """Grade retrieval by required objective atoms, never by lexical overlap alone."""
    if evidence.wrong_entity or evidence.wrong_document or evidence.wrong_year or evidence.wrong_field:
        return RetrievalQualityDecision(
            RetrievalQuality.INCORRECT,
            (),
            ("discard_bad_retrieval", "required_doc_directed_scan"),
            "retrieval binds an explicitly wrong entity/document/year/field",
        )

    flags = {
        "entity": evidence.entity_bound,
        "document_scope": evidence.document_bound,
        "metric_or_clause": evidence.metric_bound,
        "year_or_period": evidence.year_or_period_bound,
        "value_or_formula": evidence.value_or_formula_bound,
        "condition": evidence.condition_bound,
    }
    missing = [atom.name for atom in required_atoms if atom.required and not flags.get(atom.kind, False)]
    if evidence.required_comparison_operands and evidence.comparison_operand_count < evidence.required_comparison_operands:
        missing.append(f"comparison_operands:{evidence.comparison_operand_count}/{evidence.required_comparison_operands}")

    if not missing:
        return RetrievalQualityDecision(RetrievalQuality.CORRECT, (), (), "all required atoms/fields and comparison operands are bound")
    return RetrievalQualityDecision(
        RetrievalQuality.AMBIGUOUS,
        tuple(missing),
        ("query_rewrite", "field_alias_expansion", "structured_table_lookup", "parent_context_expansion", "required_doc_directed_scan"),
        "related material was found but the required objective evidence chain is incomplete",
    )


def operator_type_from_requirements(requirements: Mapping[str, object]) -> OperatorType:
    """Classify by evidence/operator requirement, not surface digits in option text."""
    if requirements.get("annotation_sensitive"):
        return OperatorType.O4_ANNOTATION_SENSITIVE
    if requirements.get("scope_absence"):
        return OperatorType.O2_SCOPE_ABSENCE
    if requirements.get("cross_document"):
        return OperatorType.O2_CROSS_DOCUMENT_OBJECTIVE
    if requirements.get("formula") or requirements.get("calculation"):
        return OperatorType.O1_FORMULA_OR_CALCULATION
    if requirements.get("date") or requirements.get("deadline"):
        return OperatorType.O1_DATE_OR_DEADLINE
    if requirements.get("comparison") or int(requirements.get("required_operands", 0) or 0) >= 2:
        return OperatorType.O1_NUMERIC_COMPARISON
    if requirements.get("exact_field"):
        return OperatorType.O1_EXACT_FIELD
    return OperatorType.O3_SEMANTIC_PROPOSITION


def structured_source_priority(source_kind: str) -> int:
    order = {
        "structured_table": 0,
        "content_list_v2": 1,
        "content_list": 2,
        "markdown_exact": 3,
        "markdown_context": 4,
    }
    return order.get(source_kind, 9)


def prefer_structured_evidence(rows: Iterable[Mapping[str, object]]) -> list[dict]:
    """Prefer facts that bind an exact row/column/year/entity over loose snippets."""
    normalized = [dict(row) for row in rows]
    return sorted(
        normalized,
        key=lambda row: (
            structured_source_priority(str(row.get("source_kind") or "")),
            not bool(row.get("entity_bound")),
            not bool(row.get("year_bound")),
            not bool(row.get("field_bound")),
            str(row.get("path") or ""),
        ),
    )
