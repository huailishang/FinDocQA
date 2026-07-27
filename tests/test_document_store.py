from pathlib import Path

from document import CanonicalDocument
from document.store import RawMineruDocumentStore


def _document(domain: str, doc_id: str) -> CanonicalDocument:
    return CanonicalDocument(
        document_id=doc_id,
        domain=domain,
        title=doc_id,
        source_type="fixture",
        source_uri=f"fixture://{doc_id}",
        parser_name="fixture",
        parser_version="1",
        pages=(),
    )


def _raw_dir(root: Path, domain: str, doc_id: str) -> Path:
    path = root / domain / doc_id / "auto"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{doc_id}_content_list_v2.json").write_text("[]", encoding="utf-8")
    return path.parent


def test_raw_mineru_store_is_lazy_and_caches_documents(tmp_path: Path) -> None:
    _raw_dir(tmp_path, "insurance", "1")
    calls = []

    def loader(path, *, domain, doc_id, parser_version=""):
        calls.append((Path(path), domain, doc_id, parser_version))
        return _document(domain, doc_id)

    store = RawMineruDocumentStore(tmp_path, parser_version="test", loader=loader)

    assert store.document_ids("insurance") == ("1",)
    assert calls == []
    first = store.get("insurance", "1")
    second = store.get("insurance", "1")

    assert first is second
    assert len(calls) == 1
    assert calls[0][1:] == ("insurance", "1", "test")


def test_raw_mineru_store_missing_document_fails_closed(tmp_path: Path) -> None:
    store = RawMineruDocumentStore(tmp_path, loader=lambda *args, **kwargs: None)

    assert store.get("insurance", "missing") is None
    assert store.document_ids("insurance") == ()
    assert tuple(store.iter_documents("insurance")) == ()


def test_adapted_page_store_is_lazy(tmp_path: Path) -> None:
    from document.store import AdaptedPageDocumentStore

    doc_dir = tmp_path / "regulatory" / "strict_doc"
    doc_dir.mkdir(parents=True)
    (doc_dir / "page_0001.md").write_text("# 法规标题\n\n正文", encoding="utf-8")
    calls = []

    def loader(path, *, domain, doc_id, source_uri, source_type, parser_version=""):
        calls.append((Path(path), domain, doc_id, source_type))
        return _document(domain, doc_id)

    store = AdaptedPageDocumentStore(tmp_path, loader=loader)

    assert store.document_ids("regulatory") == ("strict_doc",)
    assert calls == []
    assert store.get("regulatory", "strict_doc") is not None
    assert store.get("regulatory", "strict_doc") is not None
    assert len(calls) == 1
    assert calls[0][3] == "adapted_pages"


def test_fallback_document_store_prefers_first_available_store() -> None:
    from document.store import FallbackDocumentStore, InMemoryDocumentStore

    primary_doc = _document("research", "same")
    fallback_same = CanonicalDocument(
        document_id="same",
        domain="research",
        title="fallback",
        source_type="fixture",
        source_uri="fixture://fallback",
        parser_name="fixture",
        parser_version="1",
        pages=(),
    )
    fallback_extra = _document("research", "extra")
    store = FallbackDocumentStore(
        (
            InMemoryDocumentStore.from_documents([primary_doc]),
            InMemoryDocumentStore.from_documents([fallback_same, fallback_extra]),
        )
    )

    assert store.get("research", "same") is primary_doc
    assert store.document_ids("research") == ("extra", "same")
    assert [doc.document_id for doc in store.iter_documents("research")] == ["same", "extra"]
