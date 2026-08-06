"""CanonicalDocument-native lexical retrieval (Phase 2 shadow implementation).

This is intentionally not wired as the production default yet. It proves the
new retrieval contract can work without reading page_XXXX.md files directly.
E2 parity evaluation should decide when it is safe to replace the legacy path.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question, retrieval_doc_ids
from document.contracts import CanonicalDocument, CanonicalPage, CanonicalTable
from document.store import DocumentStore
from retrieval.interfaces import (
    DocumentHit,
    DocumentRetriever,
    EvidenceReranker,
    IdentityReranker,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.%％]+|[\u4e00-\u9fff]{2,}")
_BOOK_TITLE_RE = re.compile(r"《[^》]+》")
_DOCUMENT_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "for",
        "to",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "what",
        "which",
        "how",
        "during",
        "this",
        "that",
        "these",
        "those",
        "from",
        "by",
        "with",
        "at",
        "as",
    }
)
_ROW_LABEL_MATCH_STOPWORDS = _DOCUMENT_QUERY_STOPWORDS | frozenset(
    {
        "average",
        "between",
        "did",
        "does",
        "exceed",
        "exceeded",
        "highest",
        "many",
        "million",
        "millions",
        "period",
        "thousand",
        "value",
        "values",
        "year",
        "years",
    }
)


@dataclass(frozen=True)
class _TableRowAnchor:
    text: str
    before_text: str
    after_text: str
    table_id: str
    source_object_id: str
    row_index: int
    row_label: str
    match_score: int
    matched_terms: tuple[str, ...]
    span: tuple[int, int]
    coordinate_ids: tuple[str, ...]


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


def _document_query_terms(question: Question) -> tuple[str, ...]:
    raw_terms = _question_terms(question)
    filtered = tuple(
        term for term in raw_terms if term not in _DOCUMENT_QUERY_STOPWORDS
    )
    return filtered or raw_terms


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


def _term_weight(term: str) -> float:
    numeric = any(ch.isdigit() for ch in term)
    return 6.0 if numeric else 4.0 if len(term) >= 4 else 2.0


def _score_text(text: str, terms: Sequence[str]) -> tuple[float, tuple[str, ...]]:
    lowered = (text or "").lower()
    matched: list[str] = []
    score = 0.0
    for term in terms:
        count = lowered.count(term)
        if not count:
            continue
        matched.append(term)
        score += min(count, 5) * _term_weight(term)
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
    hits: list[tuple[int, float]] = []
    for term in terms:
        position = lowered.find(term)
        if position >= 0:
            hits.append((position, _term_weight(term)))

    start = 0
    if hits:
        hits.sort(key=lambda item: item[0])
        positions = [position for position, _ in hits]
        prefix = [0.0]
        for _, weight in hits:
            prefix.append(prefix[-1] + weight)

        best_support = -1.0
        best_hit_count = -1
        best_start = 0
        for position, _ in hits:
            candidate_start = max(0, position - size // 4)
            candidate_end = candidate_start + size
            left = bisect_left(positions, candidate_start)
            right = bisect_left(positions, candidate_end)
            support = prefix[right] - prefix[left]
            hit_count = right - left
            if (support, hit_count, -candidate_start) > (
                best_support,
                best_hit_count,
                -best_start,
            ):
                best_support = support
                best_hit_count = hit_count
                best_start = candidate_start
        start = best_start

    end = min(len(text), start + size)
    snippet = text[start:end].strip()
    score, matched = _score_text(snippet, terms)
    before = text[max(0, start - flank):start].strip()
    after = text[end:min(len(text), end + flank)].strip()
    return snippet, before, after, score, matched


def _normalized_row_label_terms(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_term in _terms(text):
        term = raw_term.strip("._-%％")
        if not term or any(ch.isdigit() for ch in term):
            continue
        is_cjk = all("\u4e00" <= ch <= "\u9fff" for ch in term)
        if not is_cjk:
            if term in _ROW_LABEL_MATCH_STOPWORDS:
                continue
            if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
                term = term[:-1]
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return tuple(result)


def _row_label_match_score(
    label: str,
    question_terms: Sequence[str],
) -> tuple[int, tuple[str, ...]]:
    label_terms = set(_normalized_row_label_terms(label))
    matched = tuple(term for term in question_terms if term in label_terms)
    score = sum(3 if len(term) >= 6 else 2 if len(term) >= 4 else 1 for term in matched)
    return score, matched


def _table_source_object_id(table: CanonicalTable) -> str:
    metadata = table.metadata if isinstance(table.metadata, Mapping) else {}
    return str(metadata.get("source_object_id") or table.table_id or "")


def _valid_coordinate_span(
    coordinate_spans: Mapping[str, object],
    coordinate: str,
    *,
    text_length: int,
) -> tuple[int, int] | None:
    raw_span = coordinate_spans.get(coordinate)
    if (
        not isinstance(raw_span, Sequence)
        or isinstance(raw_span, (str, bytes, bytearray))
        or len(raw_span) != 2
    ):
        return None
    start, end = raw_span
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end < start
        or end > text_length
    ):
        return None
    return start, end


def _table_row_anchor(
    *,
    page: CanonicalPage,
    question: Question,
    flank: int,
) -> _TableRowAnchor | None:
    question_terms = _normalized_row_label_terms(
        "\n".join([question.text, *question.options.values()])
    )
    if not question_terms or not page.tables:
        return None

    scored_rows: list[
        tuple[int, tuple[str, ...], CanonicalTable, int, tuple[str, ...]]
    ] = []
    for table in page.tables:
        row_offset = 1 if table.headers else 0
        for row_index, raw_row in enumerate(table.rows, start=row_offset):
            row = tuple(str(cell or "") for cell in raw_row)
            if (
                not row
                or not row[0].strip()
                or not any(cell.strip() for cell in row[1:])
            ):
                continue
            score, matched = _row_label_match_score(row[0], question_terms)
            scored_rows.append((score, matched, table, row_index, row))

    if not scored_rows:
        return None
    top_score = max(item[0] for item in scored_rows)
    if top_score <= 0:
        return None
    winners = [item for item in scored_rows if item[0] == top_score]
    if len(winners) != 1:
        return None

    score, matched_terms, table, row_index, row = winners[0]
    source_object_id = _table_source_object_id(table)
    if not source_object_id:
        return None
    coordinate_spans = page.metadata.get("coordinate_spans")
    if not isinstance(coordinate_spans, Mapping):
        coordinate_spans = table.metadata.get("coordinate_spans")
    if not isinstance(coordinate_spans, Mapping):
        return None

    coordinates = tuple(
        f"{source_object_id}/r{row_index}c{column_index}"
        for column_index in range(len(row))
    )
    spans: list[tuple[int, int]] = []
    for coordinate in coordinates:
        span = _valid_coordinate_span(
            coordinate_spans,
            coordinate,
            text_length=len(page.text),
        )
        if span is None:
            return None
        spans.append(span)
    if not spans:
        return None

    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    if end <= start:
        return None
    snippet = page.text[start:end]
    if not snippet or page.text.find(snippet) != start:
        return None
    if page.text.find(snippet, start + 1) >= 0:
        return None

    return _TableRowAnchor(
        text=snippet,
        before_text=page.text[max(0, start - flank):start].strip(),
        after_text=page.text[end:min(len(page.text), end + flank)].strip(),
        table_id=table.table_id,
        source_object_id=source_object_id,
        row_index=row_index,
        row_label=row[0],
        match_score=score,
        matched_terms=matched_terms,
        span=(start, end),
        coordinate_ids=coordinates,
    )


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
        terms = _document_query_terms(question)
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
        # An explicit required/candidate scope is already the upstream document
        # selection decision. Do not silently drop scoped documents because of a
        # second document-retrieval top_k. The limit only applies to full-corpus
        # discovery when no explicit scope exists.
        limit = len(explicit) if explicit else max(0, int(self.top_k))
        return tuple(hits[:limit])


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
        anchor = _table_row_anchor(
            page=page,
            question=question,
            flank=self.context_flank_chars,
        )
        if anchor is not None:
            snippet = anchor.text
            before = anchor.before_text
            after = anchor.after_text
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
                **(
                    {
                        "table_row_anchor": {
                            "table_id": anchor.table_id,
                            "source_object_id": anchor.source_object_id,
                            "row_index": anchor.row_index,
                            "row_label": anchor.row_label,
                            "match_score": anchor.match_score,
                            "matched_terms": list(anchor.matched_terms),
                            "span": list(anchor.span),
                            "coordinate_ids": list(anchor.coordinate_ids),
                        }
                    }
                    if anchor is not None
                    else {}
                ),
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
