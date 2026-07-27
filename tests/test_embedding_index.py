from contracts import ClassificationResult, Question, QuestionLabel
from document import CanonicalDocument, CanonicalPage, CanonicalTable
from document.store import InMemoryDocumentStore
from retrieval.embedding_index import (
    CanonicalEmbeddingIndex,
    EmbeddingEvidenceRetriever,
    canonical_page_embedding_text,
)


class FixtureEmbedder:
    def embed(self, texts):
        items = [texts] if isinstance(texts, str) else list(texts)
        vectors = []
        for text in items:
            if "营业收入" in text:
                vectors.append([1.0, 0.0])
            elif "净利润" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors


def _store() -> InMemoryDocumentStore:
    revenue_table = CanonicalTable(
        table_id="t1",
        page_number=1,
        markdown="| 指标 | 金额 |\n| --- | --- |\n| 营业收入 | 100亿元 |",
    )
    document = CanonicalDocument(
        document_id="report_2025",
        domain="financial_reports",
        title="2025年报",
        source_type="fixture",
        source_uri="report.pdf",
        parser_name="fixture",
        parser_version="1",
        pages=(
            CanonicalPage(
                page_number=1,
                text="主要财务指标",
                blocks=(),
                tables=(revenue_table,),
            ),
            CanonicalPage(
                page_number=2,
                text="2025年净利润为20亿元。",
                blocks=(),
            ),
        ),
    )
    return InMemoryDocumentStore.from_documents([document])


def test_embedding_text_keeps_structured_table_content() -> None:
    page = next(iter(_store().iter_documents())).pages[0]
    text = canonical_page_embedding_text(page)
    assert "营业收入" in text
    assert "100亿元" in text


def test_explicit_embedding_index_and_retrieval() -> None:
    store = _store()
    embedder = FixtureEmbedder()
    index = CanonicalEmbeddingIndex.build(
        store,
        embedder,
        domain="financial_reports",
        max_pages=2,
        batch_size=2,
    )
    assert len(index.entries) == 2
    assert index.embedding_dimension == 2

    question = Question(
        qid="q1",
        domain="financial_reports",
        text="营业收入是多少？",
        options={},
        answer_format="freeform",
        doc_ids=("report_2025",),
    )
    classification = ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,))
    candidates = EmbeddingEvidenceRetriever(index, embedder, top_k=2).retrieve(
        question,
        classification,
    )

    assert candidates
    assert candidates[0].metadata["page_number"] == 1
    assert "营业收入" in candidates[0].text
    assert candidates[0].score > candidates[1].score


def test_embedding_index_requires_explicit_page_budget() -> None:
    store = _store()
    embedder = FixtureEmbedder()
    try:
        CanonicalEmbeddingIndex.build(store, embedder, max_pages=0)
    except ValueError as exc:
        assert "max_pages" in str(exc)
    else:
        raise AssertionError("expected explicit max_pages budget validation")
