"""Parent-child chunk builder (P7D Workstream B, B-OFFLINE-3).

Turns ``ParsedDocument`` blocks into retrieval-ready ``StructureChunk`` values
that preserve:

- table boundaries (a table is never sliced across chunks);
- parent section context (each chunk carries the ancestor heading titles, so
  a future retriever can prepend parent context when a child block is matched);
- stable ids that mirror the source block ids (deterministic diffing).

This module is intentionally separate from ``LexicalHybridRetriever``. It does
not change current retrieval behavior; it only provides the contract and
helpers a future structure-aware retriever (or a Workstream A/B A/B evaluation)
would consume. No retrieval scoring, solver logic, prompt or config default is
modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Optional, Tuple

from .blocks import Block, ContentType, ParsedDocument, Section

# Content types that must never be merged or sliced. Tables especially: the
# current window-based retriever can split a table across two 1800-char
# windows, which breaks row/column binding for financial answers.
_ATOMIC_TYPES = frozenset({ContentType.TABLE, ContentType.CODE})


@dataclass(frozen=True)
class StructureChunk:
    """A retrieval-ready structural chunk.

    Attributes:
        chunk_id: stable id; for single-block chunks it equals ``block_id``;
            for merged chunks it is the joined member block ids.
        page: page number inherited from the member blocks.
        section_path: ancestor heading titles (root -> leaf) shared by all
            member blocks; empty tuple for pre-heading content.
        content_type: type of the (first) member block. Merged chunks are
            labelled ``TEXT``.
        content: chunk text. Tables are kept verbatim; text runs are joined
            with blank lines.
        source_file: source page file shared by member blocks.
        parent_context: ancestor section titles joined by ``parent_context_sep``,
            excluding the chunk's own deepest section. This is the "parent
            section context" a retriever can prepend when a child block is
            matched, without re-deriving it from the section tree.
        block_ids: ids of the member blocks, in document order.
        metadata: extra fields (e.g. merged member count, table row/col counts).
    """

    chunk_id: str
    page: Optional[int]
    section_path: Tuple[str, ...]
    content_type: ContentType
    content: str
    source_file: str
    parent_context: str
    block_ids: Tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _ancestor_titles(section_path: Tuple[str, ...], own_is_heading: bool) -> Tuple[str, ...]:
    """Return the parent titles for a block.

    For a heading block, its own title is the last element of ``section_path``;
    the parent context is everything before it. For non-heading blocks the
    whole ``section_path`` is the ancestor chain and is kept as-is (the block
    does not "own" the deepest heading, it just lives under it).
    """
    if not section_path:
        return ()
    if own_is_heading:
        return section_path[:-1]
    return section_path


def build_chunks(
    doc: ParsedDocument,
    *,
    include_headings: bool = False,
    parent_context_sep: str = " / ",
    max_text_chars: Optional[int] = None,
) -> List[StructureChunk]:
    """Build one ``StructureChunk`` per block.

    Tables and code blocks are kept atomic (never sliced). Text blocks may be
    truncated to ``max_text_chars`` when set, but only by character count, not
    by windowing — a future retriever can still choose its own windowing on top
    of the chunk's ``content``.

    Args:
        doc: parsed document from ``parse_markdown_text`` / ``parse_page_file``.
        include_headings: when False (default), heading blocks are skipped
            because their text is already recoverable via ``section_path`` /
            ``parent_context``. Set True to emit heading chunks too.
        parent_context_sep: separator used to join ancestor titles into
            ``parent_context``.
        max_text_chars: optional character cap applied to ``TEXT``/``LIST``
            chunk content only; atomic types are never truncated.

    Returns:
        chunks in document order.
    """
    chunks: List[StructureChunk] = []
    for blk in doc.blocks:
        if blk.content_type == ContentType.HEADING and not include_headings:
            continue
        is_heading = blk.content_type == ContentType.HEADING
        ancestors = _ancestor_titles(blk.section_path, own_is_heading=is_heading)
        parent_context = parent_context_sep.join(ancestors)

        content = blk.content
        if (max_text_chars is not None
                and blk.content_type in (ContentType.TEXT, ContentType.LIST)
                and len(content) > max_text_chars):
            content = content[:max_text_chars]

        meta = dict(blk.metadata)
        meta["merged_count"] = 1
        chunks.append(StructureChunk(
            chunk_id=blk.block_id,
            page=blk.page,
            section_path=blk.section_path,
            content_type=blk.content_type,
            content=content,
            source_file=blk.source_file,
            parent_context=parent_context,
            block_ids=(blk.block_id,),
            metadata=meta,
        ))
    return chunks


def merge_adjacent_text(
    chunks: Iterable[StructureChunk],
    *,
    max_chars: Optional[int] = 1800,
) -> List[StructureChunk]:
    """Merge contiguous ``TEXT``/``LIST`` chunks sharing the same section path.

    Atomic chunks (``TABLE`` / ``CODE`` / ``HR`` / ``HEADING``) are passed
    through untouched and act as merge barriers. Merging only happens within a
    single source file and the same ``section_path``, so cross-section or
    cross-page content is never combined.

    The merged ``chunk_id`` is the member block ids joined by ``+`` so it stays
    deterministic and traceable. ``parent_context`` is inherited from the first
    member (they share the same ancestors by construction).

    Args:
        chunks: output of ``build_chunks``.
        max_chars: soft cap on merged content length; once exceeded, a new
            merged chunk starts. ``None`` disables the cap (merge greedily
            within the same section). Atomic chunks ignore this cap.
    """
    result: List[StructureChunk] = []
    pending: Optional[StructureChunk] = None

    def _flush():
        nonlocal pending
        if pending is not None:
            result.append(pending)
            pending = None

    for ch in chunks:
        mergeable = ch.content_type in (ContentType.TEXT, ContentType.LIST)
        if not mergeable:
            _flush()
            result.append(ch)
            continue
        if pending is None:
            pending = ch
            continue
        # Same source + same section path -> merge; else flush and start fresh.
        same_group = (ch.source_file == pending.source_file
                      and ch.section_path == pending.section_path)
        if not same_group:
            _flush()
            pending = ch
            continue
        merged_content = pending.content + "\n\n" + ch.content
        if max_chars is not None and len(merged_content) > max_chars:
            # Would exceed cap: flush pending, start a new group with ch.
            _flush()
            pending = ch
            continue
        merged_ids = pending.block_ids + ch.block_ids
        merged_meta = dict(pending.metadata)
        merged_meta["merged_count"] = len(merged_ids)
        # Preserve table/row metadata from members if any (none for TEXT/LIST).
        pending = StructureChunk(
            chunk_id="+".join(merged_ids),
            page=pending.page,
            section_path=pending.section_path,
            content_type=ContentType.TEXT,  # merged label
            content=merged_content,
            source_file=pending.source_file,
            parent_context=pending.parent_context,
            block_ids=merged_ids,
            metadata=merged_meta,
        )
    _flush()
    return result


def section_context_for_block(
    sections: Iterable[Section],
    block_id: str,
) -> Optional[Section]:
    """Return the ``Section`` whose ``block_ids`` contain ``block_id``.

    Helper for a future retriever that, having matched a child block, wants to
    pull the owning section's metadata (title, level, path). Returns ``None``
    if no section owns the block (e.g. pre-heading content).
    """
    for sec in sections:
        if block_id in sec.block_ids:
            return sec
    return None
