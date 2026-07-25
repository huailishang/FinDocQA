from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.safe_paid_runner import _provider_call_budgets

QIDS = ["case_004", "case_008", "case_019"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _base_manifest() -> dict:
    return {
        "run_id": "unit_two_call_contract_manifest",
        "approved_commit": _git_head(),
        "config_sha256": _sha256(ROOT / "config/config.yaml"),
        "max_questions": 3,
        "token_budget": 150000,
        "per_question_token_budget": 50000,
        "approval_level": "stable_two_call_contract_dry_validation",
        "allowed_qids": list(QIDS),
        "calculation_contract": "two-call",
        "per_qid_completed_call_budget": 2,
        "stage_call_budgets": {
            "calculation_formula_extraction": 1,
            "calculation_option_matching": 1,
        },
        "failure_policy": {"fallback_calls": 0, "retry_calls": 0, "continue_on_blocking": True},
        "circuit_breaker_policy": {},
        "artifact_mode": "evaluation-only",
    }


def _run_safe_runner(manifest: dict, out: Path) -> subprocess.CompletedProcess[str]:
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [
        sys.executable,
        str(ROOT / "scripts/safe_paid_runner.py"),
        "--allow-paid-run",
        "--max-questions", "3",
        "--token-budget", "150000",
        "--per-question-token-budget", "50000",
        "--run-manifest", str(manifest_path),
        "--config", "config/config.yaml",
        "--output-dir", str(out / "runner_out"),
        "--artifact-mode", "evaluation-only",
    ]
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)


def test_provider_call_budgets_resolve_two_call_to_two_per_qid_and_six_total():
    default, by_qid, max_total = _provider_call_budgets(_base_manifest())
    assert default == 2
    assert by_qid == {qid: 2 for qid in QIDS}
    assert max_total == 6


def test_safe_runner_rejects_under_specified_two_call_manifest_before_execute():
    manifest = _base_manifest()
    manifest.pop("stage_call_budgets")
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_safe_runner(manifest, Path(tmp))
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "calculation_contract_invalid" in combined
    assert "stage_call_budgets_required_for_two-call" in combined


def test_safe_runner_accepts_valid_two_call_manifest_without_execute_and_preserves_stage_budgets():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        proc = _run_safe_runner(_base_manifest(), out)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report_path = out / "runner_out" / "manifest_validation_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["validated"] is True
        assert report["calculation_contract"] == "two-call"
        assert report["stage_call_budgets"] == {
            "calculation_formula_extraction": 1,
            "calculation_option_matching": 1,
        }
        policy = report["provider_call_accounting_policy"]
        assert policy["default_per_qid_provider_call_budget"] == 2
        assert policy["per_qid_provider_call_budget_by_qid"] == {qid: 2 for qid in QIDS}
        assert policy["max_provider_call_budget"] == 6
        assert report["execute_requested"] is False


def test_safe_runner_rejects_fallback_or_retry_not_zero():
    manifest = _base_manifest()
    manifest["failure_policy"] = {"fallback_calls": 1, "retry_calls": 0}
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_safe_runner(manifest, Path(tmp))
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "fallback_calls_must_be_0" in combined


if __name__ == "__main__":
    test_provider_call_budgets_resolve_two_call_to_two_per_qid_and_six_total()
    test_safe_runner_rejects_under_specified_two_call_manifest_before_execute()
    test_safe_runner_accepts_valid_two_call_manifest_without_execute_and_preserves_stage_budgets()
    test_safe_runner_rejects_fallback_or_retry_not_zero()
    print("integrate two-call contract into smoke manifest and runner tests: PASS")
