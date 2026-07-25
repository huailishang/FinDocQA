#!/usr/bin/env python3
"""Minimal file-driven agent loop for the enhanced-baseline workflow.

All report paths use output/ (gitignored) — handoffs/ no longer used.

Usage:
    python scripts/agent_loop.py init [--stage STAGE] [--round ROUND] [--force]
    python scripts/agent_loop.py status
    python scripts/agent_loop.py advance
    python scripts/agent_loop.py archive [--round ROUND]

See docs/agent-loop-orchestration.md for the full design.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
LOOP_DIR = OUTPUT / "agent_loop"
STATE_FILE = LOOP_DIR / "state.json"
CURRENT_TASK = LOOP_DIR / "current_task.md"

# Report paths — all under output/ (gitignored)
EXECUTOR_REPORT = OUTPUT / "executor_report.md"
EVALUATOR_REPORT = OUTPUT / "evaluator_report.md"
FINAL_REVIEW_PACKAGE = OUTPUT / "final_review_package.md"
FINAL_REVIEWER_REPORT = OUTPUT / "final_reviewer_report.md"

TZ = timezone(timedelta(hours=8))  # CST


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _default_state(stage: str = "stage_1_observability", round_num: int = 1) -> Dict[str, Any]:
    return {
        "round": round_num,
        "stage": stage,
        "status": "needs_executor",
        "next_actor": "executor",
        "last_decision": None,
        "blocking": False,
        "blocked_reason": None,
        "created_at": None,
        "updated_at": None,
    }


# ── helpers ──────────────────────────────────────────────────────────


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        print("No loop state found. Initialize with:", file=sys.stderr)
        print(f"  python scripts/agent_loop.py init --stage <stage> --round <round>", file=sys.stderr)
        sys.exit(1)
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_prompt(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"  -> written {path.relative_to(ROOT)}")


def _report_exists(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


_VERDICT_RE = re.compile(r"\b(PASS_WITH_NOTES|PASS|FAIL)\b")
_DECISION_RE = re.compile(r"\b(ACCEPT_WITH_NOTES|ACCEPT|REWORK|BLOCKED|STOP)\b")


def _parse_verdict(report_path: Path, pattern: re.Pattern) -> Optional[str]:
    if not _report_exists(report_path):
        return None
    text = report_path.read_text(encoding="utf-8", errors="replace")
    m = pattern.search(text)
    return m.group(1) if m else None


def _parse_evaluator_verdict(report_path: Path) -> Optional[str]:
    return _parse_verdict(report_path, _VERDICT_RE)


def _ensure_task_file() -> None:
    if not CURRENT_TASK.exists():
        CURRENT_TASK.parent.mkdir(parents=True, exist_ok=True)
        CURRENT_TASK.write_text("# Current Task\n\nDescribe the task for this round here.\n", encoding="utf-8")


# ── commands ─────────────────────────────────────────────────────────


def cmd_init(stage: str, round_num: int, force: bool = False) -> None:
    if STATE_FILE.exists() and not force:
        print(
            f"State file already exists at {STATE_FILE.relative_to(ROOT)}.\n"
            f"Use --force to overwrite."
        )
        return

    state = _default_state(stage=stage, round_num=round_num)
    state["created_at"] = now()
    state["updated_at"] = now()
    _save_state(state)
    _ensure_task_file()
    print(f"Init: round={state['round']}, stage={state['stage']}, status={state['status']}")


def cmd_status() -> None:
    if not STATE_FILE.exists():
        print("No loop state. Run 'init' first:\n  python scripts/agent_loop.py init --stage <stage> --round <round>")
        return

    state = _load_state()
    print(f"Round:        {state['round']}")
    print(f"Stage:        {state['stage']}")
    print(f"Status:       {state['status']}")
    print(f"Next actor:   {state['next_actor']}")

    if state["blocking"]:
        print(f"BLOCKED:      {state.get('blocked_reason', 'unknown reason')}")

    expected = _expected_input(state)
    if expected:
        print(f"Input file:   {expected}")
    output = _next_output(state)
    if output:
        print(f"Next output:  {output}")

    print(f"Last decision: {state.get('last_decision', 'N/A')}")
    print(f"Updated:      {state.get('updated_at', 'N/A')}")


def cmd_advance() -> None:
    state = _load_state()
    status = state["status"]

    if status == "needs_executor":
        _advance_needs_executor(state)
    elif status == "waiting_executor_report":
        _advance_waiting_executor(state)
    elif status == "waiting_evaluator_report":
        _advance_waiting_evaluator(state)
    elif status == "waiting_final_review":
        _advance_waiting_final_review(state)
    elif status == "needs_rework":
        print("Status: needs_rework — Executor needs to revise.")
        print("Update current_task.md, then rerun advance.")
    elif status == "ready_next_stage":
        print("Round complete. Run 'archive' then 'init --stage <next> --round <N>'")
    elif status == "stopped":
        print("Loop stopped. Re-init to start fresh.")
    elif status == "blocked":
        print(f"Loop BLOCKED: {state.get('blocked_reason', 'unknown')}")
    else:
        print(f"Unknown status: {status}")


def cmd_archive(round_num: int | None = None) -> None:
    if round_num is None:
        state = _load_state()
        round_num = state["round"]

    round_str = f"round_{round_num:03d}"
    archive_dir = LOOP_DIR / "history" / round_str
    archive_dir.mkdir(parents=True, exist_ok=True)

    sources = [
        (STATE_FILE, "state.json"),
        (CURRENT_TASK, "current_task.md"),
        (EXECUTOR_REPORT, "executor_report.md"),
        (EVALUATOR_REPORT, "evaluator_report.md"),
        (FINAL_REVIEW_PACKAGE, "final_review_package.md"),
        (FINAL_REVIEWER_REPORT, "final_reviewer_report.md"),
    ]
    copied = 0
    for src, name in sources:
        if _report_exists(src):
            shutil.copy2(src, archive_dir / name)
            copied += 1

    print(f"Archived round {round_num} to {archive_dir.relative_to(ROOT)} ({copied} files)")


# ── advance helpers ──────────────────────────────────────────────────


def _advance_needs_executor(state: Dict[str, Any]) -> None:
    task_text = CURRENT_TASK.read_text(encoding="utf-8") if CURRENT_TASK.exists() else "No task defined."

    prompt = (
        f"You are the Executor. Follow enhanced-baseline/docs/agent-execution-protocol.md.\n"
        f"\n"
        f"Current loop state:\n"
        f"- Round: {state['round']}\n"
        f"- Stage: {state['stage']}\n"
        f"- Status: {state['status']}\n"
        f"\n"
        f"Task:\n"
        f"{task_text}\n"
        f"\n"
        f"Required report path:\n"
        f"{EXECUTOR_REPORT}\n"
        f"\n"
        f"Return and write the Executor Report exactly as specified in agent-execution-protocol.md."
    )
    _write_prompt(LOOP_DIR / "executor_prompt.md", prompt)

    state["status"] = "waiting_executor_report"
    state["next_actor"] = "executor"
    state["updated_at"] = now()
    _save_state(state)
    print("→ executor_prompt.md written")


def _advance_waiting_executor(state: Dict[str, Any]) -> None:
    if not _report_exists(EXECUTOR_REPORT):
        print(f"Waiting: {EXECUTOR_REPORT}")
        return

    prompt = (
        f"You are the Evaluator. Follow enhanced-baseline/docs/agent-execution-protocol.md.\n"
        f"\n"
        f"Review:\n"
        f"- {EXECUTOR_REPORT}\n"
        f"- current code diff\n"
        f"- current loop task\n"
        f"\n"
        f"Do not implement fixes.\n"
        f"\n"
        f"Required report path:\n"
        f"{EVALUATOR_REPORT}\n"
        f"\n"
        f"If PASS or PASS_WITH_NOTES, also ensure:\n"
        f"{FINAL_REVIEW_PACKAGE}\n"
        f"\n"
        f"Return and write the Evaluator Report exactly as specified."
    )
    _write_prompt(LOOP_DIR / "evaluator_prompt.md", prompt)

    state["status"] = "waiting_evaluator_report"
    state["next_actor"] = "evaluator"
    state["updated_at"] = now()
    _save_state(state)
    print("→ evaluator_prompt.md written")


def _advance_waiting_evaluator(state: Dict[str, Any]) -> None:
    if not _report_exists(EVALUATOR_REPORT):
        print(f"Waiting: {EVALUATOR_REPORT}")
        return

    verdict = _parse_evaluator_verdict(EVALUATOR_REPORT)
    if verdict == "FAIL":
        state["status"] = "needs_rework"
        state["next_actor"] = "executor"
        state["updated_at"] = now()
        _save_state(state)
        print("→ verdict=FAIL, needs_rework")
        return

    prompt = (
        f"You are the Final Reviewer. Follow enhanced-baseline/docs/agent-execution-protocol.md.\n"
        f"\n"
        f"Review:\n"
        f"- {EXECUTOR_REPORT}\n"
        f"- {EVALUATOR_REPORT}\n"
        f"- {FINAL_REVIEW_PACKAGE}\n"
        f"- relevant code diff\n"
        f"\n"
        f"Write:\n"
        f"{FINAL_REVIEWER_REPORT}\n"
        f"\n"
        f"Decide:\n"
        f"- ACCEPT / ACCEPT_WITH_NOTES / REWORK / BLOCKED / STOP\n"
    )
    _write_prompt(LOOP_DIR / "final_reviewer_prompt.md", prompt)

    state["status"] = "waiting_final_review"
    state["next_actor"] = "final_reviewer"
    state["updated_at"] = now()
    _save_state(state)
    print(f"→ verdict={verdict}, final_reviewer_prompt.md written")


def _advance_waiting_final_review(state: Dict[str, Any]) -> None:
    if not _report_exists(FINAL_REVIEWER_REPORT):
        print(f"Waiting: {FINAL_REVIEWER_REPORT}")
        return

    decision = _parse_verdict(FINAL_REVIEWER_REPORT, _DECISION_RE)

    if decision == "REWORK":
        state["status"] = "needs_rework"
        state["next_actor"] = "executor"
        state["last_decision"] = "REWORK"
    elif decision == "BLOCKED":
        state["status"] = "blocked"
        state["next_actor"] = "user"
        state["blocking"] = True
        state["blocked_reason"] = "Final Reviewer marked BLOCKED"
        state["last_decision"] = "BLOCKED"
    elif decision == "STOP":
        state["status"] = "stopped"
        state["next_actor"] = "none"
        state["last_decision"] = "STOP"
    else:
        state["status"] = "ready_next_stage"
        state["next_actor"] = "user"
        state["last_decision"] = decision or "ACCEPT"

    state["updated_at"] = now()
    _save_state(state)
    cmd_archive(round_num=state["round"])
    print(f"→ decision={decision or 'ACCEPT'}, round {state['round']} complete")


def _expected_input(state: Dict[str, Any]) -> Optional[str]:
    return {
        "waiting_executor_report": str(EXECUTOR_REPORT),
        "waiting_evaluator_report": str(EVALUATOR_REPORT),
        "waiting_final_review": str(FINAL_REVIEWER_REPORT),
    }.get(state["status"])


def _next_output(state: Dict[str, Any]) -> Optional[str]:
    return {
        "needs_executor": str(LOOP_DIR / "executor_prompt.md"),
        "waiting_executor_report": str(LOOP_DIR / "evaluator_prompt.md"),
        "waiting_evaluator_report": str(LOOP_DIR / "final_reviewer_prompt.md"),
    }.get(state["status"])


# ── main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent loop orchestrator")
    parser.add_argument("command", choices=["init", "status", "advance", "archive"])
    parser.add_argument("--stage", default="stage_1_observability", help="Stage label for init")
    parser.add_argument("--round", type=int, default=None, help="Round number")
    parser.add_argument("--force", action="store_true", help="Overwrite existing state")
    args = parser.parse_args()

    if args.command == "init":
        cmd_init(stage=args.stage, round_num=args.round or 1, force=args.force)
    elif args.command == "status":
        cmd_status()
    elif args.command == "advance":
        cmd_advance()
    elif args.command == "archive":
        cmd_archive(round_num=args.round)


if __name__ == "__main__":
    main()
