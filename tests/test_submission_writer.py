from __future__ import annotations

import csv
import json

import pytest

from contracts import ClassificationResult, PipelineResult, SolverResult
from evaluation.writer import CsvSubmissionWriter


def _result(qid: str, answer: str, prompt: int, completion: int, total: int, answer_format: str = "mcq") -> PipelineResult:
    return PipelineResult(
        qid=qid,
        answer=answer,
        classification=ClassificationResult(labels=[]),
        solver_result=SolverResult(qid=qid, answer=answer, solver="direct"),
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        metadata={"answer_format": answer_format},
    )


def test_submission_writer_emits_official_columns_and_summary(tmp_path):
    writer = CsvSubmissionWriter(tmp_path)
    writer.write([
        _result("q2", "B", 20, 4, 24),
        _result("q1", "A", 10, 3, 13),
    ])

    with (tmp_path / "submission.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows == [
        ["qid", "answer", "prompt_tokens", "completion_tokens", "total_tokens"],
        ["summary", "", "30", "7", "37"],
        ["q1", "A", "10", "3", "13"],
        ["q2", "B", "20", "4", "24"],
    ]


def test_submission_writer_keeps_debug_json(tmp_path):
    writer = CsvSubmissionWriter(tmp_path)
    writer.write([_result("q1", "A", 1, 2, 3)])
    payload = json.loads((tmp_path / "debug_results.json").read_text(encoding="utf-8"))
    assert payload[0]["qid"] == "q1"
    assert payload[0]["total_tokens"] == 3


def test_submission_writer_preserves_multi_letter_answer_with_context(tmp_path):
    writer = CsvSubmissionWriter(tmp_path)
    writer.write([_result("q1", "DBA", 1, 2, 3, answer_format="multi")])
    rows = list(csv.reader((tmp_path / "submission.csv").open(newline="", encoding="utf-8")))
    assert rows[-1][1] == "ABD"


def test_submission_writer_rejects_multi_letter_mcq_without_artifacts(tmp_path):
    writer = CsvSubmissionWriter(tmp_path)
    with pytest.raises(ValueError, match="invalid submission answer"):
        writer.write([_result("q1", "ABCD", 1, 2, 3, answer_format="mcq")])
    assert not (tmp_path / "submission.csv").exists()
    assert not (tmp_path / "debug_results.json").exists()


def test_submission_writer_rejects_missing_format_context(tmp_path):
    result = _result("q1", "A", 1, 2, 3)
    result = PipelineResult(**{**result.__dict__, "metadata": {}})
    with pytest.raises(ValueError, match="unknown_answer_format"):
        CsvSubmissionWriter(tmp_path).write([result])
