"""Parser-neutral section/block contracts (P7D Workstream B, B-OFFLINE-1).

These types describe document structure in a way that is independent of the
underlying parser (PyMuPDF4LLM Markdown, MinerU Markdown/JSON, or any future
adapter). Every parser adapter should normalize its output into these
contracts so downstream retrieval / chunking logic never needs to know which
parser produced the pages.

Design constraints (from ``docs/p7d-parallel-workstreams.md`` Workstream B):

- preserve heading path (ancestor section titles, root -> leaf);
- preserve page mapping (from ``page_XXXX.md`` filename or parser metadata);
- preserve content type, especially table boundaries (tables must stay whole);
- keep stable, deterministic identifiers so re-parsing the same input yields
  identical ``block_id`` / ``section_id`` values (enables offline diffing).

The dataclasses are frozen and use tuples for ordered sequences so they are
hashable and safe to compare in offline regression tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class ContentType(str, Enum):
    """Coarse content type for a block.

    ``TABLE`` is first-class so chunking can keep a table whole instead of
    slicing it into 1800-char windows (a known weakness of the current
    window-based retriever). ``FORMULA`` is reserved for future parser
    adapters that can isolate formula blocks (e.g. MinerU); the deterministic
    Markdown parser does not emit it today.
    """

    HEADING = "heading"
    TEXT = "text"
    TABLE = "table"
    LIST = "list"
    CODE = "code"
    FORMULA = "formula"
    HR = "hr"  # horizontal rule / page break marker


@dataclass(frozen=True)
class Block:
    """A single structural unit of a page.

    Attributes:
        block_id: deterministic id, stable across re-parses of the same input
            (``{source_file}::b{index}``). Never contains secrets.
        page: 1-based page number parsed from ``page_XXXX.md`` when available;
            ``None`` when the source has no page mapping.
        section_path: ancestor heading titles from root to the heading that
            contains this block, inclusive of the block's own heading when the
            block itself is a heading. Empty tuple for content before any
            heading.
        content_type: coarse type used by the chunk builder to decide whether
            the block may be merged / sliced.
        content: raw block text. For tables this is the full GFM table
            (header + separator + rows) so it renders standalone.
        source_file: absolute or repo-relative path of the source page file.
        heading_level: 1-6 for ATX headings, ``None`` otherwise.
        metadata: parser-specific extras (e.g. row/col counts for tables).
    """

    block_id: str
    page: Optional[int]
    section_path: Tuple[str, ...]
    content_type: ContentType
    content: str
    source_file: str
    heading_level: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Section:
    """A heading and the blocks that belong to it.

    ``section_path`` is the ancestor chain including this section's own title
    (root -> ... -> this title). ``block_ids`` lists every non-heading block
    that falls under this section up to the next sibling/ancestor heading, in
    document order. Headings themselves are emitted both as ``Block`` (so the
    chunk builder can include them) and as ``Section`` (so a future retriever
    can pull parent context by section id).
    """

    section_id: str
    title: str
    level: int
    page: Optional[int]
    section_path: Tuple[str, ...]
    source_file: str
    block_ids: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    """Result of parsing one page file into structural contracts.

    A page file (``page_0001.md``) maps to exactly one ``ParsedDocument``.
    A full document (multiple pages) is represented by a sequence of
    ``ParsedDocument`` values; cross-page section continuity is recoverable by
    callers because ``section_path`` is built per-page from the headings
    actually present on that page. Cross-page heading stitching is deliberately
    not done here to keep the parser deterministic and per-file testable.
    """

    domain: str
    doc_id: str
    source_file: str
    page: Optional[int]
    parser: str
    blocks: Tuple[Block, ...]
    sections: Tuple[Section, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
