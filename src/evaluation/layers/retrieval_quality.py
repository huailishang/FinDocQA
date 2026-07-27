"""E2: retrieval/evidence quality evaluation."""
from __future__ import annotations

import math
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


def _candidate_text(candidate: EvidenceCandidate) -> str:
    return "\n".join((candidate.before_text, candidate.text, candidate.after_text))


def _ratio(hit: int, total: int) -> float | None:
    return None if total == 0 else hit / total


def _reciprocal_rank(relevances: Sequence[int]) -> float | None:
    if not relevances:
        return None
    for rank, relevant in enumerate(relevances, start=1):
        if relevant:
            return 1.0 / rank
    return 0.0


def _ndcg(relevances: Sequence[int]) -> float | None:
    if not relevances:
        return None
    dcg = sum(value / math.log2(index + 2) for index, value in enumerate(relevances))
    ideal = sorted(relevances, reverse=True)
    idcg = sum(value / math.log2(index + 2) for index, value in enumerate(ideal))
    return 0.0 if idcg == 0 else dcg / idcg


@dataclass(frozen=True)
class RetrievalGold:
    required_doc_ids: tuple[str, ...] = ()
    # Strict page truth: every listed page is expected to be retrieved.
    required_pages: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    evidence_text_anchors: tuple[str, ...] = ()
    # Each group represents one evidence need with one or more equivalent pages.
    # Example: ((("doc1", 30), ("doc1", 153)),) means either page is acceptable.
    acceptable_page_groups: tuple[tuple[tuple[str, int], ...], ...] = ()


@dataclass(frozen=True)
class RetrievalQualityResult:
    document_recall_at_k: float | None
    complete_document_recall_at_k: float | None
    page_recall_at_k: float | None
    acceptable_page_group_recall_at_k: float | None
    evidence_anchor_recall_at_k: float | None
    reciprocal_rank_at_k: float | None
    ndcg_at_k: float | None
    retrieved_documents: tuple[str, ...]
    retrieved_pages: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "document_recall_at_k": self.document_recall_at_k,
            "complete_document_recall_at_k": self.complete_document_recall_at_k,
            "page_recall_at_k": self.page_recall_at_k,
            "acceptable_page_group_recall_at_k": self.acceptable_page_group_recall_at_k,
            "evidence_anchor_recall_at_k": self.evidence_anchor_recall_at_k,
            "reciprocal_rank_at_k": self.reciprocal_rank_at_k,
            "ndcg_at_k": self.ndcg_at_k,
            "retrieved_documents": list(self.retrieved_documents),
            "retrieved_pages": [list(item) for item in self.retrieved_pages],
        }


@dataclass(frozen=True)
class RetrievalStrategyResult:
    """One retrieval strategy measurement for A/B comparison."""

    strategy: str
    quality: RetrievalQualityResult
    latency_ms: float | None = None
    api_calls: int = 0
    estimated_cost: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            **self.quality.to_dict(),
            "latency_ms": self.latency_ms,
            "api_calls": self.api_calls,
            "estimated_cost": self.estimated_cost,
        }


def _candidate_relevant(
    candidate: EvidenceCandidate,
    *,
    required_docs: set[str],
    gold_pages: set[tuple[str, int]],
    acceptable_pages: set[tuple[str, int]],
    anchors: Sequence[str],
) -> int:
    page = _candidate_page(candidate)
    page_key = (candidate.doc_id, page) if page is not None else None
    # Page-level Gold is the strongest ranking truth. Once explicit or
    # equivalent acceptable pages exist, a different page must not become
    # relevant merely because it repeats the same anchor text elsewhere.
    if gold_pages or acceptable_pages:
        return int(page_key in gold_pages or page_key in acceptable_pages)

    text = _candidate_text(candidate)
    if anchors and any(anchor and anchor in text for anchor in anchors):
        # When document truth is also known, keep anchor relevance bound to the
        # Gold document instead of rewarding an identical phrase in another doc.
        return int(not required_docs or candidate.doc_id in required_docs)
    if required_docs:
        return int(candidate.doc_id in required_docs)
    return 0


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

    normalized_groups = tuple(
        tuple((str(doc_id), int(page)) for doc_id, page in group)
        for group in gold.acceptable_page_groups
        if group
    )
    acceptable_pages = {page for group in normalized_groups for page in group}
    group_hit = sum(1 for group in normalized_groups if set(group) & retrieved_page_set)

    joined_text = "\n".join(_candidate_text(candidate) for candidate in top)
    anchor_hit = sum(
        1 for anchor in gold.evidence_text_anchors if anchor and anchor in joined_text
    )

    relevances = tuple(
        _candidate_relevant(
            candidate,
            required_docs=required_docs,
            gold_pages=gold_pages,
            acceptable_pages=acceptable_pages,
            anchors=gold.evidence_text_anchors,
        )
        for candidate in top
    )

    return RetrievalQualityResult(
        document_recall_at_k=_ratio(doc_hit, len(required_docs)),
        complete_document_recall_at_k=(
            None if not required_docs else float(required_docs.issubset(retrieved_doc_set))
        ),
        page_recall_at_k=_ratio(page_hit, len(gold_pages)),
        acceptable_page_group_recall_at_k=_ratio(group_hit, len(normalized_groups)),
        evidence_anchor_recall_at_k=_ratio(anchor_hit, len(gold.evidence_text_anchors)),
        reciprocal_rank_at_k=_reciprocal_rank(relevances),
        ndcg_at_k=_ndcg(relevances),
        retrieved_documents=retrieved_docs,
        retrieved_pages=retrieved_pages,
    )


def evaluate_retrieval_strategy(
    strategy: str,
    candidates: Sequence[EvidenceCandidate],
    gold: RetrievalGold,
    *,
    k: int = 5,
    latency_ms: float | None = None,
    api_calls: int = 0,
    estimated_cost: float | None = None,
) -> RetrievalStrategyResult:
    """Evaluate one strategy with quality plus operational measurements."""
    return RetrievalStrategyResult(
        strategy=str(strategy),
        quality=evaluate_retrieval(candidates, gold, k=k),
        latency_ms=None if latency_ms is None else float(latency_ms),
        api_calls=max(0, int(api_calls)),
        estimated_cost=None if estimated_cost is None else float(estimated_cost),
    )
