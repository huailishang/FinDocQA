"""MinerU -> CanonicalDocument adapter.

Phase-1 intentionally reuses the existing, well-tested MinerU page-contract
adapter.  The old retrieval corpus is not changed; this module only adds the
new parser-agnostic document boundary on top of it.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from document.contracts import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalFigure,
    CanonicalFormula,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from structure.blocks import Block, ContentType
from structure.mineru_adapter import MinerUContentItem, adapt_document, load_content_items
from structure.parser import parse_page_file

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_FORMULA_RE = re.compile(r"^\$\$\s*(.*?)\s*\$\$", re.DOTALL)

_TYPE_MAP = {
    ContentType.HEADING: CanonicalBlockType.HEADING,
    ContentType.TEXT: CanonicalBlockType.TEXT,
    ContentType.TABLE: CanonicalBlockType.TABLE,
    ContentType.LIST: CanonicalBlockType.LIST,
    ContentType.CODE: CanonicalBlockType.CODE,
    ContentType.FORMULA: CanonicalBlockType.FORMULA,
    ContentType.HR: CanonicalBlockType.HR,
}


def _read_structure(path: Path) -> dict[str, Any]:
    structure_path = path / "document_structure.json"
    if not structure_path.is_file():
        return {}
    try:
        payload = json.loads(structure_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _gfm_rows(markdown: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    pipe_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if len(pipe_lines) < 2:
        return (), ()

    def cells(line: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in line.strip("|").split("|"))

    headers = cells(pipe_lines[0])
    data_lines = pipe_lines[2:] if len(pipe_lines) >= 2 else ()
    rows = tuple(cells(line) for line in data_lines)
    return headers, rows


def _formula_text(block: Block) -> str | None:
    if block.content_type == ContentType.FORMULA:
        return block.content.strip()
    match = _FORMULA_RE.match(block.content.strip())
    return match.group(1).strip() if match else None


def _lineage(
    *,
    source_type: str,
    source_uri: str,
    parser_name: str,
    parser_version: str,
    page_number: int | None,
    adapted_page_path: str,
    metadata: Mapping[str, Any] | None = None,
) -> SourceLineage:
    extras = {"adapted_page_path": adapted_page_path}
    extras.update(dict(metadata or {}))
    return SourceLineage(
        source_type=source_type,
        source_path=source_uri,
        parser_name=parser_name,
        parser_version=parser_version,
        page_number=page_number,
        source_page_index=(page_number - 1 if page_number and page_number > 0 else None),
        metadata=extras,
    )


def _canonicalize_block(
    block: Block,
    *,
    order: int,
    lineage: SourceLineage,
) -> tuple[CanonicalBlock, CanonicalTable | None, CanonicalFormula | None, CanonicalFigure | None]:
    block_type = _TYPE_MAP.get(block.content_type, CanonicalBlockType.UNKNOWN)
    table: CanonicalTable | None = None
    formula: CanonicalFormula | None = None
    figure: CanonicalFigure | None = None
    table_id = formula_id = figure_id = None

    if block.content_type == ContentType.TABLE:
        table_id = f"{block.block_id}::table"
        headers, rows = _gfm_rows(block.content)
        table = CanonicalTable(
            table_id=table_id,
            page_number=block.page,
            markdown=block.content,
            headers=headers,
            rows=rows,
            lineage=lineage,
            metadata={"legacy_block_id": block.block_id, **dict(block.metadata)},
        )

    formula_text = _formula_text(block)
    if formula_text:
        block_type = CanonicalBlockType.FORMULA
        formula_id = f"{block.block_id}::formula"
        formula = CanonicalFormula(
            formula_id=formula_id,
            page_number=block.page,
            expression=formula_text,
            latex=formula_text,
            lineage=lineage,
            metadata={"legacy_block_id": block.block_id},
        )

    image_match = _IMAGE_RE.search(block.content)
    if image_match:
        figure_id = f"{block.block_id}::figure"
        figure = CanonicalFigure(
            figure_id=figure_id,
            page_number=block.page,
            uri=image_match.group(2).strip(),
            caption=image_match.group(1).strip(),
            alt_text=image_match.group(1).strip(),
            lineage=lineage,
            metadata={"legacy_block_id": block.block_id},
        )
        if block_type == CanonicalBlockType.TEXT and block.content.strip().startswith("!["):
            block_type = CanonicalBlockType.FIGURE

    canonical = CanonicalBlock(
        block_id=block.block_id,
        page_number=block.page,
        block_type=block_type,
        text=block.content,
        section_path=tuple(block.section_path),
        heading_level=block.heading_level,
        reading_order=order,
        table_id=table_id,
        formula_id=formula_id,
        figure_id=figure_id,
        lineage=lineage,
        metadata=dict(block.metadata),
    )
    return canonical, table, formula, figure


def canonical_from_adapted_mineru(
    adapted_dir: str | Path,
    *,
    domain: str | None = None,
    doc_id: str | None = None,
    source_uri: str | None = None,
    source_type: str = "mineru",
    parser_version: str = "",
) -> CanonicalDocument:
    """Load an existing page_XXXX.md MinerU contract as CanonicalDocument."""
    root = Path(adapted_dir)
    structure = _read_structure(root)
    resolved_domain = str(domain or structure.get("domain") or "unknown")
    resolved_doc_id = str(doc_id or structure.get("doc_id") or root.name)
    parser_name = str(structure.get("parser") or "mineru")
    resolved_source_uri = str(source_uri or root)

    pages: list[CanonicalPage] = []
    title = ""
    page_files = sorted(root.glob("page_*.md"))
    quality_flags = tuple(str(w) for w in structure.get("warnings", []) if str(w))
    if structure.get("degraded"):
        quality_flags = tuple((*quality_flags, "degraded_reconstruction"))

    for page_path in page_files:
        parsed = parse_page_file(
            page_path,
            domain=resolved_domain,
            doc_id=resolved_doc_id,
            parser=f"{parser_name}_canonical_adapter",
        )
        page_lineage = _lineage(
            source_type=source_type,
            source_uri=resolved_source_uri,
            parser_name=parser_name,
            parser_version=parser_version,
            page_number=parsed.page,
            adapted_page_path=str(page_path),
            metadata={"reconstruction_mode": structure.get("reconstruction_mode", "")},
        )
        blocks: list[CanonicalBlock] = []
        tables: list[CanonicalTable] = []
        formulas: list[CanonicalFormula] = []
        figures: list[CanonicalFigure] = []
        section_paths: list[tuple[str, ...]] = []
        for order, block in enumerate(parsed.blocks):
            canonical, table, formula, figure = _canonicalize_block(
                block, order=order, lineage=page_lineage
            )
            blocks.append(canonical)
            if table is not None:
                tables.append(table)
            if formula is not None:
                formulas.append(formula)
            if figure is not None:
                figures.append(figure)
            if canonical.section_path and canonical.section_path not in section_paths:
                section_paths.append(canonical.section_path)
            if not title and canonical.block_type == CanonicalBlockType.HEADING:
                title = canonical.text.lstrip("#").strip()

        page_text = page_path.read_text(encoding="utf-8", errors="ignore")
        pages.append(
            CanonicalPage(
                page_number=parsed.page,
                text=page_text,
                blocks=tuple(blocks),
                tables=tuple(tables),
                formulas=tuple(formulas),
                figures=tuple(figures),
                section_paths=tuple(section_paths),
                quality_flags=quality_flags,
                lineage=page_lineage,
                metadata={"legacy_parser": parsed.parser},
            )
        )

    return CanonicalDocument(
        document_id=resolved_doc_id,
        domain=resolved_domain,
        title=title or resolved_doc_id,
        source_type=source_type,
        source_uri=resolved_source_uri,
        parser_name=parser_name,
        parser_version=parser_version,
        pages=tuple(pages),
        metadata={
            "reconstruction_mode": structure.get("reconstruction_mode", ""),
            "degraded": bool(structure.get("degraded", False)),
            "warnings": quality_flags,
            "legacy_document_structure": structure,
        },
    )


def _enrich_with_raw_items(
    document: CanonicalDocument,
    items: tuple[MinerUContentItem, ...],
) -> CanonicalDocument:
    """Restore MinerU-only structure that page Markdown cannot faithfully keep."""

    enriched_pages: list[CanonicalPage] = []
    for page in document.pages:
        if page.page_number is None:
            enriched_pages.append(page)
            continue
        page_items = [item for item in items if item.page_index + 1 == page.page_number]
        raw_tables = [item for item in page_items if item.item_type == "table"]
        raw_formulas = [item for item in page_items if item.item_type in {"formula", "equation"}]
        raw_figures = [item for item in page_items if item.item_type == "image"]

        tables = list(page.tables)
        for index, item in enumerate(raw_tables):
            if index < len(tables):
                table = tables[index]
                tables[index] = replace(
                    table,
                    html=item.table_html or table.html,
                    caption=item.table_caption or table.caption,
                    footnote=item.table_footnote or table.footnote,
                    metadata={**dict(table.metadata), "raw_structured_source": "mineru"},
                )
            else:
                tables.append(
                    CanonicalTable(
                        table_id=f"{document.document_id}::p{page.page_number}::raw_table{index}",
                        page_number=page.page_number,
                        html=item.table_html,
                        caption=item.table_caption,
                        footnote=item.table_footnote,
                        lineage=page.lineage,
                        metadata={"raw_structured_source": "mineru"},
                    )
                )

        formulas = list(page.formulas)
        for index, item in enumerate(raw_formulas):
            expression = item.text.strip()
            if index < len(formulas):
                formula = formulas[index]
                formulas[index] = replace(
                    formula,
                    expression=expression or formula.expression,
                    latex=expression or formula.latex,
                    metadata={**dict(formula.metadata), "raw_structured_source": "mineru"},
                )
            elif expression:
                formulas.append(
                    CanonicalFormula(
                        formula_id=f"{document.document_id}::p{page.page_number}::raw_formula{index}",
                        page_number=page.page_number,
                        expression=expression,
                        latex=expression,
                        lineage=page.lineage,
                        metadata={"raw_structured_source": "mineru"},
                    )
                )

        figures = list(page.figures)
        for index, item in enumerate(raw_figures):
            if index < len(figures):
                figure = figures[index]
                figures[index] = replace(
                    figure,
                    uri=item.image_path or figure.uri,
                    caption=item.image_caption or figure.caption,
                    alt_text=item.image_caption or figure.alt_text,
                    metadata={**dict(figure.metadata), "raw_structured_source": "mineru"},
                )
            else:
                figures.append(
                    CanonicalFigure(
                        figure_id=f"{document.document_id}::p{page.page_number}::raw_figure{index}",
                        page_number=page.page_number,
                        uri=item.image_path,
                        caption=item.image_caption,
                        alt_text=item.image_caption,
                        lineage=page.lineage,
                        metadata={"raw_structured_source": "mineru"},
                    )
                )

        enriched_pages.append(
            replace(
                page,
                tables=tuple(tables),
                formulas=tuple(formulas),
                figures=tuple(figures),
                metadata={**dict(page.metadata), "raw_structured_item_count": len(page_items)},
            )
        )

    return replace(
        document,
        pages=tuple(enriched_pages),
        metadata={**dict(document.metadata), "raw_structured_item_count": len(items)},
    )


def canonical_from_raw_mineru(
    mineru_dir: str | Path,
    *,
    domain: str,
    doc_id: str,
    parser_version: str = "",
) -> CanonicalDocument:
    """Adapt raw MinerU output in isolation, then return CanonicalDocument.

    A temporary legacy page contract is used so the established MinerU adapter
    remains the single reconstruction implementation during Phase 1.
    """
    raw_root = Path(mineru_dir)
    raw_items = load_content_items(raw_root, doc_id)
    with tempfile.TemporaryDirectory(prefix="findocqa_canonical_mineru_") as tmp:
        adapted = Path(tmp) / resolved_safe_name(doc_id)
        adapt_document(raw_root, adapted, domain=domain, doc_id=doc_id)
        document = canonical_from_adapted_mineru(
            adapted,
            domain=domain,
            doc_id=doc_id,
            source_uri=str(raw_root),
            source_type="mineru_raw",
            parser_version=parser_version,
        )
    return _enrich_with_raw_items(document, raw_items)


def resolved_safe_name(value: str) -> str:
    """Keep temporary paths deterministic enough for diagnostics without qid logic."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
    return safe or "document"
