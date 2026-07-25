from contracts import Question
from scripts.evaluate_document_scope_recall import _masked_question


def test_masked_a_question_removes_ground_truth_doc_ids_from_all_resolver_inputs() -> None:
    question = Question(
        qid="a1",
        domain="financial_reports",
        text="根据比亚迪 2024 年年度报告判断。",
        options={"A": "正确", "B": "错误"},
        answer_format="tf",
        doc_ids=("annual_byd_2024_report",),
        candidate_doc_ids=("should_also_be_removed",),
        raw={
            "qid": "a1",
            "domain": "financial_reports",
            "question": "根据比亚迪 2024 年年度报告判断。",
            "type": "判断题",
            "doc_ids": ["annual_byd_2024_report"],
            "answer": "A",
        },
    )

    masked = _masked_question(question)

    assert masked.doc_ids == ()
    assert masked.candidate_doc_ids == ()
    assert "doc_ids" not in masked.raw
    assert "answer" not in masked.raw
    assert masked.raw["type"] == "判断题"
    assert masked.text == question.text
    assert masked.options == question.options
