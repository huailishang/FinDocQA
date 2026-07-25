"""Global evidence selection for bounded solver prompts.

Candidate-document scope remains an upstream retrieval/audit concept.  This
module selects a smaller prompt-visible evidence scope without rewriting the
candidate scope or verification-only evidence view.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
import math
import re
from typing import Any, Mapping, Sequence

from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel


_NUMERIC_ANCHOR_RE = re.compile(
    r"(?:\d{4}\s*年?|\d+(?:\.\d+)?\s*(?:%|％|个百分点|万|亿|元|倍))"
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[\u4e00-\u9fff]{2,8}")
_FORMULA_RE = re.compile(r"(?:=|÷|/|×|\*|同比|环比|占比|增长率|差额|比率|比例)")
_UNIT_RE = re.compile(r"(?:单位|人民币|万元|亿元|元|%|％|个百分点|倍)")
_TABLE_RE = re.compile(r"(?:^|\n)\s*\|.+\|", re.MULTILINE)


@dataclass(frozen=True)
class PromptEvidencePolicy:
    max_context_chars: int = 30_000
    max_candidates: int = 20
    min_candidates_per_doc: int = 1
    main_doc_max_candidates: int = 7
    other_doc_max_candidates: int = 5
    near_duplicate_overlap: float = 0.72
    policy_source: str = "bb_p0_03e_a_r1_global_evidence_compaction_v1"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "PromptEvidencePolicy":
        values = dict(raw or {})
        return cls(
            max_context_chars=_positive_int(
                values.get("max_context_chars"), cls.max_context_chars
            ),
            max_candidates=_positive_int(
                values.get("max_candidates"), cls.max_candidates
            ),
            min_candidates_per_doc=_positive_int(
                values.get("min_candidates_per_doc"), cls.min_candidates_per_doc
            ),
            main_doc_max_candidates=_positive_int(
                values.get("main_doc_max_candidates"), cls.main_doc_max_candidates
            ),
            other_doc_max_candidates=_positive_int(
                values.get("other_doc_max_candidates"), cls.other_doc_max_candidates
            ),
            near_duplicate_overlap=_bounded_float(
                values.get("near_duplicate_overlap"), cls.near_duplicate_overlap
            ),
            policy_source=str(
                values.get("policy_source") or cls.policy_source
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PromptEvidenceSelection:
    selected_candidates: tuple[EvidenceCandidate, ...]
    original_candidate_count: int
    deduplicated_candidate_count: int
    selected_candidate_count: int
    dropped_candidate_count: int
    selected_doc_ids: tuple[str, ...]
    scope_doc_ids: tuple[str, ...]
    missing_scope_coverage_doc_ids: tuple[str, ...]
    estimated_rendered_context_chars: int
    drop_reason_counts: Mapping[str, int]
    selection_reasons_by_doc: Mapping[str, tuple[str, ...]]
    policy: PromptEvidencePolicy

    def to_metadata(self) -> dict[str, object]:
        return {
            "prompt_original_candidate_count": self.original_candidate_count,
            "prompt_deduplicated_candidate_count": self.deduplicated_candidate_count,
            "prompt_selected_candidate_count": self.selected_candidate_count,
            "prompt_dropped_candidate_count": self.dropped_candidate_count,
            "prompt_selected_doc_ids": list(self.selected_doc_ids),
            "prompt_scope_doc_ids": list(self.selected_doc_ids),
            "prompt_scope_missing_candidate_doc_ids": list(
                self.missing_scope_coverage_doc_ids
            ),
            "prompt_selection_estimated_context_chars": (
                self.estimated_rendered_context_chars
            ),
            "prompt_drop_reason_counts": dict(self.drop_reason_counts),
            "prompt_selection_reasons_by_doc": {
                doc_id: list(reasons)
                for doc_id, reasons in self.selection_reasons_by_doc.items()
            },
            "prompt_evidence_policy": self.policy.to_dict(),
            "prompt_evidence_policy_source": self.policy.policy_source,
        }


@dataclass(frozen=True)
class _ScoredCandidate:
    index: int
    candidate: EvidenceCandidate
    score: float
    features: tuple[str, ...]
    option_focus: str
    structural: bool


class GlobalPromptEvidenceSelector:
    """Select globally useful windows under one prompt-level budget."""

    def __init__(self, policy: PromptEvidencePolicy | None = None) -> None:
        self.policy = policy or PromptEvidencePolicy()

    def select(
        self,
        question: Question,
        classification: ClassificationResult,
        candidates: Sequence[EvidenceCandidate],
        *,
        scope_candidate_doc_ids: Sequence[str] = (),
    ) -> PromptEvidenceSelection:
        original = list(candidates)
        scope_docs = tuple(
            dict.fromkeys(
                str(value) for value in scope_candidate_doc_ids if str(value)
            )
        )
        if not scope_docs:
            scope_docs = tuple(
                dict.fromkeys(str(item.doc_id) for item in original if str(item.doc_id))
            )
        doc_rank = {doc_id: index for index, doc_id in enumerate(scope_docs)}
        numeric_anchors = _numeric_anchors(question)
        query_terms = _query_terms(question)
        max_raw_score = max((float(item.score or 0.0) for item in original), default=1.0)

        scored = [
            self._score_candidate(
                index=index,
                candidate=candidate,
                doc_rank=doc_rank,
                max_raw_score=max_raw_score,
                numeric_anchors=numeric_anchors,
                query_terms=query_terms,
            )
            for index, candidate in enumerate(original)
        ]
        deduped, duplicate_indices = self._deduplicate(scored)
        ranked = sorted(
            deduped,
            key=lambda item: (
                item.score,
                -doc_rank.get(str(item.candidate.doc_id), len(doc_rank)),
                -item.index,
            ),
            reverse=True,
        )

        selected: list[_ScoredCandidate] = []
        selected_indices: set[int] = set()
        selected_by_doc: Counter[str] = Counter()
        reasons_by_index: dict[int, set[str]] = defaultdict(set)

        def add(item: _ScoredCandidate, reason: str) -> bool:
            if item.index in selected_indices:
                reasons_by_index[item.index].add(reason)
                return True
            if len(selected) >= self.policy.max_candidates:
                return False
            doc_id = str(item.candidate.doc_id)
            doc_limit = (
                self.policy.main_doc_max_candidates
                if doc_rank.get(doc_id, 999) == 0
                else self.policy.other_doc_max_candidates
            )
            if selected_by_doc[doc_id] >= doc_limit:
                return False
            projected = [*selected, item]
            projected_candidates = self._ordered_candidates(projected, doc_rank)
            projected_chars = estimate_rendered_context_chars(
                question, projected_candidates
            )
            if projected_chars > self.policy.max_context_chars:
                return False
            selected.append(item)
            selected_indices.add(item.index)
            selected_by_doc[doc_id] += 1
            reasons_by_index[item.index].add(reason)
            return True

        # One strong candidate per candidate document preserves minimum audit
        # coverage without pretending every document is required evidence.
        by_doc: dict[str, list[_ScoredCandidate]] = defaultdict(list)
        for item in ranked:
            by_doc[str(item.candidate.doc_id)].append(item)
        for doc_id in scope_docs:
            for item in by_doc.get(doc_id, [])[: self.policy.min_candidates_per_doc]:
                add(item, "candidate_doc_minimum_coverage")

        # Preserve one independently retrieved window for each option when
        # available. This prevents high-scoring generic windows from crowding
        # out a lower-scoring option-specific fact.
        for label in sorted(question.options):
            option_items = [item for item in ranked if item.option_focus == label]
            if option_items:
                add(option_items[0], f"option_focus_{label}")

        labels = set(classification.labels)
        if QuestionLabel.CALCULATION in labels or question.answer_format == "freeform":
            structural = [item for item in ranked if item.structural]
            for item in structural[:4]:
                add(item, "calculation_or_extraction_structure_anchor")

        # Cross-document questions must retain more than one prompt-visible
        # document when multiple candidate documents exist.
        if QuestionLabel.CROSS_DOC in labels and len(scope_docs) > 1:
            selected_docs = {str(item.candidate.doc_id) for item in selected}
            for item in ranked:
                if str(item.candidate.doc_id) not in selected_docs:
                    if add(item, "cross_document_minimum_coverage"):
                        selected_docs.add(str(item.candidate.doc_id))
                if len(selected_docs) >= 2:
                    break

        for item in ranked:
            add(item, "global_relevance_fill")

        ordered_scored = sorted(
            selected,
            key=lambda item: (
                doc_rank.get(str(item.candidate.doc_id), len(doc_rank)),
                -item.score,
                item.index,
            ),
        )
        enriched: list[EvidenceCandidate] = []
        reasons_by_doc: dict[str, list[str]] = defaultdict(list)
        for item in ordered_scored:
            reasons = sorted(reasons_by_index[item.index])
            doc_id = str(item.candidate.doc_id)
            reasons_by_doc[doc_id].extend(reasons)
            enriched.append(
                replace(
                    item.candidate,
                    metadata={
                        **dict(item.candidate.metadata or {}),
                        "prompt_selection_score": round(item.score, 6),
                        "prompt_selection_features": list(item.features),
                        "prompt_selection_reasons": reasons,
                        "prompt_selection_policy_source": self.policy.policy_source,
                    },
                )
            )

        selected_docs = tuple(
            dict.fromkeys(str(item.doc_id) for item in enriched if str(item.doc_id))
        )
        missing_scope_docs = tuple(
            doc_id for doc_id in scope_docs if doc_id not in selected_docs
        )
        primary_drop_reasons: Counter[str] = Counter()
        for item in scored:
            if item.index in selected_indices:
                continue
            if item.index in duplicate_indices:
                primary_drop_reasons["near_duplicate"] += 1
                continue
            doc_id = str(item.candidate.doc_id)
            doc_limit = (
                self.policy.main_doc_max_candidates
                if doc_rank.get(doc_id, 999) == 0
                else self.policy.other_doc_max_candidates
            )
            if selected_by_doc[doc_id] >= doc_limit:
                primary_drop_reasons["per_doc_cap"] += 1
            elif len(selected) >= self.policy.max_candidates:
                primary_drop_reasons["global_candidate_cap"] += 1
            else:
                projected_candidates = self._ordered_candidates(
                    [*selected, item], doc_rank
                )
                if estimate_rendered_context_chars(
                    question, projected_candidates
                ) > self.policy.max_context_chars:
                    primary_drop_reasons["context_char_budget"] += 1
                else:
                    primary_drop_reasons["lower_global_priority"] += 1
        return PromptEvidenceSelection(
            selected_candidates=tuple(enriched),
            original_candidate_count=len(original),
            deduplicated_candidate_count=len(deduped),
            selected_candidate_count=len(enriched),
            dropped_candidate_count=len(original) - len(enriched),
            selected_doc_ids=selected_docs,
            scope_doc_ids=scope_docs,
            missing_scope_coverage_doc_ids=missing_scope_docs,
            estimated_rendered_context_chars=estimate_rendered_context_chars(
                question, enriched
            ),
            drop_reason_counts=dict(sorted(primary_drop_reasons.items())),
            selection_reasons_by_doc={
                doc_id: tuple(dict.fromkeys(reasons))
                for doc_id, reasons in reasons_by_doc.items()
            },
            policy=self.policy,
        )

    def _score_candidate(
        self,
        *,
        index: int,
        candidate: EvidenceCandidate,
        doc_rank: Mapping[str, int],
        max_raw_score: float,
        numeric_anchors: set[str],
        query_terms: set[str],
    ) -> _ScoredCandidate:
        text = _candidate_text(candidate)
        metadata = dict(candidate.metadata or {})
        rank = doc_rank.get(str(candidate.doc_id), len(doc_rank))
        normalized_raw = max(0.0, float(candidate.score or 0.0)) / max(
            1.0, max_raw_score
        )
        score = normalized_raw * 60.0 + 80.0 / (rank + 1)
        features: list[str] = ["retrieval_score", f"candidate_doc_rank_{rank + 1}"]

        matched_terms = {
            str(value).strip().lower()
            for value in metadata.get("matched_terms", []) or []
            if str(value).strip()
        }
        query_hits = matched_terms & query_terms
        if query_hits:
            score += min(32.0, len(query_hits) * 2.0)
            features.append("query_term_coverage")

        numeric_hits = {anchor for anchor in numeric_anchors if anchor in text}
        if numeric_hits:
            score += min(40.0, len(numeric_hits) * 8.0)
            features.append("numeric_or_temporal_anchor")

        option_focus = str(metadata.get("option_focus") or "").strip().upper()
        if option_focus:
            score += 34.0
            features.append(f"option_focus_{option_focus}")
        if metadata.get("exact_option_page") is True:
            score += 45.0
            features.append("exact_option_page")

        structural = False
        if _FORMULA_RE.search(text):
            score += 18.0
            features.append("formula_or_metric_relation")
            structural = True
        if _UNIT_RE.search(text):
            score += 10.0
            features.append("unit_anchor")
            structural = True
        if _TABLE_RE.search(text):
            score += 20.0
            features.append("table_header_or_row_context")
            structural = True
        if candidate.section_title:
            score += 4.0
            features.append("section_context")

        score_breakdown = metadata.get("score_breakdown")
        if isinstance(score_breakdown, Mapping):
            numeric_score = float(score_breakdown.get("numeric_hits", 0) or 0)
            title_score = float(score_breakdown.get("title_hits", 0) or 0)
            score += min(18.0, numeric_score * 1.5) + min(8.0, title_score * 2.0)
            if numeric_score:
                structural = True

        return _ScoredCandidate(
            index=index,
            candidate=candidate,
            score=score,
            features=tuple(dict.fromkeys(features)),
            option_focus=option_focus,
            structural=structural,
        )

    def _deduplicate(
        self, scored: Sequence[_ScoredCandidate]
    ) -> tuple[list[_ScoredCandidate], set[int]]:
        kept: list[_ScoredCandidate] = []
        dropped: set[int] = set()
        for item in sorted(scored, key=lambda value: value.score, reverse=True):
            duplicate = False
            for existing in kept:
                if str(item.candidate.doc_id) != str(existing.candidate.doc_id):
                    continue
                same_source = str(item.candidate.source) == str(existing.candidate.source)
                overlap = _text_overlap(
                    _candidate_text(item.candidate),
                    _candidate_text(existing.candidate),
                )
                if same_source and overlap >= self.policy.near_duplicate_overlap:
                    duplicate = True
                    break
                if overlap >= 0.92:
                    duplicate = True
                    break
            if duplicate:
                dropped.add(item.index)
            else:
                kept.append(item)
        return kept, dropped

    @staticmethod
    def _ordered_candidates(
        selected: Sequence[_ScoredCandidate], doc_rank: Mapping[str, int]
    ) -> list[EvidenceCandidate]:
        return [
            item.candidate
            for item in sorted(
                selected,
                key=lambda value: (
                    doc_rank.get(str(value.candidate.doc_id), len(doc_rank)),
                    -value.score,
                    value.index,
                ),
            )
        ]


def estimate_rendered_context_chars(
    question: Question, candidates: Sequence[EvidenceCandidate]
) -> int:
    """Mirror GroupedEvidenceAssembler rendering without materialising text."""
    by_doc: dict[str, list[EvidenceCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_doc[str(candidate.doc_id)].append(candidate)
    parts = [f"[QUESTION] {question.text}", "[OPTIONS]"]
    parts.extend(f"{key}. {value}" for key, value in sorted(question.options.items()))
    parts.append("[EVIDENCE]")
    render_doc_ids = [str(value) for value in question.doc_ids]
    if not render_doc_ids:
        render_doc_ids = list(
            dict.fromkeys(str(candidate.doc_id) for candidate in candidates)
        )
    for doc_id in render_doc_ids:
        parts.append(f"\n[DOC {doc_id}]")
        doc_candidates = by_doc.get(doc_id, [])
        if not doc_candidates:
            parts.append("No retrieved evidence for this document.")
            continue
        for index, candidate in enumerate(doc_candidates, start=1):
            parts.append(_render_block(candidate, index).strip())
    return len("\n\n".join(parts))


def _render_block(candidate: EvidenceCandidate, index: int) -> str:
    return (
        f"[SOURCE {index}] {candidate.source}\n"
        f"{candidate.before_text}\n{candidate.text}\n{candidate.after_text}"
    )


def _candidate_text(candidate: EvidenceCandidate) -> str:
    return "\n".join(
        value for value in (candidate.before_text, candidate.text, candidate.after_text) if value
    )


def _numeric_anchors(question: Question) -> set[str]:
    source = "\n".join([question.text, *question.options.values()])
    return {"".join(value.split()) for value in _NUMERIC_ANCHOR_RE.findall(source)}


def _query_terms(question: Question) -> set[str]:
    source = "\n".join([question.text, *question.options.values()]).lower()
    return {value.strip() for value in _TOKEN_RE.findall(source) if value.strip()}


def _text_overlap(left: str, right: str) -> float:
    a = "".join(left.split())
    b = "".join(right.split())
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    a_grams = {a[index : index + 4] for index in range(max(0, len(a) - 3))}
    b_grams = {b[index : index + 4] for index in range(max(0, len(b) - 3))}
    if not a_grams or not b_grams:
        return 0.0
    return len(a_grams & b_grams) / min(len(a_grams), len(b_grams))


def _positive_int(value: object, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bounded_float(value: object, default: float) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0.0 < parsed <= 1.0 and math.isfinite(parsed) else default
