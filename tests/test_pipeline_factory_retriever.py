from pathlib import Path

from agent.factory import PipelineFactory
from document.store import (
    AdaptedPageDocumentStore,
    FallbackDocumentStore,
    RawMineruDocumentStore,
)
from retrieval.canonical_lexical import CanonicalLexicalEvidenceRetriever
from retrieval.hybrid import LexicalHybridRetriever
from retrieval.scope_aware import ScopeAwareEvidenceRetriever


def test_factory_keeps_legacy_retriever_as_default(tmp_path: Path) -> None:
    factory = PipelineFactory(config={}, project_root=tmp_path)

    retriever = factory.build_retriever()

    assert isinstance(retriever, LexicalHybridRetriever)


def test_factory_builds_canonical_lexical_as_explicit_option(tmp_path: Path) -> None:
    config = {
        "pipeline": {"retriever": "canonical_lexical"},
        "paths": {"processed_docs": "adapted_primary"},
        "document_scope": {"enabled": False},
        "retrieval": {
            "canonical_raw_root": "canonical_raw",
            "canonical_document_top_k": 7,
            "canonical_top_k_per_doc": 5,
            "fallback_processed_docs": ["adapted_fallback"],
        },
    }
    factory = PipelineFactory(config=config, project_root=tmp_path)

    retriever = factory.build_retriever()

    assert isinstance(retriever, ScopeAwareEvidenceRetriever)
    assert isinstance(retriever.delegate, CanonicalLexicalEvidenceRetriever)
    assert isinstance(retriever.delegate.store, FallbackDocumentStore)
    stores = retriever.delegate.store.stores
    assert len(stores) == 3
    assert isinstance(stores[0], RawMineruDocumentStore)
    assert stores[0].root == tmp_path / "canonical_raw"
    assert isinstance(stores[1], AdaptedPageDocumentStore)
    assert stores[1].root == tmp_path / "adapted_primary"
    assert isinstance(stores[2], AdaptedPageDocumentStore)
    assert stores[2].root == tmp_path / "adapted_fallback"
    assert retriever.delegate.top_k_per_doc == 5
    assert retriever.delegate.document_retriever.top_k == 7
    assert retriever.document_scope_resolver is None
