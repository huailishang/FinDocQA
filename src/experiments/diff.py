"""Per-question diff engine (D-OFFLINE-2).

Compares two ``ExperimentResult`` values question by question and reports what
changed: answer, solver route, evidence count/sources, fallback, degraded,
warnings, finish_reason, token cost. The engine is symmetric and deterministic
— diff(A, B) and diff(B, A) report the same set of changed qids with mirrored
directions.

The diff is the core of Workstream D's "fixed comparison contracts": without
gold labels we cannot claim accuracy, but we CAN claim answer stability /
regression and attribute it to a changed variable (corpus, chunking, solver).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from .manifest import ExperimentResult, QuestionResult


@dataclass(frozen=True)
class QuestionDiff:
    """Diff for one qid present in both experiments."""

    qid: str
    answer_changed: bool
    solver_changed: bool
    evidence_count_delta: int
    evidence_sources_changed: bool
    fallback_changed: bool
    degraded_changed: bool
    warnings_changed: bool
    finish_reason_changed: bool
    token_delta: int
    # human-readable field-level notes for the report
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DiffResult:
    """Aggregate diff between two experiments."""

    baseline_id: str
    candidate_id: str
    common_qids: Tuple[str, ...]
    only_in_baseline: Tuple[str, ...]
    only_in_candidate: Tuple[str, ...]
    question_diffs: Tuple[QuestionDiff, ...]
    answer_change_count: int
    solver_change_count: int
    total_token_delta: int
    fallback_delta: int
    degraded_delta: int
    length_finish_delta: int
    summary: Mapping[str, int]


class DiffEngine:
    """Stateless diff engine wrapped as a class for discoverability."""

    def diff(
        self,
        baseline: ExperimentResult,
        candidate: ExperimentResult,
    ) -> DiffResult:
        return diff_experiments(baseline, candidate)


def _index_by_qid(results: Tuple[QuestionResult, ...]) -> Dict[str, QuestionResult]:
    return {r.qid: r for r in results}


def diff_experiments(
    baseline: ExperimentResult,
    candidate: ExperimentResult,
) -> DiffResult:
    """Compute the deterministic per-question diff between two experiments."""
    base = _index_by_qid(baseline.manifest.results)
    cand = _index_by_qid(candidate.manifest.results)

    common = sorted(set(base) & set(cand))
    only_base = sorted(set(base) - set(cand))
    only_cand = sorted(set(cand) - set(base))

    qdiffs: List[QuestionDiff] = []
    answer_changes = 0
    solver_changes = 0
    token_delta_total = 0
    fallback_delta = 0
    degraded_delta = 0
    length_delta = 0

    for qid in common:
        b = base[qid]
        c = cand[qid]

        answer_changed = b.answer != c.answer
        solver_changed = b.solver != c.solver
        ev_delta = c.evidence_count - b.evidence_count
        ev_src_changed = set(b.evidence_sources) != set(c.evidence_sources)
        fallback_changed = b.fallback_used != c.fallback_used
        degraded_changed = b.degraded != c.degraded
        warnings_changed = tuple(b.warnings) != tuple(c.warnings)
        finish_changed = (b.finish_reason or None) != (c.finish_reason or None)
        t_delta = c.total_tokens - b.total_tokens

        if answer_changed:
            answer_changes += 1
        if solver_changed:
            solver_changes += 1
        token_delta_total += t_delta
        fallback_delta += (1 if c.fallback_used else 0) - (1 if b.fallback_used else 0)
        degraded_delta += (1 if c.degraded else 0) - (1 if b.degraded else 0)
        if b.finish_reason == "length":
            length_delta -= 1
        if c.finish_reason == "length":
            length_delta += 1

        notes: List[str] = []
        if answer_changed:
            notes.append(f"answer: {b.answer} -> {c.answer}")
        if solver_changed:
            notes.append(f"solver: {b.solver} -> {c.solver}")
        if ev_delta:
            notes.append(f"evidence_count: {b.evidence_count} -> {c.evidence_count}")
        if ev_src_changed:
            notes.append("evidence_sources changed")
        if fallback_changed:
            notes.append(f"fallback: {b.fallback_used} -> {c.fallback_used}")
        if degraded_changed:
            notes.append(f"degraded: {b.degraded} -> {c.degraded}")
        if warnings_changed:
            notes.append("warnings changed")
        if finish_changed:
            notes.append(f"finish_reason: {b.finish_reason} -> {c.finish_reason}")
        if t_delta:
            notes.append(f"tokens: {b.total_tokens} -> {c.total_tokens}")

        qdiffs.append(QuestionDiff(
            qid=qid,
            answer_changed=answer_changed,
            solver_changed=solver_changed,
            evidence_count_delta=ev_delta,
            evidence_sources_changed=ev_src_changed,
            fallback_changed=fallback_changed,
            degraded_changed=degraded_changed,
            warnings_changed=warnings_changed,
            finish_reason_changed=finish_changed,
            token_delta=t_delta,
            notes=tuple(notes),
        ))

    summary: Dict[str, int] = {
        "common_questions": len(common),
        "only_in_baseline": len(only_base),
        "only_in_candidate": len(only_cand),
        "answer_changes": answer_changes,
        "solver_changes": solver_changes,
        "fallback_delta": fallback_delta,
        "degraded_delta": degraded_delta,
        "length_finish_delta": length_delta,
    }

    return DiffResult(
        baseline_id=baseline.manifest.experiment_id,
        candidate_id=candidate.manifest.experiment_id,
        common_qids=tuple(common),
        only_in_baseline=tuple(only_base),
        only_in_candidate=tuple(only_cand),
        question_diffs=tuple(qdiffs),
        answer_change_count=answer_changes,
        solver_change_count=solver_changes,
        total_token_delta=token_delta_total,
        fallback_delta=fallback_delta,
        degraded_delta=degraded_delta,
        length_finish_delta=length_delta,
        summary=summary,
    )
