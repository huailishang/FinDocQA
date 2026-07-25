"""Enhanced evidence assembler — dedup, source ordering, section paths, table context.

This is a default-off assembler for Lane 2 evaluation. It extends
GroupedEvidenceAssembler with:

1. Deduplication: near-identical candidate text is collapsed.
2. Source-order sorting: within each doc_id, candidates are ordered by page
   number (parsed from page_XXXX.md) rather than by retrieval score.
3. Section-path annotation: each candidate is enriched with parent section
   context from parsed structure blocks.
4. Table/formula grouping: when a candidate overlaps a GFM table, its context
   is expanded to keep the full table visible.

All changes are scoped to the assembler: no corpus, retrieval, model, prompt,
or answer-parser changes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from contracts import (
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
)
from evidence.assembler import GroupedEvidenceAssembler


# Regex to extract page number from page_XXXX.md filenames.
_PAGE_RE = re.compile(r"page_(\d+)", re.IGNORECASE)
# Regex for GFM table delimiter lines.
_TABLE_DELIM_RE = re.compile(r"^\s*\|[\s:|]+\|\s*$")
# Regex for any GFM table row (line containing | with at least one cell).
_TABLE_ROW_RE = re.compile(r"^\s*\|.+?\|.*$")


class EnhancedEvidenceAssembler(GroupedEvidenceAssembler):
    """Default-off evidence assembler with dedup, source ordering, and section context.

    Features controlled by constructor flags (all default-on within the
    assembler; the off switch is at the PipelineFactory / config level):

    - enable_dedup: collapse near-identical fragments (default True).
    - enable_source_order: sort by page number within doc_id (default True).
    - enable_section_context: enrich candidates with parent section paths from
      parsed page structure (default True).
    - enable_table_grouping: expand candidate context to keep full GFM tables
      visible (default True).

    Each feature is independently toggleable so an A/B ablation can isolate
    which mechanism drives any answer change.
    """

    def __init__(
        self,
        token_budgets: Optional[Sequence] = None,
        enable_dedup: bool = True,
        enable_source_order: bool = True,
        enable_section_context: bool = True,
        enable_table_grouping: bool = True,
        *,
        enable_prompt_evidence_compaction: bool = False,
        prompt_evidence_policy: Optional[dict[str, Any]] = None,
        prompt_budget_model: str = "qwen3.7-max",
        structured_table_root: Path | str | None = None,
        enable_structured_table_verification: bool = False,
        enable_structured_table_prompt_injection: bool = False,
        structured_table_max_rows_per_doc: int = 12,
        contract_exact_field_full_text_root: Path | str | None = None,
        contract_exact_field_retrieval_root: Path | str | None = None,
        enable_contract_exact_field_verification: bool = False,
        contract_exact_field_max_windows_per_doc: int = 3,
        insurance_clause_full_text_root: Path | str | None = None,
        insurance_clause_product_catalog_path: Path | str | None = None,
        insurance_clause_registry_path: Path | str | None = None,
        allow_curated_insurance_fixture_for_offline_evaluation: bool = False,
        enable_insurance_clause_verification: bool = False,
        insurance_calculation_full_text_root: Path | str | None = None,
        insurance_calculation_product_catalog_path: Path | str | None = None,
        enable_insurance_calculation_verification: bool = False,
    ) -> None:
        super().__init__(
            token_budgets,
            enable_prompt_evidence_compaction=enable_prompt_evidence_compaction,
            prompt_evidence_policy=prompt_evidence_policy,
            prompt_budget_model=prompt_budget_model,
            structured_table_root=structured_table_root,
            enable_structured_table_verification=enable_structured_table_verification,
            enable_structured_table_prompt_injection=enable_structured_table_prompt_injection,
            structured_table_max_rows_per_doc=structured_table_max_rows_per_doc,
            contract_exact_field_full_text_root=contract_exact_field_full_text_root,
            contract_exact_field_retrieval_root=contract_exact_field_retrieval_root,
            enable_contract_exact_field_verification=enable_contract_exact_field_verification,
            contract_exact_field_max_windows_per_doc=contract_exact_field_max_windows_per_doc,
            insurance_clause_full_text_root=insurance_clause_full_text_root,
            insurance_clause_product_catalog_path=insurance_clause_product_catalog_path,
            insurance_clause_registry_path=insurance_clause_registry_path,
            allow_curated_insurance_fixture_for_offline_evaluation=allow_curated_insurance_fixture_for_offline_evaluation,
            enable_insurance_clause_verification=enable_insurance_clause_verification,
            insurance_calculation_full_text_root=insurance_calculation_full_text_root,
            insurance_calculation_product_catalog_path=insurance_calculation_product_catalog_path,
            enable_insurance_calculation_verification=enable_insurance_calculation_verification,
        )
        self._enable_dedup = enable_dedup
        self._enable_source_order = enable_source_order
        self._enable_section_context = enable_section_context
        self._enable_table_grouping = enable_table_grouping

    def assemble(
        self,
        question: Question,
        classification: ClassificationResult,
        candidates: Sequence[EvidenceCandidate],
    ) -> EvidenceBundle:
        passed = list(candidates)

        # Step 1: deduplicate
        if self._enable_dedup:
            passed = self._deduplicate(passed)

        # Step 2: sort by source order within each doc
        if self._enable_source_order:
            passed = self._order_by_source(passed)

        # Step 3: enrich with section context from parsed page files
        if self._enable_section_context:
            passed = [self._enrich_section(c) for c in passed]

        # Step 4: expand table neighborhoods
        if self._enable_table_grouping:
            passed = self._expand_table_context(passed)

        bundle = super().assemble(question, classification, passed)

        metadata = dict(bundle.metadata)
        metadata.update({
            "enhanced_assembler": True,
            "enhanced_dedup": self._enable_dedup,
            "enhanced_source_order": self._enable_source_order,
            "enhanced_section_context": self._enable_section_context,
            "enhanced_table_grouping": self._enable_table_grouping,
            "enhanced_pre_dedup_count": len(candidates),
            "enhanced_post_dedup_count": len(passed),
        })
        return replace(bundle, metadata=metadata)

    # ── Step 1: Deduplication ────────────────────────────────────────

    @staticmethod
    def _deduplicate(
        candidates: Sequence[EvidenceCandidate],
    ) -> list[EvidenceCandidate]:
        """Collapse near-identical fragments by text-overlap scoring.

        Uses the same trigram-overlap method as StructureAwareEvidenceAssembler.
        When two candidates from the same doc overlap by more than 60 %, the
        higher-scoring one is kept (or the longer text if scores are equal).
        """
        if len(candidates) <= 1:
            return list(candidates)

        by_doc: dict[str, list[EvidenceCandidate]] = defaultdict(list)
        for c in candidates:
            by_doc[c.doc_id].append(c)

        result: list[EvidenceCandidate] = []
        for doc_id, group in by_doc.items():
            # Sort within doc by score descending, then by text length descending
            group.sort(key=lambda c: (c.score, len(c.text or "")), reverse=True)
            kept: list[EvidenceCandidate] = []
            for candidate in group:
                is_dup = False
                needle = (candidate.text or "").strip()
                if not needle:
                    continue
                for existing in kept:
                    haystack = (existing.text or "").strip()
                    overlap = EnhancedEvidenceAssembler._overlap_ratio(
                        needle, haystack
                    )
                    if overlap >= 0.6:
                        is_dup = True
                        break
                if not is_dup:
                    kept.append(candidate)
            result.extend(kept)
        return result

    @staticmethod
    def _overlap_ratio(a: str, b: str) -> float:
        """Compute trigram-overlap ratio between two strings.

        Returns 0-1 where 1 = identical trigram set.
        """
        if not a or not b:
            return 0.0
        # Check containment first (fast path)
        if a in b or b in a:
            return 1.0
        trigrams_a = EnhancedEvidenceAssembler._trigrams(a)
        trigrams_b = EnhancedEvidenceAssembler._trigrams(b)
        if not trigrams_a or not trigrams_b:
            return 0.0
        intersection = trigrams_a & trigrams_b
        union = trigrams_a | trigrams_b
        return len(intersection) / len(union)

    @staticmethod
    def _trigrams(text: str) -> set[str]:
        """Character trigrams from compacted text (whitespace removed)."""
        compact = "".join(text.split())
        if len(compact) < 3:
            return {compact} if compact else set()
        return {compact[i : i + 3] for i in range(len(compact) - 2)}

    # ── Step 2: Source-order sorting ─────────────────────────────────

    @staticmethod
    def _page_number(source: str) -> int:
        """Extract page number from a page_XXXX.md path.

        Returns a large sentinel (999999) when the pattern does not match so
        unparseable candidates sort after all real pages.
        """
        m = _PAGE_RE.search(source)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 999999
        return 999999

    @staticmethod
    def _order_by_source(
        candidates: Sequence[EvidenceCandidate],
    ) -> list[EvidenceCandidate]:
        """Within each doc_id, order candidates by page number ascending.

        Candidates from the same page keep their relative order (stable sort).
        """
        by_doc: dict[str, list[EvidenceCandidate]] = defaultdict(list)
        for c in candidates:
            by_doc[c.doc_id].append(c)

        result: list[EvidenceCandidate] = []
        for doc_id, group in by_doc.items():
            group.sort(
                key=lambda c: EnhancedEvidenceAssembler._page_number(str(c.source))
            )
            result.extend(group)
        return result

    # ── Step 3: Section-path enrichment ──────────────────────────────

    @staticmethod
    def _enrich_section(candidate: EvidenceCandidate) -> EvidenceCandidate:
        """Attempt to attach section-path context from the page file.

        Reads the page file indicated by ``candidate.source`` and scans for
        heading lines (ATX headings ``# ...``). If a heading is found before
        the approximate position of the candidate text, it is prepended as
        section context in ``before_text``.

        This is a lightweight heuristic that does not require the full
        structure-parser pipeline. It trades accuracy for zero dependencies.
        """
        page_path = Path(candidate.source)
        if not page_path.is_file() or page_path.suffix.lower() != ".md":
            return candidate

        try:
            page_text = page_path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeError):
            return candidate

        needle = (candidate.text or "").strip()
        if not needle:
            return candidate

        # Find the approximate position of the candidate text in the page
        pos = page_text.find(needle)
        if pos < 0:
            return candidate

        # Scan preceding lines for ATX headings
        prefix_lines = page_text[:pos].splitlines()
        headings: list[str] = []
        for line in reversed(prefix_lines[-50:]):  # look back up to 50 lines
            stripped = line.strip()
            if stripped.startswith("#"):
                # Strip leading # markers
                heading_text = stripped.lstrip("#").strip()
                if heading_text and heading_text not in headings:
                    headings.insert(0, heading_text)
                    if len(headings) >= 3:  # max depth
                        break

        if headings:
            section_prefix = " / ".join(headings)
            existing_before = (candidate.before_text or "").strip()
            new_before = f"[SECTION] {section_prefix}"
            if existing_before:
                new_before += "\n" + existing_before
            return replace(
                candidate,
                before_text=new_before,
                section_title=headings[-1] if headings else candidate.section_title,
                metadata={
                    **dict(candidate.metadata),
                    "enhanced_section_path": headings,
                    "enhanced_section_prefix": section_prefix,
                },
            )
        return candidate

    # ── Step 4: Table context expansion ──────────────────────────────

    @staticmethod
    def _is_table_line(line: str) -> bool:
        """Check if a line is part of a GFM table."""
        stripped = line.strip()
        if not stripped.startswith("|"):
            return False
        return bool(_TABLE_ROW_RE.match(stripped))

    @staticmethod
    def _expand_table_context(
        candidates: Sequence[EvidenceCandidate],
    ) -> list[EvidenceCandidate]:
        """Expand candidate context to include full GFM table rows.

        When a candidate text or its before/after context contains table-like
        content (``|...|`` rows), read the source page and expand the
        candidate's text and flank to cover the entire table block (from the
        table header to the last data row). This prevents tables from being
        split across candidates.
        """
        result: list[EvidenceCandidate] = []
        for candidate in candidates:
            combined = (
                f"{candidate.before_text}\n{candidate.text}\n{candidate.after_text}"
            )
            if not EnhancedEvidenceAssembler._has_table_content(combined):
                result.append(candidate)
                continue
            expanded = EnhancedEvidenceAssembler._expand_one_table(candidate)
            result.append(expanded if expanded is not None else candidate)
        return result

    @staticmethod
    def _has_table_content(text: str) -> bool:
        """Check if text contains GFM table indicator lines."""
        for line in text.splitlines():
            if _TABLE_DELIM_RE.match(line):
                return True
        return False

    @staticmethod
    def _expand_one_table(
        candidate: EvidenceCandidate,
    ) -> Optional[EvidenceCandidate]:
        """Expand a single candidate to cover the full table in its page.

        Returns a new EvidenceCandidate with expanded text/before/after, or
        None if expansion is not possible.
        """
        page_path = Path(candidate.source)
        if not page_path.is_file():
            return None
        try:
            page_text = page_path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeError):
            return None

        # Find the candidate text anchor in the page
        anchor = (candidate.text or "").strip()
        if not anchor:
            return None
        pos = page_text.find(anchor)
        if pos < 0:
            return None

        lines = page_text.splitlines(keepends=True)
        # Find line index of the anchor
        char_count = 0
        anchor_line_idx = -1
        for i, line in enumerate(lines):
            line_end = char_count + len(line)
            if char_count <= pos < line_end:
                anchor_line_idx = i
                break
            char_count = line_end
        if anchor_line_idx < 0:
            return None

        # Expand backward to find the table header
        table_start = anchor_line_idx
        for i in range(anchor_line_idx, -1, -1):
            if EnhancedEvidenceAssembler._is_table_line(lines[i]):
                table_start = i
            else:
                # Stop at a blank line or a non-table line before the table
                if i < anchor_line_idx and not lines[i].strip():
                    table_start = i + 1
                    break
                if i < anchor_line_idx:
                    break

        # Expand forward to find table end
        table_end = anchor_line_idx
        for i in range(anchor_line_idx, len(lines)):
            if EnhancedEvidenceAssembler._is_table_line(lines[i]) or _TABLE_DELIM_RE.match(lines[i].strip()):
                table_end = i
            else:
                # Stop at a blank line or non-table line after the table
                if i > anchor_line_idx and not lines[i].strip():
                    break
                if i > anchor_line_idx:
                    break

        # Extract the table block
        table_text = "".join(lines[table_start : table_end + 1]).strip()
        if not table_text or table_text == anchor:
            return None

        # Build context before and after the table
        before_text = "".join(lines[max(0, table_start - 10) : table_start]).strip()
        after_text = "".join(
            lines[table_end + 1 : min(len(lines), table_end + 11)]
        ).strip()

        return replace(
            candidate,
            text=table_text,
            before_text=before_text,
            after_text=after_text,
            metadata={
                **dict(candidate.metadata),
                "enhanced_table_expanded": True,
                "enhanced_table_rows": table_end - table_start + 1,
            },
        )