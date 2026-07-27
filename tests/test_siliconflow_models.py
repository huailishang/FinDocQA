from contracts import EvidenceCandidate, Question
from retrieval.siliconflow_models import SiliconFlowEvidenceReranker


class RecordingClient:
    def __init__(self) -> None:
        self.path = ""
        self.payload = {}

    def post_json(self, path, payload):
        self.path = path
        self.payload = dict(payload)
        return {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.1},
            ]
        }


def test_reranker_uses_options_and_bounds_payload() -> None:
    client = RecordingClient()
    reranker = SiliconFlowEvidenceReranker(
        client,
        max_query_chars=32,
        max_document_chars=12,
    )
    question = Question(
        qid="q1",
        domain="financial_contracts",
        text="以下哪些说法正确？",
        options={"A": "关键数字3.49%", "B": "关键数字77.09%"},
        answer_format="multi",
        doc_ids=(),
    )
    candidates = (
        EvidenceCandidate(
            domain="financial_contracts",
            doc_id="a",
            source="fixture://a",
            text="A" * 100,
        ),
        EvidenceCandidate(
            domain="financial_contracts",
            doc_id="b",
            source="fixture://b",
            text="B" * 100,
        ),
    )

    ranked = reranker.rerank(question, candidates, top_k=2)

    assert client.path == "rerank"
    assert "关键数字3.49%" in client.payload["query"]
    assert len(client.payload["query"]) <= 32
    assert all(len(text) <= 12 for text in client.payload["documents"])
    assert [item.doc_id for item in ranked] == ["a", "b"]


def test_reranker_requires_positive_payload_limits() -> None:
    client = RecordingClient()
    for kwargs in ({"max_query_chars": 0}, {"max_document_chars": 0}):
        try:
            SiliconFlowEvidenceReranker(client, **kwargs)
        except ValueError as exc:
            assert "must be >= 1" in str(exc)
        else:
            raise AssertionError("expected payload limit validation")
