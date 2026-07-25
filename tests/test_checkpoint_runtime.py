from __future__ import annotations

import json
from pathlib import Path

import pytest

from answer_contract import contract_to_dict
from contracts import ClassificationResult, PipelineResult, SolverResult
from data.loader import JsonQuestionLoader
from evaluation.writer import SubmissionTemplate, CsvSubmissionWriter


B_QUESTIONS_DIR = Path("../data/upload_b/question_b")
B_TEMPLATE = Path("../data/upload_b/submit.csv")


def _result(question, answers: tuple[str, ...]) -> PipelineResult:
    answer = answers[0]
    return PipelineResult(
        qid=question.qid,
        answer=answer,
        classification=ClassificationResult(labels=[]),
        solver_result=SolverResult(qid=question.qid, answer=answer, solver="fixture"),
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        reasoning="依据题目给出的关键事实完成核验，结果满足题目要求，因此将该值作为最终答案。",
        metadata={
            "answer_format": question.answer_format,
            "answer_contract": contract_to_dict(question.answer_contract),
            "final_state": "accepted",
        },
        submission_answers=answers,
    )


def _answers(question, slot_count: int) -> tuple[str, ...]:
    if question.answer_format == "freeform":
        return tuple(str(index + 1) for index in range(slot_count))
    return ("A",)


def test_b_checkpoint_accepts_five_results_and_never_emits_submission(tmp_path: Path) -> None:
    questions = list(JsonQuestionLoader(B_QUESTIONS_DIR).load())
    template = SubmissionTemplate.load(B_TEMPLATE)
    results = [
        _result(question, _answers(question, template.slot_count_by_qid[question.qid]))
        for question in questions[:5]
    ]
    writer = CsvSubmissionWriter(
        tmp_path,
        submission_mode="multi_slot",
        submission_template_path=B_TEMPLATE,
    )

    writer.write_checkpoint(results)

    assert (tmp_path / "debug_results.json").exists()
    assert not (tmp_path / "submission.csv").exists()
    payload = json.loads((tmp_path / "debug_results.json").read_text(encoding="utf-8"))
    assert len(payload) == 5


def test_b_final_99_fails_closed_but_keeps_checkpoint_debug(tmp_path: Path) -> None:
    questions = list(JsonQuestionLoader(B_QUESTIONS_DIR).load())
    template = SubmissionTemplate.load(B_TEMPLATE)
    results = [
        _result(question, _answers(question, template.slot_count_by_qid[question.qid]))
        for question in questions[:99]
    ]
    writer = CsvSubmissionWriter(
        tmp_path,
        submission_mode="multi_slot",
        submission_template_path=B_TEMPLATE,
    )
    writer.write_checkpoint(results)

    with pytest.raises(ValueError, match="does not match template"):
        writer.write_final(results)

    assert (tmp_path / "debug_results.json").exists()
    assert not (tmp_path / "submission.csv").exists()


def test_b_final_100_emits_submission(tmp_path: Path) -> None:
    questions = list(JsonQuestionLoader(B_QUESTIONS_DIR).load())
    template = SubmissionTemplate.load(B_TEMPLATE)
    results = [
        _result(question, _answers(question, template.slot_count_by_qid[question.qid]))
        for question in questions
    ]
    writer = CsvSubmissionWriter(
        tmp_path,
        submission_mode="multi_slot",
        submission_template_path=B_TEMPLATE,
    )

    writer.write_checkpoint(results)
    writer.write_final(results)

    assert (tmp_path / "submission.csv").exists()


def test_evaluation_only_partial_checkpoint_never_emits_submission(tmp_path: Path) -> None:
    questions = list(JsonQuestionLoader(B_QUESTIONS_DIR).load())
    template = SubmissionTemplate.load(B_TEMPLATE)
    results = [
        _result(question, _answers(question, template.slot_count_by_qid[question.qid]))
        for question in questions[:5]
    ]
    writer = CsvSubmissionWriter(
        tmp_path,
        artifact_mode="evaluation-only",
        submission_mode="multi_slot",
        submission_template_path=B_TEMPLATE,
    )

    writer.write_checkpoint(results)
    writer.write_final(results)

    assert (tmp_path / "debug_results.json").exists()
    assert not (tmp_path / "submission.csv").exists()
