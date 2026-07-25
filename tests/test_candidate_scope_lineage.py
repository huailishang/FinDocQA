from __future__ import annotations

from pathlib import Path

from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from evidence.assembler import GroupedEvidenceAssembler
from retrieval.document_scope import DocumentCandidate, DocumentScopeResult
from retrieval.hybrid import AuditedEvidenceCandidates, LexicalHybridRetriever
from solvers.base import solver_available_doc_ids


class FixedScopeResolver:
    def __init__(self, *doc_ids: str) -> None:
        self.doc_ids = tuple(doc_ids)
        self.calls = 0

    def resolve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> DocumentScopeResult:
        self.calls += 1
        candidates = tuple(
            DocumentCandidate(
                doc_id=doc_id,
                domain=question.domain,
                score=100.0 - index,
                rank=index + 1,
                matched_terms=("保险责任",),
                matched_title_terms=(doc_id,),
                source_paths=(doc_id,),
            )
            for index, doc_id in enumerate(self.doc_ids)
        )
        return DocumentScopeResult(
            qid=question.qid,
            domain=question.domain,
            candidate_doc_ids=self.doc_ids,
            candidates=candidates,
            query_terms=("保险责任",),
            strategy="fixed_scope_test",
            provider_calls=0,
            warnings=(),
            effective_top_k=len(self.doc_ids),
        )


class CapturingAugmenter:
    def __init__(self) -> None:
        self.seen_doc_ids: tuple[str, ...] = ()

    def augment(self, question: Question, candidates):
        self.seen_doc_ids = tuple(question.doc_ids)
        return list(candidates), {"enabled": True, "table_rows_added": 0}


def _question() -> Question:
    return Question(
        qid="bb_scope_r1",
        domain="insurance",
        text="候选文档中的保险责任是什么？",
        options={"A": "保险责任", "B": "其他"},
        answer_format="mcq",
        doc_ids=(),
    )


def _classification() -> ClassificationResult:
    return ClassificationResult(labels=(QuestionLabel.CLAUSE_LOOKUP,))


def _write_doc(root: Path, doc_id: str) -> None:
    doc_dir = root / "insurance" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "page_0001.md").write_text(
        "保险责任 候选文档 给付责任",
        encoding="utf-8",
    )


def test_retriever_records_real_requested_resolved_missing_and_returned_docs(tmp_path: Path) -> None:
    _write_doc(tmp_path, "doc_present")
    resolver = FixedScopeResolver("doc_present", "doc_missing")
    retriever = LexicalHybridRetriever(tmp_path, document_scope_resolver=resolver)

    candidates = retriever.retrieve(_question(), _classification())

    assert resolver.calls == 1
    assert candidates.audit_metadata["scope_candidate_doc_ids"] == [
        "doc_present",
        "doc_missing",
    ]
    assert candidates.audit_metadata["retriever_requested_doc_ids"] == [
        "doc_present",
        "doc_missing",
    ]
    assert candidates.audit_metadata["retriever_resolved_doc_ids"] == ["doc_present"]
    assert candidates.audit_metadata["retriever_missing_doc_ids"] == ["doc_missing"]
    assert candidates.audit_metadata["retrieved_doc_ids"] == ["doc_present"]
    assert candidates.audit_metadata["retriever_scope_audit_source"] == "retriever_call_boundary"
    assert all(
        candidate.metadata["retriever_requested_doc_ids"]
        == ["doc_present", "doc_missing"]
        for candidate in candidates
    )


def test_zero_evidence_keeps_retriever_request_truth_through_assembler(tmp_path: Path) -> None:
    retriever = LexicalHybridRetriever(
        tmp_path,
        document_scope_resolver=FixedScopeResolver("missing_only"),
    )
    question = _question()

    candidates = retriever.retrieve(question, _classification())
    bundle = GroupedEvidenceAssembler().assemble(question, _classification(), candidates)

    assert candidates == ()
    assert candidates.audit_metadata["retriever_requested_doc_ids"] == ["missing_only"]
    assert bundle.metadata["scope_candidate_doc_ids"] == ["missing_only"]
    assert bundle.metadata["retriever_requested_doc_ids"] == ["missing_only"]
    assert bundle.metadata["retriever_resolved_doc_ids"] == []
    assert bundle.metadata["retriever_missing_doc_ids"] == ["missing_only"]
    assert bundle.metadata["retrieved_doc_ids"] == []
    assert bundle.metadata["retriever_scope_audit_source"] == "retriever_call_boundary"
    assert bundle.metadata["assembler_used_doc_ids"] == []
    assert bundle.question.doc_ids == ()


def test_candidate_scope_is_used_for_sidecar_augmentation_without_mutating_truth(tmp_path: Path) -> None:
    _write_doc(tmp_path, "candidate_doc")
    question = _question()
    candidates = LexicalHybridRetriever(
        tmp_path,
        document_scope_resolver=FixedScopeResolver("candidate_doc"),
    ).retrieve(question, _classification())
    assembler = GroupedEvidenceAssembler()
    capture = CapturingAugmenter()
    assembler._structured_table_augmenter = capture

    bundle = assembler.assemble(question, _classification(), candidates)

    assert capture.seen_doc_ids == ("candidate_doc",)
    assert bundle.question.doc_ids == ()
    assert bundle.metadata["augmentation_scope_source"] == "candidate_scope"
    assert bundle.metadata["solver_available_doc_ids"] == ["candidate_doc"]


def _audited_candidates(*candidates: EvidenceCandidate) -> AuditedEvidenceCandidates:
    return AuditedEvidenceCandidates(
        candidates,
        {
            "scope_candidate_doc_ids": ["in_scope"],
            "retriever_requested_doc_ids": ["in_scope"],
            "retriever_resolved_doc_ids": ["in_scope"],
            "retriever_missing_doc_ids": [],
            "retrieved_doc_ids": [str(candidate.doc_id) for candidate in candidates],
            "retriever_scope_request_source": "document_scope_resolver",
            "retriever_scope_audit_source": "retriever_call_boundary",
            "scope_expansion_reasons": {},
        },
    )


def test_out_of_scope_candidate_without_reason_is_fail_closed_metadata() -> None:
    candidates = _audited_candidates(
        EvidenceCandidate(
            domain="insurance",
            doc_id="in_scope",
            source="in.md",
            text="in",
        ),
        EvidenceCandidate(
            domain="insurance",
            doc_id="unexpected",
            source="unexpected.md",
            text="unexpected",
        ),
    )

    bundle = GroupedEvidenceAssembler().assemble(_question(), _classification(), candidates)

    assert bundle.metadata["out_of_scope_doc_ids"] == ["unexpected"]
    assert bundle.metadata["scope_expansion_reasons"] == {}
    assert bundle.metadata["out_of_scope_without_reason_doc_ids"] == ["unexpected"]


def test_explicit_legal_scope_expansion_reason_is_preserved() -> None:
    candidates = _audited_candidates(
        EvidenceCandidate(
            domain="insurance",
            doc_id="in_scope",
            source="in.md",
            text="in",
        ),
        EvidenceCandidate(
            domain="insurance",
            doc_id="corrected",
            source="corrected.md",
            text="corrected",
            metadata={"scope_expansion_reason": "corrective_retrieval"},
        ),
    )

    bundle = GroupedEvidenceAssembler().assemble(_question(), _classification(), candidates)

    assert bundle.metadata["out_of_scope_doc_ids"] == ["corrected"]
    assert bundle.metadata["scope_expansion_reasons"] == {
        "corrected": "corrective_retrieval"
    }
    assert bundle.metadata["out_of_scope_without_reason_doc_ids"] == []
    assert bundle.metadata["unknown_scope_expansion_reason_doc_ids"] == []


def test_solver_available_docs_follow_actual_solver_candidate_view() -> None:
    candidates = _audited_candidates(
        EvidenceCandidate(
            domain="insurance",
            doc_id="in_scope",
            source="in.md",
            text="in",
        )
    )
    bundle = GroupedEvidenceAssembler().assemble(_question(), _classification(), candidates)

    assert solver_available_doc_ids(bundle) == ["in_scope"]
