"""Conservative canonical-document context recovery for material formulas.

C3-B deliberately recovers only structurally linked evidence inside one
``CanonicalDocument``.  It never performs semantic variable binding, fuzzy table
matching, cross-document stitching, or model-assisted formula repair.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Iterable, Mapping, Sequence

from calculation.contracts import (
    FormulaEvidence,
    FormulaGateResult,
    FormulaGateStatus,
    FormulaSourceRef,
)
from calculation.compiler import SafeFormulaCompiler
from calculation.material import FormulaEvidenceGate, LocalContextVariableBinder
from document.contracts import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalFormula,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from document.store import DocumentStore

_CROSS_PAGE_NEXT_MARKERS = ("见下页", "下一页", "下页续", "续表")
_CROSS_PAGE_PREV_MARKERS = ("见上页", "上一页", "上页续", "续上页")
_CONTEXT_BLOCK_TYPES = {
    CanonicalBlockType.HEADING,
    CanonicalBlockType.TEXT,
    CanonicalBlockType.LIST,
    CanonicalBlockType.CODE,
}


@dataclass(frozen=True)
class FormulaRecoveryStep:
    """One auditable recovery action and the exact canonical sources it used."""

    action: str
    detail: str = ""
    source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "detail": self.detail,
            "source_refs": [item.to_dict() for item in self.source_refs],
        }


@dataclass(frozen=True)
class FormulaRecoveryResult:
    """C3-B recovery result; PASS means the recovered evidence also passed C3-A Gate."""

    status: FormulaGateStatus
    recovered_evidence: FormulaEvidence
    recovered_source_refs: Sequence[FormulaSourceRef] = field(default_factory=tuple)
    recovery_steps: Sequence[FormulaRecoveryStep] = field(default_factory=tuple)
    reasons: Sequence[str] = field(default_factory=tuple)
    gate_result: FormulaGateResult | None = None

    @property
    def ready_for_execution(self) -> bool:
        return bool(
            self.status is FormulaGateStatus.PASS
            and self.gate_result is not None
            and self.gate_result.status is FormulaGateStatus.PASS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "recovered_evidence": self.recovered_evidence.to_dict(),
            "recovered_source_refs": [item.to_dict() for item in self.recovered_source_refs],
            "recovery_steps": [item.to_dict() for item in self.recovery_steps],
            "reasons": list(self.reasons),
            "gate_result": self.gate_result.to_dict() if self.gate_result else None,
            "ready_for_execution": self.ready_for_execution,
        }


def _unique_strings(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _dedupe_refs(values: Iterable[FormulaSourceRef]) -> tuple[FormulaSourceRef, ...]:
    result: list[FormulaSourceRef] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in values:
        key = (ref.doc_id, ref.page_number, ref.source, ref.block_id)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return tuple(result)


def _source_ref(
    document: CanonicalDocument,
    *,
    page_number: int | None,
    lineage: SourceLineage | None,
    block_id: str,
    excerpt: str,
) -> FormulaSourceRef:
    source = str(lineage.source_path if lineage is not None else document.source_uri or "")
    return FormulaSourceRef(
        doc_id=document.document_id,
        page_number=page_number,
        source=source,
        block_id=block_id,
        excerpt=str(excerpt or "")[:500],
    )


def _table_text(table: CanonicalTable) -> str:
    parts: list[str] = []
    if table.caption.strip():
        parts.append(table.caption.strip())
    if table.markdown.strip():
        parts.append(table.markdown.strip())
    elif table.headers or table.rows:
        if table.headers:
            parts.append(" | ".join(table.headers))
        parts.extend(" | ".join(row) for row in table.rows)
    elif table.html.strip():
        parts.append(table.html.strip())
    if table.footnote.strip():
        parts.append(table.footnote.strip())
    return "\n".join(part for part in parts if part)


def _metadata_strings(metadata: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    values: list[Any] = []
    for key in keys:
        raw = metadata.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.extend(raw)
        elif raw is not None:
            values.append(raw)
    return _unique_strings(values)


class _CanonicalFormulaLookupStatus(str, Enum):
    MISSING = "MISSING"
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class _CanonicalFormulaLookupResult:
    status: _CanonicalFormulaLookupStatus
    formula_id: str = ""
    formula: CanonicalFormula | None = None
    match_count: int = 0


class FormulaContextRecovery:
    """Recover bounded formula context through canonical structural links only."""

    def __init__(self, store: DocumentStore, *, same_page_window: int = 2) -> None:
        if same_page_window < 0 or same_page_window > 4:
            raise ValueError("same_page_window must be between 0 and 4")
        self.store = store
        self.same_page_window = int(same_page_window)

    def recover(self, evidence: FormulaEvidence, *, domain: str | None = None) -> FormulaRecoveryResult:
        review: list[str] = []
        fatal: list[str] = []
        steps: list[FormulaRecoveryStep] = []
        context_parts: list[str] = [str(evidence.context_text or "").strip()]
        refs: list[FormulaSourceRef] = list(evidence.source_refs)

        doc_ids = _unique_strings(ref.doc_id for ref in evidence.source_refs)
        if not doc_ids:
            fatal.append("recovery_document_id_missing")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)
        if len(doc_ids) != 1:
            fatal.append("cross_document_recovery_forbidden")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)
        doc_id = doc_ids[0]

        resolved_domain, document = self._resolve_document(
            domain=str(domain or evidence.metadata.get("domain") or ""),
            doc_id=doc_id,
        )
        if document is None:
            fatal.append("canonical_document_not_found")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)

        if any(ref.doc_id and ref.doc_id != document.document_id for ref in evidence.source_refs):
            fatal.append("cross_document_recovery_forbidden")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)

        page_numbers = tuple(
            sorted({int(ref.page_number) for ref in evidence.source_refs if ref.page_number is not None})
        )
        if not page_numbers:
            raw_page = evidence.metadata.get("page_number")
            if isinstance(raw_page, int):
                page_numbers = (raw_page,)
        if len(page_numbers) != 1:
            review.append("formula_page_not_unique")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)
        page = document.page(page_numbers[0])
        if page is None:
            fatal.append("formula_page_not_found")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)

        anchor = self._locate_anchor(evidence, page)
        if isinstance(anchor, str):
            review.append(anchor)
            return self._finish(evidence, context_parts, refs, steps, review, fatal)

        anchor_ref = _source_ref(
            document,
            page_number=anchor.page_number,
            lineage=anchor.lineage,
            block_id=anchor.block_id,
            excerpt=anchor.text,
        )
        if anchor.lineage is None or not anchor_ref.source:
            review.append(f"recovery_lineage_missing:block:{anchor.block_id}")
        refs.append(anchor_ref)

        local_blocks, local_reason = self._same_page_blocks(page, anchor)
        if local_reason:
            review.append(local_reason)
        else:
            try:
                referenced = SafeFormulaCompiler.referenced_symbols(
                    evidence.normalized_expression
                )
            except (TypeError, ValueError):
                referenced = ()
            anchor_order = anchor.reading_order
            preceding_text = "\n".join(
                block.text
                for block in local_blocks
                if anchor_order is not None
                and block.reading_order is not None
                and block.reading_order < anchor_order
            )
            preceding_has_complete_bindings = bool(referenced) and all(
                re.search(rf"\b{re.escape(name)}\s*=", preceding_text)
                for name in referenced
            )
            local_refs: list[FormulaSourceRef] = []
            for block in local_blocks:
                # Tables and neighboring formulas require explicit structural linkage;
                # never pull them merely because they are physically nearby.
                if block.block_id != anchor.block_id and block.block_type not in _CONTEXT_BLOCK_TYPES:
                    continue
                # Once all referenced variables are bound before the formula,
                # later unlinked assignment blocks are physical neighbors, not
                # competing bindings.
                if (
                    preceding_has_complete_bindings
                    and anchor_order is not None
                    and block.reading_order is not None
                    and block.reading_order > anchor_order
                    and any(
                        re.search(rf"\b{re.escape(name)}\s*=", block.text)
                        for name in referenced
                    )
                ):
                    continue
                if block.text.strip():
                    context_parts.append(block.text.strip())
                ref = _source_ref(
                    document,
                    page_number=block.page_number,
                    lineage=block.lineage,
                    block_id=block.block_id,
                    excerpt=block.text,
                )
                if block.lineage is None or not ref.source:
                    review.append(f"recovery_lineage_missing:block:{block.block_id}")
                refs.append(ref)
                local_refs.append(ref)
            steps.append(
                FormulaRecoveryStep(
                    action="same_page_window",
                    detail=f"page={page.page_number};window={self.same_page_window}",
                    source_refs=_dedupe_refs(local_refs),
                )
            )

        formula_lookup = self._lookup_canonical_formula(evidence, page, anchor)
        if formula_lookup.status is _CanonicalFormulaLookupStatus.AMBIGUOUS:
            review.append(f"canonical_formula_not_unique:{formula_lookup.formula_id}")
            return self._finish(evidence, context_parts, refs, steps, review, fatal)
        canonical_formula = formula_lookup.formula
        formula_metadata = canonical_formula.metadata if canonical_formula is not None else {}

        explicit_footnote_refs = _unique_strings(
            (
                *_metadata_strings(evidence.metadata, "linked_footnote_ref", "linked_footnote_refs"),
                *_metadata_strings(anchor.metadata, "linked_footnote_ref", "linked_footnote_refs"),
                *_metadata_strings(formula_metadata, "linked_footnote_ref", "linked_footnote_refs"),
            )
        )
        if explicit_footnote_refs:
            review.append("unsupported_explicit_footnote_linkage")

        table_ids = self._explicit_table_ids(evidence, page, anchor, canonical_formula)
        for table_id in table_ids:
            matches = [
                table
                for doc_page in document.pages
                for table in doc_page.tables
                if table.table_id == table_id
            ]
            if not matches:
                review.append(f"linked_table_not_found:{table_id}")
                continue
            if len(matches) > 1:
                review.append(f"linked_table_ambiguous:{table_id}")
                continue
            table = matches[0]
            text = _table_text(table)
            if text:
                context_parts.append(text)
            ref = _source_ref(
                document,
                page_number=table.page_number,
                lineage=table.lineage,
                block_id=table.table_id,
                excerpt=text,
            )
            if table.lineage is None or not ref.source:
                review.append(f"recovery_lineage_missing:table:{table.table_id}")
            refs.append(ref)
            steps.append(
                FormulaRecoveryStep(
                    action="linked_table",
                    detail=f"table_id={table.table_id}",
                    source_refs=(ref,),
                )
            )

        # Cross-page authorization is intentionally narrower than recovered
        # context. Generic same-page neighbors may contribute variables, but their
        # "见下页/见上页" text must never authorize the formula to leave its page.
        anchor_text = str(anchor.text or "")
        wants_next = any(marker in anchor_text for marker in _CROSS_PAGE_NEXT_MARKERS)
        wants_prev = any(marker in anchor_text for marker in _CROSS_PAGE_PREV_MARKERS)
        explicit_block_ids, explicit_formula_ids = self._explicit_continuation_targets(
            anchor.metadata,
            formula_metadata,
        )
        has_explicit_continuation = bool(explicit_block_ids or explicit_formula_ids)
        continuation_docs = _unique_strings(
            (
                *_metadata_strings(anchor.metadata, "continuation_doc_id", "continuation_document_id"),
                *_metadata_strings(formula_metadata, "continuation_doc_id", "continuation_document_id"),
            )
        )
        if continuation_docs and continuation_docs != (document.document_id,):
            fatal.append("cross_document_recovery_forbidden")
        elif wants_next and wants_prev:
            review.append("adjacent_page_direction_ambiguous")
        elif wants_next or wants_prev:
            offset = 1 if wants_next else -1
            adjacent_page = (
                document.page(int(page.page_number or 0) + offset)
                if page.page_number is not None
                else None
            )
            if adjacent_page is None:
                review.append("adjacent_page_not_found")
            else:
                candidates = self._continuation_candidates(
                    anchor,
                    adjacent_page,
                    formula_metadata=formula_metadata,
                    allow_unlinked_fallback=True,
                )
                self._append_unique_continuation(
                    document=document,
                    from_page=page,
                    adjacent_page=adjacent_page,
                    candidates=candidates,
                    authorization_source="anchor_text",
                    context_parts=context_parts,
                    refs=refs,
                    steps=steps,
                    review=review,
                )
        elif has_explicit_continuation and page.page_number is not None:
            # Explicit target ids are anchor/formula-owned authorization even when
            # prose contains no direction marker. Search only the two adjacent
            # pages and require one unique target across both.
            located: list[tuple[CanonicalPage, CanonicalBlock]] = []
            for offset in (-1, 1):
                adjacent_page = document.page(int(page.page_number) + offset)
                if adjacent_page is None:
                    continue
                candidates = self._continuation_candidates(
                    anchor,
                    adjacent_page,
                    formula_metadata=formula_metadata,
                    allow_unlinked_fallback=False,
                )
                located.extend((adjacent_page, candidate) for candidate in candidates)
            if len(located) != 1:
                review.append(
                    "adjacent_page_continuation_not_unique"
                    if located
                    else "adjacent_page_continuation_not_found"
                )
            else:
                adjacent_page, continuation = located[0]
                self._append_unique_continuation(
                    document=document,
                    from_page=page,
                    adjacent_page=adjacent_page,
                    candidates=(continuation,),
                    authorization_source="explicit_continuation_metadata",
                    context_parts=context_parts,
                    refs=refs,
                    steps=steps,
                    review=review,
                )

        recovered_refs = _dedupe_refs(refs)
        recovered = FormulaEvidence(
            raw_formula=evidence.raw_formula,
            normalized_expression=evidence.normalized_expression,
            context_text=self._merge_context(context_parts),
            variable_definitions=dict(evidence.variable_definitions),
            conditions=tuple(evidence.conditions),
            source_refs=recovered_refs,
            linked_table_refs=_unique_strings((*evidence.linked_table_refs, *table_ids)),
            metadata={
                **dict(evidence.metadata),
                "recovery_domain": resolved_domain,
                "recovery_document_id": document.document_id,
            },
        )
        return self._finish(recovered, [recovered.context_text], recovered_refs, steps, review, fatal, already_recovered=True)

    def _resolve_document(self, *, domain: str, doc_id: str) -> tuple[str, CanonicalDocument | None]:
        if domain:
            return domain, self.store.get(domain, doc_id)
        matches = [doc for doc in self.store.iter_documents() if doc.document_id == doc_id]
        if len(matches) == 1:
            return matches[0].domain, matches[0]
        return "", None

    @staticmethod
    def _locate_anchor(evidence: FormulaEvidence, page: CanonicalPage) -> CanonicalBlock | str:
        block_ids = _unique_strings(ref.block_id for ref in evidence.source_refs)
        if block_ids:
            matches = [block for block in page.blocks if block.block_id in block_ids]
            if len(matches) == 1:
                return matches[0]
            return "formula_block_not_unique"

        formula_id = str(evidence.metadata.get("formula_id") or "").strip()
        if formula_id:
            matches = [block for block in page.blocks if block.formula_id == formula_id]
            if len(matches) == 1:
                return matches[0]
        return "formula_block_not_unique"

    def _same_page_blocks(
        self,
        page: CanonicalPage,
        anchor: CanonicalBlock,
    ) -> tuple[tuple[CanonicalBlock, ...], str]:
        if anchor.reading_order is None:
            return (), "reading_order_missing"
        orders = [block.reading_order for block in page.blocks]
        if any(order is None for order in orders):
            return (), "reading_order_incomplete"
        int_orders = [int(order) for order in orders if order is not None]
        if len(set(int_orders)) != len(int_orders):
            return (), "reading_order_not_unique"
        ordered = sorted(page.blocks, key=lambda block: int(block.reading_order or 0))
        position = next((index for index, block in enumerate(ordered) if block.block_id == anchor.block_id), None)
        if position is None:
            return (), "formula_block_not_unique"
        start = max(0, position - self.same_page_window)
        end = min(len(ordered), position + self.same_page_window + 1)
        selected = tuple(ordered[start:end])
        selected_orders = [int(block.reading_order or 0) for block in selected]
        if any(right - left != 1 for left, right in zip(selected_orders, selected_orders[1:])):
            return (), "reading_order_not_contiguous"
        return selected, ""

    @staticmethod
    def _append_unique_continuation(
        *,
        document: CanonicalDocument,
        from_page: CanonicalPage,
        adjacent_page: CanonicalPage,
        candidates: Sequence[CanonicalBlock],
        authorization_source: str,
        context_parts: list[str],
        refs: list[FormulaSourceRef],
        steps: list[FormulaRecoveryStep],
        review: list[str],
    ) -> None:
        if len(candidates) != 1:
            review.append(
                "adjacent_page_continuation_not_unique"
                if candidates
                else "adjacent_page_continuation_not_found"
            )
            return
        continuation = candidates[0]
        if continuation.text.strip():
            context_parts.append(continuation.text.strip())
        ref = _source_ref(
            document,
            page_number=continuation.page_number,
            lineage=continuation.lineage,
            block_id=continuation.block_id,
            excerpt=continuation.text,
        )
        if continuation.lineage is None or not ref.source:
            review.append(f"recovery_lineage_missing:block:{continuation.block_id}")
        refs.append(ref)
        direction = "next" if (adjacent_page.page_number or 0) > (from_page.page_number or 0) else "prev"
        steps.append(
            FormulaRecoveryStep(
                action="adjacent_page_continuation",
                detail=(
                    f"from_page={from_page.page_number};to_page={adjacent_page.page_number};"
                    f"authorization={authorization_source};direction={direction}"
                ),
                source_refs=(ref,),
            )
        )

    @staticmethod
    def _lookup_canonical_formula(
        evidence: FormulaEvidence,
        page: CanonicalPage,
        anchor: CanonicalBlock,
    ) -> _CanonicalFormulaLookupResult:
        formula_id = str(anchor.formula_id or evidence.metadata.get("formula_id") or "").strip()
        if not formula_id:
            return _CanonicalFormulaLookupResult(
                status=_CanonicalFormulaLookupStatus.MISSING,
            )
        matches = [formula for formula in page.formulas if formula.formula_id == formula_id]
        if not matches:
            return _CanonicalFormulaLookupResult(
                status=_CanonicalFormulaLookupStatus.MISSING,
                formula_id=formula_id,
            )
        if len(matches) == 1:
            return _CanonicalFormulaLookupResult(
                status=_CanonicalFormulaLookupStatus.UNIQUE,
                formula_id=formula_id,
                formula=matches[0],
                match_count=1,
            )
        return _CanonicalFormulaLookupResult(
            status=_CanonicalFormulaLookupStatus.AMBIGUOUS,
            formula_id=formula_id,
            match_count=len(matches),
        )

    @staticmethod
    def _explicit_table_ids(
        evidence: FormulaEvidence,
        page: CanonicalPage,
        anchor: CanonicalBlock,
        canonical_formula: CanonicalFormula | None = None,
    ) -> tuple[str, ...]:
        ids: list[Any] = list(evidence.linked_table_refs)
        if anchor.table_id:
            ids.append(anchor.table_id)
        ids.extend(
            _metadata_strings(
                anchor.metadata,
                "table_id",
                "linked_table_ref",
                "linked_table_refs",
                "linked_table_ids",
            )
        )
        if canonical_formula is None:
            lookup = FormulaContextRecovery._lookup_canonical_formula(evidence, page, anchor)
            canonical_formula = lookup.formula
        if canonical_formula is not None:
            ids.extend(
                _metadata_strings(
                    canonical_formula.metadata,
                    "table_id",
                    "linked_table_ref",
                    "linked_table_refs",
                    "linked_table_ids",
                )
            )
        return _unique_strings(ids)

    @staticmethod
    def _explicit_continuation_targets(*metadata_sources: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        block_ids: list[str] = []
        formula_ids: list[str] = []
        for metadata in metadata_sources:
            block_ids.extend(_metadata_strings(metadata, "continuation_block_id", "continuation_block_ids"))
            formula_ids.extend(_metadata_strings(metadata, "continuation_formula_id", "continuation_formula_ids"))
        return _unique_strings(block_ids), _unique_strings(formula_ids)

    @staticmethod
    def _continuation_candidates(
        anchor: CanonicalBlock,
        page: CanonicalPage,
        *,
        formula_metadata: Mapping[str, Any] | None = None,
        allow_unlinked_fallback: bool = True,
    ) -> tuple[CanonicalBlock, ...]:
        formula_metadata = formula_metadata or {}
        explicit_ids, explicit_formula_ids = FormulaContextRecovery._explicit_continuation_targets(
            anchor.metadata,
            formula_metadata,
        )
        if explicit_ids or explicit_formula_ids:
            return tuple(
                block
                for block in page.blocks
                if block.block_id in explicit_ids or (block.formula_id and block.formula_id in explicit_formula_ids)
            )

        anchor_keys = {anchor.block_id}
        if anchor.formula_id:
            anchor_keys.add(anchor.formula_id)
        linked: list[CanonicalBlock] = []
        for block in page.blocks:
            references = set(
                _metadata_strings(
                    block.metadata,
                    "continuation_of",
                    "linked_from_block_id",
                    "source_block_id",
                    "continuation_of_formula_id",
                )
            )
            if references & anchor_keys:
                linked.append(block)
        if linked:
            return tuple(linked)

        flagged = [
            block
            for block in page.blocks
            if bool(block.metadata.get("continuation") or block.metadata.get("is_continuation"))
        ]
        if flagged:
            return tuple(flagged)

        if not allow_unlinked_fallback:
            return ()

        # An anchor-owned textual marker authorizes only the adjacent page.  In
        # that narrow case, accept exactly the ordinary candidates on that page;
        # multiple candidates remain REVIEW rather than being guessed.
        return tuple(block for block in page.blocks if block.block_type in _CONTEXT_BLOCK_TYPES)

    @staticmethod
    def _merge_context(parts: Iterable[str]) -> str:
        result: list[str] = []
        seen: set[str] = set()
        for raw in parts:
            value = str(raw or "").strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return "\n".join(result)

    @staticmethod
    def _finish(
        evidence: FormulaEvidence,
        context_parts: Sequence[str],
        refs: Sequence[FormulaSourceRef],
        steps: Sequence[FormulaRecoveryStep],
        review: Sequence[str],
        fatal: Sequence[str],
        *,
        already_recovered: bool = False,
    ) -> FormulaRecoveryResult:
        recovered_refs = _dedupe_refs(refs)
        recovered = evidence if already_recovered else FormulaEvidence(
            raw_formula=evidence.raw_formula,
            normalized_expression=evidence.normalized_expression,
            context_text=FormulaContextRecovery._merge_context(context_parts),
            variable_definitions=dict(evidence.variable_definitions),
            conditions=tuple(evidence.conditions),
            source_refs=recovered_refs,
            linked_table_refs=tuple(evidence.linked_table_refs),
            metadata=dict(evidence.metadata),
        )
        bindings = LocalContextVariableBinder().bind(recovered)
        gate = FormulaEvidenceGate().evaluate(recovered, bindings)
        reasons = tuple(dict.fromkeys([*fatal, *review, *gate.reasons]))
        if fatal or gate.status is FormulaGateStatus.FAIL:
            status = FormulaGateStatus.FAIL
        elif review or gate.status is FormulaGateStatus.REVIEW:
            status = FormulaGateStatus.REVIEW
        else:
            status = FormulaGateStatus.PASS
        return FormulaRecoveryResult(
            status=status,
            recovered_evidence=recovered,
            recovered_source_refs=recovered_refs,
            recovery_steps=tuple(steps),
            reasons=reasons,
            gate_result=gate,
        )
