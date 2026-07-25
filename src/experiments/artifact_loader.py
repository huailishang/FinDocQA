"""Artifact loader: convert output/debug_results.json into QuestionResult.

Reads the per-question debug rows the pipeline writes (the ``PipelineResult``
``asdict`` shape) and normalizes them into the experiment manifest schema.
P7E metadata (``answer_source``, ``high_risk``, ``truncation_risk``,
``computation_complete``, ``ungrounded``, ...) is preserved verbatim in
``QuestionResult.metadata`` so the taxonomy / combination-matrix engines can
use it without a separate probe.

Also reads ``submission.csv`` for token-consistency and answer-consistency
checks (debug answer vs submitted answer; debug token sum vs CSV summary row).

Read-only. No LLM, no pipeline run, no API. Standard-library only.

See ``docs/p7d-workstream-d-remote-offline.md`` and the dispatch card Lane D.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .manifest import ExperimentResult, QuestionResult, build_manifest


def _solver_meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extract solver_result.metadata from a debug row (tolerant)."""
    sr = row.get("solver_result") or {}
    if not isinstance(sr, dict):
        return {}
    meta = sr.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _verification_meta(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Extract verification_result.metadata from a debug row (tolerant).

    Real ``debug_results.json`` (written by ``CsvSubmissionWriter`` via
    ``dataclasses.asdict``) serializes the full ``PipelineResult``, so each row
    carries a ``verification_result`` mapping whose ``metadata`` holds the
    ``HighRiskVerifier`` output: ``high_risk``, ``checks_run``, ``warnings``
    (verifier warn flags) and ``placeholder``.

    Previously the loader only read ``solver_result.metadata`` and missed the
    verifier's ``high_risk`` signal, so most high-risk questions (every
    multi-option / calculation / cross-doc / empty-evidence question is flagged
    high-risk by the verifier) were dropped from ``high_risk_qids``. This is the
    D-R1 fix: merge both metadata layers with explicit priority.
    """
    vr = row.get("verification_result") or {}
    if not isinstance(vr, dict):
        return {}
    meta = vr.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


# Fields that live on the verifier side. ``high_risk`` is special-cased below
# because BOTH layers may set it (the verifier flags every high-risk route;
# solvers additionally flag specific patterns like a truncated multi-choice case).
_VERIFIER_ONLY_FIELDS = frozenset({"checks_run", "placeholder"})


def _merge_metadata(
    solver_meta: Mapping[str, Any],
    verif_meta: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Merge solver and verification metadata with explicit priority.

    Priority rules (D-R1):

    - **solver signals win** for solver-authored fields: ``answer_source``,
      ``truncation_risk``, ``computation_complete``, ``computation_grounded``,
      ``formula_extracted``, ``ungrounded``, ``no_supported_options``,
      ``finish_reason``, ``output_chars``, ``missing_option_judgments``, etc.
      These describe what the solver did and are authoritative for the solver
      layer.
    - **verifier signals win** for verifier-authored fields: ``checks_run``,
      ``placeholder``. These describe what the verifier checked.
    - **``high_risk`` is OR-merged**: ``True`` if either layer says so. The
      verifier flags every high-risk *route* (multi/calc/cross-doc/empty);
      solvers additionally flag specific high-risk *patterns*
      (e.g. a truncated multi-choice case truncation+unsupported). Both signals matter, so neither
      silently overrides the other.
    - **verifier ``warnings``** are stored under ``verifier_warnings`` so they
      do not collide with solver ``warnings`` (which describe solver-layer
      risks like ``truncation_risk`` / ``no_supported_options_fallback``).
    """
    merged: Dict[str, Any] = {}
    # Start with verifier-only fields.
    for k in _VERIFIER_ONLY_FIELDS:
        if k in verif_meta:
            merged[k] = verif_meta[k]
    # Solver fields (solver is authoritative for its own signals).
    merged.update(solver_meta)
    # high_risk: OR-merge (both layers may set it, both matter).
    s_high = solver_meta.get("high_risk")
    v_high = verif_meta.get("high_risk")
    if s_high is True or v_high is True:
        merged["high_risk"] = True
    elif s_high is False or v_high is False:
        # Only set False when at least one layer explicitly said False and
        # neither said True.
        merged["high_risk"] = False
    # Verifier warnings preserved under a distinct key to avoid collision.
    v_warnings = verif_meta.get("warnings")
    if isinstance(v_warnings, list) and v_warnings:
        merged["verifier_warnings"] = list(v_warnings)
    return merged


def _as_tuple(val: Any) -> Tuple[str, ...]:
    if isinstance(val, (list, tuple)):
        return tuple(str(x) for x in val if x is not None)
    return ()


def row_to_question_result(row: Mapping[str, Any]) -> QuestionResult:
    """Convert one debug_results.json row into a ``QuestionResult``.

    Tolerant of missing fields: the debug artifact shape has evolved across
    stages, so every field falls back to a safe default.

    D-R1: merges ``solver_result.metadata`` AND ``verification_result.metadata``
    with explicit priority (see :func:`_merge_metadata`). Previously only the
    solver layer was read, so verifier-flagged ``high_risk`` questions were
    dropped from the rollup.
    """
    solver_meta = _solver_meta(row)
    verif_meta = _verification_meta(row)
    merged_meta = _merge_metadata(solver_meta, verif_meta)
    sr = row.get("solver_result") or {}
    if not isinstance(sr, dict):
        sr = {}
    vr = row.get("verification_result") or {}
    if not isinstance(vr, dict):
        vr = {}
    classification = row.get("classification") or {}
    if not isinstance(classification, dict):
        classification = {}
    row_meta = row.get("metadata") or {}
    if not isinstance(row_meta, dict):
        row_meta = {}

    qid = str(row.get("qid") or "")
    domain = str(row_meta.get("domain") or row.get("domain") or "")
    answer_format = str(row_meta.get("answer_format") or row.get("answer_format") or "mcq")
    doc_ids = _as_tuple(row_meta.get("doc_ids") or row.get("doc_ids"))
    labels = _as_tuple(classification.get("labels"))
    solver = str(sr.get("solver") or row.get("solver") or "unknown")
    answer = str(sr.get("answer") or row.get("answer") or "")
    evidence_sources = _as_tuple(row_meta.get("evidence_sources"))
    evidence_count = int(row_meta.get("evidence_count") or merged_meta.get("evidence_count") or 0)
    fallback_used = bool(row.get("fallback_used") or merged_meta.get("fallback_used") or False)
    degraded = bool(row_meta.get("degraded") or merged_meta.get("degraded") or False)

    # Warnings: combine solver-layer warnings (from solver metadata) with
    # verifier notes (verification_result.notes) so the taxonomy sees both.
    # Verifier warn *flags* live in verif_meta["warnings"] and are also
    # preserved verbatim under merged_meta["verifier_warnings"].
    solver_warnings = _as_tuple(merged_meta.get("warnings") or row_meta.get("warnings"))
    verif_notes = _as_tuple(vr.get("notes"))
    warnings = solver_warnings + tuple(
        n for n in verif_notes if n not in solver_warnings
    )

    finish_reason = merged_meta.get("finish_reason") or row.get("finish_reason")
    finish_reason = str(finish_reason) if finish_reason is not None else None
    prompt_tokens = int(row.get("prompt_tokens") or 0)
    completion_tokens = int(row.get("completion_tokens") or 0)
    total_tokens = int(row.get("total_tokens") or 0)

    return QuestionResult(
        qid=qid, domain=domain, answer_format=answer_format, doc_ids=doc_ids,
        labels=labels, solver=solver, answer=answer,
        evidence_sources=evidence_sources, evidence_count=evidence_count,
        fallback_used=fallback_used, degraded=degraded, warnings=warnings,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        metadata=dict(merged_meta),  # merged solver + verifier metadata
    )


def load_debug_artifact(path: Path) -> List[QuestionResult]:
    """Load a ``debug_results.json`` file into a list of ``QuestionResult``.

    Raises ``FileNotFoundError`` if the file does not exist. Returns ``[]`` for
    an empty or non-list file.
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [row_to_question_result(r) for r in data if isinstance(r, dict)]


def load_submission_csv(path: Path) -> Dict[str, str]:
    """Load a ``submission.csv`` into ``{qid: answer}``.

    Tolerant of column names (``qid``/``question_id``/``id`` and
    ``answer``/``response``/``final_answer``).
    """
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as h:
        rows = [dict(r) for r in csv.DictReader(h)]
    if not rows:
        return {}
    qid_field = "qid"
    answer_field = "answer"
    for c in ("qid", "question_id", "id"):
        if c in rows[0]:
            qid_field = c
            break
    for c in ("answer", "response", "final_answer", "prediction"):
        if c in rows[0]:
            answer_field = c
            break
    return {
        str(r.get(qid_field, "")).strip(): str(r.get(answer_field, "")).strip()
        for r in rows
    }


def build_manifest_from_artifact(
    debug_path: Path,
    *,
    experiment_id: str,
    commit: str,
    config: str,
    corpus_root: str,
    changed_variables: Sequence[str] = (),
) -> ExperimentResult:
    """Build an :class:`ExperimentResult` from a ``debug_results.json`` artifact."""
    results = load_debug_artifact(debug_path)
    return build_manifest(
        experiment_id=experiment_id, commit=commit, config=config,
        corpus_root=corpus_root, results=results,
        changed_variables=changed_variables,
    )


# ── P7E metadata rollup ─────────────────────────────────────────────


def summarize_p7e_metadata(results: Sequence[QuestionResult]) -> Mapping[str, Any]:
    """Summarize P7E risk metadata across a set of question results.

    Returns a mapping with:
    - ``answer_source_distribution``: ``{source: count}``;
    - ``high_risk_qids``: qids with ``metadata.high_risk=True``;
    - ``truncation_risk_qids``: qids with ``finish_reason=length`` OR
      ``metadata.truncation_risk=True``;
    - ``fallback_qids``: qids with ``fallback_used=True``;
    - ``calculation_incomplete_qids``: calc-routed qids with
      ``metadata.computation_complete=False``;
    - ``unsupported_guess_qids``: qids with ``answer_source`` in
      ``unsupported_guess`` / ``unsupported_guess_truncated``;
    - ``total_tokens``: sum of per-question tokens.
    """
    from collections import Counter

    answer_source_dist: Counter[str] = Counter()
    high_risk_qids: List[str] = []
    truncation_qids: List[str] = []
    fallback_qids: List[str] = []
    calc_incomplete_qids: List[str] = []
    unsupported_guess_qids: List[str] = []
    total_tokens = 0

    for r in results:
        meta = r.metadata if isinstance(r.metadata, Mapping) else {}
        src = str(meta.get("answer_source", "") or "")
        answer_source_dist[src if src else "<missing>"] += 1
        total_tokens += int(r.total_tokens or 0)

        if meta.get("high_risk") is True:
            high_risk_qids.append(r.qid)
        if r.finish_reason == "length" or meta.get("truncation_risk") is True:
            truncation_qids.append(r.qid)
        if r.fallback_used:
            fallback_qids.append(r.qid)
        is_calc = r.solver == "calculation" or "calculation" in r.labels
        if is_calc and meta.get("computation_complete") is False:
            calc_incomplete_qids.append(r.qid)
        if src in ("unsupported_guess", "unsupported_guess_truncated"):
            unsupported_guess_qids.append(r.qid)

    return {
        "answer_source_distribution": dict(answer_source_dist),
        "high_risk_qids": sorted(set(high_risk_qids)),
        "truncation_risk_qids": sorted(set(truncation_qids)),
        "fallback_qids": sorted(set(fallback_qids)),
        "calculation_incomplete_qids": sorted(set(calc_incomplete_qids)),
        "unsupported_guess_qids": sorted(set(unsupported_guess_qids)),
        "total_tokens": total_tokens,
        "question_count": len(results),
    }


def check_token_consistency(
    results: Sequence[QuestionResult],
    submission_answers: Mapping[str, str],
) -> Mapping[str, Any]:
    """Compare debug artifact tokens/answers against a submission CSV.

    Returns a mapping with:
    - ``debug_total_tokens``: sum of per-question ``total_tokens``;
    - ``submission_qid_count``: number of qids in the submission;
    - ``answer_mismatches``: qids where debug answer != submission answer;
    - ``qids_only_in_debug`` / ``qids_only_in_submission``.
    """
    debug_total = sum(int(r.total_tokens or 0) for r in results)
    debug_answers = {r.qid: r.answer for r in results}
    debug_qids = set(debug_answers)
    sub_qids = set(submission_answers)
    mismatches = sorted(
        q for q in (debug_qids & sub_qids)
        if debug_answers[q] != submission_answers[q]
    )
    return {
        "debug_total_tokens": debug_total,
        "submission_qid_count": len(sub_qids),
        "answer_mismatches": mismatches,
        "qids_only_in_debug": sorted(debug_qids - sub_qids),
        "qids_only_in_submission": sorted(sub_qids - debug_qids),
    }
