"""Read-only CanonicalDocument stores.

The QA pipeline should depend on this boundary instead of reaching directly into
MinerU/PyMuPDF directories. Storage backends can later be file-, DB-, or remote-
based without changing retrieval contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from document.contracts import CanonicalDocument


class DocumentStore(Protocol):
    def get(self, domain: str, document_id: str) -> CanonicalDocument | None:
        ...

    def document_ids(self, domain: str | None = None) -> tuple[str, ...]:
        ...

    def iter_documents(self, domain: str | None = None) -> Iterable[CanonicalDocument]:
        ...


@dataclass(frozen=True)
class InMemoryDocumentStore:
    """Small deterministic store used by adapters, tests and offline evaluation."""

    _documents: Mapping[tuple[str, str], CanonicalDocument]

    @classmethod
    def from_documents(cls, documents: Iterable[CanonicalDocument]) -> "InMemoryDocumentStore":
        index: dict[tuple[str, str], CanonicalDocument] = {}
        for document in documents:
            key = (document.domain, document.document_id)
            if key in index:
                raise ValueError(
                    f"duplicate canonical document: domain={key[0]!r} id={key[1]!r}"
                )
            index[key] = document
        return cls(index)

    def get(self, domain: str, document_id: str) -> CanonicalDocument | None:
        return self._documents.get((str(domain), str(document_id)))

    def document_ids(self, domain: str | None = None) -> tuple[str, ...]:
        ids = [
            doc_id
            for (doc_domain, doc_id) in self._documents
            if domain is None or doc_domain == domain
        ]
        return tuple(sorted(set(ids)))

    def iter_documents(self, domain: str | None = None) -> Iterable[CanonicalDocument]:
        for key in sorted(self._documents):
            document = self._documents[key]
            if domain is None or document.domain == domain:
                yield document
