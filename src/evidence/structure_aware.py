"""Optional structure-aware evidence assembly for Workstream B."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Sequence
import re

from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question
from evidence.assembler import GroupedEvidenceAssembler
from structure.blocks import ContentType
from structure.chunks import build_chunks
from structure.parser import parse_page_file


class StructureAwareEvidenceAssembler(GroupedEvidenceAssembler):
    """Enrich retrieved candidates without changing retrieval order or scores."""

    def assemble(
        self,
        question: Question,
        classification: ClassificationResult,
        candidates: Sequence[EvidenceCandidate],
    ) -> EvidenceBundle:
        enriched_list = [self._enrich_candidate(candidate) for candidate in candidates]
        enriched_list.extend(self._supplement_numeric_formula_pages(question, enriched_list))
        enriched = tuple(enriched_list)
        bundle = super().assemble(question, classification, enriched)
        metadata = dict(bundle.metadata)
        metadata.update({
            "structure_aware": True,
            "structure_enriched_candidates": sum(
                1 for candidate in enriched if candidate.metadata.get("structure_enriched")
            ),
            "structure_formula_supplement_sources": [
                candidate.source for candidate in enriched
                if candidate.metadata.get("structure_formula_supplement")
            ],
            "structure_formula_anchors": [
                candidate.metadata.get("structure_formula_anchor") for candidate in enriched
                if candidate.metadata.get("structure_formula_supplement")
            ],
        })
        return replace(bundle, metadata=metadata)

    def _enrich_candidate(self, candidate: EvidenceCandidate) -> EvidenceCandidate:
        page_path = Path(candidate.source)
        if not page_path.is_file() or page_path.suffix.lower() != ".md":
            return candidate
        try:
            parsed = parse_page_file(page_path, domain=candidate.domain, doc_id=candidate.doc_id)
            chunks = build_chunks(parsed)
        except (OSError, UnicodeError, ValueError):
            return candidate
        if not chunks:
            return candidate

        needle = candidate.text.strip()
        best = max(chunks, key=lambda chunk: self._overlap_score(needle, chunk.content))
        score = self._overlap_score(needle, best.content)
        if score <= 0:
            return candidate

        metadata = dict(candidate.metadata)
        metadata.update({
            "structure_enriched": True,
            "structure_chunk_id": best.chunk_id,
            "structure_block_ids": list(best.block_ids),
            "structure_content_type": best.content_type.value,
            "structure_section_path": list(best.section_path),
            "structure_parent_context": best.parent_context,
            "structure_overlap_score": score,
        })
        text = best.content if best.content_type is ContentType.TABLE else candidate.text
        before = candidate.before_text
        if best.parent_context:
            before = f"[SECTION] {best.parent_context}\n{before}".strip()
        return replace(
            candidate,
            text=text,
            before_text=before,
            section_title=best.section_path[-1] if best.section_path else candidate.section_title,
            metadata=metadata,
        )

    def _supplement_numeric_formula_pages(
        self, question: Question, candidates: Sequence[EvidenceCandidate]
    ) -> list[EvidenceCandidate]:
        """Add a bounded page when an exact percentage anchor is missing.

        This runs only in B1 after normal retrieval. It does not rescore or reorder
        existing candidates and adds at most one page per document.
        """
        query = " ".join([question.text, *question.options.values()])
        anchors = sorted(set(re.findall(r"\d+(?:\.\d+)?%", query)))
        if not anchors:
            return []
        by_doc: dict[str, list[EvidenceCandidate]] = {}
        for candidate in candidates:
            by_doc.setdefault(str(candidate.doc_id), []).append(candidate)
        additions: list[EvidenceCandidate] = []
        for doc_id in map(str, question.doc_ids):
            doc_candidates = by_doc.get(doc_id, [])
            if not doc_candidates:
                continue
            existing_text = "\n".join(
                f"{c.before_text}\n{c.text}\n{c.after_text}" for c in doc_candidates
            )
            missing = [anchor for anchor in anchors if anchor not in existing_text]
            if not missing:
                continue
            doc_root = Path(doc_candidates[0].source).parent
            for page_path in sorted(doc_root.glob("page_*.md")):
                try:
                    text = page_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                anchor = next((item for item in missing if item in text), None)
                if anchor is None:
                    continue
                pos = text.index(anchor)
                start = max(0, pos - 700)
                end = min(len(text), pos + 1100)
                additions.append(EvidenceCandidate(
                    domain=question.domain, doc_id=doc_id, source=str(page_path),
                    text=text[start:end], score=0.0, retriever="structure_formula_anchor",
                    metadata={
                        "structure_enriched": True,
                        "structure_formula_anchor": anchor,
                        "structure_formula_supplement": True,
                    },
                ))
                break
        return additions

    @staticmethod
    def _overlap_score(needle: str, haystack: str) -> int:
        if not needle or not haystack:
            return 0
        if needle in haystack:
            return len(needle) + 1000
        if haystack in needle:
            return len(haystack) + 500
        def grams(text: str) -> set[str]:
            compact = "".join(text.split())
            if len(compact) < 3:
                return {compact} if compact else set()
            return {compact[i:i + 3] for i in range(len(compact) - 2)}
        return len(grams(needle) & grams(haystack))
