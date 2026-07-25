"""Masked legacy benchmark for BB-P0-01 document-scope recall.

Ground-truth ``doc_ids`` are held only by this evaluation script.  Every resolver
call receives a masked Question with empty required/candidate scope and a raw
payload from which ``doc_ids``/answer-like fields are removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from contracts import Question
from utils.config import load_config


KS = (1, 3, 5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate masked legacy document-scope recall.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output-dir", default="evaluation_artifacts/bb_p0_01")
    parser.add_argument("--domain", default=None)
    return parser.parse_args()


def _masked_question(question: Question) -> Question:
    raw = {
        key: value
        for key, value in dict(question.raw or {}).items()
        if key not in {"doc_ids", "answer", "label", "gold", "ground_truth"}
    }
    return replace(question, doc_ids=(), candidate_doc_ids=(), raw=raw)


def _metric_template() -> dict[str, Any]:
    return {
        "questions": 0,
        "any_hit": {str(k): 0 for k in KS},
        "all_required": {str(k): 0 for k in KS},
        "reciprocal_rank_sum": 0.0,
    }


def _finalize_metric(metric: Mapping[str, Any]) -> dict[str, Any]:
    total = int(metric["questions"])
    denom = total or 1
    return {
        "questions": total,
        "AnyHitRecall": {
            f"@{k}": round(float(metric["any_hit"][str(k)]) / denom, 6)
            for k in KS
        },
        "AllRequiredRecall": {
            f"@{k}": round(float(metric["all_required"][str(k)]) / denom, 6)
            for k in KS
        },
        "MRR": round(float(metric["reciprocal_rank_sum"]) / denom, 6),
    }


def _update_metric(metric: dict[str, Any], truth: tuple[str, ...], ranked: tuple[str, ...]) -> None:
    metric["questions"] += 1
    truth_set = set(truth)
    for k in KS:
        top = set(ranked[:k])
        if truth_set & top:
            metric["any_hit"][str(k)] += 1
        if truth_set and truth_set <= top:
            metric["all_required"][str(k)] += 1
    ranks = [ranked.index(doc_id) + 1 for doc_id in truth if doc_id in ranked]
    if ranks:
        metric["reciprocal_rank_sum"] += 1.0 / min(ranks)


def _miss_reason(*, truth: tuple[str, ...], ranked: tuple[str, ...], catalog_doc_ids: set[str]) -> str:
    if not ranked:
        return "no_candidate_above_threshold"
    missing_catalog = [doc_id for doc_id in truth if doc_id not in catalog_doc_ids]
    if missing_catalog:
        return "ground_truth_doc_missing_from_catalog"
    if not set(truth) & set(ranked):
        return "ranking_or_query_signal_gap"
    return "partial_required_doc_recall"


def evaluate(config_path: Path, output_dir: Path, domain: str | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    # This benchmark is permanently bound to masked legacy truth. The active
    # production config may point questions_dir at multi-slot input, so explicitly
    # remove that profile override and use the legacy group_a path here.
    config.setdefault("paths", {}).pop("questions_dir", None)
    config.setdefault("paths", {})["question_group"] = "group_a"
    factory = PipelineFactory(config=config, project_root=ROOT)
    resolver = factory.build_document_scope_resolver()
    if resolver is None:
        raise SystemExit("document_scope.enabled must be true for BB-P0-01 benchmark")
    classifier = factory.build_classifier()
    questions = list(factory.build_loader().load())
    if domain:
        questions = [question for question in questions if question.domain == domain]

    overall = _metric_template()
    per_domain: dict[str, dict[str, Any]] = {}
    misses: list[dict[str, Any]] = []
    provider_calls = 0

    catalog_by_domain = {
        name: {entry.doc_id for entry in resolver.catalog.entries_for_domain(name)}
        for name in sorted({question.domain for question in questions})
    }

    for question in questions:
        truth = tuple(str(value) for value in question.doc_ids)
        masked = _masked_question(question)
        classification = classifier.classify(masked)
        result = resolver.resolve(masked, classification)
        provider_calls += int(result.provider_calls)
        ranked = tuple(result.candidate_doc_ids)

        _update_metric(overall, truth, ranked)
        domain_metric = per_domain.setdefault(question.domain, _metric_template())
        _update_metric(domain_metric, truth, ranked)

        if not set(truth) <= set(ranked[:5]):
            misses.append(
                {
                    "qid": question.qid,
                    "domain": question.domain,
                    "ground_truth_doc_ids": list(truth),
                    "top5_candidate_doc_ids": list(ranked[:5]),
                    "query_terms": list(result.query_terms),
                    "candidate_scores": [
                        {"doc_id": candidate.doc_id, "score": candidate.score}
                        for candidate in result.candidates[:5]
                    ],
                    "miss_reason": _miss_reason(
                        truth=truth,
                        ranked=ranked[:5],
                        catalog_doc_ids=catalog_by_domain.get(question.domain, set()),
                    ),
                }
            )

    report = {
        "package": "BB-P0-01",
        "benchmark": "masked_group_a_doc_id_recall",
        "leakage_guard": {
            "resolver_input_doc_ids": "masked_empty",
            "resolver_input_candidate_doc_ids": "masked_empty",
            "raw_doc_ids_removed": True,
            "qid_doc_id_mapping_used": False,
            "answer_or_leaderboard_truth_used": False,
        },
        "strategy": resolver.strategy,
        "provider_calls": provider_calls,
        "overall": _finalize_metric(overall),
        "per_domain": {
            name: _finalize_metric(metric)
            for name, metric in sorted(per_domain.items())
        },
        "top5_miss_count": len(misses),
        "misses": misses,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "document_catalog.json").write_text(
        json.dumps(resolver.catalog.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "masked_a_recall_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    config_path = (ROOT / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    output_dir = (ROOT / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    report = evaluate(config_path, output_dir, domain=args.domain)
    print(json.dumps({
        "provider_calls": report["provider_calls"],
        "overall": report["overall"],
        "per_domain": report["per_domain"],
        "top5_miss_count": report["top5_miss_count"],
        "report_path": str(output_dir / "masked_a_recall_report.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
