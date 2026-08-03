#!/usr/bin/env python3
"""Measure C3-P SUM Binder activation through the real Factory workflow."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from agent.factory import PipelineFactory
from agent.workflow import BlockingAnswerValidationError
from calculation import SourceBoundNumericSeriesAggregator
from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    QuestionLabel,
    SolverResult,
)
from evidence.c3_numeric_series_binding import SourceBoundSumSeriesBinder
from evidence.structured_tables import load_structured_table_rows
from scripts.evaluate_c3_source_bound_sum_series_binder import _negative_cases
from solvers.calculation import CalculationSolver


SCHEMA_VERSION = "c3-sum-normal-pipeline-activation/v1"
MAP_REVISION = "2026-08-03-r1"
ACTIVE_BOTTLENECK_ID = "B-01"
HYPOTHESIS_ID = "H-01"
BASELINE_PATH = Path(
    "evaluation_artifacts/c3_sum_normal_pipeline_activation_v1/baseline_report.json"
)
BASELINE_SHA256 = "569464fa26e1576f8c86f3dcfd321269b89c3b42393565eaa38124a03ed8813e"
AFTER_PATH = Path(
    "evaluation_artifacts/c3_sum_normal_pipeline_activation_v1/after_report.json"
)
SCOPE_CAVEAT = "仅固定 structured-table SUM 边界，真实项目覆盖未知"

SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "amount_sum",
        "question": "表中三个部门利润分别为10万元、20万元和30万元，请计算合计金额。",
        "doc_id": "sum-profit-doc",
        "headers": ("部门", "利润（万元）"),
        "cells": (("一部", "10"), ("二部", "20"), ("三部", "30")),
        "expected": "60",
    },
    {
        "name": "cost_sum",
        "question": "表中三个项目成本分别为1000元、2500元和500元，请计算总和。",
        "doc_id": "sum-cost-doc",
        "headers": ("项目", "成本（元）"),
        "cells": (("A", "1,000"), ("B", "2,500"), ("C", "500")),
        "expected": "4000",
    },
    {
        "name": "net_change_sum",
        "question": "表中三个区域净变动分别为20万元、负5万元和10万元，请计算共计金额。",
        "doc_id": "sum-change-doc",
        "headers": ("区域", "净变动（万元）"),
        "cells": (("东区", "20"), ("西区", "(5)"), ("南区", "10")),
        "expected": "25",
    },
)


class LocalStructuredRetriever:
    def __init__(self, candidates_by_qid: Mapping[str, Sequence[EvidenceCandidate]]) -> None:
        self.candidates_by_qid = {
            str(qid): tuple(candidates)
            for qid, candidates in candidates_by_qid.items()
        }

    def retrieve(self, question: Question, classification: ClassificationResult):
        return self.candidates_by_qid[question.qid]


def _table_html(
    headers: Sequence[str], cells: Sequence[Sequence[str]]
) -> str:
    header_html = "".join(f"<th>{value}</th>" for value in headers)
    row_html = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in cells
    )
    return f"<table><tr>{header_html}</tr>{row_html}</table>"


def _force_clients_none(workflow: Any) -> None:
    routed = workflow.solver
    solvers = list((getattr(routed, "solvers", {}) or {}).values())
    solvers.extend(
        [
            getattr(routed, "default_solver", None),
            getattr(workflow, "fallback_solver", None),
        ]
    )
    for solver in solvers:
        if solver is None:
            continue
        if hasattr(solver, "llm_client"):
            solver.llm_client = None
        if hasattr(solver, "fallback_llm_client"):
            solver.fallback_llm_client = None


def _read_baseline() -> dict[str, Any]:
    path = ROOT / BASELINE_PATH
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BASELINE_SHA256:
        raise ValueError(
            f"baseline hash changed: expected={BASELINE_SHA256} actual={digest}"
        )
    baseline = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "correct_deterministic_activation_count": 0,
        "binder_call_count": 0,
        "aggregator_call_count": 0,
        "calculation_solver_entered_count": 3,
        "case_count": 3,
        "provider_calls": 0,
        "legacy_calls": 0,
        "total_tokens": 0,
    }
    for key, value in expected.items():
        if baseline.get(key) != value:
            raise ValueError(f"baseline fact changed: {key}={baseline.get(key)!r}")
    return baseline


def _candidate_from_row(row: Any) -> EvidenceCandidate:
    return EvidenceCandidate(
        domain=row.domain,
        doc_id=row.doc_id,
        source=row.canonical_source,
        text=row.normalized_row_text,
        retriever="mineru_structured_table",
        metadata={
            **row.to_dict(),
            "source_kind": "mineru_structured_table",
            "structured_table_evidence": True,
        },
    )


def _generic_freeform_bundle(bundle: EvidenceBundle) -> EvidenceBundle:
    raw = dict(bundle.question.raw or {})
    raw.update({"_input_adapter": "canonical_question_v1", "split": ""})
    question = replace(
        bundle.question,
        answer_format="freeform",
        options={},
        raw=raw,
        submission_slot_count=None,
        submission_slot_contracts=(),
    )
    return replace(bundle, question=question)


@contextmanager
def _stable_fixture_root() -> Iterable[Path]:
    """Use one deterministic local fixture path so report lineage is repeatable."""
    root = Path(tempfile.gettempdir()) / "findocqa-c3-sum-normal-pipeline-activation-v1"
    root.mkdir(parents=True, exist_ok=True)
    yield root


@contextmanager
def _deny_network() -> Iterable[dict[str, int]]:
    counter = {"count": 0}
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect

    def blocked_create_connection(*args, **kwargs):
        counter["count"] += 1
        raise RuntimeError("network_disabled_for_c3_sum_activation_evaluation")

    def blocked_connect(self, *args, **kwargs):
        counter["count"] += 1
        raise RuntimeError("network_disabled_for_c3_sum_activation_evaluation")

    socket.create_connection = blocked_create_connection
    socket.socket.connect = blocked_connect
    try:
        yield counter
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect


def _solver_result_dict(result: SolverResult) -> dict[str, Any]:
    return {
        "qid": result.qid,
        "answer": result.answer,
        "solver": result.solver,
        "raw_output": result.raw_output,
        "confidence": result.confidence,
        "metadata": dict(result.metadata or {}),
    }


def _build_factory_positive_records(
    counters: dict[str, int],
) -> tuple[list[dict[str, Any]], bool]:
    factory = PipelineFactory(
        config={"runtime": {"fallback_enabled": False}},
        project_root=ROOT,
        artifact_mode="evaluation-only",
    )
    prepared = {
        spec["name"]: factory.prepare_question(str(spec["question"]))
        for spec in SPECS
    }

    records: list[dict[str, Any]] = []
    with _stable_fixture_root() as table_root:
        candidates_by_qid: dict[str, tuple[EvidenceCandidate, ...]] = {}
        for spec in SPECS:
            auto = table_root / "financial_reports" / spec["doc_id"] / "auto"
            auto.mkdir(parents=True, exist_ok=True)
            payload = [
                {
                    "type": "table",
                    "page_idx": 0,
                    "table_body": _table_html(spec["headers"], spec["cells"]),
                }
            ]
            (auto / f"{spec['doc_id']}_content_list_v2.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            rows = load_structured_table_rows(
                table_root, "financial_reports", str(spec["doc_id"])
            )
            question = prepared[spec["name"]].question
            candidates_by_qid[question.qid] = tuple(
                _candidate_from_row(row) for row in rows
            )

        workflow = factory.build_workflow(writer=None)
        workflow.retriever = LocalStructuredRetriever(candidates_by_qid)
        _force_clients_none(workflow)

        calculation_solver = workflow.solver.solvers["calculation"]
        original_calculation_solve = calculation_solver.solve

        def counted_calculation_solve(bundle: EvidenceBundle):
            counters["calculation_solver"] += 1
            return original_calculation_solve(bundle)

        calculation_solver.solve = counted_calculation_solve

        for spec in SPECS:
            question = prepared[spec["name"]].question
            before = dict(counters)
            result = None
            blocked = None
            try:
                result = workflow.process_one(question)
            except BlockingAnswerValidationError as exc:
                blocked = exc

            if result is None:
                exception_metadata = dict(blocked.metadata if blocked else {})
                solver_metadata = dict(exception_metadata.get("solver_metadata") or {})
                answer = ""
                final_state = str(exception_metadata.get("final_state") or "blocked")
                block_reason = blocked.reason if blocked else "unknown"
                solver_name = str(exception_metadata.get("solver") or "")
                workflow_metadata: dict[str, Any] = exception_metadata
            else:
                solver_metadata = dict(result.solver_result.metadata or {})
                answer = result.answer
                final_state = str(result.metadata.get("final_state") or "")
                block_reason = ""
                solver_name = result.solver_result.solver
                workflow_metadata = dict(result.metadata or {})

            binder_calls = counters["binder"] - before["binder"]
            aggregator_calls = counters["aggregator"] - before["aggregator"]
            calculation_calls = (
                counters["calculation_solver"] - before["calculation_solver"]
            )
            source_refs = list(solver_metadata.get("source_refs") or [])
            binding_trace = list(solver_metadata.get("binding_trace") or [])
            result_trace = list(solver_metadata.get("result_trace") or [])
            source_lineage_complete = bool(
                solver_metadata.get("source_lineage_complete") is True
                and len(source_refs) == len(candidates_by_qid[question.qid])
                and all(
                    isinstance(item, Mapping)
                    and str(item.get("doc_id") or "")
                    and str(item.get("source") or "")
                    for item in source_refs
                )
            )
            correct = bool(
                calculation_calls == 1
                and binder_calls == 1
                and aggregator_calls == 1
                and answer == spec["expected"]
                and solver_metadata.get("answer_source")
                == "c3_source_bound_sum_series"
                and final_state == "accepted"
                and source_lineage_complete
            )
            records.append(
                {
                    "case": spec["name"],
                    "question": question.text,
                    "expected": spec["expected"],
                    "answer": answer,
                    "answer_source": str(
                        solver_metadata.get("answer_source") or ""
                    ),
                    "routed_solver": solver_name,
                    "calculation_solver_calls": calculation_calls,
                    "binder_calls": binder_calls,
                    "aggregator_calls": aggregator_calls,
                    "correct_deterministic_activation": correct,
                    "final_state": final_state,
                    "block_reason": block_reason,
                    "classification_labels": [
                        label.value
                        for label in workflow.classifier.classify(question).labels
                    ],
                    "query_understanding": dict(
                        question.raw.get("_query_understanding") or {}
                    ),
                    "structured_candidate_count": len(
                        candidates_by_qid[question.qid]
                    ),
                    "provider_calls": int(
                        solver_metadata.get("provider_call_count", 0) or 0
                    ),
                    "legacy_calls": int(
                        solver_metadata.get("legacy_execution_invoked") is True
                    ),
                    "total_tokens": int(
                        solver_metadata.get("total_tokens", 0) or 0
                    ),
                    "request_contract": str(
                        solver_metadata.get("request_contract") or ""
                    ),
                    "binding_trace": binding_trace,
                    "result_trace": result_trace,
                    "source_refs": source_refs,
                    "source_lineage_complete": source_lineage_complete,
                    "gate_status": str(solver_metadata.get("gate_status") or ""),
                    "audit_reasons": list(
                        solver_metadata.get("audit_reasons") or []
                    ),
                    "solver_metadata": solver_metadata,
                    "workflow_integrity": {
                        "final_state": workflow_metadata.get("final_state"),
                        "grounded": workflow_metadata.get("grounded"),
                        "solver_lineage_complete": (
                            workflow_metadata.get("solver_lineage_complete")
                            if workflow_metadata.get("solver_lineage_complete")
                            is not None
                            else solver_metadata.get("source_lineage_complete")
                        ),
                        "blocking_reasons": list(
                            workflow_metadata.get("blocking_reasons") or []
                        ),
                    },
                }
            )

    return records, True


def _build_negative_guardrails(
    counters: dict[str, int],
) -> list[dict[str, Any]]:
    solver = CalculationSolver(llm_client=None, fallback_llm_client=None)
    records: list[dict[str, Any]] = []
    for case_name, original_bundle, expected_reason in _negative_cases():
        bundle = _generic_freeform_bundle(original_bundle)
        isolated = SourceBoundSumSeriesBinder().bind(bundle)
        before = dict(counters)
        result = solver.solve(bundle)
        binder_calls = counters["binder"] - before["binder"]
        aggregator_calls = counters["aggregator"] - before["aggregator"]
        answer_source = str(result.metadata.get("answer_source") or "")
        false_activation = bool(
            answer_source == "c3_source_bound_sum_series" or aggregator_calls > 0
        )
        records.append(
            {
                "case": case_name,
                "expected_reason": expected_reason,
                "binder_ready": isolated.ready,
                "binder_reasons": list(isolated.reasons),
                "expected_reason_preserved": expected_reason in isolated.reasons,
                "solver_answer": result.answer,
                "answer_source": answer_source,
                "binder_calls": binder_calls,
                "aggregator_calls": aggregator_calls,
                "false_deterministic_activation": false_activation,
                "provider_calls": int(
                    result.metadata.get("provider_call_count", 0) or 0
                ),
                "legacy_calls": int(
                    result.metadata.get("legacy_execution_invoked") is True
                ),
                "total_tokens": int(result.metadata.get("total_tokens", 0) or 0),
            }
        )
    return records


def _build_path_parity(counters: dict[str, int]) -> dict[str, Any]:
    base_bundle = _generic_freeform_bundle(_negative_cases()[0][1])
    non_sum_bundle = replace(
        base_bundle,
        question=replace(
            base_bundle.question,
            text="请列出三个部门各自的利润。",
        ),
    )
    solver = CalculationSolver(llm_client=None, fallback_llm_client=None)
    expected_non_sum = solver._solve_freeform(non_sum_bundle)
    before_non_sum = dict(counters)
    actual_non_sum = solver.solve(non_sum_bundle)
    non_sum_binder_calls = counters["binder"] - before_non_sum["binder"]
    non_sum_aggregator_calls = counters["aggregator"] - before_non_sum["aggregator"]

    insurance_question = Question(
        qid="insurance-route-parity",
        domain="insurance",
        text="根据保险条款计算给付结果。",
        options={"A": "10", "B": "20"},
        answer_format="mcq",
        doc_ids=(),
        raw={},
    )
    insurance_bundle = EvidenceBundle(
        question=insurance_question,
        classification=ClassificationResult(labels=(QuestionLabel.CALCULATION,)),
        candidates=(),
        prompt_context="",
        estimated_tokens=0,
    )
    sentinel = SolverResult(
        qid=insurance_question.qid,
        answer="A",
        solver="calculation",
        raw_output="insurance_route_sentinel",
        confidence=1.0,
        metadata={
            "answer_source": "deterministic_insurance_calculation",
            "provider_call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    )
    insurance_solver = CalculationSolver(llm_client=None, fallback_llm_client=None)
    insurance_solver._solve_insurance_calculation = lambda bundle: sentinel
    before_insurance = dict(counters)
    actual_insurance = insurance_solver.solve(insurance_bundle)
    insurance_binder_calls = counters["binder"] - before_insurance["binder"]
    insurance_aggregator_calls = counters["aggregator"] - before_insurance["aggregator"]

    return {
        "non_sum": {
            "equivalent": actual_non_sum == expected_non_sum,
            "expected": _solver_result_dict(expected_non_sum),
            "actual": _solver_result_dict(actual_non_sum),
            "binder_calls": non_sum_binder_calls,
            "aggregator_calls": non_sum_aggregator_calls,
        },
        "insurance": {
            "equivalent": actual_insurance == sentinel,
            "expected": _solver_result_dict(sentinel),
            "actual": _solver_result_dict(actual_insurance),
            "binder_calls": insurance_binder_calls,
            "aggregator_calls": insurance_aggregator_calls,
        },
    }


def build_report() -> dict[str, Any]:
    baseline = _read_baseline()
    counters = {"calculation_solver": 0, "binder": 0, "aggregator": 0}
    original_bind = SourceBoundSumSeriesBinder.bind
    original_execute = SourceBoundNumericSeriesAggregator.execute

    def counted_bind(self, bundle):
        counters["binder"] += 1
        return original_bind(self, bundle)

    def counted_execute(self, request):
        counters["aggregator"] += 1
        return original_execute(self, request)

    SourceBoundSumSeriesBinder.bind = counted_bind
    SourceBoundNumericSeriesAggregator.execute = counted_execute
    try:
        with _deny_network() as network_counter:
            positive_records, factory_called = _build_factory_positive_records(counters)
            negative_guardrails = _build_negative_guardrails(counters)
            path_parity = _build_path_parity(counters)
    finally:
        SourceBoundSumSeriesBinder.bind = original_bind
        SourceBoundNumericSeriesAggregator.execute = original_execute

    correct_count = sum(
        int(record["correct_deterministic_activation"])
        for record in positive_records
    )
    positive_binder_calls = sum(record["binder_calls"] for record in positive_records)
    positive_aggregator_calls = sum(
        record["aggregator_calls"] for record in positive_records
    )
    false_activation_count = sum(
        int(record["false_deterministic_activation"])
        for record in negative_guardrails
    )
    rejected_aggregator_calls = sum(
        record["aggregator_calls"] for record in negative_guardrails
    )
    reasons_preserved = all(
        record["expected_reason_preserved"] for record in negative_guardrails
    )
    source_lineage_complete = all(
        record["source_lineage_complete"] for record in positive_records
    )
    provider_calls = sum(
        record["provider_calls"] for record in positive_records + negative_guardrails
    )
    legacy_calls = sum(
        record["legacy_calls"] for record in positive_records + negative_guardrails
    )
    total_tokens = sum(
        record["total_tokens"] for record in positive_records + negative_guardrails
    )
    network_calls = int(network_counter["count"])
    guardrail_pass = bool(
        len(negative_guardrails) == 33
        and false_activation_count == 0
        and rejected_aggregator_calls == 0
        and reasons_preserved
        and path_parity["non_sum"]["equivalent"] is True
        and path_parity["insurance"]["equivalent"] is True
        and provider_calls == legacy_calls == network_calls == total_tokens == 0
    )
    measurement_valid = bool(
        factory_called
        and len(positive_records) == 3
        and correct_count == 3
        and positive_binder_calls == 3
        and positive_aggregator_calls == 3
        and source_lineage_complete
        and guardrail_pass
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "map_revision": MAP_REVISION,
        "active_bottleneck_id": ACTIVE_BOTTLENECK_ID,
        "hypothesis_id": HYPOTHESIS_ID,
        "baseline_artifact": BASELINE_PATH.as_posix(),
        "baseline_sha256": BASELINE_SHA256,
        "before": {
            "case_count": baseline["case_count"],
            "calculation_solver_entered_count": baseline[
                "calculation_solver_entered_count"
            ],
            "correct_deterministic_activation_count": baseline[
                "correct_deterministic_activation_count"
            ],
            "binder_call_count": baseline["binder_call_count"],
            "aggregator_call_count": baseline["aggregator_call_count"],
        },
        "after": {
            "case_count": len(positive_records),
            "calculation_solver_entered_count": sum(
                record["calculation_solver_calls"] for record in positive_records
            ),
            "correct_deterministic_activation_count": correct_count,
            "binder_call_count": positive_binder_calls,
            "aggregator_call_count": positive_aggregator_calls,
        },
        "delta": {
            "correct_deterministic_activation_count": (
                correct_count
                - baseline["correct_deterministic_activation_count"]
            ),
            "binder_call_count": positive_binder_calls - baseline["binder_call_count"],
            "aggregator_call_count": (
                positive_aggregator_calls - baseline["aggregator_call_count"]
            ),
        },
        "positive_records": positive_records,
        "negative_guardrails": negative_guardrails,
        "negative_guardrail_count": len(negative_guardrails),
        "false_deterministic_activation_count": false_activation_count,
        "aggregator_calls_on_rejected_binding": rejected_aggregator_calls,
        "stable_reasons_preserved": reasons_preserved,
        "path_parity": path_parity,
        "source_lineage_complete": source_lineage_complete,
        "provider_calls": provider_calls,
        "legacy_calls": legacy_calls,
        "network_calls": network_calls,
        "total_tokens": total_tokens,
        "guardrail_result": "PASS" if guardrail_pass else "FAIL",
        "scope_caveat": SCOPE_CAVEAT,
        "measurement_valid": measurement_valid,
    }
    return json.loads(json.dumps(report, ensure_ascii=False))


def validate_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected activation report schema")
    if report.get("map_revision") != MAP_REVISION:
        raise ValueError("map revision mismatch")
    if report.get("active_bottleneck_id") != ACTIVE_BOTTLENECK_ID:
        raise ValueError("active bottleneck mismatch")
    if report.get("hypothesis_id") != HYPOTHESIS_ID:
        raise ValueError("hypothesis mismatch")
    before = report.get("before")
    after = report.get("after")
    delta = report.get("delta")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise ValueError("before/after missing")
    if before.get("correct_deterministic_activation_count") != 0:
        raise ValueError("frozen baseline activation mismatch")
    if before.get("binder_call_count") != 0:
        raise ValueError("frozen baseline Binder count mismatch")
    if before.get("aggregator_call_count") != 0:
        raise ValueError("frozen baseline aggregator count mismatch")
    if after.get("correct_deterministic_activation_count") != 3:
        raise ValueError("after activation threshold not met")
    if after.get("binder_call_count") != 3:
        raise ValueError("after Binder count mismatch")
    if after.get("aggregator_call_count") != 3:
        raise ValueError("after aggregator count mismatch")
    if not isinstance(delta, Mapping) or delta.get(
        "correct_deterministic_activation_count"
    ) != 3:
        raise ValueError("activation delta mismatch")
    positives = report.get("positive_records")
    negatives = report.get("negative_guardrails")
    if not isinstance(positives, list) or len(positives) != 3:
        raise ValueError("positive records mismatch")
    if not isinstance(negatives, list) or len(negatives) != 33:
        raise ValueError("negative guardrail count mismatch")
    if report.get("false_deterministic_activation_count") != 0:
        raise ValueError("false deterministic activation detected")
    if report.get("aggregator_calls_on_rejected_binding") != 0:
        raise ValueError("aggregator invoked on rejected binding")
    if report.get("stable_reasons_preserved") is not True:
        raise ValueError("Binder reason compatibility regressed")
    if report.get("source_lineage_complete") is not True:
        raise ValueError("source lineage incomplete")
    parity = report.get("path_parity")
    if not isinstance(parity, Mapping):
        raise ValueError("path parity missing")
    if parity.get("non_sum", {}).get("equivalent") is not True:
        raise ValueError("non-SUM path changed")
    if parity.get("insurance", {}).get("equivalent") is not True:
        raise ValueError("insurance route changed")
    if any(
        int(report.get(key, -1)) != 0
        for key in ("provider_calls", "legacy_calls", "network_calls", "total_tokens")
    ):
        raise ValueError("zero-call invariant violated")
    if report.get("guardrail_result") != "PASS":
        raise ValueError("guardrail result failed")
    if report.get("scope_caveat") != SCOPE_CAVEAT:
        raise ValueError("scope caveat mismatch")
    if report.get("measurement_valid") is not True:
        raise ValueError("activation measurement invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=AFTER_PATH.as_posix())
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_report()
    validate_report(report)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "C3_SUM_NORMAL_PIPELINE_ACTIVATION",
            "before=0/3",
            f"after={report['after']['correct_deterministic_activation_count']}/3",
            f"delta=+{report['delta']['correct_deterministic_activation_count']}",
            f"negative={report['negative_guardrail_count']}/33",
            f"false_activation={report['false_deterministic_activation_count']}",
            f"guardrail={report['guardrail_result']}",
            f"provider_calls={report['provider_calls']}",
            f"legacy_calls={report['legacy_calls']}",
            f"network_calls={report['network_calls']}",
            f"tokens={report['total_tokens']}",
            f"measurement_valid={str(report['measurement_valid']).lower()}",
        )
        for record in report["positive_records"]:
            print(
                record["case"],
                f"answer={record['answer']}",
                f"binder={record['binder_calls']}",
                f"aggregator={record['aggregator_calls']}",
                f"lineage={str(record['source_lineage_complete']).lower()}",
                f"final_state={record['final_state']}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
