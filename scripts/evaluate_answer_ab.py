"""Compare final-answer quality for multiple retrieval strategies.

Default mode is a zero-provider dry run that validates Gold isolation, visible
question/output contracts, strategy names and resume state. Real model execution
requires both ``--execute`` and ``--allow-provider-calls`` plus an explicit total
provider-call budget.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from data.loader import JsonQuestionLoader
from evaluation.answer_ab import (
    AnswerABStrategy,
    load_answer_ab_checkpoint,
    load_answer_gold_cases,
    run_answer_ab,
)
from evaluation.writer import SubmissionTemplate
from submission_contract import load_answer_slot_contracts
from utils.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E4 final-answer A/B. Dry-run by default; provider calls require explicit opt-in."
    )
    parser.add_argument("--gold", required=True, help="Private answer Gold JSON path.")
    parser.add_argument("--questions-dir", required=True, help="Visible question JSON/JSONL directory.")
    parser.add_argument(
        "--submission-template",
        required=True,
        help="Visible output template used only to recover answer slot counts.",
    )
    parser.add_argument(
        "--answer-slot-contracts",
        required=True,
        help="Visible per-slot output contracts for the question set.",
    )
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=("lexical_hybrid", "canonical_lexical"),
        help="pipeline.retriever modes to compare; all other config stays identical.",
    )
    parser.add_argument(
        "--checkpoint",
        default="evaluation_artifacts/answer_ab_checkpoint.jsonl",
        help="Append-only per-strategy/per-case checkpoint.",
    )
    parser.add_argument(
        "--output",
        default="evaluation_artifacts/answer_ab.json",
        help="Private JSON report path.",
    )
    parser.add_argument(
        "--provider-ledger",
        default="evaluation_artifacts/answer_ab_provider_ledger.jsonl",
        help="Sanitized provider token/call ledger used only in execute mode.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually run the workflows.")
    parser.add_argument(
        "--allow-provider-calls",
        action="store_true",
        help="Second explicit guard required together with --execute.",
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        default=0,
        help="Required positive total provider-call cap in execute mode.",
    )
    parser.add_argument(
        "--max-provider-calls-per-case",
        type=int,
        default=1,
        help="Maximum provider attempts for one case in this process; use >1 only for same-model retry routes.",
    )
    parser.add_argument(
        "--fixed-model",
        default="",
        help="Force all configured ModelScope/SiliconFlow retry routes to the same model for a fair A/B.",
    )
    return parser.parse_args()


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def _load_visible_questions(args: argparse.Namespace):
    template = SubmissionTemplate.load(_resolve(args.submission_template))
    slot_contracts = load_answer_slot_contracts(_resolve(args.answer_slot_contracts))
    if set(template.slot_count_by_qid) != set(slot_contracts):
        missing = sorted(set(template.slot_count_by_qid) - set(slot_contracts))
        extra = sorted(set(slot_contracts) - set(template.slot_count_by_qid))
        raise SystemExit(
            "slot contract/template qid mismatch: "
            f"missing={missing[:10]} extra={extra[:10]}"
        )
    loader = JsonQuestionLoader(
        _resolve(args.questions_dir),
        submission_slot_count_by_qid=template.slot_count_by_qid,
        submission_slot_contracts_by_qid=slot_contracts,
        require_submission_slot_contract=True,
    )
    return tuple(loader.load())


def _strategy_config(base_config: dict, args: argparse.Namespace, mode: str) -> dict:
    config = copy.deepcopy(base_config)
    config.setdefault("pipeline", {})["retriever"] = mode
    config.setdefault("submission", {})["mode"] = "multi_slot"
    paths = config.setdefault("paths", {})
    paths["questions_dir"] = str(_resolve(args.questions_dir))
    paths["submission_template"] = str(_resolve(args.submission_template))
    paths["answer_slot_contracts"] = str(_resolve(args.answer_slot_contracts))
    return config


def _summary_view(payload: dict[str, object]) -> dict[str, object]:
    summaries = []
    for item in payload.get("strategies", []):
        summaries.append(
            {
                "strategy": item.get("strategy"),
                "cases": item.get("case_count"),
                "errors": item.get("errors"),
                "blocked_cases": item.get("blocked_cases"),
                "correct_but_blocked_cases": item.get("correct_but_blocked_cases"),
                "false_reject_rate_on_correct": item.get("false_reject_rate_on_correct"),
                "incorrect_but_accepted_cases": item.get("incorrect_but_accepted_cases"),
                "false_accept_rate_on_incorrect": item.get("false_accept_rate_on_incorrect"),
                "case_exact_match": item.get("case_exact_match"),
                "case_value_accuracy": item.get("case_value_accuracy"),
                "slot_value_accuracy": item.get("slot_value_accuracy"),
                "total_tokens": item.get("total_tokens"),
                "provider_call_count": item.get("provider_call_count"),
                "mean_latency_ms": item.get("mean_latency_ms"),
            }
        )
    return {
        "answer_quality_status": payload.get("answer_quality_status"),
        "strategies": summaries,
    }


def _configure_fixed_model(args: argparse.Namespace) -> None:
    model = str(args.fixed_model or "").strip()
    if not model:
        return
    os.environ["MODELSCOPE_MODEL_1"] = model
    os.environ["MODELSCOPE_MODEL_2"] = model
    os.environ["MODELSCOPE_MODEL_3"] = model
    os.environ["SILICONFLOW_MODEL"] = model


def _configure_provider_safety(args: argparse.Namespace, *, qids: tuple[str, ...]) -> None:
    if not args.execute:
        return
    if not args.allow_provider_calls:
        raise SystemExit("execute mode requires --allow-provider-calls")
    if args.max_provider_calls <= 0:
        raise SystemExit("execute mode requires a positive --max-provider-calls")
    if args.max_provider_calls_per_case <= 0:
        raise SystemExit("execute mode requires a positive --max-provider-calls-per-case")

    ledger_path = _resolve(args.provider_ledger)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["SAFE_RUN_EXECUTION"] = "1"
    os.environ["LLM_TOKEN_LEDGER_PATH"] = str(ledger_path)
    os.environ["SAFE_RUN_MAX_PROVIDER_CALL_BUDGET"] = str(args.max_provider_calls)
    # This runner is usually invoked one strategy at a time. Per-case retries are
    # allowed only when the caller has deliberately fixed every retry route to the
    # same model; the total cap remains the final circuit breaker.
    per_qid_cap = int(args.max_provider_calls_per_case)
    os.environ["SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON"] = json.dumps(
        {qid: per_qid_cap for qid in qids}, ensure_ascii=False
    )
    os.environ.setdefault("SAFE_RUN_DECISION_PURPOSE", "e4_answer_ab")
    os.environ.setdefault("SAFE_RUN_ID", "answer_ab")


def main() -> int:
    args = parse_args()
    visible_questions = _load_visible_questions(args)
    cases = load_answer_gold_cases(_resolve(args.gold), questions=visible_questions)
    if not cases:
        raise SystemExit("no answer Gold cases loaded")

    checkpoint_path = _resolve(args.checkpoint)
    prior = load_answer_ab_checkpoint(checkpoint_path)
    requested_pairs = {(mode, case.case_id) for mode in args.strategies for case in cases}
    completed_pairs = {
        (item.strategy, item.case_id)
        for item in prior
        if (item.strategy, item.case_id) in requested_pairs
    }
    pending_pairs = requested_pairs - completed_pairs

    if not args.execute:
        print(
            json.dumps(
                {
                    "mode": "dry_run_zero_provider_calls",
                    "gold_cases": len(cases),
                    "visible_questions": len(visible_questions),
                    "strategies": list(args.strategies),
                    "requested_strategy_cases": len(requested_pairs),
                    "checkpointed_strategy_cases": len(completed_pairs),
                    "pending_strategy_cases": len(pending_pairs),
                    "provider_calls_made": 0,
                    "next_gate": "rerun with explicit provider authorization and execute flags",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    _configure_fixed_model(args)
    _configure_provider_safety(
        args,
        qids=tuple(case.case_id for case in cases),
    )
    base_config = load_config(_resolve(args.config))
    strategies = []
    for mode in args.strategies:
        factory = PipelineFactory(
            config=_strategy_config(base_config, args, mode),
            project_root=ROOT,
        )
        workflow = factory.build_workflow()
        strategies.append(AnswerABStrategy(name=mode, runner=workflow.process_one))

    report = run_answer_ab(
        cases,
        strategies=tuple(strategies),
        checkpoint_path=checkpoint_path,
        prior_measurements=prior,
    )
    payload = report.to_dict()
    output_path = _resolve(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(_summary_view(payload), ensure_ascii=False, indent=2))
    print(f"report={output_path}")
    print(f"checkpoint={checkpoint_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
