#!/usr/bin/env python3
"""FinanceBench E4 preflight and explicitly gated real-run entrypoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from evaluation.answer_ab import AnswerABStrategy, load_answer_ab_checkpoint, run_answer_ab
from evaluation.external_benchmarks.financebench_e4 import (
    FROZEN_CASE_IDS,
    build_financebench_e4_cases,
    build_financebench_preflight_config,
    financebench_e4_inventory,
    run_factory_retrieval_preflight,
    select_frozen_financebench_cases,
    validate_frozen_inventory,
)
from utils.config import load_config

DEFAULT_SOURCE = REPO / "evaluation_artifacts/external_benchmarks/financebench/github_selected/financebench_open_source.jsonl"
DEFAULT_PROCESSED = REPO / "evaluation_artifacts/external_benchmarks/financebench/canonical_evidence_smoke_v1/processed"
DEFAULT_OUTPUT = REPO / "evaluation_artifacts/external_benchmarks/financebench/e4_preflight_v1"
DEFAULT_CONFIG = REPO / "config/config.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FinanceBench frozen-slice E4 preflight")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--processed-docs", type=Path, default=DEFAULT_PROCESSED)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute", action="store_true", help="request a real provider-backed E4 run")
    parser.add_argument("--allow-provider-calls", action="store_true")
    parser.add_argument("--max-provider-calls", type=int, default=0)
    parser.add_argument("--checkpoint", type=Path, default=None)
    return parser


def validate_execute_gate(args: argparse.Namespace) -> None:
    """Fail closed before any workflow/provider construction."""
    if not args.execute:
        return
    if not args.allow_provider_calls:
        raise ValueError("--execute requires --allow-provider-calls")
    if int(args.max_provider_calls or 0) <= 0:
        raise ValueError("--execute requires --max-provider-calls > 0")


def _gold_page_map(source_cases) -> dict[str, tuple[int, ...]]:
    return {
        case.case_id: tuple(sorted({int(item.page_num) + 1 for item in case.gold_label.evidence}))
        for case in source_cases
    }


def _dry_run(args: argparse.Namespace) -> dict[str, object]:
    source_cases = select_frozen_financebench_cases(args.source)
    validate_frozen_inventory(source_cases)
    e4_cases = build_financebench_e4_cases(source_cases)
    base_config = load_config(args.config)
    preflight_config = build_financebench_preflight_config(
        base_config,
        processed_docs=args.processed_docs,
    )
    retrieval = run_factory_retrieval_preflight(
        e4_cases,
        config=preflight_config,
        project_root=REPO,
    )
    gold_pages = _gold_page_map(source_cases)
    all_gold_hits = 0
    annotation_hits = 0
    annotation_total = 0
    rows: list[dict[str, object]] = []
    for item in retrieval:
        expected = gold_pages[item.case_id]
        retrieved = set(item.retrieved_page_numbers)
        all_gold = all(page in retrieved for page in expected)
        hits = sum(page in retrieved for page in expected)
        all_gold_hits += int(all_gold)
        annotation_hits += hits
        annotation_total += len(expected)
        rows.append(
            {
                "case_id": item.case_id,
                "doc_name": item.doc_name,
                "retrieved_doc_ids": list(item.retrieved_doc_ids),
                "retrieved_page_numbers_top5": list(item.retrieved_page_numbers),
                "official_evidence_pages": list(expected),
                "all_gold_pages_hit_at_5": all_gold,
                "annotation_hits_at_5": hits,
                "request_source": item.request_source,
                "scope_provider_calls": item.scope_provider_calls,
            }
        )
    inventory = financebench_e4_inventory(source_cases)
    payload: dict[str, object] = {
        "mode": "dry_run_zero_provider_calls",
        "cases": len(e4_cases),
        "strategy": "canonical_lexical",
        "provider_calls_made": sum(item.scope_provider_calls for item in retrieval),
        "execute_authorized": False,
        "all_gold_at_5": {"hits": all_gold_hits, "total": len(e4_cases)},
        "annotation_recall_at_5": {"hits": annotation_hits, "total": annotation_total},
        "candidate_binding_preserved": sum(
            tuple(case.question.candidate_doc_ids) == (source.document.doc_name,)
            for case, source in zip(e4_cases, source_cases)
        ),
        "inventory": [item.__dict__ for item in inventory],
        "retrieval": rows,
        "future_metrics_contract": [
            "case_exact_match",
            "case_value_accuracy",
            "blocked_cases",
            "correct_but_blocked",
            "incorrect_but_accepted",
            "false_reject",
            "false_accept",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "provider_call_count",
            "latency_ms",
            "per_case_error",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "dry_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _configure_provider_safety(args: argparse.Namespace, *, qids: tuple[str, ...]) -> None:
    """Install the repository pre-call circuit breaker for a future authorized run."""
    if not args.execute:
        return
    ledger = args.output_dir / "provider_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    os.environ["SAFE_RUN_EXECUTION"] = "1"
    os.environ["LLM_TOKEN_LEDGER_PATH"] = str(ledger)
    os.environ["SAFE_RUN_MAX_PROVIDER_CALL_BUDGET"] = str(int(args.max_provider_calls))
    os.environ["SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON"] = json.dumps(
        {qid: int(args.max_provider_calls) for qid in qids}, ensure_ascii=False
    )
    os.environ.setdefault("SAFE_RUN_DECISION_PURPOSE", "financebench_e4")
    os.environ.setdefault("SAFE_RUN_ID", "financebench_e4")


def _execute(args: argparse.Namespace) -> dict[str, object]:
    """Real-run path; reachable only after the explicit provider gate."""
    source_cases = select_frozen_financebench_cases(args.source)
    validate_frozen_inventory(source_cases)
    e4_cases = build_financebench_e4_cases(source_cases)
    _configure_provider_safety(args, qids=tuple(case.case_id for case in e4_cases))
    base_config = load_config(args.config)
    config = build_financebench_preflight_config(base_config, processed_docs=args.processed_docs)
    factory = PipelineFactory(config=config, project_root=REPO)
    workflow = factory.build_workflow()
    checkpoint = args.checkpoint or (args.output_dir / "answer_ab_checkpoint.jsonl")
    prior = load_answer_ab_checkpoint(checkpoint)
    report = run_answer_ab(
        e4_cases,
        strategies=(AnswerABStrategy(name="canonical_lexical", runner=workflow.process_one),),
        checkpoint_path=checkpoint,
        prior_measurements=prior,
    )
    answer_ab_payload = report.to_dict()
    inventory = financebench_e4_inventory(source_cases)
    payload: dict[str, object] = {
        "mode": "real_e4",
        "strategy": "canonical_lexical",
        "external_cases": [item.__dict__ for item in inventory],
        "answer_ab": answer_ab_payload,
    }
    provider_calls = sum(
        int(strategy.get("provider_call_count", 0) or 0)
        for strategy in answer_ab_payload.get("strategies", [])
        if isinstance(strategy, dict)
    )
    payload["provider_calls_made"] = provider_calls
    if provider_calls > args.max_provider_calls:
        raise RuntimeError(
            f"provider call budget exceeded: calls={provider_calls} budget={args.max_provider_calls}"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "real_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        validate_execute_gate(args)
    except ValueError as exc:
        parser.error(str(exc))
    payload = _execute(args) if args.execute else _dry_run(args)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
