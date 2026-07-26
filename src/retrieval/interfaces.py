"""Stable retrieval module contracts for FinDocQA."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question
from document.store import DocumentStore


@dataclass(frozen=True)
class DocumentHit:
    domain: str
    document_id: str
    score: float
    retriever: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class DocumentRetriever(Protocol):
    name: str

    def retrieve_documents(
        self,
        question: Question,
        classification: ClassificationResult,
        store: DocumentStore,
    ) -> Sequence[DocumentHit]:
        ...


class StoreBoundEvidenceRetriever(Protocol):
    name: str

    def retrieve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> Sequence[EvidenceCandidate]:
        ...


class EvidenceReranker(Protocol):
    name: str

    def rerank(
        self,
        question: Question,
        candidates: Sequence[EvidenceCandidate],
        *,
        top_k: int | None = None,
    ) -> Sequence[EvidenceCandidate]:
        ...


class IdentityReranker:
    """No-op implementation used until a real reranker is enabled."""

    name = "identity"

    def rerank(
        self,
        question: Question,
        candidates: Sequence[EvidenceCandidate],
        *,
        top_k: int | None = None,
    ) -> Sequence[EvidenceCandidate]:
        del question
        ordered = tuple(candidates)
        if top_k is None:
            return ordered
        return ordered[: max(0, int(top_k))]
