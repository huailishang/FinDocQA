from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from evaluation.formal_submission import FORMAL_SUBMISSION_HEADER
from evaluation.token_accounting import (
    LedgerSource,
    TokenAccountingError,
    aggregate_candidate_usage,
    read_multi_slot_submission,
    validate_csv_against_usage,
    validate_pipeline_results_against_usage,
)


GOOD_REASONING = "依据给定证据核对关键事实并完成必要计算，结果与题目约束一致，因此得到该最终答案。"


def _ledger_row(attempt_id: str, *, purpose: str, prompt: int, completion: int) -> dict:
    return {
        "attempt_id": attempt_id,
        "qid": "q1",
        "provider": "organizer-api",
        "model": "Qwen3.6-plus",
        "purpose": purpose,
        "final_status": "COMPLETED",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _source(tmp_path: Path) -> LedgerSource:
    ledger = tmp_path / "formal.jsonl"
    rows = [
        _ledger_row("a1", purpose="initial_answer", prompt=100, completion=20),
        _ledger_row("a2", purpose="format_repair", prompt=30, completion=10),
        _ledger_row("a3", purpose="reasoning_repair", prompt=25, completion=15),
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return LedgerSource(
        run_id="formal-run",
        purpose="initial_answer",
        ledger_path=ledger,
        allowed_qids=("q1",),
        model="Qwen3.6-plus",
    )


def _write_formal_csv(path: Path, *, prompt: int, completion: int, total: int, summary_total: int | None = None) -> None:
    summary_total = total if summary_total is None else summary_total
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([
            list(FORMAL_SUBMISSION_HEADER),
            ["summary", "", "", "", "", prompt, completion, summary_total, ""],
            ["q1", "A", "", "", "", prompt, completion, total, GOOD_REASONING],
        ])


def test_multiple_related_calls_are_all_accumulated_per_qid(tmp_path: Path) -> None:
    source = _source(tmp_path)
    usage = aggregate_candidate_usage([source], candidate_qids=("q1",))

    assert usage["loaded_ledger_rows"] == 3
    assert usage["accounted_decision_calls"] == 3
    assert usage["all_decision_calls_accounted"] is True
    assert usage["by_qid"]["q1"]["prompt_tokens"] == 155
    assert usage["by_qid"]["q1"]["completion_tokens"] == 45
    assert usage["by_qid"]["q1"]["total_tokens"] == 200
    assert usage["by_qid"]["q1"]["provider_calls"] == 3

    validate_pipeline_results_against_usage([
        {
            "qid": "q1",
            "prompt_tokens": 155,
            "completion_tokens": 45,
            "total_tokens": 200,
            "metadata": {"provider_ledger_token_totals": {
                "prompt_tokens": 155,
                "completion_tokens": 45,
                "total_tokens": 200,
            }},
        }
    ], usage)


def test_pipeline_result_cannot_report_only_last_call_usage(tmp_path: Path) -> None:
    usage = aggregate_candidate_usage([_source(tmp_path)], candidate_qids=("q1",))
    with pytest.raises(TokenAccountingError, match="PipelineResult/ledger mismatch"):
        validate_pipeline_results_against_usage([
            {"qid": "q1", "prompt_tokens": 25, "completion_tokens": 15, "total_tokens": 40}
        ], usage)


def test_formal_csv_requires_per_qid_and_summary_token_equations(tmp_path: Path) -> None:
    usage = aggregate_candidate_usage([_source(tmp_path)], candidate_qids=("q1",))
    valid = tmp_path / "valid.csv"
    _write_formal_csv(valid, prompt=155, completion=45, total=200)
    parsed = read_multi_slot_submission(valid)
    assert parsed["summary"]["total_tokens"] == 200
    assert parsed["by_qid"]["q1"]["reasoning"] == GOOD_REASONING
    validate_csv_against_usage(valid, usage)

    bad_row = tmp_path / "bad_row.csv"
    _write_formal_csv(bad_row, prompt=155, completion=45, total=199)
    with pytest.raises(TokenAccountingError, match="token equation mismatch"):
        read_multi_slot_submission(bad_row)

    bad_summary = tmp_path / "bad_summary.csv"
    _write_formal_csv(bad_summary, prompt=155, completion=45, total=200, summary_total=199)
    with pytest.raises(TokenAccountingError, match="token equation mismatch|summary mismatch"):
        read_multi_slot_submission(bad_summary)


def test_formal_csv_rejects_missing_reasoning(tmp_path: Path) -> None:
    path = tmp_path / "missing_reasoning.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([
            list(FORMAL_SUBMISSION_HEADER),
            ["summary", "", "", "", "", 10, 5, 15, ""],
            ["q1", "A", "", "", "", 10, 5, 15, ""],
        ])
    with pytest.raises(TokenAccountingError, match="reasoning"):
        read_multi_slot_submission(path)
