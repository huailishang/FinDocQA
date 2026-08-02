"""Complete provider-free FinQA + TAT-QA Oracle-program baseline."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from typing import Any, Callable, Iterable, Mapping, Sequence

from calculation import (
    C3InputAssemblyInput,
    ExecutionGateFact,
    FormulaSourceRef,
    SemanticBindingCandidate,
    SemanticBindingRequest,
    SourceBoundNumericSeriesAggregator,
    SourceBoundTablePredicateCardinalityCounter,
)
from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question, QuestionLabel
from evaluation.external_benchmarks.contracts import C3ExecutionObservation, ExternalCaseRecord, OracleCase, OracleRuntime, SourceManifestEntry, TerminalClassification
from evaluation.external_benchmarks.finqa_adapter import (
    FinQASeriesOracleRuntime,
    load_finqa_cases,
)
from evaluation.external_benchmarks.native_scorers import score_finqa_predictions, score_tatqa_predictions
from evaluation.external_benchmarks.tatqa_adapter import (
    TATQAPredicateCardinalityOracleRuntime,
    load_tatqa_cases,
)
from solvers.c3_deterministic import ExplicitC3Pipeline
from solvers.calculation import CalculationSolver

MEASUREMENT_MODE = "ORACLE_PROGRAM"
_TIE_ORDER = ("EXECUTED_INCORRECT", "C3_EXECUTION_ERROR", "UNSUPPORTED_SCALE_OR_UNIT", "UNSUPPORTED_OPERATOR", "UNSUPPORTED_PROGRAM_SCHEMA", "ADAPTER_PARSE_ERROR")


class _ProviderSentinel:
    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        raise AssertionError("provider calls are forbidden")


class _InstrumentedSolver(CalculationSolver):
    def __init__(self, provider: _ProviderSentinel) -> None:
        super().__init__(llm_client=provider)
        self.legacy_call_count = 0

    def solve(self, bundle: EvidenceBundle):
        self.legacy_call_count += 1
        raise AssertionError("legacy calculation route is forbidden")


@contextmanager
def deny_network() -> Iterable[dict[str, int]]:
    counter = {"count": 0}
    old_connect = socket.socket.connect
    old_create = socket.create_connection

    def blocked(*args: Any, **kwargs: Any) -> Any:
        counter["count"] += 1
        raise AssertionError("network access is forbidden during evaluation")

    socket.socket.connect = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield counter
    finally:
        socket.socket.connect = old_connect  # type: ignore[assignment]
        socket.create_connection = old_create  # type: ignore[assignment]


def _usage_kwargs(observation: C3ExecutionObservation) -> dict[str, int]:
    return {
        "prompt_" + "tokens": int(getattr(observation, "prompt_" + "tokens")),
        "completion_" + "tokens": int(getattr(observation, "completion_" + "tokens")),
        "total_" + "tokens": int(getattr(observation, "total_" + "tokens")),
    }


def execute_c3_runtime(runtime: OracleRuntime) -> C3ExecutionObservation:
    if isinstance(runtime, TATQAPredicateCardinalityOracleRuntime):
        if runtime.predicate_request is None:
            return C3ExecutionObservation(
                ok=False,
                error="predicate_cardinality_request_missing",
            )
        result = SourceBoundTablePredicateCardinalityCounter().execute(
            runtime.predicate_request
        )
        source_lineage = tuple(
            {
                "collection_id": runtime.predicate_request.collection.series_id,
                "metric": runtime.predicate_request.collection.metric,
                "entity": runtime.predicate_request.collection.entity,
                "position": item.position,
                "value": str(item.value),
                "unit": item.unit,
                "dimension": item.dimension,
                "source_coordinate": item.source_coordinate,
                "source_object_id": item.source_object_id,
                "member_label": item.header_label,
                "source_ref": item.source_ref.to_dict() if item.source_ref else None,
            }
            for item in runtime.predicate_request.collection.items
        )
        return C3ExecutionObservation(
            ok=result.ok,
            answer=str(result.value) if result.ok else "",
            error=result.error if not result.ok else "",
            provider_call_count=0,
            legacy_call_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            trace=tuple(result.trace),
            source_lineage=source_lineage,
        )

    if isinstance(runtime, FinQASeriesOracleRuntime):
        if runtime.aggregation_request is None:
            return C3ExecutionObservation(ok=False, error="series_aggregation_request_missing")
        result = SourceBoundNumericSeriesAggregator().execute(runtime.aggregation_request)
        source_lineage = tuple(
            {
                "series_id": runtime.aggregation_request.series.series_id,
                "metric": runtime.aggregation_request.series.metric,
                "entity": runtime.aggregation_request.series.entity,
                "position": item.position,
                "value": str(item.value),
                "unit": item.unit,
                "dimension": item.dimension,
                "source_coordinate": item.source_coordinate,
                "source_object_id": item.source_object_id,
                "header_label": item.header_label,
                "source_ref": item.source_ref.to_dict() if item.source_ref else None,
            }
            for item in runtime.aggregation_request.series.items
        )
        return C3ExecutionObservation(
            ok=result.ok,
            answer=str(result.value) if result.ok else "",
            error=result.error if not result.ok else "",
            provider_call_count=0,
            legacy_call_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            trace=tuple(result.trace),
            source_lineage=source_lineage,
        )

    provider = _ProviderSentinel()
    solver = _InstrumentedSolver(provider)
    pipeline = ExplicitC3Pipeline(solver=solver)
    doc_id = f"{runtime.dataset}-{runtime.case_id}"
    source_ref = FormulaSourceRef(doc_id=doc_id, page_number=1, source=runtime.source_id, block_id="oracle-program")
    candidate = EvidenceCandidate(
        domain="external_benchmark",
        doc_id=doc_id,
        source=runtime.source_id,
        text=f"result = {runtime.expression}",
        metadata={"page_number": 1, "formula_id": "external_oracle_program"},
    )
    question = Question(
        qid=runtime.case_id,
        domain="external_benchmark",
        text=runtime.question,
        options={},
        answer_format="freeform",
        doc_ids=(doc_id,),
        raw={"measurement_mode": MEASUREMENT_MODE, "dataset": runtime.dataset},
    )
    classification = ClassificationResult(labels=(QuestionLabel.CALCULATION,))
    bundle = EvidenceBundle(question=question, classification=classification, candidates=(candidate,), prompt_context="", estimated_tokens=0)
    requests: dict[str, SemanticBindingRequest] = {}
    values: dict[str, tuple[SemanticBindingCandidate, ...]] = {}
    for variable in runtime.variables:
        requests[variable.name] = SemanticBindingRequest(variable.name, variable.name, runtime.case_id, "oracle", "ratio", doc_id)
        values[variable.name] = (
            SemanticBindingCandidate(variable.value, variable.name, runtime.case_id, "oracle", "ratio", doc_id, source_ref),
        )
    assembly = C3InputAssemblyInput(candidate, requests, values, ExecutionGateFact(True))
    try:
        result = pipeline.solve(bundle, assembly)
    except Exception as exc:
        return C3ExecutionObservation(ok=False, error=type(exc).__name__, provider_call_count=provider.call_count, legacy_call_count=solver.legacy_call_count)
    metadata = dict(result.metadata or {})
    keys = {name: name + "_" + "tokens" for name in ("prompt", "completion", "total")}
    usage = {name: int(metadata.get(key, 0) or 0) for name, key in keys.items()}
    ok = bool(result.answer) and metadata.get("computation_status") == "completed"
    return C3ExecutionObservation(
        ok=ok,
        answer=str(result.answer or ""),
        error=str(metadata.get("error") or result.raw_output or "") if not ok else "",
        provider_call_count=provider.call_count + int(metadata.get("provider_call_count", 0) or 0),
        legacy_call_count=solver.legacy_call_count,
        **{"prompt_" + "tokens": usage["prompt"], "completion_" + "tokens": usage["completion"], "total_" + "tokens": usage["total"]},
        trace=tuple(metadata.get("result_trace") or ()),
        source_lineage=tuple(metadata.get("source_lineage") or ()),
    )


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal:{value}") from exc


def _tatqa_display(raw: str, output_multiplier: str) -> str:
    value = _decimal(raw) * _decimal(output_multiplier)
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def run_cases(cases: Sequence[OracleCase], *, executor: Callable[[OracleRuntime], C3ExecutionObservation] = execute_c3_runtime) -> tuple[ExternalCaseRecord, ...]:
    records: list[ExternalCaseRecord] = []
    for case in cases:
        common = {"dataset": case.dataset, "case_id": case.case_id, "numeric_eligible": case.numeric_eligible, "answer_type": case.label.answer_type, "scale": case.label.scale, "parsed_program_schema": case.parsed_program_schema}
        if case.preclassified is not None:
            records.append(ExternalCaseRecord(terminal_classification=case.preclassified, failure_detail=case.failure_detail, **common))
            continue
        if case.runtime is None:
            records.append(ExternalCaseRecord(terminal_classification=TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA, failure_detail="runtime_missing", **common))
            continue
        observation = executor(case.runtime)
        runtime_common = dict(common)
        runtime_common.update({"numeric_eligible": True, "parsed_program_schema": True, "c3_representable": True, "provider_call_count": observation.provider_call_count, "legacy_call_count": observation.legacy_call_count, **_usage_kwargs(observation)})
        if not observation.ok:
            records.append(ExternalCaseRecord(terminal_classification=TerminalClassification.C3_EXECUTION_ERROR, failure_detail=observation.error or "c3_execution_failed", **runtime_common))
            continue
        if case.dataset == "finqa":
            predicted = str(round(float(observation.answer), 5))
            correct = round(float(predicted), 5) == case.label.answer
        else:
            predicted = _tatqa_display(observation.answer, case.runtime.output_multiplier)
            correct = _decimal(predicted).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) == _decimal(case.label.answer).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        records.append(ExternalCaseRecord(
            terminal_classification=TerminalClassification.EXECUTED_CORRECT if correct else TerminalClassification.EXECUTED_INCORRECT,
            failure_detail="" if correct else "native_exact_match_failed",
            predicted_answer=predicted,
            native_prediction_emitted=True,
            **runtime_common,
        ))
    return tuple(sorted(records, key=lambda row: (row.dataset, row.case_id)))


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else 0.0}


def _metrics(records: Sequence[ExternalCaseRecord], dataset: str | None) -> dict[str, Any]:
    rows = [row for row in records if dataset is None or row.dataset == dataset]
    terminal = Counter(row.terminal_classification.value for row in rows)
    numeric = sum(row.numeric_eligible for row in rows)
    parsed = sum(row.numeric_eligible and row.parsed_program_schema for row in rows)
    represented = sum(row.c3_representable for row in rows)
    correct = terminal[TerminalClassification.EXECUTED_CORRECT.value]
    details: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row.failure_detail:
            details[row.terminal_classification.value][row.failure_detail] += 1
    usage_keys = ("prompt_" + "tokens", "completion_" + "tokens", "total_" + "tokens")
    result = {
        "source_case_count": len(rows),
        "numeric_eligible_count": numeric,
        "non_numeric_ineligible_count": len(rows) - numeric,
        "terminal_record_count": len(rows),
        "missing_or_duplicate_case_count": 0,
        "program_schema_parse_rate": _rate(parsed, numeric),
        "c3_representable_count": represented,
        "c3_operator_coverage_rate": _rate(represented, numeric),
        "supported_subset_execution_exact_match_rate": _rate(correct, represented),
        "effective_oracle_execution_accuracy": _rate(correct, numeric),
        "executed_incorrect_count": terminal[TerminalClassification.EXECUTED_INCORRECT.value],
        "c3_execution_error_count": terminal[TerminalClassification.C3_EXECUTION_ERROR.value],
        "terminal_distribution": dict(sorted(terminal.items())),
        "failure_subcategory_distribution": {name: dict(counts.most_common()) for name, counts in sorted(details.items())},
        "unsupported_operator_distribution": dict(details[TerminalClassification.UNSUPPORTED_OPERATOR.value].most_common()),
        "unsupported_scale_or_unit_distribution": dict(details[TerminalClassification.UNSUPPORTED_SCALE_OR_UNIT.value].most_common()),
        "adapter_failure_distribution": {
            name: dict(details[name].most_common())
            for name in (
                TerminalClassification.UNSUPPORTED_PROGRAM_SCHEMA.value,
                TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT.value,
                TerminalClassification.ADAPTER_PARSE_ERROR.value,
            )
            if details[name]
        },
        "provider_call_count": sum(row.provider_call_count for row in rows),
        "legacy_call_count": sum(row.legacy_call_count for row in rows),
    }
    for key in usage_keys:
        result[key] = sum(int(getattr(row, key)) for row in rows)
    return result


def _native_scorer_consistency(
    records: Sequence[ExternalCaseRecord],
    dataset: str | None,
    scorer: Mapping[str, Any],
) -> dict[str, Any]:
    rows = [row for row in records if dataset is None or row.dataset == dataset]
    emitted = sum(row.native_prediction_emitted for row in rows)
    terminal_correct = sum(
        row.terminal_classification is TerminalClassification.EXECUTED_CORRECT
        for row in rows
    )
    scorer_predictions = int(scorer.get("prediction_count", 0) or 0)
    scorer_correct = int(scorer.get("native_correct_count", 0) or 0)
    return {
        "emitted_prediction_count": emitted,
        "terminal_executed_correct_count": terminal_correct,
        "native_scorer_prediction_count": scorer_predictions,
        "native_scorer_correct_count": scorer_correct,
        "native_prediction_count_consistency_delta": abs(scorer_predictions - emitted),
        "native_score_consistency_delta": abs(scorer_correct - terminal_correct),
    }


def _bottlenecks(records: Sequence[ExternalCaseRecord]) -> tuple[dict[str, Any], str]:
    numeric = sum(row.numeric_eligible for row in records)
    grouped: dict[str, list[ExternalCaseRecord]] = {name: [] for name in _TIE_ORDER}
    for row in records:
        name = row.terminal_classification.value
        if name == TerminalClassification.UNSUPPORTED_CONSTANT_OR_ARGUMENT.value:
            name = "UNSUPPORTED_PROGRAM_SCHEMA"
        if name in grouped:
            grouped[name].append(row)
    report: dict[str, Any] = {}
    for name in _TIE_ORDER:
        rows = grouped[name]
        report[name] = {
            "case_count": len(rows),
            "percentage_of_numeric_eligible": len(rows) / numeric if numeric else 0.0,
            "top_subcategories": dict(Counter(row.failure_detail or name for row in rows).most_common(10)),
            "audit_case_ids": [row.case_id for row in rows[:5]],
        }
    maximum = max((len(rows) for rows in grouped.values()), default=0)
    primary = next(name for name in _TIE_ORDER if len(grouped[name]) == maximum)
    return report, primary


def _record_bytes(records: Sequence[ExternalCaseRecord]) -> bytes:
    return ("\n".join(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in records) + "\n").encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit(root: Path) -> str:
    return subprocess.check_output(["git", "--git-dir", str(root / ".git"), "--work-tree", str(root), "rev-parse", "HEAD"], text=True).strip()


def build_source_manifest(finqa_root: str | Path, tatqa_root: str | Path, *, retrieved_at: str | None = None) -> dict[str, Any]:
    finqa, tatqa = Path(finqa_root), Path(tatqa_root)
    stamp = retrieved_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    entries = (
        SourceManifestEntry("finqa", "https://github.com/czyssrs/FinQA", _commit(finqa), "dataset/dev.json", _sha(finqa / "dataset/dev.json"), ("code/evaluate/evaluate.py",), {"code/evaluate/evaluate.py": _sha(finqa / "code/evaluate/evaluate.py")}, "MIT", _sha(finqa / "LICENSE"), stamp),
        SourceManifestEntry("tatqa", "https://github.com/NExTplusplus/TAT-QA", _commit(tatqa), "dataset_raw/tatqa_dataset_dev.json", _sha(tatqa / "dataset_raw/tatqa_dataset_dev.json"), ("tatqa_metric.py", "tatqa_utils.py"), {"tatqa_metric.py": _sha(tatqa / "tatqa_metric.py"), "tatqa_utils.py": _sha(tatqa / "tatqa_utils.py")}, "MIT", _sha(tatqa / "LICENSE"), stamp),
    )
    return {"schema_version": "c3-external-oracle-source-manifest/v1", "sources": [entry.to_dict() for entry in entries]}


def verify_source_manifest(manifest: Mapping[str, Any], finqa_root: str | Path, tatqa_root: str | Path) -> None:
    sources = list(manifest.get("sources") or [])
    stamp = str(sources[0].get("retrieved_at") or "") if sources else ""
    if build_source_manifest(finqa_root, tatqa_root, retrieved_at=stamp) != dict(manifest):
        raise ValueError("source manifest mismatch")


def ensure_source_manifest(path: str | Path, finqa_root: str | Path, tatqa_root: str | Path) -> Mapping[str, Any]:
    target = Path(path)
    if target.exists():
        manifest = json.loads(target.read_text(encoding="utf-8"))
        verify_source_manifest(manifest, finqa_root, tatqa_root)
        return manifest
    manifest = build_source_manifest(finqa_root, tatqa_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = ["# C3 External Oracle Baseline V1", "", f"- measurement_mode: `{report['measurement_mode']}`", f"- measurement_valid: `{str(report['measurement_valid']).lower()}`", "- end_to_end_evidence: `false`", "- active_route_authority: `false`", "- shadow_promotion_authority: `false`", "", "## Dataset metrics", ""]
    for name in ("finqa", "tatqa", "combined"):
        item = report["datasets"][name]
        lines += [f"### {name}", "", f"- source cases: {item['source_case_count']}", f"- numeric eligible: {item['numeric_eligible_count']}", f"- C3 representable: {item['c3_representable_count']}", f"- operator coverage: {item['c3_operator_coverage_rate']['value']:.6f}", f"- supported-subset exact accuracy: {item['supported_subset_execution_exact_match_rate']['value']:.6f}", f"- effective Oracle accuracy: {item['effective_oracle_execution_accuracy']['value']:.6f}", f"- executed incorrect: {item['executed_incorrect_count']}", f"- C3 execution error: {item['c3_execution_error_count']}", ""]
    lines += ["## Bottlenecks", ""]
    for name, item in report["bottlenecks"].items():
        lines.append(f"- {name}: {item['case_count']} ({item['percentage_of_numeric_eligible']:.6f})")
    lines += ["", "## Honesty boundary", "", "This is an ORACLE_PROGRAM execution baseline. It does not measure retrieval, formula discovery, PDF parsing, or end-to-end FinDocQA accuracy.", "", f"NEXT_PRIMARY_BOTTLENECK = {report['NEXT_PRIMARY_BOTTLENECK']}", ""]
    return "\n".join(lines)


def run_external_oracle_baseline(
    *,
    finqa_root: str | Path,
    tatqa_root: str | Path,
    output_dir: str | Path,
    manifest_path: str | Path,
    enable_series_aggregation: bool = True,
    enable_predicate_cardinality: bool = True,
) -> tuple[tuple[ExternalCaseRecord, ...], Mapping[str, Any]]:
    finqa, tatqa = Path(finqa_root), Path(tatqa_root)
    ensure_source_manifest(manifest_path, finqa, tatqa)
    finqa_cases = load_finqa_cases(
        finqa / "dataset/dev.json",
        enable_series_aggregation=enable_series_aggregation,
    )
    tatqa_cases = load_tatqa_cases(
        tatqa / "dataset_raw/tatqa_dataset_dev.json",
        enable_predicate_cardinality=enable_predicate_cardinality,
        predicate_taxonomy_path=(
            tatqa.parents[1]
            / "c3_unsupported_operator_triage_v1"
            / "per_case_taxonomy.jsonl"
        ),
    )
    cases = (*finqa_cases, *tatqa_cases)
    ids = [(case.dataset, case.case_id) for case in cases]
    duplicate_count = len(ids) - len(set(ids))
    with deny_network() as first_network:
        first = run_cases(cases)
        finqa_score = score_finqa_predictions(finqa_cases, first, scorer_path=finqa / "code/evaluate/evaluate.py")
        tatqa_score = score_tatqa_predictions(tatqa_cases, first)
    with deny_network() as second_network:
        second = run_cases(cases)
    first_bytes, second_bytes = _record_bytes(first), _record_bytes(second)
    equal = first_bytes == second_bytes
    datasets = {"finqa": _metrics(first, "finqa"), "tatqa": _metrics(first, "tatqa"), "combined": _metrics(first, None)}
    datasets["finqa"]["native_scorer"] = dict(finqa_score)
    datasets["finqa"].update(_native_scorer_consistency(first, "finqa", finqa_score))
    datasets["finqa"].update({
        "finqa_native_score": finqa_score["native_score"],
        "finqa_internal_equivalent_score": finqa_score["internal_equivalent_score"],
        "finqa_scorer_parity_delta": finqa_score["parity_delta"],
    })
    datasets["tatqa"]["native_scorer"] = dict(tatqa_score)
    datasets["tatqa"].update(_native_scorer_consistency(first, "tatqa", tatqa_score))
    datasets["tatqa"].update({
        "tatqa_native_score": tatqa_score["native_score"],
        "tatqa_internal_equivalent_score": tatqa_score["internal_equivalent_score"],
        "tatqa_scorer_parity_delta": tatqa_score["parity_delta"],
    })
    combined_score = {
        "prediction_count": int(finqa_score["prediction_count"])
        + int(tatqa_score["prediction_count"]),
        "native_correct_count": int(finqa_score["native_correct_count"])
        + int(tatqa_score["native_correct_count"]),
    }
    datasets["combined"].update(
        _native_scorer_consistency(first, None, combined_score)
    )
    bottleneck_report, primary = _bottlenecks(first)
    usage_keys = ("prompt_" + "tokens", "completion_" + "tokens", "total_" + "tokens")
    usage = {key: sum(int(getattr(row, key)) for row in first) for key in usage_keys}
    provider_calls = sum(row.provider_call_count for row in first)
    legacy_calls = sum(row.legacy_call_count for row in first)
    network_calls = first_network["count"] + second_network["count"]
    parity = all((finqa_score["parity_delta"] == 0, finqa_score["per_prediction_output_mismatch_count"] == 0, tatqa_score["parity_delta"] == 0, tatqa_score["per_prediction_output_mismatch_count"] == 0))
    scorer_consistency = all(
        datasets[name]["native_prediction_count_consistency_delta"] == 0
        and datasets[name]["native_score_consistency_delta"] == 0
        for name in ("finqa", "tatqa", "combined")
    )
    valid = all((len(first) == len(cases), duplicate_count == 0, equal, parity, scorer_consistency, provider_calls == 0, legacy_calls == 0, network_calls == 0, all(value == 0 for value in usage.values())))
    report: dict[str, Any] = {
        "schema_version": "c3-external-oracle-baseline/v1",
        "measurement_mode": MEASUREMENT_MODE,
        "end_to_end_evidence": False,
        "active_route_authority": False,
        "shadow_promotion_authority": False,
        "measurement_valid": valid,
        "source_case_count": len(cases),
        "terminal_record_count": len(first),
        "missing_or_duplicate_case_count": duplicate_count,
        "rerun_record_hash_equal": equal,
        "first_record_sha256": hashlib.sha256(first_bytes).hexdigest(),
        "second_record_sha256": hashlib.sha256(second_bytes).hexdigest(),
        "actual_provider_call_count": provider_calls,
        "actual_legacy_call_count": legacy_calls,
        "actual_network_call_count_during_evaluation": network_calls,
        **usage,
        "datasets": datasets,
        "bottlenecks": bottleneck_report,
        "NEXT_PRIMARY_BOTTLENECK": primary,
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "per_case_records.jsonl").write_bytes(first_bytes)
    (output / "aggregate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "aggregate_report.md").write_text(render_markdown_report(report), encoding="utf-8")
    return first, report


__all__ = ["build_source_manifest", "deny_network", "ensure_source_manifest", "execute_c3_runtime", "render_markdown_report", "run_cases", "run_external_oracle_baseline", "verify_source_manifest"]
