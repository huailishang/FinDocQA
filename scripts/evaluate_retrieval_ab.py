"""Compare two configured retrieval paths through solver-visible evidence, with zero provider calls."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from evaluation.retrieval_ab import (
    RetrievalABStrategy,
    load_retrieval_gold_cases,
    run_retrieval_ab,
)
from utils.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zero-API retrieval A/B through the final solver-visible evidence boundary."
    )
    parser.add_argument("--gold", required=True, help="Retrieval Gold JSON path.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=("lexical_hybrid", "canonical_lexical"),
        help="pipeline.retriever modes to compare.",
    )
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument(
        "--output",
        default="evaluation_artifacts/retrieval_ab.json",
        help="JSON report path; evaluation_artifacts is recommended for private Gold.",
    )
    return parser.parse_args()


def _build_strategy(config: dict, mode: str) -> RetrievalABStrategy:
    strategy_config = copy.deepcopy(config)
    strategy_config.setdefault("pipeline", {})["retriever"] = mode
    factory = PipelineFactory(config=strategy_config, project_root=ROOT)
    return RetrievalABStrategy(
        name=mode,
        retriever=factory.build_retriever(),
        assembler=factory.build_assembler(),
    )


def _summary_view(payload: dict[str, object]) -> dict[str, object]:
    strategies = []
    for item in payload.get("strategies", []):
        raw = item.get("raw", {})
        solver = item.get("solver_visible", {})
        strategies.append(
            {
                "strategy": item.get("strategy"),
                "cases": item.get("case_count"),
                "errors": item.get("errors"),
                "raw_complete_doc_recall": raw.get("complete_document_recall_at_k"),
                "raw_page_group_recall": raw.get("acceptable_page_group_recall_at_k"),
                "raw_anchor_recall": raw.get("evidence_anchor_recall_at_k"),
                "solver_complete_doc_recall": solver.get("complete_document_recall_at_k"),
                "solver_page_group_recall": solver.get("acceptable_page_group_recall_at_k"),
                "solver_anchor_recall": solver.get("evidence_anchor_recall_at_k"),
                "mean_total_latency_ms": item.get("mean_total_latency_ms"),
                "mean_estimated_tokens": item.get("mean_estimated_tokens"),
            }
        )
    return {
        "answer_quality_status": payload.get("answer_quality_status"),
        "strategies": strategies,
    }


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    cases = load_retrieval_gold_cases(Path(args.gold))
    if not cases:
        raise SystemExit("no retrieval Gold cases loaded")

    base_factory = PipelineFactory(config=copy.deepcopy(config), project_root=ROOT)
    classifier = base_factory.build_classifier()
    strategies = tuple(_build_strategy(config, mode) for mode in args.strategies)
    report = run_retrieval_ab(cases, classifier=classifier, strategies=strategies, k=args.k)
    payload = report.to_dict()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(_summary_view(payload), ensure_ascii=False, indent=2))
    print(f"report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
