"""Lightweight lexical hybrid retrieval.

This first real retriever intentionally uses only the Python standard library.
It combines exact term hits, title/section hints, and simple token overlap so it
can run under the competition-friendly no-embedding constraint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel, retrieval_doc_ids
from retrieval.document_scope import DocumentScopeResolver, DocumentScopeResult
from retrieval.scope_audit import AuditedEvidenceCandidates, RetrievalScopeAudit
from retrieval.financial_target_page_locator import locate_financial_target_pages
from retrieval.focused_exact_pages import focused_page_candidates

# Regex for extracting numeric patterns from text
_NUMERIC_PATTERNS = [
    (r"\d{4}\s*年", 6.0),           # years: 2020年
    (r"\d+\.?\d*\s*[万亿]", 6.0),   # amounts: 100万, 1亿
    (r"\d+\.?\d*\s*[%％]", 5.0),    # percentages: 5% or 5％
    (r"\d+\.?\d*\s*元", 5.0),       # yuan amounts: 100元
]
_NUMERIC_WEIGHT = 6.0


class LexicalHybridRetriever:
    name = "lexical_hybrid"

    title_terms = (
        "责任免除", "等待期", "犹豫期", "保险责任", "现金价值", "退保", "赔付", "给付",
        "营业收入", "净利润", "募集资金", "决议", "义务", "除外",
    )

    # P4 default: window body size in chars. Must NOT be changed here (P6g only
    # exposes the before/after flank, not the window body).
    _WINDOW_SIZE = 1800
    # P4/P6g default: before/after context flank in chars around each scored
    # window. Exposed as config ``retrieval.context_flank_chars`` in P6g; the
    # committed default must remain 600 so behavior is unchanged when the key
    # is absent or set to 600.
    DEFAULT_CONTEXT_FLANK_CHARS = 600

    def __init__(
        self,
        processed_docs_dir: Path,
        top_k_per_doc: int = 5,
        windows_per_page: int = 3,
        top_k_per_doc_by_domain: Optional[Mapping[str, Any]] = None,
        context_flank_chars: Any = DEFAULT_CONTEXT_FLANK_CHARS,
        context_flank_chars_by_domain: Optional[Mapping[str, Any]] = None,
        fallback_processed_docs_dirs: Optional[Sequence[Path]] = None,
        document_scope_resolver: Optional[DocumentScopeResolver] = None,
    ) -> None:
        self.processed_docs_dir = processed_docs_dir
        self.fallback_processed_docs_dirs = tuple(
            Path(path) for path in (fallback_processed_docs_dirs or ())
        )
        self.document_scope_resolver = document_scope_resolver
        self.top_k_per_doc = top_k_per_doc
        # P4: how many highest-scoring windows to keep per page. Lets a single
        # long page (e.g. regulatory HTML/TXT parsed into one page_0001.md)
        # contribute more than one candidate instead of capping at one.
        self.windows_per_page = windows_per_page
        # P6e-7: optional per-domain top_k overrides. Empty by default so the
        # global ``top_k_per_doc`` is used for every domain, preserving the
        # pre-P6e-7 behavior exactly. Invalid (non-positive / non-int) values
        # are dropped silently and fall back to the global default for that
        # domain, matching the assembler's config-coercion style.
        self._top_k_by_domain: Dict[str, int] = self._coerce_domain_map(
            top_k_per_doc_by_domain
        )
        # P6g: before/after context flank chars. Coerced via the same strict
        # helper style as top_k (P6e-7a): non-integral floats / float-like
        # strings / bool / non-positive values fall back to the default 600.
        self.context_flank_chars: int = self._coerce_flank(context_flank_chars)
        # P6g-3: optional per-domain context_flank_chars overrides. Modeled on
        # ``top_k_per_doc_by_domain``: empty by default, so every domain uses
        # ``self.context_flank_chars``, preserving the pre-P6g-3 behavior
        # exactly. Invalid (non-positive / non-int) values are dropped silently
        # and fall back to the global flank for that domain.
        self._flank_by_domain: Dict[str, int] = self._coerce_domain_map(
            context_flank_chars_by_domain
        )

    @staticmethod
    def _coerce_domain_map(raw: Optional[Mapping[str, Any]]) -> Dict[str, int]:
        """Coerce an optional config mapping into a validated ``{domain: k}``.

        Non-dict input, missing keys, and non-positive / non-int values are
        ignored so a malformed local config never crashes a normal run.
        """
        if not raw or not isinstance(raw, Mapping):
            return {}
        result: Dict[str, int] = {}
        for domain, raw_value in raw.items():
            coerced = LexicalHybridRetriever._coerce_positive_int(raw_value)
            if coerced is not None:
                result[str(domain)] = coerced
        return result

    @staticmethod
    def _coerce_positive_int(raw: Any) -> Optional[int]:
        """Return a positive int, or None if the value is unusable.

        P6e-7a hardening: non-integral values are rejected rather than
        silently truncated. Accepted inputs:

        - ``int`` (positive);
        - integral ``float`` such as ``4.0``;
        - pure integer strings such as ``"4"``.

        Rejected inputs (return None, domain falls back to global default):

        - ``bool`` (``bool`` is a subclass of ``int`` but not a top_k);
        - zero and negative values;
        - non-integral floats such as ``4.5`` (rejected, not truncated to 4);
        - float-like strings such as ``"4.0"`` and ``"4.5"`` (rejected to
          avoid ambiguity with the integer-string path);
        - non-numeric strings such as ``"abc"``.
        """
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw if raw > 0 else None
        if isinstance(raw, float):
            if not raw.is_integer():
                return None
            value = int(raw)
            return value if value > 0 else None
        if isinstance(raw, str):
            stripped = raw.strip()
            # Pure integer string only. ``int("4.0")`` / ``int("4.5")`` raise
            # ValueError, so float-like strings are rejected here.
            try:
                value = int(stripped)
            except ValueError:
                return None
            return value if value > 0 else None
        return None

    @staticmethod
    def _coerce_flank(raw: Any) -> int:
        """Coerce a context-flank config value to a positive int.

        Reuses the same strict rules as ``_coerce_positive_int`` (P6e-7a): bool,
        zero/negative, non-integral floats (``4.5``), float-like strings
        (``"4.0"``/``"4.5"``), and non-numeric strings are rejected. On any
        rejection the P6g default ``DEFAULT_CONTEXT_FLANK_CHARS`` (600) is used
        so a malformed local config never changes committed behavior and never
        crashes a normal run.
        """
        coerced = LexicalHybridRetriever._coerce_positive_int(raw)
        if coerced is not None:
            return coerced
        return LexicalHybridRetriever.DEFAULT_CONTEXT_FLANK_CHARS

    def _resolve_top_k(self, domain: str) -> int:
        """Return the effective top_k for a question's domain.

        Falls back to the global ``top_k_per_doc`` when no valid override is
        registered for the domain. This is the single point where the
        per-domain policy is applied; ``_retrieve_doc`` calls it so every
        retrieval pass (main + per-option) honors the same effective value.
        """
        return self._top_k_by_domain.get(str(domain), self.top_k_per_doc)

    def _resolve_doc_dir(self, domain: str, doc_id: str) -> Optional[Path]:
        """Resolve a document from the primary corpus, then read-only fallbacks.

        This keeps the MinerU corpus authoritative while allowing documents that
        are not part of the 190-PDF bundle (for example generated strict_v3
        regulatory texts) to remain retrievable from the legacy parsed corpus.
        """
        roots = (self.processed_docs_dir, *self.fallback_processed_docs_dirs)
        for root in roots:
            candidate = root / domain / doc_id
            if candidate.is_dir() and any(
                page.is_file() and page.stat().st_size > 0
                for page in candidate.glob("page_*.md")
            ):
                return candidate
        return None

    def _resolve_flank(self, domain: str) -> int:
        """Return the effective context_flank_chars for a question's domain.

        Falls back to the global ``self.context_flank_chars`` when no valid
        per-domain override is registered. Modeled on ``_resolve_top_k`` so
        the per-domain flank pattern is auditable and consistent.
        """
        return self._flank_by_domain.get(str(domain), self.context_flank_chars)

    # ── public API ──────────────────────────────────────────────────

    def retrieve(
        self, question: Question, classification: ClassificationResult
    ) -> Sequence[EvidenceCandidate]:
        labels = set(classification.labels)
        queries = self._build_queries(question, classification)
        query_terms = self._query_terms(queries)
        numeric_terms = self._extract_numeric_terms(queries)
        scope_doc_ids, scope_result = self._resolve_retrieval_scope(question, classification)
        requested_doc_ids = tuple(dict.fromkeys(str(value) for value in scope_doc_ids if str(value)))

        candidates: List[EvidenceCandidate] = []
        resolved_doc_dirs: Dict[str, Path] = {}
        for doc_id in requested_doc_ids:
            doc_dir = self._resolve_doc_dir(question.domain, doc_id)
            if doc_dir is None:
                continue
            resolved_doc_dirs[doc_id] = doc_dir

            # Main retrieval pass
            doc_candidates = list(
                self._retrieve_doc(question, doc_id, doc_dir, query_terms, numeric_terms)
            )

            # For multi-option, do per-option retrieval passes to ensure coverage
            if QuestionLabel.MULTI_OPTION in labels:
                doc_candidates = self._retrieve_per_option(
                    question, doc_id, doc_dir, doc_candidates, query_terms, numeric_terms
                )

            candidates.extend(doc_candidates)

        focused = focused_page_candidates(question, resolved_doc_dirs)
        seen = {(candidate.source, candidate.text[:80]) for candidate in candidates}
        for candidate in focused:
            key = (candidate.source, candidate.text[:80])
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)

        target_page_candidates, target_page_audit = locate_financial_target_pages(
            question,
            resolved_doc_dirs,
            baseline_candidates=tuple(candidates),
            fallback_roots=self.fallback_processed_docs_dirs,
        )
        for candidate in target_page_candidates:
            key = (candidate.source, candidate.text[:80])
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)

        # Only multi-slot resolver-driven retrieval gets the detailed resolver
        # payload. Generic request lineage below is emitted for every path.
        if scope_result is not None:
            scope_meta = {
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
            candidates = [
                replace(
                    candidate,
                    metadata={**dict(candidate.metadata or {}), **scope_meta},
                )
                for candidate in candidates
            ]

        if scope_result is not None:
            scope_candidate_doc_ids = tuple(scope_result.candidate_doc_ids)
        elif question.candidate_doc_ids and not question.doc_ids:
            scope_candidate_doc_ids = tuple(
                dict.fromkeys(str(value) for value in question.candidate_doc_ids if str(value))
            )
        else:
            # legacy declared documents are retained as the effective scope for
            # uniform observability, without changing their required-doc truth.
            scope_candidate_doc_ids = requested_doc_ids

        resolved_doc_ids = tuple(resolved_doc_dirs)
        retrieved_doc_ids = tuple(
            dict.fromkeys(str(candidate.doc_id) for candidate in candidates if str(candidate.doc_id))
        )
        audit = RetrievalScopeAudit(
            scope_candidate_doc_ids=scope_candidate_doc_ids,
            retriever_requested_doc_ids=requested_doc_ids,
            retriever_resolved_doc_ids=resolved_doc_ids,
            retriever_missing_doc_ids=tuple(
                doc_id for doc_id in requested_doc_ids if doc_id not in resolved_doc_dirs
            ),
            retrieved_doc_ids=retrieved_doc_ids,
            request_source=self._scope_request_source(question, scope_result),
            provider_calls=int(scope_result.provider_calls) if scope_result is not None else 0,
            scope_expansion_reasons={},
        )
        audit_metadata = audit.to_metadata()
        audit_metadata["financial_target_page_locator"] = target_page_audit
        candidates = [
            replace(
                candidate,
                metadata={**dict(candidate.metadata or {}), **audit_metadata},
            )
            for candidate in candidates
        ]
        return AuditedEvidenceCandidates(candidates, audit_metadata)

    def _resolve_retrieval_scope(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> tuple[tuple[str, ...], DocumentScopeResult | None]:
        """Resolve retrieval scope while keeping required and candidate scopes separate."""
        explicit = retrieval_doc_ids(question)
        if explicit:
            return explicit, None
        if self.document_scope_resolver is None:
            return (), None
        result = self.document_scope_resolver.resolve(question, classification)
        return tuple(result.candidate_doc_ids), result

    @staticmethod
    def _scope_request_source(
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

    # ── query building ──────────────────────────────────────────────

    def _build_queries(
        self, question: Question, classification: ClassificationResult
    ) -> Sequence[str]:
        queries = [question.text]
        queries.extend(question.options.values())

        labels = set(classification.labels)
        if QuestionLabel.CLAUSE_LOOKUP in labels:
            queries.extend(["责任免除", "等待期", "除外", "应当", "不得", "不承担"])
        if QuestionLabel.CALCULATION in labels:
            queries.extend(["现金价值", "退保", "赔付", "比例", "计算", "金额", "保费"])
        if QuestionLabel.FACT_LOOKUP in labels:
            queries.extend(["营业收入", "净利润", "金额", "数据", "收入", "利润"])
        return [query for query in queries if query]

    def _query_terms(self, queries: Sequence[str]) -> List[str]:
        terms: list[str] = []
        for query in queries:
            terms.extend(self._tokenize(query))
            for title in self.title_terms:
                if title in query:
                    terms.append(title)
        # Also add any embedded numeric patterns from raw queries
        for query in queries:
            terms.extend(self._extract_numeric_terms([query]))
        return self._dedup_terms(terms)

    def _extract_numeric_terms(self, queries: Sequence[str]) -> List[str]:
        """Extract numeric patterns like years, amounts, percentages."""
        terms: list[str] = []
        for query in queries:
            for pattern, _ in _NUMERIC_PATTERNS:
                terms.extend(re.findall(pattern, query))
        return self._dedup_terms(terms)

    # ── retrieval ──────────────────────────────────────────────────

    def _retrieve_doc(
        self,
        question: Question,
        doc_id: str,
        doc_dir: Path,
        query_terms: Sequence[str],
        numeric_terms: Sequence[str],
    ) -> Sequence[EvidenceCandidate]:
        scored: list[Tuple[float, Path, str, int, int, list[str], Dict[str, Any]]] = []
        for page in sorted(doc_dir.glob("page_*.md")):
            text = page.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            # P4: collect up to windows_per_page top windows per page instead of
            # only the single best window. This is what lets single-page docs
            # (regulatory HTML/TXT) yield multiple candidates.
            for win in self._score_text_top_n(
                text, query_terms, numeric_terms, self.windows_per_page
            ):
                score, start, end, matched, breakdown = win
                if score > 0:
                    scored.append((score, page, text, start, end, matched, breakdown))

        if not scored:
            return self._fallback_first_page(question, doc_id, doc_dir, query_terms, numeric_terms)

        # Sort by score, take top_k_per_doc (P6e-7: effective value may be
        # overridden per question domain via config).
        effective_top_k = self._resolve_top_k(question.domain)
        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:effective_top_k]

        # Deduplicate overlapping windows from the same page
        top = self._deduplicate_windows(top)

        # Restore page order after dedup
        top.sort(key=lambda item: str(item[1]))

        effective_flank = self._resolve_flank(question.domain)
        candidates: list[EvidenceCandidate] = []
        for score, page, text, start, end, matched, breakdown in top:
            before_start = max(0, start - effective_flank)
            after_end = min(len(text), end + effective_flank)
            candidates.append(
                EvidenceCandidate(
                    domain=question.domain,
                    doc_id=doc_id,
                    source=str(page),
                    text=text[start:end].strip(),
                    before_text=text[before_start:start].strip(),
                    after_text=text[end:after_end].strip(),
                    section_title=self._nearest_title(text, start),
                    score=score,
                    retriever=self.name,
                    metadata={
                        "query_terms": list(query_terms),
                        "matched_terms": matched,
                        "score_breakdown": breakdown,
                    },
                )
            )
        return candidates

    def _retrieve_per_option(
        self,
        question: Question,
        doc_id: str,
        doc_dir: Path,
        existing: List[EvidenceCandidate],
        main_terms: Sequence[str],
        main_numeric: Sequence[str],
    ) -> List[EvidenceCandidate]:
        """For multi-option questions, retrieve per option and merge."""
        # P4: dedupe by (source, text-prefix) instead of just source. Previously
        # a single-page doc could never add a per-option candidate because every
        # option re-queried the same page_0001.md and the source was already
        # "seen". Keying on the snippet text lets distinct windows of the same
        # page through while still suppressing true duplicates.
        seen_keys: Set[str] = {
            (c.source, c.text[:80]) for c in existing if c.text
        }

        for opt_key, opt_text in question.options.items():
            if not opt_text.strip():
                continue
            opt_queries = [opt_text]
            opt_terms = self._query_terms(opt_queries)
            opt_numeric = self._extract_numeric_terms(opt_queries)
            # Keep the option-focused pass genuinely focused.  Mixing the
            # full-question terms back in can let generic high-frequency terms
            # crowd out the exact page needed for one option (for example a
            # cooling-off clause or one precise financial figure).
            focused_terms = self._dedup_terms(list(opt_terms))
            focused_numeric = self._dedup_terms(list(opt_numeric))
            if not focused_terms and not focused_numeric:
                continue

            opt_candidates = list(self._retrieve_doc(
                question, doc_id, doc_dir, focused_terms, focused_numeric
            ))
            question_terms = self._query_terms([question.text])
            question_numeric = self._extract_numeric_terms([question.text])
            exact_terms = self._dedup_terms(list(focused_terms) + list(question_terms))
            exact_numeric = self._dedup_terms(list(focused_numeric) + list(question_numeric))
            exact_candidate = self._retrieve_exact_option_page(
                question, doc_id, doc_dir, opt_key, opt_text,
                exact_terms, exact_numeric,
            )
            if exact_candidate is not None:
                opt_candidates.append(exact_candidate)
            for c in opt_candidates:
                key = (c.source, c.text[:80]) if c.text else (c.source, "")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                new_meta = dict(c.metadata)
                new_meta["option_focus"] = opt_key
                existing.append(
                    EvidenceCandidate(
                        domain=c.domain, doc_id=c.doc_id, source=c.source,
                        text=c.text, before_text=c.before_text,
                        after_text=c.after_text, section_title=c.section_title,
                        score=c.score * 0.95,  # slight penalty vs main-pass results
                        retriever=c.retriever, metadata=new_meta,
                    )
                )
        return existing

    @staticmethod
    def _normalize_numeric_text(value: str) -> str:
        """Normalize formatting differences in financial numeric literals."""
        return re.sub(r"[,，\s]", "", value or "")

    def _retrieve_exact_option_page(
        self,
        question: Question,
        doc_id: str,
        doc_dir: Path,
        opt_key: str,
        opt_text: str,
        option_terms: Sequence[str],
        numeric_terms: Sequence[str],
    ) -> Optional[EvidenceCandidate]:
        """Return the page with the strongest option-specific literal match."""
        strong_terms = [term for term in option_terms if len(term) >= 4]
        normalized_numbers = [
            self._normalize_numeric_text(term) for term in numeric_terms
            if self._normalize_numeric_text(term)
        ]
        best: Optional[Tuple[float, Path, str, list[str]]] = None
        for page in sorted(doc_dir.glob("page_*.md")):
            text = page.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            matched = [term for term in strong_terms if term in text]
            normalized_text = self._normalize_numeric_text(text)
            matched_numbers = [term for term in normalized_numbers if term in normalized_text]
            heading_hits = [
                title for title in self.title_terms
                if title in opt_text or title in question.text
                if any(
                    line.lstrip().startswith("#") and title in line
                    for line in text.splitlines()
                )
            ]
            score = (
                len(matched) * 8.0
                + len(matched_numbers) * 12.0
                + len(heading_hits) * 30.0
            )
            matched.extend(heading_hits)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, page, text, matched + matched_numbers)
        if best is None:
            return None
        score, page, text, matched = best
        return EvidenceCandidate(
            domain=question.domain,
            doc_id=doc_id,
            source=str(page),
            text=text[: self._WINDOW_SIZE].strip(),
            section_title=self._nearest_title(text, 0),
            score=score,
            retriever=self.name,
            metadata={
                "option_focus": opt_key,
                "exact_option_page": True,
                "matched_terms": matched[:15],
                "query_terms": list(option_terms),
            },
        )

    # ── scoring ────────────────────────────────────────────────────

    def _score_text(
        self, text: str, query_terms: Sequence[str], numeric_terms: Sequence[str]
    ) -> Tuple[float, int, int, list[str], Dict[str, Any]]:
        """Score a text passage and return the single best window.

        Returns:
            (best_score, best_start, best_end, matched_terms, breakdown)

        Kept for backwards compatibility with diagnostic scripts.
        """
        top = self._score_text_top_n(text, query_terms, numeric_terms, 1)
        if not top:
            return 0.0, 0, min(len(text), 1800), [], {}
        return top[0]

    def _score_text_top_n(
        self,
        text: str,
        query_terms: Sequence[str],
        numeric_terms: Sequence[str],
        n: int,
    ) -> List[Tuple[float, int, int, list[str], Dict[str, Any]]]:
        """Return up to ``n`` highest-scoring non-overlapping windows.

        Each window is ``(score, start, end, matched_terms, breakdown)``.
        Windows that overlap a higher-scoring kept window by more than 60% are
        dropped, so a long single page can contribute several distinct snippets
        instead of just one.
        """
        if n <= 0 or not text:
            return []

        windows = self._windows(text, size=1800, overlap=450)
        scored_windows: list[Tuple[float, int, int, list[str], Dict[str, Any]]] = []
        for start, end, window in windows:
            score = 0.0
            matched: list[str] = []
            exact_hits = 0
            title_hits = 0
            numeric_hits = 0

            # Score query terms
            for term in query_terms:
                count = window.count(term)
                if count:
                    capped = min(count, 5)
                    is_numeric = bool(re.search(r"\d", term))
                    weight = _NUMERIC_WEIGHT if is_numeric else (4.0 if len(term) >= 3 else 1.0)
                    score += capped * weight
                    exact_hits += capped
                    if term not in matched:
                        matched.append(term)

            # Score numeric terms after removing comma/space formatting so
            # question 1332亿元 matches report 1,332 亿元.
            normalized_window = self._normalize_numeric_text(window)
            for nterm in numeric_terms:
                normalized_term = self._normalize_numeric_text(nterm)
                count = normalized_window.count(normalized_term) if normalized_term else 0
                if count:
                    capped = min(count, 3)
                    score += capped * _NUMERIC_WEIGHT
                    numeric_hits += capped
                    if nterm not in matched:
                        matched.append(nterm)

            # Title term bonuses
            for title in self.title_terms:
                if title in window:
                    score += 3.0
                    title_hits += 1
                    if title not in matched:
                        matched.append(title)

            if score > 0:
                scored_windows.append(
                    (
                        score,
                        start,
                        end,
                        matched[:15],
                        {
                            "score": round(score, 1),
                            "exact_hits": exact_hits,
                            "title_hits": title_hits,
                            "numeric_hits": numeric_hits,
                        },
                    )
                )

        if not scored_windows:
            return []

        # Sort by score descending, then greedily keep non-overlapping windows.
        scored_windows.sort(key=lambda w: w[0], reverse=True)
        kept: list[Tuple[float, int, int, list[str], Dict[str, Any]]] = []
        for cand in scored_windows:
            _, c_start, c_end, _, _ = cand
            overlaps_existing = False
            for _, k_start, k_end, _, _ in kept:
                overlap_start = max(c_start, k_start)
                overlap_end = min(c_end, k_end)
                if overlap_start < overlap_end:
                    overlap_len = overlap_end - overlap_start
                    min_len = min(c_end - c_start, k_end - k_start)
                    if min_len > 0 and overlap_len / min_len > 0.6:
                        overlaps_existing = True
                        break
            if not overlaps_existing:
                kept.append(cand)
                if len(kept) >= n:
                    break
        return kept

    # ── deduplication ──────────────────────────────────────────────

    def _deduplicate_windows(
        self,
        candidates: List[Tuple[float, Path, str, int, int, list[str], Dict[str, Any]]],
    ) -> List[Tuple[float, Path, str, int, int, list[str], Dict[str, Any]]]:
        """Remove candidates whose text overlaps by more than 60% with a higher-scored candidate from the same page."""
        if len(candidates) <= 1:
            return candidates

        # Group by page
        by_page: Dict[str, list] = {}
        for item in candidates:
            page_key = str(item[1])
            by_page.setdefault(page_key, []).append(item)

        result: list = []
        for page_key, group in by_page.items():
            # Already sorted by score descending
            keep = []
            for item in group:
                _, _, text, start, end, _, _ = item
                span = (start, end)
                is_dup = False
                for _, _, kept_text, kept_start, kept_end, _, _ in keep:
                    # Check overlap ratio
                    overlap_start = max(start, kept_start)
                    overlap_end = min(end, kept_end)
                    if overlap_start < overlap_end:
                        overlap_len = overlap_end - overlap_start
                        this_len = end - start
                        kept_len = kept_end - kept_start
                        min_len = min(this_len, kept_len)
                        if min_len > 0 and overlap_len / min_len > 0.6:
                            is_dup = True
                            break
                if not is_dup:
                    keep.append(item)
            result.extend(keep)

        return result

    # ── fallback ────────────────────────────────────────────────────

    def _fallback_first_page(
        self,
        question: Question,
        doc_id: str,
        doc_dir: Path,
        query_terms: Sequence[str],
        numeric_terms: Sequence[str],
    ) -> Sequence[EvidenceCandidate]:
        for page in sorted(doc_dir.glob("page_*.md")):
            text = page.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return [
                    EvidenceCandidate(
                        domain=question.domain,
                        doc_id=doc_id,
                        source=str(page),
                        text=text[:1800],
                        score=0.0,
                        retriever=self.name,
                        metadata={
                            "fallback": "first_page",
                            "is_fallback": True,
                            "query_terms": list(query_terms),
                            "matched_terms": [],
                            "score_breakdown": {"score": 0.0, "fallback_reason": "no_terms_matched"},
                        },
                    )
                ]
        return []

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _windows(text: str, size: int, overlap: int) -> Iterable[Tuple[int, int, str]]:
        if len(text) <= size:
            yield 0, len(text), text
            return
        step = max(1, size - overlap)
        for start in range(0, len(text), step):
            end = min(len(text), start + size)
            yield start, end, text[start:end]
            if end == len(text):
                break

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        chinese_terms = re.findall(r"[一-鿿]{2,}", text)
        latin_terms = re.findall(r"[A-Za-z0-9_.%-]{2,}", text)
        terms: list[str] = []
        for term in chinese_terms:
            terms.append(term)
            if len(term) > 4:
                terms.extend(term[i : i + 4] for i in range(0, len(term) - 3, 2))
        terms.extend(latin_terms)
        return terms

    @staticmethod
    def _dedup_terms(terms: Sequence[str]) -> List[str]:
        seen: Set[str] = set()
        result: list[str] = []
        for term in terms:
            t = term.strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    @staticmethod
    def _nearest_title(text: str, pos: int) -> Optional[str]:
        prefix = text[:pos]
        lines = prefix.splitlines()[-30:]
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith("#") or any(term in stripped for term in LexicalHybridRetriever.title_terms):
                return stripped[:120]
        return None
