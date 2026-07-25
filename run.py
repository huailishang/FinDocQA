"""Enhanced baseline entrypoint.

The current entrypoint wires the full modular workflow and can run either real
questions or a built-in self-test sample. It validates architecture and data
flow before LLM/retrieval details are filled in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from agent.workflow import BlockingAnswerValidationError
from contracts import (
    ClassificationResult, PipelineResult, Question, QuestionLabel,
    SolverResult, VerificationResult,
)
from utils.config import load_config

from verification.production_integrity import failed_result_from_blocking, validate_results_before_write
from verification.dual_lineage import accepted_final_state
from runtime_safety import set_attempt_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the enhanced baseline workflow.")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config YAML.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions.")
    parser.add_argument("--domain", default=None, help="Only run questions from one domain, e.g. insurance.")
    parser.add_argument(
        "--qid",
        action="append",
        default=[],
        help="Run only the specified qid; repeat the option for a focused batch.",
    )
    parser.add_argument("--save-every", type=int, default=5, help="Persist cumulative results every N questions (default: 5).")
    parser.add_argument("--output-dir", default=None, help="Override paths.output_dir for this run.")
    parser.add_argument(
        "--artifact-mode",
        choices=("standard", "evaluation-only"),
        default="standard",
        help="Explicit output contract. evaluation-only writes debug/checkpoints but never submission.csv.",
    )
    parser.add_argument("--restart", action="store_true", help="Ignore any checkpoint and rerun selected questions from the beginning.")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Run pipeline without writing submission/debug output.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run one built-in sample question without requiring downloaded data.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Preserve blocked qids in diagnostics and continue with remaining questions.",
    )
    parser.add_argument(
        "--skip-recorded-on-resume",
        action="store_true",
        help="Treat every qid already present in the checkpoint, including failed qids, as processed.",
    )
    return parser.parse_args()


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def summarize(results: Sequence[PipelineResult]) -> str:
    fallback_count = sum(1 for item in results if item.fallback_used)
    generated_valid = sum(1 for item in results if item.metadata.get("answer_validation") == "generated_valid")
    blocking_invalid = sum(1 for item in results if item.metadata.get("answer_validation") == "blocking_invalid")
    solver_counts: dict[str, int] = {}
    for item in results:
        solver_counts[item.solver_result.solver] = solver_counts.get(item.solver_result.solver, 0) + 1
    solver_text = ", ".join(f"{name}={count}" for name, count in sorted(solver_counts.items()))
    return f"processed={len(results)}, generated_valid={generated_valid}, fallback_used={fallback_count}, blocking_invalid={blocking_invalid}, solvers=[{solver_text}]"


def sample_questions() -> list[Question]:
    return [
        Question(
            qid="self_test_001",
            domain="insurance",
            text="某保险产品等待期内因意外伤害出险是否适用等待期？",
            options={"A": "适用", "B": "不适用", "C": "无法判断", "D": "以上都不对"},
            answer_format="mcq",
            doc_ids=["1"],
            raw={"source": "self_test"},
        )
    ]



def _pipeline_result_from_dict(item: dict) -> PipelineResult:
    classification_raw = item.get("classification") or {}
    labels = []
    for value in classification_raw.get("labels", []):
        try:
            labels.append(QuestionLabel(value))
        except ValueError:
            labels.append(QuestionLabel.DEFAULT)
    classification = ClassificationResult(
        labels=labels,
        reasons=classification_raw.get("reasons") or {},
    )

    solver_raw = item.get("solver_result") or {}
    solver_result = SolverResult(
        qid=str(solver_raw.get("qid") or item.get("qid") or ""),
        answer=str(solver_raw.get("answer") or item.get("answer") or ""),
        solver=str(solver_raw.get("solver") or "unknown"),
        raw_output=str(solver_raw.get("raw_output") or ""),
        confidence=solver_raw.get("confidence"),
        metadata=solver_raw.get("metadata") or {},
    )

    verification_result = None
    verification_raw = item.get("verification_result")
    if verification_raw:
        verification_result = VerificationResult(
            qid=str(verification_raw.get("qid") or item.get("qid") or ""),
            answer=str(verification_raw.get("answer") or item.get("answer") or ""),
            changed=bool(verification_raw.get("changed", False)),
            verifier=str(verification_raw.get("verifier") or "unknown"),
            notes=verification_raw.get("notes") or [],
            metadata=verification_raw.get("metadata") or {},
        )

    return PipelineResult(
        qid=str(item.get("qid") or ""),
        answer=str(item.get("answer") or ""),
        classification=classification,
        solver_result=solver_result,
        verification_result=verification_result,
        prompt_tokens=int(item.get("prompt_tokens", 0) or 0),
        completion_tokens=int(item.get("completion_tokens", 0) or 0),
        total_tokens=int(item.get("total_tokens", 0) or 0),
        reasoning=str(item.get("reasoning") or ""),
        fallback_used=bool(item.get("fallback_used", False)),
        error=item.get("error"),
        metadata=item.get("metadata") or {},
        submission_answers=tuple(str(value) for value in item.get("submission_answers", []) or []),
    )



def load_checkpoint(path: Path) -> list[PipelineResult]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("checkpoint root must be a list")
        results = [_pipeline_result_from_dict(item) for item in raw]
        return [result for result in results if result.qid]
    except Exception as exc:
        raise SystemExit(f"Invalid checkpoint {path}: {exc}. Use --restart to ignore it.") from exc


def save_checkpoint(path: Path, results: Sequence[PipelineResult]) -> None:
    validate_results_before_write(results, allow_failed=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)

def persist_runtime_checkpoint(
    writer,
    checkpoint_path: Path,
    results: Sequence[PipelineResult],
) -> None:
    """Persist resumable/debug state without emitting a partial B submission."""
    save_checkpoint(checkpoint_path, results)
    writer.write_checkpoint(results)
    # Preserve historical A-board behavior: legacy mode may emit a cumulative
    # submission at each checkpoint. B-board finalization is handled explicitly
    # only after a complete official selection finishes.
    if writer.submission_mode == "a_board_legacy" and writer.submission_enabled:
        writer.write_final(results)


def token_budget_from_env() -> int:
    raw = os.getenv("LLM_TOKEN_BUDGET", "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError as exc:
        raise SystemExit(f"Invalid LLM_TOKEN_BUDGET={raw!r}; expected integer.") from exc

def main() -> None:
    args = parse_args()
    load_local_env(ROOT / ".env")
    config_path = (ROOT / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    config = load_config(config_path)
    if args.output_dir:
        config.setdefault("paths", {})["output_dir"] = args.output_dir

    factory = PipelineFactory(
        config=config, project_root=ROOT, artifact_mode=args.artifact_mode
    )
    writer = None if args.no_write else factory.build_writer()
    workflow = factory.build_workflow(writer=writer)

    if args.self_test:
        questions = sample_questions()
    else:
        loader = factory.build_loader()
        questions = list(loader.load())
        if args.domain:
            questions = [question for question in questions if question.domain == args.domain]
        if args.qid:
            selected_qids_arg = set(args.qid)
            questions = [question for question in questions if question.qid in selected_qids_arg]
        if args.limit is not None:
            questions = questions[: args.limit]

    if not questions:
        raise SystemExit(
            "No questions loaded. Prepare data first, e.g. run the baseline prepare_data step "
            "so ../data/raw_dataset/questions/group_a exists, or use --self-test."
        )

    checkpoint_path = (writer.output_dir if writer is not None else ROOT / "output") / "run_checkpoint.json"
    results: list[PipelineResult] = [] if args.restart or args.no_write else load_checkpoint(checkpoint_path)
    selected_qids = {question.qid for question in questions}
    results = [result for result in results if result.qid in selected_qids]
    if args.skip_recorded_on_resume:
        completed_qids = {result.qid for result in results}
    else:
        completed_qids = {
            result.qid for result in results
            if accepted_final_state(result.metadata.get("final_state") or "accepted") and not result.error
        }
    pending_questions = [question for question in questions if question.qid not in completed_qids]
    token_budget = token_budget_from_env()
    used_tokens = sum(result.total_tokens for result in results)

    if completed_qids:
        print(f"[resume] loaded {len(completed_qids)} completed questions; {len(pending_questions)} remaining")
    elif args.restart:
        print("[restart] checkpoint ignored; starting selected questions from the beginning")

    save_every = max(1, args.save_every)
    completed_this_run = 0
    for question in pending_questions:
        if token_budget and used_tokens >= token_budget:
            if writer is not None and results:
                persist_runtime_checkpoint(writer, checkpoint_path, results)
            raise SystemExit(
                f"LLM token budget reached before {question.qid}: "
                f"used={used_tokens}, budget={token_budget}. Stopping without fallback."
            )
        absolute_index = len(results) + 1
        try:
            set_attempt_context(question.qid)
            result = workflow.process_one(question)
            results.append(result)
            used_tokens += result.total_tokens
            completed_this_run += 1
            print(f"[{absolute_index}/{len(questions)}] {result.qid} -> {result.answer} tokens={result.total_tokens}")
        except BlockingAnswerValidationError as exc:
            results = [item for item in results if item.qid != question.qid]
            failed = failed_result_from_blocking(question, exc)
            results.append(failed)
            if writer is not None:
                persist_runtime_checkpoint(writer, checkpoint_path, results)
                print(f"[failure] preserved blocked qid={question.qid} reason={exc.reason}")
            if args.continue_on_failure:
                continue
            raise
        except Exception:
            if writer is not None and results:
                persist_runtime_checkpoint(writer, checkpoint_path, results)
                print(f"[checkpoint] saved {len(results)} records before failure")
            raise
        if writer is not None and (completed_this_run % save_every == 0 or len(results) == len(questions)):
            persist_runtime_checkpoint(writer, checkpoint_path, results)
            print(f"[checkpoint] saved cumulative {len(results)}/{len(questions)} questions")

    if writer is not None and not pending_questions and results:
        persist_runtime_checkpoint(writer, checkpoint_path, results)
        print(f"[resume] all {len(results)} selected questions were already completed")

    if writer is not None and results and writer.submission_enabled:
        selected_qid_order = [question.qid for question in questions]
        if writer.selection_matches_final_contract(selected_qid_order):
            # The checkpoint/debug artifacts already exist at this point. Final
            # B validation may fail closed without destroying resumable state.
            writer.write_final(results)
            print(f"[final] submission emitted for {len(results)} official questions")
        elif writer.submission_mode == "b_board":
            print(
                f"[partial] saved {len(results)} selected questions; "
                "submission.csv not emitted"
            )

    print("")
    print("=== Per-Question Debug Summary ===")
    for result in results:
        m = result.metadata
        labels = ",".join(m.get("classifier_labels", [])) or "?"
        solver = m.get("solver", result.solver_result.solver)
        evidence_count = m.get("evidence_count", 0)
        missing = m.get("missing_doc_ids", [])
        fallbacks = m.get("retrieval_fallbacks", [])
        tokens = result.total_tokens or m.get("estimated_tokens", 0)
        fallback_flag = " DEGRADED" if m.get("degraded") else ""
        missing_str = f" missing_doc_ids={missing}" if missing else ""
        fb_str = f" retrieval_fb={fallbacks}" if fallbacks else ""
        dry_run = " DRY_RUN" if result.solver_result.metadata.get("dry_run") else ""
        err = f" ERROR={result.error}" if result.error else ""
        # Answer and verifier warnings (Stage 3/4 observability)
        answer = result.answer
        vresult = result.verification_result
        vmeta = vresult.metadata if vresult else {}
        v_warnings = vmeta.get("warnings", []) if vmeta else []
        v_changed = vmeta.get("placeholder") is False and vresult and vresult.changed
        warn_str = f" vwarn={v_warnings}" if v_warnings else ""
        changed_str = " VCHANGED" if v_changed else ""
        print(
            f"  [{result.qid}]"
            f" domain={m.get('domain', '?')}"
            f" labels=[{labels}]"
            f" solver={solver}"
            f" answer={answer}"
            f" evidence={evidence_count}"
            f" tokens={tokens}"
            f"{missing_str}{fb_str}{fallback_flag}{dry_run}{warn_str}{changed_str}{err}"
        )
    print("")
    print(summarize(results))


if __name__ == "__main__":
    main()
