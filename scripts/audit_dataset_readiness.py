"""Zero-API multi-slot input, submission-contract and DocumentScope readiness audit."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from answer_contract import contract_to_dict
from agent.factory import PipelineFactory
from contracts import ClassificationResult, PipelineResult, SolverResult
from evaluation.writer import SUBMISSION_HEADER, SubmissionTemplate, CsvSubmissionWriter
from utils.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit real multi-slot readiness without provider calls.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output-dir", default="evaluation_artifacts/bb_p0_02")
    return parser.parse_args()


def _distribution(values: Iterable[Any]) -> dict[str, int]:
    return {
        str(key): int(count)
        for key, count in sorted(Counter(values).items(), key=lambda item: str(item[0]))
    }


def _numeric_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 6),
        "median": round(statistics.median(values), 6),
        "mean": round(statistics.fmean(values), 6),
        "max": round(max(values), 6),
    }


def _synthetic_result(question, slot_count: int) -> PipelineResult:
    if question.answer_format == "freeform":
        answers = tuple("0" for _ in range(slot_count))
    else:
        answers = ("A",)
    answer = answers[0]
    return PipelineResult(
        qid=question.qid,
        answer=answer,
        classification=ClassificationResult(labels=()),
        solver_result=SolverResult(
            qid=question.qid,
            answer=answer,
            solver="bb_p0_02_schema_fixture",
            metadata={"answer_source": "generated"},
        ),
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        metadata={
            "answer_format": question.answer_format,
            "answer_contract": contract_to_dict(question.answer_contract),
            "final_state": "accepted",
            "answer_source": "generated",
        },
        submission_answers=answers,
    )


def _validate_b_writer_schema(
    questions_by_qid: Mapping[str, Any],
    template: SubmissionTemplate,
    template_path: Path,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    missing_questions = [qid for qid in template.qid_order if qid not in questions_by_qid]
    extra_questions = sorted(set(questions_by_qid) - set(template.qid_order))
    if missing_questions:
        errors.append(f"template qids missing from loaded questions: {missing_questions}")
    if extra_questions:
        errors.append(f"loaded qids missing from template: {extra_questions}")
    if errors:
        return False, errors

    results = [
        _synthetic_result(
            questions_by_qid[qid],
            int(template.slot_count_by_qid[qid]),
        )
        for qid in template.qid_order
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="bb_p0_02_schema_") as temp_dir:
            output_dir = Path(temp_dir)
            CsvSubmissionWriter(
                output_dir,
                submission_mode="multi_slot",
                submission_template_path=template_path,
            ).write(results)
            with (output_dir / "submission.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.reader(handle))
            if tuple(rows[0]) != SUBMISSION_HEADER:
                errors.append(f"writer header mismatch: {rows[0]}")
            if len(rows) != len(template.qid_order) + 2:
                errors.append(
                    f"writer row count mismatch: expected {len(template.qid_order) + 2}, got {len(rows)}"
                )
            if [row[0] for row in rows[2:]] != list(template.qid_order):
                errors.append("writer qid order differs from official template")
            for row_number, row in enumerate(rows[1:], start=2):
                try:
                    prompt = int(row[5] or 0)
                    completion = int(row[6] or 0)
                    total = int(row[7] or 0)
                except (IndexError, ValueError):
                    errors.append(f"invalid token columns at generated row {row_number}")
                    continue
                if prompt + completion != total:
                    errors.append(f"token equation failed at generated row {row_number}")
    except Exception as exc:  # pragma: no cover - audit should retain details
        errors.append(f"writer validation exception: {type(exc).__name__}: {exc}")
    return not errors, errors


def audit(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = load_config(config_path)
    factory = PipelineFactory(config=config, project_root=ROOT)
    loader = factory.build_loader()
    questions = list(loader.load())
    questions_by_qid = {question.qid: question for question in questions}
    classifier = factory.build_classifier()
    resolver = factory.build_document_scope_resolver()
    if resolver is None:
        raise SystemExit("document_scope.enabled must be true")

    template_value = str(config.get("paths", {}).get("submission_template") or "").strip()
    if not template_value:
        raise SystemExit("paths.submission_template is required for multi-slot audit")
    template_path = Path(template_value)
    if not template_path.is_absolute():
        template_path = (ROOT / template_path).resolve()
    template = SubmissionTemplate.load(template_path)

    contract_error_qids = [
        question.qid
        for question in questions
        if question.answer_format == "unknown" or question.answer_contract is None
    ]
    scope_records: list[dict[str, Any]] = []
    no_candidate_qids: list[str] = []
    weak_scope_qids: list[str] = []
    provider_calls = 0
    candidate_counts: list[int] = []
    effective_top_ks: list[int] = []
    adaptive_flags: list[bool] = []
    confidence_values: list[str] = []
    per_domain_top1: dict[str, list[float]] = defaultdict(list)
    per_domain_margin: dict[str, list[float]] = defaultdict(list)

    for question in questions:
        result = resolver.resolve(question, classifier.classify(question))
        provider_calls += int(result.provider_calls)
        candidate_counts.append(len(result.candidate_doc_ids))
        effective_top_ks.append(int(result.effective_top_k))
        adaptive_flags.append(bool(result.adaptive_scope))
        confidence_values.append(result.confidence)
        if not result.candidate_doc_ids:
            no_candidate_qids.append(question.qid)
        if "weak_scope" in result.warnings:
            weak_scope_qids.append(question.qid)
        scores = [float(candidate.score) for candidate in result.candidates]
        if scores:
            per_domain_top1[question.domain].append(scores[0])
            per_domain_margin[question.domain].append(
                scores[0] - scores[1] if len(scores) >= 2 else scores[0]
            )
        scope_records.append(
            {
                "qid": question.qid,
                "domain": question.domain,
                "answer_format": question.answer_format,
                "source_file": question.raw.get("_source_file"),
                "source_line": question.raw.get("_source_line"),
                "required_doc_ids": list(question.doc_ids),
                "scope": result.to_dict(),
            }
        )

    submission_schema_pass, submission_errors = _validate_b_writer_schema(
        questions_by_qid,
        template,
        template_path,
    )
    per_domain_scope_stats = {
        domain: {
            "top1_score": _numeric_summary(per_domain_top1.get(domain, [])),
            "top1_margin": _numeric_summary(per_domain_margin.get(domain, [])),
        }
        for domain in sorted({question.domain for question in questions})
    }
    summary = {
        "package": "BB-P0-02",
        "audit": "real_multi_slot_zero_api_readiness",
        "questions_dir": str(loader.questions_dir),
        "submission_template": str(template_path),
        "loaded_question_count": len(questions),
        "unique_qid_count": len(questions_by_qid),
        "per_domain_count": _distribution(question.domain for question in questions),
        "per_type_count": _distribution(question.answer_format for question in questions),
        "raw_type_count": _distribution(
            str(question.raw.get("type") or question.raw.get("_raw_type") or "")
            for question in questions
        ),
        "source_extension_count": _distribution(
            Path(str(question.raw.get("_source_file") or "")).suffix.lower()
            for question in questions
        ),
        "unknown_answer_contract_count": len(contract_error_qids),
        "contract_error_qids": contract_error_qids,
        "submission_header": list(template.header),
        "submission_slot_distribution": {
            str(key): value for key, value in sorted(template.slot_distribution.items())
        },
        "submission_contract_error_qids": [
            qid
            for qid in template.qid_order
            if qid not in questions_by_qid
        ],
        "b_submission_schema_pass": submission_schema_pass,
        "b_submission_schema_errors": submission_errors,
        "document_scope_strategy": resolver.strategy,
        "provider_calls": provider_calls,
        "no_candidate_count": len(no_candidate_qids),
        "no_candidate_qids": no_candidate_qids,
        "weak_scope_count": len(weak_scope_qids),
        "weak_scope_qids": weak_scope_qids,
        "candidate_count_distribution": _distribution(candidate_counts),
        "effective_top_k_distribution": _distribution(effective_top_ks),
        "adaptive_scope_distribution": _distribution(adaptive_flags),
        "confidence_distribution": _distribution(confidence_values),
        "per_domain_scope_score_stats": per_domain_scope_stats,
        "candidate_scope_is_required_truth": False,
        "document_truth_metrics_reported": False,
        "paid_provider_calls": 0,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "multi_slot_readiness_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "multi_slot_scope_candidates.json").write_text(
        json.dumps(
            {
                "package": "BB-P0-02",
                "provider_calls": provider_calls,
                "candidate_scope_is_required_truth": False,
                "questions": scope_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    args = parse_args()
    config_path = (
        (ROOT / args.config).resolve()
        if not Path(args.config).is_absolute()
        else Path(args.config)
    )
    output_dir = (
        (ROOT / args.output_dir).resolve()
        if not Path(args.output_dir).is_absolute()
        else Path(args.output_dir)
    )
    report = audit(config_path, output_dir)
    print(
        json.dumps(
            {
                "loaded_question_count": report["loaded_question_count"],
                "per_domain_count": report["per_domain_count"],
                "per_type_count": report["per_type_count"],
                "unknown_answer_contract_count": report["unknown_answer_contract_count"],
                "submission_slot_distribution": report["submission_slot_distribution"],
                "b_submission_schema_pass": report["b_submission_schema_pass"],
                "no_candidate_count": report["no_candidate_count"],
                "weak_scope_count": report["weak_scope_count"],
                "candidate_count_distribution": report["candidate_count_distribution"],
                "effective_top_k_distribution": report["effective_top_k_distribution"],
                "adaptive_scope_distribution": report["adaptive_scope_distribution"],
                "provider_calls": report["provider_calls"],
                "report_path": str(output_dir / "multi_slot_readiness_audit.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
