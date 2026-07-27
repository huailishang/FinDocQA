import json
from pathlib import Path

from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, QuestionLabel
from evaluation.retrieval_ab import (
    RetrievalABStrategy,
    load_retrieval_gold_cases,
    run_retrieval_ab,
)


def test_load_retrieval_gold_cases_keeps_gold_out_of_question_scope(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "qid": "case_1",
                        "domain": "financial_reports",
                        "question": "根据甲公司和乙公司年报，哪些判断正确？",
                        "options": {"A": "甲正确", "B": "乙正确"},
                        "expected_answer": ["AB"],
                        "required_doc_ids": ["doc_a", "doc_b"],
                        "acceptable_page_groups": [
                            [["doc_a", 3]],
                            [["doc_b", 7]],
                        ],
                        "evidence_anchors": ["甲正确", "乙正确"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = load_retrieval_gold_cases(gold_path)

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "case_1"
    assert case.question.answer_format == "multi"
    assert tuple(case.question.doc_ids) == ()
    assert tuple(case.question.candidate_doc_ids) == ()
    assert "expected_answer" not in case.question.raw
    assert tuple(case.gold.required_doc_ids) == ("doc_a", "doc_b")
    assert case.gold.acceptable_page_groups == (
        (("doc_a", 3),),
        (("doc_b", 7),),
    )


class _Classifier:
    def classify(self, question):
        return ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,))


class _Retriever:
    def __init__(self, candidates):
        self._candidates = tuple(candidates)

    def retrieve(self, question, classification):
        return self._candidates


class _Assembler:
    def assemble(self, question, classification, candidates):
        selected = tuple(candidates[:1])
        return EvidenceBundle(
            question=question,
            classification=classification,
            candidates=selected,
            prompt_context="\n".join(item.text for item in selected),
            estimated_tokens=123,
        )


def _candidate(doc_id: str, page: int, text: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        domain="financial_reports",
        doc_id=doc_id,
        source=f"canonical://financial_reports/{doc_id}/page/{page}",
        text=text,
        metadata={"page_number": page},
    )


def test_run_retrieval_ab_measures_raw_and_solver_visible_evidence(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "qid": "case_1",
                        "domain": "financial_reports",
                        "question": "营业收入是多少？",
                        "options": {},
                        "required_doc_ids": ["doc_a"],
                        "acceptable_page_groups": [[['doc_a', 3]]],
                        "evidence_anchors": ["100亿元"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases = load_retrieval_gold_cases(gold_path)
    good = _candidate("doc_a", 3, "营业收入100亿元")
    distractor = _candidate("doc_b", 1, "无关内容")

    report = run_retrieval_ab(
        cases,
        classifier=_Classifier(),
        strategies=(
            RetrievalABStrategy(
                name="old",
                retriever=_Retriever((distractor, good)),
                assembler=_Assembler(),
            ),
            RetrievalABStrategy(
                name="new",
                retriever=_Retriever((good, distractor)),
                assembler=_Assembler(),
            ),
        ),
        k=5,
    )
    payload = report.to_dict()

    assert payload["answer_quality_status"] == "not_run_zero_api"
    old = payload["strategies"][0]
    new = payload["strategies"][1]
    assert old["raw"]["complete_document_recall_at_k"] == 1.0
    assert old["solver_visible"]["complete_document_recall_at_k"] == 0.0
    assert new["solver_visible"]["complete_document_recall_at_k"] == 1.0
    assert new["solver_visible"]["evidence_anchor_recall_at_k"] == 1.0
    assert new["mean_estimated_tokens"] == 123.0
    assert new["errors"] == 0
