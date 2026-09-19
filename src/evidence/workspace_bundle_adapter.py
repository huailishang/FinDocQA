"""Build an existing EvidenceBundle from an explicit bounded page workspace.

This adapter is intentionally input-only: it validates a caller-supplied bounded
workspace, preserves page-level lineage, and reuses the existing question,
classifier, and evidence-assembly contracts. It performs no retrieval or model
calls and never expands beyond the supplied pages.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any, Mapping, Sequence

from agent.classifier import RuleBasedQuestionClassifier
from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
)
from evidence.assembler import GroupedEvidenceAssembler
from question.preparation import QuestionPreparationPipeline


_PAGE_SEMANTICS = "ONE_BASED_CANONICAL_PHYSICAL_PAGE"
_ADAPTER_VERSION = "workspace_bundle_adapter/v1"


@dataclass(frozen=True)
class WorkspaceBundleBuild:
    question: Question
    classification: ClassificationResult
    bundle: EvidenceBundle
    workspace_scope_id: str
    workspace_scope_sha256: str


class WorkspaceBundleAdapter:
    """Fail-closed adapter from a bounded page workspace to EvidenceBundle."""

    def __init__(
        self,
        *,
        preparation: QuestionPreparationPipeline | None = None,
        classifier: RuleBasedQuestionClassifier | None = None,
        assembler: GroupedEvidenceAssembler | None = None,
    ) -> None:
        self._preparation = preparation or QuestionPreparationPipeline()
        self._classifier = classifier or RuleBasedQuestionClassifier()
        self._assembler = assembler or GroupedEvidenceAssembler()

    def build(
        self,
        *,
        question_payload: Mapping[str, Any],
        document_family: str,
        workspace_pages: Sequence[int],
        page_text_by_number: Mapping[int, str],
        provenance_by_page: Mapping[int | str, Sequence[str]],
        scope_policy: Mapping[str, Any],
        workspace_kind: str,
    ) -> WorkspaceBundleBuild:
        payload = self._validate_question_payload(question_payload)
        family = str(document_family or "").strip()
        if not family:
            raise ValueError("document_family must be non-empty")

        kind = str(workspace_kind or "").strip()
        if not kind:
            raise ValueError("workspace_kind must be non-empty")

        pages = self._validate_pages(workspace_pages)
        normalized_policy = self._normalize_scope_policy(scope_policy, len(pages))
        texts = self._normalize_page_texts(pages, page_text_by_number)
        provenance = self._normalize_provenance(pages, provenance_by_page)

        prepared_payload = dict(payload)
        prepared_payload["doc_ids"] = (family,)
        prepared = self._preparation.prepare(prepared_payload)
        question = prepared.question
        if question.qid != str(payload["qid"]).strip():
            raise ValueError("prepared question qid does not match explicit qid")
        if tuple(question.doc_ids) != (family,):
            raise ValueError("prepared question document scope mismatch")

        classification = self._classifier.classify(question)
        workspace_scope_sha256 = self._workspace_hash(
            qid=question.qid,
            document_family=family,
            workspace_kind=kind,
            scope_policy=normalized_policy,
            pages=pages,
            page_text_by_number=texts,
            provenance_by_page=provenance,
        )
        workspace_scope_id = "workspace_" + workspace_scope_sha256[:16]

        candidates = tuple(
            self._candidate(
                question=question,
                document_family=family,
                page=page,
                text=texts[page],
                provenance=provenance[page],
                workspace_kind=kind,
                workspace_scope_id=workspace_scope_id,
                workspace_scope_sha256=workspace_scope_sha256,
            )
            for page in pages
        )
        assembled = self._assembler.assemble(question, classification, candidates)

        solver_pages = tuple(
            int(candidate.metadata.get("canonical_physical_page"))
            for candidate in assembled.candidates
        )
        if solver_pages != pages:
            raise ValueError("assembler changed bounded workspace page set or order")
        verification_pages = tuple(
            int(candidate.metadata.get("canonical_physical_page"))
            for candidate in assembled.verification_candidates
        )
        if verification_pages != pages:
            raise ValueError("assembler expanded verification candidates outside bounded workspace")

        workspace_metadata = {
            "document_family": family,
            "workspace_scope_id": workspace_scope_id,
            "workspace_scope_sha256": workspace_scope_sha256,
            "workspace_kind": kind,
            "workspace_pages": list(pages),
            "workspace_provenance_by_page": {
                str(page): list(provenance[page]) for page in pages
            },
            "workspace_scope_policy": dict(normalized_policy),
            "workspace_adapter_version": _ADAPTER_VERSION,
        }
        bundle = replace(
            assembled,
            metadata={**dict(assembled.metadata or {}), **workspace_metadata},
        )
        return WorkspaceBundleBuild(
            question=question,
            classification=classification,
            bundle=bundle,
            workspace_scope_id=workspace_scope_id,
            workspace_scope_sha256=workspace_scope_sha256,
        )

    @staticmethod
    def _validate_question_payload(
        question_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(question_payload, Mapping):
            raise TypeError("question_payload must be a mapping")
        payload = dict(question_payload)
        qid = str(payload.get("qid") or "").strip()
        if not qid:
            raise ValueError("question_payload must contain an explicit non-empty qid")
        text = str(
            payload.get("question")
            or payload.get("text")
            or payload.get("query")
            or ""
        ).strip()
        if not text:
            raise ValueError("question_payload must contain non-empty question/text")
        payload["qid"] = qid
        return payload

    @staticmethod
    def _validate_pages(workspace_pages: Sequence[int]) -> tuple[int, ...]:
        if isinstance(workspace_pages, (str, bytes)) or not isinstance(
            workspace_pages, Sequence
        ):
            raise TypeError("workspace_pages must be a sequence of positive integers")
        pages: list[int] = []
        for page in workspace_pages:
            if isinstance(page, bool) or not isinstance(page, int) or page <= 0:
                raise ValueError("workspace_pages must contain positive integers only")
            pages.append(page)
        if not pages:
            raise ValueError("workspace_pages must be non-empty")
        if len(set(pages)) != len(pages):
            raise ValueError("workspace_pages must not contain duplicates")
        return tuple(pages)

    @staticmethod
    def _normalize_scope_policy(
        scope_policy: Mapping[str, Any],
        page_count: int,
    ) -> dict[str, Any]:
        if not isinstance(scope_policy, Mapping):
            raise TypeError("scope_policy must be a mapping")
        policy = dict(scope_policy)
        if policy.get("page_index_semantics") != _PAGE_SEMANTICS:
            raise ValueError("scope_policy.page_index_semantics must be one-based canonical")
        if policy.get("fail_closed_on_outside_page") is not True:
            raise ValueError("scope_policy must fail closed on outside pages")
        if policy.get("fail_closed_on_unknown_page") is not True:
            raise ValueError("scope_policy must fail closed on unknown pages")
        if policy.get("full_document_fallback_allowed") is not False:
            raise ValueError("full-document fallback must be disabled")
        max_pages = policy.get("max_unique_pages")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("scope_policy.max_unique_pages must be a positive integer")
        if page_count > max_pages:
            raise ValueError("workspace page count exceeds scope_policy.max_unique_pages")
        return {
            "fail_closed_on_outside_page": True,
            "fail_closed_on_unknown_page": True,
            "full_document_fallback_allowed": False,
            "max_unique_pages": max_pages,
            "page_index_semantics": _PAGE_SEMANTICS,
        }

    @staticmethod
    def _normalize_page_texts(
        pages: Sequence[int],
        page_text_by_number: Mapping[int, str],
    ) -> dict[int, str]:
        if not isinstance(page_text_by_number, Mapping):
            raise TypeError("page_text_by_number must be a mapping")
        normalized: dict[int, str] = {}
        for page in pages:
            if page not in page_text_by_number:
                raise ValueError(f"missing page text for page {page}")
            text = str(page_text_by_number[page] or "")
            if not text.strip():
                raise ValueError(f"page text must be non-empty for page {page}")
            normalized[page] = text
        return normalized

    @staticmethod
    def _normalize_provenance(
        pages: Sequence[int],
        provenance_by_page: Mapping[int | str, Sequence[str]],
    ) -> dict[int, tuple[str, ...]]:
        if not isinstance(provenance_by_page, Mapping):
            raise TypeError("provenance_by_page must be a mapping")
        normalized: dict[int, tuple[str, ...]] = {}
        for page in pages:
            raw = provenance_by_page.get(page)
            if raw is None:
                raw = provenance_by_page.get(str(page))
            if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise ValueError(f"missing provenance lanes for page {page}")
            lanes = tuple(
                sorted({str(lane).strip() for lane in raw if str(lane).strip()})
            )
            if not lanes:
                raise ValueError(f"provenance lanes must be non-empty for page {page}")
            normalized[page] = lanes
        return normalized

    @staticmethod
    def _workspace_hash(
        *,
        qid: str,
        document_family: str,
        workspace_kind: str,
        scope_policy: Mapping[str, Any],
        pages: Sequence[int],
        page_text_by_number: Mapping[int, str],
        provenance_by_page: Mapping[int, Sequence[str]],
    ) -> str:
        canonical = {
            "qid": qid,
            "document_family": document_family,
            "workspace_kind": workspace_kind,
            "scope_policy": dict(scope_policy),
            "pages": [
                {
                    "page": page,
                    "text_sha256": hashlib.sha256(
                        page_text_by_number[page].encode("utf-8")
                    ).hexdigest(),
                    "provenance": list(provenance_by_page[page]),
                }
                for page in pages
            ],
        }
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _candidate(
        *,
        question: Question,
        document_family: str,
        page: int,
        text: str,
        provenance: Sequence[str],
        workspace_kind: str,
        workspace_scope_id: str,
        workspace_scope_sha256: str,
    ) -> EvidenceCandidate:
        page_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return EvidenceCandidate(
            domain=question.domain,
            doc_id=document_family,
            source=f"bounded-workspace://{document_family}/page/{page}",
            text=text,
            retriever="bounded_workspace_input",
            metadata={
                "canonical_physical_page": page,
                "workspace_scope_id": workspace_scope_id,
                "workspace_scope_sha256": workspace_scope_sha256,
                "workspace_kind": workspace_kind,
                "workspace_page_provenance": list(provenance),
                "page_text_sha256": page_text_sha256,
            },
        )
