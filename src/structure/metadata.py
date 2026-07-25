"""Deterministic source/page/path metadata helpers (Lane B, remote-offline).

Pure functions that derive stable, parser-neutral metadata (doc_id, page
label, normalized source path, section-path string, section depth) from
``Block`` / ``StructureChunk`` sources. They let a future structure-aware
retriever cite each chunk's origin (which document, which page, which section
path) without re-implementing path parsing in every consumer.

Design constraints:

- **deterministic**: identical inputs always yield identical metadata, across
  platforms and runs. Windows backslash paths are normalized to POSIX forward
  slashes so a chunk parsed on Windows cites the same ``source_rel`` as one
  parsed on Linux.
- **pure**: no I/O, no retrieval, no solver, no prompt, no config change.
- **additive**: existing ``Block`` / ``StructureChunk`` fields are unchanged;
  these helpers only *read* them and produce derived metadata mappings.

See ``docs/p7d-workstream-b-remote-offline.md`` and the dispatch card Lane B.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping, Optional, Sequence

from .blocks import Block
from .chunks import StructureChunk


def page_label(page: Optional[int]) -> str:
    """Return the canonical page file stem, e.g. ``page_0001``.

    Returns an empty string when ``page`` is ``None`` (no page mapping), so
    callers can always embed the label without a conditional.
    """
    if page is None:
        return ""
    return f"page_{int(page):04d}"


def normalize_source_path(source_file: str, base: Optional[str] = None) -> str:
    """Normalize a source file path to a deterministic POSIX relative string.

    - Windows backslashes are converted to forward slashes (cross-platform).
    - ``.`` and ``..`` are resolved by ``PurePosixPath``.
    - when ``base`` is given and the path is under it, the result is made
      relative to ``base``; otherwise the POSIX-normalized path is returned.
    """
    s = str(source_file).replace("\\", "/")
    p = PurePosixPath(s)
    if base:
        base_p = PurePosixPath(str(base).replace("\\", "/"))
        try:
            p = p.relative_to(base_p)
        except ValueError:
            # Not under base: keep the POSIX-normalized path as-is.
            pass
    return p.as_posix()


def derive_doc_id(source_file: str, corpus_root: Optional[str] = None) -> str:
    """Derive a doc_id from a page file path.

    The doc_id is the name of the directory immediately containing the page
    file — the conventional ``<corpus>/<domain>/<doc_id>/page_XXXX.md`` layout.
    When the path has no parent directory, the file stem is used.

    ``corpus_root`` is accepted for API symmetry with ``normalize_source_path``
    but the doc_id is always the immediate parent directory name, so it does
    not depend on the corpus root.
    """
    s = str(source_file).replace("\\", "/")
    p = PurePosixPath(s)
    parent = p.parent
    if parent.name:
        return parent.name
    return p.stem


def section_path_string(section_path: Sequence[str], sep: str = " / ") -> str:
    """Join ancestor section titles into a single citation string."""
    return sep.join(section_path)


def section_depth(section_path: Sequence[str]) -> int:
    """Return the number of ancestor sections (0 for pre-heading content)."""
    return len(tuple(section_path))


def build_source_metadata(
    *,
    source_file: str,
    page: Optional[int],
    section_path: Sequence[str] = (),
    doc_id: Optional[str] = None,
    domain: Optional[str] = None,
    base: Optional[str] = None,
) -> Mapping[str, Any]:
    """Build the canonical deterministic source/page/path metadata mapping.

    This is the citation a structure-aware retriever can attach to a chunk so
    that any matched evidence is traceable to a specific document, page and
    section path. All fields are derived purely from the inputs; identical
    inputs always yield identical metadata.

    Fields:
        doc_id: derived from the source path unless explicitly given.
        domain: the domain label (empty string when not provided).
        page: the 1-based page number (may be ``None``).
        page_label: ``page_0001`` or ``""``.
        source_file: the original source path (unchanged).
        source_rel: the POSIX-normalized, base-relative source path.
        section_path: tuple of ancestor section titles.
        section_path_string: ``"第一章 / 投保规则"``.
        section_depth: number of ancestor sections.
    """
    return {
        "doc_id": doc_id if doc_id is not None else derive_doc_id(source_file, base),
        "domain": domain or "",
        "page": page,
        "page_label": page_label(page),
        "source_file": source_file,
        "source_rel": normalize_source_path(source_file, base),
        "section_path": tuple(section_path),
        "section_path_string": section_path_string(section_path),
        "section_depth": section_depth(section_path),
    }


def chunk_source_metadata(
    chunk: StructureChunk,
    *,
    doc_id: Optional[str] = None,
    domain: Optional[str] = None,
    base: Optional[str] = None,
) -> Mapping[str, Any]:
    """Extract deterministic source/page/path metadata from a ``StructureChunk``."""
    return build_source_metadata(
        source_file=chunk.source_file,
        page=chunk.page,
        section_path=chunk.section_path,
        doc_id=doc_id,
        domain=domain,
        base=base,
    )


def block_source_metadata(
    block: Block,
    *,
    doc_id: Optional[str] = None,
    domain: Optional[str] = None,
    base: Optional[str] = None,
) -> Mapping[str, Any]:
    """Extract deterministic source/page/path metadata from a ``Block``."""
    return build_source_metadata(
        source_file=block.source_file,
        page=block.page,
        section_path=block.section_path,
        doc_id=doc_id,
        domain=domain,
        base=base,
    )
