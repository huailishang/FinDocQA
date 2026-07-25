"""Hybrid multi-slot token accounting and paid-run isolation contracts."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evaluation.formal_submission import FORMAL_SUBMISSION_HEADER, validate_reasoning_contract

SUBMISSION_HEADER = FORMAL_SUBMISSION_HEADER
DEFAULT_CANDIDATE_TOKEN_HARD_CAP = 5_000_000
TERMINAL_PROVIDER_STATUSES = frozenset({"COMPLETED", "ERROR", "TIMEOUT"})
BLOCKING_LEDGER_STATUSES = frozenset({"PRE_CALL_BLOCKED", "PRE_CALL_BLOCKED_ACKNOWLEDGED"})
DECISION_PURPOSES = frozenset({
    "initial_answer", "format_repair", "reasoning_repair", "reasoning_summary",
    "verification", "retrieval_rerun", "max_preview_adjudication",
    "other_declared_decision_call",
})


class TokenAccountingError(ValueError):
    """Fail-closed accounting or isolation violation."""


@dataclass(frozen=True)
class TokenTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    provider_calls: int = 0

    def plus(self, other: "TokenTotals") -> "TokenTotals":
        return TokenTotals(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
            self.provider_calls + other.provider_calls,
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class LedgerSource:
    """One isolated provider ledger participating in a hybrid candidate."""

    run_id: str
    purpose: str
    ledger_path: Path
    allowed_qids: tuple[str, ...]
    model: str = ""
    output_dir: Path | None = None
    usage_file: Path | None = None
    resolved_runtime_config_path: Path | None = None
    candidate_role: str = "decision"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, base_dir: Path | None = None) -> "LedgerSource":
        base = Path(base_dir or Path.cwd()).resolve()

        def _path(raw: Any) -> Path | None:
            if raw in (None, ""):
                return None
            path = Path(str(raw))
            return path.resolve() if path.is_absolute() else (base / path).resolve()

        raw_qids = value.get("allowed_qids")
        if not isinstance(raw_qids, Sequence) or isinstance(raw_qids, (str, bytes)):
            raise TokenAccountingError("ledger source allowed_qids must be an array")
        ledger_path = _path(value.get("ledger_path"))
        if ledger_path is None:
            raise TokenAccountingError("ledger source requires ledger_path")
        return cls(
            run_id=str(value.get("run_id") or "").strip(),
            purpose=str(value.get("purpose") or "").strip(),
            ledger_path=ledger_path,
            allowed_qids=tuple(str(qid).strip() for qid in raw_qids if str(qid).strip()),
            model=str(value.get("model") or "").strip(),
            output_dir=_path(value.get("output_dir")),
            usage_file=_path(value.get("usage_file")),
            resolved_runtime_config_path=_path(value.get("resolved_runtime_config_path")),
            candidate_role=str(value.get("candidate_role") or "decision").strip(),
        )

    def as_manifest_dict(self, *, relative_to: Path | None = None) -> dict[str, Any]:
        base = Path(relative_to).resolve() if relative_to is not None else None

        def _display(path: Path | None) -> str:
            if path is None:
                return ""
            resolved = path.resolve()
            if base is not None:
                try:
                    return resolved.relative_to(base).as_posix()
                except ValueError:
                    pass
            return str(resolved)

        return {
            "run_id": self.run_id,
            "purpose": self.purpose,
            "ledger_path": _display(self.ledger_path),
            "allowed_qids": list(self.allowed_qids),
            "model": self.model,
            "output_dir": _display(self.output_dir),
            "usage_file": _display(self.usage_file),
            "resolved_runtime_config_path": _display(self.resolved_runtime_config_path),
            "candidate_role": self.candidate_role,
        }


def _strict_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TokenAccountingError(f"{field} must be a non-negative integer; got {value!r}")
    if value < 0:
        raise TokenAccountingError(f"{field} must be a non-negative integer; got {value!r}")
    return value


def _strict_csv_int(value: Any, *, field: str) -> int:
    raw = str(value).strip()
    if not raw or not raw.isdigit():
        raise TokenAccountingError(f"{field} must be a non-negative integer string; got {value!r}")
    return int(raw)


def validate_token_equation(prompt_tokens: Any, completion_tokens: Any, total_tokens: Any, *, prefix: str) -> TokenTotals:
    prompt = _strict_nonnegative_int(prompt_tokens, field=f"{prefix}.prompt_tokens")
    completion = _strict_nonnegative_int(completion_tokens, field=f"{prefix}.completion_tokens")
    total = _strict_nonnegative_int(total_tokens, field=f"{prefix}.total_tokens")
    if prompt + completion != total:
        raise TokenAccountingError(f"{prefix} token equation mismatch: {prompt} + {completion} != {total}")
    return TokenTotals(prompt, completion, total, 1)


def enforce_candidate_token_hard_cap(total_tokens: int, *, hard_cap_tokens: int = DEFAULT_CANDIDATE_TOKEN_HARD_CAP) -> None:
    total = _strict_nonnegative_int(total_tokens, field="candidate.total_tokens")
    cap = _strict_nonnegative_int(hard_cap_tokens, field="candidate.hard_cap_tokens")
    if cap <= 0:
        raise TokenAccountingError("candidate hard cap must be positive")
    if total >= cap:
        raise TokenAccountingError(
            f"candidate token hard cap reached: total={total} cap={cap}; policy=block_at_or_above"
        )


def _normalize_source(source: LedgerSource) -> LedgerSource:
    run_id = source.run_id.strip()
    purpose = source.purpose.strip()
    if not run_id:
        raise TokenAccountingError("ledger source run_id must be non-empty")
    if purpose not in DECISION_PURPOSES:
        raise TokenAccountingError(f"unsupported decision purpose for {run_id}: {purpose!r}")
    qids = tuple(source.allowed_qids)
    if not qids or len(qids) != len(set(qids)):
        raise TokenAccountingError(f"ledger source {run_id} requires unique non-empty allowed_qids")
    return LedgerSource(
        run_id=run_id,
        purpose=purpose,
        ledger_path=source.ledger_path.resolve(),
        allowed_qids=qids,
        model=source.model.strip(),
        output_dir=source.output_dir.resolve() if source.output_dir is not None else None,
        usage_file=source.usage_file.resolve() if source.usage_file is not None else None,
        resolved_runtime_config_path=(source.resolved_runtime_config_path.resolve() if source.resolved_runtime_config_path is not None else None),
        candidate_role=source.candidate_role.strip() or "decision",
    )


def validate_ledger_isolation(sources: Sequence[LedgerSource]) -> dict[str, Any]:
    normalized = [_normalize_source(source) for source in sources]
    if not normalized:
        raise TokenAccountingError("at least one ledger source is required")
    run_ids = [source.run_id for source in normalized]
    if len(run_ids) != len(set(run_ids)):
        raise TokenAccountingError("ledger sources must use unique run_id values")

    def _unique_paths(label: str, paths: Iterable[Path | None]) -> list[str]:
        values = [str(path.resolve()) for path in paths if path is not None]
        if len(values) != len(set(values)):
            raise TokenAccountingError(f"ledger sources must use isolated {label} paths")
        return values

    ledger_paths = _unique_paths("ledger", (source.ledger_path for source in normalized))
    output_dirs = _unique_paths("output_dir", (source.output_dir for source in normalized))
    usage_files = _unique_paths("usage_file", (source.usage_file for source in normalized))
    resolved_configs = _unique_paths("resolved_runtime_config", (source.resolved_runtime_config_path for source in normalized))
    for source in normalized:
        if source.output_dir is None:
            continue
        output_dir = source.output_dir.resolve()
        for label, path in (
            ("ledger_path", source.ledger_path),
            ("usage_file", source.usage_file),
            ("resolved_runtime_config_path", source.resolved_runtime_config_path),
        ):
            if path is None:
                continue
            try:
                path.resolve().relative_to(output_dir)
            except ValueError as exc:
                raise TokenAccountingError(f"{source.run_id} {label} must be inside its isolated output_dir") from exc
    return {
        "run_ids": run_ids,
        "ledger_paths": ledger_paths,
        "output_dirs": output_dirs,
        "usage_files": usage_files,
        "resolved_runtime_configs": resolved_configs,
        "isolated": True,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise TokenAccountingError(f"token ledger does not exist: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise TokenAccountingError(f"invalid JSONL row {path}:{line_number}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise TokenAccountingError(f"ledger row must be an object: {path}:{line_number}")
        row = dict(value)
        row["_line_number"] = line_number
        rows.append(row)
    return rows


def load_ledger_rows(source: LedgerSource) -> list[dict[str, Any]]:
    source = _normalize_source(source)
    allowed = set(source.allowed_qids)
    normalized: list[dict[str, Any]] = []
    seen_attempt_ids: set[str] = set()
    for raw in _read_jsonl(source.ledger_path):
        line = int(raw.pop("_line_number"))
        attempt_id = str(raw.get("attempt_id") or "").strip()
        if not attempt_id:
            raise TokenAccountingError(f"missing attempt_id in {source.ledger_path}:{line}")
        if attempt_id in seen_attempt_ids:
            raise TokenAccountingError(f"duplicate ledger attempt_id in run {source.run_id}: {attempt_id}")
        seen_attempt_ids.add(attempt_id)
        raw_run_id = str(raw.get("run_id") or "").strip()
        if raw_run_id and raw_run_id != source.run_id:
            raise TokenAccountingError(
                f"ledger run_id mismatch at {source.ledger_path}:{line}: {raw_run_id!r} != {source.run_id!r}"
            )
        qid = str(raw.get("qid") or "").strip()
        if not qid:
            raise TokenAccountingError(f"missing qid in {source.ledger_path}:{line}")
        if qid not in allowed:
            raise TokenAccountingError(f"qid outside allowed_qids in run {source.run_id}: {qid}")
        status = str(raw.get("final_status") or raw.get("status") or "").upper().strip()
        if status in BLOCKING_LEDGER_STATUSES:
            raise TokenAccountingError(
                f"pre-call blocked row makes ledger non-candidate-ready: {source.run_id}/{qid}/{attempt_id}"
            )
        if status not in TERMINAL_PROVIDER_STATUSES:
            raise TokenAccountingError(
                f"non-terminal provider row in candidate ledger: {source.run_id}/{qid}/{attempt_id} status={status!r}"
            )
        raw_purpose = str(raw.get("purpose") or "").strip()
        purpose = raw_purpose or source.purpose
        if purpose not in DECISION_PURPOSES:
            raise TokenAccountingError(f"unsupported decision purpose in {source.run_id}/{attempt_id}: {purpose!r}")
        model = str(raw.get("model") or source.model or "").strip()
        if not model:
            raise TokenAccountingError(f"missing model in {source.run_id}/{attempt_id}")
        if source.model and model != source.model:
            raise TokenAccountingError(
                f"model mismatch in {source.run_id}/{attempt_id}: {model!r} != {source.model!r}"
            )
        totals = validate_token_equation(
            raw.get("prompt_tokens"), raw.get("completion_tokens"), raw.get("total_tokens"),
            prefix=f"{source.run_id}/{qid}/{attempt_id}",
        )
        normalized.append({
            "run_id": source.run_id,
            "purpose": purpose,
            "attempt_id": attempt_id,
            "qid": qid,
            "provider": str(raw.get("provider") or "").strip(),
            "model": model,
            "stage": str(raw.get("stage") or "").strip(),
            "status": status,
            "prompt_tokens": totals.prompt_tokens,
            "completion_tokens": totals.completion_tokens,
            "total_tokens": totals.total_tokens,
            "candidate_role": source.candidate_role,
            "source_ledger": str(source.ledger_path),
        })
    observed_qids = {str(row["qid"]) for row in normalized}
    missing_source_qids = sorted(allowed - observed_qids)
    if missing_source_qids:
        raise TokenAccountingError(
            f"ledger source {source.run_id} is missing terminal rows for allowed_qids: {missing_source_qids}"
        )
    return normalized


def load_multiple_ledgers(sources: Sequence[LedgerSource]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    isolation = validate_ledger_isolation(sources)
    rows: list[dict[str, Any]] = []
    seen_attempts: set[tuple[str, str]] = set()
    for source in sources:
        for row in load_ledger_rows(source):
            identity = (str(row["run_id"]), str(row["attempt_id"]))
            if identity in seen_attempts:
                raise TokenAccountingError(
                    f"duplicate ledger row across sources: run_id={identity[0]} attempt_id={identity[1]}"
                )
            seen_attempts.add(identity)
            rows.append(row)
    return rows, isolation


def _add_to_bucket(bucket: dict[str, Any], row: Mapping[str, Any]) -> None:
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens", 0)) + int(row["prompt_tokens"])
    bucket["completion_tokens"] = int(bucket.get("completion_tokens", 0)) + int(row["completion_tokens"])
    bucket["total_tokens"] = int(bucket.get("total_tokens", 0)) + int(row["total_tokens"])
    bucket["provider_calls"] = int(bucket.get("provider_calls", 0)) + 1


def _validate_bucket_equation(bucket: Mapping[str, Any], *, prefix: str) -> None:
    prompt = int(bucket.get("prompt_tokens", 0))
    completion = int(bucket.get("completion_tokens", 0))
    total = int(bucket.get("total_tokens", 0))
    if prompt + completion != total:
        raise TokenAccountingError(f"{prefix} token equation mismatch: {prompt} + {completion} != {total}")


def aggregate_candidate_usage(
    sources: Sequence[LedgerSource],
    *,
    candidate_qids: Sequence[str],
    selected_answer_source_by_qid: Mapping[str, str] | None = None,
    zero_call_qids: Sequence[str] = (),
    hard_cap_tokens: int = DEFAULT_CANDIDATE_TOKEN_HARD_CAP,
) -> dict[str, Any]:
    """Aggregate all decision calls; answer selection never deletes comparison usage."""
    qids = tuple(str(qid).strip() for qid in candidate_qids if str(qid).strip())
    if not qids or len(qids) != len(set(qids)):
        raise TokenAccountingError("candidate_qids must be unique and non-empty")
    qid_set = set(qids)
    zero_call_set = {str(qid) for qid in zero_call_qids}
    if not zero_call_set.issubset(qid_set):
        raise TokenAccountingError("zero_call_qids must be a subset of candidate_qids")

    rows, isolation = load_multiple_ledgers(sources)
    out_of_candidate = sorted({str(row["qid"]) for row in rows if str(row["qid"]) not in qid_set})
    if out_of_candidate:
        raise TokenAccountingError(f"ledger contains qids outside candidate_qids: {out_of_candidate}")

    selected = {str(qid): str(run_id) for qid, run_id in dict(selected_answer_source_by_qid or {}).items()}
    unknown_selected_qids = sorted(set(selected) - qid_set)
    if unknown_selected_qids:
        raise TokenAccountingError(f"selected answer source contains unknown qids: {unknown_selected_qids}")
    known_run_ids = {source.run_id for source in sources}
    unknown_selected_runs = sorted({run_id for run_id in selected.values() if run_id not in known_run_ids})
    if unknown_selected_runs:
        raise TokenAccountingError(f"selected answer source contains unknown run_ids: {unknown_selected_runs}")

    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "provider_calls": 0}
    by_qid: dict[str, dict[str, Any]] = {
        qid: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "provider_calls": 0,
            "run_ids": [],
            "models": [],
            "purposes": [],
        }
        for qid in qids
    }
    by_run_id: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_purpose: dict[str, dict[str, Any]] = {}
    matrix: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    unselected_comparison_calls = 0
    unselected_comparison_tokens = 0

    for row in rows:
        qid = str(row["qid"])
        _add_to_bucket(totals, row)
        _add_to_bucket(by_qid[qid], row)
        for list_key, value in (
            ("run_ids", str(row["run_id"])),
            ("models", str(row["model"])),
            ("purposes", str(row["purpose"])),
        ):
            if value not in by_qid[qid][list_key]:
                by_qid[qid][list_key].append(value)
        for mapping, key in (
            (by_run_id, str(row["run_id"])),
            (by_model, str(row["model"])),
            (by_purpose, str(row["purpose"])),
        ):
            _add_to_bucket(mapping.setdefault(key, {}), row)
        matrix_key = (str(row["run_id"]), qid, str(row["model"]), str(row["purpose"]))
        matrix_bucket = matrix.setdefault(matrix_key, {
            "run_id": matrix_key[0], "qid": matrix_key[1],
            "model": matrix_key[2], "purpose": matrix_key[3],
        })
        _add_to_bucket(matrix_bucket, row)
        selected_run = selected.get(qid)
        if selected_run and str(row["run_id"]) != selected_run:
            unselected_comparison_calls += 1
            unselected_comparison_tokens += int(row["total_tokens"])

    missing_qids = sorted(
        qid for qid, usage in by_qid.items()
        if int(usage["provider_calls"]) == 0 and qid not in zero_call_set
    )
    if missing_qids:
        raise TokenAccountingError(
            f"candidate qids have no provider ledger rows and are not declared zero-call: {missing_qids}"
        )
    for qid, usage in by_qid.items():
        usage["run_ids"] = sorted(usage["run_ids"])
        usage["models"] = sorted(usage["models"])
        usage["purposes"] = sorted(usage["purposes"])
        usage["selected_answer_source_run_id"] = selected.get(qid, "")
        usage["zero_call"] = qid in zero_call_set
        _validate_bucket_equation(usage, prefix=f"candidate.by_qid.{qid}")
    for label, mapping in (("by_run_id", by_run_id), ("by_model", by_model), ("by_purpose", by_purpose)):
        for key, usage in mapping.items():
            _validate_bucket_equation(usage, prefix=f"candidate.{label}.{key}")
    _validate_bucket_equation(totals, prefix="candidate.totals")
    enforce_candidate_token_hard_cap(int(totals["total_tokens"]), hard_cap_tokens=hard_cap_tokens)

    return {
        "schema_version": "bb_hybrid_token_accounting/v1",
        "candidate_qids": list(qids),
        "zero_call_qids": sorted(zero_call_set),
        "selected_answer_source_by_qid": selected,
        "totals": totals,
        "by_qid": by_qid,
        "by_run_id": dict(sorted(by_run_id.items())),
        "by_model": dict(sorted(by_model.items())),
        "by_purpose": dict(sorted(by_purpose.items())),
        "by_run_qid_model_purpose": [matrix[key] for key in sorted(matrix)],
        "ledger_sources": [source.as_manifest_dict() for source in sources],
        "ledger_isolation": isolation,
        "loaded_ledger_rows": len(rows),
        "accounted_decision_calls": int(totals["provider_calls"]),
        "all_decision_calls_accounted": len(rows) == int(totals["provider_calls"]),
        "unselected_comparison_calls_accounted": unselected_comparison_calls,
        "unselected_comparison_tokens_accounted": unselected_comparison_tokens,
        "hard_cap_tokens": int(hard_cap_tokens),
        "hard_cap_policy": "block_at_or_above",
        "hard_cap_pass": int(totals["total_tokens"]) < int(hard_cap_tokens),
    }


def _mapping_from_result(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TokenAccountingError(f"pipeline result must be a mapping or dataclass; got {type(value)!r}")


def validate_pipeline_results_against_usage(results: Sequence[Any], usage: Mapping[str, Any]) -> None:
    expected_qids = tuple(str(qid) for qid in usage.get("candidate_qids") or ())
    by_qid = dict(usage.get("by_qid") or {})
    result_by_qid: dict[str, dict[str, Any]] = {}
    for value in results:
        result = _mapping_from_result(value)
        qid = str(result.get("qid") or "")
        if not qid or qid in result_by_qid:
            raise TokenAccountingError(f"invalid or duplicate PipelineResult qid: {qid!r}")
        result_by_qid[qid] = result
    if set(result_by_qid) != set(expected_qids):
        raise TokenAccountingError(
            "PipelineResult qid set does not match candidate usage: "
            f"missing={sorted(set(expected_qids) - set(result_by_qid))} "
            f"extra={sorted(set(result_by_qid) - set(expected_qids))}"
        )
    for qid in expected_qids:
        result = result_by_qid[qid]
        expected = by_qid[qid]
        actual = validate_token_equation(
            result.get("prompt_tokens"), result.get("completion_tokens"), result.get("total_tokens"),
            prefix=f"PipelineResult.{qid}",
        )
        expected_triplet = (
            int(expected.get("prompt_tokens", 0)), int(expected.get("completion_tokens", 0)),
            int(expected.get("total_tokens", 0)),
        )
        if (actual.prompt_tokens, actual.completion_tokens, actual.total_tokens) != expected_triplet:
            raise TokenAccountingError(
                f"PipelineResult/ledger mismatch for {qid}: "
                f"result={(actual.prompt_tokens, actual.completion_tokens, actual.total_tokens)} ledger={expected_triplet}"
            )
        metadata = result.get("metadata")
        if isinstance(metadata, Mapping) and metadata.get("provider_ledger_token_totals") is not None:
            ledger_meta = metadata.get("provider_ledger_token_totals")
            if not isinstance(ledger_meta, Mapping):
                raise TokenAccountingError(
                    f"PipelineResult metadata provider_ledger_token_totals must be an object for {qid}"
                )
            meta_totals = validate_token_equation(
                ledger_meta.get("prompt_tokens"), ledger_meta.get("completion_tokens"),
                ledger_meta.get("total_tokens"),
                prefix=f"PipelineResult.{qid}.metadata.provider_ledger_token_totals",
            )
            if (meta_totals.prompt_tokens, meta_totals.completion_tokens, meta_totals.total_tokens) != expected_triplet:
                raise TokenAccountingError(f"PipelineResult metadata/ledger mismatch for {qid}")


def read_multi_slot_submission(path: Path) -> dict[str, Any]:
    submission_path = Path(path)
    with submission_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2:
        raise TokenAccountingError(f"multi-slot CSV is incomplete: {submission_path}")
    header = tuple(str(value).strip() for value in rows[0])
    if header != SUBMISSION_HEADER:
        raise TokenAccountingError(f"invalid multi-slot CSV header: expected={SUBMISSION_HEADER!r} actual={header!r}")
    if str(rows[1][0]).strip() != "summary":
        raise TokenAccountingError("multi-slot CSV summary row must immediately follow the header")

    def _row_with_width(raw: Sequence[str], *, row_number: int) -> list[str]:
        if len(raw) != len(SUBMISSION_HEADER):
            raise TokenAccountingError(
                f"multi-slot CSV row {row_number} width mismatch: {len(raw)} != {len(SUBMISSION_HEADER)}"
            )
        return [str(value) for value in raw]

    summary_row = _row_with_width(rows[1], row_number=2)
    if str(summary_row[8]).strip():
        raise TokenAccountingError("multi-slot CSV summary reasoning must be empty")
    summary = {
        "prompt_tokens": _strict_csv_int(summary_row[5], field="summary.prompt_tokens"),
        "completion_tokens": _strict_csv_int(summary_row[6], field="summary.completion_tokens"),
        "total_tokens": _strict_csv_int(summary_row[7], field="summary.total_tokens"),
    }
    _validate_bucket_equation(summary, prefix="csv.summary")
    qid_order: list[str] = []
    by_qid: dict[str, dict[str, Any]] = {}
    for row_number, raw in enumerate(rows[2:], start=3):
        row = _row_with_width(raw, row_number=row_number)
        qid = row[0].strip()
        if not qid:
            raise TokenAccountingError(f"empty qid in multi-slot CSV row {row_number}")
        if qid in by_qid:
            raise TokenAccountingError(f"duplicate qid in multi-slot CSV: {qid}")
        usage = {
            "prompt_tokens": _strict_csv_int(row[5], field=f"{qid}.prompt_tokens"),
            "completion_tokens": _strict_csv_int(row[6], field=f"{qid}.completion_tokens"),
            "total_tokens": _strict_csv_int(row[7], field=f"{qid}.total_tokens"),
        }
        _validate_bucket_equation(usage, prefix=f"csv.{qid}")
        answers = tuple(str(value).strip() for value in row[1:5] if str(value).strip())
        reasoning = str(row[8]).strip()
        reasoning_check = validate_reasoning_contract(reasoning, answers=answers)
        if not reasoning_check.valid:
            raise TokenAccountingError(f"invalid multi-slot reasoning for {qid}: {reasoning_check.reason}")
        qid_order.append(qid)
        by_qid[qid] = {"answers": tuple(row[1:5]), "reasoning": reasoning, "reasoning_validation": reasoning_check.to_dict(), **usage, "raw_row": row}
    computed = {
        "prompt_tokens": sum(int(value["prompt_tokens"]) for value in by_qid.values()),
        "completion_tokens": sum(int(value["completion_tokens"]) for value in by_qid.values()),
        "total_tokens": sum(int(value["total_tokens"]) for value in by_qid.values()),
    }
    if computed != summary:
        raise TokenAccountingError(f"multi-slot CSV summary mismatch: summary={summary} computed={computed}")
    return {
        "path": str(submission_path.resolve()),
        "header": list(header),
        "qid_order": qid_order,
        "summary": summary,
        "by_qid": by_qid,
    }


def validate_csv_against_usage(path: Path, usage: Mapping[str, Any]) -> None:
    csv_data = read_multi_slot_submission(path)
    expected_qids = [str(qid) for qid in usage.get("candidate_qids") or ()]
    if csv_data["qid_order"] != expected_qids:
        raise TokenAccountingError(
            f"CSV qid order does not match candidate usage: csv={csv_data['qid_order']} usage={expected_qids}"
        )
    usage_by_qid = dict(usage.get("by_qid") or {})
    for qid in expected_qids:
        actual = csv_data["by_qid"][qid]
        expected = usage_by_qid[qid]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if int(actual[key]) != int(expected.get(key, 0)):
                raise TokenAccountingError(
                    f"CSV/ledger token mismatch for {qid}.{key}: csv={actual[key]} ledger={expected.get(key)}"
                )
    totals = dict(usage.get("totals") or {})
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if int(csv_data["summary"][key]) != int(totals.get(key, 0)):
            raise TokenAccountingError(
                f"CSV/ledger summary mismatch for {key}: csv={csv_data['summary'][key]} ledger={totals.get(key)}"
            )


def build_candidate_usage_manifest(
    *,
    candidate_id: str,
    usage: Mapping[str, Any],
    sources: Sequence[LedgerSource],
    selected_answer_source_by_qid: Mapping[str, str],
    candidate_csv: Path | None = None,
    relative_to: Path | None = None,
) -> dict[str, Any]:
    candidate_id = str(candidate_id).strip()
    if not candidate_id:
        raise TokenAccountingError("candidate_id must be non-empty")
    base = Path(relative_to).resolve() if relative_to is not None else None

    def _display(path: Path | None) -> str:
        if path is None:
            return ""
        resolved = path.resolve()
        if base is not None:
            try:
                return resolved.relative_to(base).as_posix()
            except ValueError:
                pass
        return str(resolved)

    return {
        "schema_version": "bb_hybrid_candidate_manifest/v1",
        "candidate_id": candidate_id,
        "candidate_csv": _display(candidate_csv),
        "candidate_qids": list(usage.get("candidate_qids") or ()),
        "selected_answer_source_by_qid": dict(selected_answer_source_by_qid),
        "ledger_sources": [source.as_manifest_dict(relative_to=base) for source in sources],
        "cumulative_usage": dict(usage.get("totals") or {}),
        "cumulative_usage_by_qid": dict(usage.get("by_qid") or {}),
        "cumulative_usage_by_run_id": dict(usage.get("by_run_id") or {}),
        "cumulative_usage_by_model": dict(usage.get("by_model") or {}),
        "cumulative_usage_by_purpose": dict(usage.get("by_purpose") or {}),
        "accounted_decision_calls": int(usage.get("accounted_decision_calls", 0)),
        "all_decision_calls_accounted": bool(usage.get("all_decision_calls_accounted")),
        "unselected_comparison_calls_accounted": int(usage.get("unselected_comparison_calls_accounted", 0)),
        "unselected_comparison_tokens_accounted": int(usage.get("unselected_comparison_tokens_accounted", 0)),
        "ledger_isolation": dict(usage.get("ledger_isolation") or {}),
        "hard_cap_tokens": int(usage.get("hard_cap_tokens", DEFAULT_CANDIDATE_TOKEN_HARD_CAP)),
        "hard_cap_policy": str(usage.get("hard_cap_policy") or "block_at_or_above"),
        "hard_cap_pass": bool(usage.get("hard_cap_pass")),
    }


def validate_manifest_against_usage(manifest: Mapping[str, Any], usage: Mapping[str, Any]) -> None:
    if str(manifest.get("schema_version") or "") != "bb_hybrid_candidate_manifest/v1":
        raise TokenAccountingError("unsupported hybrid candidate manifest schema")
    if list(manifest.get("candidate_qids") or ()) != list(usage.get("candidate_qids") or ()):
        raise TokenAccountingError("manifest candidate_qids do not match token usage")
    if dict(manifest.get("cumulative_usage") or {}) != dict(usage.get("totals") or {}):
        raise TokenAccountingError("manifest cumulative_usage does not match token usage")
    if dict(manifest.get("cumulative_usage_by_qid") or {}) != dict(usage.get("by_qid") or {}):
        raise TokenAccountingError("manifest cumulative_usage_by_qid does not match token usage")
    if int(manifest.get("accounted_decision_calls", -1)) != int(usage.get("accounted_decision_calls", 0)):
        raise TokenAccountingError("manifest accounted_decision_calls mismatch")
    if manifest.get("all_decision_calls_accounted") is not True:
        raise TokenAccountingError("manifest must assert all_decision_calls_accounted=true")
    if manifest.get("hard_cap_policy") != "block_at_or_above":
        raise TokenAccountingError("manifest hard_cap_policy must be block_at_or_above")
    enforce_candidate_token_hard_cap(
        int(dict(manifest.get("cumulative_usage") or {}).get("total_tokens", 0)),
        hard_cap_tokens=int(manifest.get("hard_cap_tokens", DEFAULT_CANDIDATE_TOKEN_HARD_CAP)),
    )


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def annotate_ledger_file(path: Path, *, run_id: str, purpose: str) -> None:
    """Attach immutable run/purpose lineage to runtime ledger rows."""
    run_id = str(run_id).strip()
    purpose = str(purpose).strip()
    if not run_id:
        raise TokenAccountingError("run_id must be non-empty when annotating a ledger")
    if purpose not in DECISION_PURPOSES:
        raise TokenAccountingError(f"unsupported decision purpose: {purpose!r}")
    ledger_path = Path(path)
    if not ledger_path.exists():
        return
    output_rows: list[dict[str, Any]] = []
    for raw in _read_jsonl(ledger_path):
        raw.pop("_line_number", None)
        existing_run_id = str(raw.get("run_id") or "").strip()
        existing_purpose = str(raw.get("purpose") or "").strip()
        if existing_run_id and existing_run_id != run_id:
            raise TokenAccountingError(
                f"cannot overwrite conflicting ledger run_id: {existing_run_id!r} != {run_id!r}"
            )
        if existing_purpose and existing_purpose != purpose:
            raise TokenAccountingError(
                f"cannot overwrite conflicting ledger purpose: {existing_purpose!r} != {purpose!r}"
            )
        raw["run_id"] = run_id
        raw["purpose"] = purpose
        output_rows.append(raw)
    temporary = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    temporary.replace(ledger_path)


def _resolve_manifest_path(root: Path, raw: Any, *, field: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise TokenAccountingError(f"paid manifest requires {field}")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def validate_paid_runtime_manifest_contract(
    manifest: Mapping[str, Any],
    *,
    root: Path,
    requested_output_dir: Path,
) -> dict[str, Any]:
    """Validate Lane C run isolation and cumulative hard-cap fields."""
    root = Path(root).resolve()
    requested_output_dir = Path(requested_output_dir).resolve()
    run_id = str(manifest.get("run_id") or "").strip()
    model = str(manifest.get("model") or "").strip()
    purpose = str(manifest.get("decision_purpose") or manifest.get("purpose") or "").strip()
    if not run_id:
        raise TokenAccountingError("paid manifest requires run_id")
    if not model:
        raise TokenAccountingError("paid manifest requires model")
    if purpose not in DECISION_PURPOSES:
        raise TokenAccountingError(f"paid manifest has unsupported decision_purpose: {purpose!r}")

    output_dir = _resolve_manifest_path(root, manifest.get("output_dir"), field="output_dir")
    if output_dir != requested_output_dir:
        raise TokenAccountingError(
            f"requested output_dir does not match paid manifest: {requested_output_dir} != {output_dir}"
        )
    ledger_path = _resolve_manifest_path(root, manifest.get("token_ledger_path"), field="token_ledger_path")
    usage_file = _resolve_manifest_path(root, manifest.get("usage_file"), field="usage_file")
    resolved_config = _resolve_manifest_path(
        root, manifest.get("resolved_runtime_config_path"), field="resolved_runtime_config_path"
    )
    artifact_paths = [ledger_path, usage_file, resolved_config]
    if len({str(path) for path in artifact_paths}) != len(artifact_paths):
        raise TokenAccountingError("paid manifest runtime artifact paths must be distinct")
    expected_artifacts = {
        "token_ledger_path": output_dir / "token_ledger.jsonl",
        "usage_file": output_dir / "provider_usage.json",
        "resolved_runtime_config_path": output_dir / "resolved_runtime_config.json",
    }
    for label, path in (
        ("token_ledger_path", ledger_path),
        ("usage_file", usage_file),
        ("resolved_runtime_config_path", resolved_config),
    ):
        if path != expected_artifacts[label]:
            raise TokenAccountingError(
                f"{label} must use the standard isolated path: {expected_artifacts[label]}"
            )
    for label, path in (
        ("token_ledger_path", ledger_path),
        ("usage_file", usage_file),
        ("resolved_runtime_config_path", resolved_config),
    ):
        try:
            path.relative_to(output_dir)
        except ValueError as exc:
            raise TokenAccountingError(f"{label} must be inside output_dir") from exc

    raw_qids = manifest.get("allowed_qids")
    if not isinstance(raw_qids, Sequence) or isinstance(raw_qids, (str, bytes)):
        raise TokenAccountingError("paid manifest allowed_qids must be an array")
    allowed_qids = [str(qid).strip() for qid in raw_qids if str(qid).strip()]
    if not allowed_qids or len(allowed_qids) != len(set(allowed_qids)):
        raise TokenAccountingError("paid manifest allowed_qids must be unique and non-empty")
    if manifest.get("fallback_authorized") is not False:
        raise TokenAccountingError("multi-slot paid manifest must set fallback_authorized=false")
    failure_policy = manifest.get("failure_policy")
    if not isinstance(failure_policy, Mapping):
        raise TokenAccountingError("paid manifest failure_policy must be an object")
    if int(failure_policy.get("fallback_calls", 0) or 0) != 0:
        raise TokenAccountingError("multi-slot paid manifest must set failure_policy.fallback_calls=0")
    retry_count = _strict_nonnegative_int(manifest.get("retry_count", 0), field="paid_manifest.retry_count")
    if retry_count != 0 and manifest.get("retry_authorized") is not True:
        raise TokenAccountingError("retry_count must be 0 unless retry_authorized=true")

    hard_cap = _strict_nonnegative_int(
        manifest.get("total_token_hard_cap", DEFAULT_CANDIDATE_TOKEN_HARD_CAP),
        field="paid_manifest.total_token_hard_cap",
    )
    if hard_cap > DEFAULT_CANDIDATE_TOKEN_HARD_CAP:
        raise TokenAccountingError(
            f"paid manifest hard cap cannot exceed {DEFAULT_CANDIDATE_TOKEN_HARD_CAP}"
        )
    prior_total = _strict_nonnegative_int(
        manifest.get("candidate_prior_total_tokens", 0),
        field="paid_manifest.candidate_prior_total_tokens",
    )
    run_budget = _strict_nonnegative_int(manifest.get("token_budget"), field="paid_manifest.token_budget")
    enforce_candidate_token_hard_cap(prior_total + run_budget, hard_cap_tokens=hard_cap)
    per_qid_call_cap = _strict_nonnegative_int(
        manifest.get("per_qid_completed_call_budget", 1),
        field="paid_manifest.per_qid_completed_call_budget",
    )
    if per_qid_call_cap <= 0:
        raise TokenAccountingError("per_qid_completed_call_budget must be positive")
    circuit_policy = manifest.get("circuit_breaker_policy")
    if not isinstance(circuit_policy, Mapping):
        raise TokenAccountingError("paid manifest circuit_breaker_policy must be an object")
    max_provider_calls = int(
        circuit_policy.get("max_model_calls")
        or circuit_policy.get("max_provider_calls")
        or len(allowed_qids) * per_qid_call_cap
    )
    if max_provider_calls <= 0:
        raise TokenAccountingError("paid manifest max provider call cap must be positive")
    return {
        "run_id": run_id,
        "model": model,
        "decision_purpose": purpose,
        "output_dir": output_dir,
        "token_ledger_path": ledger_path,
        "usage_file": usage_file,
        "resolved_runtime_config_path": resolved_config,
        "allowed_qids": allowed_qids,
        "retry_count": retry_count,
        "candidate_prior_total_tokens": prior_total,
        "run_token_budget": run_budget,
        "total_token_hard_cap": hard_cap,
        "per_qid_completed_call_budget": per_qid_call_cap,
        "max_provider_calls": max_provider_calls,
        "fallback": "NO",
    }


def validate_runtime_cumulative_cap(
    *,
    prior_total_tokens: int,
    current_run_tokens: int,
    hard_cap_tokens: int,
    reserve_tokens: int = 0,
) -> None:
    total = (
        _strict_nonnegative_int(prior_total_tokens, field="runtime.prior_total_tokens")
        + _strict_nonnegative_int(current_run_tokens, field="runtime.current_run_tokens")
        + _strict_nonnegative_int(reserve_tokens, field="runtime.reserve_tokens")
    )
    enforce_candidate_token_hard_cap(total, hard_cap_tokens=hard_cap_tokens)
