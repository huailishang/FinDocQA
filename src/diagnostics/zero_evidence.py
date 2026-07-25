"""Zero-evidence diagnostic instrumentation (R2).

A read-only diagnostic that traces one question through the retrieval
pipeline and records the evidence funnel at each stage, so a "zero evidence"
failure can be attributed to: missing parsed docs, empty index, retrieval
miss, post-filter over-aggressive pruning, or solver context too small.

This module does NOT change production behavior. It only reads pipeline state
and produces JSON + Markdown reports. It is enabled explicitly via CLI or
programmatic call; the default pipeline run never invokes it.

Funnel stages (per question):

    referenced_doc_count       — len(question.doc_ids)
    resolved_doc_count         — doc_ids that resolve to an existing parsed dir
    parsed_page_count          — total page_XXXX.md files found for resolved docs
    indexed_chunk_count        — chunks the retriever would index for those docs
    retrieved_candidate_count  — raw candidates the retriever returns
    post_filter_evidence_count — candidates surviving the assembler filter
    solver_context_chars       — len(prompt_context) fed to the solver

A question with ``post_filter_evidence_count == 0`` (or
``retrieved_candidate_count == 0``) is a "zero-evidence" question and the
funnel pinpoints the first stage where the count drops to zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    QuestionLabel,
)


@dataclass(frozen=True)
class QuestionDiagnostic:
    """Funnel counts for one question."""

    qid: str
    domain: str
    referenced_doc_count: int
    resolved_doc_count: int
    parsed_page_count: int
    indexed_chunk_count: int
    retrieved_candidate_count: int
    post_filter_evidence_count: int
    solver_context_chars: int
    zero_evidence_stage: str  # "" if not zero-evidence, else the first zero stage
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticReport:
    """Aggregate report over one or more questions."""

    question_count: int
    zero_evidence_count: int
    zero_evidence_qids: Tuple[str, ...]
    stage_totals: Mapping[str, int]  # stage -> total count across all questions
    questions: Tuple[QuestionDiagnostic, ...]
    json_text: str
    markdown_text: str


# Stage names in funnel order.
_STAGES = (
    "referenced_doc_count",
    "resolved_doc_count",
    "parsed_page_count",
    "indexed_chunk_count",
    "retrieved_candidate_count",
    "post_filter_evidence_count",
    "solver_context_chars",
)


def _count_parsed_pages(processed_root: Path, domain: str, doc_id: str) -> int:
    """Count page_XXXX.md files under processed_root/domain/doc_id/."""
    doc_dir = processed_root / domain / doc_id
    if not doc_dir.is_dir():
        return 0
    return len(list(doc_dir.glob("page_*.md")))


def _count_indexed_chunks(processed_root: Path, domain: str, doc_id: str) -> int:
    """Estimate indexed chunk count.

    The current LexicalHybridRetriever builds windows from page text. Without
    importing the retriever (which may require config/credentials), we estimate
    by counting non-empty text blocks in page files. This is a lower bound;
    the real retriever may produce more windows. The exact count is not
    critical — what matters is whether it is zero (no parseable text at all).
    """
    doc_dir = processed_root / domain / doc_id
    if not doc_dir.is_dir():
        return 0
    chunk_count = 0
    for page_file in sorted(doc_dir.glob("page_*.md")):
        text = page_file.read_text(encoding="utf-8", errors="ignore")
        # Rough estimate: one chunk per ~1800 chars, minimum 1 if non-empty.
        stripped = text.strip()
        if stripped:
            chunk_count += max(1, len(stripped) // 1800)
    return chunk_count


def diagnose_question(
    question: Question,
    *,
    processed_root: Path,
    evidence_bundle: Optional[EvidenceBundle] = None,
) -> QuestionDiagnostic:
    """Trace one question through the evidence funnel.

    Args:
        question: the question to diagnose.
        processed_root: root of parsed docs (``processed_root/<domain>/<doc_id>/page_*.md``).
        evidence_bundle: optional assembled bundle from a real pipeline run.
            When provided, ``retrieved_candidate_count`` and
            ``post_filter_evidence_count`` are read from it. When None, they
            are reported as 0 (the diagnostic cannot infer retrieval without
            running the pipeline).

    Returns:
        QuestionDiagnostic with all funnel counts filled.
    """
    processed_root = Path(processed_root)
    referenced = list(question.doc_ids)
    referenced_doc_count = len(referenced)

    resolved = 0
    parsed_pages = 0
    indexed = 0
    warnings: List[str] = []

    for doc_id in referenced:
        doc_dir = processed_root / question.domain / doc_id
        if doc_dir.is_dir():
            resolved += 1
            pages = _count_parsed_pages(processed_root, question.domain, doc_id)
            parsed_pages += pages
            if pages == 0:
                warnings.append(f"doc_id={doc_id}: directory exists but no page_*.md files")
            indexed += _count_indexed_chunks(processed_root, question.domain, doc_id)
        else:
            warnings.append(f"doc_id={doc_id}: not found under {processed_root}/{question.domain}/")

    if evidence_bundle is not None:
        retrieved = len(evidence_bundle.candidates)
        post_filter = evidence_bundle.metadata.get("evidence_count", len(evidence_bundle.candidates))
        context_chars = len(evidence_bundle.prompt_context)
    else:
        retrieved = 0
        post_filter = 0
        context_chars = 0
        warnings.append("no evidence_bundle provided; retrieved/post_filter/context set to 0")

    counts = {
        "referenced_doc_count": referenced_doc_count,
        "resolved_doc_count": resolved,
        "parsed_page_count": parsed_pages,
        "indexed_chunk_count": indexed,
        "retrieved_candidate_count": retrieved,
        "post_filter_evidence_count": post_filter,
        "solver_context_chars": context_chars,
    }

    zero_stage = ""
    for stage in _STAGES:
        if stage == "solver_context_chars":
            # solver_context_chars == 0 is only a zero-evidence signal when
            # post_filter is also 0; a non-zero evidence count with zero
            # context chars is a different (assembly) bug.
            if counts["post_filter_evidence_count"] > 0:
                continue
        if counts[stage] == 0:
            zero_stage = stage
            break

    return QuestionDiagnostic(
        qid=question.qid,
        domain=question.domain,
        referenced_doc_count=referenced_doc_count,
        resolved_doc_count=resolved,
        parsed_page_count=parsed_pages,
        indexed_chunk_count=indexed,
        retrieved_candidate_count=retrieved,
        post_filter_evidence_count=post_filter,
        solver_context_chars=context_chars,
        zero_evidence_stage=zero_stage,
        warnings=tuple(warnings),
    )


def diagnose_questions(
    questions: Sequence[Question],
    *,
    processed_root: Path,
    bundles: Optional[Mapping[str, EvidenceBundle]] = None,
) -> DiagnosticReport:
    """Diagnose a batch of questions and produce JSON + Markdown reports.

    Args:
        questions: questions to diagnose.
        processed_root: root of parsed docs.
        bundles: optional mapping qid -> EvidenceBundle from a real pipeline run.

    Returns:
        DiagnosticReport with aggregate funnel totals and per-question detail.
    """
    processed_root = Path(processed_root)
    bundles = bundles or {}
    diags: List[QuestionDiagnostic] = []
    for q in questions:
        bundle = bundles.get(q.qid)
        diags.append(diagnose_question(q, processed_root=processed_root, evidence_bundle=bundle))

    zero_qids = tuple(d.qid for d in diags if d.zero_evidence_stage)
    stage_totals: Dict[str, int] = {}
    for stage in _STAGES:
        stage_totals[stage] = sum(getattr(d, stage) for d in diags)

    json_data = {
        "question_count": len(diags),
        "zero_evidence_count": len(zero_qids),
        "zero_evidence_qids": list(zero_qids),
        "stage_totals": stage_totals,
        "questions": [asdict(d) for d in diags],
    }
    json_text = json.dumps(json_data, ensure_ascii=False, indent=2)

    md_lines: List[str] = [
        "# Zero-Evidence Diagnostic Report",
        "",
        f"- questions: {len(diags)}",
        f"- zero-evidence questions: {len(zero_qids)}",
        "",
        "## Stage totals",
        "",
        "| stage | total |",
        "| --- | ---: |",
    ]
    for stage in _STAGES:
        md_lines.append(f"| {stage} | {stage_totals[stage]} |")
    md_lines += ["", "## Per-question funnel", "",
                 "| qid | ref | resolved | pages | indexed | retrieved | post_filter | ctx_chars | zero_stage |",
                 "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for d in diags:
        md_lines.append(
            f"| {d.qid} | {d.referenced_doc_count} | {d.resolved_doc_count} | "
            f"{d.parsed_page_count} | {d.indexed_chunk_count} | {d.retrieved_candidate_count} | "
            f"{d.post_filter_evidence_count} | {d.solver_context_chars} | {d.zero_evidence_stage or '-'} |"
        )
    if zero_qids:
        md_lines += ["", "## Zero-evidence questions", ""]
        for qid in zero_qids:
            md_lines.append(f"- `{qid}`")
    markdown_text = "\n".join(md_lines) + "\n"

    return DiagnosticReport(
        question_count=len(diags),
        zero_evidence_count=len(zero_qids),
        zero_evidence_qids=zero_qids,
        stage_totals=stage_totals,
        questions=tuple(diags),
        json_text=json_text,
        markdown_text=markdown_text,
    )


# ── EvidenceBundle serialization ──────────────────────────────────────
#
# The diagnostic CLI must trace the REAL evidence funnel, not masquerade a
# missing bundle as zero-evidence. To support both sanctioned modes —
# (a) run the real retriever+assembler live, or (b) load real evidence
# artifacts dumped by a prior pipeline run — we need to (de)serialize an
# EvidenceBundle to/from JSON. These helpers are dependency-free and only
# touch the plain dataclasses defined in ``contracts``.


def bundle_to_dict(bundle: EvidenceBundle) -> Dict[str, Any]:
    """Serialize an EvidenceBundle to a plain dict for JSON persistence."""
    q = bundle.question
    return {
        "question": {
            "qid": q.qid,
            "domain": q.domain,
            "text": q.text,
            "options": dict(q.options),
            "answer_format": q.answer_format,
            "doc_ids": list(q.doc_ids),
        },
        "classification": {
            "labels": [label.value for label in bundle.classification.labels],
            "reasons": dict(bundle.classification.reasons),
        },
        "candidates": [
            {
                "domain": c.domain,
                "doc_id": c.doc_id,
                "source": c.source,
                "text": c.text,
                "before_text": c.before_text,
                "after_text": c.after_text,
                "section_title": c.section_title,
                "score": c.score,
                "retriever": c.retriever,
                "metadata": dict(c.metadata),
            }
            for c in bundle.candidates
        ],
        "prompt_context": bundle.prompt_context,
        "estimated_tokens": bundle.estimated_tokens,
        "metadata": dict(bundle.metadata),
    }


def bundle_from_dict(data: Mapping[str, Any]) -> EvidenceBundle:
    """Reconstruct an EvidenceBundle from ``bundle_to_dict`` output.

    Tolerant of missing keys so artifacts from slightly older runs still load.
    """
    q = data.get("question", {})
    question = Question(
        qid=str(q.get("qid", "")),
        domain=str(q.get("domain", "")),
        text=str(q.get("text", "")),
        options=dict(q.get("options", {})),
        answer_format=str(q.get("answer_format", "mcq")),
        doc_ids=list(q.get("doc_ids", [])),
    )

    cls = data.get("classification", {})
    labels: List[QuestionLabel] = []
    for raw_label in cls.get("labels", []):
        try:
            labels.append(QuestionLabel(str(raw_label)))
        except ValueError:
            continue
    classification = ClassificationResult(
        labels=tuple(labels),
        reasons=dict(cls.get("reasons", {})),
    )

    candidates: List[EvidenceCandidate] = []
    for c in data.get("candidates", []):
        if not isinstance(c, Mapping):
            continue
        try:
            score = float(c.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        candidates.append(
            EvidenceCandidate(
                domain=str(c.get("domain", question.domain)),
                doc_id=str(c.get("doc_id", "")),
                source=str(c.get("source", "")),
                text=str(c.get("text", "")),
                before_text=str(c.get("before_text", "")),
                after_text=str(c.get("after_text", "")),
                section_title=c.get("section_title"),
                score=score,
                retriever=str(c.get("retriever", "unknown")),
                metadata=dict(c.get("metadata", {})),
            )
        )

    return EvidenceBundle(
        question=question,
        classification=classification,
        candidates=tuple(candidates),
        prompt_context=str(data.get("prompt_context", "")),
        estimated_tokens=int(data.get("estimated_tokens", 0) or 0),
        metadata=dict(data.get("metadata", {})),
    )


def bundle_to_json(bundle: EvidenceBundle) -> str:
    """Serialize an EvidenceBundle to a JSON string."""
    return json.dumps(bundle_to_dict(bundle), ensure_ascii=False, indent=2)


def bundle_from_json(text: str) -> EvidenceBundle:
    """Parse a JSON string produced by ``bundle_to_json`` into a bundle."""
    return bundle_from_dict(json.loads(text))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for the zero-evidence diagnostic.

    Uses ``PipelineFactory.build_loader()`` (the project's canonical loader
    contract, same as ``run.py``) instead of importing a loader class
    directly. This keeps the CLI in sync with how the real pipeline loads
    questions and avoids symbol drift.
    """
    import argparse
    import sys

    # Defensive path setup so the module is runnable both as
    # `python scripts/zero_evidence_diagnostic.py` (scripts/ already sets
    # sys.path) and directly as `python -m diagnostics.zero_evidence`.
    src_dir = Path(__file__).resolve().parent.parent  # .../src
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    project_root = src_dir.parent  # project root (parent of src/)

    from utils.config import load_config
    from agent.factory import PipelineFactory

    parser = argparse.ArgumentParser(description="Zero-evidence diagnostic instrumentation.")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config YAML.")
    parser.add_argument("--processed-root", default=None, help="Override processed_docs root.")
    parser.add_argument("--qid", default=None, help="Diagnose only this qid (default: all).")
    parser.add_argument("--domain", default=None, help="Filter by domain.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions.")
    parser.add_argument("--output-json", default=None, help="Write JSON report to this path.")
    parser.add_argument("--output-md", default=None, help="Write Markdown report to this path.")
    parser.add_argument(
        "--evidence-artifacts",
        default=None,
        help="Directory of per-qid evidence bundle JSON files (<qid>.json) from a real "
             "pipeline run. When given, bundles are loaded from disk instead of running "
             "retrieval. A qid whose artifact is missing or unparseable falls back to live "
             "retrieval (with a warning), so a missing bundle is never reported as "
             "zero-evidence.",
    )
    parser.add_argument(
        "--dump-evidence",
        default=None,
        help="When running live retrieval, also write per-qid evidence bundle JSON to this "
             "directory, so it can be reused later via --evidence-artifacts.",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (project_root / args.config).resolve()
    config = load_config(config_path)
    # ``load_config`` returns a plain ``dict`` (not a namespace object), so we
    # must use dict access — the previous ``config.paths.processed_docs`` raised
    # ``AttributeError: 'dict' object has no attribute 'paths'`` whenever
    # ``--processed-root`` was omitted. Mirror PipelineFactory._resolve_path so
    # relative paths resolve against the project root exactly like run.py.
    if args.processed_root:
        processed_root = Path(args.processed_root)
    else:
        processed_rel = config.get("paths", {}).get(
            "processed_docs", "../data/processed_pymupdf4llm"
        )
        processed_root = Path(processed_rel)
        if not processed_root.is_absolute():
            processed_root = (project_root / processed_rel).resolve()

    # Reuse the project's canonical loader (same as run.py). This reads
    # raw_dataset/questions/group_a via the configured paths and returns
    # JsonQuestionLoader; if the data is absent the loader returns an empty
    # list rather than raising.
    factory = PipelineFactory(config=config, project_root=project_root)
    loader = factory.build_loader()
    questions = list(loader.load())
    if args.domain:
        questions = [q for q in questions if q.domain == args.domain]
    if args.qid:
        questions = [q for q in questions if q.qid == args.qid]
    if args.limit is not None:
        questions = questions[: args.limit]

    if not questions:
        print("No questions matched the filter (check --qid/--domain or data availability).")
        return 0

    # Build the OFFLINE retrieval components (classifier + retriever + assembler).
    # These need NO LLM / API, so the diagnostic traces the REAL evidence funnel
    # instead of masquerading a missing bundle as zero-evidence. Previously the
    # CLI called diagnose_questions() with no bundles, which forced
    # retrieved/post_filter/context to 0 for every question — a fake
    # "zero-evidence" signal that said nothing about real retrieval.
    classifier = factory.build_classifier()
    retriever = factory.build_retriever()
    if args.processed_root:
        # Keep the retriever pointed at the same corpus the parse/index stages
        # count, so the funnel is internally consistent when --processed-root
        # overrides the config value.
        retriever.processed_docs_dir = processed_root
    assembler = factory.build_assembler()

    artifacts_dir = Path(args.evidence_artifacts) if args.evidence_artifacts else None
    dump_dir = Path(args.dump_evidence) if args.dump_evidence else None
    if dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)

    bundles: Dict[str, Optional[EvidenceBundle]] = {}
    retrieval_warnings: List[str] = []
    for q in questions:
        bundle: Optional[EvidenceBundle] = None
        if artifacts_dir is not None:
            artifact_path = artifacts_dir / f"{q.qid}.json"
            if artifact_path.is_file():
                try:
                    bundle = bundle_from_json(artifact_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                    retrieval_warnings.append(
                        f"{q.qid}: evidence artifact unparseable ({exc}); falling back to live retrieval"
                    )
                    bundle = None
            else:
                retrieval_warnings.append(
                    f"{q.qid}: no evidence artifact at {artifact_path}; falling back to live retrieval"
                )
                bundle = None
        if bundle is None:
            # Live retrieval: real retriever + assembler (offline). Even when
            # retrieval returns 0 candidates this is a TRUE zero, not a
            # masquerade — the funnel reports what the real retriever did.
            classification = classifier.classify(q)
            candidates = retriever.retrieve(q, classification)
            bundle = assembler.assemble(q, classification, candidates)
            if dump_dir is not None:
                (dump_dir / f"{q.qid}.json").write_text(
                    bundle_to_json(bundle), encoding="utf-8"
                )
        bundles[q.qid] = bundle

    report = diagnose_questions(questions, processed_root=processed_root, bundles=bundles)

    # Surface retrieval-mode warnings (missing/unparseable artifacts) so the
    # operator knows which qids fell back to live retrieval.
    for warning in retrieval_warnings:
        print(f"warning: {warning}")

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(report.json_text, encoding="utf-8")
        print(f"JSON report written to {args.output_json}")
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(report.markdown_text, encoding="utf-8")
        print(f"Markdown report written to {args.output_md}")

    print(report.markdown_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
