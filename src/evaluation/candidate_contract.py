"""Answer-contract validation for frozen baselines and candidate CSV files."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from answer_contract import contract_to_dict, validate_answer_against_contract
from contracts import QuestionAnswerContract


def validate_answer_rows(
    rows: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, QuestionAnswerContract],
    *,
    source: str,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    checked = 0
    for index, row in enumerate(rows, start=2):
        qid = str(row.get("qid") or "")
        if not qid or qid == "summary":
            continue
        checked += 1
        contract = contracts.get(qid)
        if contract is None:
            violations.append({
                "row_index": index,
                "qid": qid,
                "answer": str(row.get("answer") or ""),
                "source": source,
                "reason": "unknown_answer_contract",
            })
            continue
        result = validate_answer_against_contract(row.get("answer"), contract)
        if not result.valid:
            violations.append({
                "row_index": index,
                "qid": qid,
                "raw_type": contract.raw_type,
                "answer_format": contract.answer_format,
                "answer": str(row.get("answer") or ""),
                "normalized_answer": result.answer,
                "source": source,
                "reason": result.reason,
            })
    return {
        "answer_contract_schema_version": "question_answer_contract_v1",
        "question_contract_source": "explicit answer_format from raw question JSON",
        "source": source,
        "rows_checked": checked,
        "violations": violations,
        "candidate_contract_pass": not violations,
    }


def build_candidate_rows_from_decisions(
    baseline_rows: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply production replacement decisions to a frozen candidate baseline.

    Only an explicitly accepted replacement may change an answer.  Every other
    decision path keeps the original non-empty baseline answer.
    """
    decision_by_qid: dict[str, Mapping[str, Any]] = {}
    for decision in decisions:
        qid = str(decision.get("qid") or "")
        if not qid:
            raise ValueError("replacement decision is missing qid")
        if qid in decision_by_qid:
            raise ValueError(f"duplicate replacement decision qid: {qid}")
        decision_by_qid[qid] = decision

    candidate_rows: list[dict[str, Any]] = []
    seen_qids: set[str] = set()
    changed_qids: list[str] = []
    fallback_qids: list[str] = []
    for source_row in baseline_rows:
        row = dict(source_row)
        qid = str(row.get("qid") or "")
        if not qid or qid == "summary":
            candidate_rows.append(row)
            continue
        if qid in seen_qids:
            raise ValueError(f"duplicate baseline qid: {qid}")
        seen_qids.add(qid)
        baseline_answer = str(row.get("answer") or "").strip()
        if not baseline_answer:
            raise ValueError(f"blank baseline answer: {qid}")
        decision = decision_by_qid.get(qid)
        if decision is not None:
            accepted = bool(
                decision.get("accepted_for_replacement")
                and decision.get("replacement_allowed")
            )
            effective = str(decision.get("effective_answer") or "").strip()
            if accepted and effective:
                row["answer"] = effective
                if effective != baseline_answer:
                    changed_qids.append(qid)
            else:
                row["answer"] = baseline_answer
                if decision.get("fallback_to_baseline"):
                    fallback_qids.append(qid)
        candidate_rows.append(row)

    unknown_decision_qids = sorted(set(decision_by_qid) - seen_qids)
    if unknown_decision_qids:
        raise ValueError(
            "replacement decisions not present in baseline: "
            + ",".join(unknown_decision_qids)
        )
    return candidate_rows, {
        "business_rows": len(seen_qids),
        "unique_business_qids": len(seen_qids),
        "decision_count": len(decision_by_qid),
        "changed_qids": sorted(changed_qids),
        "fallback_qids": sorted(fallback_qids),
    }


def load_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def validate_csv_path(
    path: Path,
    contracts: Mapping[str, QuestionAnswerContract],
) -> dict[str, Any]:
    _fields, rows = load_csv_rows(path)
    result = validate_answer_rows(rows, contracts, source=str(path))
    result["path"] = str(path)
    return result


def require_valid_answer_rows(
    rows: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, QuestionAnswerContract],
    *,
    source: str,
) -> dict[str, Any]:
    result = validate_answer_rows(rows, contracts, source=source)
    if result["violations"]:
        details = "; ".join(
            f"{row['qid']}={row['answer']!r}:{row['reason']}"
            for row in result["violations"]
        )
        raise ValueError(f"answer contract violation before candidate write: {details}")
    return result
