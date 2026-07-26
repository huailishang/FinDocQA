"""CanonicalDocument-native lexical retrieval (Phase 2 shadow implementation).

This is intentionally not wired as the production default yet. It proves the
new retrieval contract can work without reading page_XXXX.md files directly.
E2 parity evaluation should decide when it is safe to replace the legacy path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question, retrieval_doc_ids
from document.contracts import CanonicalDocument, CanonicalPage
from document.store import DocumentStore
from retrieval.interfaces import (
    DocumentHit,
    DocumentRetriever,
    EvidenceReranker,
    IdentityReranker,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.%％]+|[\u4e00-\u9fff]{2,}")
_BOOK_TITLE_RE = re.compile(r"《[^》]+》")


def _terms(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []

    def add(token: str) -> None:
        normalized = token.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    for match in _TOKEN_RE.findall(text or ""):
        token = match.strip().lower()
        if not token:
            continue
        is_cjk = all("\u4e00" <= ch <= "\u9fff" for ch in token)
        if not is_cjk:
            add(token)
            continue
        if len(token) <= 4:
            add(token)
        for size in (2, 3, 4):
            if len(token) < size:
                continue
            for start in range(0, len(token) - size + 1):
                add(token[start:start + size])
    return tuple(result)


def _question_terms(question: Question) -> tuple[str, ...]:
    text = "\n".join([question.text, *question.options.values()])
    return _terms(text)


def _evidence_terms(question: Question) -> tuple[str, ...]:
    """Terms for page/block retrieval after document identity has been resolved.

    Long document titles inside Chinese book-title brackets are valuable for
    document scope, but they are usually noise for page ranking because headers,
    declarations, and cover pages repeat the title many times. Strip those
    identity spans for evidence retrieval while keeping options and the semantic
    remainder of the question.
    """
    question_text = _BOOK_TITLE_RE.sub(" ", question.text or "")
    question_text = re.sub(r"^\s*根据\s*", "", question_text)
    text = "\n".join([question_text, *question.options.values()])
    terms = _terms(text)
    return terms or _question_terms(question)


def _score_text(text: str, terms: Sequence[str]) -> tuple[float, tuple[str, ...]]:
    lowered = (text or "").lower()
    matched: list[str] = []
    score = 0.0
    for term in terms:
        count = lowered.count(term)
        if not count:
            continue
        matched.append(term)
        numeric = any(ch.isdigit() for ch in term)
        weight = 6.0 if numeric else 4.0 if len(term) >= 4 else 2.0
        score += min(count, 5) * weight
    return score, tuple(matched)


def _window(
    text: str,
    terms: Sequence[str],
    size: int,
    flank: int,
) -> tuple[str, str, str, float, tuple[str, ...]]:
    if not text:
        return "", "", "", 0.0, ()
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - size // 4)
    end = min(len(text), start + size)
    snippet = text[start:end].strip()
    score, matched = _score_text(snippet, terms)
    before = text[max(0, start - flank):start].strip()
    after = text[end:min(len(text), end + flank)].strip()
    return snippet, before, after, score, matched


@dataclass
class CanonicalDocumentRetriever(DocumentRetriever):
    top_k: int = 8
    name: str = "canonical_document_lexical"

    def retrieve_documents(
        self,
        question: Question,
        classification: ClassificationResult,
        store: DocumentStore,
    ) -> Sequence[DocumentHit]:
        del classification
        terms = _question_terms(question)
        explicit = retrieval_doc_ids(question)
        documents: Iterable[CanonicalDocument]
        if explicit:
            documents = (
                document
                for doc_id in explicit
                if (document := store.get(question.domain, str(doc_id))) is not None
            )
        else:
            documents = store.iter_documents(question.domain)

        hits: list[DocumentHit] = []
        for document in documents:
            title_score, title_terms = _score_text(document.title, terms)
            body_score = 0.0
            body_terms: set[str] = set()
            for page in document.pages:
                page_score, matched = _score_text(page.text, terms)
                body_score = max(body_score, page_score)
                body_terms.update(matched)
            score = title_score * 2.0 + body_score
            if explicit or score > 0:
                hits.append(
                    DocumentHit(
                        domain=document.domain,
                        document_id=document.document_id,
                        score=score,
                        retriever=self.name,
                        metadata={
                            "matched_title_terms": list(title_terms),
                            "matched_body_terms": sorted(body_terms),
                            "source_type": document.source_type,
                        },
                    )
                )
        hits.sort(key=lambda hit: (-hit.score, hit.document_id))
        return tuple(hits[: max(0, int(self.top_k))])


@dataclass
class CanonicalLexicalEvidenceRetriever:
    store: DocumentStore
    document_retriever: DocumentRetriever | None = None
    reranker: EvidenceReranker | None = None
    top_k_per_doc: int = 5
    window_chars: int = 1800
    context_flank_chars: int = 600
    name: str = "canonical_lexical"

    def __post_init__(self) -> None:
        if self.document_retriever is None:
            self.document_retriever = CanonicalDocumentRetriever()
        if self.reranker is None:
            self.reranker = IdentityReranker()

    def retrieve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> Sequence[EvidenceCandidate]:
        assert self.document_retriever is not None
        assert self.reranker is not None
        terms = _evidence_terms(question)
        doc_hits = self.document_retriever.retrieve_documents(
            question,
            classification,
            self.store,
        )
        candidates: list[EvidenceCandidate] = []
        for doc_hit in doc_hits:
            document = self.store.get(doc_hit.domain, doc_hit.document_id)
            if document is None:
                continue
            page_candidates: list[EvidenceCandidate] = []
            for page in document.pages:
                candidate = self._page_candidate(question, document, page, terms, doc_hit)
                if candidate is not None:
                    page_candidates.append(candidate)
            page_candidates.sort(key=lambda item: (-item.score, item.source))
            candidates.extend(page_candidates[: max(0, int(self.top_k_per_doc))])

        candidates.sort(key=lambda item: (-item.score, item.doc_id, item.source))
        return tuple(self.reranker.rerank(question, candidates))

    def _page_candidate(
        self,
        question: Question,
        document: CanonicalDocument,
        page: CanonicalPage,
        terms: Sequence[str],
        doc_hit: DocumentHit,
    ) -> EvidenceCandidate | None:
        snippet, before, after, score, matched = _window(
            page.text,
            terms,
            self.window_chars,
            self.context_flank_chars,
        )
        if score <= 0 and not retrieval_doc_ids(question):
            return None
        if not snippet:
            return None
        page_number = page.page_number or 0
        source = f"canonical://{document.domain}/{document.document_id}/page/{page_number}"
        section_title = ""
        if page.section_paths:
            section_title = page.section_paths[0][-1] if page.section_paths[0] else ""
        return EvidenceCandidate(
            domain=document.domain,
            doc_id=document.document_id,
            source=source,
            text=snippet,
            before_text=before,
            after_text=after,
            section_title=section_title or None,
            score=score + doc_hit.score * 0.05,
            retriever=self.name,
            metadata={
                "canonical_document": True,
                "page_number": page.page_number,
                "matched_terms": list(matched),
                "document_score": doc_hit.score,
                "document_retriever": doc_hit.retriever,
                "parser_name": document.parser_name,
                "source_type": document.source_type,
                "lineage": {
                    "source_path": (
                        page.lineage.source_path if page.lineage else document.source_uri
                    ),
                    "page_number": page.page_number,
                    "source_page_index": (
                        page.lineage.source_page_index if page.lineage else None
                    ),
                },
            },
        )
