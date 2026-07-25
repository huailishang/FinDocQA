"""Dynamic, baseline-relative candidate and HOLD-file generation.

The module is intentionally domain agnostic.  It never infers correctness from
baseline parity or from a model answer.  Callers must supply independently
computed production answers and explicit gate results for every changed QID.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import csv
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence


LABEL_ORDER = "ABCD"


def canonical_answer(value: Any) -> str:
    labels = set(str(value or "").upper())
    return "".join(label for label in LABEL_ORDER if label in labels)


def answer_change_type(before: str, after: str) -> str:
    before_set, after_set = set(canonical_answer(before)), set(canonical_answer(after))
    if before_set == after_set:
        return "MATCH"
    if before_set < after_set:
        return "ADDITION"
    if after_set < before_set:
        return "REMOVAL"
    return "REPLACEMENT"


@dataclass(frozen=True)
class CandidateGate:
    qid: str
    domain: str
    mechanism_group: str
    baseline_answer: str
    production_answer: str
    all_options_closed: bool
    strict_evidence_closed: bool
    direct_support_for_additions: bool
    direct_refutation_for_removals: bool
    source_reproducible: bool
    ablation_passed: bool
    mutation_passed: bool
    historical_negative: bool = False
    independent_truth_status: str = "local_evidence_closure"
    risk_rank: int = 100

    @property
    def change_type(self) -> str:
        return answer_change_type(self.baseline_answer, self.production_answer)

    @property
    def changed(self) -> bool:
        return self.change_type != "MATCH"

    @property
    def eligible(self) -> bool:
        return bool(
            self.changed
            and self.all_options_closed
            and self.strict_evidence_closed
            and self.direct_support_for_additions
            and self.direct_refutation_for_removals
            and self.source_reproducible
            and self.ablation_passed
            and self.mutation_passed
            and not self.historical_negative
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update({
            "change_type": self.change_type,
            "changed": self.changed,
            "eligible": self.eligible,
            "slot2_eligible": self.eligible,
        })
        return payload


def read_baseline_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if not fields or "qid" not in fields or "answer" not in fields:
        raise ValueError("baseline CSV must contain qid and answer columns")
    return fields, rows


def answer_map(rows: Sequence[Mapping[str, str]]) -> dict[str, str]:
    return {
        str(row.get("qid") or ""): canonical_answer(row.get("answer"))
        for row in rows
        if row.get("qid") and str(row.get("qid")).lower() != "summary"
    }


def exact_csv_diff(
    baseline_rows: Sequence[Mapping[str, str]],
    candidate_rows: Sequence[Mapping[str, str]],
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    if len(baseline_rows) != len(candidate_rows):
        raise AssertionError("candidate CSV row count differs from baseline")
    diffs: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(zip(baseline_rows, candidate_rows), 1):
        if str(before.get("qid") or "") != str(after.get("qid") or ""):
            raise AssertionError(f"candidate CSV QID order differs at row {index}")
        for field in fields:
            left = str(before.get(field) or "")
            right = str(after.get(field) or "")
            if left != right:
                diffs.append({
                    "row": index,
                    "qid": str(before.get("qid") or ""),
                    "field": field,
                    "before": left,
                    "after": right,
                })
    return diffs


def build_candidate_rows(
    baseline_rows: Sequence[Mapping[str, str]],
    replacements: Mapping[str, str],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for source in baseline_rows:
        row = dict(source)
        qid = str(row.get("qid") or "")
        if qid in replacements:
            row["answer"] = canonical_answer(replacements[qid])
        output.append(row)
    return output


def write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, str]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return sha256(path.read_bytes()).hexdigest()


def _expected_answer_diffs(selected: Sequence[CandidateGate]) -> list[dict[str, str]]:
    return sorted(
        ({"qid": row.qid, "field": "answer", "before": canonical_answer(row.baseline_answer), "after": canonical_answer(row.production_answer)} for row in selected),
        key=lambda row: row["qid"],
    )


def validate_csv_diff(
    actual_diffs: Sequence[Mapping[str, Any]],
    selected: Sequence[CandidateGate],
) -> None:
    normalized = sorted(
        ({
            "qid": str(row.get("qid") or ""),
            "field": str(row.get("field") or ""),
            "before": canonical_answer(row.get("before")) if row.get("field") == "answer" else str(row.get("before") or ""),
            "after": canonical_answer(row.get("after")) if row.get("field") == "answer" else str(row.get("after") or ""),
        } for row in actual_diffs),
        key=lambda row: (row["qid"], row["field"]),
    )
    expected = _expected_answer_diffs(selected)
    if normalized != expected:
        raise AssertionError(f"summary/CSV exact diff mismatch: expected={expected}, actual={normalized}")


def group_candidates(gates: Sequence[CandidateGate]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[CandidateGate]] = {}
    for gate in gates:
        if not gate.eligible:
            continue
        key = (gate.domain, gate.mechanism_group, gate.change_type)
        groups.setdefault(key, []).append(gate)
    output: list[dict[str, Any]] = []
    for (domain, mechanism, change_type), rows in sorted(groups.items()):
        ranked = sorted(rows, key=lambda row: (row.risk_rank, row.qid))
        output.append({
            "domain": domain,
            "mechanism_group": mechanism,
            "change_type": change_type,
            "qids": [row.qid for row in ranked],
            "count": len(ranked),
            "batch_eligible": 2 <= len(ranked) <= 4,
            "single_eligible": len(ranked) == 1,
            "risk_order": [{"qid": row.qid, "risk_rank": row.risk_rank} for row in ranked],
        })
    return output


def select_hold_group(
    gates: Sequence[CandidateGate],
    *,
    domain: str | None = None,
    allow_single: bool = True,
) -> list[CandidateGate]:
    groups = group_candidates(gates)
    eligible = [row for row in groups if (domain is None or row["domain"] == domain) and row["batch_eligible"]]
    if eligible:
        chosen = min(eligible, key=lambda row: (min(item["risk_rank"] for item in row["risk_order"]), row["domain"], row["mechanism_group"], row["change_type"]))
        qids = set(chosen["qids"][:4])
        return sorted((gate for gate in gates if gate.qid in qids and gate.eligible), key=lambda row: (row.risk_rank, row.qid))
    if allow_single:
        singles = [gate for gate in gates if gate.eligible and (domain is None or gate.domain == domain)]
        if singles:
            return [min(singles, key=lambda row: (row.risk_rank, row.qid))]
    return []


def generate_hold(
    *,
    baseline_csv: Path,
    gates: Sequence[CandidateGate],
    output_path: Path | None,
    domain: str | None = None,
    allow_single: bool = True,
    write_file: bool = True,
) -> dict[str, Any]:
    fields, baseline_rows = read_baseline_rows(baseline_csv)
    selected = select_hold_group(gates, domain=domain, allow_single=allow_single)
    manifest: dict[str, Any] = {
        "generated": False,
        "path": None,
        "sha256": None,
        "selected_qids": [row.qid for row in selected],
        "domain": domain,
        "reason": "NO_ELIGIBLE_CANDIDATE" if not selected else "ELIGIBLE_GROUP_SELECTED",
        "exact_diffs": [],
    }
    if not selected:
        return manifest
    replacements = {row.qid: row.production_answer for row in selected}
    candidate_rows = build_candidate_rows(baseline_rows, replacements)
    diffs = exact_csv_diff(baseline_rows, candidate_rows, fields)
    validate_csv_diff(diffs, selected)
    manifest["exact_diffs"] = diffs
    if write_file:
        if output_path is None:
            raise ValueError("output_path is required when write_file=True")
        manifest["sha256"] = write_csv(output_path, fields, candidate_rows)
        manifest["path"] = str(output_path)
        manifest["generated"] = True
    else:
        manifest["generated"] = True
        manifest["path"] = str(output_path) if output_path else None
    return manifest


def build_dynamic_summary(
    *,
    gates: Sequence[CandidateGate],
    coverage: Mapping[str, Any],
    paraphrase_stability: Mapping[str, Any],
    baseline_parity: Mapping[str, Any],
    independent_truth: Mapping[str, Any],
    hold_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    changed = sorted(gate.qid for gate in gates if gate.changed)
    eligible = sorted(gate.qid for gate in gates if gate.eligible)
    return {
        "fixed_benchmark_coverage": dict(coverage),
        "paraphrase_stability": dict(paraphrase_stability),
        "baseline_parity": dict(baseline_parity),
        "independent_truth": dict(independent_truth),
        "candidate_count": len(changed),
        "eligible_candidate_count": len(eligible),
        "relative_baseline_changed_qids": changed,
        "eligible_qids": eligible,
        "same_mechanism_groups": group_candidates(gates),
        "hold_generated": bool(hold_manifest.get("generated")),
        "hold_path": hold_manifest.get("path"),
        "hold_exact_diffs": list(hold_manifest.get("exact_diffs") or []),
    }
