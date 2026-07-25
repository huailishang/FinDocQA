from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_safety import TokenLedger, TokenAttempt
from scripts.safe_paid_runner import _check_provider_call_budget, _write_post_run_integrity_summary, _write_stop_and_summary


def _append_completed_and_preblocked(out: Path, qid: str = "case_004") -> None:
    ledger = TokenLedger(out / "token_ledger.jsonl")
    ledger.append(TokenAttempt(
        attempt_id="a1",
        qid=qid,
        provider="freetoken-primary",
        model="qwen3.7-plus",
        stage="calculation_formula_extraction",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        status="COMPLETED",
        final_status="COMPLETED",
    ))
    ledger.append(TokenAttempt(
        attempt_id="a2",
        qid=qid,
        provider="freetoken-primary",
        model="qwen3.7-plus",
        stage="calculation_option_matching",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        status="PRE_CALL_BLOCKED",
        final_status="PRE_CALL_BLOCKED",
    ))


def test_post_run_summary_marks_pre_call_blocked_as_budget_failure():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _append_completed_and_preblocked(out)
        summary = _write_post_run_integrity_summary(
            out,
            ["case_004", "case_008"],
            per_qid_provider_call_budget={"case_004": 1, "case_008": 1},
            max_provider_call_budget=2,
        )
        assert summary["total_provider_call_count"] == 1
        assert summary["completed_provider_call_count"] == 1
        assert summary["attempted_provider_call_count"] == 2
        assert summary["pre_call_blocked_total_count"] == 1
        assert summary["pre_call_blocked_qids"] == ["case_004"]
        assert summary["pre_call_blocked_count_by_qid"]["case_004"] == 1
        assert summary["pre_call_blocked_ok"] is False
        assert summary["per_qid_provider_call_budget_ok"] is False
        assert summary["provider_call_budget_ok"] is False
        assert summary["strict_contract_ok"] is False
        assert "case_004" in summary["qids_exceeding_call_budget"]
        assert summary["unrun_qids"] == ["case_008"]


def test_check_provider_call_budget_raises_on_pre_call_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _append_completed_and_preblocked(out)
        try:
            _check_provider_call_budget(
                out,
                ["case_004", "case_008"],
                per_qid_provider_call_budget={"case_004": 1, "case_008": 1},
                max_provider_call_budget=2,
            )
        except SystemExit as exc:
            assert "provider_call_budget_precheck_blocked:case_004" in str(exc)
        else:
            raise AssertionError("PRE_CALL_BLOCKED was incorrectly accepted as budget OK")



def test_pre_call_blocked_preserves_stage_label_in_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ledger = TokenLedger(out / "token_ledger.jsonl")
        ledger.append(TokenAttempt(
            attempt_id="a1",
            qid="case_004",
            provider="freetoken-primary",
            model="qwen3.7-plus",
            stage="calculation_formula_extraction",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            status="COMPLETED",
            final_status="COMPLETED",
        ))
        ledger.append(TokenAttempt(
            attempt_id="a2",
            qid="case_004",
            provider="freetoken-primary",
            model="qwen3.7-plus",
            stage="calculation_option_matching",
            status="PRE_CALL_BLOCKED",
            final_status="PRE_CALL_BLOCKED",
        ))
        rows = TokenLedger(out / "token_ledger.jsonl").rows()
        assert rows[0]["stage"] == "calculation_formula_extraction"
        assert rows[1]["stage"] == "calculation_option_matching"



def test_pre_call_blocked_writes_circuit_breaker_and_leaves_remaining_qid_unrun():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _append_completed_and_preblocked(out)
        ledger = TokenLedger(out / "token_ledger.jsonl")
        _write_stop_and_summary(
            out,
            ["case_004", "case_008"],
            ledger,
            reason="provider_call_budget_precheck_blocked:case_004",
            last_attempt_id="a2",
            per_qid_provider_call_budget={"case_004": 1, "case_008": 1},
            max_provider_call_budget=2,
        )
        import json
        circuit = json.loads((out / "circuit_breaker.json").read_text(encoding="utf-8"))
        summary = json.loads((out / "post_run_integrity_summary.json").read_text(encoding="utf-8"))
        assert circuit["triggered"] is True
        assert circuit["reason"] == "provider_call_budget_precheck_blocked:case_004"
        assert summary["interrupted"] is True
        assert summary["unrun_qids"] == ["case_008"]
        assert summary["strict_contract_ok"] is False


if __name__ == "__main__":
    test_post_run_summary_marks_pre_call_blocked_as_budget_failure()
    test_check_provider_call_budget_raises_on_pre_call_blocked()
    test_pre_call_blocked_preserves_stage_label_in_ledger()
    test_pre_call_blocked_writes_circuit_breaker_and_leaves_remaining_qid_unrun()
    print("pre-call blocked circuit breaker tests: PASS")
