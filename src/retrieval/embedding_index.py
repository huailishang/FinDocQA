"""Explicitly built in-memory embedding index for CanonicalDocument pages.

The index never builds itself implicitly. Callers must provide an embedding
model and an explicit ``max_pages`` budget, which prevents accidental full-corpus
API usage. The same interface works with local embedding models or API clients.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question, retrieval_doc_ids
from document.contracts import CanonicalPage
from document.store import DocumentStore


class EmbeddingModel(Protocol):
    """Minimal embedding model contract shared by local and API implementations."""

    def embed(self, texts: str | Sequence[str]) -> list[list[float]]:
        ...


def canonical_page_embedding_text(page: CanonicalPage) -> str:
    """Create one retrieval text view without discarding structured page content."""
    parts: list[str] = []
    if page.text.strip():
        parts.append(page.text.strip())
    for table in page.tables:
        table_text = table.markdown.strip() or table.html.strip()
        if table.caption.strip():
            parts.append(table.caption.strip())
        if table_text:
            parts.append(table_text)
        if table.footnote.strip():
            parts.append(table.footnote.strip())
    for formula in page.formulas:
        if formula.expression.strip():
            parts.append(formula.expression.strip())
        if formula.latex.strip() and formula.latex.strip() != formula.expression.strip():
            parts.append(formula.latex.strip())
    for figure in page.figures:
        if figure.caption.strip():
            parts.append(figure.caption.strip())
        if figure.alt_text.strip():
            parts.append(figure.alt_text.strip())
    return "\n".join(part for part in parts if part)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding vectors must have the same non-zero dimension")
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass(frozen=True)
class VectorEvidenceEntry:
    candidate: EvidenceCandidate
    vector: tuple[float, ...]


@dataclass(frozen=True)
class CanonicalEmbeddingIndex:
    """Small in-memory page index intended for development and evaluation."""

    entries: tuple[VectorEvidenceEntry, ...]
    embedding_dimension: int

    @classmethod
    def build(
        cls,
        store: DocumentStore,
        embedder: EmbeddingModel,
        *,
        max_pages: int,
        domain: str | None = None,
        document_ids: Sequence[str] = (),
        batch_size: int = 16,
        max_chars_per_page: int = 12000,
    ) -> "CanonicalEmbeddingIndex":
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1; full-corpus indexing must be explicitly budgeted")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if max_chars_per_page < 1:
            raise ValueError("max_chars_per_page must be >= 1")
        allowed_docs = {str(value) for value in document_ids if str(value)}

        candidates: list[EvidenceCandidate] = []
        texts: list[str] = []
        for document in store.iter_documents(domain):
            if allowed_docs and document.document_id not in allowed_docs:
                continue
            for page in document.pages:
                full_text = canonical_page_embedding_text(page).strip()
                if not full_text:
                    continue
                text = full_text[:max_chars_per_page]
                page_number = page.page_number
                source = (
                    f"canonical://{document.domain}/{document.document_id}/page/{page_number}"
                    if page_number is not None
                    else f"canonical://{document.domain}/{document.document_id}/page/unknown"
                )
                candidates.append(
                    EvidenceCandidate(
                        domain=document.domain,
                        doc_id=document.document_id,
                        source=source,
                        text=text,
                        score=0.0,
                        retriever="embedding_index",
                        metadata={
                            "page_number": page_number,
                            "canonical_document": True,
                            "embedding_indexed": True,
                            "embedding_text_chars": len(text),
                            "embedding_source_chars": len(full_text),
                            "embedding_text_truncated": len(text) < len(full_text),
                        },
                    )
                )
                texts.append(text)
                if len(texts) >= max_pages:
                    break
            if len(texts) >= max_pages:
                break

        if not texts:
            return cls(entries=(), embedding_dimension=0)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(embedder.embed(batch))
        if len(vectors) != len(candidates):
            raise ValueError(
                f"embedding result count mismatch: expected={len(candidates)} actual={len(vectors)}"
            )
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or next(iter(dimensions)) < 1:
            raise ValueError("embedding vectors must have one consistent non-zero dimension")
        dimension = next(iter(dimensions))
        entries = tuple(
            VectorEvidenceEntry(candidate=candidate, vector=tuple(float(value) for value in vector))
            for candidate, vector in zip(candidates, vectors)
        )
        return cls(entries=entries, embedding_dimension=dimension)


class EmbeddingEvidenceRetriever:
    """Query a prebuilt CanonicalEmbeddingIndex using cosine similarity."""

    name = "canonical_embedding"

    def __init__(
        self,
        index: CanonicalEmbeddingIndex,
        embedder: EmbeddingModel,
        *,
        top_k: int = 10,
    ) -> None:
        self.index = index
        self.embedder = embedder
        self.top_k = max(1, int(top_k))

    def retrieve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> Sequence[EvidenceCandidate]:
        del classification
        if not self.index.entries:
            return ()
        query_text = "\n".join([question.text, *question.options.values()]).strip()
        vectors = self.embedder.embed(query_text)
        if len(vectors) != 1:
            raise ValueError(f"query embedding must return exactly one vector, got {len(vectors)}")
        query_vector = vectors[0]
        if len(query_vector) != self.index.embedding_dimension:
            raise ValueError(
                "query/index embedding dimension mismatch: "
                f"query={len(query_vector)} index={self.index.embedding_dimension}"
            )

        explicit_docs = set(retrieval_doc_ids(question))
        scored: list[EvidenceCandidate] = []
        for entry in self.index.entries:
            candidate = entry.candidate
            if candidate.domain != question.domain:
                continue
            if explicit_docs and candidate.doc_id not in explicit_docs:
                continue
            score = _cosine_similarity(query_vector, entry.vector)
            metadata = dict(candidate.metadata or {})
            metadata["embedding_similarity"] = score
            scored.append(
                EvidenceCandidate(
                    domain=candidate.domain,
                    doc_id=candidate.doc_id,
                    source=candidate.source,
                    text=candidate.text,
                    before_text=candidate.before_text,
                    after_text=candidate.after_text,
                    section_title=candidate.section_title,
                    score=score,
                    retriever=self.name,
                    metadata=metadata,
                )
            )
        scored.sort(key=lambda item: (-item.score, item.doc_id, item.source))
        return tuple(scored[: self.top_k])
