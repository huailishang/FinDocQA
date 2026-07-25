"""Error taxonomy and combination-matrix report generator (D-OFFLINE-3).

Produces deterministic, fixture-driven reports:

- ``generate_error_taxonomy_report``: classify per-question failures using the
  taxonomy from ``docs/p6j-post-submission-score-gap-triage.md`` (format,
  doc_id_mapping, missing_processed_doc, retrieval_miss, retrieval_noise,
  solver_reasoning, multi_choice_overselect/underselect, calculation_error,
  cross_doc_mixing, answer_normalization, truncation, model_variance,
  unknown_needs_more_evidence). Without gold labels we cannot claim accuracy,
  but we CAN flag structural risk signals (fallback, degraded, length finish,
  empty evidence, solver mismatch) deterministically.
- ``generate_combination_report``: given a set of ExperimentResult + DiffResult
  pairs against A0, summarize the combination matrix (A0/A1/B1/C1/AB/AC/ABC)
  so the Final Reviewer can decide which combinations are worth a controlled
  100-question run.

All reports are plain text/markdown strings and require no LLM. They are
exercised by synthetic fixtures under ``tests/fixtures/p7d_d/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .diff import DiffResult
from .manifest import ExperimentResult, QuestionResult

# Taxonomy categories (single primary label per question). Mirrors
# docs/p6j-post-submission-score-gap-triage.md so downstream tooling shares
# vocabulary with the P6j triage.
ERROR_CATEGORIES: Tuple[str, ...] = (
    "format_or_submission",
    "doc_id_mapping",
    "missing_processed_doc",
    "retrieval_miss",
    "retrieval_noise",
    "solver_reasoning",
    "multi_choice_overselect",
    "multi_choice_underselect",
    "calculation_error",
    "cross_doc_mixing",
    "answer_normalization",
    "truncation",
    "model_variance",
    "unknown_needs_more_evidence",
)


@dataclass(frozen=True)
class TaxonomyEntry:
    qid: str
    category: str
    reason: str
    signals: Tuple[str, ...]


@dataclass(frozen=True)
class ErrorTaxonomyReport:
    experiment_id: str
    question_count: int
    flagged_count: int
    by_category: Mapping[str, int]
    entries: Tuple[TaxonomyEntry, ...]
    text: str


@dataclass(frozen=True)
class CombinationRow:
    experiment_id: str
    changed_variables: Tuple[str, ...]
    common_questions: int
    answer_changes: int
    solver_changes: int
    total_token_delta: int
    fallback_delta: int
    degraded_delta: int
    length_finish_delta: int
    verdict: str  # recommend | reject | inconclusive


@dataclass(frozen=True)
class CombinationReport:
    baseline_id: str
    rows: Tuple[CombinationRow, ...]
    text: str


def _classify_question(r: QuestionResult) -> Optional[TaxonomyEntry]:
    """Assign a deterministic primary error category from observable signals.

    This is a signal-based heuristic, NOT a gold-label judgment. It flags
    structural risk; a human (or future gold-set step) must confirm the
    category. Returns None when no risk signal is present.

    Signals considered (priority high -> low):

    - P7E ``truncation_risk=True`` OR ``finish_reason=length`` -> truncation;
    - ``missing_doc`` warning OR empty evidence -> missing_processed_doc /
      retrieval_miss;
    - calc-routed AND ``computation_complete=False`` -> calculation_error
      (P7E);
    - ``fallback_used`` -> retrieval_miss;
    - P7E ``unsupported_guess`` / ``llm_text_guess`` / ``high_risk`` / degraded
      -> solver_reasoning.
    """
    meta = r.metadata if isinstance(r.metadata, Mapping) else {}
    answer_source = str(meta.get("answer_source", "") or "")
    truncation_risk = meta.get("truncation_risk") is True
    high_risk = meta.get("high_risk") is True
    computation_complete = meta.get("computation_complete")
    is_calc = r.solver == "calculation" or "calculation" in r.labels

    signals: List[str] = []
    if r.fallback_used:
        signals.append("fallback_used")
    if r.degraded:
        signals.append("degraded")
    if r.finish_reason == "length":
        signals.append("finish_reason=length")
    if truncation_risk:
        signals.append("truncation_risk")
    if r.evidence_count == 0:
        signals.append("empty_evidence")
    if "missing_doc" in " ".join(r.warnings).lower():
        signals.append("missing_doc_warning")
    if answer_source in ("unsupported_guess", "unsupported_guess_truncated"):
        signals.append("unsupported_guess")
    if high_risk:
        signals.append("high_risk")
    if answer_source == "llm_text_guess":
        signals.append("llm_text_guess")
    if is_calc and computation_complete is False:
        signals.append("calculation_incomplete")

    if not signals:
        return None

    # Priority: truncation > missing_processed_doc > calculation_error >
    # solver_reasoning (degraded/high_risk/unsupported/llm_guess) >
    # retrieval_miss (fallback/empty_evidence) > unknown.
    if "finish_reason=length" in signals or "truncation_risk" in signals:
        category, reason = "truncation", "output truncated (finish_reason=length or truncation_risk)"
    elif "missing_doc_warning" in signals:
        category, reason = "missing_processed_doc", "missing_doc warning for requested doc_ids"
    elif "calculation_incomplete" in signals:
        category, reason = "calculation_error", "calculation-routed but computation_complete=False"
    elif "unsupported_guess" in signals or "llm_text_guess" in signals \
            or "high_risk" in signals or "degraded" in signals:
        category, reason = "solver_reasoning", "solver produced ungrounded/high-risk/degraded answer"
    elif "fallback_used" in signals or "empty_evidence" in signals:
        category, reason = "retrieval_miss", "retrieval found nothing usable (fallback or empty evidence)"
    else:
        category, reason = "unknown_needs_more_evidence", "risk signal without clear cause"

    return TaxonomyEntry(
        qid=r.qid, category=category, reason=reason, signals=tuple(signals)
    )


def generate_error_taxonomy_report(
    experiment: ExperimentResult,
) -> ErrorTaxonomyReport:
    """Classify each question's risk signals and render a markdown report."""
    entries: List[TaxonomyEntry] = []
    by_cat: Dict[str, int] = {c: 0 for c in ERROR_CATEGORIES}
    for r in experiment.manifest.results:
        entry = _classify_question(r)
        if entry is None:
            continue
        entries.append(entry)
        by_cat[entry.category] = by_cat.get(entry.category, 0) + 1

    lines: List[str] = [
        f"# Error Taxonomy Report — {experiment.manifest.experiment_id}",
        "",
        f"- commit: `{experiment.manifest.commit}`",
        f"- corpus_root: `{experiment.manifest.corpus_root}`",
        f"- changed_variables: {list(experiment.manifest.changed_variables) or 'none'}",
        f"- questions: {experiment.question_count}",
        f"- flagged: {len(entries)}",
        "",
        "## By category",
        "",
        "| category | count |",
        "| --- | ---: |",
    ]
    for cat in ERROR_CATEGORIES:
        if by_cat.get(cat, 0):
            lines.append(f"| {cat} | {by_cat[cat]} |")
    lines += ["", "## Flagged questions", "",
              "| qid | category | reason | signals |",
              "| --- | --- | --- | --- |"]
    for e in entries:
        lines.append(f"| {e.qid} | {e.category} | {e.reason} | {', '.join(e.signals)} |")
    if not entries:
        lines.append("| _none_ | — | no risk signals observed | — |")

    text = "\n".join(lines) + "\n"
    return ErrorTaxonomyReport(
        experiment_id=experiment.manifest.experiment_id,
        question_count=experiment.question_count,
        flagged_count=len(entries),
        by_category={k: v for k, v in by_cat.items() if v},
        entries=tuple(entries),
        text=text,
    )


def _verdict(diff: DiffResult, candidate: ExperimentResult) -> str:
    """Decide a conservative combination verdict from the diff.

    Without gold labels we cannot prove improvement, so the bar is "no
    regression on structural signals". Any answer change is treated as
    inconclusive (could be improvement or regression); fallback/degraded/
    length regressions reject the combination.
    """
    if diff.degraded_delta > 0 or diff.fallback_delta > 0 or diff.length_finish_delta > 0:
        return "reject"
    if diff.answer_change_count > 0:
        return "inconclusive"
    return "recommend"


def generate_combination_report(
    baseline: ExperimentResult,
    candidates: Sequence[Tuple[ExperimentResult, DiffResult]],
) -> CombinationReport:
    """Render the combination matrix (A0 vs each candidate) as markdown."""
    rows: List[CombinationRow] = []
    for cand, diff in candidates:
        rows.append(CombinationRow(
            experiment_id=cand.manifest.experiment_id,
            changed_variables=cand.manifest.changed_variables,
            common_questions=len(diff.common_qids),
            answer_changes=diff.answer_change_count,
            solver_changes=diff.solver_change_count,
            total_token_delta=diff.total_token_delta,
            fallback_delta=diff.fallback_delta,
            degraded_delta=diff.degraded_delta,
            length_finish_delta=diff.length_finish_delta,
            verdict=_verdict(diff, cand),
        ))

    lines: List[str] = [
        f"# Combination Matrix Report — baseline `{baseline.manifest.experiment_id}`",
        "",
        f"- baseline commit: `{baseline.manifest.commit}`",
        f"- baseline questions: {baseline.question_count}",
        f"- baseline total_tokens: {baseline.total_tokens}",
        "",
        "## Candidate combinations",
        "",
        "| experiment | changed variables | common Q | answer chg | solver chg | token Δ | fallback Δ | degraded Δ | length Δ | verdict |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.experiment_id} | {', '.join(row.changed_variables) or '—'} | "
            f"{row.common_questions} | {row.answer_changes} | {row.solver_changes} | "
            f"{row.total_token_delta:+d} | {row.fallback_delta:+d} | "
            f"{row.degraded_delta:+d} | {row.length_finish_delta:+d} | {row.verdict} |"
        )
    if not rows:
        lines.append("| _no candidates provided_ | | | | | | | | | |")

    lines += ["", "## Verdict legend", "",
               "- `recommend`: no answer change AND no structural regression "
               "(fallback/degraded/length). Safe to advance to controlled 100-Q run.",
               "- `inconclusive`: answer(s) changed but no structural regression. "
               "Needs gold-set or targeted validation before advancing.",
               "- `reject`: structural regression detected. Do not advance."]

    text = "\n".join(lines) + "\n"
    return CombinationReport(
        baseline_id=baseline.manifest.experiment_id,
        rows=tuple(rows),
        text=text,
    )
