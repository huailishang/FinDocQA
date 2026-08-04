from __future__ import annotations

from contracts import ClassificationResult, Question, QuestionLabel
from document import CanonicalDocument, CanonicalPage
from document.store import InMemoryDocumentStore
from retrieval.canonical_lexical import (
    _DOCUMENT_QUERY_STOPWORDS,
    _document_query_terms,
    _evidence_terms,
    _question_terms,
    CanonicalDocumentRetriever,
)


FROZEN_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or",
    "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "how", "during",
    "this", "that", "these", "those",
    "from", "by", "with", "at", "as",
}


def _question(text: str, *, options: dict[str, str] | None = None) -> Question:
    return Question(
        qid="stopword-filter-test",
        domain="financial_reports",
        text=text,
        options=options or {},
        answer_format="free_text",
        doc_ids=(),
    )


def test_frozen_stopword_set_matches_contract_exactly() -> None:
    assert _DOCUMENT_QUERY_STOPWORDS == FROZEN_STOPWORDS


def test_document_query_terms_filter_standard_english_stopwords() -> None:
    question = _question("What was the total amount of revenue in the period?")
    assert _document_query_terms(question) == ("total", "amount", "revenue", "period")


def test_document_query_terms_preserve_financial_terms() -> None:
    question = _question("million thousand year period amount total average revenue margin")
    assert _document_query_terms(question) == (
        "million", "thousand", "year", "period", "amount",
        "total", "average", "revenue", "margin",
    )


def test_document_query_terms_preserve_numbers_years_units_and_percentages() -> None:
    question = _question("In 2024 was revenue 35% or 1000 million USD?")
    assert _document_query_terms(question) == (
        "2024", "revenue", "35%", "1000", "million", "usd",
    )


def test_document_query_terms_preserve_chinese_tokens() -> None:
    terms = _document_query_terms(_question("该公司在2024年的营业收入和净利润是多少？"))
    assert "2024" in terms
    assert "营业收入" in terms
    assert "净利润" in terms


def test_all_stopword_query_falls_back_to_original_terms() -> None:
    question = _question("what is the of and by")
    assert _document_query_terms(question) == _question_terms(question)
    assert _document_query_terms(question) == ("what", "is", "the", "of", "and", "by")


def test_question_terms_remain_unfiltered() -> None:
    question = _question("What was the revenue?", options={"A": "in 2024"})
    assert _question_terms(question) == ("what", "was", "the", "revenue", "in", "2024")


def test_evidence_terms_remain_unfiltered() -> None:
    question = _question("What was the revenue in the period?")
    assert _evidence_terms(question) == (
        "what", "was", "the", "revenue", "in", "period",
    )


def test_document_retriever_uses_filtered_terms_only() -> None:
    noise = CanonicalDocument(
        document_id="noise",
        domain="financial_reports",
        title="the of in for was",
        source_type="fixture",
        source_uri="fixture://noise",
        parser_name="fixture",
        parser_version="1",
        pages=(CanonicalPage(page_number=1, text="the of in for was " * 20, blocks=()),),
    )
    target = CanonicalDocument(
        document_id="target",
        domain="financial_reports",
        title="revenue report",
        source_type="fixture",
        source_uri="fixture://target",
        parser_name="fixture",
        parser_version="1",
        pages=(CanonicalPage(page_number=1, text="revenue was 100 million", blocks=()),),
    )
    store = InMemoryDocumentStore.from_documents((noise, target))

    hits = CanonicalDocumentRetriever(top_k=5).retrieve_documents(
        _question("What was the revenue in the report?"),
        ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,)),
        store,
    )

    assert [hit.document_id for hit in hits] == ["target"]
    assert hits[0].metadata["matched_title_terms"] == ["revenue", "report"]
    assert "was" not in hits[0].metadata["matched_body_terms"]


def test_stopword_matching_is_exact_not_substring_based() -> None:
    question = _question("asset format within revenue")
    assert _document_query_terms(question) == ("asset", "format", "within", "revenue")


def test_stopword_filter_is_case_insensitive_after_token_normalization() -> None:
    question = _question("WHAT Was THE Revenue")
    assert _document_query_terms(question) == ("revenue",)


def test_document_query_terms_include_filtered_option_terms() -> None:
    question = _question(
        "Which amount?",
        options={"A": "the total", "B": "average revenue"},
    )
    assert _document_query_terms(question) == (
        "amount",
        "total",
        "average",
        "revenue",
    )


def test_evidence_terms_keep_pure_stopword_query_unfiltered() -> None:
    question = _question("what is the of and by")
    assert _evidence_terms(question) == ("what", "is", "the", "of", "and", "by")


def test_explicit_scope_with_pure_stopwords_still_returns_document() -> None:
    document = CanonicalDocument(
        document_id="scoped",
        domain="financial_reports",
        title="unrelated title",
        source_type="fixture",
        source_uri="fixture://scoped",
        parser_name="fixture",
        parser_version="1",
        pages=(CanonicalPage(page_number=1, text="unrelated body", blocks=()),),
    )
    question = Question(
        qid="pure-stopword-explicit-scope",
        domain="financial_reports",
        text="what is the of and by",
        options={},
        answer_format="free_text",
        doc_ids=("scoped",),
    )

    hits = CanonicalDocumentRetriever(top_k=1).retrieve_documents(
        question,
        ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,)),
        InMemoryDocumentStore.from_documents((document,)),
    )

    assert [hit.document_id for hit in hits] == ["scoped"]


def test_non_stopword_tokens_with_underscores_are_preserved() -> None:
    question = _question("as_of total_amount in 2024")
    assert _document_query_terms(question) == ("as_of", "total_amount", "2024")
