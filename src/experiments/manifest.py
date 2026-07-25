"""Experiment manifest and per-question result schema (D-OFFLINE-1).

Defines the contracts an experiment must record so Workstream D can compare
experiments deterministically. The schema mirrors the per-question fields
listed in ``docs/p7d-parallel-workstreams.md`` Workstream D (qid, answer
format, doc_ids, labels, solver, evidence sources/snippets, fallback/degraded/
warning metadata, final answer, token/latency data).

Experiment identities (A0/A1/B1/C1/AB/AC/ABC) are encoded in
``ExperimentManifest.experiment_id`` so the combination matrix can be assembled
without ambiguity. Manifests are frozen dataclasses with tuple fields so two
runs over the same artifacts compare equal and hash safely in offline tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class QuestionResult:
    """One question's recorded outcome under a given experiment.

    Fields are intentionally a superset of what the current pipeline debug
    metadata exposes, so the harness can ingest today's ``output/debug_results.json``
    shape as well as future structure-aware runs. Missing optional fields use
    sensible defaults and never crash the diff engine.
    """

    qid: str
    domain: str
    answer_format: str  # mcq | multi | tf
    doc_ids: Tuple[str, ...]
    labels: Tuple[str, ...]
    solver: str
    answer: str
    evidence_sources: Tuple[str, ...] = ()
    evidence_count: int = 0
    fallback_used: bool = False
    degraded: bool = False
    warnings: Tuple[str, ...] = ()
    finish_reason: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentManifest:
    """Identity + provenance + results for one experiment run.

    ``experiment_id`` follows the Workstream D convention (A0=frozen baseline,
    A1=MinerU only, B1=structure chunks only, C1=composite solver only,
    AB/AC/ABC=combinations). ``changed_variables`` lists what differs from A0
    so the combination matrix can attribute deltas.
    """

    experiment_id: str
    commit: str
    config: str
    corpus_root: str
    changed_variables: Tuple[str, ...]
    results: Tuple[QuestionResult, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentResult:
    """A manifest plus aggregate rollups computed at build time."""

    manifest: ExperimentManifest
    question_count: int
    total_tokens: int
    avg_tokens: float
    fallback_count: int
    degraded_count: int
    length_finish_count: int
    solver_distribution: Mapping[str, int]
    answer_distribution: Mapping[str, int]


def build_manifest(
    experiment_id: str,
    commit: str,
    config: str,
    corpus_root: str,
    results: Sequence[QuestionResult],
    *,
    changed_variables: Sequence[str] = (),
    metadata: Optional[Mapping[str, Any]] = None,
) -> ExperimentResult:
    """Assemble a manifest and compute deterministic aggregate rollups.

    Pure function: identical inputs yield identical ``ExperimentResult``. Used
    both by real artifact loaders (future) and by fixture-based tests.
    """
    res_tuple = tuple(results)
    manifest = ExperimentManifest(
        experiment_id=experiment_id,
        commit=commit,
        config=config,
        corpus_root=corpus_root,
        changed_variables=tuple(changed_variables),
        results=res_tuple,
        metadata=metadata or {},
    )
    qcount = len(res_tuple)
    total = sum(r.total_tokens for r in res_tuple)
    fallback = sum(1 for r in res_tuple if r.fallback_used)
    degraded = sum(1 for r in res_tuple if r.degraded)
    length_finish = sum(1 for r in res_tuple if r.finish_reason == "length")
    solver_dist: dict[str, int] = {}
    for r in res_tuple:
        solver_dist[r.solver] = solver_dist.get(r.solver, 0) + 1
    answer_dist: dict[str, int] = {}
    for r in res_tuple:
        answer_dist[r.answer] = answer_dist.get(r.answer, 0) + 1
    return ExperimentResult(
        manifest=manifest,
        question_count=qcount,
        total_tokens=total,
        avg_tokens=(total / qcount) if qcount else 0.0,
        fallback_count=fallback,
        degraded_count=degraded,
        length_finish_count=length_finish,
        solver_distribution=solver_dist,
        answer_distribution=answer_dist,
    )
