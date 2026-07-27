import json
from pathlib import Path

from agent.workflow import BlockingAnswerValidationError
from contracts import ClassificationResult, PipelineResult, Question, QuestionLabel, SolverResult
from evaluation.answer_ab import (
    AnswerABCase,
    AnswerABStrategy,
    load_answer_ab_checkpoint,
    load_answer_gold_cases,
    run_answer_ab,
)


def _question(*, qid: str = "case_1", slot_count: int = 2) -> Question:
    return Question(
        qid=qid,
        domain="financial_reports",
        text="请给出两个结果。",
        options={},
        answer_format="freeform",
        doc_ids=("gold_doc_should_not_leak",),
        candidate_doc_ids=("candidate_should_not_leak",),
        submission_slot_count=slot_count,
        raw={
            "qid": qid,
            "domain": "financial_reports",
            "question": "请给出两个结果。",
            "doc_ids": ["gold_doc_should_not_leak"],
            "_source_file": "fixture.json",
        },
    )


def test_load_answer_gold_cases_uses_visible_question_contract_without_gold_leakage(
    tmp_path: Path,
) -> None:
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "qid": "case_1",
                        "expected_answer": ["100", "200"],
                        "required_doc_ids": ["gold_doc_should_not_leak"],
                        "evidence_anchors": ["100", "200"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = load_answer_gold_cases(gold_path, questions=(_question(),))

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "case_1"
    assert case.gold_answers == ("100", "200")
    assert case.question.submission_slot_count == 2
    assert tuple(case.question.doc_ids) == ()
    assert tuple(case.question.candidate_doc_ids) == ()
    assert "doc_ids" not in case.question.raw
    assert "expected_answer" not in case.question.raw
    assert "required_doc_ids" not in case.question.raw


def _pipeline_result(qid: str, answers: tuple[str, ...], *, total_tokens: int) -> PipelineResult:
    answer = answers[0] if answers else ""
    return PipelineResult(
        qid=qid,
        answer=answer,
        answer_values=answers,
        classification=ClassificationResult(labels=(QuestionLabel.CALCULATION,)),
        solver_result=SolverResult(qid=qid, answer=answer, solver="fixture"),
        prompt_tokens=max(0, total_tokens - 10),
        completion_tokens=min(10, total_tokens),
        total_tokens=total_tokens,
        metadata={"provider_call_count": 1},
    )


def test_run_answer_ab_scores_multi_slot_values_and_costs() -> None:
    question = _question(slot_count=3)
    case = AnswerABCase(
        case_id="case_1",
        question=question,
        gold_answers=("112.32", "112.31", "0.01"),
    )

    report = run_answer_ab(
        (case,),
        strategies=(
            AnswerABStrategy(
                name="old",
                runner=lambda q: _pipeline_result(
                    q.qid, ("112.32", "112.30", "0.02"), total_tokens=120
                ),
            ),
            AnswerABStrategy(
                name="new",
                runner=lambda q: _pipeline_result(
                    q.qid, ("112.3200", "112.31", "0.010000"), total_tokens=100
                ),
            ),
        ),
    )
    payload = report.to_dict()

    old, new = payload["strategies"]
    assert old["case_value_accuracy"] == 0.0
    assert new["case_value_accuracy"] == 1.0
    assert new["case_exact_match"] == 0.0
    assert new["slot_value_accuracy"] == 1.0
    assert new["total_tokens"] == 100
    assert new["provider_call_count"] == 1
    assert new["errors"] == 0
    assert old["incorrect_but_accepted_cases"] == 1
    assert old["false_accept_rate_on_incorrect"] == 1.0
    assert new["correct_but_blocked_cases"] == 0


def test_run_answer_ab_resumes_completed_strategy_case_without_rerun(tmp_path: Path) -> None:
    question = _question(slot_count=1)
    case = AnswerABCase(case_id="case_1", question=question, gold_answers=("100",))
    checkpoint = tmp_path / "answer_ab.jsonl"

    first_report = run_answer_ab(
        (case,),
        strategies=(
            AnswerABStrategy(
                name="old",
                runner=lambda q: _pipeline_result(q.qid, ("100",), total_tokens=50),
            ),
        ),
        checkpoint_path=checkpoint,
    )
    assert first_report.to_dict()["strategies"][0]["case_value_accuracy"] == 1.0

    prior = load_answer_ab_checkpoint(checkpoint)
    calls: list[str] = []

    def should_not_run(q: Question) -> PipelineResult:
        calls.append(q.qid)
        raise AssertionError("completed checkpoint must be reused")

    resumed = run_answer_ab(
        (case,),
        strategies=(AnswerABStrategy(name="old", runner=should_not_run),),
        checkpoint_path=checkpoint,
        prior_measurements=prior,
    )

    assert calls == []
    assert resumed.to_dict()["strategies"][0]["case_value_accuracy"] == 1.0
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 1


def test_run_answer_ab_scores_blocked_pipeline_answer_separately_from_gate() -> None:
    question = _question(slot_count=2)
    case = AnswerABCase(
        case_id="case_1",
        question=question,
        gold_answers=("1468.47%", "740.58%"),
    )

    def blocked_runner(q: Question) -> PipelineResult:
        raise BlockingAnswerValidationError(
            q.qid,
            q.answer_format,
            "1468.47%",
            "production_integrity:calculation_incomplete",
            metadata={
                "submission_answers": ["1468.47%", "740.58%"],
                "actual_prompt_tokens": 100,
                "actual_completion_tokens": 20,
                "actual_total_tokens": 120,
                "provider_call_count": 1,
                "final_state": "blocked",
            },
        )

    report = run_answer_ab(
        (case,),
        strategies=(AnswerABStrategy(name="old", runner=blocked_runner),),
    ).to_dict()

    strategy = report["strategies"][0]
    assert report["answer_quality_status"] == "completed"
    assert strategy["errors"] == 0
    assert strategy["blocked_cases"] == 1
    assert strategy["case_value_accuracy"] == 1.0
    assert strategy["slot_value_accuracy"] == 1.0
    assert strategy["total_tokens"] == 120
    assert strategy["provider_call_count"] == 1
    assert strategy["correct_but_blocked_cases"] == 1
    assert strategy["false_reject_rate_on_correct"] == 1.0
    assert strategy["incorrect_but_accepted_cases"] == 0
    assert strategy["cases"][0]["blocked"] is True
    assert strategy["cases"][0]["blocking_reason"] == "production_integrity:calculation_incomplete"
