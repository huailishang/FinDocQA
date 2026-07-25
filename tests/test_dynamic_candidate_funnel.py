from __future__ import annotations

import csv
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evaluation.dynamic_candidate_funnel import (
    CandidateGate,
    build_dynamic_summary,
    generate_hold,
    group_candidates,
    validate_csv_diff,
)


def baseline_csv(path: Path) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["qid", "answer", "token"], lineterminator="\n")
        writer.writeheader()
        writer.writerows([
            {"qid": "q1", "answer": "A", "token": "x"},
            {"qid": "q2", "answer": "AB", "token": "y"},
            {"qid": "q3", "answer": "C", "token": "z"},
        ])
    return path


def gate(qid: str, before: str, after: str, *, mechanism: str = "same", domain: str = "financial_reports") -> CandidateGate:
    return CandidateGate(
        qid=qid,
        domain=domain,
        mechanism_group=mechanism,
        baseline_answer=before,
        production_answer=after,
        all_options_closed=True,
        strict_evidence_closed=True,
        direct_support_for_additions=True,
        direct_refutation_for_removals=True,
        source_reproducible=True,
        ablation_passed=True,
        mutation_passed=True,
        risk_rank=10,
    )


def test_injected_answer_change_is_detected_dynamically(tmp_path: Path) -> None:
    gates = [gate("q1", "A", "B"), gate("q2", "AB", "AB")]
    summary = build_dynamic_summary(
        gates=gates,
        coverage={"qids": 2},
        paraphrase_stability={"passed": 1, "count": 2},
        baseline_parity={"matches": 1, "count": 2},
        independent_truth={"status": "local"},
        hold_manifest={"generated": False},
    )
    assert summary["candidate_count"] == 1
    assert summary["relative_baseline_changed_qids"] == ["q1"]
    assert summary["eligible_candidate_count"] == 1


def test_two_same_mechanism_changes_generate_exact_hold(tmp_path: Path) -> None:
    base = baseline_csv(tmp_path / "base.csv")
    gates = [gate("q1", "A", "B"), gate("q2", "AB", "AC"), gate("q3", "C", "C")]
    output = tmp_path / "hold.csv"
    manifest = generate_hold(
        baseline_csv=base,
        gates=gates,
        output_path=output,
        domain="financial_reports",
        allow_single=True,
        write_file=True,
    )
    assert manifest["generated"] is True
    assert manifest["selected_qids"] == ["q1", "q2"]
    assert output.exists()
    assert [(row["qid"], row["before"], row["after"]) for row in manifest["exact_diffs"]] == [
        ("q1", "A", "B"),
        ("q2", "AB", "AC"),
    ]
    groups = group_candidates(gates)
    assert len(groups) == 1
    assert groups[0]["batch_eligible"] is True


def test_zero_change_does_not_generate_csv(tmp_path: Path) -> None:
    base = baseline_csv(tmp_path / "base.csv")
    output = tmp_path / "hold.csv"
    manifest = generate_hold(
        baseline_csv=base,
        gates=[gate("q1", "A", "A"), gate("q2", "AB", "AB")],
        output_path=output,
        domain="financial_reports",
        allow_single=True,
        write_file=True,
    )
    assert manifest["generated"] is False
    assert manifest["selected_qids"] == []
    assert not output.exists()


def test_summary_csv_diff_mismatch_fails() -> None:
    gates = [gate("q1", "A", "B")]
    with pytest.raises(AssertionError, match="summary/CSV exact diff mismatch"):
        validate_csv_diff(
            [{"qid": "q1", "field": "answer", "before": "A", "after": "C"}],
            gates,
        )


def test_reporting_dimensions_are_separate() -> None:
    summary = build_dynamic_summary(
        gates=[gate("q1", "A", "A")],
        coverage={"scope": "fixed", "passed": 20, "count": 20},
        paraphrase_stability={"scope": "rewrite", "passed": 4, "count": 5},
        baseline_parity={"matches": 20, "count": 20},
        independent_truth={"status": "not_established_by_parity"},
        hold_manifest={"generated": False},
    )
    assert summary["fixed_benchmark_coverage"]["scope"] == "fixed"
    assert summary["paraphrase_stability"]["scope"] == "rewrite"
    assert summary["baseline_parity"]["matches"] == 20
    assert summary["independent_truth"]["status"] == "not_established_by_parity"
