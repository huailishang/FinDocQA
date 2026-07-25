"""Explicitly authorized wrapper for any paid API batch.

This is the only approved paid-run entrypoint. It validates a frozen manifest,
positive budgets, allowed qids, and processed-qid replay protection before it
can invoke run.py.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from runtime_safety import CircuitBreaker, TokenLedger, validate_paid_run, write_circuit_breaker
from evaluation.token_accounting import (
    TokenAccountingError,
    annotate_ledger_file,
    validate_paid_runtime_manifest_contract,
    validate_runtime_cumulative_cap,
)
from verification.calculation_contract import validate_next_smoke_manifest_contract



def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Expected JSON list artifact: {path}")
    return data

def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _ledger_token_usage(out: Path, qid: str) -> dict[str, Any]:
    rows = [x for x in TokenLedger(out / "token_ledger.jsonl").rows() if str(x.get("qid")) == qid]
    if not rows:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "ledger_rows": 0}
    return {
        "prompt_tokens": sum(int(x.get("prompt_tokens", 0) or 0) for x in rows),
        "completion_tokens": sum(int(x.get("completion_tokens", 0) or 0) for x in rows),
        "total_tokens": sum(int(x.get("total_tokens", 0) or 0) for x in rows),
        "ledger_rows": len(rows),
    }


def _normalize_record_tokens_from_ledger(out: Path, qid: str, record: dict[str, Any]) -> dict[str, Any]:
    """Make the provider ledger authoritative for cumulative per-qid usage."""
    usage = _ledger_token_usage(out, qid)
    if int(usage.get("ledger_rows") or 0) <= 0:
        return record
    record = dict(record)
    original = {
        "prompt_tokens": int(record.get("prompt_tokens") or 0),
        "completion_tokens": int(record.get("completion_tokens") or 0),
        "total_tokens": int(record.get("total_tokens") or 0),
    }
    ledger_totals = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    record.update(ledger_totals)
    meta = dict(record.get("metadata") or {})
    meta["original_result_token_totals"] = original
    meta["provider_ledger_token_totals"] = dict(ledger_totals)
    meta["provider_ledger_row_count"] = int(usage.get("ledger_rows") or 0)
    meta["provider_ledger_reconciled"] = True
    meta["ledger_token_recovered"] = original != ledger_totals
    record["metadata"] = meta
    return record


def _is_complete_local_deterministic_record(record: dict[str, Any]) -> bool:
    metadata = dict(record.get("metadata") or {})
    return (
        metadata.get("expected_provider_call") is False
        and int(metadata.get("provider_call_count", 0) or 0) == 0
        and int(metadata.get("token_usage", 0) or 0) == 0
        and metadata.get("local_deterministic_evidence_complete") is True
        and int(record.get("total_tokens", 0) or 0) == 0
    )


def _complete_local_deterministic_qids(out: Path) -> set[str]:
    return {
        str(record.get("qid"))
        for record in _load_json_list(out / "cumulative_run_checkpoint.json")
        if record.get("qid") and _is_complete_local_deterministic_record(record)
    }


def _persist_qid_artifacts(out: Path, qid: str) -> dict[str, Any]:
    checkpoint_path = out / "run_checkpoint.json"
    debug_path = out / "debug_results.json"
    if not checkpoint_path.exists():
        raise SystemExit("checkpoint_missing_after_call")
    if not debug_path.exists():
        raise SystemExit("debug_missing_after_call")
    checkpoint_records = _load_json_list(checkpoint_path)
    debug_records = _load_json_list(debug_path)
    checkpoint_record = next((x for x in reversed(checkpoint_records) if str(x.get("qid")) == qid), None)
    debug_record = next((x for x in reversed(debug_records) if str(x.get("qid")) == qid), None)
    if checkpoint_record is None:
        raise SystemExit("qid_missing_from_checkpoint")
    if debug_record is None:
        raise SystemExit("qid_missing_from_debug")
    checkpoint_record = _normalize_record_tokens_from_ledger(out, qid, checkpoint_record)
    debug_record = _normalize_record_tokens_from_ledger(out, qid, debug_record)
    per_qid_dir = out / "per_qid" / qid
    _atomic_write_json(per_qid_dir / "run_checkpoint.json", [checkpoint_record])
    _atomic_write_json(per_qid_dir / "debug_results.json", [debug_record])
    cumulative_checkpoint_path = out / "cumulative_run_checkpoint.json"
    cumulative_debug_path = out / "cumulative_debug_results.json"
    cumulative_checkpoint = [x for x in _load_json_list(cumulative_checkpoint_path) if str(x.get("qid")) != qid]
    cumulative_debug = [x for x in _load_json_list(cumulative_debug_path) if str(x.get("qid")) != qid]
    cumulative_checkpoint.append(checkpoint_record)
    cumulative_debug.append(debug_record)
    _atomic_write_json(cumulative_checkpoint_path, cumulative_checkpoint)
    _atomic_write_json(cumulative_debug_path, cumulative_debug)
    return {"checkpoint_record": checkpoint_record, "debug_record": debug_record}

def _write_post_run_integrity_summary(
    out: Path,
    allowed_qids: list[str],
    *,
    interrupted: bool = False,
    stop_reason: str = "",
    per_qid_provider_call_budget: int | dict[str, int] = 1,
    max_provider_call_budget: int | None = None,
) -> dict[str, Any]:
    ledger_rows = TokenLedger(out / "token_ledger.jsonl").rows()
    provider_terminal = {"COMPLETED", "ERROR", "TIMEOUT"}
    terminal_rows = [
        x for x in ledger_rows
        if str(x.get("final_status") or x.get("status")).upper() in provider_terminal
    ]
    pre_call_blocked_rows = [
        x for x in ledger_rows
        if str(x.get("final_status") or x.get("status")).upper() == "PRE_CALL_BLOCKED"
    ]
    acknowledged_pre_call_blocked_rows = [
        x for x in ledger_rows
        if str(x.get("final_status") or x.get("status")).upper() == "PRE_CALL_BLOCKED_ACKNOWLEDGED"
    ]
    ledger_qids = [str(x.get("qid")) for x in terminal_rows]
    pre_call_blocked_qids = list(dict.fromkeys(str(x.get("qid")) for x in pre_call_blocked_rows if x.get("qid")))
    pre_call_block_reasons_by_qid: dict[str, list[str]] = {}
    for row in pre_call_blocked_rows:
        qid = str(row.get("qid") or "")
        reason = str(row.get("pre_call_block_reason") or "pre_call_blocked")
        if qid:
            pre_call_block_reasons_by_qid.setdefault(qid, []).append(reason)
    checkpoint_qids = [str(x.get("qid")) for x in _load_json_list(out / "cumulative_run_checkpoint.json")]
    debug_qids = [str(x.get("qid")) for x in _load_json_list(out / "cumulative_debug_results.json")]
    checkpoint_records = _load_json_list(out / "cumulative_run_checkpoint.json")
    local_deterministic_qids = [
        str(record.get("qid")) for record in checkpoint_records
        if record.get("qid") and _is_complete_local_deterministic_record(record)
    ]
    attempted_set = set(ledger_qids) | set(local_deterministic_qids) | set(pre_call_blocked_qids)
    attempted_qids = [qid for qid in allowed_qids if qid in attempted_set]
    default_per_qid_budget = 1
    if isinstance(per_qid_provider_call_budget, dict):
        per_qid_budget_by_qid = {qid: int(per_qid_provider_call_budget.get(qid, 1) or 1) for qid in allowed_qids}
    else:
        default_per_qid_budget = int(per_qid_provider_call_budget or 1)
        per_qid_budget_by_qid = {qid: default_per_qid_budget for qid in allowed_qids}
    ledger_row_count_by_qid = {qid: 0 for qid in allowed_qids}
    token_sum_by_qid = {qid: 0 for qid in allowed_qids}
    pre_call_blocked_count_by_qid = {qid: 0 for qid in allowed_qids}
    for row in terminal_rows:
        qid = str(row.get("qid"))
        ledger_row_count_by_qid[qid] = ledger_row_count_by_qid.get(qid, 0) + 1
        token_sum_by_qid[qid] = token_sum_by_qid.get(qid, 0) + int(row.get("total_tokens", 0) or 0)
    for row in pre_call_blocked_rows:
        qid = str(row.get("qid"))
        pre_call_blocked_count_by_qid[qid] = pre_call_blocked_count_by_qid.get(qid, 0) + 1
    total_provider_call_count = len(terminal_rows)
    pre_call_blocked_total_count = len(pre_call_blocked_rows)
    qids_exceeding_call_budget = [
        qid for qid, count in ledger_row_count_by_qid.items()
        if count > int(per_qid_budget_by_qid.get(qid, default_per_qid_budget))
    ]
    qids_exceeding_call_budget = sorted(set(qids_exceeding_call_budget).union(pre_call_blocked_qids))
    pre_call_blocked_ok = pre_call_blocked_total_count == 0
    per_qid_provider_call_budget_ok = not qids_exceeding_call_budget
    total_provider_call_budget_ok = (
        True if max_provider_call_budget is None
        else total_provider_call_count <= max_provider_call_budget
    )
    provider_call_budget_ok = per_qid_provider_call_budget_ok and total_provider_call_budget_ok and pre_call_blocked_ok
    completed_provider_call_count = total_provider_call_count
    attempted_provider_call_count = total_provider_call_count + pre_call_blocked_total_count
    strict_contract_ok = provider_call_budget_ok and pre_call_blocked_ok
    completed_prefix_matches = checkpoint_qids == debug_qids == attempted_qids
    full_match = attempted_qids == checkpoint_qids == debug_qids == list(allowed_qids)
    summary = {
        "expected_qids": list(allowed_qids),
        "attempted_qids": attempted_qids,
        "ledger_qids": ledger_qids,
        "local_deterministic_qids": local_deterministic_qids,
        "local_deterministic_count": len(local_deterministic_qids),
        "checkpoint_qids": checkpoint_qids,
        "debug_qids": debug_qids,
        "missing_from_checkpoint": [qid for qid in attempted_qids if qid not in checkpoint_qids],
        "missing_from_debug": [qid for qid in attempted_qids if qid not in debug_qids],
        "unrun_qids": [qid for qid in allowed_qids if qid not in attempted_qids],
        "all_artifacts_match": full_match,
        "attempted_artifacts_match": completed_prefix_matches,
        "ledger_row_count_by_qid": ledger_row_count_by_qid,
        "token_sum_by_qid": token_sum_by_qid,
        "pre_call_blocked_count_by_qid": pre_call_blocked_count_by_qid,
        "pre_call_blocked_total_count": pre_call_blocked_total_count,
        "pre_call_blocked_qids": pre_call_blocked_qids,
        "pre_call_block_reasons_by_qid": pre_call_block_reasons_by_qid,
        "acknowledged_pre_call_blocked_total_count": len(acknowledged_pre_call_blocked_rows),
        "acknowledged_pre_call_blocked_qids": list(dict.fromkeys(
            str(row.get("qid")) for row in acknowledged_pre_call_blocked_rows if row.get("qid")
        )),
        "pre_call_blocked_ok": pre_call_blocked_ok,
        "provider_call_accounting_policy": {
            "model_call_definition": "provider_ledger_terminal_row",
            "default_per_qid_provider_call_budget": default_per_qid_budget,
            "per_qid_provider_call_budget_by_qid": per_qid_budget_by_qid,
            "max_provider_call_budget": max_provider_call_budget,
        },
        "total_provider_call_count": total_provider_call_count,
        "completed_provider_call_count": completed_provider_call_count,
        "attempted_provider_call_count": attempted_provider_call_count,
        "strict_contract_ok": strict_contract_ok,
        "per_qid_provider_call_budget_ok": per_qid_provider_call_budget_ok,
        "total_provider_call_budget_ok": total_provider_call_budget_ok,
        "provider_call_budget_ok": provider_call_budget_ok,
        "qids_exceeding_call_budget": qids_exceeding_call_budget,
        "interrupted": bool(interrupted),
        "stop_reason": stop_reason,
        "circuit_triggered": bool(interrupted),
        "run_status": "FAILED_STOPPED" if interrupted else "COMPLETED_STOPPED",
        "submission_exists": (out / "submission.csv").exists(),
    }
    _atomic_write_json(out / "post_run_integrity_summary.json", summary)
    return summary




def _write_circuit_state(
    out: Path,
    *,
    triggered: bool,
    reason: str,
    processed_qids: list[str],
    used_tokens: int,
    last_attempt_id: str = "",
) -> dict[str, Any]:
    payload = {
        "triggered": bool(triggered),
        "reason": str(reason),
        "processed_qids": list(processed_qids),
        "used_tokens": int(used_tokens),
        "last_attempt_id": str(last_attempt_id),
    }
    _atomic_write_json(out / "circuit_breaker.json", payload)
    return payload


def _write_safe_run_report(
    out: Path,
    *,
    allowed_qids: list[str],
    ledger: TokenLedger,
    interrupted: bool,
    stop_reason: str,
    circuit_triggered: bool,
) -> dict[str, Any]:
    summary_path = out / "post_run_integrity_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    report = {
        "schema_version": "safe_paid_runner_report/v1",
        "status": "FAILED_STOPPED" if interrupted else "COMPLETED_STOPPED",
        "expected_qids": list(allowed_qids),
        "attempted_qids": list(summary.get("attempted_qids") or []),
        "provider_calls": int(summary.get("total_provider_call_count", 0) or 0),
        "used_tokens": int(ledger.used_tokens()),
        "interrupted": bool(interrupted),
        "circuit_triggered": bool(circuit_triggered),
        "stop_reason": str(stop_reason),
        "submission_exists": bool(summary.get("submission_exists", False)),
        "integrity_summary": str(summary_path),
    }
    _atomic_write_json(out / "safe_run_report.json", report)
    return report


def _terminal_attempt_ids_for_ledger(out: Path) -> set[str]:
    terminal = {"COMPLETED", "ERROR", "TIMEOUT", "PRE_CALL_BLOCKED", "PRE_CALL_BLOCKED_ACKNOWLEDGED"}
    ids: set[str] = set()
    for row in TokenLedger(out / "token_ledger.jsonl").rows():
        status = str(row.get("final_status") or row.get("status") or "").upper()
        attempt_id = str(row.get("attempt_id") or "")
        if attempt_id and status in terminal:
            ids.add(attempt_id)
    return ids


def _mark_stale_recovery_artifacts(out: Path) -> dict[str, Any]:
    """Mark stale recovery/circuit artifacts when STARTED attempts later resolve.

    R4 artifact hygiene: a previous precheck may have written recovery_required
    or circuit_breaker while a provider call was still STARTED.  If the final
    ledger now contains terminal rows for those attempt ids and the post-run
    summary is healthy, keep an explicit recovery_status.json and make the old
    artifacts non-authoritative.
    """
    terminal_attempt_ids = _terminal_attempt_ids_for_ledger(out)
    recovery_path = out / "recovery_required.json"
    circuit_path = out / "circuit_breaker.json"
    summary_path = out / "post_run_integrity_summary.json"
    recovery = json.loads(recovery_path.read_text(encoding="utf-8")) if recovery_path.exists() else {}
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    inflight = recovery.get("inflight_attempts") if isinstance(recovery, dict) else []
    inflight = inflight if isinstance(inflight, list) else []
    blocked_attempt_ids = [str(item.get("attempt_id") or "") for item in inflight if isinstance(item, dict)]
    resolved_attempt_ids = [attempt_id for attempt_id in blocked_attempt_ids if attempt_id and attempt_id in terminal_attempt_ids]
    summary_healthy = bool(summary.get("provider_call_budget_ok") and not summary.get("interrupted") and summary.get("pre_call_blocked_total_count", 0) == 0)
    stale = bool(blocked_attempt_ids and len(resolved_attempt_ids) == len([x for x in blocked_attempt_ids if x]) and summary_healthy)
    status = {
        "recovery_required_active": bool(recovery.get("recovery_required")) and not stale if isinstance(recovery, dict) else False,
        "stale_recovery_artifact": stale,
        "resolved_attempt_ids": resolved_attempt_ids,
        "summary_healthy": summary_healthy,
    }
    if stale and isinstance(recovery, dict):
        recovery = dict(recovery)
        recovery["recovery_required"] = False
        recovery["stale"] = True
        recovery["stale_reason"] = "started_attempts_resolved_by_terminal_ledger_rows"
        _atomic_write_json(recovery_path, recovery)
        if circuit_path.exists():
            circuit = json.loads(circuit_path.read_text(encoding="utf-8"))
            if isinstance(circuit, dict):
                circuit["triggered"] = False
                circuit["stale"] = True
                circuit["stale_reason"] = "recovery_artifact_resolved_by_final_ledger"
                _atomic_write_json(circuit_path, circuit)
    _atomic_write_json(out / "recovery_status.json", status)
    return status

def _continue_on_blocking_enabled(cli_enabled: bool, failure_policy: dict[str, Any]) -> bool:
    return bool(cli_enabled or failure_policy.get("continue_on_blocking", False))


def _is_business_blocking_record(record: dict[str, Any]) -> bool:
    meta = dict(record.get("metadata") or {})
    if str(meta.get("answer_validation") or "") == "blocking_invalid":
        return True
    reason = str(meta.get("blocking_reason") or record.get("error") or "")
    return reason.startswith("production_integrity:") or bool(meta.get("blocking_reasons"))


def _failure_stop_reason(record: dict[str, Any], default: str = "subprocess_failed_after_result_write") -> str:
    meta = dict(record.get("metadata") or {})
    return str(meta.get("blocking_reason") or record.get("error") or default)


def _write_stop_and_summary(
    out: Path,
    allowed: list[str],
    ledger: TokenLedger,
    *,
    reason: str,
    last_attempt_id: str = "",
    per_qid_provider_call_budget: int | dict[str, int] = 1,
    max_provider_call_budget: int | None = None,
) -> None:
    processed = [qid for qid in allowed if qid in ledger.processed_qids()]
    _write_circuit_state(
        out,
        triggered=True,
        reason=reason,
        processed_qids=processed,
        used_tokens=ledger.used_tokens(),
        last_attempt_id=last_attempt_id,
    )
    _write_post_run_integrity_summary(
        out,
        allowed,
        interrupted=True,
        stop_reason=reason,
        per_qid_provider_call_budget=per_qid_provider_call_budget,
        max_provider_call_budget=max_provider_call_budget,
    )
    _write_safe_run_report(
        out,
        allowed_qids=allowed,
        ledger=ledger,
        interrupted=True,
        stop_reason=reason,
        circuit_triggered=True,
    )


def _last_attempt_id_for_qid(ledger: TokenLedger, qid: str) -> str:
    rows = [x for x in ledger.rows() if str(x.get("qid")) == qid]
    return str(rows[-1].get("attempt_id") or "") if rows else ""


def _validate_calculation_contract_if_present(manifest_data: dict[str, Any]) -> None:
    if not manifest_data.get("calculation_contract"):
        return
    errors = validate_next_smoke_manifest_contract(manifest_data)
    if errors:
        raise SystemExit("calculation_contract_invalid:" + ";".join(errors))


def _provider_call_budgets(manifest_data: dict[str, Any]) -> tuple[int, dict[str, int], int | None]:
    _validate_calculation_contract_if_present(manifest_data)
    policy = dict(manifest_data.get("circuit_breaker") or manifest_data.get("circuit_breaker_policy") or {})
    allowed = [str(x) for x in manifest_data.get("allowed_qids", [])]
    contract = str(manifest_data.get("calculation_contract") or "")
    manifest_per_qid_budget = manifest_data.get("per_qid_completed_call_budget")
    if manifest_per_qid_budget is not None:
        default_per_qid = int(manifest_per_qid_budget)
    else:
        default_per_qid = int(policy.get("per_question_max_calls") or policy.get("per_qid_provider_calls") or 1)
    raw_by_qid = (
        policy.get("per_question_max_calls_by_qid")
        or policy.get("per_qid_provider_calls_by_qid")
        or policy.get("qid_provider_call_budgets")
        or {}
    )
    if not isinstance(raw_by_qid, dict):
        raise SystemExit("provider call budget by qid must be a JSON object")
    by_qid = {qid: int(raw_by_qid.get(qid, default_per_qid) or default_per_qid) for qid in allowed}
    if contract == "two-call":
        by_qid = {qid: 2 for qid in allowed}
        default_per_qid = 2
    elif contract == "one-call":
        by_qid = {qid: 1 for qid in allowed}
        default_per_qid = 1
    max_total_raw = policy.get("max_model_calls") or policy.get("max_provider_calls")
    max_total = int(max_total_raw) if max_total_raw is not None else sum(by_qid.values())
    return default_per_qid, by_qid, max_total


def _check_provider_call_budget(
    out: Path,
    allowed_qids: list[str],
    *,
    per_qid_provider_call_budget: int | dict[str, int],
    max_provider_call_budget: int | None,
    interrupted: bool = False,
    stop_reason: str = "",
) -> dict[str, Any]:
    summary = _write_post_run_integrity_summary(
        out,
        allowed_qids,
        interrupted=interrupted,
        stop_reason=stop_reason,
        per_qid_provider_call_budget=per_qid_provider_call_budget,
        max_provider_call_budget=max_provider_call_budget,
    )
    if not summary.get("pre_call_blocked_ok", True):
        qids = list(summary.get("pre_call_blocked_qids") or [])
        reasons = dict(summary.get("pre_call_block_reasons_by_qid") or {})
        detail = ";".join(
            f"{qid}={'|'.join(str(value) for value in reasons.get(qid, []))}"
            for qid in qids
            if reasons.get(qid)
        )
        message = "provider_call_budget_precheck_blocked:" + ",".join(qids)
        if detail:
            message += ":" + detail
        raise SystemExit(message)
    if not summary["per_qid_provider_call_budget_ok"]:
        raise SystemExit("per_qid_provider_call_budget_exceeded:" + ",".join(summary["qids_exceeding_call_budget"]))
    if not summary["total_provider_call_budget_ok"]:
        raise SystemExit("total_provider_call_budget_exceeded")
    return summary


def _structural_error_for_record(record: dict[str, Any]) -> bool:
    meta = dict(record.get("metadata") or {})
    state = str(meta.get("final_state") or "").upper()
    if _is_business_blocking_record(record):
        return False
    return state not in {"ACCEPTED", "REVIEW_REQUIRED", "HARD_BLOCK", "BLOCKED", "FAILED"}


def _observe_with_continue_on_blocking_policy(
    breaker: CircuitBreaker,
    record: dict[str, Any],
    *,
    continue_on_blocking: bool,
    token_usage_known: bool,
    per_question_budget_ok: bool,
) -> str | None:
    """Keep structural and usage safety while allowing authorized business blocks."""
    metadata = dict(record.get("metadata") or {})
    if metadata.get("prompt_estimate_under_actual") is True:
        return "prompt_estimate_under_actual"
    if int(metadata.get("actual_total_tokens", record.get("total_tokens", 0)) or 0) > int(
        metadata.get("prompt_budget_hard_cap_tokens", 45_000) or 45_000
    ):
        return "actual_total_exceeds_prompt_hard_cap"
    if continue_on_blocking and _is_business_blocking_record(record):
        if not token_usage_known:
            return "missing_token_usage"
        if not per_question_budget_ok:
            return "per_question_token_budget_exceeded"
        if _structural_error_for_record(record):
            return "structural_error"
        return None
    state = str(metadata.get("final_state") or "HARD_BLOCK")
    reasons = metadata.get("hard_block_reasons") or metadata.get("blocking_reasons") or []
    reason = "|".join(str(value) for value in reasons)
    return breaker.observe(
        final_state=state,
        reason=reason,
        structural_error=_structural_error_for_record(record),
        token_usage_known=token_usage_known,
        per_question_budget_ok=per_question_budget_ok,
    )




def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument('--allow-paid-run',action='store_true')
    p.add_argument('--max-questions',type=int)
    p.add_argument('--token-budget',type=int)
    p.add_argument('--per-question-token-budget',type=int)
    p.add_argument('--run-manifest',type=Path)
    p.add_argument('--config',type=Path,default=Path('config/config.yaml'))
    p.add_argument('--output-dir',required=True)
    p.add_argument('--artifact-mode',choices=('standard','evaluation-only'),default='standard')
    p.add_argument('--retry-authorized-qid',action='append',default=[])
    p.add_argument('--recover-started-qid',action='append',default=[],help='Explicit manual recovery for orphan STARTED qids; forbidden for canary approvals')
    p.add_argument('--continue-on-blocking',action='store_true',help='Continue after persisted production-integrity blocking results when explicitly authorized')
    p.add_argument('--execute',action='store_true',help='Actually invoke run.py after validation')
    return p.parse_args()

def main():
    a=parse_args(); config=(ROOT/a.config).resolve(); manifest=(ROOT/a.run_manifest).resolve() if a.run_manifest else None
    data,policy=validate_paid_run(allow_paid_run=a.allow_paid_run,max_questions=a.max_questions,token_budget=a.token_budget,per_question_token_budget=a.per_question_token_budget,manifest_path=manifest,root=ROOT,config_path=config)
    is_canary = str(policy.approval_level).lower().startswith('canary')
    if is_canary and a.artifact_mode != 'evaluation-only':
        raise SystemExit('Canary runs require --artifact-mode evaluation-only')
    out=(ROOT/a.output_dir).resolve()
    hybrid_runtime_enabled = str(data.get("schema_version") or "") == "bb_paid_run_manifest/v1"
    if hybrid_runtime_enabled:
        try:
            hybrid_runtime = validate_paid_runtime_manifest_contract(
                data,
                root=ROOT,
                requested_output_dir=out,
            )
        except TokenAccountingError as exc:
            raise SystemExit(f"hybrid_runtime_manifest_invalid:{exc}") from exc
    else:
        hybrid_runtime = {
            "run_id": str(data.get("run_id") or "legacy-run"),
            "model": str(data.get("model") or ""),
            "decision_purpose": "other_declared_decision_call",
            "token_ledger_path": out / "token_ledger.jsonl",
            "usage_file": out / "provider_usage.json",
            "resolved_runtime_config_path": out / "resolved_runtime_config.json",
            "candidate_prior_total_tokens": 0,
            "total_token_hard_cap": 5_000_000,
            "retry_count": int(data.get("retry_count", 0) or 0),
            "fallback": "LEGACY_POLICY",
        }
    ledger=TokenLedger(hybrid_runtime["token_ledger_path"])
    processed=ledger.processed_qids() | _complete_local_deterministic_qids(out); retries=set(a.retry_authorized_qid); recoveries=set(a.recover_started_qid)
    allowed=list(policy.allowed_qids)
    continue_on_blocking = (
        _continue_on_blocking_enabled(a.continue_on_blocking, policy.failure_policy)
        and not is_canary
    )
    default_per_qid_provider_call_budget, per_qid_provider_call_budget_by_qid, max_provider_call_budget = _provider_call_budgets(data)
    blocked=ledger.blocked_qids()
    if blocked:
        unauthorized = blocked if is_canary else (blocked - recoveries)
        if unauthorized:
            write_circuit_breaker(out/'circuit_breaker.json',reason='orphan_started_recovery_required',processed_qids=processed,used_tokens=ledger.used_tokens(),last_attempt_id='')
            recovery={'recovery_required':True,'reason':'orphan_started_recovery_required','blocked_qids':sorted(unauthorized),'inflight_attempts':ledger.inflight_attempts()}
            out.mkdir(parents=True,exist_ok=True)
            (out/'recovery_required.json').write_text(json.dumps(recovery,ensure_ascii=False,indent=2),encoding='utf-8')
            raise SystemExit('Orphan STARTED attempt blocks paid run; manual evaluator recovery required')
    pending=[q for q in allowed if q not in processed or q in retries or q in recoveries]
    if len(pending)>policy.max_questions: raise SystemExit('Pending qids exceed manifest max_questions')
    if ledger.used_tokens()+policy.per_question_token_budget>policy.token_budget: raise SystemExit('Insufficient remaining batch budget before first call')
    if hybrid_runtime_enabled:
        try:
            validate_runtime_cumulative_cap(
                prior_total_tokens=hybrid_runtime["candidate_prior_total_tokens"],
                current_run_tokens=ledger.used_tokens(),
                reserve_tokens=policy.per_question_token_budget,
                hard_cap_tokens=hybrid_runtime["total_token_hard_cap"],
            )
        except TokenAccountingError as exc:
            raise SystemExit(f"candidate_token_hard_cap_precheck:{exc}") from exc
    report={'validated':True,'artifact_mode':a.artifact_mode,'run_id':data.get('run_id'),'processed_qids':sorted(processed),'pending_qids':pending,'used_tokens':ledger.used_tokens(),'execute_requested':a.execute,'calculation_contract':data.get('calculation_contract'),'stage_call_budgets':data.get('stage_call_budgets'),'per_qid_completed_call_budget':data.get('per_qid_completed_call_budget'),'prompt_budget_contract':{'target_total_tokens':int(data.get('prompt_target_total_tokens',38000) or 38000),'hard_cap_tokens':int(data.get('prompt_hard_cap_tokens',policy.per_question_token_budget) or policy.per_question_token_budget),'full100_projection_hard_cap':int(data.get('full100_projection_hard_cap',4000000) or 4000000)},'provider_call_accounting_policy':{'model_call_definition':'provider_ledger_terminal_row','default_per_qid_provider_call_budget':default_per_qid_provider_call_budget,'per_qid_provider_call_budget_by_qid':per_qid_provider_call_budget_by_qid,'max_provider_call_budget':max_provider_call_budget}}
    if hybrid_runtime_enabled:
        report["hybrid_runtime_contract"] = {
            "decision_purpose": hybrid_runtime["decision_purpose"],
            "model": hybrid_runtime["model"],
            "token_ledger_path": str(hybrid_runtime["token_ledger_path"]),
            "usage_file": str(hybrid_runtime["usage_file"]),
            "resolved_runtime_config_path": str(hybrid_runtime["resolved_runtime_config_path"]),
            "candidate_prior_total_tokens": hybrid_runtime["candidate_prior_total_tokens"],
            "total_token_hard_cap": hybrid_runtime["total_token_hard_cap"],
            "fallback": hybrid_runtime["fallback"],
            "retry_count": hybrid_runtime["retry_count"],
        }
    out.mkdir(parents=True,exist_ok=True); (out/'manifest_validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not a.execute:
        print(json.dumps(report,ensure_ascii=False)); return
    env=dict(os.environ)
    env["LLM_TOKEN_LEDGER_PATH"] = str(hybrid_runtime["token_ledger_path"])
    env["SAFE_RUN_EXECUTION"] = "1"
    env["LLM_RESOLVED_CONFIG_PATH"] = str(hybrid_runtime["resolved_runtime_config_path"])
    if hybrid_runtime_enabled:
        env["FREETOKEN_USAGE_FILE"] = str(hybrid_runtime["usage_file"])
        env["FREETOKEN_MODEL"] = str(hybrid_runtime["model"])
        env["FREETOKEN_TOKEN_BUDGET"] = str(policy.token_budget)
        env["FREETOKEN_USER_AGENT"] = os.getenv(
            "FREETOKEN_USER_AGENT", "Mozilla/5.0"
        )
        env["LLM_MODEL_ID"] = str(hybrid_runtime["model"])
        env["SAFE_RUN_DECISION_PURPOSE"] = str(hybrid_runtime["decision_purpose"])
        env["SAFE_RUN_ID"] = str(hybrid_runtime["run_id"])
        env["SAFE_RUN_PROMPT_TARGET_TOTAL_TOKENS"] = str(
            int(data.get("prompt_target_total_tokens", 38_000) or 38_000)
        )
        env["SAFE_RUN_PROMPT_HARD_CAP_TOKENS"] = str(
            int(data.get("prompt_hard_cap_tokens", policy.per_question_token_budget) or policy.per_question_token_budget)
        )
    env["LLM_STATIC_CONFIG_PATH"] = str(config)
    env["SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON"] = json.dumps(per_qid_provider_call_budget_by_qid, ensure_ascii=False)
    env["SAFE_RUN_MAX_PROVIDER_CALL_BUDGET"] = str(max_provider_call_budget or "")
    env["SAFE_RUN_CALCULATION_CONTRACT"] = str(data.get("calculation_contract") or "")
    fallback_disabled = (
        int(policy.failure_policy.get("fallback_calls", 0) or 0) <= 0
        or data.get("fallback_authorized") is False
    )
    if fallback_disabled:
        env["SAFE_RUN_DISABLE_FALLBACK"] = "1"
        env["SAFE_RUN_FALLBACK_ENABLED"] = "0"
    else:
        env["SAFE_RUN_DISABLE_FALLBACK"] = "0"
        env["SAFE_RUN_FALLBACK_ENABLED"] = "1"
    breaker=CircuitBreaker("canary" if str(policy.approval_level).lower().startswith("canary") else "stable")
    for qid in pending:
        if hybrid_runtime_enabled:
            try:
                validate_runtime_cumulative_cap(
                    prior_total_tokens=hybrid_runtime["candidate_prior_total_tokens"],
                    current_run_tokens=ledger.used_tokens(),
                    reserve_tokens=policy.per_question_token_budget,
                    hard_cap_tokens=hybrid_runtime["total_token_hard_cap"],
                )
            except TokenAccountingError as exc:
                write_circuit_breaker(
                    out/"circuit_breaker.json",
                    reason="candidate_token_hard_cap_precheck",
                    processed_qids=ledger.processed_qids(),
                    used_tokens=ledger.used_tokens(),
                )
                raise SystemExit(f"candidate_token_hard_cap_precheck:{exc}") from exc
        if ledger.used_tokens()+policy.per_question_token_budget>policy.token_budget:
            write_circuit_breaker(out/"circuit_breaker.json",reason="insufficient_remaining_budget",processed_qids=ledger.processed_qids(),used_tokens=ledger.used_tokens())
            raise SystemExit("Insufficient remaining batch budget before call")
        cmd=[
            sys.executable, str(ROOT/'run.py'),
            '--config', str(config),
            '--output-dir', str(out),
            '--artifact-mode', a.artifact_mode,
            '--save-every', '1',
            '--qid', qid,
        ]
        run_error = None
        try:
            subprocess.run(cmd,cwd=ROOT,check=True,env=env)
        except subprocess.CalledProcessError as exc:
            run_error = exc
        finally:
            resolved_path=out/'resolved_runtime_config.json'
            if resolved_path.exists():
                resolved=json.loads(resolved_path.read_text(encoding='utf-8'))
                resolved_manifest=dict(data)
                resolved_manifest['artifact_mode']=a.artifact_mode
                resolved_manifest['resolved_runtime_config']=resolved
                (out/'resolved_run_manifest.json').write_text(json.dumps(resolved_manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        if hybrid_runtime_enabled:
            try:
                annotate_ledger_file(
                    hybrid_runtime["token_ledger_path"],
                    run_id=hybrid_runtime["run_id"],
                    purpose=hybrid_runtime["decision_purpose"],
                )
                validate_runtime_cumulative_cap(
                    prior_total_tokens=hybrid_runtime["candidate_prior_total_tokens"],
                    current_run_tokens=ledger.used_tokens(),
                    hard_cap_tokens=hybrid_runtime["total_token_hard_cap"],
                )
            except TokenAccountingError as exc:
                _write_stop_and_summary(
                    out,
                    allowed,
                    ledger,
                    reason="candidate_token_hard_cap_or_lineage_failure",
                    last_attempt_id=_last_attempt_id_for_qid(ledger, qid),
                    per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid,
                    max_provider_call_budget=max_provider_call_budget,
                )
                raise SystemExit(f"candidate_token_hard_cap_or_lineage_failure:{exc}") from exc
        if run_error is not None:
            try:
                persisted = _persist_qid_artifacts(out, qid)
            except SystemExit as persist_exc:
                stop_reason = f"subprocess_failed_and_artifact_persistence_failed:{persist_exc}"
                _write_stop_and_summary(out, allowed, ledger, reason=stop_reason, last_attempt_id=_last_attempt_id_for_qid(ledger, qid), per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid, max_provider_call_budget=max_provider_call_budget)
                raise SystemExit(f"Circuit breaker: {stop_reason}")
            record = persisted["checkpoint_record"]
            stop_reason = _failure_stop_reason(record)
            try:
                _check_provider_call_budget(out, allowed, per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid, max_provider_call_budget=max_provider_call_budget, interrupted=True, stop_reason=stop_reason)
            except SystemExit as budget_exc:
                stop_reason = str(budget_exc) or "provider_call_budget_exceeded"
                _write_stop_and_summary(out, allowed, ledger, reason=stop_reason, last_attempt_id=_last_attempt_id_for_qid(ledger, qid), per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid, max_provider_call_budget=max_provider_call_budget)
                raise SystemExit(f"Circuit breaker: {stop_reason}")
            qid_rows = [x for x in ledger.rows() if str(x.get("qid")) == qid]
            qid_tokens = sum(int(x.get("total_tokens", 0) or 0) for x in qid_rows)
            local_zero_call_complete = _is_complete_local_deterministic_record(record)
            policy_stop = _observe_with_continue_on_blocking_policy(
                breaker,
                record,
                continue_on_blocking=continue_on_blocking,
                token_usage_known=local_zero_call_complete or (bool(qid_rows) and qid_tokens > 0),
                per_question_budget_ok=qid_tokens <= policy.per_question_token_budget,
            )
            if policy_stop:
                stop_reason = policy_stop
            if _is_business_blocking_record(record) and continue_on_blocking and not policy_stop:
                continue
            _write_stop_and_summary(out, allowed, ledger, reason=stop_reason, last_attempt_id=_last_attempt_id_for_qid(ledger, qid), per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid, max_provider_call_budget=max_provider_call_budget)
            raise SystemExit(f"Circuit breaker: {stop_reason}")
        resolved_path=out/'resolved_runtime_config.json'
        if not resolved_path.exists():
            raise SystemExit('Resolved runtime config missing after model client construction')
        try:
            persisted = _persist_qid_artifacts(out, qid)
        except SystemExit as exc:
            reason = str(exc) or "artifact_persistence_failed"
            write_circuit_breaker(out/"circuit_breaker.json",reason=reason,processed_qids=ledger.processed_qids(),used_tokens=ledger.used_tokens())
            raise SystemExit(f"Circuit breaker: {reason}")
        record = persisted["checkpoint_record"]
        try:
            _check_provider_call_budget(out, allowed, per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid, max_provider_call_budget=max_provider_call_budget)
        except SystemExit as exc:
            stop = str(exc) or "provider_call_budget_exceeded"
            write_circuit_breaker(out/"circuit_breaker.json",reason=stop,processed_qids=ledger.processed_qids(),used_tokens=ledger.used_tokens(),last_attempt_id=_last_attempt_id_for_qid(ledger, qid))
            raise SystemExit(f"Circuit breaker: {stop}")
        qid_rows=[x for x in ledger.rows() if str(x.get("qid"))==qid]
        qid_tokens=sum(int(x.get("total_tokens",0) or 0) for x in qid_rows)
        local_zero_call_complete = _is_complete_local_deterministic_record(record)
        stop=_observe_with_continue_on_blocking_policy(
            breaker,
            record,
            continue_on_blocking=continue_on_blocking,
            token_usage_known=local_zero_call_complete or (bool(qid_rows) and qid_tokens > 0),
            per_question_budget_ok=qid_tokens<=policy.per_question_token_budget,
        )
        if stop:
            last_attempt=str(qid_rows[-1].get("attempt_id") or "") if qid_rows else ""
            write_circuit_breaker(out/"circuit_breaker.json",reason=stop,processed_qids=ledger.processed_qids(),used_tokens=ledger.used_tokens(),last_attempt_id=last_attempt)
            raise SystemExit(f"Circuit breaker: {stop}")
    completion_reason = "canary_completed_stop" if is_canary else "run_completed"
    _write_post_run_integrity_summary(
        out,
        allowed,
        interrupted=False,
        stop_reason=completion_reason,
        per_qid_provider_call_budget=per_qid_provider_call_budget_by_qid,
        max_provider_call_budget=max_provider_call_budget,
    )
    _write_circuit_state(
        out,
        triggered=False,
        reason=completion_reason,
        processed_qids=[qid for qid in allowed if qid in ledger.processed_qids()],
        used_tokens=ledger.used_tokens(),
    )
    _write_safe_run_report(
        out,
        allowed_qids=allowed,
        ledger=ledger,
        interrupted=False,
        stop_reason=completion_reason,
        circuit_triggered=False,
    )
    _mark_stale_recovery_artifacts(out)
if __name__=='__main__': main()
