from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from retrieval.hybrid_fusion import ReciprocalRankFusionRetriever, RetrievalLane


class FixtureRetriever:
    def __init__(self, name: str, candidates):
        self.name = name
        self._candidates = tuple(candidates)

    def retrieve(self, question, classification):
        del question, classification
        return self._candidates


def _candidate(doc_id: str, page: int, text: str, score: float) -> EvidenceCandidate:
    return EvidenceCandidate(
        domain="financial_reports",
        doc_id=doc_id,
        source=f"canonical://financial_reports/{doc_id}/page/{page}",
        text=text,
        score=score,
        retriever="fixture",
        metadata={"page_number": page},
    )


def test_rrf_promotes_candidate_seen_by_multiple_lanes() -> None:
    lexical = FixtureRetriever(
        "lexical",
        [
            _candidate("a", 1, "lexical only", 10.0),
            _candidate("b", 2, "shared evidence", 9.0),
        ],
    )
    embedding = FixtureRetriever(
        "embedding",
        [
            _candidate("b", 2, "shared evidence", 0.9),
            _candidate("c", 3, "embedding only", 0.8),
        ],
    )
    question = Question(
        qid="q1",
        domain="financial_reports",
        text="测试",
        options={},
        answer_format="freeform",
        doc_ids=(),
    )
    classification = ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,))
    retriever = ReciprocalRankFusionRetriever(
        [RetrievalLane(lexical), RetrievalLane(embedding)],
        rrf_k=60,
        per_lane_top_k=2,
        fused_top_k=3,
    )

    fused = retriever.retrieve(question, classification)

    assert [candidate.doc_id for candidate in fused] == ["b", "a", "c"]
    assert fused[0].metadata["fusion_method"] == "rrf"
    assert len(fused[0].metadata["fusion_lanes"]) == 2
    assert fused[0].retriever == "reciprocal_rank_fusion"


def test_rrf_lane_weights_are_supported() -> None:
    lexical = FixtureRetriever("lexical", [_candidate("a", 1, "A", 10.0)])
    embedding = FixtureRetriever("embedding", [_candidate("b", 2, "B", 0.9)])
    question = Question(
        qid="q2",
        domain="financial_reports",
        text="测试",
        options={},
        answer_format="freeform",
        doc_ids=(),
    )
    classification = ClassificationResult(labels=(QuestionLabel.DEFAULT,))
    retriever = ReciprocalRankFusionRetriever(
        [
            RetrievalLane(lexical, weight=1.0),
            RetrievalLane(embedding, weight=2.0),
        ],
        rrf_k=60,
        per_lane_top_k=1,
        fused_top_k=2,
    )

    fused = retriever.retrieve(question, classification)

    assert [candidate.doc_id for candidate in fused] == ["b", "a"]
