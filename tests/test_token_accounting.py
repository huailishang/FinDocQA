from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from evaluation.token_accounting import (
    SUBMISSION_HEADER,
    LedgerSource,
    TokenAccountingError,
    aggregate_candidate_usage,
    annotate_ledger_file,
    build_candidate_usage_manifest,
    enforce_candidate_token_hard_cap,
    validate_csv_against_usage,
    validate_ledger_isolation,
    validate_manifest_against_usage,
    validate_paid_runtime_manifest_contract,
    validate_pipeline_results_against_usage,
)

ROOT = Path(__file__).resolve().parents[1]


def _row(
    attempt_id: str,
    qid: str,
    prompt: int,
    completion: int,
    *,
    model: str = "qwen3.7-max",
    purpose: str | None = None,
    status: str = "COMPLETED",
) -> dict:
    row = {
        "attempt_id": attempt_id,
        "qid": qid,
        "provider": "synthetic",
        "model": model,
        "stage": purpose or "llm_chat",
        "status": status,
        "final_status": status,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if purpose:
        row["purpose"] = purpose
    return row


def _source(
    tmp_path: Path,
    run_id: str,
    purpose: str,
    qids: tuple[str, ...],
    rows: list[dict],
    *,
    model: str = "qwen3.7-max",
) -> LedgerSource:
    output_dir = tmp_path / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = output_dir / "token_ledger.jsonl"
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return LedgerSource(
        run_id=run_id,
        purpose=purpose,
        ledger_path=ledger,
        allowed_qids=qids,
        model=model,
        output_dir=output_dir,
        usage_file=output_dir / "provider_usage.json",
        resolved_runtime_config_path=output_dir / "resolved_runtime_config.json",
    )


def _usage(tmp_path: Path, sources: list[LedgerSource], selected: dict[str, str], qids=("q1",)):
    return aggregate_candidate_usage(
        sources,
        candidate_qids=qids,
        selected_answer_source_by_qid=selected,
    )


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_max_initial_call_is_accounted(tmp_path: Path):
    source = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q1", 100, 20)])
    usage = _usage(tmp_path, [source], {"q1": "baseline"})
    assert usage["totals"] == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "provider_calls": 1,
    }
    assert usage["all_decision_calls_accounted"] is True


def test_max_plus_format_repair_sums_all_calls(tmp_path: Path):
    source = _source(
        tmp_path,
        "baseline",
        "initial_answer",
        ("q1",),
        [
            _row("a1", "q1", 100, 20, purpose="initial_answer"),
            _row("a2", "q1", 30, 5, purpose="format_repair"),
        ],
    )
    usage = _usage(tmp_path, [source], {"q1": "baseline"})
    assert usage["by_qid"]["q1"]["provider_calls"] == 2
    assert usage["by_qid"]["q1"]["total_tokens"] == 155
    assert usage["by_purpose"]["format_repair"]["total_tokens"] == 35


def test_max_plus_retrieval_rerun_sums_both_isolated_ledgers(tmp_path: Path):
    baseline = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q1", 100, 20)])
    rerun = _source(
        tmp_path,
        "retrieval",
        "retrieval_rerun",
        ("q1",),
        [_row("r1", "q1", 80, 10, purpose="retrieval_rerun")],
    )
    usage = _usage(tmp_path, [baseline, rerun], {"q1": "retrieval"})
    assert usage["totals"]["total_tokens"] == 210
    assert usage["by_run_id"]["retrieval"]["total_tokens"] == 90


def test_preview_keep_max_still_accounts_unselected_preview(tmp_path: Path):
    baseline = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q1", 100, 20)])
    preview = _source(
        tmp_path,
        "preview",
        "max_preview_adjudication",
        ("q1",),
        [_row("p1", "q1", 200, 40, model="qwen3.8-max-preview", purpose="max_preview_adjudication")],
        model="qwen3.8-max-preview",
    )
    usage = _usage(tmp_path, [baseline, preview], {"q1": "baseline"})
    assert usage["totals"]["total_tokens"] == 360
    assert usage["unselected_comparison_calls_accounted"] == 1
    assert usage["unselected_comparison_tokens_accounted"] == 240


def test_preview_replace_answer_still_accounts_baseline(tmp_path: Path):
    baseline = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q1", 100, 20)])
    preview = _source(
        tmp_path,
        "preview",
        "max_preview_adjudication",
        ("q1",),
        [_row("p1", "q1", 200, 40, model="qwen3.8-max-preview", purpose="max_preview_adjudication")],
        model="qwen3.8-max-preview",
    )
    usage = _usage(tmp_path, [baseline, preview], {"q1": "preview"})
    assert usage["totals"]["provider_calls"] == 2
    assert usage["unselected_comparison_calls_accounted"] == 1
    assert usage["by_qid"]["q1"]["selected_answer_source_run_id"] == "preview"


def test_baseline_plus_two_mutually_exclusive_targeted_runs(tmp_path: Path):
    baseline = _source(
        tmp_path,
        "baseline",
        "initial_answer",
        ("q1", "q2"),
        [_row("a1", "q1", 100, 20), _row("a2", "q2", 110, 20)],
    )
    retrieval = _source(
        tmp_path,
        "retrieval",
        "retrieval_rerun",
        ("q1",),
        [_row("r1", "q1", 50, 10, purpose="retrieval_rerun")],
    )
    preview = _source(
        tmp_path,
        "preview",
        "max_preview_adjudication",
        ("q2",),
        [_row("p1", "q2", 60, 15, model="qwen3.8-max-preview", purpose="max_preview_adjudication")],
        model="qwen3.8-max-preview",
    )
    usage = _usage(
        tmp_path,
        [baseline, retrieval, preview],
        {"q1": "retrieval", "q2": "preview"},
        qids=("q1", "q2"),
    )
    assert usage["accounted_decision_calls"] == 4
    assert usage["by_qid"]["q1"]["provider_calls"] == 2
    assert usage["by_qid"]["q2"]["provider_calls"] == 2


def test_duplicate_attempt_id_is_blocked(tmp_path: Path):
    source = _source(
        tmp_path,
        "baseline",
        "initial_answer",
        ("q1",),
        [_row("dup", "q1", 10, 2), _row("dup", "q1", 10, 2)],
    )
    with pytest.raises(TokenAccountingError, match="duplicate ledger attempt_id"):
        _usage(tmp_path, [source], {"q1": "baseline"})


def test_row_equation_mismatch_is_blocked(tmp_path: Path):
    row = _row("a1", "q1", 10, 2)
    row["total_tokens"] = 99
    source = _source(tmp_path, "baseline", "initial_answer", ("q1",), [row])
    with pytest.raises(TokenAccountingError, match="token equation mismatch"):
        _usage(tmp_path, [source], {"q1": "baseline"})


def test_qid_outside_allowlist_is_blocked(tmp_path: Path):
    source = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q2", 10, 2)])
    with pytest.raises(TokenAccountingError, match="outside allowed_qids"):
        _usage(tmp_path, [source], {"q1": "baseline"})


def test_exact_five_million_cap_is_blocked(tmp_path: Path):
    source = _source(
        tmp_path,
        "baseline",
        "initial_answer",
        ("q1",),
        [_row("a1", "q1", 4_900_000, 100_000)],
    )
    with pytest.raises(TokenAccountingError, match="hard cap reached"):
        _usage(tmp_path, [source], {"q1": "baseline"})
    with pytest.raises(TokenAccountingError):
        enforce_candidate_token_hard_cap(5_000_000)


def test_duplicate_ledger_path_breaks_isolation(tmp_path: Path):
    source = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q1", 10, 2)])
    duplicate = LedgerSource(
        run_id="other",
        purpose="retrieval_rerun",
        ledger_path=source.ledger_path,
        allowed_qids=("q1",),
        model=source.model,
        output_dir=tmp_path / "other",
        usage_file=tmp_path / "other" / "provider_usage.json",
        resolved_runtime_config_path=tmp_path / "other" / "resolved_runtime_config.json",
    )
    with pytest.raises(TokenAccountingError, match="isolated ledger"):
        validate_ledger_isolation([source, duplicate])


def test_pipeline_csv_and_manifest_must_match_ledger(tmp_path: Path):
    source = _source(tmp_path, "baseline", "initial_answer", ("q1",), [_row("a1", "q1", 100, 20)])
    usage = _usage(tmp_path, [source], {"q1": "baseline"})
    result = {
        "qid": "q1",
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "metadata": {"provider_ledger_token_totals": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }},
    }
    validate_pipeline_results_against_usage([result], usage)
    bad = dict(result, total_tokens=121)
    with pytest.raises(TokenAccountingError):
        validate_pipeline_results_against_usage([bad], usage)

    csv_path = tmp_path / "candidate.csv"
    _write_csv(csv_path, [
        list(SUBMISSION_HEADER),
        ["summary", "", "", "", "", 100, 20, 120, ""],
        ["q1", "A", "", "", "", 100, 20, 120, "依据当前证据核验选项A成立，相关约束均满足，因此最终答案选择A。"],
    ])
    validate_csv_against_usage(csv_path, usage)
    manifest = build_candidate_usage_manifest(
        candidate_id="candidate",
        usage=usage,
        sources=[source],
        selected_answer_source_by_qid={"q1": "baseline"},
        candidate_csv=csv_path,
    )
    validate_manifest_against_usage(manifest, usage)


def test_paid_runtime_manifest_enforces_isolation_fallback_retry_and_cap(tmp_path: Path):
    output = tmp_path / "run"
    valid = {
        "run_id": "run-1",
        "model": "qwen3.7-max",
        "decision_purpose": "initial_answer",
        "output_dir": str(output),
        "token_ledger_path": str(output / "token_ledger.jsonl"),
        "usage_file": str(output / "provider_usage.json"),
        "resolved_runtime_config_path": str(output / "resolved_runtime_config.json"),
        "allowed_qids": ["q1"],
        "fallback_authorized": False,
        "failure_policy": {"fallback_calls": 0},
        "retry_count": 0,
        "total_token_hard_cap": 5_000_000,
        "candidate_prior_total_tokens": 4_000_000,
        "token_budget": 900_000,
        "per_qid_completed_call_budget": 1,
        "circuit_breaker_policy": {"max_model_calls": 1},
    }
    contract = validate_paid_runtime_manifest_contract(valid, root=tmp_path, requested_output_dir=output)
    assert contract["fallback"] == "NO"
    invalid = dict(valid, token_budget=1_000_000)
    with pytest.raises(TokenAccountingError, match="hard cap reached"):
        validate_paid_runtime_manifest_contract(invalid, root=tmp_path, requested_output_dir=output)
    invalid = dict(valid, fallback_authorized=True)
    with pytest.raises(TokenAccountingError, match="fallback_authorized=false"):
        validate_paid_runtime_manifest_contract(invalid, root=tmp_path, requested_output_dir=output)


def test_annotate_ledger_adds_lineage_without_overwriting_conflicts(tmp_path: Path):
    ledger = tmp_path / "token_ledger.jsonl"
    ledger.write_text(json.dumps(_row("a1", "q1", 10, 2)) + "\n", encoding="utf-8")
    annotate_ledger_file(ledger, run_id="baseline", purpose="initial_answer")
    saved = json.loads(ledger.read_text(encoding="utf-8"))
    assert saved["run_id"] == "baseline"
    assert saved["purpose"] == "initial_answer"
    with pytest.raises(TokenAccountingError, match="conflicting ledger run_id"):
        annotate_ledger_file(ledger, run_id="other", purpose="initial_answer")


def test_hybrid_candidate_builder_counts_preview_even_when_baseline_answer_kept(tmp_path: Path):
    base_csv = tmp_path / "base.csv"
    _write_csv(base_csv, [
        list(SUBMISSION_HEADER),
        ["summary", "", "", "", "", 0, 0, 0, ""],
        ["q1", "A", "", "", "", 0, 0, 0, "依据当前证据核验选项A成立，相关约束均满足，因此最终答案选择A。"],
        ["q2", "B", "", "", "", 0, 0, 0, "依据当前证据核验选项B成立，相关约束均满足，因此最终答案选择B。"],
    ])
    baseline = _source(
        tmp_path,
        "baseline",
        "initial_answer",
        ("q1", "q2"),
        [_row("a1", "q1", 100, 20), _row("a2", "q2", 100, 20)],
    )
    preview = _source(
        tmp_path,
        "preview",
        "max_preview_adjudication",
        ("q2",),
        [_row("p1", "q2", 200, 40, model="qwen3.8-max-preview", purpose="max_preview_adjudication")],
        model="qwen3.8-max-preview",
    )
    spec_path = tmp_path / "spec.json"
    output_csv = tmp_path / "candidate.csv"
    output_manifest = tmp_path / "candidate_manifest.json"
    spec_path.write_text(json.dumps({
        "candidate_id": "keep-max",
        "base_submission": str(base_csv),
        "output_csv": str(output_csv),
        "output_manifest": str(output_manifest),
        "baseline_run_id": "baseline",
        "ledger_sources": [
            baseline.as_manifest_dict(),
            preview.as_manifest_dict(),
        ],
        "selected_answer_source_by_qid": {"q2": "baseline"},
    }), encoding="utf-8")
    module = _load_script("hybrid_candidate_builder", ROOT / "scripts" / "build_hybrid_candidate.py")
    report = module.build_from_spec(spec_path)
    assert report["decision_calls"] == 3
    assert report["total_tokens"] == 480
    assert report["unselected_comparison_calls_accounted"] == 1
    candidate = list(csv.reader(output_csv.open(encoding="utf-8")))
    assert candidate[3][1] == "B"
    assert candidate[1][7] == "480"


def test_declared_source_must_cover_every_allowed_qid(tmp_path: Path):
    source = _source(
        tmp_path,
        "partial-source",
        "initial_answer",
        ("q1", "q2"),
        [_row("a1", "q1", 10, 2)],
    )
    with pytest.raises(TokenAccountingError, match="missing terminal rows for allowed_qids"):
        aggregate_candidate_usage(
            [source],
            candidate_qids=("q1", "q2"),
            selected_answer_source_by_qid={"q1": "partial-source", "q2": "partial-source"},
        )
