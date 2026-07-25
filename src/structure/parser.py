"""Deterministic Markdown structure parser (P7D Workstream B, B-OFFLINE-2).

Parses one ``page_XXXX.md`` file into the parser-neutral ``Block`` /
``Section`` contracts. The parser is intentionally deterministic and
standard-library only:

- ATX headings (``#`` .. ``######``) drive the section stack. Setext
  (underline) headings are NOT supported on purpose: their detection collides
  with table separators and horizontal rules and would break determinism.
- GFM pipe tables (``| ... |`` rows with a ``| --- |`` separator) are captured
  as a single ``TABLE`` block so the chunk builder can keep them whole.
- Fenced code blocks (``` ``` ``` / ``~~~``) are captured as ``CODE`` blocks.
- Bullet / ordered list lines are grouped into ``LIST`` blocks.
- Horizontal rules (``---`` / ``***`` / ``___`` alone on a line) become ``HR``.
- Everything else is accumulated into ``TEXT`` blocks, one block per
  contiguous run of non-special lines.

Re-parsing the same file always yields identical ``block_id`` / ``section_id``
values, which is what makes offline before/after diffing meaningful.

This module does NOT read from or write to the live retrieval pipeline. It is
exercised only by ``tests/`` fixtures and, later, by an adapter that feeds
MinerU output through the same contracts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .blocks import Block, ContentType, ParsedDocument, Section

# ATX heading: 1-6 '#' then the title. Trailing '#' decorations are stripped.
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# page_0001.md -> 1 (1-based). Tolerant of zero-padding and case.
_PAGE_FILE = re.compile(r"page_(\d+)\.md$", re.IGNORECASE)
# A GFM table row: starts with optional spaces, then | ... |.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# A table separator row: | :---: | --- | etc. Each cell is :?-+:? (>=1 dash),
# cells separated by |. Supports single-column tables (| --- |) which the
# previous two-dash-group regex incorrectly rejected.
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?(\s*\|\s*:?-+:?)*\s*\|?\s*$")
# Fenced code block opener: ``` or ~~~ (3+), optionally with a language.
_FENCE_OPEN = re.compile(r"^(\s*)(`{3,}|~{3,})\s*(.*)$")
# Unordered list item: - / * / + followed by space.
_UL_ITEM = re.compile(r"^\s{0,3}[-*+]\s+\S")
# Ordered list item: digits then . or ) then space.
_OL_ITEM = re.compile(r"^\s{0,3}\d+[.)]\s+\S")
# Horizontal rule: 3+ of -, * or _ with optional spaces, nothing else.
_HR = re.compile(r"^\s{0,3}([-*_])\s*\1\s*\1[-*_\s]*$")


def extract_page_number(path) -> Optional[int]:
    """Return the 1-based page number encoded in ``page_XXXX.md``, else None."""
    m = _PAGE_FILE.search(str(path))
    return int(m.group(1)) if m else None


def parse_page_file(
    path,
    domain: str,
    doc_id: str,
    parser: str = "markdown_structure",
) -> ParsedDocument:
    """Parse a ``page_XXXX.md`` file on disk into a ``ParsedDocument``."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    return parse_markdown_text(
        text,
        source_file=str(p),
        page=extract_page_number(p),
        domain=domain,
        doc_id=doc_id,
        parser=parser,
    )


def parse_markdown_text(
    text: str,
    *,
    source_file: str,
    page: Optional[int],
    domain: str,
    doc_id: str,
    parser: str = "markdown_structure",
) -> ParsedDocument:
    """Parse Markdown text into structural contracts.

    Pure function: same ``text`` + ``source_file`` always produce identical
    block/section ids. ``source_file`` is part of the id namespace so blocks
    from different pages never collide.
    """
    lines = text.splitlines()
    blocks: List[Block] = []
    sections: List[Section] = []
    # section_stack: list of (level, title) representing the open ancestor chain.
    section_stack: List[Tuple[int, str]] = []
    block_index = 0
    # Pending (non-heading) block ids accumulated under the current deepest
    # section, flushed when a new heading starts.
    pending_block_ids: List[str] = []
    pending_section_idx: Optional[int] = None

    i = 0
    n = len(lines)

    def _section_path() -> Tuple[str, ...]:
        return tuple(title for _, title in section_stack)

    def _push_block(content_type: ContentType, content: str,
                    heading_level: Optional[int] = None,
                    metadata: Optional[dict] = None) -> Block:
        nonlocal block_index
        bid = f"{source_file}::b{block_index}"
        block_index += 1
        blk = Block(
            block_id=bid,
            page=page,
            section_path=_section_path(),
            content_type=content_type,
            content=content,
            source_file=source_file,
            heading_level=heading_level,
            metadata=metadata or {},
        )
        blocks.append(blk)
        return blk

    def _flush_pending_into_section() -> None:
        nonlocal pending_block_ids, pending_section_idx
        if pending_section_idx is None:
            pending_block_ids = []
            return
        if not pending_block_ids:
            return
        sec = sections[pending_section_idx]
        sections[pending_section_idx] = Section(
            section_id=sec.section_id,
            title=sec.title,
            level=sec.level,
            page=sec.page,
            section_path=sec.section_path,
            source_file=sec.source_file,
            block_ids=tuple(pending_block_ids),
            metadata=sec.metadata,
        )
        pending_block_ids = []

    while i < n:
        line = lines[i]
        # Skip blank lines: they are paragraph separators, not block content.
        # Paragraph granularity is what lets ``merge_adjacent_text`` recombine
        # paragraphs deterministically instead of having one giant text block.
        if not line.strip():
            i += 1
            continue

        # ── ATX heading ──────────────────────────────────────────────
        m = _ATX_HEADING.match(line)
        if m:
            _flush_pending_into_section()
            level = len(m.group(1))
            title = m.group(2).strip()
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            section_stack.append((level, title))
            blk = _push_block(
                ContentType.HEADING, line, heading_level=level,
                metadata={"heading_level": level, "title": title},
            )
            sec_id = f"{source_file}::s{len(sections)}"
            sections.append(Section(
                section_id=sec_id, title=title, level=level, page=page,
                section_path=_section_path(), source_file=source_file,
            ))
            pending_section_idx = len(sections) - 1
            pending_block_ids = []
            i += 1
            continue

        # ── Fenced code block ────────────────────────────────────────
        fence = _FENCE_OPEN.match(line)
        if fence:
            fence_marker = fence.group(2)[0]
            fence_len = len(fence.group(2))
            buf = [line]
            i += 1
            while i < n:
                cur = lines[i]
                buf.append(cur)
                # Closing fence: same marker, >= opening length, alone on line.
                if re.match(rf"^\s*{re.escape(fence_marker)}{{{fence_len},}}\s*$", cur):
                    i += 1
                    break
                i += 1
            blk = _push_block(ContentType.CODE, "\n".join(buf),
                              metadata={"lang": fence.group(3).strip()})
            if pending_section_idx is not None:
                pending_block_ids.append(blk.block_id)
            continue

        # ── GFM table (header + separator + rows) ────────────────────
        if _TABLE_ROW.match(line) and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            buf = [line, lines[i + 1]]
            i += 2
            while i < n and _TABLE_ROW.match(lines[i]):
                buf.append(lines[i])
                i += 1
            table_text = "\n".join(buf)
            row_count = max(len(buf) - 2, 0)  # exclude header + separator
            col_count = _count_table_cols(line)
            blk = _push_block(
                ContentType.TABLE, table_text,
                metadata={"rows": row_count, "cols": col_count},
            )
            if pending_section_idx is not None:
                pending_block_ids.append(blk.block_id)
            continue

        # ── Horizontal rule ──────────────────────────────────────────
        if _HR.match(line):
            blk = _push_block(ContentType.HR, line)
            if pending_section_idx is not None:
                pending_block_ids.append(blk.block_id)
            i += 1
            continue

        # ── List block (contiguous list items) ───────────────────────
        if _UL_ITEM.match(line) or _OL_ITEM.match(line):
            buf = [line]
            i += 1
            while i < n and (_UL_ITEM.match(lines[i]) or _OL_ITEM.match(lines[i])
                             or lines[i].strip() == ""):
                # stop if a blank line is followed by a non-list line
                if lines[i].strip() == "":
                    if i + 1 < n and not (_UL_ITEM.match(lines[i + 1])
                                          or _OL_ITEM.match(lines[i + 1])):
                        break
                buf.append(lines[i])
                i += 1
            blk = _push_block(ContentType.LIST, "\n".join(buf).rstrip())
            if pending_section_idx is not None:
                pending_block_ids.append(blk.block_id)
            continue

        # ── Text block (contiguous non-special, non-blank lines) ─────
        buf = [line]
        i += 1
        while i < n:
            cur = lines[i]
            # Blank line ends the paragraph (paragraph-level granularity so
            # the merger can recombine paragraphs deterministically).
            if not cur.strip():
                break
            if (_ATX_HEADING.match(cur) or _FENCE_OPEN.match(cur)
                    or _HR.match(cur)
                    or _UL_ITEM.match(cur) or _OL_ITEM.match(cur)
                    or (_TABLE_ROW.match(cur) and i + 1 < n
                        and _TABLE_SEP.match(lines[i + 1]))):
                break
            buf.append(cur)
            i += 1
        text_content = "\n".join(buf).strip()
        if text_content:
            blk = _push_block(ContentType.TEXT, text_content)
            if pending_section_idx is not None:
                pending_block_ids.append(blk.block_id)
        # blank lines fall through silently

    # Flush any trailing pending blocks into the last open section.
    _flush_pending_into_section()

    return ParsedDocument(
        domain=domain,
        doc_id=doc_id,
        source_file=source_file,
        page=page,
        parser=parser,
        blocks=tuple(blocks),
        sections=tuple(sections),
        metadata={"line_count": n, "block_count": len(blocks),
                  "section_count": len(sections)},
    )


def _count_table_cols(header_row: str) -> int:
    """Count columns in a GFM table header row (``| a | b |`` -> 2)."""
    # Strip leading/trailing pipe, then count non-empty cells.
    inner = header_row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return sum(1 for cell in inner.split("|") if cell.strip() != "") or 1
