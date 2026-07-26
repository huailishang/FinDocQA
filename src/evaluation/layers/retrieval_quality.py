"""E2: retrieval/evidence quality evaluation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from contracts import EvidenceCandidate

_CANONICAL_PAGE_RE = re.compile(r"/page/(\d+)$")
_PAGE_FILE_RE = re.compile(r"page_(\d+)\.md$", re.IGNORECASE)


def _candidate_page(candidate: EvidenceCandidate) -> int | None:
    raw = candidate.metadata.get("page_number") if candidate.metadata else None
    if isinstance(raw, int):
        return raw
    lineage = candidate.metadata.get("lineage") if candidate.metadata else None
    if isinstance(lineage, Mapping):
        raw = lineage.get("page_number")
        if isinstance(raw, int):
            return raw
    match = _CANONICAL_PAGE_RE.search(candidate.source or "") or _PAGE_FILE_RE.search(
        candidate.source or ""
    )
    return int(match.group(1)) if match else None


def _ratio(hit: int, total: int) -> float | None:
    return None if total == 0 else hit / total


@dataclass(frozen=True)
class RetrievalGold:
    required_doc_ids: tuple[str, ...] = ()
    required_pages: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    evidence_text_anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalQualityResult:
    document_recall_at_k: float | None
    complete_document_recall_at_k: float | None
    page_recall_at_k: float | None
    evidence_anchor_recall_at_k: float | None
    retrieved_documents: tuple[str, ...]
    retrieved_pages: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "document_recall_at_k": self.document_recall_at_k,
            "complete_document_recall_at_k": self.complete_document_recall_at_k,
            "page_recall_at_k": self.page_recall_at_k,
            "evidence_anchor_recall_at_k": self.evidence_anchor_recall_at_k,
            "retrieved_documents": list(self.retrieved_documents),
            "retrieved_pages": [list(item) for item in self.retrieved_pages],
        }


def evaluate_retrieval(
    candidates: Sequence[EvidenceCandidate],
    gold: RetrievalGold,
    *,
    k: int = 5,
) -> RetrievalQualityResult:
    top = tuple(candidates[: max(0, int(k))])
    retrieved_docs = tuple(dict.fromkeys(candidate.doc_id for candidate in top))
    retrieved_doc_set = set(retrieved_docs)
    required_docs = set(gold.required_doc_ids)
    doc_hit = len(required_docs & retrieved_doc_set)

    retrieved_pages_list: list[tuple[str, int]] = []
    for candidate in top:
        page = _candidate_page(candidate)
        if page is not None:
            key = (candidate.doc_id, page)
            if key not in retrieved_pages_list:
                retrieved_pages_list.append(key)
    retrieved_pages = tuple(retrieved_pages_list)
    retrieved_page_set = set(retrieved_pages)

    gold_pages = {
        (doc_id, int(page))
        for doc_id, pages in gold.required_pages.items()
        for page in pages
    }
    page_hit = len(gold_pages & retrieved_page_set)

    joined_text = "\n".join(
        "\n".join((candidate.before_text, candidate.text, candidate.after_text))
        for candidate in top
    )
    anchor_hit = sum(
        1 for anchor in gold.evidence_text_anchors if anchor and anchor in joined_text
    )

    return RetrievalQualityResult(
        document_recall_at_k=_ratio(doc_hit, len(required_docs)),
        complete_document_recall_at_k=(
            None if not required_docs else float(required_docs.issubset(retrieved_doc_set))
        ),
        page_recall_at_k=_ratio(page_hit, len(gold_pages)),
        evidence_anchor_recall_at_k=_ratio(anchor_hit, len(gold.evidence_text_anchors)),
        retrieved_documents=retrieved_docs,
        retrieved_pages=retrieved_pages,
    )
