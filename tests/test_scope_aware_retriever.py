from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from evidence.assembler import GroupedEvidenceAssembler
from retrieval.document_scope import DocumentCandidate, DocumentScopeResult
from retrieval.scope_aware import ScopeAwareEvidenceRetriever


class SpyDelegate:
    name = "spy_delegate"

    def __init__(self) -> None:
        self.questions = []

    def retrieve(self, question, classification):
        self.questions.append(question)
        doc_ids = tuple(question.doc_ids or question.candidate_doc_ids)
        return tuple(
            EvidenceCandidate(
                domain=question.domain,
                doc_id=doc_id,
                source=f"fixture://{doc_id}",
                text=f"evidence {doc_id}",
                score=1.0,
                retriever=self.name,
            )
            for doc_id in doc_ids
        )


class SpyResolver:
    def __init__(self, *doc_ids: str) -> None:
        self.doc_ids = tuple(doc_ids)
        self.calls = 0

    def resolve(self, question, classification):
        self.calls += 1
        candidates = tuple(
            DocumentCandidate(
                doc_id=doc_id,
                domain=question.domain,
                score=10.0 - index,
                rank=index + 1,
                matched_terms=("scope",),
                matched_title_terms=(),
                source_paths=(),
            )
            for index, doc_id in enumerate(self.doc_ids)
        )
        return DocumentScopeResult(
            qid=question.qid,
            domain=question.domain,
            candidate_doc_ids=self.doc_ids,
            candidates=candidates,
            query_terms=("scope",),
            strategy="spy_scope",
            provider_calls=0,
            warnings=(),
        )


def _question(*, doc_ids=(), candidate_doc_ids=()):
    return Question(
        qid="q1",
        domain="research",
        text="跨文档问题",
        options={},
        answer_format="free_text",
        doc_ids=doc_ids,
        candidate_doc_ids=candidate_doc_ids,
    )


def _classification():
    return ClassificationResult(labels=(QuestionLabel.CROSS_DOC,))


def test_scope_aware_retriever_resolves_candidate_scope_and_preserves_audit() -> None:
    delegate = SpyDelegate()
    resolver = SpyResolver("doc_a", "doc_b")
    retriever = ScopeAwareEvidenceRetriever(delegate, resolver)

    candidates = retriever.retrieve(_question(), _classification())

    assert resolver.calls == 1
    assert delegate.questions[0].doc_ids == ()
    assert delegate.questions[0].candidate_doc_ids == ("doc_a", "doc_b")
    assert {candidate.doc_id for candidate in candidates} == {"doc_a", "doc_b"}
    assert candidates.audit_metadata["scope_candidate_doc_ids"] == ["doc_a", "doc_b"]
    assert candidates.audit_metadata["retriever_scope_request_source"] == "document_scope_resolver"
    assert all(candidate.metadata["document_scope_strategy"] == "spy_scope" for candidate in candidates)

    bundle = GroupedEvidenceAssembler().assemble(_question(), _classification(), candidates)
    assert bundle.metadata["scope_candidate_doc_ids"] == ["doc_a", "doc_b"]
    assert bundle.metadata["solver_available_doc_ids"] == ["doc_a", "doc_b"]


def test_scope_aware_retriever_declared_scope_bypasses_resolver() -> None:
    delegate = SpyDelegate()
    resolver = SpyResolver("wrong")
    retriever = ScopeAwareEvidenceRetriever(delegate, resolver)

    candidates = retriever.retrieve(_question(doc_ids=("required",)), _classification())

    assert resolver.calls == 0
    assert delegate.questions[0].doc_ids == ("required",)
    assert candidates.audit_metadata["retriever_scope_request_source"] == "declared_doc_ids"
    assert candidates.audit_metadata["scope_candidate_doc_ids"] == ["required"]
