from pathlib import Path

from contracts import ClassificationResult, Question, QuestionLabel
from document import CanonicalDocument, CanonicalPage
from document.adapters.text import canonical_from_markdown_file
from document.store import InMemoryDocumentStore
from retrieval.canonical_lexical import CanonicalLexicalEvidenceRetriever


def test_canonical_retriever_reads_store_not_page_files(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text(
        "# 年报\n\n2025年营业收入为100亿元，净利润为20亿元。\n",
        encoding="utf-8",
    )
    document = canonical_from_markdown_file(
        source,
        domain="financial_reports",
        doc_id="report_2025",
    )
    store = InMemoryDocumentStore.from_documents([document])
    retriever = CanonicalLexicalEvidenceRetriever(store=store)
    question = Question(
        qid="local_test",
        domain="financial_reports",
        text="2025年营业收入是多少？",
        options={},
        answer_format="free_text",
        doc_ids=("report_2025",),
    )
    classification = ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,))

    candidates = retriever.retrieve(question, classification)

    assert candidates
    assert candidates[0].doc_id == "report_2025"
    assert "营业收入" in candidates[0].text
    assert candidates[0].source.startswith("canonical://")
    assert candidates[0].metadata["canonical_document"] is True


def test_evidence_retrieval_does_not_overweight_document_title() -> None:
    title = "超长公司发行股份及支付现金购买资产并募集配套资金暨关联交易报告书"
    document = CanonicalDocument(
        document_id="doc1",
        domain="financial_contracts",
        title=title,
        source_type="fixture",
        source_uri="doc.pdf",
        parser_name="fixture",
        parser_version="1",
        pages=(
            CanonicalPage(
                page_number=1,
                text=(title + "。声明页。") * 8,
                blocks=(),
            ),
            CanonicalPage(
                page_number=2,
                text="标的资产在两个评估基准日的评估增值率分别为 100% 和 200%。",
                blocks=(),
            ),
        ),
    )
    store = InMemoryDocumentStore.from_documents([document])
    retriever = CanonicalLexicalEvidenceRetriever(store=store, top_k_per_doc=2)
    question = Question(
        qid="local_title_noise",
        domain="financial_contracts",
        text=f"根据《{title}》，标的资产在两个评估基准日的评估增值率分别是多少？",
        options={},
        answer_format="free_text",
        doc_ids=("doc1",),
    )
    classification = ClassificationResult(labels=(QuestionLabel.CALCULATION,))

    candidates = retriever.retrieve(question, classification)

    assert candidates
    assert candidates[0].metadata["page_number"] == 2
    assert "评估增值率" in candidates[0].text
