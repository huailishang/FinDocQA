"""Small E2 benchmark runner for comparing retrieval strategies."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from time import perf_counter
from typing import Protocol, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question
from .retrieval_quality import (
    RetrievalGold,
    RetrievalStrategyResult,
    evaluate_retrieval_strategy,
)


class RetrievalStrategy(Protocol):
    name: str

    def retrieve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> Sequence[EvidenceCandidate]:
        ...


@dataclass(frozen=True)
class RetrievalBenchmarkCase:
    case_id: str
    question: Question
    classification: ClassificationResult
    gold: RetrievalGold


@dataclass(frozen=True)
class RetrievalBenchmarkStrategy:
    name: str
    retriever: RetrievalStrategy
    api_calls_per_case: int = 0
    estimated_cost_per_case: float | None = None


@dataclass(frozen=True)
class RetrievalCaseMeasurement:
    case_id: str
    result: RetrievalStrategyResult


@dataclass(frozen=True)
class RetrievalBenchmarkSummary:
    strategy: str
    case_count: int
    document_recall_at_k: float | None
    complete_document_recall_at_k: float | None
    page_recall_at_k: float | None
    acceptable_page_group_recall_at_k: float | None
    evidence_anchor_recall_at_k: float | None
    reciprocal_rank_at_k: float | None
    ndcg_at_k: float | None
    mean_latency_ms: float | None
    p95_latency_ms: float | None
    total_api_calls: int
    total_estimated_cost: float | None
    cases: tuple[RetrievalCaseMeasurement, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "case_count": self.case_count,
            "document_recall_at_k": self.document_recall_at_k,
            "complete_document_recall_at_k": self.complete_document_recall_at_k,
            "page_recall_at_k": self.page_recall_at_k,
            "acceptable_page_group_recall_at_k": self.acceptable_page_group_recall_at_k,
            "evidence_anchor_recall_at_k": self.evidence_anchor_recall_at_k,
            "reciprocal_rank_at_k": self.reciprocal_rank_at_k,
            "ndcg_at_k": self.ndcg_at_k,
            "mean_latency_ms": self.mean_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "total_api_calls": self.total_api_calls,
            "total_estimated_cost": self.total_estimated_cost,
            "cases": [
                {"case_id": item.case_id, **item.result.to_dict()}
                for item in self.cases
            ],
        }


def _mean_optional(values: Sequence[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return mean(present) if present else None


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) * 0.95) + 0.999999) - 1))
    return ordered[index]


def run_retrieval_benchmark(
    cases: Sequence[RetrievalBenchmarkCase],
    strategy: RetrievalBenchmarkStrategy,
    *,
    k: int = 10,
) -> RetrievalBenchmarkSummary:
    """Run one retrieval strategy over a fixed Gold case set."""
    measurements: list[RetrievalCaseMeasurement] = []
    latencies: list[float] = []
    for case in cases:
        started = perf_counter()
        candidates = strategy.retriever.retrieve(case.question, case.classification)
        latency_ms = (perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        result = evaluate_retrieval_strategy(
            strategy.name,
            candidates,
            case.gold,
            k=k,
            latency_ms=latency_ms,
            api_calls=strategy.api_calls_per_case,
            estimated_cost=strategy.estimated_cost_per_case,
        )
        measurements.append(RetrievalCaseMeasurement(case_id=case.case_id, result=result))

    qualities = [item.result.quality for item in measurements]
    total_cost = None
    if strategy.estimated_cost_per_case is not None:
        total_cost = float(strategy.estimated_cost_per_case) * len(measurements)

    return RetrievalBenchmarkSummary(
        strategy=strategy.name,
        case_count=len(measurements),
        document_recall_at_k=_mean_optional([q.document_recall_at_k for q in qualities]),
        complete_document_recall_at_k=_mean_optional(
            [q.complete_document_recall_at_k for q in qualities]
        ),
        page_recall_at_k=_mean_optional([q.page_recall_at_k for q in qualities]),
        acceptable_page_group_recall_at_k=_mean_optional(
            [q.acceptable_page_group_recall_at_k for q in qualities]
        ),
        evidence_anchor_recall_at_k=_mean_optional(
            [q.evidence_anchor_recall_at_k for q in qualities]
        ),
        reciprocal_rank_at_k=_mean_optional([q.reciprocal_rank_at_k for q in qualities]),
        ndcg_at_k=_mean_optional([q.ndcg_at_k for q in qualities]),
        mean_latency_ms=mean(latencies) if latencies else None,
        p95_latency_ms=_p95(latencies),
        total_api_calls=max(0, int(strategy.api_calls_per_case)) * len(measurements),
        total_estimated_cost=total_cost,
        cases=tuple(measurements),
    )
