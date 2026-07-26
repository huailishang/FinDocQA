"""Shared data contracts for the enhanced baseline pipeline.

Keep this module lightweight and dependency-free. All pipeline modules should
communicate through these types so implementations can be swapped safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence


class QuestionLabel(str, Enum):
    """Routing labels used by the classifier and solver router."""

    CALCULATION = "calculation"
    MULTI_OPTION = "multi_option"
    CROSS_DOC = "cross_doc"
    CLAUSE_LOOKUP = "clause_lookup"
    FACT_LOOKUP = "fact_lookup"
    DEFAULT = "default"


@dataclass(frozen=True)
class QuestionAnswerContract:
    schema_version: str
    qid: str
    raw_type: str
    raw_answer_format: str
    answer_format: str
    allowed_labels: Sequence[str]
    min_selected: int
    max_selected: int
    canonical_order: Sequence[str]
    source_of_truth: str
    consistency_warnings: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class Question:
    qid: str
    domain: str
    text: str
    options: Mapping[str, str]
    answer_format: str
    # Declared/required documents from the question payload (legacy semantics).
    # Do not overload this field with multi-slot retrieval candidates.
    doc_ids: Sequence[str]
    # multi-slot retrieval scope.  These are candidate documents only and are not
    # evidence that every listed document is required by the question.
    candidate_doc_ids: Sequence[str] = field(default_factory=tuple)
    # Safe multi-slot output cardinality derived only from the official template's
    # occupied answer columns. legacy questions leave this unset.
    submission_slot_count: int | None = None
    # Per-slot expected output contract derived from official rules plus the
    # question text. Model-declared kinds are untrusted and must match these.
    submission_slot_contracts: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    raw: Mapping[str, Any] = field(default_factory=dict)
    answer_contract: QuestionAnswerContract | None = None


def retrieval_doc_ids(question: Question) -> tuple[str, ...]:
    """Return the explicit retrieval scope without changing required-doc truth."""
    declared = tuple(str(value) for value in question.doc_ids if str(value))
    if declared:
        return declared
    return tuple(str(value) for value in question.candidate_doc_ids if str(value))


def question_answer_slot_count(question: Question) -> int:
    """Return generic answer-value cardinality with legacy compatibility.

    A normal single-answer question has one answer value. Historical multi-slot
    datasets may carry an explicit output-slot count; expose that information
    through a generic API so new solvers do not depend on submission naming.
    """
    legacy = question.submission_slot_count
    if isinstance(legacy, int) and not isinstance(legacy, bool) and legacy > 0:
        return legacy
    return 1


def question_answer_slot_contracts(question: Question) -> tuple[Mapping[str, Any], ...]:
    """Return per-answer-value contracts independent of external output format."""
    return tuple(dict(item) for item in question.submission_slot_contracts or ())


@dataclass(frozen=True)
class DocumentPage:
    domain: str
    doc_id: str
    page_path: str
    page_number: Optional[int]
    text: str
    parser: str = "pymupdf4llm"


@dataclass(frozen=True)
class ClassificationResult:
    labels: Sequence[QuestionLabel]
    reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceCandidate:
    domain: str
    doc_id: str
    source: str
    text: str
    before_text: str = ""
    after_text: str = ""
    section_title: Optional[str] = None
    score: float = 0.0
    retriever: str = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceBundle:
    question: Question
    classification: ClassificationResult
    # Solver-visible candidates.  Routers, solvers, lineage inference and
    # prompt rendering must use this view only.
    candidates: Sequence[EvidenceCandidate]
    prompt_context: str
    estimated_tokens: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Optional verification-only expansion.  When empty, verification falls
    # back to candidates for historical fixture compatibility.
    verification_candidates: Sequence[EvidenceCandidate] = field(default_factory=tuple)


def get_solver_candidates(bundle: EvidenceBundle) -> tuple[EvidenceCandidate, ...]:
    """Return the immutable solver candidate view."""
    return tuple(bundle.candidates or ())


def get_verification_candidates(bundle: EvidenceBundle) -> tuple[EvidenceCandidate, ...]:
    """Return verification candidates, falling back to the solver view."""
    explicit = tuple(bundle.verification_candidates or ())
    return explicit if explicit else get_solver_candidates(bundle)


@dataclass(frozen=True)
class SolverResult:
    qid: str
    answer: str
    solver: str
    raw_output: str = ""
    confidence: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    qid: str
    answer: str
    changed: bool
    verifier: str
    notes: Sequence[str] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineResult:
    qid: str
    answer: str
    classification: ClassificationResult
    solver_result: SolverResult
    verification_result: Optional[VerificationResult] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Formal multi-slot submissions require an explicit, submission-visible
    # auditable reasoning summary.  Keep it as a first-class field so the CSV
    # writer never has to trust arbitrary metadata for reasoning provenance.
    reasoning: str = ""
    fallback_used: bool = False
    error: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Generic answer-value collection. Single-answer tasks usually contain one
    # value; structured/multi-value tasks may contain several independent values.
    # Core modules should prefer this field over any output-format-specific name.
    answer_values: Sequence[str] = field(default_factory=tuple)
    # Legacy competition/output compatibility. New code should use
    # ``answer_values``; this field remains temporarily so historical workflows
    # can migrate without a flag-day rewrite.
    submission_answers: Sequence[str] = field(default_factory=tuple)


def result_answer_values(result: PipelineResult) -> tuple[str, ...]:
    """Return generic answer values with backward-compatible fallbacks."""
    generic = tuple(str(value).strip() for value in result.answer_values if str(value).strip())
    if generic:
        return generic
    legacy = tuple(str(value).strip() for value in result.submission_answers if str(value).strip())
    if legacy:
        return legacy
    answer = str(result.answer or "").strip()
    return (answer,) if answer else ()


class QuestionLoader(Protocol):
    def load(self) -> Sequence[Question]:
        ...


class QuestionClassifier(Protocol):
    def classify(self, question: Question) -> ClassificationResult:
        ...


class EvidenceRetriever(Protocol):
    def retrieve(
        self, question: Question, classification: ClassificationResult
    ) -> Sequence[EvidenceCandidate]:
        ...


class EvidenceAssembler(Protocol):
    def assemble(
        self,
        question: Question,
        classification: ClassificationResult,
        candidates: Sequence[EvidenceCandidate],
    ) -> EvidenceBundle:
        ...


class Solver(Protocol):
    name: str

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        ...


class Verifier(Protocol):
    def verify(self, bundle: EvidenceBundle, result: SolverResult) -> VerificationResult:
        ...


class SubmissionWriter(Protocol):
    def write(self, results: Sequence[PipelineResult]) -> None:
        ...
