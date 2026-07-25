"""P7D Workstream B — parser-neutral document structure layer.

This package is intentionally decoupled from retrieval scoring, solver logic
and prompts (see ``docs/p7d-parallel-workstreams.md`` Workstream B). It only
defines:

- deterministic, parser-neutral section/block contracts;
- a deterministic Markdown structure parser that preserves heading paths,
  page mapping and table boundaries;
- a parent-child chunk builder that keeps tables whole and exposes ancestor
  section context for later structure-aware retrieval.

It does NOT touch the current ``LexicalHybridRetriever`` behavior. A future
workstream may wire these contracts into retrieval once Workstream A's MinerU
corpus is stable; until then this layer is exercised only by offline fixtures
and unit tests.
"""

from __future__ import annotations

from .blocks import Block, ContentType, ParsedDocument, Section
from .chunks import StructureChunk, build_chunks, merge_adjacent_text
from .metadata import (
    block_source_metadata,
    build_source_metadata,
    chunk_source_metadata,
    derive_doc_id,
    normalize_source_path,
    page_label,
    section_depth,
    section_path_string,
)
from .parser import (
    extract_page_number,
    parse_markdown_text,
    parse_page_file,
)

__all__ = [
    "Block",
    "ContentType",
    "ParsedDocument",
    "Section",
    "StructureChunk",
    "build_chunks",
    "merge_adjacent_text",
    "extract_page_number",
    "parse_markdown_text",
    "parse_page_file",
    "block_source_metadata",
    "build_source_metadata",
    "chunk_source_metadata",
    "derive_doc_id",
    "normalize_source_path",
    "page_label",
    "section_depth",
    "section_path_string",
]
