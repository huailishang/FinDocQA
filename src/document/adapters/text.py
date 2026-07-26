"""Plain-text and Markdown input adapters for CanonicalDocument."""
from __future__ import annotations

from pathlib import Path

from document.contracts import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from structure.blocks import ContentType
from structure.parser import parse_markdown_text

_TYPE_MAP = {
    ContentType.HEADING: CanonicalBlockType.HEADING,
    ContentType.TEXT: CanonicalBlockType.TEXT,
    ContentType.TABLE: CanonicalBlockType.TABLE,
    ContentType.LIST: CanonicalBlockType.LIST,
    ContentType.CODE: CanonicalBlockType.CODE,
    ContentType.FORMULA: CanonicalBlockType.FORMULA,
    ContentType.HR: CanonicalBlockType.HR,
}


def _table_from_markdown(block_id: str, page: int | None, content: str, lineage: SourceLineage) -> CanonicalTable:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    if len(lines) >= 2 and lines[0].startswith("|"):
        headers = tuple(part.strip() for part in lines[0].strip("|").split("|"))
        rows = tuple(
            tuple(part.strip() for part in line.strip("|").split("|"))
            for line in lines[2:]
            if line.startswith("|") and line.endswith("|")
        )
    return CanonicalTable(
        table_id=f"{block_id}::table",
        page_number=page,
        markdown=content,
        headers=headers,
        rows=rows,
        lineage=lineage,
    )


def canonical_from_markdown_file(
    path: str | Path,
    *,
    domain: str,
    doc_id: str | None = None,
    title: str = "",
) -> CanonicalDocument:
    """Import a Markdown file directly without running a PDF parser."""
    source = Path(path)
    resolved_doc_id = str(doc_id or source.stem)
    text = source.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_markdown_text(
        text,
        source_file=str(source),
        page=1,
        domain=domain,
        doc_id=resolved_doc_id,
        parser="markdown_import",
    )
    lineage = SourceLineage(
        source_type="markdown",
        source_path=str(source),
        parser_name="markdown_structure",
        page_number=1,
        source_page_index=0,
    )
    blocks: list[CanonicalBlock] = []
    tables: list[CanonicalTable] = []
    inferred_title = title
    section_paths: list[tuple[str, ...]] = []
    for order, block in enumerate(parsed.blocks):
        block_type = _TYPE_MAP.get(block.content_type, CanonicalBlockType.UNKNOWN)
        table_id = None
        if block.content_type == ContentType.TABLE:
            table = _table_from_markdown(block.block_id, block.page, block.content, lineage)
            tables.append(table)
            table_id = table.table_id
        blocks.append(
            CanonicalBlock(
                block_id=block.block_id,
                page_number=block.page,
                block_type=block_type,
                text=block.content,
                section_path=tuple(block.section_path),
                heading_level=block.heading_level,
                reading_order=order,
                table_id=table_id,
                lineage=lineage,
                metadata=dict(block.metadata),
            )
        )
        if block.section_path and tuple(block.section_path) not in section_paths:
            section_paths.append(tuple(block.section_path))
        if not inferred_title and block.content_type == ContentType.HEADING:
            inferred_title = block.content.lstrip("#").strip()

    page = CanonicalPage(
        page_number=1,
        text=text,
        blocks=tuple(blocks),
        tables=tuple(tables),
        section_paths=tuple(section_paths),
        lineage=lineage,
    )
    return CanonicalDocument(
        document_id=resolved_doc_id,
        domain=domain,
        title=inferred_title or resolved_doc_id,
        source_type="markdown",
        source_uri=str(source),
        parser_name="markdown_structure",
        parser_version="",
        pages=(page,),
    )


def canonical_from_text_file(
    path: str | Path,
    *,
    domain: str,
    doc_id: str | None = None,
    title: str = "",
) -> CanonicalDocument:
    """Import plain text as one canonical page without Markdown semantics."""
    source = Path(path)
    resolved_doc_id = str(doc_id or source.stem)
    text = source.read_text(encoding="utf-8", errors="ignore")
    lineage = SourceLineage(
        source_type="text",
        source_path=str(source),
        parser_name="plain_text_import",
        page_number=1,
        source_page_index=0,
    )
    block = CanonicalBlock(
        block_id=f"{source}::b0",
        page_number=1,
        block_type=CanonicalBlockType.TEXT,
        text=text,
        reading_order=0,
        lineage=lineage,
    )
    page = CanonicalPage(page_number=1, text=text, blocks=(block,), lineage=lineage)
    return CanonicalDocument(
        document_id=resolved_doc_id,
        domain=domain,
        title=title or resolved_doc_id,
        source_type="text",
        source_uri=str(source),
        parser_name="plain_text_import",
        parser_version="",
        pages=(page,),
    )
