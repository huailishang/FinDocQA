"""Frozen-baseline and candidate-builder utility (R3).

Freezes a scored submission CSV so it can never be silently overwritten, then
builds candidate submissions by patching specific qid answers on top of the
frozen baseline. Every candidate is validated against the baseline's qid set
and answer formats before it is written.

Core guarantees:

- **Freeze is append-only**: the frozen baseline manifest records score,
  date, SHA256, row_count. The baseline CSV itself is never modified in
  place; the freeze copies it into a protected name and records the hash.
- **Candidate validation**: a candidate built from patches is rejected if it
  introduces duplicate qids, missing qids (relative to the baseline), or
  answers that violate the baseline's answer_format per qid.
- **No overwrite of frozen baseline**: ``build_candidate`` always writes a
  new file; it never touches the frozen CSV.
- **Diff report**: after building a candidate, a markdown diff report lists
  which qids changed and from what to what.

This module is standard-library only and does NOT call any LLM, run the
pipeline, or touch the live retrieval/solver/verifier path.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class FrozenBaseline:
    """Manifest of a frozen, scored submission.

    Attributes:
        name: human-readable name (e.g. ``v3-p6l-xunfei``).
        csv_path: path to the frozen CSV copy.
        score: reported score (e.g. ``54.8``).
        score_date: ISO date string of when the score was recorded.
        sha256: SHA-256 of the frozen CSV content.
        row_count: number of data rows (questions) in the CSV.
        qids: ordered tuple of qids in the CSV.
        answer_format_by_qid: mapping qid -> answer_format (if a format map
            was supplied at freeze time; empty otherwise).
    """

    name: str
    csv_path: str
    score: float
    score_date: str
    sha256: str
    row_count: int
    qids: Tuple[str, ...]
    answer_format_by_qid: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateBuildResult:
    """Result of building a candidate from patches.

    Attributes:
        candidate_path: where the candidate CSV was written.
        baseline_name: name of the frozen baseline it was built from.
        patched_qids: qids whose answers changed.
        unchanged_qids: qids whose answers stayed the same.
        patch_summary: list of ``{qid, old, new}`` dicts for changed rows.
        rejected: True if the candidate was rejected (validation failed).
        rejection_reasons: list of reasons when rejected.
        diff_markdown: markdown diff report.
    """

    candidate_path: str
    baseline_name: str
    patched_qids: Tuple[str, ...]
    unchanged_qids: Tuple[str, ...]
    patch_summary: Tuple[Mapping[str, str], ...]
    rejected: bool
    rejection_reasons: Tuple[str, ...]
    diff_markdown: str


def _read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    """Read a submission CSV into a list of row dicts. Assumes header row."""
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _detect_qid_field(rows: Sequence[Mapping[str, str]]) -> str:
    """Detect which CSV column holds the question id."""
    if not rows:
        return "qid"
    for candidate in ("qid", "question_id", "id", "QID"):
        if candidate in rows[0]:
            return candidate
    # Fallback: first column.
    return list(rows[0].keys())[0]


def _detect_answer_field(rows: Sequence[Mapping[str, str]]) -> str:
    """Detect which CSV column holds the answer."""
    if not rows:
        return "answer"
    for candidate in ("answer", "response", "final_answer", "prediction"):
        if candidate in rows[0]:
            return candidate
    # Fallback: last column.
    keys = list(rows[0].keys())
    return keys[-1] if keys else "answer"


def freeze_baseline(
    submission_csv: Path,
    freeze_dir: Path,
    *,
    name: str,
    score: float,
    score_date: Optional[str] = None,
    answer_format_by_qid: Optional[Mapping[str, str]] = None,
) -> FrozenBaseline:
    """Freeze a scored submission CSV so it can never be silently overwritten.

    Copies the CSV into ``freeze_dir/<name>.csv`` and writes a manifest
    ``<name>.manifest.json`` recording score, date, SHA256, row_count, qids.

    Args:
        submission_csv: the CSV to freeze.
        freeze_dir: directory for frozen artifacts (created if needed).
        name: frozen baseline name (becomes the filename stem).
        score: reported leaderboard/eval score.
        score_date: ISO date string; defaults to today.
        answer_format_by_qid: optional mapping qid -> answer_format
            (``mcq``/``multi``/``tf``) for later candidate validation.

    Returns:
        FrozenBaseline manifest.
    """
    submission_csv = Path(submission_csv)
    freeze_dir = Path(freeze_dir)
    freeze_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_csv_rows(submission_csv)
    qid_field = _detect_qid_field(rows)
    answer_field = _detect_answer_field(rows)
    qids = tuple(str(r[qid_field]).strip() for r in rows)

    frozen_csv = freeze_dir / f"{name}.csv"
    # Copy verbatim (preserves exact bytes for SHA reproducibility).
    frozen_csv.write_bytes(submission_csv.read_bytes())
    sha = _sha256_file(frozen_csv)

    fmt_map = dict(answer_format_by_qid or {})
    manifest = FrozenBaseline(
        name=name,
        csv_path=str(frozen_csv),
        score=float(score),
        score_date=score_date or date.today().isoformat(),
        sha256=sha,
        row_count=len(rows),
        qids=qids,
        answer_format_by_qid=fmt_map,
    )

    manifest_path = freeze_dir / f"{name}.manifest.json"
    manifest_data = asdict(manifest)
    manifest_data["answer_format_by_qid"] = fmt_map  # ensure plain dict
    manifest_path.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_frozen_baseline(freeze_dir: Path, name: str) -> FrozenBaseline:
    """Load a previously frozen baseline manifest."""
    manifest_path = Path(freeze_dir) / f"{name}.manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return FrozenBaseline(
        name=data["name"],
        csv_path=data["csv_path"],
        score=data["score"],
        score_date=data["score_date"],
        sha256=data["sha256"],
        row_count=data["row_count"],
        qids=tuple(data["qids"]),
        answer_format_by_qid=dict(data.get("answer_format_by_qid", {})),
    )


def _validate_answer_format(answer: str, fmt: str) -> bool:
    """Validate an answer against its answer_format.

    - ``mcq``: single uppercase letter A-D.
    - ``multi``: one or more unique uppercase letters, sorted (e.g. ``AB``,
      ``ACD``). We accept any order but canonicalize in the candidate.
    - ``tf``: ``T`` or ``F``.
    - unknown/empty format: accept any non-empty string.
    """
    if not answer or not answer.strip():
        return False
    a = answer.strip().upper()
    if fmt == "mcq":
        return a in {"A", "B", "C", "D"}
    if fmt == "multi":
        if not a:
            return False
        letters = set(a)
        return all(c in "ABCD" for c in letters) and len(letters) == len(a)
    if fmt == "tf":
        return a in {"T", "F"}
    return True


def _canonicalize_answer(answer: str, fmt: str) -> str:
    """Canonicalize an answer to its stable form (sorted unique for multi)."""
    a = answer.strip().upper()
    if fmt == "multi":
        return "".join(sorted(set(a)))
    return a


def build_candidate(
    baseline: FrozenBaseline,
    patches: Mapping[str, str],
    output_csv: Path,
) -> CandidateBuildResult:
    """Build a candidate submission by patching answers on top of a frozen baseline.

    The frozen baseline CSV is never modified. A new candidate CSV is written
    to ``output_csv`` with the patched answers.

    Validation (rejects if any fail):
    - no duplicate qids in ``patches``;
    - no qid in ``patches`` that is absent from the baseline;
    - every patched answer is valid for the baseline's answer_format (when
      the format map is available);
    - the candidate's qid set must exactly match the baseline's qid set.

    Args:
        baseline: a loaded FrozenBaseline.
        patches: mapping qid -> new answer.
        output_csv: where to write the candidate CSV.

    Returns:
        CandidateBuildResult with the diff and rejection status.
    """
    baseline_csv = Path(baseline.csv_path)
    rows = _read_csv_rows(baseline_csv)
    qid_field = _detect_qid_field(rows)
    answer_field = _detect_answer_field(rows)

    baseline_qids = set(baseline.qids)
    patch_qids = set(patches.keys())
    reasons: List[str] = []

    # Duplicate qids in patches dict are impossible (dict keys), but a patch
    # qid not in baseline is a rejection.
    extra = patch_qids - baseline_qids
    if extra:
        reasons.append(f"patches reference qids not in baseline: {sorted(extra)}")

    # Validate answer formats when available.
    fmt_map = baseline.answer_format_by_qid
    for qid, ans in patches.items():
        if qid not in baseline_qids:
            continue  # already reported above
        if fmt_map:
            fmt = fmt_map.get(qid, "")
            if fmt and not _validate_answer_format(ans, fmt):
                reasons.append(f"qid={qid}: answer '{ans}' invalid for format '{fmt}'")
        elif not ans or not ans.strip():
            reasons.append(f"qid={qid}: empty answer")

    if reasons:
        return CandidateBuildResult(
            candidate_path="",
            baseline_name=baseline.name,
            patched_qids=(),
            unchanged_qids=tuple(sorted(baseline_qids)),
            patch_summary=(),
            rejected=True,
            rejection_reasons=tuple(reasons),
            diff_markdown="",
        )

    # Apply patches.
    patched_rows: List[Dict[str, str]] = []
    patch_summary: List[Mapping[str, str]] = []
    patched_qids: List[str] = []
    unchanged_qids: List[str] = []

    for row in rows:
        qid = str(row[qid_field]).strip()
        old_ans = str(row[answer_field]).strip()
        if qid in patches:
            fmt = fmt_map.get(qid, "")
            new_ans = _canonicalize_answer(patches[qid], fmt) if fmt else patches[qid].strip()
            new_row = dict(row)
            new_row[answer_field] = new_ans
            patched_rows.append(new_row)
            if new_ans != old_ans:
                patched_qids.append(qid)
                patch_summary.append({"qid": qid, "old": old_ans, "new": new_ans})
            else:
                unchanged_qids.append(qid)
        else:
            patched_rows.append(dict(row))
            unchanged_qids.append(qid)

    # Final qid-set check: candidate must have exactly the baseline qids.
    candidate_qids = set(str(r[qid_field]).strip() for r in patched_rows)
    if candidate_qids != baseline_qids:
        missing = baseline_qids - candidate_qids
        extra_c = candidate_qids - baseline_qids
        if missing:
            reasons.append(f"candidate is missing qids: {sorted(missing)}")
        if extra_c:
            reasons.append(f"candidate has extra qids: {sorted(extra_c)}")
        return CandidateBuildResult(
            candidate_path="",
            baseline_name=baseline.name,
            patched_qids=(),
            unchanged_qids=tuple(sorted(baseline_qids)),
            patch_summary=(),
            rejected=True,
            rejection_reasons=tuple(reasons),
            diff_markdown="",
        )

    # Write candidate CSV (never overwrite the frozen baseline).
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else [qid_field, answer_field]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(patched_rows)

    # Build diff markdown.
    md_lines: List[str] = [
        f"# Candidate Diff Report — {baseline.name}",
        "",
        f"- candidate: `{output_csv.name}`",
        f"- baseline score: {baseline.score}",
        f"- baseline date: {baseline.score_date}",
        f"- baseline sha256: `{baseline.sha256[:16]}…`",
        f"- patched (changed): {len(patched_qids)}",
        f"- unchanged: {len(unchanged_qids)}",
        "",
        "## Changed answers",
        "",
        "| qid | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for p in patch_summary:
        md_lines.append(f"| {p['qid']} | {p['old']} | {p['new']} |")
    if not patch_summary:
        md_lines.append("| _none_ | — | — |")
    md_text = "\n".join(md_lines) + "\n"

    return CandidateBuildResult(
        candidate_path=str(output_csv),
        baseline_name=baseline.name,
        patched_qids=tuple(patched_qids),
        unchanged_qids=tuple(unchanged_qids),
        patch_summary=tuple(patch_summary),
        rejected=False,
        rejection_reasons=(),
        diff_markdown=md_text,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point supporting both freeze and candidate-build modes."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Freeze a scored submission and build candidates from patches."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    freeze_p = sub.add_parser("freeze", help="Freeze a scored submission CSV.")
    freeze_p.add_argument("submission_csv", help="Path to the submission CSV to freeze.")
    freeze_p.add_argument("freeze_dir", help="Directory for frozen artifacts.")
    freeze_p.add_argument("--name", required=True, help="Frozen baseline name.")
    freeze_p.add_argument("--score", type=float, required=True, help="Reported score.")
    freeze_p.add_argument("--date", default=None, help="Score date (ISO). Defaults to today.")
    freeze_p.add_argument("--formats-json", default=None, help="JSON mapping qid->answer_format.")

    cand_p = sub.add_parser("candidate", help="Build a candidate from patches on a frozen baseline.")
    cand_p.add_argument("freeze_dir", help="Directory containing frozen artifacts.")
    cand_p.add_argument("--baseline-name", required=True, help="Frozen baseline name.")
    cand_p.add_argument("--patches-json", required=True, help='JSON mapping qid->new answer, e.g. \'{"case_001":"B"}\'.')
    cand_p.add_argument("--output", required=True, help="Output candidate CSV path.")

    args = parser.parse_args(argv)

    if args.command == "freeze":
        fmt_map = None
        if args.formats_json:
            fmt_map = json.loads(Path(args.formats_json).read_text(encoding="utf-8"))
        fb = freeze_baseline(
            Path(args.submission_csv), Path(args.freeze_dir),
            name=args.name, score=args.score,
            score_date=args.date, answer_format_by_qid=fmt_map,
        )
        print(f"frozen: {fb.name}")
        print(f"  csv: {fb.csv_path}")
        print(f"  score: {fb.score}")
        print(f"  sha256: {fb.sha256}")
        print(f"  rows: {fb.row_count}")
        print(f"  qids: {len(fb.qids)}")
        return 0

    if args.command == "candidate":
        baseline = load_frozen_baseline(Path(args.freeze_dir), args.baseline_name)
        patches = json.loads(args.patches_json)
        result = build_candidate(baseline, patches, Path(args.output))
        if result.rejected:
            print("REJECTED:")
            for r in result.rejection_reasons:
                print(f"  - {r}")
            return 1
        print(f"candidate: {result.candidate_path}")
        print(f"  patched: {len(result.patched_qids)}")
        print(f"  unchanged: {len(result.unchanged_qids)}")
        print(result.diff_markdown)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
