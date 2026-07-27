"""Generic document-scope wrapper for store-bound evidence retrievers."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from contracts import (
    ClassificationResult,
    EvidenceCandidate,
    Question,
    retrieval_doc_ids,
)
from retrieval.document_scope import DocumentScopeResolver, DocumentScopeResult
from retrieval.interfaces import StoreBoundEvidenceRetriever
from retrieval.scope_audit import AuditedEvidenceCandidates, RetrievalScopeAudit


def _stable_doc_ids(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


@dataclass
class ScopeAwareEvidenceRetriever:
    """Resolve candidate documents, then delegate evidence retrieval.

    Required-document truth remains on Question.doc_ids only. Resolver output is
    passed to the delegate through Question.candidate_doc_ids and is recorded in
    retriever-call audit metadata for the evidence assembler.
    """

    delegate: StoreBoundEvidenceRetriever
    document_scope_resolver: DocumentScopeResolver | None = None
    name: str = "scope_aware"

    def retrieve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> Sequence[EvidenceCandidate]:
        requested, scope_result, scoped_question = self._resolve_scope(
            question,
            classification,
        )
        raw_candidates = tuple(self.delegate.retrieve(scoped_question, classification))
        retrieved_doc_ids = _stable_doc_ids([candidate.doc_id for candidate in raw_candidates])
        resolved_doc_ids = tuple(doc_id for doc_id in requested if doc_id in retrieved_doc_ids)
        missing_doc_ids = tuple(doc_id for doc_id in requested if doc_id not in retrieved_doc_ids)

        scope_candidate_doc_ids = (
            tuple(scope_result.candidate_doc_ids)
            if scope_result is not None
            else requested
        )
        audit = RetrievalScopeAudit(
            scope_candidate_doc_ids=scope_candidate_doc_ids,
            retriever_requested_doc_ids=requested,
            retriever_resolved_doc_ids=resolved_doc_ids,
            retriever_missing_doc_ids=missing_doc_ids,
            retrieved_doc_ids=retrieved_doc_ids,
            request_source=self._request_source(question, scope_result),
            provider_calls=int(scope_result.provider_calls) if scope_result is not None else 0,
            scope_expansion_reasons={},
        )
        audit_metadata = audit.to_metadata()
        scope_metadata = self._scope_metadata(scope_result)
        candidates = tuple(
            replace(
                candidate,
                metadata={
                    **dict(candidate.metadata or {}),
                    **scope_metadata,
                    **audit_metadata,
                },
            )
            for candidate in raw_candidates
        )
        return AuditedEvidenceCandidates(candidates, audit_metadata)

    def _resolve_scope(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> tuple[tuple[str, ...], DocumentScopeResult | None, Question]:
        explicit = retrieval_doc_ids(question)
        if explicit:
            return explicit, None, question
        if self.document_scope_resolver is None:
            return (), None, question
        result = self.document_scope_resolver.resolve(question, classification)
        candidate_doc_ids = tuple(result.candidate_doc_ids)
        scoped_question = replace(question, candidate_doc_ids=candidate_doc_ids)
        return candidate_doc_ids, result, scoped_question

    @staticmethod
    def _request_source(
        question: Question,
        scope_result: DocumentScopeResult | None,
    ) -> str:
        if question.doc_ids:
            return "declared_doc_ids"
        if question.candidate_doc_ids:
            return "question_candidate_doc_ids"
        if scope_result is not None:
            return "document_scope_resolver"
        return "none"

    @staticmethod
    def _scope_metadata(scope_result: DocumentScopeResult | None) -> dict[str, object]:
        if scope_result is None:
            return {}
        return {
            "document_scope_strategy": scope_result.strategy,
            "document_scope_candidate_doc_ids": list(scope_result.candidate_doc_ids),
            "document_scope_candidates": [
                candidate.to_dict() for candidate in scope_result.candidates
            ],
            "document_scope_query_terms": list(scope_result.query_terms),
            "document_scope_provider_calls": scope_result.provider_calls,
            "document_scope_warnings": list(scope_result.warnings),
            "document_scope_effective_top_k": scope_result.effective_top_k,
            "document_scope_adaptive_scope": scope_result.adaptive_scope,
            "document_scope_confidence": scope_result.confidence,
            "document_scope_matched_identity_terms": list(
                scope_result.matched_identity_terms
            ),
            "document_scope_coverage_groups": [
                dict(group) for group in scope_result.coverage_groups
            ],
            "document_scope_is_required_scope": False,
        }
