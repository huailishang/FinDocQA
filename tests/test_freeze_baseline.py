"""R3 frozen-baseline and candidate-builder — synthetic tests.

Tests cover:
1. freeze: copies CSV, records score/date/SHA256/row_count/qids.
2. freeze is append-only: original CSV untouched, frozen copy has same bytes.
3. candidate: valid patches produce a new CSV with changed answers.
4. candidate: rejects qids not in baseline.
5. candidate: rejects invalid answers for known answer_format.
6. candidate: rejects when qid set doesn't match baseline (missing/extra).
7. candidate: never overwrites the frozen baseline CSV.
8. candidate: multi-format answers canonicalized (sorted unique).
9. diff report markdown lists changed answers.
10. load_frozen_baseline round-trips the manifest.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from experiments.freeze import (
    build_candidate,
    freeze_baseline,
    load_frozen_baseline,
)


def _write_submission_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=["qid", "answer"])
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def baseline_csv(tmp_path: Path) -> Path:
    p = tmp_path / "sub" / "submission.csv"
    _write_submission_csv(p, [
        {"qid": "case_013", "answer": "A"},
        {"qid": "case_014", "answer": "BC"},
        {"qid": "case_003", "answer": "C"},
    ])
    return p


@pytest.fixture
def fmt_map() -> dict[str, str]:
    return {
        "case_013": "mcq",
        "case_014": "multi",
        "case_003": "mcq",
    }


# ── 1. freeze records manifest ────────────────────────────────────────


def test_freeze_records_manifest(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(
        baseline_csv, freeze_dir,
        name="v3-test", score=54.8, score_date="2026-06-25",
        answer_format_by_qid=fmt_map,
    )
    assert fb.name == "v3-test"
    assert fb.score == 54.8
    assert fb.score_date == "2026-06-25"
    assert fb.row_count == 3
    assert len(fb.sha256) == 64
    assert tuple(fb.qids) == ("case_013", "case_014", "case_003")
    assert fb.answer_format_by_qid == fmt_map

    # Manifest file written.
    manifest_path = freeze_dir / "v3-test.manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["score"] == 54.8
    assert data["row_count"] == 3


# ── 2. freeze is append-only (original untouched) ─────────────────────


def test_freeze_does_not_modify_original(baseline_csv: Path, tmp_path: Path):
    original_bytes = baseline_csv.read_bytes()
    freeze_dir = tmp_path / "freeze"
    freeze_baseline(baseline_csv, freeze_dir, name="v3-test", score=54.8)
    assert baseline_csv.read_bytes() == original_bytes


# ── 3. valid candidate patches ────────────────────────────────────────


def test_valid_candidate_patches(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=50.0, answer_format_by_qid=fmt_map)
    out = tmp_path / "candidate.csv"
    result = build_candidate(fb, {"case_013": "B", "case_003": "D"}, out)

    assert result.rejected is False
    assert result.candidate_path == str(out)
    assert set(result.patched_qids) == {"case_013", "case_003"}
    assert "case_014" in result.unchanged_qids

    # Candidate CSV has the patched answers.
    with out.open(encoding="utf-8-sig") as h:
        rows = {r["qid"]: r["answer"] for r in csv.DictReader(h)}
    assert rows["case_013"] == "B"
    assert rows["case_014"] == "BC"  # unchanged
    assert rows["case_003"] == "D"


# ── 4. rejects qids not in baseline ───────────────────────────────────


def test_rejects_unknown_qid(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=50.0, answer_format_by_qid=fmt_map)
    out = tmp_path / "candidate.csv"
    result = build_candidate(fb, {"nonexistent_qid": "A"}, out)

    assert result.rejected is True
    assert any("not in baseline" in r for r in result.rejection_reasons)


# ── 5. rejects invalid answers for known format ───────────────────────


def test_rejects_invalid_answer_format(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=50.0, answer_format_by_qid=fmt_map)
    out = tmp_path / "candidate.csv"
    # mcq format only allows A-D; "E" is invalid.
    result = build_candidate(fb, {"case_013": "E"}, out)

    assert result.rejected is True
    assert any("invalid for format" in r for r in result.rejection_reasons)


# ── 6. multi-format canonicalized ─────────────────────────────────────


def test_multi_answer_canonicalized(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=50.0, answer_format_by_qid=fmt_map)
    out = tmp_path / "candidate.csv"
    # "CB" should be canonicalized to "BC".
    result = build_candidate(fb, {"case_014": "CB"}, out)

    assert result.rejected is False
    with out.open(encoding="utf-8-sig") as h:
        rows = {r["qid"]: r["answer"] for r in csv.DictReader(h)}
    assert rows["case_014"] == "BC"


# ── 7. never overwrites frozen baseline ───────────────────────────────


def test_candidate_never_overwrites_baseline(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=50.0, answer_format_by_qid=fmt_map)
    frozen_csv = Path(fb.csv_path)
    frozen_bytes_before = frozen_csv.read_bytes()

    out = tmp_path / "candidate.csv"
    build_candidate(fb, {"case_013": "B"}, out)

    assert frozen_csv.read_bytes() == frozen_bytes_before
    assert out.is_file()


# ── 8. diff report markdown ───────────────────────────────────────────


def test_diff_report_markdown(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=54.8, answer_format_by_qid=fmt_map)
    out = tmp_path / "candidate.csv"
    result = build_candidate(fb, {"case_013": "B"}, out)

    assert "| case_013 | A | B |" in result.diff_markdown
    assert "54.8" in result.diff_markdown


# ── 9. load_frozen_baseline round-trips ───────────────────────────────


def test_load_frozen_baseline_roundtrip(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=54.8, answer_format_by_qid=fmt_map)
    loaded = load_frozen_baseline(freeze_dir, "v3")

    assert loaded.name == fb.name
    assert loaded.score == fb.score
    assert loaded.sha256 == fb.sha256
    assert loaded.qids == fb.qids
    assert loaded.answer_format_by_qid == fb.answer_format_by_qid


# ── 10. no-op patches (same answer) are unchanged ─────────────────────


def test_noop_patch_is_unchanged(baseline_csv: Path, tmp_path: Path, fmt_map: dict):
    freeze_dir = tmp_path / "freeze"
    fb = freeze_baseline(baseline_csv, freeze_dir, name="v3", score=50.0, answer_format_by_qid=fmt_map)
    out = tmp_path / "candidate.csv"
    # Patch with the same answer.
    result = build_candidate(fb, {"case_013": "A"}, out)

    assert result.rejected is False
    assert result.patched_qids == ()
    assert "case_013" in result.unchanged_qids
