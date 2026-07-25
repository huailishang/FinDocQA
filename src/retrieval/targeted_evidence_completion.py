"""Compatibility surface for bounded local search.

True typed completion is implemented by
FinancialEvidenceCompletionAdapter.  This module retains the Package AE
search-audit API, but it never marks an atom resolved merely because a keyword
hit exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from evidence_completion.adapters.financial_reports import (
    MAX_ROUNDS,
    FinancialEvidenceCompletionAdapter,
    build_financial_request,
    search_financial_candidates,
)
from evidence_completion.contracts import EvidenceRequest
from verification.evidence_gap_classifier import classify_financial_gaps, retrievable_atoms
from verification.financial_claim_ast import FinancialClaimSpec
from verification.financial_metric_ledger import FinancialMetricLedger

SCHEMA_VERSION = "bounded_local_search_audit_v2"
TargetedEvidenceRequest = EvidenceRequest


@dataclass(frozen=True)
class TargetedCompletionAudit:
    schema_version: str
    max_rounds: int
    rounds_run: int
    provider_calls: int
    declared_doc_boundary_pass: bool
    whole_corpus_scan: bool
    visited_doc_ids: tuple[str, ...]
    requests: tuple[Mapping[str, Any], ...]
    hits: tuple[Mapping[str, Any], ...]
    initial_missing_atoms: tuple[str, ...]
    potentially_resolved_atoms: tuple[str, ...]
    remaining_missing_atoms: tuple[str, ...]
    stopped_reason: str
    classified_gaps: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_targeted_requests(
    claim_spec: FinancialClaimSpec,
    missing_atoms: Sequence[str],
    *,
    round_number: int,
) -> tuple[EvidenceRequest, ...]:
    gaps = classify_financial_gaps(claim_spec, missing_atoms)
    return tuple(
        build_financial_request(claim_spec, atom, round_number=round_number)
        for atom in retrievable_atoms(gaps)
        if 1 <= round_number <= MAX_ROUNDS
    )


def run_targeted_completion(
    claim_spec: FinancialClaimSpec,
    missing_atoms: Sequence[str],
    *,
    structured_root: str | Path,
    domain: str,
    declared_doc_ids: Sequence[str],
    max_rounds: int = MAX_ROUNDS,
) -> TargetedCompletionAudit:
    """Run a bounded search audit without claiming evidence completion."""
    rounds = min(max(int(max_rounds), 0), MAX_ROUNDS)
    initial = tuple(dict.fromkeys(str(atom) for atom in missing_atoms if str(atom)))
    gaps = classify_financial_gaps(claim_spec, initial)
    retrievable = retrievable_atoms(gaps)
    ledger = FinancialMetricLedger.from_documents(
        str(Path(structured_root)), domain, declared_doc_ids
    )
    requests: list[Mapping[str, Any]] = []
    hits: list[Mapping[str, Any]] = []
    visited: list[str] = []
    rounds_run = 0
    for round_number in range(1, rounds + 1):
        if not retrievable:
            break
        rounds_run = round_number
        for atom in retrievable:
            request = build_financial_request(
                claim_spec, atom, round_number=round_number
            )
            requests.append(request.to_dict())
            visited.extend(request.allowed_doc_ids)
            hits.extend(
                hit.to_dict()
                for hit in search_financial_candidates(
                    request,
                    structured_root=structured_root,
                    domain=domain,
                    ledger=ledger,
                )
            )
        # Compatibility audit does not perform typed merge. One round is enough
        # to demonstrate boundaries; true completion owns the second-round loop.
        break
    semantic = [gap for gap in gaps if not gap.retrievable]
    stopped = (
        "semantic_gap_no_retrieval" if semantic and not retrievable
        else "search_audit_complete_no_resolution_claim"
    )
    return TargetedCompletionAudit(
        schema_version=SCHEMA_VERSION,
        max_rounds=rounds,
        rounds_run=rounds_run,
        provider_calls=0,
        declared_doc_boundary_pass=set(visited) <= set(declared_doc_ids),
        whole_corpus_scan=False,
        visited_doc_ids=tuple(dict.fromkeys(visited)),
        requests=tuple(requests),
        hits=tuple(hits),
        initial_missing_atoms=initial,
        potentially_resolved_atoms=(),
        remaining_missing_atoms=initial,
        stopped_reason=stopped,
        classified_gaps=tuple(gap.to_dict() for gap in gaps),
    )


__all__ = [
    "MAX_ROUNDS",
    "FinancialEvidenceCompletionAdapter",
    "TargetedCompletionAudit",
    "TargetedEvidenceRequest",
    "build_targeted_requests",
    "run_targeted_completion",
]
