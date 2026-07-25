from pathlib import Path

from contracts import ClassificationResult, Question, QuestionLabel
from evidence.assembler import GroupedEvidenceAssembler
from retrieval.document_scope import DocumentCandidate, DocumentScopeResult
from retrieval.hybrid import LexicalHybridRetriever


class SpyScopeResolver:
    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        self.calls = 0

    def resolve(self, question: Question, classification: ClassificationResult) -> DocumentScopeResult:
        self.calls += 1
        candidate = DocumentCandidate(
            doc_id=self.doc_id,
            domain=question.domain,
            score=99.0,
            rank=1,
            matched_terms=("候选",),
            matched_title_terms=("候选文档",),
            source_paths=(f"/tmp/{self.doc_id}",),
        )
        return DocumentScopeResult(
            qid=question.qid,
            domain=question.domain,
            candidate_doc_ids=(self.doc_id,),
            candidates=(candidate,),
            query_terms=("候选",),
            strategy="spy",
            provider_calls=0,
            warnings=(),
        )


def _write_doc(root: Path, doc_id: str, text: str) -> None:
    doc_dir = root / "insurance" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "page_0001.md").write_text(text, encoding="utf-8")


def _question(*, doc_ids=(), candidate_doc_ids=()) -> Question:
    return Question(
        qid="scope-test",
        domain="insurance",
        text="候选文档中的保险责任是什么？",
        options={"A": "候选", "B": "其他"},
        answer_format="mcq",
        doc_ids=doc_ids,
        candidate_doc_ids=candidate_doc_ids,
    )


def _classification() -> ClassificationResult:
    return ClassificationResult(labels=(QuestionLabel.CLAUSE_LOOKUP,))


def test_declared_doc_ids_bypass_scope_resolver(tmp_path: Path) -> None:
    _write_doc(tmp_path, "required", "保险责任 required")
    _write_doc(tmp_path, "candidate", "保险责任 candidate")
    spy = SpyScopeResolver("candidate")
    retriever = LexicalHybridRetriever(tmp_path, document_scope_resolver=spy)

    candidates = retriever.retrieve(
        _question(doc_ids=("required",), candidate_doc_ids=("candidate",)),
        _classification(),
    )

    assert spy.calls == 0
    assert candidates
    assert {candidate.doc_id for candidate in candidates} == {"required"}
    assert all("document_scope_strategy" not in candidate.metadata for candidate in candidates)


def test_multi_slot_empty_doc_ids_invokes_resolver_and_keeps_required_scope_empty(tmp_path: Path) -> None:
    _write_doc(tmp_path, "candidate", "候选文档 保险责任")
    spy = SpyScopeResolver("candidate")
    retriever = LexicalHybridRetriever(tmp_path, document_scope_resolver=spy)
    question = _question()

    candidates = retriever.retrieve(question, _classification())

    assert spy.calls == 1
    assert question.doc_ids == ()
    assert question.candidate_doc_ids == ()
    assert candidates
    assert {candidate.doc_id for candidate in candidates} == {"candidate"}
    assert all(candidate.metadata["document_scope_is_required_scope"] is False for candidate in candidates)
    assert all(candidate.metadata["document_scope_provider_calls"] == 0 for candidate in candidates)
    assert all(candidate.metadata["document_scope_candidates"][0]["score"] == 99.0 for candidate in candidates)

    bundle = GroupedEvidenceAssembler().assemble(question, _classification(), candidates)
    assert "[DOC candidate]" in bundle.prompt_context
    assert "候选文档" in bundle.prompt_context
    assert bundle.question.doc_ids == ()


def test_explicit_candidate_scope_is_retrieval_only_and_bypasses_resolver(tmp_path: Path) -> None:
    _write_doc(tmp_path, "candidate", "候选文档 保险责任")
    spy = SpyScopeResolver("unused")
    retriever = LexicalHybridRetriever(tmp_path, document_scope_resolver=spy)
    question = _question(candidate_doc_ids=("candidate",))

    candidates = retriever.retrieve(question, _classification())

    assert spy.calls == 0
    assert question.doc_ids == ()
    assert question.candidate_doc_ids == ("candidate",)
    assert {candidate.doc_id for candidate in candidates} == {"candidate"}
