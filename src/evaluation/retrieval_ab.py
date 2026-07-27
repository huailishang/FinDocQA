"""Zero-API A/B evaluation helpers for retrieval pipelines."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Mapping, Sequence

from contracts import EvidenceAssembler, EvidenceRetriever, Question, QuestionClassifier
from evaluation.layers import RetrievalGold, RetrievalQualityResult, evaluate_retrieval


_GOLD_ONLY_KEYS = {
    "doc_ids",
    "required_doc_ids",
    "required_documents",
    "answer",
    "expected_answer",
    "label",
    "gold",
    "ground_truth",
    "acceptable_page_groups",
    "required_pages",
    "evidence_anchors",
    "evidence_text_anchors",
}
_QUALITY_FIELDS = (
    "document_recall_at_k",
    "complete_document_recall_at_k",
    "page_recall_at_k",
    "acceptable_page_group_recall_at_k",
    "evidence_anchor_recall_at_k",
    "reciprocal_rank_at_k",
    "ndcg_at_k",
)


@dataclass(frozen=True)
class RetrievalABCase:
    case_id: str
    question: Question
    gold: RetrievalGold


@dataclass(frozen=True)
class RetrievalABStrategy:
    name: str
    retriever: EvidenceRetriever
    assembler: EvidenceAssembler


@dataclass(frozen=True)
class RetrievalABCaseMeasurement:
    case_id: str
    raw_quality: RetrievalQualityResult
    solver_quality: RetrievalQualityResult
    retrieval_latency_ms: float
    assembly_latency_ms: float
    prompt_context_chars: int
    estimated_tokens: int
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "raw": self.raw_quality.to_dict(),
            "solver_visible": self.solver_quality.to_dict(),
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "assembly_latency_ms": self.assembly_latency_ms,
            "total_latency_ms": self.retrieval_latency_ms + self.assembly_latency_ms,
            "prompt_context_chars": self.prompt_context_chars,
            "estimated_tokens": self.estimated_tokens,
            "error": self.error,
        }


@dataclass(frozen=True)
class RetrievalABStrategySummary:
    name: str
    cases: tuple[RetrievalABCaseMeasurement, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.name,
            "case_count": len(self.cases),
            "errors": sum(bool(item.error) for item in self.cases),
            "raw": _mean_quality(tuple(item.raw_quality for item in self.cases)),
            "solver_visible": _mean_quality(tuple(item.solver_quality for item in self.cases)),
            "mean_retrieval_latency_ms": _mean(tuple(item.retrieval_latency_ms for item in self.cases)),
            "mean_assembly_latency_ms": _mean(tuple(item.assembly_latency_ms for item in self.cases)),
            "mean_total_latency_ms": _mean(
                tuple(item.retrieval_latency_ms + item.assembly_latency_ms for item in self.cases)
            ),
            "mean_prompt_context_chars": _mean(tuple(float(item.prompt_context_chars) for item in self.cases)),
            "mean_estimated_tokens": _mean(tuple(float(item.estimated_tokens) for item in self.cases)),
            "cases": [item.to_dict() for item in self.cases],
        }


@dataclass(frozen=True)
class RetrievalABReport:
    strategies: tuple[RetrievalABStrategySummary, ...]
    answer_quality_status: str = "not_run_zero_api"

    def to_dict(self) -> dict[str, object]:
        return {
            "answer_quality_status": self.answer_quality_status,
            "strategies": [strategy.to_dict() for strategy in self.strategies],
        }


def _mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _mean_quality(results: Sequence[RetrievalQualityResult]) -> dict[str, float | None]:
    payload: dict[str, float | None] = {}
    for field_name in _QUALITY_FIELDS:
        values = [
            float(value)
            for result in results
            if (value := getattr(result, field_name)) is not None
        ]
        payload[field_name] = mean(values) if values else None
    return payload


def _normalize_page_groups(raw_groups: object) -> tuple[tuple[tuple[str, int], ...], ...]:
    if not isinstance(raw_groups, Sequence) or isinstance(raw_groups, (str, bytes)):
        return ()
    groups: list[tuple[tuple[str, int], ...]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, Sequence) or isinstance(raw_group, (str, bytes)):
            continue
        group: list[tuple[str, int]] = []
        for raw_item in raw_group:
            if (
                isinstance(raw_item, Sequence)
                and not isinstance(raw_item, (str, bytes))
                and len(raw_item) >= 2
            ):
                group.append((str(raw_item[0]), int(raw_item[1])))
        if group:
            groups.append(tuple(group))
    return tuple(groups)


def _normalize_required_pages(raw: object) -> dict[str, tuple[int, ...]]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, tuple[int, ...]] = {}
    for doc_id, pages in raw.items():
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
            continue
        normalized[str(doc_id)] = tuple(int(page) for page in pages)
    return normalized


def load_retrieval_gold_cases(path: Path) -> tuple[RetrievalABCase, ...]:
    """Load retrieval Gold while keeping truth out of the tested question scope."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", []) if isinstance(payload, Mapping) else payload
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
        raise ValueError("retrieval gold must contain a cases array")

    cases: list[RetrievalABCase] = []
    for item in raw_cases:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or item.get("case_id") or "").strip()
        domain = str(item.get("domain") or "").strip()
        text = str(item.get("question") or item.get("question_text") or "").strip()
        if not qid or not domain or not text:
            raise ValueError("each retrieval gold case requires qid/case_id, domain and question")

        raw_options = item.get("options") or {}
        options = (
            {str(key): str(value) for key, value in raw_options.items()}
            if isinstance(raw_options, Mapping)
            else {}
        )
        question = Question(
            qid=qid,
            domain=domain,
            text=text,
            options=options,
            answer_format="multi" if options else "freeform",
            doc_ids=(),
            candidate_doc_ids=(),
            raw={
                str(key): value
                for key, value in item.items()
                if str(key) not in _GOLD_ONLY_KEYS
            },
        )
        evidence_anchors = item.get("evidence_anchors", item.get("evidence_text_anchors", ())) or ()
        cases.append(
            RetrievalABCase(
                case_id=qid,
                question=question,
                gold=RetrievalGold(
                    required_doc_ids=tuple(
                        str(value) for value in item.get("required_doc_ids", ()) or ()
                    ),
                    required_pages=_normalize_required_pages(item.get("required_pages", {})),
                    evidence_text_anchors=tuple(str(value) for value in evidence_anchors),
                    acceptable_page_groups=_normalize_page_groups(
                        item.get("acceptable_page_groups", ())
                    ),
                ),
            )
        )
    return tuple(cases)


def run_retrieval_ab(
    cases: Sequence[RetrievalABCase],
    *,
    classifier: QuestionClassifier,
    strategies: Sequence[RetrievalABStrategy],
    k: int = 20,
) -> RetrievalABReport:
    """Compare retrieval paths through the final solver-visible evidence boundary."""
    summaries: list[RetrievalABStrategySummary] = []
    for strategy in strategies:
        measurements: list[RetrievalABCaseMeasurement] = []
        for case in cases:
            classification = classifier.classify(case.question)
            retrieval_started = perf_counter()
            error = ""
            try:
                candidates = tuple(strategy.retriever.retrieve(case.question, classification))
            except Exception as exc:  # keep A/B batch comparable even when one case fails
                candidates = ()
                error = f"retrieval:{type(exc).__name__}: {exc}"
            retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000.0
            raw_quality = evaluate_retrieval(candidates, case.gold, k=k)

            assembly_started = perf_counter()
            bundle = None
            if not error:
                try:
                    bundle = strategy.assembler.assemble(
                        case.question, classification, candidates
                    )
                except Exception as exc:
                    error = f"assembly:{type(exc).__name__}: {exc}"
            assembly_latency_ms = (perf_counter() - assembly_started) * 1000.0
            solver_candidates = tuple(bundle.candidates) if bundle is not None else ()
            solver_quality = evaluate_retrieval(solver_candidates, case.gold, k=k)
            measurements.append(
                RetrievalABCaseMeasurement(
                    case_id=case.case_id,
                    raw_quality=raw_quality,
                    solver_quality=solver_quality,
                    retrieval_latency_ms=retrieval_latency_ms,
                    assembly_latency_ms=assembly_latency_ms,
                    prompt_context_chars=len(bundle.prompt_context) if bundle is not None else 0,
                    estimated_tokens=int(bundle.estimated_tokens) if bundle is not None else 0,
                    error=error,
                )
            )
        summaries.append(
            RetrievalABStrategySummary(name=strategy.name, cases=tuple(measurements))
        )
    return RetrievalABReport(strategies=tuple(summaries))
