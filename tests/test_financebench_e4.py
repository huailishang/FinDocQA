from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from agent.workflow import BlockingAnswerValidationError
from contracts import ClassificationResult, PipelineResult, QuestionLabel, SolverResult
from evaluation.answer_ab import AnswerABStrategy, load_answer_ab_checkpoint, run_answer_ab
from evaluation.external_benchmarks.financebench_e4 import (
    FROZEN_CASE_IDS,
    FROZEN_DOC_IDS,
    build_financebench_e4_cases,
    build_financebench_preflight_config,
    financebench_e4_inventory,
    run_factory_retrieval_preflight,
    runtime_gold_key_hits,
    select_frozen_financebench_cases,
    validate_frozen_inventory,
)
from utils.config import load_config

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "evaluation_artifacts/external_benchmarks/financebench/github_selected/financebench_open_source.jsonl"
PROCESSED = REPO / "evaluation_artifacts/external_benchmarks/financebench/canonical_evidence_smoke_v1/processed"
CONFIG = REPO / "config/config.yaml"
SCRIPT = REPO / "scripts/evaluate_financebench_e4.py"


def _source_cases():
    cases = select_frozen_financebench_cases(SOURCE)
    validate_frozen_inventory(cases)
    return cases


def _pipeline_result(qid: str, answer: str, *, provider_calls: int = 0, total_tokens: int = 0):
    return PipelineResult(
        qid=qid,
        answer=answer,
        answer_values=(answer,),
        classification=ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,)),
        solver_result=SolverResult(
            qid=qid,
            answer=answer,
            solver="CONTRACT_TEST_ONLY",
            metadata={"provider_call_count": provider_calls},
        ),
        prompt_tokens=max(0, total_tokens - 5),
        completion_tokens=min(5, total_tokens),
        total_tokens=total_tokens,
        metadata={"provider_call_count": provider_calls, "contract_test_only": True},
    )


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("evaluate_financebench_e4_testmodule", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_inventory_and_e4_adapter_preserve_candidate_document_binding() -> None:
    source_cases = _source_cases()
    e4_cases = build_financebench_e4_cases(source_cases)
    inventory = financebench_e4_inventory(source_cases)

    assert len(source_cases) == len(e4_cases) == len(inventory) == 8
    assert tuple(case.case_id for case in source_cases) == FROZEN_CASE_IDS
    assert {item.doc_name for item in inventory} == FROZEN_DOC_IDS
    assert all(item.use_scope == "RESEARCH_ONLY_NONCOMMERCIAL" for item in inventory)
    assert all(item.license_id == "CC-BY-NC-4.0" for item in inventory)

    for source, case in zip(source_cases, e4_cases):
        assert case.case_id == source.case_id
        assert tuple(case.question.doc_ids) == ()
        assert tuple(case.question.candidate_doc_ids) == (source.document.doc_name,)
        assert case.gold_answers == (source.gold_label.answer,)
        assert runtime_gold_key_hits(case.question) == ()
        assert "justification" not in case.question.raw
        assert "evidence" not in case.question.raw


def test_preflight_config_is_copy_and_keeps_committed_config_unchanged() -> None:
    base = load_config(CONFIG)
    original_processed = base["paths"]["processed_docs"]
    original_retriever = base["pipeline"]["retriever"]

    preflight = build_financebench_preflight_config(base, processed_docs=PROCESSED)

    assert base["paths"]["processed_docs"] == original_processed
    assert base["pipeline"]["retriever"] == original_retriever
    assert preflight["pipeline"]["retriever"] == "canonical_lexical"
    assert Path(preflight["paths"]["processed_docs"]) == PROCESSED
    assert preflight["retrieval"]["canonical_top_k_per_doc"] == 5
    assert preflight["retrieval"]["canonical_window_chars"] == 1800
    assert preflight["retrieval"]["canonical_context_flank_chars"] == 600


def test_factory_retrieval_preflight_stays_in_bound_doc_and_matches_frozen_baseline() -> None:
    source_cases = _source_cases()
    e4_cases = build_financebench_e4_cases(source_cases)
    config = build_financebench_preflight_config(load_config(CONFIG), processed_docs=PROCESSED)

    rows = run_factory_retrieval_preflight(e4_cases, config=config, project_root=REPO)
    gold_pages = {
        case.case_id: tuple(sorted({e.page_num + 1 for e in case.gold_label.evidence}))
        for case in source_cases
    }

    assert len(rows) == 8
    assert all(row.request_source == "question_candidate_doc_ids" for row in rows)
    assert all(row.scope_provider_calls == 0 for row in rows)
    assert all(set(row.retrieved_doc_ids).issubset({row.doc_name}) for row in rows)

    all_gold_hits = sum(
        all(page in set(row.retrieved_page_numbers) for page in gold_pages[row.case_id])
        for row in rows
    )
    annotation_hits = sum(
        page in set(row.retrieved_page_numbers)
        for row in rows
        for page in gold_pages[row.case_id]
    )
    annotation_total = sum(len(gold_pages[row.case_id]) for row in rows)
    assert all_gold_hits == 2
    assert annotation_hits == 2
    assert annotation_total == 10


def test_financebench_cases_reuse_answer_ab_scoring_checkpoint_and_resume(tmp_path: Path) -> None:
    cases = build_financebench_e4_cases(_source_cases())[:2]
    predictions = {
        cases[0].case_id: cases[0].gold_answers[0],
        cases[1].case_id: "CONTRACT_TEST_ONLY_WRONG",
    }
    calls: list[str] = []

    def fake_runner(question):
        calls.append(question.qid)
        return _pipeline_result(
            question.qid,
            predictions[question.qid],
            provider_calls=2,
            total_tokens=17,
        )

    checkpoint = tmp_path / "financebench_e4_checkpoint.jsonl"
    report = run_answer_ab(
        cases,
        strategies=(AnswerABStrategy(name="CONTRACT_TEST_ONLY", runner=fake_runner),),
        checkpoint_path=checkpoint,
    ).to_dict()
    strategy = report["strategies"][0]

    assert calls == [case.case_id for case in cases]
    assert strategy["case_value_accuracy"] == 0.5
    assert strategy["provider_call_count"] == 4
    assert strategy["total_tokens"] == 34
    assert strategy["incorrect_but_accepted_cases"] == 1
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 2

    prior = load_answer_ab_checkpoint(checkpoint)
    resumed_calls: list[str] = []

    def must_not_run(question):
        resumed_calls.append(question.qid)
        raise AssertionError("resume must skip completed strategy/case pairs")

    resumed = run_answer_ab(
        cases,
        strategies=(AnswerABStrategy(name="CONTRACT_TEST_ONLY", runner=must_not_run),),
        checkpoint_path=checkpoint,
        prior_measurements=prior,
    ).to_dict()
    assert resumed_calls == []
    assert resumed["strategies"][0]["case_value_accuracy"] == 0.5
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 2


def test_financebench_answer_ab_retains_blocked_and_error_fields() -> None:
    cases = build_financebench_e4_cases(_source_cases())[:2]

    def fake_runner(question):
        if question.qid == cases[0].case_id:
            raise BlockingAnswerValidationError(
                question.qid,
                question.answer_format,
                cases[0].gold_answers[0],
                "CONTRACT_TEST_ONLY_BLOCK",
                metadata={
                    "submission_answers": [cases[0].gold_answers[0]],
                    "provider_call_count": 3,
                    "actual_prompt_tokens": 8,
                    "actual_completion_tokens": 2,
                    "actual_total_tokens": 10,
                },
            )
        raise RuntimeError("CONTRACT_TEST_ONLY_ERROR")

    report = run_answer_ab(
        cases,
        strategies=(AnswerABStrategy(name="CONTRACT_TEST_ONLY", runner=fake_runner),),
    ).to_dict()
    measurements = report["strategies"][0]["cases"]

    assert measurements[0]["blocked"] is True
    assert measurements[0]["blocking_reason"] == "CONTRACT_TEST_ONLY_BLOCK"
    assert measurements[0]["provider_call_count"] == 3
    assert measurements[0]["case_value_correct"] == 1.0
    assert measurements[1]["blocked"] is False
    assert "CONTRACT_TEST_ONLY_ERROR" in measurements[1]["error"]


def test_cli_default_dry_run_is_zero_provider(tmp_path: Path, capsys) -> None:
    module = _load_cli_module()
    rc = module.main(["--output-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert rc == 0
    assert '"mode": "dry_run_zero_provider_calls"' in captured.out
    payload = __import__("json").loads((tmp_path / "dry_run.json").read_text(encoding="utf-8"))
    assert payload["cases"] == 8
    assert payload["strategy"] == "canonical_lexical"
    assert payload["provider_calls_made"] == 0
    assert payload["execute_authorized"] is False
    assert payload["candidate_binding_preserved"] == 8
    assert payload["all_gold_at_5"] == {"hits": 2, "total": 8}
    assert payload["annotation_recall_at_5"] == {"hits": 2, "total": 10}


def test_cli_execute_gate_fails_before_workflow_construction(monkeypatch) -> None:
    module = _load_cli_module()
    constructed: list[bool] = []

    def forbidden_build_workflow(self, *args, **kwargs):
        constructed.append(True)
        raise AssertionError("build_workflow must not be reached by negative gate")

    monkeypatch.setattr(module.PipelineFactory, "build_workflow", forbidden_build_workflow)

    with pytest.raises(SystemExit) as missing_allow:
        module.main(["--execute"])
    assert missing_allow.value.code == 2
    assert constructed == []

    with pytest.raises(SystemExit) as zero_budget:
        module.main(["--execute", "--allow-provider-calls", "--max-provider-calls", "0"])
    assert zero_budget.value.code == 2
    assert constructed == []


def test_future_authorized_execute_installs_repository_precall_budget(monkeypatch, tmp_path: Path) -> None:
    module = _load_cli_module()
    for key in (
        "SAFE_RUN_EXECUTION",
        "LLM_TOKEN_LEDGER_PATH",
        "SAFE_RUN_MAX_PROVIDER_CALL_BUDGET",
        "SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON",
        "SAFE_RUN_DECISION_PURPOSE",
        "SAFE_RUN_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    args = argparse.Namespace(execute=True, output_dir=tmp_path, max_provider_calls=4)

    module._configure_provider_safety(args, qids=("q1", "q2"))

    import os, json
    assert os.environ["SAFE_RUN_EXECUTION"] == "1"
    assert os.environ["SAFE_RUN_MAX_PROVIDER_CALL_BUDGET"] == "4"
    assert Path(os.environ["LLM_TOKEN_LEDGER_PATH"]) == tmp_path / "provider_ledger.jsonl"
    assert json.loads(os.environ["SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON"]) == {"q1": 4, "q2": 4}
