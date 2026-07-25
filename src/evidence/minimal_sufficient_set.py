"""Offline shadow reduction to a minimal sufficient prompt evidence set.

BB-P0-04E deliberately runs *after* ``GlobalPromptEvidenceSelector``.  It does
not retrieve, rerank, or add evidence.  It only removes baseline-selected
windows when every observable mandatory requirement remains covered.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from evidence.prompt_evidence_selection import (
    PromptEvidenceSelection,
    estimate_rendered_context_chars,
)


_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*年?")
_VALUE_UNIT_RE = re.compile(
    r"(?<!\d)\d+(?:\.\d+)?\s*(?:%|％|个百分点|万亿元|亿元|万元|千元|元|倍|个月|月|天|日)"
)
_UNIT_RE = re.compile(r"个百分点|万亿元|亿元|万元|千元|元|%|％|倍|个月|月|天|日")
_TITLE_ENTITY_RE = re.compile(r"《[^》\n]{2,60}》")
_ORG_ENTITY_RE = re.compile(
    r"[A-Za-z0-9\u4e00-\u9fff·（）()\-]{2,40}"
    r"(?:人民银行|银行|保险公司|保险|证券公司|证券|基金|集团|公司|委员会|交易所|协会|研究院|中心)"
)
_LATIN_ENTITY_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9&_.-]{2,30}\b")
_FORMULA_RE = re.compile(r"(?:=|÷|/|×|\*|同比|环比|增长率|占比|比率|比例|差额)")
_TABLE_RE = re.compile(r"(?:^|\n)\s*\|.+\|", re.MULTILINE)
_NEGATION_RE = re.compile(r"(?:不得|不应|不能|不可以|不包括|不承担|不适用|禁止|未予|无权|未能)")
_EXCEPTION_RE = re.compile(r"(?:除外|除非|例外|但书|但(?:是|若|如)?|除[^。；，\n]{0,24}外)")
_CONDITION_RE = re.compile(r"(?:仅当|只有|前提|条件|必须|应当|若|如果|符合|达到|满足)")


@dataclass(frozen=True)
class EvidenceRequirement:
    """One deterministic coverage constraint over baseline-selected windows."""

    requirement_id: str
    category: str
    description: str
    matching_indices: tuple[int, ...]
    minimum_hits: int = 1

    def covered_by(self, active_indices: set[int]) -> bool:
        return len(active_indices.intersection(self.matching_indices)) >= self.minimum_hits

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MinimalSufficientEvidenceResult:
    """Shadow-only contraction result and its audit contract."""

    selected_candidates: tuple[EvidenceCandidate, ...]
    baseline_candidate_count: int
    minimal_candidate_count: int
    baseline_context_chars: int
    minimal_context_chars: int
    mandatory_requirement_count: int
    baseline_missing_requirement_ids: tuple[str, ...]
    minimal_missing_requirement_ids: tuple[str, ...]
    coverage_regression_requirement_ids: tuple[str, ...]
    removed_sources: tuple[str, ...]
    retained_sources: tuple[str, ...]
    requirements: tuple[EvidenceRequirement, ...]
    policy_source: str = "bb_p0_11_minimal_sufficient_evidence_shadow_v1"

    @property
    def reduction_ratio(self) -> float:
        if self.baseline_candidate_count <= 0:
            return 0.0
        return 1.0 - (self.minimal_candidate_count / self.baseline_candidate_count)

    @property
    def context_reduction_ratio(self) -> float:
        if self.baseline_context_chars <= 0:
            return 0.0
        return 1.0 - (self.minimal_context_chars / self.baseline_context_chars)

    @property
    def coverage_regression_count(self) -> int:
        return len(self.coverage_regression_requirement_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_source": self.policy_source,
            "baseline_candidate_count": self.baseline_candidate_count,
            "minimal_candidate_count": self.minimal_candidate_count,
            "candidate_reduction_ratio": self.reduction_ratio,
            "baseline_context_chars": self.baseline_context_chars,
            "minimal_context_chars": self.minimal_context_chars,
            "context_reduction_ratio": self.context_reduction_ratio,
            "mandatory_requirement_count": self.mandatory_requirement_count,
            "baseline_missing_requirement_ids": list(self.baseline_missing_requirement_ids),
            "minimal_missing_requirement_ids": list(self.minimal_missing_requirement_ids),
            "coverage_regression_requirement_ids": list(
                self.coverage_regression_requirement_ids
            ),
            "coverage_regression_count": self.coverage_regression_count,
            "removed_sources": list(self.removed_sources),
            "retained_sources": list(self.retained_sources),
            "requirements": [requirement.to_dict() for requirement in self.requirements],
        }


class CoverageRegressionError(RuntimeError):
    """Fail closed when shadow compression drops baseline-covered evidence."""

    def __init__(self, requirement_ids: Sequence[str]) -> None:
        self.requirement_ids = tuple(str(value) for value in requirement_ids)
        super().__init__(
            "minimal evidence compression lost mandatory coverage: "
            + ", ".join(self.requirement_ids)
        )


def ensure_no_coverage_regression(requirement_ids: Sequence[str]) -> None:
    """Raise before a reduced evidence set can be consumed when coverage regresses."""
    normalized = tuple(str(value) for value in requirement_ids if str(value))
    if normalized:
        raise CoverageRegressionError(normalized)


class MinimalSufficientEvidenceReducer:
    """Greedily remove redundant windows while preserving mandatory coverage.

    The input contract intentionally requires a ``PromptEvidenceSelection`` so
    this layer cannot become a competing first-stage reranker by accident.
    Existing selector scores are used only as a deterministic *removal* tie
    breaker; no new relevance score is computed here.
    """

    policy_source = "bb_p0_11_minimal_sufficient_evidence_shadow_v1"

    def reduce(
        self,
        question: Question,
        classification: ClassificationResult,
        baseline_selection: PromptEvidenceSelection,
    ) -> MinimalSufficientEvidenceResult:
        if not isinstance(baseline_selection, PromptEvidenceSelection):
            raise TypeError(
                "baseline_selection must be GlobalPromptEvidenceSelector output"
            )

        baseline = tuple(baseline_selection.selected_candidates)
        requirements = self._build_requirements(
            question=question,
            classification=classification,
            baseline_selection=baseline_selection,
        )
        baseline_active = set(range(len(baseline)))
        baseline_missing = tuple(
            requirement.requirement_id
            for requirement in requirements
            if not requirement.covered_by(baseline_active)
        )

        active = set(baseline_active)
        covered_count_by_index = self._covered_requirement_count_by_index(
            requirements,
            len(baseline),
        )
        removal_order = sorted(
            active,
            key=lambda index: (
                covered_count_by_index[index],
                self._selector_score(baseline[index]),
                str(baseline[index].doc_id),
                str(baseline[index].source),
                index,
            ),
        )
        for index in removal_order:
            if len(active) <= 1:
                break
            tentative = set(active)
            tentative.remove(index)
            if all(
                requirement.covered_by(tentative)
                or requirement.requirement_id in baseline_missing
                for requirement in requirements
            ):
                active = tentative

        retained_indices = tuple(sorted(active))
        retained = tuple(baseline[index] for index in retained_indices)
        minimal_missing = tuple(
            requirement.requirement_id
            for requirement in requirements
            if not requirement.covered_by(active)
        )
        baseline_missing_set = set(baseline_missing)
        regressions = tuple(
            requirement_id
            for requirement_id in minimal_missing
            if requirement_id not in baseline_missing_set
        )
        ensure_no_coverage_regression(regressions)
        removed_sources = tuple(
            str(candidate.source)
            for index, candidate in enumerate(baseline)
            if index not in active
        )
        retained_sources = tuple(str(candidate.source) for candidate in retained)

        return MinimalSufficientEvidenceResult(
            selected_candidates=retained,
            baseline_candidate_count=len(baseline),
            minimal_candidate_count=len(retained),
            baseline_context_chars=estimate_rendered_context_chars(question, baseline),
            minimal_context_chars=estimate_rendered_context_chars(question, retained),
            mandatory_requirement_count=len(requirements),
            baseline_missing_requirement_ids=baseline_missing,
            minimal_missing_requirement_ids=minimal_missing,
            coverage_regression_requirement_ids=regressions,
            removed_sources=removed_sources,
            retained_sources=retained_sources,
            requirements=requirements,
            policy_source=self.policy_source,
        )

    def _build_requirements(
        self,
        *,
        question: Question,
        classification: ClassificationResult,
        baseline_selection: PromptEvidenceSelection,
    ) -> tuple[EvidenceRequirement, ...]:
        candidates = tuple(baseline_selection.selected_candidates)
        candidate_texts = tuple(_candidate_text(candidate) for candidate in candidates)
        requirements: list[EvidenceRequirement] = []
        seen_ids: set[str] = set()

        def add(
            requirement_id: str,
            category: str,
            description: str,
            matching_indices: Sequence[int],
            minimum_hits: int = 1,
        ) -> None:
            if requirement_id in seen_ids:
                return
            seen_ids.add(requirement_id)
            requirements.append(
                EvidenceRequirement(
                    requirement_id=requirement_id,
                    category=category,
                    description=description,
                    matching_indices=tuple(dict.fromkeys(int(i) for i in matching_indices)),
                    minimum_hits=max(1, int(minimum_hits)),
                )
            )

        # legacy declared docs are required truth.  multi-slot has only candidate
        # scope, so preserving one baseline-selected window per candidate doc is
        # deliberately conservative and never upgrades candidate scope to truth.
        scope_docs = tuple(
            dict.fromkeys(
                str(value)
                for value in (
                    question.doc_ids
                    if question.doc_ids
                    else baseline_selection.scope_doc_ids
                )
                if str(value)
            )
        )
        if not scope_docs:
            scope_docs = tuple(
                dict.fromkeys(str(candidate.doc_id) for candidate in candidates if str(candidate.doc_id))
            )
        doc_category = "required_document" if question.doc_ids else "candidate_document"
        for doc_id in scope_docs:
            indices = [
                index
                for index, candidate in enumerate(candidates)
                if str(candidate.doc_id) == doc_id
            ]
            add(
                f"{doc_category}:{doc_id}",
                doc_category,
                f"preserve prompt-visible coverage for document {doc_id}",
                indices,
            )
            lineage_indices = [
                index for index in indices if _lineage_complete(candidates[index])
            ]
            if lineage_indices:
                add(
                    f"lineage:{doc_id}",
                    "lineage",
                    f"preserve doc/page/source lineage for document {doc_id}",
                    lineage_indices,
                )

        # Preserve every option-specific lane that the upstream selector managed
        # to surface.  Missing option-focus evidence remains visible as an
        # upstream gap rather than being fabricated here.
        for label in sorted(str(value).upper() for value in question.options):
            option_indices = [
                index
                for index, candidate in enumerate(candidates)
                if str((candidate.metadata or {}).get("option_focus") or "").upper() == label
            ]
            if option_indices:
                add(
                    f"option_focus:{label}",
                    "option_focus",
                    f"preserve upstream option-focus evidence for option {label}",
                    option_indices,
                )

        question_surface = "\n".join([question.text, *question.options.values()])
        for category, anchor in _observable_question_anchors(question_surface):
            matching = [
                index
                for index, text in enumerate(candidate_texts)
                if _contains_normalized(text, anchor)
            ]
            add(
                f"{category}:{_requirement_key(anchor)}",
                category,
                f"preserve observable question/option anchor {anchor!r}",
                matching,
            )

        labels = set(classification.labels)
        if QuestionLabel.CALCULATION in labels or question.answer_format == "freeform":
            formula_indices = [
                index for index, text in enumerate(candidate_texts) if _FORMULA_RE.search(text)
            ]
            if formula_indices:
                add(
                    "calculation_structure:formula",
                    "calculation_structure",
                    "preserve a formula/metric-relation source window",
                    formula_indices,
                )
            unit_indices = [
                index for index, text in enumerate(candidate_texts) if _UNIT_RE.search(text)
            ]
            if unit_indices:
                add(
                    "calculation_structure:unit",
                    "calculation_structure",
                    "preserve a calculation unit source window",
                    unit_indices,
                )
            variable_indices = [
                index
                for index, candidate in enumerate(candidates)
                if _matched_question_terms(candidate, question_surface) >= 2
                and bool(re.search(r"\d", candidate_texts[index]))
            ]
            if variable_indices:
                add(
                    "calculation_structure:variables",
                    "calculation_structure",
                    "preserve a window binding question terms to numeric variables",
                    variable_indices,
                )
            table_indices = [
                index for index, text in enumerate(candidate_texts) if _TABLE_RE.search(text)
            ]
            if table_indices:
                add(
                    "calculation_structure:table",
                    "calculation_structure",
                    "preserve a table/header context window when present",
                    table_indices,
                )

        for name, pattern in (
            ("negation", _NEGATION_RE),
            ("exception", _EXCEPTION_RE),
            ("condition", _CONDITION_RE),
        ):
            protected = [
                index for index, text in enumerate(candidate_texts) if pattern.search(text)
            ]
            if protected:
                add(
                    f"protected_original:{name}",
                    "protected_original",
                    f"preserve at least one original {name} clause window",
                    protected,
                )

        if candidates:
            add(
                "baseline_nonempty_evidence",
                "minimum_evidence",
                "never reduce a non-empty baseline selection to zero windows",
                tuple(range(len(candidates))),
            )

        return tuple(requirements)

    @staticmethod
    def _covered_requirement_count_by_index(
        requirements: Sequence[EvidenceRequirement],
        candidate_count: int,
    ) -> list[int]:
        counts = [0] * candidate_count
        for requirement in requirements:
            for index in requirement.matching_indices:
                if 0 <= index < candidate_count:
                    counts[index] += 1
        return counts

    @staticmethod
    def _selector_score(candidate: EvidenceCandidate) -> float:
        metadata = dict(candidate.metadata or {})
        raw = metadata.get("prompt_selection_score")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(candidate.score or 0.0)


def _observable_question_anchors(text: str) -> tuple[tuple[str, str], ...]:
    anchors: list[tuple[str, str]] = []
    for value in _YEAR_RE.findall(text):
        anchors.append(("year_anchor", value.strip()))
    for value in _VALUE_UNIT_RE.findall(text):
        anchors.append(("numeric_unit_anchor", value.strip()))
    for value in _UNIT_RE.findall(text):
        anchors.append(("unit_anchor", value.strip()))
    for value in _TITLE_ENTITY_RE.findall(text):
        anchors.append(("entity_anchor", value.strip()))
    for value in _ORG_ENTITY_RE.findall(text):
        anchors.append(("entity_anchor", value.strip()))
    for value in _LATIN_ENTITY_RE.findall(text):
        anchors.append(("entity_anchor", value.strip()))
    return tuple(dict.fromkeys(anchors))


def _candidate_text(candidate: EvidenceCandidate) -> str:
    return "\n".join(
        value
        for value in (candidate.before_text, candidate.text, candidate.after_text)
        if value
    )


def _contains_normalized(text: str, anchor: str) -> bool:
    return _normalize_surface(anchor) in _normalize_surface(text)


def _normalize_surface(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _requirement_key(value: str) -> str:
    normalized = _normalize_surface(value)
    return re.sub(r"[^0-9a-z%\u4e00-\u9fff]+", "_", normalized)[:80] or "empty"


def _matched_question_terms(candidate: EvidenceCandidate, question_surface: str) -> int:
    matched_terms = {
        _normalize_surface(str(term))
        for term in ((candidate.metadata or {}).get("matched_terms") or ())
        if str(term).strip()
    }
    question_norm = _normalize_surface(question_surface)
    return sum(1 for term in matched_terms if term and term in question_norm)


def _lineage_complete(candidate: EvidenceCandidate) -> bool:
    if not str(candidate.doc_id).strip() or not str(candidate.source).strip():
        return False
    metadata: Mapping[str, Any] = candidate.metadata or {}
    page_fields = (
        metadata.get("page_number"),
        metadata.get("page"),
        metadata.get("page_path"),
        metadata.get("source_page"),
    )
    if any(value not in (None, "") for value in page_fields):
        return True
    source = str(candidate.source)
    return bool(re.search(r"(?:page[_-]?\d+|\.md(?::\d+)?)", source, re.IGNORECASE))
