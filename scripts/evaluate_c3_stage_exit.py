#!/usr/bin/env python3
"""Audit C3-M/N/O activation without changing any product runtime.

This evaluation-only script uses local, dataset-agnostic examples. It separates:

- Oracle runtime execution;
- explicit caller-supplied product requests;
- the existing C3 shadow observer;
- the normal FinDocQA question/workflow route.

It never calls a model, provider, network service, or benchmark-specific branch.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.factory import PipelineFactory
from agent.workflow import BlockingAnswerValidationError
from calculation import (
    AggregationOutputOperation,
    AggregationSelector,
    ExecutionGateFact,
    FormulaSourceRef,
    SeriesAggregationOutputSpec,
    SourceBoundNumericSeries,
    SourceBoundNumericSeriesAggregationRequest,
    SourceBoundNumericSeriesAggregator,
    SourceBoundNumericSeriesItem,
    SourceBoundTableMember,
    SourceBoundTableMemberCollection,
    SourceBoundTablePredicateCardinalityCounter,
    SourceBoundTablePredicateCardinalityRequest,
    SourceBoundTableSectionCardinalityCounter,
    SourceBoundTableSectionCardinalityRequest,
    SourceSeriesBindingStatus,
    TablePredicateOperator,
    TableSectionAxisType,
)
from contracts import EvidenceBundle, EvidenceCandidate, SolverResult
from evaluation.external_benchmarks.c3_oracle_baseline import (
    deny_network,
    execute_c3_runtime,
)
from evaluation.external_benchmarks.finqa_adapter import FinQASeriesOracleRuntime
from evidence.structured_tables import load_structured_table_rows
from evaluation.external_benchmarks.tatqa_adapter import (
    TATQAPredicateCardinalityOracleRuntime,
    TATQASectionCardinalityOracleRuntime,
)
from solvers.calculation import CalculationSolver


SCHEMA_VERSION = "c3-stage-exit-gate/v1"
VALID_ACTIVATION_STATES = {
    "ACTIVE",
    "EXPLICIT_CALLER_ONLY",
    "SHADOW_ONLY",
    "ORACLE_ONLY",
    "NOT_WIRED",
    "BLOCKED_BY_MISSING_BINDING",
    "BLOCKED_BY_MISSING_ROUTING",
    "BLOCKED_BY_MISSING_EVIDENCE",
}
CAPABILITY_ORDER = (
    "SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION",
    "SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY",
    "SOURCE_BOUND_TABLE_SECTION_CARDINALITY",
)
LAYER_ORDER = (
    "ORACLE_RUNTIME",
    "EXPLICIT_C3_CALL",
    "SHADOW_OBSERVER",
    "NORMAL_PIPELINE",
)
SOURCE_OBJECT = "document://local-audit/table/1"
SNAPSHOT_ROOT = Path(
    "evaluation_artifacts/c3_external_oracle_baseline_v1"
)
C3O_SNAPSHOT = SNAPSHOT_ROOT / "c3o_source_bound_table_section_cardinality_v1"
TAXONOMY = Path(
    "evaluation_artifacts/c3_unsupported_operator_triage_v1/per_case_taxonomy.jsonl"
)


class _LocalEvidenceRetriever:
    """Return local ordinary or structured evidence for a prepared question."""

    def __init__(
        self,
        evidence_by_qid: Mapping[str, str | Sequence[EvidenceCandidate]],
    ) -> None:
        self.evidence_by_qid = dict(evidence_by_qid)

    def retrieve(self, question, classification):
        evidence = self.evidence_by_qid[question.qid]
        if not isinstance(evidence, str):
            return tuple(evidence)
        return (
            EvidenceCandidate(
                domain=question.domain,
                doc_id=f"local-doc-{question.qid[-8:]}",
                source=f"{SOURCE_OBJECT}#page=1",
                text=evidence,
                metadata={"page_number": 1, "audit_fixture": True},
            ),
        )


def _table_html(
    headers: Sequence[str], rows: Sequence[Sequence[str]]
) -> str:
    header_html = "".join(f"<th>{value}</th>" for value in headers)
    row_html = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><tr>{header_html}</tr>{row_html}</table>"


def _candidate_from_structured_row(row: Any) -> EvidenceCandidate:
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
            "audit_fixture": True,
        },
    )


def _structured_sum_candidates() -> tuple[EvidenceCandidate, ...]:
    """Build the C3-M probe through the real structured-table loader."""
    fixture_root = (
        Path(tempfile.gettempdir()) / "findocqa-c3-stage-exit-post-h01"
    )
    doc_id = "stage-exit-sum-profit-doc"
    auto = fixture_root / "financial_reports" / doc_id / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "type": "table",
            "page_idx": 0,
            "table_body": _table_html(
                ("部门", "利润（万元）"),
                (("一部", "10"), ("二部", "20"), ("三部", "30")),
            ),
        }
    ]
    (auto / f"{doc_id}_content_list_v2.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    rows = load_structured_table_rows(
        fixture_root, "financial_reports", doc_id
    )
    return tuple(_candidate_from_structured_row(row) for row in rows)


@contextmanager
def _count_product_executor_calls() -> Iterable[dict[str, int]]:
    """Instrument the three product APIs only for the normal-pipeline probes."""

    classes = {
        CAPABILITY_ORDER[0]: SourceBoundNumericSeriesAggregator,
        CAPABILITY_ORDER[1]: SourceBoundTablePredicateCardinalityCounter,
        CAPABILITY_ORDER[2]: SourceBoundTableSectionCardinalityCounter,
    }
    counts = {name: 0 for name in classes}
    originals: dict[str, Callable[..., Any]] = {}

    for name, cls in classes.items():
        original = cls.execute
        originals[name] = original

        def wrapper(self, request, *, _name=name, _original=original):
            counts[_name] += 1
            return _original(self, request)

        cls.execute = wrapper  # type: ignore[method-assign]
    try:
        yield counts
    finally:
        for name, cls in classes.items():
            cls.execute = originals[name]  # type: ignore[method-assign]


@contextmanager
def _count_request_assembly_calls() -> Iterable[dict[str, int]]:
    """Observe construction of the three source-bound request contracts."""

    classes = {
        CAPABILITY_ORDER[0]: SourceBoundNumericSeriesAggregationRequest,
        CAPABILITY_ORDER[1]: SourceBoundTablePredicateCardinalityRequest,
        CAPABILITY_ORDER[2]: SourceBoundTableSectionCardinalityRequest,
    }
    counts = {name: 0 for name in classes}
    originals: dict[str, Callable[..., Any]] = {}

    for name, cls in classes.items():
        original = cls.__init__
        originals[name] = original

        def wrapper(self, *args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            _original(self, *args, **kwargs)

        cls.__init__ = wrapper  # type: ignore[method-assign]
    try:
        yield counts
    finally:
        for name, cls in classes.items():
            cls.__init__ = originals[name]  # type: ignore[method-assign]


def _force_factory_solver_clients_none(workflow: Any) -> dict[str, Any]:
    """Disable every factory-created provider client without replacing solvers."""

    routed_solver = workflow.solver
    solver_entries: dict[str, Any] = dict(getattr(routed_solver, "solvers", {}) or {})
    solver_entries["default"] = getattr(routed_solver, "default_solver", None)
    solver_entries["workflow_fallback"] = getattr(workflow, "fallback_solver", None)
    observations: dict[str, dict[str, Any]] = {}
    seen: set[int] = set()
    for route, solver in solver_entries.items():
        if solver is None:
            observations[route] = {"solver_class": "", "forced_none": True}
            continue
        duplicate = id(solver) in seen
        seen.add(id(solver))
        had_primary = getattr(solver, "llm_client", None) is not None
        had_fallback = getattr(solver, "fallback_llm_client", None) is not None
        if hasattr(solver, "llm_client"):
            solver.llm_client = None
        if hasattr(solver, "fallback_llm_client"):
            solver.fallback_llm_client = None
        observations[route] = {
            "solver_class": type(solver).__name__,
            "duplicate_instance": duplicate,
            "had_primary_before_override": had_primary,
            "had_fallback_before_override": had_fallback,
            "primary_is_none": getattr(solver, "llm_client", None) is None,
            "fallback_is_none": getattr(solver, "fallback_llm_client", None) is None,
            "forced_none": bool(
                getattr(solver, "llm_client", None) is None
                and getattr(solver, "fallback_llm_client", None) is None
            ),
        }
    return observations


def _derive_normal_status(observation: Mapping[str, Any]) -> str:
    if observation.get("product_executor_invoked") is True and observation.get("correct_result") is True:
        return "ACTIVE"
    if observation.get("calculation_solver_entered") is not True:
        return "BLOCKED_BY_MISSING_ROUTING"
    if observation.get("request_assembly_observed") is True:
        return "NOT_WIRED"
    return "BLOCKED_BY_MISSING_BINDING"


def _derive_shadow_status(observation: Mapping[str, Any]) -> str:
    state = str(observation.get("state") or "")
    if state == "EXECUTED":
        return "SHADOW_ONLY"
    if state == "BLOCKED":
        return (
            "BLOCKED_BY_MISSING_BINDING"
            if observation.get("pipeline_invoked") is True
            else "BLOCKED_BY_MISSING_EVIDENCE"
        )
    if state in {"DISABLED", "NOT_APPLICABLE", "ERROR", ""}:
        return "NOT_WIRED"
    return "NOT_WIRED"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _source_ref(label: str) -> FormulaSourceRef:
    return FormulaSourceRef(
        doc_id="local-audit-document",
        page_number=1,
        source=SOURCE_OBJECT,
        block_id="table-1",
        excerpt=label,
    )


def _numeric_series() -> SourceBoundNumericSeries:
    values = (Decimal("10"), Decimal("20"), Decimal("30"))
    items = tuple(
        SourceBoundNumericSeriesItem(
            position=index,
            value=value,
            unit="万元",
            dimension="currency",
            source_ref=_source_ref(f"部门{index + 1}:{value}"),
            source_coordinate=f"{SOURCE_OBJECT}/r{index + 1}c1",
            source_object_id=SOURCE_OBJECT,
            header_label=f"部门{index + 1}",
        )
        for index, value in enumerate(values)
    )
    return SourceBoundNumericSeries(
        series_id="local-profit-series",
        items=items,
        metric="利润",
        entity="部门",
        source_object_id=SOURCE_OBJECT,
        binding_status=SourceSeriesBindingStatus.EXACT,
        aggregation_range_explicit=True,
        total_components_ambiguity=False,
    )


def _aggregation_request() -> SourceBoundNumericSeriesAggregationRequest:
    return SourceBoundNumericSeriesAggregationRequest(
        series=_numeric_series(),
        selectors=(AggregationSelector.SUM,),
        output=SeriesAggregationOutputSpec(
            operation=AggregationOutputOperation.SELECTOR,
            operands=(AggregationSelector.SUM,),
            output_kind="SCALAR",
            output_semantics="number",
        ),
        question_aggregation_match=ExecutionGateFact(True),
    )


def _predicate_request() -> SourceBoundTablePredicateCardinalityRequest:
    base = _numeric_series()
    predicate_items = tuple(
        SourceBoundNumericSeriesItem(
            position=item.position,
            value=value,
            unit=item.unit,
            dimension=item.dimension,
            source_ref=_source_ref(f"项目{item.position + 1}:{value}"),
            source_coordinate=item.source_coordinate,
            source_object_id=item.source_object_id,
            header_label=f"项目{item.position + 1}",
        )
        for item, value in zip(
            base.items,
            (Decimal("25"), Decimal("50"), Decimal("75")),
        )
    )
    collection = SourceBoundNumericSeries(
        series_id="local-project-amounts",
        items=predicate_items,
        metric="项目金额",
        entity="项目",
        source_object_id=SOURCE_OBJECT,
        binding_status=SourceSeriesBindingStatus.EXACT,
        aggregation_range_explicit=True,
        total_components_ambiguity=False,
    )
    return SourceBoundTablePredicateCardinalityRequest(
        collection=collection,
        operator=TablePredicateOperator.GREATER_THAN,
        threshold=Decimal("50"),
        threshold_unit="万元",
        threshold_dimension="currency",
        question_predicate_match=ExecutionGateFact(True),
    )


def _section_request() -> SourceBoundTableSectionCardinalityRequest:
    labels = ("董事长", "总经理", "财务负责人", "风控负责人")
    members = tuple(
        SourceBoundTableMember(
            position=index,
            member_label=label,
            source_ref=_source_ref(label),
            source_coordinate=f"{SOURCE_OBJECT}/r{index + 1}c0",
            source_object_id=SOURCE_OBJECT,
        )
        for index, label in enumerate(labels)
    )
    collection = SourceBoundTableMemberCollection(
        collection_id="local-executive-section",
        members=members,
        source_object_id=SOURCE_OBJECT,
        axis_type=TableSectionAxisType.ROWS_IN_BOUND_SECTION,
        binding_status=SourceSeriesBindingStatus.EXACT,
        range_explicit=True,
        boundary_rows_excluded=True,
    )
    return SourceBoundTableSectionCardinalityRequest(
        collection=collection,
        question_cardinality_match=ExecutionGateFact(True),
    )


def _capability_specs() -> dict[str, dict[str, Any]]:
    return {
        CAPABILITY_ORDER[0]: {
            "short_name": "C3-M",
            "question": "表中三个部门利润分别为10万元、20万元和30万元，请计算合计金额。",
            "evidence": "部门1利润10万元；部门2利润20万元；部门3利润30万元。",
            "request": _aggregation_request(),
            "executor": SourceBoundNumericSeriesAggregator,
            "expected": "60",
            "input_contract": "SourceBoundNumericSeriesAggregationRequest",
            "failure_boundary": [
                "EXACT source binding",
                "explicit complete range",
                "single source object",
                "consistent unit and dimension",
                "supported selector and question match",
            ],
            "historical_gain": {
                "new_representable": 33,
                "snapshot": "c3m_source_bound_numeric_series_aggregation_v1",
            },
        },
        CAPABILITY_ORDER[1]: {
            "short_name": "C3-N",
            "question": "三个项目金额分别为25万元、50万元和75万元，请计算严格大于50万元的项目有几个。",
            "evidence": "项目1金额25万元；项目2金额50万元；项目3金额75万元。",
            "request": _predicate_request(),
            "executor": SourceBoundTablePredicateCardinalityCounter,
            "expected": "1",
            "input_contract": "SourceBoundTablePredicateCardinalityRequest",
            "failure_boundary": [
                "EXACT source-bound numeric collection",
                "strict GREATER_THAN or LESS_THAN only",
                "finite Decimal threshold",
                "unit and dimension match",
                "complete unique coordinates and question match",
            ],
            "historical_gain": {
                "new_representable": 16,
                "snapshot": "c3n_source_bound_table_predicate_cardinality_v1",
            },
        },
        CAPABILITY_ORDER[2]: {
            "short_name": "C3-O",
            "question": "截至2020年，请计算表格中高管名单区段共有多少名高管。",
            "evidence": "高管名单：董事长、总经理、财务负责人、风控负责人。",
            "request": _section_request(),
            "executor": SourceBoundTableSectionCardinalityCounter,
            "expected": "4",
            "input_contract": "SourceBoundTableSectionCardinalityRequest",
            "failure_boundary": [
                "EXACT source-bound member collection",
                "explicit complete range",
                "boundary rows excluded",
                "ordered unique source coordinates",
                "supported axis and question match",
            ],
            "historical_gain": {
                "new_representable": 3,
                "snapshot": "c3o_source_bound_table_section_cardinality_v1",
            },
        },
    }


def _oracle_runtime(name: str, spec: Mapping[str, Any]):
    common = {
        "dataset": "local_audit",
        "case_id": f"local_{spec['short_name'].lower()}",
        "question": spec["question"],
        "expression": name.lower(),
        "variables": (),
        "source_id": SOURCE_OBJECT,
        "native_program": "local_source_bound_request",
        "scale": "",
        "output_multiplier": "1",
    }
    if name == CAPABILITY_ORDER[0]:
        return FinQASeriesOracleRuntime(
            **common,
            aggregation_request=spec["request"],
            official_table_program="local_sum",
        )
    if name == CAPABILITY_ORDER[1]:
        return TATQAPredicateCardinalityOracleRuntime(
            **common,
            predicate_request=spec["request"],
            oracle_axis="LOCAL_NUMERIC_ROWS",
        )
    return TATQASectionCardinalityOracleRuntime(
        **common,
        section_request=spec["request"],
        oracle_axis=TableSectionAxisType.ROWS_IN_BOUND_SECTION.value,
    )


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "answer": str(result.value) if result.ok else "",
        "error": result.error if not result.ok else "",
        "gate_status": result.gate_status,
        "trace": [dict(row) for row in result.trace],
        "source_ref_count": len(result.source_refs),
        "audit_reasons": list(result.audit_reasons),
    }


def _probe_oracle_and_explicit(
    specs: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    oracle: dict[str, dict[str, Any]] = {}
    explicit: dict[str, dict[str, Any]] = {}
    for name in CAPABILITY_ORDER:
        spec = specs[name]
        runtime = _oracle_runtime(name, spec)
        observation = execute_c3_runtime(runtime)
        oracle[name] = {
            "status": "ACTIVE",
            "entry": "execute_c3_runtime",
            "key_call_path": [
                "evaluation.external_benchmarks.c3_oracle_baseline.execute_c3_runtime",
                spec["executor"].__name__ + ".execute",
            ],
            "correct_result": observation.ok
            and observation.answer == spec["expected"],
            "answer": observation.answer,
            "expected": spec["expected"],
            "manual_program_or_request": True,
            "provider_calls": observation.provider_call_count,
            "legacy_calls": observation.legacy_call_count,
            "network_calls": 0,
            "total_tokens": observation.total_tokens,
            "trace": [dict(row) for row in observation.trace],
            "source_lineage": [dict(row) for row in observation.source_lineage],
            "blocking_module": "",
        }

        result = spec["executor"]().execute(spec["request"])
        payload = _result_payload(result)
        explicit[name] = {
            "status": "EXPLICIT_CALLER_ONLY",
            "entry": spec["executor"].__name__ + ".execute",
            "key_call_path": [
                "caller constructs " + spec["input_contract"],
                spec["executor"].__name__ + ".execute",
            ],
            "correct_result": payload["ok"]
            and payload["answer"] == spec["expected"],
            "answer": payload["answer"],
            "expected": spec["expected"],
            "manual_program_or_request": True,
            "provider_calls": 0,
            "legacy_calls": 0,
            "network_calls": 0,
            "total_tokens": 0,
            "trace": payload["trace"],
            "metadata": {
                "gate_status": payload["gate_status"],
                "source_ref_count": payload["source_ref_count"],
                "audit_reasons": payload["audit_reasons"],
                "explicit_c3_pipeline_product_request_wiring": False,
            },
            "blocking_module": (
                "ExplicitC3Pipeline accepts generic C3InputAssemblyInput; "
                "it does not select or construct this product request"
            ),
        }
    return oracle, explicit


def _probe_normal_pipeline(
    specs: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    factory = PipelineFactory(
        config={
            "pipeline": {"c3_shadow": {"enabled": True}},
            "runtime": {"fallback_enabled": False},
        },
        project_root=ROOT,
        artifact_mode="evaluation-only",
    )
    prepared_by_name = {
        name: factory.prepare_question(specs[name]["question"])
        for name in CAPABILITY_ORDER
    }
    evidence_by_qid: dict[str, str | Sequence[EvidenceCandidate]] = {
        prepared_by_name[name].question.qid: specs[name]["evidence"]
        for name in CAPABILITY_ORDER
    }
    evidence_by_qid[prepared_by_name[CAPABILITY_ORDER[0]].question.qid] = (
        _structured_sum_candidates()
    )

    factory_build_workflow_call_count = 0
    workflow = factory.build_workflow(writer=None)
    factory_build_workflow_call_count += 1
    original_retriever_class = type(workflow.retriever).__name__
    local_retriever = _LocalEvidenceRetriever(evidence_by_qid)
    workflow.retriever = local_retriever
    provider_override = _force_factory_solver_clients_none(workflow)

    routed_solver = workflow.solver
    factory_solver_route_keys = sorted(
        str(key) for key in (getattr(routed_solver, "solvers", {}) or {})
    )
    calculator = getattr(routed_solver, "solvers", {}).get("calculation")
    if not isinstance(calculator, CalculationSolver):
        raise TypeError("factory calculation route is not CalculationSolver")

    calculation_call_count = 0
    original_calculation_solve = calculator.solve

    def recorded_calculation_solve(bundle: EvidenceBundle) -> SolverResult:
        nonlocal calculation_call_count
        calculation_call_count += 1
        return original_calculation_solve(bundle)

    calculator.solve = recorded_calculation_solve  # type: ignore[method-assign]

    factory_observation = {
        "probe_mode": "factory_build_workflow_with_post_build_local_retriever_override",
        "factory_build_workflow_called": factory_build_workflow_call_count == 1,
        "factory_build_workflow_call_count": factory_build_workflow_call_count,
        "factory_class": type(factory).__name__,
        "factory_workflow_class": type(workflow).__name__,
        "factory_classifier_class": type(workflow.classifier).__name__,
        "factory_solver_class": type(routed_solver).__name__,
        "factory_calculation_solver_class": type(calculator).__name__,
        "factory_solver_route_keys": factory_solver_route_keys,
        "retriever_override": {
            "applied": True,
            "timing": "after_factory_build_workflow",
            "original_class": original_retriever_class,
            "override_class": type(local_retriever).__name__,
            "read_only_local_fixture": True,
        },
        "provider_clients_forced_none": all(
            bool(item.get("forced_none")) for item in provider_override.values()
        ),
        "provider_client_overrides": provider_override,
    }
    observed_call_path = [
        f"{type(factory).__name__}.build_workflow",
        f"{type(workflow).__name__}.process_one",
        f"{type(workflow.classifier).__name__}.classify",
        f"{type(routed_solver).__name__}.solve",
        f"{type(calculator).__name__}.solve",
    ]

    normal: dict[str, dict[str, Any]] = {}
    shadow: dict[str, dict[str, Any]] = {}
    with _count_product_executor_calls() as product_counts, _count_request_assembly_calls() as request_counts:
        for name in CAPABILITY_ORDER:
            prepared = prepared_by_name[name]
            classification = workflow.classifier.classify(prepared.question)
            before_calculation = calculation_call_count
            before_product = dict(product_counts)
            before_request = dict(request_counts)
            pipeline_result = None
            blocked: BlockingAnswerValidationError | None = None
            try:
                pipeline_result = workflow.process_one(prepared.question)
            except BlockingAnswerValidationError as exc:
                blocked = exc

            solver_entered = calculation_call_count == before_calculation + 1
            executor_delta = {
                capability: product_counts[capability] - before_product[capability]
                for capability in CAPABILITY_ORDER
            }
            request_delta = {
                capability: request_counts[capability] - before_request[capability]
                for capability in CAPABILITY_ORDER
            }
            metadata: Mapping[str, Any]
            if pipeline_result is not None:
                metadata = dict(pipeline_result.solver_result.metadata or {})
                solver_name = pipeline_result.solver_result.solver
                answer = pipeline_result.answer
                final_state = str(pipeline_result.metadata.get("final_state") or "")
                shadow_record = dict(pipeline_result.metadata.get("c3_shadow") or {})
                block_reason = ""
            else:
                exception_metadata = dict(blocked.metadata if blocked else {})
                metadata = dict(exception_metadata.get("solver_metadata") or {})
                solver_name = str(exception_metadata.get("solver") or "")
                answer = ""
                final_state = "blocked"
                shadow_record = dict(exception_metadata.get("c3_shadow") or {})
                block_reason = blocked.reason if blocked else "unknown"

            request_observed = request_delta[name] > 0
            executor_invoked = executor_delta[name] > 0
            normal_observation = {
                **factory_observation,
                "entry": "PipelineFactory.build_workflow + EnhancedBaselineWorkflow.process_one",
                "key_call_path": list(observed_call_path),
                "question": prepared.question.text,
                "query_understanding": dict(
                    prepared.question.raw.get("_query_understanding") or {}
                ),
                "classification_labels": [label.value for label in classification.labels],
                "classification_reasons": dict(classification.reasons),
                "routed_solver": solver_name,
                "calculation_solver_entered": solver_entered,
                "request_contract": specs[name]["input_contract"],
                "request_assembly_observed": request_observed,
                "request_assembly_hits": request_delta,
                "request_assembly_evidence": {
                    "runtime_constructor_instrumentation": True,
                    "target_contract_constructor_hits": request_delta[name],
                    "all_contract_constructor_hits": dict(request_delta),
                },
                "product_executor_invoked": executor_invoked,
                "product_executor_hits": executor_delta,
                "correct_result": bool(
                    executor_invoked and answer == str(specs[name]["expected"])
                ),
                "answer": answer,
                "answer_source": str(metadata.get("answer_source") or ""),
                "final_state": final_state,
                "block_reason": block_reason,
                "manual_program_or_request": False,
                "provider_calls": int(metadata.get("provider_call_count", 0) or 0),
                "legacy_calls": int(
                    metadata.get("legacy_execution_invoked") is True
                ),
                "network_calls": 0,
                "total_tokens": int(metadata.get("total_tokens", 0) or 0),
                "trace": list(metadata.get("result_trace") or ()),
                "source_lineage": list(metadata.get("source_lineage") or ()),
                "source_lineage_complete": bool(
                    metadata.get("source_lineage_complete") is True
                ),
                "metadata": dict(metadata),
                "blocking_module": (
                    "binding/evidence assembly: no observed construction of "
                    + specs[name]["input_contract"]
                    if not request_observed and not executor_invoked
                    else ""
                ),
            }
            normal_observation["status"] = _derive_normal_status(normal_observation)
            normal[name] = normal_observation

            shadow_observation = {
                "entry": f"{type(workflow).__name__}.process_one -> {type(workflow.c3_shadow_observer).__name__}.observe",
                "key_call_path": [
                    f"{type(workflow).__name__}.process_one",
                    f"{type(workflow.c3_shadow_observer).__name__}.observe",
                    "parse_shadow_input_record",
                ],
                "state": str(shadow_record.get("state") or ""),
                "reason_codes": list(shadow_record.get("reason_codes") or ()),
                "pipeline_invoked": bool(shadow_record.get("pipeline_invoked")),
                "request_assembly_observed": False,
                "request_assembly_hits": {
                    capability: 0 for capability in CAPABILITY_ORDER
                },
                "product_executor_invoked": False,
                "product_executor_hits": {
                    capability: 0 for capability in CAPABILITY_ORDER
                },
                "correct_result": bool(shadow_record.get("would_execute")),
                "manual_program_or_request": False,
                "provider_calls": int(shadow_record.get("provider_call_count", 0) or 0),
                "legacy_calls": int(
                    shadow_record.get("legacy_execution_invoked") is True
                ),
                "network_calls": 0,
                "total_tokens": int(shadow_record.get("total_tokens", 0) or 0),
                "trace": list(shadow_record.get("trace") or ()),
                "metadata": shadow_record,
                "blocking_module": (
                    "C3ShadowObserver requires caller-supplied c3_shadow_input_v1; "
                    "the local retriever override supplies ordinary evidence only"
                    if str(shadow_record.get("state") or "") == "BLOCKED"
                    and not bool(shadow_record.get("pipeline_invoked"))
                    else ""
                ),
            }
            shadow_observation["status"] = _derive_shadow_status(shadow_observation)
            shadow[name] = shadow_observation
    return shadow, normal


def _load_snapshot_metrics(root: Path) -> dict[str, Any]:
    report_path = root / C3O_SNAPSHOT / "aggregate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    combined = report["datasets"]["combined"]
    return {
        "measurement_valid": report["measurement_valid"],
        "numeric_eligible": combined["numeric_eligible_count"],
        "representable": combined["c3_representable_count"],
        "correct": combined["terminal_executed_correct_count"],
        "incorrect": combined["executed_incorrect_count"],
        "c3_errors": combined["c3_execution_error_count"],
        "remaining_unsupported_operator": report["bottlenecks"][
            "UNSUPPORTED_OPERATOR"
        ]["case_count"],
        "effective_oracle_accuracy": combined[
            "effective_oracle_execution_accuracy"
        ]["value"],
        "provider_calls": report["actual_provider_call_count"],
        "legacy_calls": report["actual_legacy_call_count"],
        "network_calls": report["actual_network_call_count_during_evaluation"],
        "total_tokens": report["total_tokens"],
        "snapshot_files": {
            name: {
                "path": str((C3O_SNAPSHOT / name).as_posix()),
                "sha256": _sha256(root / C3O_SNAPSHOT / name),
            }
            for name in (
                "per_case_records.jsonl",
                "aggregate_report.json",
                "aggregate_report.md",
            )
        },
    }


def _remaining_operator_summary(root: Path) -> dict[str, Any]:
    records_path = root / C3O_SNAPSHOT / "per_case_records.jsonl"
    unsupported = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line
        )
        if row["terminal_classification"] == "UNSUPPORTED_OPERATOR"
    }
    taxonomy_rows = [
        json.loads(line)
        for line in (root / TAXONOMY).read_text(encoding="utf-8").splitlines()
        if line
    ]
    taxonomy = {row["case_id"]: row for row in taxonomy_rows}
    detail_counts = Counter(row["failure_detail"] for row in unsupported.values())
    candidate_counts = Counter(
        str(taxonomy.get(case_id, {}).get("candidate_capability") or "UNCLASSIFIED")
        for case_id in unsupported
    )
    subfamily_counts = Counter(
        str(taxonomy.get(case_id, {}).get("semantic_subfamily") or "UNCLASSIFIED")
        for case_id in unsupported
    )
    qualified_product_counts: Counter[str] = Counter()
    for case_id in unsupported:
        row = taxonomy.get(case_id, {})
        proof = row.get("oracle_proof") or {}
        if (
            row.get("candidate_type") == "PRODUCT_CAPABILITY"
            and row.get("selection_eligibility") is True
            and row.get("binding_uniqueness_status") == "UNIQUE"
            and proof.get("proof_status") == "COMPLETE"
            and proof.get("binding_uniqueness_status") == "UNIQUE"
        ):
            qualified_product_counts[
                str(row.get("candidate_capability") or "UNCLASSIFIED")
            ] += 1
    max_qualified = max(qualified_product_counts.values(), default=0)
    return {
        "total": len(unsupported),
        "failure_detail_counts": dict(sorted(detail_counts.items())),
        "candidate_capability_counts": dict(sorted(candidate_counts.items())),
        "semantic_subfamily_counts": dict(sorted(subfamily_counts.items())),
        "qualified_unique_complete_product_family_counts": dict(
            sorted(qualified_product_counts.items())
        ),
        "max_qualified_product_family_size": max_qualified,
        "product_family_with_at_least_five_qualified_cases": max_qualified >= 5,
        "interpretation": (
            "No remaining data-independent product family has at least five "
            "selection-eligible, uniquely bound, complete-proof cases. The five "
            "percent-literal cases are measurement-adapter repair, while the "
            "largest unrecovered product families are unbound or incomplete."
        ),
    }


def _source_wiring_audit(root: Path) -> dict[str, Any]:
    paths = {
        "run": Path("run.py"),
        "factory": Path("src/agent/factory.py"),
        "workflow": Path("src/agent/workflow.py"),
        "router": Path("src/solvers/router.py"),
        "calculation_solver": Path("src/solvers/calculation.py"),
        "explicit_c3": Path("src/solvers/c3_deterministic.py"),
        "shadow": Path("src/solvers/c3_shadow.py"),
        "oracle": Path("src/evaluation/external_benchmarks/c3_oracle_baseline.py"),
        "finqa_adapter": Path("src/evaluation/external_benchmarks/finqa_adapter.py"),
        "tatqa_adapter": Path("src/evaluation/external_benchmarks/tatqa_adapter.py"),
    }
    executor_symbols = {
        CAPABILITY_ORDER[0]: "SourceBoundNumericSeriesAggregator",
        CAPABILITY_ORDER[1]: "SourceBoundTablePredicateCardinalityCounter",
        CAPABILITY_ORDER[2]: "SourceBoundTableSectionCardinalityCounter",
    }
    request_symbols = {
        CAPABILITY_ORDER[0]: "SourceBoundNumericSeriesAggregationRequest",
        CAPABILITY_ORDER[1]: "SourceBoundTablePredicateCardinalityRequest",
        CAPABILITY_ORDER[2]: "SourceBoundTableSectionCardinalityRequest",
    }

    def scan(symbols: Mapping[str, str]) -> dict[str, dict[str, list[int]]]:
        occurrences: dict[str, dict[str, list[int]]] = {}
        for logical_name, relative in paths.items():
            lines = (root / relative).read_text(encoding="utf-8").splitlines()
            occurrences[logical_name] = {
                capability: [
                    index
                    for index, line in enumerate(lines, start=1)
                    if symbol in line
                ]
                for capability, symbol in symbols.items()
            }
        return occurrences

    executor_occurrences = scan(executor_symbols)
    request_occurrences = scan(request_symbols)
    normal_paths = (
        "run",
        "factory",
        "workflow",
        "router",
        "calculation_solver",
    )
    expected_paths = {
        CAPABILITY_ORDER[0]: {
            "executor": ("calculation_solver",),
            "request": ("calculation_solver",),
        },
        CAPABILITY_ORDER[1]: {"executor": (), "request": ()},
        CAPABILITY_ORDER[2]: {"executor": (), "request": ()},
    }

    expected_normal_wiring_by_capability: dict[str, dict[str, Any]] = {}
    unexpected_executor = False
    unexpected_request = False
    for capability in CAPABILITY_ORDER:
        observed_executor_paths = tuple(
            path_name
            for path_name in normal_paths
            if executor_occurrences[path_name][capability]
        )
        observed_request_paths = tuple(
            path_name
            for path_name in normal_paths
            if request_occurrences[path_name][capability]
        )
        expected_executor_paths = tuple(expected_paths[capability]["executor"])
        expected_request_paths = tuple(expected_paths[capability]["request"])
        executor_matches = observed_executor_paths == expected_executor_paths
        request_matches = observed_request_paths == expected_request_paths
        unexpected_executor = unexpected_executor or not executor_matches
        unexpected_request = unexpected_request or not request_matches
        expected_normal_wiring_by_capability[capability] = {
            "expected_executor_paths": list(expected_executor_paths),
            "observed_executor_paths": list(observed_executor_paths),
            "executor_matches": executor_matches,
            "expected_request_contract_paths": list(expected_request_paths),
            "observed_request_contract_paths": list(observed_request_paths),
            "request_contract_matches": request_matches,
        }

    return {
        "paths": {name: str(path.as_posix()) for name, path in paths.items()},
        "executor_symbols": dict(executor_symbols),
        "request_contract_symbols": dict(request_symbols),
        "executor_symbol_occurrences": executor_occurrences,
        "request_contract_symbol_occurrences": request_occurrences,
        "expected_normal_wiring_by_capability": expected_normal_wiring_by_capability,
        "normal_chain_executor_symbols_present": any(
            executor_occurrences[path_name][capability]
            for path_name in normal_paths
            for capability in CAPABILITY_ORDER
        ),
        "normal_chain_request_contract_symbols_present": any(
            request_occurrences[path_name][capability]
            for path_name in normal_paths
            for capability in CAPABILITY_ORDER
        ),
        "unexpected_normal_chain_executor_symbols_present": unexpected_executor,
        "unexpected_normal_chain_request_contract_symbols_present": unexpected_request,
        "normal_wiring_matches_expectation": not unexpected_executor
        and not unexpected_request,
        "explicit_pipeline_executor_symbols_present": any(
            executor_occurrences["explicit_c3"][capability]
            for capability in CAPABILITY_ORDER
        ),
        "explicit_pipeline_request_contract_symbols_present": any(
            request_occurrences["explicit_c3"][capability]
            for capability in CAPABILITY_ORDER
        ),
        "shadow_executor_symbols_present": any(
            executor_occurrences["shadow"][capability]
            for capability in CAPABILITY_ORDER
        ),
        "shadow_request_contract_symbols_present": any(
            request_occurrences["shadow"][capability]
            for capability in CAPABILITY_ORDER
        ),
        "oracle_has_all_executor_symbols": all(
            executor_occurrences["oracle"][capability]
            for capability in CAPABILITY_ORDER
        ),
        "oracle_has_all_request_contract_symbols": all(
            any(
                request_occurrences[path_name][capability]
                for path_name in ("oracle", "finqa_adapter", "tatqa_adapter")
            )
            for capability in CAPABILITY_ORDER
        ),
    }


def _activation_matrix(
    oracle: Mapping[str, Mapping[str, Any]],
    explicit: Mapping[str, Mapping[str, Any]],
    shadow: Mapping[str, Mapping[str, Any]],
    normal: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    for name in CAPABILITY_ORDER:
        matrix[name] = {
            "ORACLE_RUNTIME": dict(oracle[name]),
            "EXPLICIT_C3_CALL": dict(explicit[name]),
            "SHADOW_OBSERVER": dict(shadow[name]),
            "NORMAL_PIPELINE": dict(normal[name]),
        }
    return matrix


def _blocking_layer_counts(
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for capability in CAPABILITY_ORDER:
        for layer in LAYER_ORDER:
            cell = matrix[capability][layer]
            status = str(cell["status"])
            if status.startswith("BLOCKED_BY_") or status == "NOT_WIRED":
                counts[status] += 1
    return dict(sorted(counts.items()))


def _derive_stage_rule_evaluation(
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
    snapshot_metrics: Mapping[str, Any],
    remaining: Mapping[str, Any],
) -> dict[str, Any]:
    all_oracle_correct = all(
        matrix[name]["ORACLE_RUNTIME"].get("correct_result") is True
        for name in CAPABILITY_ORDER
    )
    all_explicit_correct = all(
        matrix[name]["EXPLICIT_C3_CALL"].get("correct_result") is True
        for name in CAPABILITY_ORDER
    )
    normal_active_count = sum(
        matrix[name]["NORMAL_PIPELINE"].get("status") == "ACTIVE"
        for name in CAPABILITY_ORDER
    )
    all_normal_factory_built = all(
        matrix[name]["NORMAL_PIPELINE"].get("factory_build_workflow_called") is True
        and matrix[name]["NORMAL_PIPELINE"].get("factory_build_workflow_call_count") == 1
        for name in CAPABILITY_ORDER
    )
    inactive_capabilities = tuple(
        name
        for name in CAPABILITY_ORDER
        if matrix[name]["NORMAL_PIPELINE"].get("status") != "ACTIVE"
    )
    all_inactive_normal_routed_calculation = bool(inactive_capabilities) and all(
        matrix[name]["NORMAL_PIPELINE"].get("calculation_solver_entered") is True
        and "calculation"
        in tuple(matrix[name]["NORMAL_PIPELINE"].get("classification_labels") or ())
        for name in inactive_capabilities
    )
    all_inactive_request_assembly_unobserved = bool(inactive_capabilities) and all(
        matrix[name]["NORMAL_PIPELINE"].get("request_assembly_observed") is False
        for name in inactive_capabilities
    )
    all_inactive_product_hits_zero = bool(inactive_capabilities) and all(
        not any(
            int(value or 0)
            for value in (
                matrix[name]["NORMAL_PIPELINE"].get("product_executor_hits") or {}
            ).values()
        )
        for name in inactive_capabilities
    )
    all_shadow_blocked_before_pipeline = all(
        matrix[name]["SHADOW_OBSERVER"].get("state") == "BLOCKED"
        and matrix[name]["SHADOW_OBSERVER"].get("pipeline_invoked") is False
        for name in CAPABILITY_ORDER
    )

    exit_inputs = {
        "c3_m_n_o_stable": bool(
            snapshot_metrics.get("measurement_valid") is True
            and snapshot_metrics.get("representable") == 1602
            and snapshot_metrics.get("correct") == 1600
            and snapshot_metrics.get("incorrect") == 2
            and snapshot_metrics.get("c3_errors") == 0
        ),
        "oracle_or_explicit_paths_correct": all_oracle_correct and all_explicit_correct,
        "normal_pipeline_factory_built": all_normal_factory_built,
        "normal_pipeline_has_non_active_capability": normal_active_count < 3,
        "primary_blocker_is_integration": bool(
            all_inactive_normal_routed_calculation
            and all_inactive_request_assembly_unobserved
            and all_inactive_product_hits_zero
            and all_shadow_blocked_before_pipeline
        ),
        "remaining_candidates_are_long_tail": not bool(
            remaining.get("product_family_with_at_least_five_qualified_cases")
        ),
    }
    continue_inputs = {
        "all_three_normal_pipeline_active": normal_active_count == 3,
        "routing_binding_evidence_not_primary_blocker": not exit_inputs[
            "primary_blocker_is_integration"
        ],
        "new_generic_family_at_least_five": bool(
            remaining.get("product_family_with_at_least_five_qualified_cases")
        ),
    }
    exit_satisfied = all(exit_inputs.values())
    continue_satisfied = all(continue_inputs.values())
    if exit_satisfied and not continue_satisfied:
        decision = "EXIT_OPERATOR_EXPANSION"
    elif continue_satisfied and not exit_satisfied:
        decision = "CONTINUE_OPERATOR_EXPANSION"
    else:
        decision = "INVALID_RULE_STATE"
    return {
        "exit_operator_expansion": exit_inputs,
        "continue_operator_expansion": continue_inputs,
        "exit_rule_satisfied": exit_satisfied,
        "continue_rule_satisfied": continue_satisfied,
        "derived_decision": decision,
    }


def _derive_recommended_next_layer(
    matrix: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> str:
    normal_statuses = [
        str(matrix[name]["NORMAL_PIPELINE"].get("status") or "")
        for name in CAPABILITY_ORDER
    ]
    if any(status == "BLOCKED_BY_MISSING_ROUTING" for status in normal_statuses):
        return "SOLVER_ROUTING"
    if any(status == "BLOCKED_BY_MISSING_EVIDENCE" for status in normal_statuses):
        return "EVIDENCE_RETRIEVAL"
    if any(status == "BLOCKED_BY_MISSING_BINDING" for status in normal_statuses):
        return "BINDING_AND_EVIDENCE_ASSEMBLY"
    if normal_statuses and all(status == "ACTIVE" for status in normal_statuses):
        return "NEXT_QUALIFIED_OPERATOR_FAMILY"
    return "INTEGRATION_DIAGNOSIS"


def build_stage_exit_report(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    specs = _capability_specs()
    with deny_network() as network_counter:
        oracle, explicit = _probe_oracle_and_explicit(specs)
        shadow, normal = _probe_normal_pipeline(specs)

    snapshot_metrics = _load_snapshot_metrics(root)
    remaining = _remaining_operator_summary(root)
    source_wiring = _source_wiring_audit(root)
    for name in CAPABILITY_ORDER:
        request_evidence = normal[name]["request_assembly_evidence"]
        request_evidence["normal_chain_request_contract_symbol_occurrences"] = {
            path_name: list(
                source_wiring["request_contract_symbol_occurrences"][path_name][name]
            )
            for path_name in (
                "run",
                "factory",
                "workflow",
                "router",
                "calculation_solver",
            )
        }
        request_evidence["normal_chain_executor_symbol_occurrences"] = {
            path_name: list(
                source_wiring["executor_symbol_occurrences"][path_name][name]
            )
            for path_name in (
                "run",
                "factory",
                "workflow",
                "router",
                "calculation_solver",
            )
        }
        request_evidence["static_contract_symbol_present"] = any(
            request_evidence["normal_chain_request_contract_symbol_occurrences"].values()
        )
        request_evidence["static_executor_symbol_present"] = any(
            request_evidence["normal_chain_executor_symbol_occurrences"].values()
        )

    matrix = _activation_matrix(oracle, explicit, shadow, normal)
    stage_rules = _derive_stage_rule_evaluation(matrix, snapshot_metrics, remaining)
    stage_decision = str(stage_rules["derived_decision"])
    recommended_next_layer = _derive_recommended_next_layer(matrix)

    provider_calls = sum(
        int(matrix[name][layer].get("provider_calls", 0) or 0)
        for name in CAPABILITY_ORDER
        for layer in LAYER_ORDER
    )
    legacy_calls = sum(
        int(matrix[name][layer].get("legacy_calls", 0) or 0)
        for name in CAPABILITY_ORDER
        for layer in LAYER_ORDER
    )
    total_tokens = sum(
        int(matrix[name][layer].get("total_tokens", 0) or 0)
        for name in CAPABILITY_ORDER
        for layer in LAYER_ORDER
    )
    network_calls = int(network_counter["count"])

    capability_inventory = {
        name: {
            "stage": specs[name]["short_name"],
            "input_contract": specs[name]["input_contract"],
            "executor": specs[name]["executor"].__name__,
            "expected_local_result": specs[name]["expected"],
            "failure_closed_boundary": list(specs[name]["failure_boundary"]),
            "historical_external_coverage_gain": dict(specs[name]["historical_gain"]),
            "evidence_boundary": (
                "Oracle-program coverage only; not retrieval, PDF parsing, "
                "natural-language binding, or end-to-end accuracy"
            ),
        }
        for name in CAPABILITY_ORDER
    }

    expected_normal_runtime = {
        CAPABILITY_ORDER[0]: {
            "status": "ACTIVE",
            "request_assembly_observed": True,
            "product_executor_invoked": True,
            "answer": "60",
            "answer_source": "c3_source_bound_sum_series",
        },
        CAPABILITY_ORDER[1]: {
            "status": "BLOCKED_BY_MISSING_BINDING",
            "request_assembly_observed": False,
            "product_executor_invoked": False,
            "answer": "",
        },
        CAPABILITY_ORDER[2]: {
            "status": "BLOCKED_BY_MISSING_BINDING",
            "request_assembly_observed": False,
            "product_executor_invoked": False,
            "answer": "",
        },
    }
    normal_probe_integrity = all(
        normal[name]["factory_build_workflow_called"] is True
        and normal[name]["factory_build_workflow_call_count"] == 1
        and normal[name]["provider_clients_forced_none"] is True
        and normal[name]["retriever_override"]["applied"] is True
        and normal[name]["retriever_override"]["timing"]
        == "after_factory_build_workflow"
        and all(
            normal[name].get(key) == value
            for key, value in expected_normal_runtime[name].items()
        )
        and (
            name != CAPABILITY_ORDER[0]
            or (
                normal[name]["correct_result"] is True
                and normal[name]["source_lineage_complete"] is True
                and normal[name]["request_assembly_hits"][name] == 1
                and normal[name]["product_executor_hits"][name] == 1
            )
        )
        for name in CAPABILITY_ORDER
    )
    source_audit_integrity = bool(
        source_wiring["oracle_has_all_executor_symbols"]
        and source_wiring["oracle_has_all_request_contract_symbols"]
        and source_wiring["normal_wiring_matches_expectation"]
        and not source_wiring[
            "unexpected_normal_chain_executor_symbols_present"
        ]
        and not source_wiring[
            "unexpected_normal_chain_request_contract_symbols_present"
        ]
        and not source_wiring["explicit_pipeline_executor_symbols_present"]
        and not source_wiring["explicit_pipeline_request_contract_symbols_present"]
        and not source_wiring["shadow_executor_symbols_present"]
        and not source_wiring["shadow_request_contract_symbols_present"]
    )
    measurement_valid = bool(
        stage_decision in {"EXIT_OPERATOR_EXPANSION", "CONTINUE_OPERATOR_EXPANSION"}
        and stage_decision == stage_rules["derived_decision"]
        and recommended_next_layer == _derive_recommended_next_layer(matrix)
        and normal_probe_integrity
        and source_audit_integrity
        and provider_calls == 0
        and legacy_calls == 0
        and network_calls == 0
        and total_tokens == 0
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "baseline_head": _git_head(root),
        "capability_inventory": capability_inventory,
        "activation_matrix": matrix,
        "normal_pipeline_probes": {
            name: dict(normal[name]) for name in CAPABILITY_ORDER
        },
        "source_wiring_audit": source_wiring,
        "blocking_layer_counts": _blocking_layer_counts(matrix),
        "historical_snapshot": snapshot_metrics,
        "remaining_operator_summary": remaining,
        "stage_rule_evaluation": stage_rules,
        "stage_decision": stage_decision,
        "stage_decision_reasons": [
            "C3-M/N/O execute correctly with complete trace in Oracle and explicit caller-supplied request paths.",
            "The Factory-built C3-M probe uses structured-table evidence and reaches the existing SUM Binder and C3-M executor with answer 60.",
            "C3-N and C3-O still enter CalculationSolver without constructing their source-bound requests or invoking their executors.",
            "Capability-aware source audit permits only the expected C3-M wiring in CalculationSolver; Shadow and explicit pipeline remain separate.",
            "No remaining generic product family has at least five uniquely bound, complete-proof, selection-eligible cases.",
        ],
        "recommended_next_layer": recommended_next_layer,
        "recommended_next_layer_reason": (
            "C3-M is active through the Factory-built structured-table path. C3-N and "
            "C3-O reach CalculationSolver but still have no request assembly or product "
            "executor invocation, so their first observed gap remains binding and evidence assembly."
        ),
        "provider_calls": provider_calls,
        "legacy_calls": legacy_calls,
        "network_calls": network_calls,
        "total_tokens": total_tokens,
        "measurement_valid": measurement_valid,
    }
    return json.loads(json.dumps(payload, ensure_ascii=False))


def validate_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected report schema")
    matrix = report.get("activation_matrix")
    if not isinstance(matrix, Mapping) or set(matrix) != set(CAPABILITY_ORDER):
        raise ValueError("activation matrix capability mismatch")
    for capability in CAPABILITY_ORDER:
        layers = matrix[capability]
        if not isinstance(layers, Mapping) or set(layers) != set(LAYER_ORDER):
            raise ValueError(f"activation layers mismatch:{capability}")
        for layer in LAYER_ORDER:
            status = layers[layer].get("status")
            if status not in VALID_ACTIVATION_STATES:
                raise ValueError(f"invalid activation state:{capability}:{layer}:{status}")

        normal = layers["NORMAL_PIPELINE"]
        if normal.get("status") != _derive_normal_status(normal):
            raise ValueError(f"normal status not observation-derived:{capability}")
        if normal.get("factory_build_workflow_called") is not True:
            raise ValueError(f"factory workflow not built:{capability}")
        if normal.get("factory_build_workflow_call_count") != 1:
            raise ValueError(f"factory workflow call count invalid:{capability}")
        if normal.get("factory_workflow_class") != "EnhancedBaselineWorkflow":
            raise ValueError(f"unexpected factory workflow class:{capability}")
        if normal.get("factory_solver_class") != "RoutedSolver":
            raise ValueError(f"unexpected factory solver class:{capability}")
        if normal.get("factory_calculation_solver_class") != "CalculationSolver":
            raise ValueError(f"unexpected factory calculation solver:{capability}")
        if "calculation" not in tuple(normal.get("factory_solver_route_keys") or ()):
            raise ValueError(f"factory calculation route missing:{capability}")
        if normal.get("provider_clients_forced_none") is not True:
            raise ValueError(f"provider clients not forced none:{capability}")
        retriever_override = normal.get("retriever_override") or {}
        if not (
            retriever_override.get("applied") is True
            and retriever_override.get("timing") == "after_factory_build_workflow"
            and retriever_override.get("read_only_local_fixture") is True
        ):
            raise ValueError(f"retriever override disclosure invalid:{capability}")
        call_path = tuple(str(item) for item in normal.get("key_call_path") or ())
        if not call_path or call_path[0] != "PipelineFactory.build_workflow":
            raise ValueError(f"normal call path not factory-derived:{capability}")
        if any("run.py" in item for item in call_path):
            raise ValueError(f"unexecuted run.py path claimed:{capability}")
        if "source_bound_request_created" in normal:
            raise ValueError(f"legacy request/executor equivalence field present:{capability}")
        if not isinstance(normal.get("request_assembly_observed"), bool):
            raise ValueError(f"request assembly observation missing:{capability}")
        if not isinstance(normal.get("product_executor_invoked"), bool):
            raise ValueError(f"executor invocation observation missing:{capability}")
        request_evidence = normal.get("request_assembly_evidence") or {}
        if request_evidence.get("runtime_constructor_instrumentation") is not True:
            raise ValueError(f"request constructor instrumentation missing:{capability}")
        if "normal_chain_request_contract_symbol_occurrences" not in request_evidence:
            raise ValueError(f"request static audit missing:{capability}")
        if "normal_chain_executor_symbol_occurrences" not in request_evidence:
            raise ValueError(f"executor static audit missing:{capability}")

        if capability == CAPABILITY_ORDER[0]:
            expected_hits = {
                CAPABILITY_ORDER[0]: 1,
                CAPABILITY_ORDER[1]: 0,
                CAPABILITY_ORDER[2]: 0,
            }
            if not (
                normal.get("status") == "ACTIVE"
                and normal.get("request_assembly_observed") is True
                and normal.get("request_assembly_hits") == expected_hits
                and normal.get("product_executor_invoked") is True
                and normal.get("product_executor_hits") == expected_hits
                and normal.get("answer") == "60"
                and normal.get("answer_source")
                == "c3_source_bound_sum_series"
                and normal.get("final_state") == "accepted"
                and normal.get("correct_result") is True
                and normal.get("source_lineage_complete") is True
                and bool(normal.get("trace"))
                and bool(normal.get("source_lineage"))
            ):
                raise ValueError("C3-M normal activation facts invalid")
        else:
            zero_hits = {name: 0 for name in CAPABILITY_ORDER}
            if not (
                normal.get("status") == "BLOCKED_BY_MISSING_BINDING"
                and normal.get("request_assembly_observed") is False
                and normal.get("request_assembly_hits") == zero_hits
                and normal.get("product_executor_invoked") is False
                and normal.get("product_executor_hits") == zero_hits
                and normal.get("answer") == ""
                and normal.get("final_state") == "blocked"
            ):
                raise ValueError(
                    f"inactive normal capability facts invalid:{capability}"
                )

        shadow = layers["SHADOW_OBSERVER"]
        if shadow.get("status") != _derive_shadow_status(shadow):
            raise ValueError(f"shadow status not observation-derived:{capability}")
        if not (
            shadow.get("state") == "BLOCKED"
            and shadow.get("pipeline_invoked") is False
            and shadow.get("request_assembly_observed") is False
            and shadow.get("request_assembly_hits")
            == {name: 0 for name in CAPABILITY_ORDER}
            and shadow.get("product_executor_invoked") is False
            and shadow.get("product_executor_hits")
            == {name: 0 for name in CAPABILITY_ORDER}
        ):
            raise ValueError(f"shadow isolation facts invalid:{capability}")

    source_wiring = report.get("source_wiring_audit")
    if not isinstance(source_wiring, Mapping):
        raise ValueError("source wiring audit missing")
    for key in (
        "executor_symbol_occurrences",
        "request_contract_symbol_occurrences",
        "expected_normal_wiring_by_capability",
        "normal_chain_executor_symbols_present",
        "normal_chain_request_contract_symbols_present",
        "unexpected_normal_chain_executor_symbols_present",
        "unexpected_normal_chain_request_contract_symbols_present",
        "normal_wiring_matches_expectation",
    ):
        if key not in source_wiring:
            raise ValueError(f"source wiring audit field missing:{key}")

    if not (
        source_wiring.get("normal_chain_executor_symbols_present") is True
        and source_wiring.get("normal_chain_request_contract_symbols_present")
        is True
        and source_wiring.get(
            "unexpected_normal_chain_executor_symbols_present"
        )
        is False
        and source_wiring.get(
            "unexpected_normal_chain_request_contract_symbols_present"
        )
        is False
        and source_wiring.get("normal_wiring_matches_expectation") is True
        and source_wiring.get("oracle_has_all_executor_symbols") is True
        and source_wiring.get("oracle_has_all_request_contract_symbols") is True
        and source_wiring.get("explicit_pipeline_executor_symbols_present")
        is False
        and source_wiring.get(
            "explicit_pipeline_request_contract_symbols_present"
        )
        is False
        and source_wiring.get("shadow_executor_symbols_present") is False
        and source_wiring.get("shadow_request_contract_symbols_present")
        is False
    ):
        raise ValueError("capability-aware source wiring audit invalid")

    normal_paths = (
        "run",
        "factory",
        "workflow",
        "router",
        "calculation_solver",
    )
    declared_wiring = source_wiring["expected_normal_wiring_by_capability"]
    executor_occurrences = source_wiring["executor_symbol_occurrences"]
    request_occurrences = source_wiring["request_contract_symbol_occurrences"]
    for capability in CAPABILITY_ORDER:
        expected_paths = (
            ["calculation_solver"]
            if capability == CAPABILITY_ORDER[0]
            else []
        )
        observed_executor_paths = [
            path_name
            for path_name in normal_paths
            if executor_occurrences[path_name][capability]
        ]
        observed_request_paths = [
            path_name
            for path_name in normal_paths
            if request_occurrences[path_name][capability]
        ]
        expected_declaration = {
            "expected_executor_paths": expected_paths,
            "observed_executor_paths": expected_paths,
            "executor_matches": True,
            "expected_request_contract_paths": expected_paths,
            "observed_request_contract_paths": expected_paths,
            "request_contract_matches": True,
        }
        if observed_executor_paths != expected_paths:
            raise ValueError(
                f"unexpected normal executor wiring:{capability}"
            )
        if observed_request_paths != expected_paths:
            raise ValueError(
                f"unexpected normal request wiring:{capability}"
            )
        if declared_wiring.get(capability) != expected_declaration:
            raise ValueError(
                f"declared normal wiring mismatch:{capability}"
            )

    snapshot_metrics = report.get("historical_snapshot")
    remaining = report.get("remaining_operator_summary")
    if not isinstance(snapshot_metrics, Mapping) or not isinstance(remaining, Mapping):
        raise ValueError("stage rule inputs missing")
    derived_rules = _derive_stage_rule_evaluation(matrix, snapshot_metrics, remaining)
    if report.get("stage_rule_evaluation") != derived_rules:
        raise ValueError("stage rule evaluation does not match observations")
    if report.get("stage_decision") != derived_rules["derived_decision"]:
        raise ValueError("stage decision does not match rule-derived decision")
    derived_next_layer = _derive_recommended_next_layer(matrix)
    if report.get("recommended_next_layer") != derived_next_layer:
        raise ValueError("recommended next layer does not match blocking evidence")
    if report.get("measurement_valid") is not True:
        raise ValueError("stage-exit measurement invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="evaluation_artifacts/c3_stage_exit_gate_v1/report.json",
    )
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_stage_exit_report(ROOT)
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
            "C3_STAGE_EXIT_GATE",
            f"measurement_valid={str(report['measurement_valid']).lower()}",
            f"decision={report['stage_decision']}",
            f"next_layer={report['recommended_next_layer']}",
            f"provider_calls={report['provider_calls']}",
            f"legacy_calls={report['legacy_calls']}",
            f"network_calls={report['network_calls']}",
            f"tokens={report['total_tokens']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
