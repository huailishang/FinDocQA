from document import CanonicalDocument, CanonicalPage
from document.store import InMemoryDocumentStore


def _doc(doc_id: str, domain: str = "financial_reports") -> CanonicalDocument:
    return CanonicalDocument(
        document_id=doc_id,
        domain=domain,
        title=doc_id,
        source_type="text",
        source_uri=f"{doc_id}.txt",
        parser_name="test",
        parser_version="",
        pages=(CanonicalPage(page_number=1, text=doc_id, blocks=()),),
    )


def test_store_is_domain_scoped() -> None:
    store = InMemoryDocumentStore.from_documents(
        [_doc("a"), _doc("b"), _doc("x", "insurance")]
    )
    assert store.get("financial_reports", "a").document_id == "a"
    assert store.get("insurance", "a") is None
    assert store.document_ids("financial_reports") == ("a", "b")
