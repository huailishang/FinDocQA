"""MinerU output adapter — content_list_v2 first, Markdown fallback (R1).

Converts MinerU output into the project's ``page_XXXX.md`` +
``document_structure.json`` contract used by the retrieval pipeline.

Reconstruction strategy (priority order):

1. **content_list_v2** (preferred): MinerU's structured JSON where each item
   carries ``type`` / ``text`` / ``page_idx``. We group items by ``page_idx``
   and rebuild one ``page_XXXX.md`` per page, preserving heading levels,
   tables (as GFM), and text order. This is the high-fidelity path.

2. **Markdown fallback** (degraded): when ``content_list_v2.json`` is absent,
   empty, or unparseable, we fall back to MinerU's unified Markdown file
   (``full.md`` / ``*.md``). Because the unified Markdown loses per-page
   boundaries, we split heuristically on form-feed or ``<!-- page -->``
   markers; if no page markers exist, the entire document becomes a single
   ``page_0001.md`` and ``degraded=True`` is recorded.

In both modes we write ``document_structure.json`` recording
``reconstruction_mode`` and ``warnings`` so downstream consumers know exactly
how the page files were produced.

This module is standard-library only and does NOT touch the live retrieval
pipeline. It is exercised by synthetic fixtures under ``tests/fixtures/mineru/``
and by the CLI in ``scripts/adapt_mineru.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .blocks import ContentType, ParsedDocument
from .parser import parse_markdown_text


# MinerU content_list item "type" values we recognize.
_TEXT_TYPES = {"text", "paragraph", "title", "header"}
_HEADING_HINTS = {"title", "header", "heading"}
_TABLE_TYPES = {"table"}
_IMAGE_TYPES = {"image"}
_EQUATION_TYPES = {"equation", "formula"}
_LIST_TYPES = {"list", "list_item"}

# Form-feed and HTML comment page markers used by some MinerU Markdown dumps.
_PAGE_FF = "\x0c"
_PAGE_COMMENT = re.compile(r"<!--\s*page[:\s]*(\d+)\s*-->", re.IGNORECASE)

# Keys under which MinerU nests per-page block lists in a page-grouped
# content_list_v2 (e.g. ``{"page_idx": 0, "blocks": [...]}``).
_PAGE_CHILD_KEYS = ("items", "blocks", "sub_blocks", "preproc_blocks")
# Explicit block "type" values that mark a page-group container, as opposed to
# a real renderable block that merely happens to carry a nested list.
_CONTAINER_TYPES = {"page", "page_group", "page_info"}


def _page_group_children(entry: Mapping[str, Any]) -> Optional[List[Any]]:
    """If ``entry`` is a page-group container, return its child block list.

    MinerU ``auto/`` content_list_v2 can be **page-grouped**: a list of page
    objects, each shaped like ``{"page_idx": 0, "blocks": [...]}``, rather than
    a flat list of items each carrying ``page_idx``. Without this, a page-group
    object renders as empty text (no ``type`` / ``text``), so the adapter
    writes zero pages and degrades to ``markdown_fallback``.

    A container is recognised when it carries a child list under one of
    ``_PAGE_CHILD_KEYS`` AND its own ``type`` (if any) is a container type
    (``page`` / ``page_group`` / ``page_info``) or absent. A real block that
    happens to nest children (e.g. a ``list``) keeps its normal rendering and
    is NOT recursed, so nothing is double-counted or lost.
    """
    for key in _PAGE_CHILD_KEYS:
        val = entry.get(key)
        if not isinstance(val, list):
            continue
        has_explicit_type = any(
            isinstance(entry.get(k), str) and entry.get(k).strip()
            for k in ("type", "block_type", "block_type_name")
        )
        item_type = _extract_item_type(entry)
        if has_explicit_type and item_type not in _CONTAINER_TYPES:
            return None
        return val
    return None


def _has_page_idx(item: Mapping[str, Any]) -> bool:
    """True if ``item`` carries an explicit non-negative page index."""
    for key in ("page_idx", "page_index", "page"):
        val = item.get(key)
        if isinstance(val, int) and val >= 0:
            return True
    return False


def _flatten_content_list(raw: Any) -> List[Mapping[str, Any]]:
    """Flatten a content_list_v2 payload into a flat list of item dicts.

    Handles two MinerU layouts:

    1. **flat** (legacy / fixtures): ``[{type, text, page_idx}, ...]`` —
       returned as-is.
    2. **page-grouped** (nested, MinerU ``auto/`` mode): a list of page
       objects ``[{page_idx, blocks:[...]}, ...]``. Children are extracted and
       inherit the parent page's ``page_idx`` when they don't carry their own,
       so the existing per-page grouping rebuilds one ``page_XXXX.md`` per
       page instead of degrading to ``markdown_fallback``.
    """
    if not isinstance(raw, list):
        return []
    flat: List[Mapping[str, Any]] = []
    for outer_idx, entry in enumerate(raw):
        # Shape 3 (real MinerU 3.4 auto/): a top-level list-of-lists where
        # each outer element is itself the list of blocks for one page, e.g.
        # ``[[block, block, ...], [block, ...], ...]``. The outer index is the
        # page index; each inner block inherits it (unless it carries its own
        # page_idx). Without this the whole document rendered as zero pages
        # and degraded to markdown_fallback.
        if isinstance(entry, list):
            for child in entry:
                if not isinstance(child, Mapping):
                    continue
                if _has_page_idx(child):
                    flat.append(child)
                else:
                    merged = dict(child)
                    merged.setdefault("page_idx", outer_idx)
                    flat.append(merged)
            continue
        if not isinstance(entry, Mapping):
            continue
        # Shape 2: dict page-group container {page_idx, blocks:[...]}.
        children = _page_group_children(entry)
        if children is None:
            # Shape 1: flat item (with or without its own page_idx).
            flat.append(entry)
            continue
        parent_page = _extract_page_idx(entry)
        for child in children:
            if not isinstance(child, Mapping):
                continue
            if _has_page_idx(child):
                flat.append(child)
            else:
                merged = dict(child)
                merged.setdefault("page_idx", parent_page)
                flat.append(merged)
    return flat


@dataclass(frozen=True)
class AdaptationResult:
    """Outcome of adapting one MinerU document.

    Attributes:
        domain: target domain.
        doc_id: target doc id.
        target_dir: where page files + document_structure.json were written.
        reconstruction_mode: ``content_list_v2`` or ``markdown_fallback``.
        page_count: number of page_XXXX.md files written.
        degraded: True when the fallback path was used.
        warnings: human-readable warnings encountered during adaptation.
        source_files: MinerU source files that were consumed.
    """

    domain: str
    doc_id: str
    target_dir: str
    reconstruction_mode: str
    page_count: int
    degraded: bool
    warnings: Tuple[str, ...]
    source_files: Tuple[str, ...]


def _content_list_candidate_names(doc_id: Optional[str]) -> Tuple[str, ...]:
    """Build the ordered list of content_list filenames to probe.

    MinerU ``auto/`` mode names files ``<doc_id>_content_list_v2.json``; older
    dumps and our fixtures use the bare ``content_list_v2.json``. When a
    ``doc_id`` is known we probe the prefixed name first, then fall back to the
    bare name so both layouts resolve.
    """
    bare = ("content_list_v2.json", "content_list.json")
    if doc_id:
        return (f"{doc_id}_content_list_v2.json", f"{doc_id}_content_list.json") + bare
    return bare


def _find_content_list(
    mineru_dir: Path, doc_id: Optional[str] = None
) -> Optional[Path]:
    """Locate content_list_v2.json (or legacy content_list.json).

    Probes the bare name and, when ``doc_id`` is provided, the
    ``<doc_id>_content_list_v2.json`` name — at the top level and one level
    down under ``auto/`` / ``mineru_raw/`` / ``raw/`` (the subdirectories
    MinerU nests its output under).
    """
    names = _content_list_candidate_names(doc_id)
    for name in names:
        p = mineru_dir / name
        if p.is_file():
            return p
    # Search one level down (MinerU sometimes nests under auto/, mineru_raw/).
    for sub in ("auto", "mineru_raw", "raw"):
        for name in names:
            p = mineru_dir / sub / name
            if p.is_file():
                return p
    return None


def _markdown_candidate_names(doc_id: Optional[str]) -> Tuple[str, ...]:
    """Build the ordered list of Markdown filenames to probe.

    MinerU ``auto/`` mode writes ``<doc_id>.md``; older dumps use ``full.md``
    and friends. When a ``doc_id`` is known we probe ``<doc_id>.md`` first.
    """
    bare = ("full.md", "full_markdown.md", "result.md", "output.md")
    if doc_id:
        return (f"{doc_id}.md",) + bare
    return bare


def _find_markdown(
    mineru_dir: Path, doc_id: Optional[str] = None
) -> Optional[Path]:
    """Locate the unified Markdown file produced by MinerU.

    Probes the bare names and, when ``doc_id`` is provided, ``<doc_id>.md`` —
    at the top level and one level down under ``auto/`` / ``mineru_raw/`` /
    ``raw/`` / ``markdown/``.
    """
    names = _markdown_candidate_names(doc_id)
    for name in names:
        p = mineru_dir / name
        if p.is_file():
            return p
    # Search one level down.
    for sub in ("auto", "mineru_raw", "raw", "markdown"):
        for name in names:
            p = mineru_dir / sub / name
            if p.is_file():
                return p
    # Last resort: any .md file at top level.
    md_files = sorted(mineru_dir.glob("*.md"))
    if md_files:
        return md_files[0]
    return None


def _extract_nested_text(obj: Any) -> str:
    """Recursively extract leaf text from a real MinerU 3.4 content object.

    Real MinerU blocks nest their text under a ``content`` mapping, e.g.::

        {
          "type": "paragraph",
          "content": {
            "paragraph_content": [
              {"type": "text", "content": "实际文本"}
            ]
          }
        }

    The top-level block has no string ``text`` field, so the flat
    ``_extract_item_text`` path returns "" and the page is dropped. This helper
    walks the nested ``content`` tree and concatenates every leaf string found
    under a known text field key (``text`` / ``content`` / ``text_content``).

    Rules:
    - A scalar string is a leaf ONLY when reached via a known text-field key
      (``text``/``content``/``text_content``). Scalar metadata like
      ``type:"paragraph"`` / ``bbox`` / ``level`` is never emitted.
    - Only dict / list children are recursed; scalars are ignored unless they
      are a recognized text-field leaf, so ``type``/``bbox``/``page_idx``
      never leak into the rendered page text.
    """
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, Mapping):
        # Leaf text: known text fields with a string value.
        for key in ("text", "text_content"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        content_val = obj.get("content")
        if isinstance(content_val, str) and content_val.strip():
            return content_val.strip()
        # Recurse into structured children only (dict / list), never into
        # scalar metadata (type, bbox, page_idx, level, ...).
        parts: List[str] = []
        for val in obj.values():
            if isinstance(val, (Mapping, list, tuple)):
                t = _extract_nested_text(val)
                if t:
                    parts.append(t)
        return " ".join(p for p in parts if p)
    if isinstance(obj, (list, tuple)):
        parts: List[str] = []
        for item in obj:
            t = _extract_nested_text(item)
            if t:
                parts.append(t)
        return " ".join(p for p in parts if p)
    return ""


def _extract_item_text(item: Mapping[str, Any]) -> str:
    """Extract text content from a content_list item, tolerating field-name variants.

    Handles three layouts:
    1. Flat string fields (``text`` / ``content`` / ``text_content``).
    2. Real MinerU 3.4 nested ``content`` object (paragraph_content /
       title_content / ... ), via ``_extract_nested_text``.
    """
    for key in ("text", "content", "text_content"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Real MinerU 3.4 nests text under a `content` object
    # (e.g. content.paragraph_content[].content). Recurse to extract it so
    # pages are not dropped as empty.
    nested = _extract_nested_text(item)
    if nested.strip():
        return nested
    return ""


def _extract_item_type(item: Mapping[str, Any]) -> str:
    """Extract the block type, tolerating field-name variants."""
    for key in ("type", "block_type", "block_type_name"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    return "text"


def _extract_page_idx(item: Mapping[str, Any]) -> int:
    """Extract the page index (0-based in MinerU), defaulting to 0."""
    for key in ("page_idx", "page_index", "page"):
        val = item.get(key)
        if isinstance(val, int) and val >= 0:
            return val
    return 0


def _extract_level(item: Mapping[str, Any]) -> Optional[int]:
    """Extract heading level if the item is a heading."""
    for key in ("level", "heading_level"):
        val = item.get(key)
        if isinstance(val, int) and 1 <= val <= 6:
            return val
    return None


def _format_table_html(html: str) -> str:
    """Convert an HTML table to a best-effort GFM table.

    MinerU tables are often HTML. We do a lightweight conversion: extract
    rows/cells and rebuild a pipe table. If conversion fails, we keep the raw
    HTML inside a fenced block so the content is at least preserved.
    """
    if not html or not html.strip():
        return ""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL)
    if not rows:
        return f"```\n{html.strip()}\n```"
    table_rows: List[List[str]] = []
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.IGNORECASE | re.DOTALL)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if cells:
            table_rows.append(cells)
    if not table_rows:
        return f"```\n{html.strip()}\n```"
    # Normalize column count to the widest row.
    max_cols = max(len(r) for r in table_rows)
    for r in table_rows:
        while len(r) < max_cols:
            r.append("")
    lines = []
    lines.append("| " + " | ".join(table_rows[0]) + " |")
    lines.append("| " + " | ".join("---" for _ in table_rows[0]) + " |")
    for r in table_rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _extract_image_info(item: Mapping[str, Any]) -> Tuple[str, str]:
    """Extract (img_path, caption) from an image block, tolerating schemas.

    Handles two layouts:
    1. Flat (fixtures / older dumps): ``img_path`` / ``img_caption`` at the top
       level.
    2. Real MinerU 3.4 nested ``content`` object, e.g.::

           {"type": "image",
            "content": {"image_source": {"path": "images/x.jpg"},
                        "image_caption": [{"type":"text","content":"图1 ..."}]}}

       The path lives under ``content.image_source.path`` and the caption under
       ``content.image_caption[].content`` (concatenated). Without this, an
       image-only page rendered no Markdown text, was dropped, and the page
       number gap broke page traceability (annual report page 4 missing).
    """
    # Flat schema.
    img_path = str(item.get("img_path", "") or "").strip()
    caption_raw = item.get("img_caption")
    if caption_raw is None:
        caption_raw = item.get("caption")
    if isinstance(caption_raw, list):
        caption_text = " ".join(str(c) for c in caption_raw if c).strip()
    elif caption_raw is not None:
        caption_text = str(caption_raw).strip()
    else:
        caption_text = ""

    # Nested real MinerU 3.4 schema — only fill in when the flat fields are
    # absent so we never overwrite an explicit flat value.
    content = item.get("content")
    if isinstance(content, Mapping):
        if not img_path:
            src = content.get("image_source")
            if isinstance(src, Mapping):
                for key in ("path", "img_path", "src", "url"):
                    val = src.get(key)
                    if isinstance(val, str) and val.strip():
                        img_path = val.strip()
                        break
            elif isinstance(content.get("image_source"), str):
                img_path = content["image_source"].strip()
        if not caption_text:
            cap = content.get("image_caption")
            if isinstance(cap, list):
                # Each caption segment may itself be {type, content:{...}} or
                # {type, content:"..."}; reuse the nested-text extractor.
                caption_text = _extract_nested_text(cap).strip()
            elif isinstance(cap, Mapping):
                caption_text = _extract_nested_text(cap).strip()
            elif isinstance(cap, str):
                caption_text = cap.strip()
    return img_path, caption_text


def _item_to_markdown(item: Mapping[str, Any], warnings: List[str]) -> str:
    """Render one content_list item as a Markdown fragment."""
    item_type = _extract_item_type(item)
    text = _extract_item_text(item)

    if item_type in _TABLE_TYPES:
        content = item.get("content") if isinstance(item.get("content"), Mapping) else {}
        html = (
            item.get("table_body")
            or item.get("html")
            or content.get("table_body")
            or content.get("html")
            or ""
        )
        caption = _extract_nested_text(
            item.get("table_caption")
            or item.get("caption")
            or content.get("table_caption")
            or content.get("caption")
            or ""
        )
        footnote = _extract_nested_text(
            item.get("table_footnote")
            or item.get("footnote")
            or content.get("table_footnote")
            or content.get("footnote")
            or ""
        )
        table_md = _format_table_html(str(html))
        if not table_md and text:
            table_md = text
        parts = []
        if caption:
            parts.append(f"*{caption}*")
        if table_md:
            parts.append(table_md)
        if footnote:
            parts.append(f"*{footnote}*")
        return "\n\n".join(parts)

    if item_type in _EQUATION_TYPES:
        # Preserve LaTeX in a fenced block so it is not lost.
        return f"$$\n{text}\n$$"

    if item_type in _IMAGE_TYPES:
        img_path, caption_text = _extract_image_info(item)
        parts: List[str] = []
        if img_path:
            parts.append(f"![{caption_text}]({img_path})")
        if caption_text:
            parts.append(f"*{caption_text}*")
        # An image-only block with no path/caption still yields a minimal
        # placeholder so the page is preserved (never return "" here — the
        # caller drops empty fragments and would lose the page number).
        if not parts:
            parts.append(f"<!-- image block (no path/caption extracted) -->")
        return "\n".join(parts)

    if item_type in _HEADING_HINTS:
        level = _extract_level(item) or 1
        return f"{'#' * level} {text}"

    # Default: plain text paragraph. Preserve as-is.
    return text


def _group_items_by_page(
    items: Sequence[Mapping[str, Any]],
    warnings: List[str],
) -> Dict[int, List[str]]:
    """Group content_list items by page_idx and render each to Markdown.

    A page is registered as soon as ANY block claims it, even if that block
    renders no textual Markdown (e.g. an image-only page whose image path
    could not be extracted). Previously such blocks were silently dropped,
    which left gaps in the page numbering (e.g. page_0001..0003, 0005) and
    broke page traceability. Now an image-only page with no renderable text
    still gets a placeholder fragment so ``page_XXXX.md`` is written.
    """
    by_page: Dict[int, List[str]] = {}
    for idx, item in enumerate(items):
        if not isinstance(item, Mapping):
            warnings.append(f"item {idx}: not a dict, skipped")
            continue
        page_idx = _extract_page_idx(item)
        md = _item_to_markdown(item, warnings)
        if md and md.strip():
            by_page.setdefault(page_idx, []).append(md)
        else:
            # Register the page even with no textual content so the page file
            # is written and page numbering has no gaps. Image-only pages
            # (whose image path extraction failed) are the main case.
            by_page.setdefault(page_idx, []).append(
                f"<!-- page {page_idx}: no renderable text (image-only or empty blocks) -->"
            )
    if not by_page:
        warnings.append("content_list_v2: no renderable items found")
    return by_page


def _write_page_file(
    target_dir: Path,
    page_number: int,
    fragments: Sequence[str],
) -> Path:
    """Write page_XXXX.md (1-based page number) with the given fragments."""
    page_path = target_dir / f"page_{page_number:04d}.md"
    content = "\n\n".join(f.strip() for f in fragments if f.strip())
    page_path.write_text(content + "\n", encoding="utf-8")
    return page_path


def _write_document_structure(
    target_dir: Path,
    *,
    domain: str,
    doc_id: str,
    reconstruction_mode: str,
    degraded: bool,
    page_count: int,
    warnings: Sequence[str],
    source_files: Sequence[str],
) -> Path:
    """Write document_structure.json with reconstruction metadata."""
    struct_path = target_dir / "document_structure.json"
    structure: Dict[str, Any] = {
        "domain": domain,
        "doc_id": doc_id,
        "parser": "mineru",
        "reconstruction_mode": reconstruction_mode,
        "degraded": degraded,
        "page_count": page_count,
        "warnings": list(warnings),
        "source_files": list(source_files),
    }
    struct_path.write_text(
        json.dumps(structure, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return struct_path


def _split_markdown_by_pages(md_text: str) -> List[Tuple[int, str]]:
    """Split unified Markdown into (page_number, text) pairs.

    Tries form-feed first, then ``<!-- page N -->`` comments. If neither is
    found, returns the whole text as page 1 (and the caller marks degraded).
    """
    if _PAGE_FF in md_text:
        chunks = md_text.split(_PAGE_FF)
        return [(i + 1, chunk.strip()) for i, chunk in enumerate(chunks) if chunk.strip()]
    comment_matches = list(_PAGE_COMMENT.finditer(md_text))
    if comment_matches:
        pages: List[Tuple[int, str]] = []
        for i, m in enumerate(comment_matches):
            page_num = int(m.group(1))
            start = m.end()
            end = comment_matches[i + 1].start() if i + 1 < len(comment_matches) else len(md_text)
            chunk = md_text[start:end].strip()
            if chunk:
                pages.append((page_num, chunk))
        if pages:
            return pages
    # No page markers: entire document is one page.
    return [(1, md_text.strip())] if md_text.strip() else []


def adapt_document(
    mineru_dir: Path,
    target_dir: Path,
    *,
    domain: str,
    doc_id: str,
) -> AdaptationResult:
    """Adapt one MinerU document directory into the page file contract.

    Args:
        mineru_dir: directory containing MinerU output (content_list_v2.json
            and/or *.md).
        target_dir: where to write ``page_XXXX.md`` + ``document_structure.json``.
            Created if it does not exist.
        domain: target domain (e.g. ``insurance``).
        doc_id: target doc id.

    Returns:
        AdaptationResult describing what was written and how.
    """
    mineru_dir = Path(mineru_dir)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    source_files: List[str] = []
    page_count = 0
    degraded = False
    mode = "markdown_fallback"

    content_list_path = _find_content_list(mineru_dir, doc_id)
    if content_list_path is not None:
        source_files.append(str(content_list_path))
        try:
            items = json.loads(content_list_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            warnings.append(f"content_list_v2.json unparseable: {exc}; falling back to Markdown")
            items = None
        else:
            if not isinstance(items, list):
                warnings.append(f"content_list_v2.json is not a list (got {type(items).__name__}); falling back")
                items = None
            elif not items:
                warnings.append("content_list_v2.json is empty; falling back to Markdown")
                items = None
            else:
                # Normalize page-grouped (nested) layouts into a flat item
                # list so per-page grouping rebuilds one page_XXXX.md per
                # page. Flat layouts pass through unchanged.
                items = _flatten_content_list(items)

        if items:
            mode = "content_list_v2"
            by_page = _group_items_by_page(items, warnings)
            for page_idx in sorted(by_page):
                page_number = page_idx + 1  # MinerU is 0-based; our contract is 1-based.
                _write_page_file(target_dir, page_number, by_page[page_idx])
                page_count += 1

    if page_count == 0:
        # Fallback to unified Markdown.
        degraded = True
        mode = "markdown_fallback"
        md_path = _find_markdown(mineru_dir, doc_id)
        if md_path is None:
            warnings.append("no content_list_v2.json and no Markdown file found; nothing written")
        else:
            source_files.append(str(md_path))
            md_text = md_path.read_text(encoding="utf-8", errors="ignore")
            pages = _split_markdown_by_pages(md_text)
            if len(pages) <= 1 and _PAGE_FF not in md_text and not _PAGE_COMMENT.search(md_text):
                warnings.append("Markdown has no page markers; entire document written as page_0001.md (degraded)")
            for page_num, chunk in pages:
                _write_page_file(target_dir, page_num, [chunk])
                page_count += 1

    _write_document_structure(
        target_dir,
        domain=domain,
        doc_id=doc_id,
        reconstruction_mode=mode,
        degraded=degraded,
        page_count=page_count,
        warnings=warnings,
        source_files=source_files,
    )

    return AdaptationResult(
        domain=domain,
        doc_id=doc_id,
        target_dir=str(target_dir),
        reconstruction_mode=mode,
        page_count=page_count,
        degraded=degraded,
        warnings=tuple(warnings),
        source_files=tuple(source_files),
    )


# ── resumable manifest (Lane A) ───────────────────────────────────────
#
# A corpus adaptation can be interrupted (long run, partial MinerU output).
# The manifest records, per doc_id, the source files consumed and their
# SHA-256 signatures plus the adaptation outcome. On re-run with ``resume=True``,
# a doc whose sources are unchanged (same paths, same hashes) and whose prior
# status is ``completed`` is skipped — its existing ``page_XXXX.md`` output is
# reused and a result rebuilt from the manifest is returned, so callers see a
# full result list without re-adapting finished work.
#
# The manifest is a single JSON file at ``<target_root>/<domain>/_adapt_manifest.json``.
# It is the only resume state; there is no per-file lock or database.

_MANIFEST_VERSION = 1


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _manifest_path(target_root: Path, domain: str) -> Path:
    return Path(target_root) / domain / "_adapt_manifest.json"


def _load_manifest(target_root: Path, domain: str) -> Dict[str, Any]:
    p = _manifest_path(target_root, domain)
    if not p.is_file():
        return {"version": _MANIFEST_VERSION, "domain": domain, "docs": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": _MANIFEST_VERSION, "domain": domain, "docs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("docs"), dict):
        return {"version": _MANIFEST_VERSION, "domain": domain, "docs": {}}
    return data


def _save_manifest(target_root: Path, domain: str, manifest: Mapping[str, Any]) -> None:
    p = _manifest_path(target_root, domain)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source_signature(source_files: Sequence[str]) -> Dict[str, str]:
    """Map each source file path to its SHA-256, skipping missing files.

    Missing source files are recorded with an empty string so a later re-run
    detects the change (file gone) rather than silently treating the doc as
    unchanged.
    """
    sig: Dict[str, str] = {}
    for s in source_files:
        p = Path(s)
        if p.is_file():
            sig[s] = _sha256_file(p)
        else:
            sig[s] = ""
    return sig


def _sources_unchanged(prev: Mapping[str, str], current: Mapping[str, str]) -> bool:
    """True iff the source signature is identical (same paths, same hashes)."""
    if set(prev.keys()) != set(current.keys()):
        return False
    return all(prev[k] == current[k] for k in prev)


def _rebuild_result_from_manifest(
    dst: Path, *, domain: str, doc_id: str, entry: Mapping[str, Any]
) -> AdaptationResult:
    """Rebuild an AdaptationResult from a manifest entry (skip path)."""
    return AdaptationResult(
        domain=domain,
        doc_id=doc_id,
        target_dir=str(dst),
        reconstruction_mode=str(entry.get("reconstruction_mode", "content_list_v2")),
        page_count=int(entry.get("page_count", 0) or 0),
        degraded=bool(entry.get("degraded", False)),
        warnings=tuple(entry.get("warnings", []) or []),
        source_files=tuple(entry.get("source_files", []) or []),
    )


def adapt_corpus(
    mineru_root: Path,
    target_root: Path,
    *,
    domain: str,
    doc_ids: Optional[Sequence[str]] = None,
    resume: bool = False,
) -> List[AdaptationResult]:
    """Adapt all documents under ``mineru_root/<domain>/`` into ``target_root/<domain>/``.

    Args:
        mineru_root: root containing ``<domain>/<doc_id>/`` directories.
        target_root: root where adapted ``<domain>/<doc_id>/`` will be written.
        domain: domain to adapt.
        doc_ids: optional explicit list of doc_ids; defaults to all subdirectories
            under ``mineru_root/<domain>/``.
        resume: when True, skip documents whose source files are unchanged since
            the last successful adaptation (recorded in
            ``<target_root>/<domain>/_adapt_manifest.json``). Their existing
            ``page_XXXX.md`` output is reused. When False (default) every
            document is re-adapted from scratch and the manifest is overwritten.

    Returns:
        one AdaptationResult per document. Skipped (resumed) documents return a
        result rebuilt from the manifest with the same non-path fields.
    """
    mineru_root = Path(mineru_root)
    target_root = Path(target_root)
    domain_dir = mineru_root / domain
    if not domain_dir.is_dir():
        return []
    if doc_ids is None:
        doc_ids = sorted(
            p.name for p in domain_dir.iterdir() if p.is_dir() and not p.name.startswith("_")
        )

    manifest = _load_manifest(target_root, domain) if resume else {
        "version": _MANIFEST_VERSION, "domain": domain, "docs": {}
    }
    docs_meta: Dict[str, Any] = manifest.setdefault("docs", {})

    results: List[AdaptationResult] = []
    for doc_id in doc_ids:
        src = domain_dir / doc_id
        dst = target_root / domain / doc_id

        prev = docs_meta.get(doc_id)
        if resume and isinstance(prev, Mapping) and prev.get("status") == "completed":
            # Probe current source signature against the recorded one. We can
            # only build it after adapting (adapt_document discovers the source
            # files), so for the skip check we re-probe the *expected* source
            # files recorded in the manifest. If any are missing or changed we
            # fall through to a full re-adapt.
            prev_sources = prev.get("source_signature", {}) or {}
            current_sig = _source_signature(list(prev_sources.keys()))
            if _sources_unchanged(prev_sources, current_sig) and dst.is_dir():
                results.append(_rebuild_result_from_manifest(
                    dst, domain=domain, doc_id=doc_id, entry=prev
                ))
                continue

        result = adapt_document(src, dst, domain=domain, doc_id=doc_id)
        results.append(result)

        # Record outcome. A doc with page_count==0 and a "nothing written"
        # warning is still recorded as completed (it was attempted); a missing
        # mineru_dir is recorded so resume does not loop on it. The signature
        # is built from the actual sources adapt_document discovered.
        docs_meta[doc_id] = {
            "status": "completed",
            "reconstruction_mode": result.reconstruction_mode,
            "page_count": result.page_count,
            "degraded": result.degraded,
            "warnings": list(result.warnings),
            "source_files": list(result.source_files),
            "source_signature": _source_signature(result.source_files),
            "adapted_at": datetime.now().isoformat(timespec="seconds"),
        }

    if resume:
        _save_manifest(target_root, domain, manifest)
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: adapt one MinerU document or a whole domain."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Adapt MinerU output into page_XXXX.md + document_structure.json."
    )
    parser.add_argument("mineru_dir", help="MinerU output directory for one document, or a domain root with --corpus.")
    parser.add_argument("target_dir", help="Target directory for adapted output.")
    parser.add_argument("--domain", default="insurance", help="Domain label.")
    parser.add_argument("--doc-id", default=None, help="Doc id (required for single-doc mode).")
    parser.add_argument("--corpus", action="store_true", help="Treat mineru_dir as a domain root and adapt all doc_ids.")
    parser.add_argument("--resume", action="store_true", help="Skip documents whose sources are unchanged since the last successful adaptation (corpus mode only).")
    args = parser.parse_args(argv)

    if args.corpus:
        results = adapt_corpus(
            Path(args.mineru_dir), Path(args.target_dir),
            domain=args.domain, resume=args.resume,
        )
        for r in results:
            print(f"  {r.doc_id}: mode={r.reconstruction_mode}, pages={r.page_count}, degraded={r.degraded}")
        print(f"adapted {len(results)} documents")
        if args.resume:
            print(f"resume manifest: {_manifest_path(Path(args.target_dir), args.domain)}")
    else:
        if not args.doc_id:
            parser.error("--doc-id is required in single-document mode")
        r = adapt_document(
            Path(args.mineru_dir), Path(args.target_dir),
            domain=args.domain, doc_id=args.doc_id,
        )
        print(f"mode={r.reconstruction_mode}")
        print(f"pages={r.page_count}")
        print(f"degraded={r.degraded}")
        if r.warnings:
            print("warnings:")
            for w in r.warnings:
                print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
