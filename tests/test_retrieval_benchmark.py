from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from evaluation.layers import (
    RetrievalBenchmarkCase,
    RetrievalBenchmarkStrategy,
    RetrievalGold,
    run_retrieval_benchmark,
)


class FixedRetriever:
    def __init__(self, name: str, candidates):
        self.name = name
        self._candidates = tuple(candidates)

    def retrieve(self, question, classification):
        del question, classification
        return self._candidates


def _candidate(doc_id: str, page: int, text: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        domain="financial_reports",
        doc_id=doc_id,
        source=f"canonical://financial_reports/{doc_id}/page/{page}",
        text=text,
        metadata={"page_number": page},
    )


def _case() -> RetrievalBenchmarkCase:
    question = Question(
        qid="q1",
        domain="financial_reports",
        text="营业收入是多少？",
        options={},
        answer_format="freeform",
        doc_ids=(),
    )
    classification = ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,))
    gold = RetrievalGold(
        required_doc_ids=("good",),
        evidence_text_anchors=("营业收入",),
        acceptable_page_groups=((("good", 2),),),
    )
    return RetrievalBenchmarkCase(
        case_id="case1",
        question=question,
        classification=classification,
        gold=gold,
    )


def test_benchmark_runner_compares_ranking_quality() -> None:
    good = FixedRetriever(
        "good",
        [
            _candidate("good", 2, "营业收入为100亿元"),
            _candidate("noise", 1, "无关"),
        ],
    )
    bad = FixedRetriever(
        "bad",
        [
            _candidate("noise", 1, "无关"),
            _candidate("good", 2, "营业收入为100亿元"),
        ],
    )

    good_summary = run_retrieval_benchmark(
        [_case()],
        RetrievalBenchmarkStrategy(name="good", retriever=good),
        k=2,
    )
    bad_summary = run_retrieval_benchmark(
        [_case()],
        RetrievalBenchmarkStrategy(
            name="bad",
            retriever=bad,
            api_calls_per_case=2,
            estimated_cost_per_case=0.01,
        ),
        k=2,
    )

    assert good_summary.reciprocal_rank_at_k == 1.0
    assert bad_summary.reciprocal_rank_at_k == 0.5
    assert good_summary.ndcg_at_k > bad_summary.ndcg_at_k
    assert bad_summary.total_api_calls == 2
    assert bad_summary.total_estimated_cost == 0.01
    assert good_summary.mean_latency_ms is not None
    assert good_summary.p95_latency_ms is not None
