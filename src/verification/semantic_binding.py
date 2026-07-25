"""Small fail-closed semantic binding audit for cited evidence.

This module intentionally avoids a full NLI model.  It verifies that a claim and
its cited local evidence share the required semantic concepts before structural
lineage may be treated as sufficient evidence binding.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

_DOC_REF = re.compile(r"DOC:\d+", re.IGNORECASE)


@dataclass(frozen=True)
class SemanticConcept:
    name: str
    aliases: tuple[str, ...]

    @classmethod
    def build(cls, name: str, aliases: Sequence[str]) -> "SemanticConcept":
        values = tuple(dict.fromkeys(str(value).strip() for value in aliases if str(value).strip()))
        if not values:
            raise ValueError(f"semantic concept {name!r} requires at least one alias")
        return cls(name=str(name).strip() or values[0], aliases=values)


@dataclass(frozen=True)
class SemanticBindingResult:
    valid: bool
    reason: str
    cited_refs: tuple[str, ...]
    claim_coverage_ratio: float
    claim_concepts: Mapping[str, bool]
    evidence_by_ref: Mapping[str, Mapping[str, object]]
    unsupported_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["cited_refs"] = list(self.cited_refs)
        payload["unsupported_refs"] = list(self.unsupported_refs)
        payload["claim_concepts"] = dict(self.claim_concepts)
        payload["evidence_by_ref"] = {key: dict(value) for key, value in self.evidence_by_ref.items()}
        return payload


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _concept_hits(text: str, concepts: Sequence[SemanticConcept]) -> tuple[dict[str, bool], dict[str, list[str]]]:
    normalized = _normalize(text)
    coverage: dict[str, bool] = {}
    hits: dict[str, list[str]] = {}
    for concept in concepts:
        matched = [alias for alias in concept.aliases if _normalize(alias) in normalized]
        coverage[concept.name] = bool(matched)
        hits[concept.name] = matched
    return coverage, hits


def audit_semantic_binding(
    *,
    claim_text: object,
    evidence_by_ref: Mapping[str, object],
    required_concepts: Sequence[SemanticConcept],
    cited_refs: Sequence[str] | None = None,
    min_claim_coverage: float = 0.75,
    require_every_cited_ref: bool = True,
) -> SemanticBindingResult:
    """Verify that cited evidence directly covers the claim's required concepts.

    The gate is deliberately simple and fail-closed:
    - the claim must mention enough of the configured semantic concepts;
    - at least one DOC reference must be cited;
    - each cited evidence block (or at least one, if configured) must contain all
      required concepts, so a traceable but semantically unrelated citation fails.

    The caller supplies concept aliases derived from the question/claim domain;
    this function contains no qid-specific logic.
    """

    concepts = tuple(required_concepts)
    if not concepts:
        raise ValueError("semantic binding requires at least one concept")
    if not (0.0 <= float(min_claim_coverage) <= 1.0):
        raise ValueError("min_claim_coverage must be between 0 and 1")

    normalized_evidence = {str(key).upper(): str(value or "") for key, value in evidence_by_ref.items()}
    refs = tuple(
        dict.fromkeys(
            str(value).upper()
            for value in (
                cited_refs
                if cited_refs is not None
                else [match.group(0) for match in _DOC_REF.finditer(str(claim_text or ""))]
            )
            if str(value).strip()
        )
    )

    claim_coverage, claim_hits = _concept_hits(str(claim_text or ""), concepts)
    claim_ratio = sum(claim_coverage.values()) / len(concepts)

    evidence_audit: dict[str, Mapping[str, object]] = {}
    unsupported: list[str] = []
    for ref in refs:
        text = normalized_evidence.get(ref, "")
        coverage, hits = _concept_hits(text, concepts)
        all_required = bool(text) and all(coverage.values())
        evidence_audit[ref] = {
            "exists": bool(text),
            "all_required_concepts": all_required,
            "coverage": coverage,
            "hits": hits,
            "text_chars": len(text),
        }
        if not all_required:
            unsupported.append(ref)

    if not refs:
        reason = "missing_cited_doc_ref"
        valid = False
    elif claim_ratio < float(min_claim_coverage):
        reason = "claim_semantic_coverage_insufficient"
        valid = False
    elif any(ref not in normalized_evidence for ref in refs):
        reason = "cited_evidence_missing"
        valid = False
    elif require_every_cited_ref and unsupported:
        reason = "cited_evidence_semantically_unrelated"
        valid = False
    elif not require_every_cited_ref and len(unsupported) == len(refs):
        reason = "no_cited_evidence_supports_required_concepts"
        valid = False
    else:
        reason = "semantic_binding_pass"
        valid = True

    return SemanticBindingResult(
        valid=valid,
        reason=reason,
        cited_refs=refs,
        claim_coverage_ratio=round(claim_ratio, 6),
        claim_concepts={name: bool(value) for name, value in claim_coverage.items()},
        evidence_by_ref=evidence_audit,
        unsupported_refs=tuple(unsupported),
    )
