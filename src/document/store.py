"""Read-only CanonicalDocument stores.

The QA pipeline should depend on this boundary instead of reaching directly into
MinerU/PyMuPDF directories. Storage backends can later be file-, DB-, or remote-
based without changing retrieval contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

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


class RawMineruDocumentStore:
    """Lazy CanonicalDocument store backed by raw MinerU output directories.

    Only requested domain/document pairs are adapted. Loaded documents are
    cached for the lifetime of the store so scoped retrieval does not rebuild
    the same document repeatedly.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        parser_version: str = "",
        loader: Callable[..., CanonicalDocument] | None = None,
    ) -> None:
        self.root = Path(root)
        self.parser_version = str(parser_version or "")
        if loader is None:
            from document.adapters.mineru import canonical_from_raw_mineru

            loader = canonical_from_raw_mineru
        self._loader = loader
        self._cache: dict[tuple[str, str], CanonicalDocument] = {}

    def _doc_dir(self, domain: str, document_id: str) -> Path:
        return self.root / str(domain) / str(document_id)

    @staticmethod
    def _has_raw_contract(path: Path, document_id: str) -> bool:
        return (path / "auto" / f"{document_id}_content_list_v2.json").is_file()

    def get(self, domain: str, document_id: str) -> CanonicalDocument | None:
        key = (str(domain), str(document_id))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self._doc_dir(*key)
        if not path.is_dir() or not self._has_raw_contract(path, key[1]):
            return None
        document = self._loader(
            path,
            domain=key[0],
            doc_id=key[1],
            parser_version=self.parser_version,
        )
        self._cache[key] = document
        return document

    def document_ids(self, domain: str | None = None) -> tuple[str, ...]:
        if domain is None:
            ids: set[str] = set()
            if not self.root.is_dir():
                return ()
            for domain_dir in self.root.iterdir():
                if not domain_dir.is_dir():
                    continue
                ids.update(self.document_ids(domain_dir.name))
            return tuple(sorted(ids))

        domain_dir = self.root / str(domain)
        if not domain_dir.is_dir():
            return ()
        result = [
            path.name
            for path in domain_dir.iterdir()
            if path.is_dir() and self._has_raw_contract(path, path.name)
        ]
        return tuple(sorted(result))

    def iter_documents(self, domain: str | None = None) -> Iterable[CanonicalDocument]:
        if domain is None:
            if not self.root.is_dir():
                return
            for domain_dir in sorted(
                (path for path in self.root.iterdir() if path.is_dir()),
                key=lambda path: path.name,
            ):
                yield from self.iter_documents(domain_dir.name)
            return
        for document_id in self.document_ids(domain):
            document = self.get(str(domain), document_id)
            if document is not None:
                yield document


class AdaptedPageDocumentStore:
    """Lazy CanonicalDocument store for an existing page_XXXX.md corpus."""

    def __init__(
        self,
        root: str | Path,
        *,
        parser_version: str = "",
        loader: Callable[..., CanonicalDocument] | None = None,
    ) -> None:
        self.root = Path(root)
        self.parser_version = str(parser_version or "")
        if loader is None:
            from document.adapters.mineru import canonical_from_adapted_mineru

            loader = canonical_from_adapted_mineru
        self._loader = loader
        self._cache: dict[tuple[str, str], CanonicalDocument] = {}

    def _doc_dir(self, domain: str, document_id: str) -> Path:
        return self.root / str(domain) / str(document_id)

    @staticmethod
    def _has_page_contract(path: Path) -> bool:
        return any(page.is_file() for page in path.glob("page_*.md"))

    def get(self, domain: str, document_id: str) -> CanonicalDocument | None:
        key = (str(domain), str(document_id))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self._doc_dir(*key)
        if not path.is_dir() or not self._has_page_contract(path):
            return None
        document = self._loader(
            path,
            domain=key[0],
            doc_id=key[1],
            source_uri=str(path),
            source_type="adapted_pages",
            parser_version=self.parser_version,
        )
        self._cache[key] = document
        return document

    def document_ids(self, domain: str | None = None) -> tuple[str, ...]:
        if domain is None:
            ids: set[str] = set()
            if not self.root.is_dir():
                return ()
            for domain_dir in self.root.iterdir():
                if domain_dir.is_dir():
                    ids.update(self.document_ids(domain_dir.name))
            return tuple(sorted(ids))
        domain_dir = self.root / str(domain)
        if not domain_dir.is_dir():
            return ()
        return tuple(
            sorted(
                path.name
                for path in domain_dir.iterdir()
                if path.is_dir() and self._has_page_contract(path)
            )
        )

    def iter_documents(self, domain: str | None = None) -> Iterable[CanonicalDocument]:
        if domain is None:
            if not self.root.is_dir():
                return
            for domain_dir in sorted(
                (path for path in self.root.iterdir() if path.is_dir()),
                key=lambda path: path.name,
            ):
                yield from self.iter_documents(domain_dir.name)
            return
        for document_id in self.document_ids(domain):
            document = self.get(str(domain), document_id)
            if document is not None:
                yield document


class FallbackDocumentStore:
    """Ordered document-store chain; the first store containing a document wins."""

    def __init__(self, stores: Iterable[DocumentStore]) -> None:
        self.stores = tuple(stores)
        if not self.stores:
            raise ValueError("FallbackDocumentStore requires at least one store")

    def get(self, domain: str, document_id: str) -> CanonicalDocument | None:
        for store in self.stores:
            document = store.get(domain, document_id)
            if document is not None:
                return document
        return None

    def document_ids(self, domain: str | None = None) -> tuple[str, ...]:
        ids: set[str] = set()
        for store in self.stores:
            ids.update(store.document_ids(domain))
        return tuple(sorted(ids))

    def iter_documents(self, domain: str | None = None) -> Iterable[CanonicalDocument]:
        seen: set[str] = set()
        for store in self.stores:
            for document in store.iter_documents(domain):
                if document.document_id in seen:
                    continue
                seen.add(document.document_id)
                yield document
