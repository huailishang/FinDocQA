"""Composable hybrid evidence retrieval using reciprocal-rank fusion (RRF).

This module is implementation-neutral: lexical, embedding, table-aware, or any
future retriever can participate as long as it implements the common retrieve
contract. A reranker can optionally be applied after fusion.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question
from retrieval.interfaces import EvidenceReranker, IdentityReranker, StoreBoundEvidenceRetriever


@dataclass(frozen=True)
class RetrievalLane:
    """One candidate-producing retrieval lane in a hybrid pipeline."""

    retriever: StoreBoundEvidenceRetriever
    weight: float = 1.0
    name: str | None = None

    @property
    def lane_name(self) -> str:
        return str(self.name or getattr(self.retriever, "name", "retriever"))


def _candidate_key(candidate: EvidenceCandidate) -> tuple[str, str, int | None]:
    raw_page = candidate.metadata.get("page_number") if candidate.metadata else None
    page = raw_page if isinstance(raw_page, int) else None
    return (str(candidate.doc_id), str(candidate.source), page)


class ReciprocalRankFusionRetriever:
    """Merge independent retrievers and optionally rerank the fused candidates."""

    name = "reciprocal_rank_fusion"

    def __init__(
        self,
        lanes: Sequence[RetrievalLane],
        *,
        rrf_k: int = 60,
        per_lane_top_k: int = 20,
        fused_top_k: int = 30,
        reranker: EvidenceReranker | None = None,
        rerank_top_k: int | None = None,
    ) -> None:
        if not lanes:
            raise ValueError("at least one retrieval lane is required")
        if rrf_k < 1:
            raise ValueError("rrf_k must be >= 1")
        self.lanes = tuple(lanes)
        self.rrf_k = int(rrf_k)
        self.per_lane_top_k = max(1, int(per_lane_top_k))
        self.fused_top_k = max(1, int(fused_top_k))
        self.reranker = reranker or IdentityReranker()
        self.rerank_top_k = rerank_top_k

    def retrieve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> Sequence[EvidenceCandidate]:
        scores: dict[tuple[str, str, int | None], float] = {}
        first_seen: dict[tuple[str, str, int | None], EvidenceCandidate] = {}
        lane_hits: dict[tuple[str, str, int | None], list[dict[str, object]]] = {}

        for lane in self.lanes:
            weight = float(lane.weight)
            if weight <= 0:
                continue
            candidates = tuple(
                lane.retriever.retrieve(question, classification)
            )[: self.per_lane_top_k]
            for rank, candidate in enumerate(candidates, start=1):
                key = _candidate_key(candidate)
                scores[key] = scores.get(key, 0.0) + weight / (self.rrf_k + rank)
                first_seen.setdefault(key, candidate)
                lane_hits.setdefault(key, []).append(
                    {
                        "lane": lane.lane_name,
                        "rank": rank,
                        "weight": weight,
                        "original_score": float(candidate.score),
                    }
                )

        ordered_keys = sorted(
            scores,
            key=lambda key: (
                -scores[key],
                key[0],
                key[2] if key[2] is not None else 10**9,
                key[1],
            ),
        )[: self.fused_top_k]

        fused: list[EvidenceCandidate] = []
        for key in ordered_keys:
            candidate = first_seen[key]
            metadata = dict(candidate.metadata or {})
            metadata.update(
                {
                    "fusion_method": "rrf",
                    "fusion_rrf_k": self.rrf_k,
                    "fusion_score": scores[key],
                    "fusion_lanes": tuple(lane_hits[key]),
                }
            )
            fused.append(
                replace(
                    candidate,
                    score=scores[key],
                    retriever=self.name,
                    metadata=metadata,
                )
            )

        if not fused:
            return ()
        reranked = self.reranker.rerank(
            question,
            tuple(fused),
            top_k=self.rerank_top_k,
        )
        return tuple(reranked)
