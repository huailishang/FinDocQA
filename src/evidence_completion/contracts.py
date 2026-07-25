"""Domain-neutral evidence-completion contracts.

This module intentionally knows nothing about financial metrics or dataset-specific QIDs.
Domain adapters map their own claim schemas into these contracts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class GapClass(str, Enum):
    SEMANTIC = "semantic"
    EVIDENCE = "evidence"
    LINEAGE = "lineage"
    CONTRACT = "contract"
    CONFLICT = "conflict"


class EvidenceGrade(str, Enum):
    CORRECT = "correct"
    AMBIGUOUS = "ambiguous"
    INCORRECT = "incorrect"


@dataclass(frozen=True)
class EvidenceGap:
    atom: str
    gap_class: GapClass
    reason: str
    retrievable: bool
    source: str = "sufficiency"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["gap_class"] = self.gap_class.value
        return payload


@dataclass(frozen=True)
class EvidenceRequest:
    atom: str
    entity: str
    metric: str
    period: str
    comparison_period: str
    statement_scope: str
    attribution_scope: str
    unit_expectation: str
    expected_unit_family: str
    peer_unit_family: str
    unit_compatibility_required: bool
    per_share_basis_expectation: str
    policy_stage_expectation: str
    query_terms: tuple[str, ...]
    allowed_doc_ids: tuple[str, ...]
    reason: str
    round: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceHit:
    doc_id: str
    source: str
    local_window: str
    round: int
    matched_terms: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GradedEvidenceHit:
    hit: Mapping[str, Any]
    grade: EvidenceGrade
    dimensions: Mapping[str, str]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": dict(self.hit),
            "grade": self.grade.value,
            "dimensions": dict(self.dimensions),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TypedEvidenceFact:
    entity: str
    metric: str
    period: str
    comparison_period: str
    value: Any
    unit: str
    statement_scope: str
    attribution_scope: str
    fact_state: str
    doc_id: str
    canonical_source: str
    local_window: str
    parse_method: str
    quality_grade: EvidenceGrade
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["quality_grade"] = self.quality_grade.value
        return payload


@dataclass(frozen=True)
class CompletionResult:
    schema_version: str
    initial_sufficiency: Mapping[str, Any]
    classified_gaps: tuple[Mapping[str, Any], ...]
    requests: tuple[Mapping[str, Any], ...]
    raw_hits: tuple[Mapping[str, Any], ...]
    graded_hits: tuple[Mapping[str, Any], ...]
    typed_facts: tuple[Mapping[str, Any], ...]
    accepted_facts: tuple[Mapping[str, Any], ...]
    rejected_facts: tuple[Mapping[str, Any], ...]
    claim_fact_bindings: tuple[Mapping[str, Any], ...]
    binding_counts: Mapping[str, int]
    round_refinements: tuple[Mapping[str, Any], ...]
    raw_typed_fact_count: int
    unique_typed_fact_count: int
    raw_accepted_fact_count: int
    unique_accepted_fact_count: int
    duplicate_fact_count: int
    merged_source_facts: tuple[Mapping[str, Any], ...]
    conflicting_atoms: tuple[str, ...]
    conflict_sources: tuple[Mapping[str, Any], ...]
    resolution_status: str
    post_completion_evidence: Mapping[str, Any]
    post_completion_sufficiency: Mapping[str, Any]
    post_completion_status: str
    post_completion_safe_to_override: bool
    rounds_run: int
    stopped_reason: str
    provider_calls: int
    declared_doc_boundary_pass: bool
    whole_corpus_scan: bool
    visited_doc_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuestionMemorySnapshot:
    schema_version: str
    layers: Mapping[str, Any]
    token_budget: int
    before_tokens: int
    after_tokens: int
    compression_triggered: bool
    budget_exhausted: bool
    protected_hashes: Mapping[str, str]
    final_answer: str
    final_sufficiency: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
