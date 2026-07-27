"""Shared retrieval-scope audit contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate


@dataclass(frozen=True)
class RetrievalScopeAudit:
    """Truth recorded at the retriever call boundary."""

    scope_candidate_doc_ids: tuple[str, ...]
    retriever_requested_doc_ids: tuple[str, ...]
    retriever_resolved_doc_ids: tuple[str, ...]
    retriever_missing_doc_ids: tuple[str, ...]
    retrieved_doc_ids: tuple[str, ...]
    request_source: str
    provider_calls: int
    scope_expansion_reasons: Mapping[str, str]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "scope_candidate_doc_ids": list(self.scope_candidate_doc_ids),
            "retriever_requested_doc_ids": list(self.retriever_requested_doc_ids),
            "retriever_resolved_doc_ids": list(self.retriever_resolved_doc_ids),
            "retriever_missing_doc_ids": list(self.retriever_missing_doc_ids),
            "retrieved_doc_ids": list(self.retrieved_doc_ids),
            "retriever_scope_request_source": self.request_source,
            "retriever_scope_audit_source": "retriever_call_boundary",
            "retriever_scope_provider_calls": int(self.provider_calls),
            "scope_expansion_reasons": dict(self.scope_expansion_reasons),
        }


class AuditedEvidenceCandidates(tuple):
    """Evidence sequence that retains retriever truth even when empty."""

    def __new__(
        cls,
        candidates: Sequence[EvidenceCandidate],
        audit_metadata: Mapping[str, Any],
    ) -> "AuditedEvidenceCandidates":
        instance = super().__new__(cls, tuple(candidates))
        instance.audit_metadata = dict(audit_metadata)
        return instance
