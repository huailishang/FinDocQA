"""Parser-agnostic document contracts for FinDocQA.

These contracts form the boundary between document ingestion/parsing and the QA
pipeline.  Downstream retrieval and reasoning should depend on these types, not
on MinerU/PyMuPDF/benchmark-specific payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple


class CanonicalBlockType(str, Enum):
    HEADING = "heading"
    TEXT = "text"
    TABLE = "table"
    LIST = "list"
    CODE = "code"
    FORMULA = "formula"
    FIGURE = "figure"
    HR = "hr"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceLineage:
    source_type: str
    source_path: str
    parser_name: str = ""
    parser_version: str = ""
    page_number: Optional[int] = None
    source_page_index: Optional[int] = None
    bbox: Optional[Tuple[float, float, float, float]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalTable:
    table_id: str
    page_number: Optional[int]
    markdown: str = ""
    html: str = ""
    headers: Tuple[str, ...] = ()
    rows: Tuple[Tuple[str, ...], ...] = ()
    caption: str = ""
    footnote: str = ""
    lineage: Optional[SourceLineage] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalFormula:
    formula_id: str
    page_number: Optional[int]
    expression: str
    latex: str = ""
    lineage: Optional[SourceLineage] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalFigure:
    figure_id: str
    page_number: Optional[int]
    uri: str = ""
    caption: str = ""
    alt_text: str = ""
    lineage: Optional[SourceLineage] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalBlock:
    block_id: str
    page_number: Optional[int]
    block_type: CanonicalBlockType
    text: str
    section_path: Tuple[str, ...] = ()
    heading_level: Optional[int] = None
    reading_order: Optional[int] = None
    bbox: Optional[Tuple[float, float, float, float]] = None
    table_id: Optional[str] = None
    formula_id: Optional[str] = None
    figure_id: Optional[str] = None
    lineage: Optional[SourceLineage] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalPage:
    page_number: Optional[int]
    text: str
    blocks: Tuple[CanonicalBlock, ...]
    tables: Tuple[CanonicalTable, ...] = ()
    formulas: Tuple[CanonicalFormula, ...] = ()
    figures: Tuple[CanonicalFigure, ...] = ()
    section_paths: Tuple[Tuple[str, ...], ...] = ()
    quality_flags: Tuple[str, ...] = ()
    lineage: Optional[SourceLineage] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalDocument:
    document_id: str
    domain: str
    title: str
    source_type: str
    source_uri: str
    parser_name: str
    parser_version: str
    pages: Tuple[CanonicalPage, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def iter_blocks(self) -> Iterable[CanonicalBlock]:
        for page in self.pages:
            yield from page.blocks

    def page(self, page_number: int) -> CanonicalPage | None:
        return next((p for p in self.pages if p.page_number == page_number), None)


@dataclass(frozen=True)
class RawDocumentSource:
    document_id: str
    domain: str
    source_type: str
    source_uri: str
    title: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
