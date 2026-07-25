"""Build one multi-slot candidate while preserving cumulative decision-call usage."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.token_accounting import (  # noqa: E402
    SUBMISSION_HEADER,
    LedgerSource,
    TokenAccountingError,
    aggregate_candidate_usage,
    build_candidate_usage_manifest,
    read_multi_slot_submission,
    validate_csv_against_usage,
    validate_manifest_against_usage,
    validate_pipeline_results_against_usage,
    write_json,
)


def _resolve(base: Path, raw: Any, *, field: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise TokenAccountingError(f"candidate spec requires {field}")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_candidate_csv(
    output_path: Path,
    *,
    qid_order: list[str],
    answers_by_qid: Mapping[str, tuple[str, ...]],
    reasoning_by_qid: Mapping[str, str],
    usage: Mapping[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    totals = dict(usage["totals"])
    by_qid = dict(usage["by_qid"])
    rows: list[list[Any]] = [
        list(SUBMISSION_HEADER),
        [
            "summary", "", "", "", "",
            totals["prompt_tokens"], totals["completion_tokens"], totals["total_tokens"], "",
        ],
    ]
    for qid in qid_order:
        answers = list(answers_by_qid[qid])
        if len(answers) != 4:
            raise TokenAccountingError(f"candidate source row must contain four answer columns: {qid}")
        qid_usage = by_qid[qid]
        rows.append([
            qid,
            *answers,
            qid_usage["prompt_tokens"],
            qid_usage["completion_tokens"],
            qid_usage["total_tokens"],
            reasoning_by_qid[qid],
        ])
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    temporary.replace(output_path)


def build_from_spec(spec_path: Path) -> dict[str, Any]:
    spec_path = Path(spec_path).resolve()
    spec = _load_json(spec_path)
    if not isinstance(spec, Mapping):
        raise TokenAccountingError("candidate spec must be a JSON object")
    base = spec_path.parent
    candidate_id = str(spec.get("candidate_id") or "").strip()
    if not candidate_id:
        raise TokenAccountingError("candidate spec requires candidate_id")

    base_submission_path = _resolve(base, spec.get("base_submission"), field="base_submission")
    output_csv = _resolve(base, spec.get("output_csv"), field="output_csv")
    output_manifest = _resolve(base, spec.get("output_manifest"), field="output_manifest")
    base_submission = read_multi_slot_submission(base_submission_path)
    qid_order = list(base_submission["qid_order"])
    answers_by_qid = {
        qid: tuple(base_submission["by_qid"][qid]["answers"])
        for qid in qid_order
    }
    reasoning_by_qid = {
        qid: str(base_submission["by_qid"][qid]["reasoning"])
        for qid in qid_order
    }

    raw_sources = spec.get("ledger_sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise TokenAccountingError("candidate spec requires non-empty ledger_sources")
    sources = [LedgerSource.from_mapping(value, base_dir=base) for value in raw_sources]
    baseline_run_id = str(spec.get("baseline_run_id") or sources[0].run_id).strip()
    selected_by_qid = {qid: baseline_run_id for qid in qid_order}
    explicit_selected = spec.get("selected_answer_source_by_qid") or {}
    if not isinstance(explicit_selected, Mapping):
        raise TokenAccountingError("selected_answer_source_by_qid must be an object")
    selected_by_qid.update({str(qid): str(run_id) for qid, run_id in explicit_selected.items()})

    raw_overrides = spec.get("answer_overrides") or []
    if not isinstance(raw_overrides, list):
        raise TokenAccountingError("answer_overrides must be an array")
    source_csv_cache: dict[Path, dict[str, Any]] = {}
    for override in raw_overrides:
        if not isinstance(override, Mapping):
            raise TokenAccountingError("answer override must be an object")
        qid = str(override.get("qid") or "").strip()
        if qid not in answers_by_qid:
            raise TokenAccountingError(f"answer override qid not in base submission: {qid!r}")
        source_submission_path = _resolve(base, override.get("source_submission"), field="source_submission")
        source_submission = source_csv_cache.setdefault(
            source_submission_path,
            read_multi_slot_submission(source_submission_path),
        )
        if qid not in source_submission["by_qid"]:
            raise TokenAccountingError(f"answer override qid missing from source submission: {qid}")
        answers_by_qid[qid] = tuple(source_submission["by_qid"][qid]["answers"])
        reasoning_by_qid[qid] = str(source_submission["by_qid"][qid]["reasoning"])
        selected_by_qid[qid] = str(override.get("source_run_id") or "").strip()
        if not selected_by_qid[qid]:
            raise TokenAccountingError(f"answer override requires source_run_id: {qid}")

    usage = aggregate_candidate_usage(
        sources,
        candidate_qids=qid_order,
        selected_answer_source_by_qid=selected_by_qid,
        zero_call_qids=tuple(str(qid) for qid in spec.get("zero_call_qids") or ()),
        hard_cap_tokens=int(spec.get("hard_cap_tokens", 5_000_000)),
    )
    _write_candidate_csv(
        output_csv,
        qid_order=qid_order,
        answers_by_qid=answers_by_qid,
        reasoning_by_qid=reasoning_by_qid,
        usage=usage,
    )
    validate_csv_against_usage(output_csv, usage)

    pipeline_results_path = spec.get("pipeline_results")
    if pipeline_results_path:
        pipeline_results = _load_json(_resolve(base, pipeline_results_path, field="pipeline_results"))
        if not isinstance(pipeline_results, list):
            raise TokenAccountingError("pipeline_results artifact must be a JSON array")
        validate_pipeline_results_against_usage(pipeline_results, usage)

    manifest = build_candidate_usage_manifest(
        candidate_id=candidate_id,
        usage=usage,
        sources=sources,
        selected_answer_source_by_qid=selected_by_qid,
        candidate_csv=output_csv,
        relative_to=ROOT,
    )
    manifest["base_submission"] = str(base_submission_path)
    manifest["answer_override_qids"] = sorted(str(value.get("qid")) for value in raw_overrides)
    validate_manifest_against_usage(manifest, usage)
    write_json(output_manifest, manifest)
    return {
        "candidate_id": candidate_id,
        "output_csv": str(output_csv),
        "output_manifest": str(output_manifest),
        "qid_count": len(qid_order),
        "decision_calls": usage["accounted_decision_calls"],
        "total_tokens": usage["totals"]["total_tokens"],
        "unselected_comparison_calls_accounted": usage["unselected_comparison_calls_accounted"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(build_from_spec(args.spec), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
