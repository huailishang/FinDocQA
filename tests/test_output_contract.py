import json
from pathlib import Path

from contracts import ClassificationResult, PipelineResult, QuestionLabel, SolverResult, result_answer_values
from result_output import JsonResultWriter, ResultRecord


def _pipeline_result(**kwargs) -> PipelineResult:
    defaults = dict(
        qid="q1",
        answer="A",
        classification=ClassificationResult(labels=(QuestionLabel.DEFAULT,)),
        solver_result=SolverResult(qid="q1", answer="A", solver="fixture"),
    )
    defaults.update(kwargs)
    return PipelineResult(**defaults)


def test_generic_answer_values_prefer_new_field() -> None:
    result = _pipeline_result(
        answer="first",
        answer_values=("first", "second"),
        submission_answers=("legacy",),
    )
    assert result_answer_values(result) == ("first", "second")
    record = ResultRecord.from_pipeline_result(result)
    assert record.primary_answer == "first"
    assert record.answer_values == ("first", "second")


def test_generic_answer_values_keep_legacy_compatibility() -> None:
    result = _pipeline_result(
        answer="legacy-first",
        submission_answers=("legacy-first", "legacy-second"),
    )
    assert result_answer_values(result) == ("legacy-first", "legacy-second")


def test_generic_answer_values_fall_back_to_primary_answer() -> None:
    result = _pipeline_result(answer="BC")
    assert result_answer_values(result) == ("BC",)


def test_json_writer_is_output_neutral(tmp_path: Path) -> None:
    record = ResultRecord.from_pipeline_result(
        _pipeline_result(
            answer="112.32",
            answer_values=("112.32", "112.31", "0.01"),
            reasoning="deterministic calculation",
        )
    )
    path = tmp_path / "result.json"
    JsonResultWriter(path).write([record])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["question_id"] == "q1"
    assert payload[0]["answer_values"] == ["112.32", "112.31", "0.01"]
    assert "submission" not in json.dumps(payload[0], ensure_ascii=False).lower()
