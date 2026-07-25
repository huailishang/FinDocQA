from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

from answer_contract import contract_to_dict
from contracts import ClassificationResult, PipelineResult, SolverResult
from data.loader import JsonQuestionLoader
from evaluation.formal_submission import FORMAL_SUBMISSION_HEADER
from evaluation.writer import SubmissionTemplate, CsvSubmissionWriter
from solvers.base import validate_submission_answer


B_QUESTIONS_DIR = Path("../data/upload_b/question_b")
B_SUBMISSION_TEMPLATE = Path("../data/upload_b/submit.csv")
B_HEADER = (
    "qid",
    "answer_1",
    "answer_2",
    "answer_3",
    "answer_4",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)


def _result(question, answers: tuple[str, ...]) -> PipelineResult:
    answer = answers[0]
    return PipelineResult(
        qid=question.qid,
        answer=answer,
        classification=ClassificationResult(labels=[]),
        solver_result=SolverResult(qid=question.qid, answer=answer, solver="offline_fixture"),
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        reasoning="依据题目给出的事实完成核验，答案满足对应格式要求，因此输出该结果作为最终答案。",
        metadata={
            "answer_format": question.answer_format,
            "answer_contract": contract_to_dict(question.answer_contract),
            "final_state": "accepted",
        },
        submission_answers=answers,
    )


def test_real_b_template_exposes_only_schema_and_slot_shape() -> None:
    template = SubmissionTemplate.load(B_SUBMISSION_TEMPLATE)

    assert template.header == B_HEADER
    assert len(template.qid_order) == 100
    assert Counter(template.slot_count_by_qid.values()) == {1: 90, 2: 7, 3: 2, 4: 1}
    assert not hasattr(template, "answers_by_qid")


@pytest.mark.parametrize(
    ("candidate", "expected_kind"),
    [
        ("12.34", "number"),
        ("12.34%", "percentage"),
        ("2026年1月1日", "date"),
        ("公司A>公司B", "ordering"),
        ("甲公司", "text"),
    ],
)
def test_freeform_contract_accepts_multi_slot_result_shapes(candidate: str, expected_kind: str) -> None:
    result = validate_submission_answer(candidate, "freeform")

    assert result.valid is True
    assert result.answer == candidate
    assert result.reason == f"valid_freeform:{expected_kind}"


def test_b_writer_outputs_official_header_template_order_and_all_business_qids(tmp_path: Path) -> None:
    questions = list(JsonQuestionLoader(B_QUESTIONS_DIR).load())
    template = SubmissionTemplate.load(B_SUBMISSION_TEMPLATE)
    by_qid = {question.qid: question for question in questions}
    results = []
    for qid in template.qid_order:
        question = by_qid[qid]
        slot_count = template.slot_count_by_qid[qid]
        answers = (
            tuple(str(index + 1) for index in range(slot_count))
            if question.answer_format == "freeform"
            else ("A",)
        )
        results.append(_result(question, answers))

    writer = CsvSubmissionWriter(
        tmp_path,
        submission_mode="multi_slot",
        submission_template_path=B_SUBMISSION_TEMPLATE,
    )
    writer.write(results)

    with (tmp_path / "submission.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert tuple(rows[0]) == FORMAL_SUBMISSION_HEADER
    assert rows[1] == ["summary", "", "", "", "", "100", "200", "300", ""]
    assert [row[0] for row in rows[2:]] == list(template.qid_order)
    assert len(rows[2:]) == 100
    assert all(int(row[5]) + int(row[6]) == int(row[7]) for row in rows[2:])


def test_factory_wires_multi_slot_writer_profile(tmp_path: Path) -> None:
    from agent.factory import PipelineFactory

    template_path = tmp_path / "submit.csv"
    template_path.write_text(
        "qid,answer_1,answer_2,answer_3,answer_4,prompt_tokens,completion_tokens,total_tokens\n"
        "summary,,,,,0,0,0\n"
        "q1,A,,,,0,0,0\n",
        encoding="utf-8",
    )
    factory = PipelineFactory(
        config={
            "paths": {
                "output_dir": "out",
                "submission_template": "submit.csv",
            },
            "submission": {"mode": "multi_slot"},
        },
        project_root=tmp_path,
    )

    writer = factory.build_writer()

    assert writer.submission_mode == "multi_slot"
    assert writer.submission_template_path == template_path.resolve()


def test_b_writer_rejects_collapsing_multiple_slots_into_one_field(tmp_path: Path) -> None:
    template_path = tmp_path / "template.csv"
    template_path.write_text(
        "qid,answer_1,answer_2,answer_3,answer_4,prompt_tokens,completion_tokens,total_tokens\n"
        "summary,,,,,0,0,0\n"
        "q1,999,999,,,0,0,0\n",
        encoding="utf-8",
    )
    question = list(JsonQuestionLoader(B_QUESTIONS_DIR).load())[0]
    result = PipelineResult(
        qid="q1",
        answer="1;2",
        classification=ClassificationResult(labels=[]),
        solver_result=SolverResult(qid="q1", answer="1;2", solver="fixture"),
        metadata={"answer_format": "freeform", "final_state": "accepted"},
        submission_answers=("1;2",),
    )

    with pytest.raises(ValueError, match="requires 2 answer slots"):
        CsvSubmissionWriter(
            tmp_path / "out",
            submission_mode="multi_slot",
            submission_template_path=template_path,
        ).write([result])
