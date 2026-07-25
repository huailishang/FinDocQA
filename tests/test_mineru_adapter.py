"""R1 MinerU adapter — synthetic fixture tests.

Tests cover:
1. content_list_v2 path (preferred): multi-page, headings, tables, text.
2. markdown_fallback path (degraded): no content_list, single .md, no page markers.
3. markdown_fallback with page markers (form-feed / <!-- page --> comments).
4. empty / missing MinerU dir: graceful warning, nothing written.
5. document_structure.json records reconstruction_mode and warnings.
6. table HTML -> GFM conversion.
7. corpus adaptation (multiple doc_ids).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from structure.mineru_adapter import adapt_document, adapt_corpus


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def content_list_v2_doc(tmp_path: Path) -> Path:
    """A synthetic MinerU dir with content_list_v2.json (2 pages)."""
    mineru_dir = tmp_path / "mineru" / "doc1"
    mineru_dir.mkdir(parents=True)
    items = [
        {"type": "title", "text": "保险条款", "page_idx": 0, "level": 1},
        {"type": "text", "text": "第一条 本合同适用以下条款。", "page_idx": 0},
        {"type": "table", "table_body": "<table><tr><td>项目</td><td>金额</td></tr><tr><td>保费</td><td>100</td></tr></table>", "page_idx": 0},
        {"type": "title", "text": "免责条款", "page_idx": 1, "level": 2},
        {"type": "text", "text": "第二条 下列情况不予赔付。", "page_idx": 1},
    ]
    (mineru_dir / "content_list_v2.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8"
    )
    return mineru_dir


@pytest.fixture
def markdown_only_doc(tmp_path: Path) -> Path:
    """A synthetic MinerU dir with only a unified Markdown file (no content_list)."""
    mineru_dir = tmp_path / "mineru" / "doc2"
    mineru_dir.mkdir(parents=True)
    md = "# 标题\n\n段落内容。\n\n## 子标题\n\n更多内容。\n"
    (mineru_dir / "full.md").write_text(md, encoding="utf-8")
    return mineru_dir


@pytest.fixture
def markdown_with_ff_doc(tmp_path: Path) -> Path:
    """A unified Markdown with form-feed page markers."""
    mineru_dir = tmp_path / "mineru" / "doc3"
    mineru_dir.mkdir(parents=True)
    md = "# 第一页\n\n内容A。\n\x0c# 第二页\n\n内容B。\n"
    (mineru_dir / "full.md").write_text(md, encoding="utf-8")
    return mineru_dir


@pytest.fixture
def empty_doc(tmp_path: Path) -> Path:
    """An empty MinerU dir (no content_list, no markdown)."""
    mineru_dir = tmp_path / "mineru" / "doc_empty"
    mineru_dir.mkdir(parents=True)
    return mineru_dir


# ── 1. content_list_v2 preferred path ─────────────────────────────────


def test_content_list_v2_rebuilds_pages(content_list_v2_doc: Path, tmp_path: Path):
    target = tmp_path / "target" / "insurance" / "doc1"
    result = adapt_document(content_list_v2_doc, target, domain="insurance", doc_id="doc1")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 2
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    page2 = (target / "page_0002.md").read_text(encoding="utf-8")
    assert "保险条款" in page1
    assert "第一条" in page1
    assert "免责条款" in page2
    assert "第二条" in page2


def test_content_list_v2_table_converted_to_gfm(content_list_v2_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    adapt_document(content_list_v2_doc, target, domain="insurance", doc_id="doc1")
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    # GFM table: header + separator + row.
    assert "| 项目 | 金额 |" in page1
    assert "| --- | --- |" in page1
    assert "| 保费 | 100 |" in page1


def test_content_list_v2_writes_document_structure(content_list_v2_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    result = adapt_document(content_list_v2_doc, target, domain="insurance", doc_id="doc1")
    struct_path = target / "document_structure.json"
    assert struct_path.is_file()
    struct = json.loads(struct_path.read_text(encoding="utf-8"))
    assert struct["reconstruction_mode"] == "content_list_v2"
    assert struct["degraded"] is False
    assert struct["page_count"] == 2
    assert struct["domain"] == "insurance"
    assert struct["doc_id"] == "doc1"
    assert struct["parser"] == "mineru"
    assert "content_list_v2.json" in struct["source_files"][0]


# ── 2. markdown fallback (degraded) ───────────────────────────────────


def test_markdown_fallback_is_degraded(markdown_only_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    result = adapt_document(markdown_only_doc, target, domain="insurance", doc_id="doc2")

    assert result.reconstruction_mode == "markdown_fallback"
    assert result.degraded is True
    assert result.page_count == 1
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    assert "标题" in page1
    assert "段落内容" in page1
    assert any("no page markers" in w for w in result.warnings)


def test_markdown_fallback_document_structure(markdown_only_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    adapt_document(markdown_only_doc, target, domain="insurance", doc_id="doc2")
    struct = json.loads((target / "document_structure.json").read_text(encoding="utf-8"))
    assert struct["reconstruction_mode"] == "markdown_fallback"
    assert struct["degraded"] is True


# ── 3. markdown with form-feed page markers ───────────────────────────


def test_markdown_form_feed_splits_pages(markdown_with_ff_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    result = adapt_document(markdown_with_ff_doc, target, domain="insurance", doc_id="doc3")

    assert result.reconstruction_mode == "markdown_fallback"
    assert result.page_count == 2
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    page2 = (target / "page_0002.md").read_text(encoding="utf-8")
    assert "第一页" in page1
    assert "第二页" in page2


# ── 4. empty / missing dir ────────────────────────────────────────────


def test_empty_dir_records_warning(empty_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    result = adapt_document(empty_doc, target, domain="insurance", doc_id="doc_empty")

    assert result.page_count == 0
    assert result.degraded is True
    assert any("nothing written" in w for w in result.warnings)
    # document_structure.json is still written with the failure recorded.
    struct = json.loads((target / "document_structure.json").read_text(encoding="utf-8"))
    assert struct["page_count"] == 0
    assert struct["degraded"] is True


# ── 5. corpus adaptation ──────────────────────────────────────────────


def test_adapt_corpus_multiple_docs(tmp_path: Path):
    mineru_root = tmp_path / "mineru"
    domain_dir = mineru_root / "insurance"
    for doc_id in ("1", "2"):
        d = domain_dir / doc_id
        d.mkdir(parents=True)
        items = [{"type": "text", "text": f"文档 {doc_id} 内容", "page_idx": 0}]
        (d / "content_list_v2.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    target_root = tmp_path / "target"
    results = adapt_corpus(mineru_root, target_root, domain="insurance")

    assert len(results) == 2
    assert all(r.reconstruction_mode == "content_list_v2" for r in results)
    assert all(r.page_count == 1 for r in results)
    assert {r.doc_id for r in results} == {"1", "2"}


# ── 6. table HTML edge cases ──────────────────────────────────────────


def test_table_html_no_rows_falls_back_to_code_block(tmp_path: Path):
    mineru_dir = tmp_path / "mineru" / "doc_t"
    mineru_dir.mkdir(parents=True)
    items = [{"type": "table", "table_body": "<table></table>", "page_idx": 0}]
    (mineru_dir / "content_list_v2.json").write_text(json.dumps(items), encoding="utf-8")
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="doc_t")
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    # Empty table -> code block fallback with raw HTML.
    assert "```" in page1


# ── 7. deterministic re-adaptation ────────────────────────────────────


def test_re_adaptation_is_deterministic(content_list_v2_doc: Path, tmp_path: Path):
    target1 = tmp_path / "t1"
    target2 = tmp_path / "t2"
    r1 = adapt_document(content_list_v2_doc, target1, domain="insurance", doc_id="doc1")
    r2 = adapt_document(content_list_v2_doc, target2, domain="insurance", doc_id="doc1")
    # Only non-path fields must be equal (target_dir differs by design).
    assert r1.reconstruction_mode == r2.reconstruction_mode
    assert r1.page_count == r2.page_count
    assert r1.degraded == r2.degraded
    assert r1.warnings == r2.warnings
    p1 = (target1 / "page_0001.md").read_text(encoding="utf-8")
    p2 = (target2 / "page_0001.md").read_text(encoding="utf-8")
    assert p1 == p2


# ── 8. MinerU auto/ mode: <doc_id>_content_list_v2.json ───────────────
#
# Real MinerU ``auto/`` runs name outputs after the doc id, e.g.
# ``auto/12345_content_list_v2.json``. The adapter must discover these
# doc-id-prefixed files (at the top level and under ``auto/``) and rebuild
# pages from them, not fall back to Markdown.


@pytest.fixture
def auto_doc_id_content_list(tmp_path: Path) -> Path:
    """A MinerU dir whose content list lives at ``auto/<doc_id>_content_list_v2.json``."""
    mineru_dir = tmp_path / "mineru" / "12345"
    mineru_dir.mkdir(parents=True)
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir()
    items = [
        {"type": "title", "text": "年度报告", "page_idx": 0, "level": 1},
        {"type": "text", "text": "本报告涵盖2024年度经营情况。", "page_idx": 0},
        {"type": "text", "text": "续页内容。", "page_idx": 1},
    ]
    (auto_dir / "12345_content_list_v2.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8"
    )
    return mineru_dir


def test_auto_doc_id_content_list_is_discovered(auto_doc_id_content_list: Path, tmp_path: Path):
    target = tmp_path / "target"
    result = adapt_document(
        auto_doc_id_content_list, target, domain="insurance", doc_id="12345"
    )

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 2
    assert any("12345_content_list_v2.json" in s for s in result.source_files)
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    page2 = (target / "page_0002.md").read_text(encoding="utf-8")
    assert "年度报告" in page1
    assert "续页内容" in page2


def test_top_level_doc_id_content_list_is_discovered(tmp_path: Path):
    """The doc-id-prefixed name is also honoured at the top level (no auto/)."""
    mineru_dir = tmp_path / "mineru" / "67890"
    mineru_dir.mkdir(parents=True)
    items = [{"type": "text", "text": "顶层 content_list。", "page_idx": 0}]
    (mineru_dir / "67890_content_list_v2.json").write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="67890")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.page_count == 1
    assert "顶层 content_list" in (target / "page_0001.md").read_text(encoding="utf-8")


def test_content_list_prefixed_name_preferred_over_bare_in_auto(tmp_path: Path):
    """When both a doc-id-prefixed and a bare content_list exist under auto/,
    the prefixed (real MinerU) file wins."""
    mineru_dir = tmp_path / "mineru" / "111"
    mineru_dir.mkdir(parents=True)
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir()
    (auto_dir / "111_content_list_v2.json").write_text(
        json.dumps([{"type": "text", "text": "PREFIXED", "page_idx": 0}], ensure_ascii=False),
        encoding="utf-8",
    )
    (auto_dir / "content_list_v2.json").write_text(
        json.dumps([{"type": "text", "text": "BARE", "page_idx": 0}], ensure_ascii=False),
        encoding="utf-8",
    )
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="111")

    assert result.reconstruction_mode == "content_list_v2"
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    assert "PREFIXED" in page1
    assert "BARE" not in page1


# ── 9. MinerU auto/ mode: <doc_id>.md markdown fallback ───────────────


@pytest.fixture
def auto_doc_id_markdown(tmp_path: Path) -> Path:
    """A MinerU dir whose only output is ``auto/<doc_id>.md`` (no content list)."""
    mineru_dir = tmp_path / "mineru" / "22222"
    mineru_dir.mkdir(parents=True)
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir()
    md = "# 封面\n\n这是 MinerU auto 模式产出的统一 Markdown。\n"
    (auto_dir / "22222.md").write_text(md, encoding="utf-8")
    return mineru_dir


def test_auto_doc_id_markdown_is_discovered(auto_doc_id_markdown: Path, tmp_path: Path):
    target = tmp_path / "target"
    result = adapt_document(
        auto_doc_id_markdown, target, domain="insurance", doc_id="22222"
    )

    assert result.reconstruction_mode == "markdown_fallback"
    assert result.degraded is True
    assert result.page_count == 1
    assert any("22222.md" in s for s in result.source_files)
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    assert "MinerU auto 模式" in page1


def test_auto_doc_id_markdown_with_form_feed(tmp_path: Path):
    """``auto/<doc_id>.md`` with form-feed markers splits into pages."""
    mineru_dir = tmp_path / "mineru" / "33333"
    mineru_dir.mkdir(parents=True)
    (mineru_dir / "auto").mkdir()
    md = "# 第一页\n\n内容A。\n\x0c# 第二页\n\n内容B。\n"
    (mineru_dir / "auto" / "33333.md").write_text(md, encoding="utf-8")
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="33333")

    assert result.reconstruction_mode == "markdown_fallback"
    assert result.page_count == 2
    assert "第一页" in (target / "page_0001.md").read_text(encoding="utf-8")
    assert "第二页" in (target / "page_0002.md").read_text(encoding="utf-8")


def test_corpus_adapt_with_auto_doc_id_layout(tmp_path: Path):
    """adapt_corpus threads doc_id through so auto/<doc_id>_* layouts resolve."""
    mineru_root = tmp_path / "mineru"
    domain_dir = mineru_root / "insurance"
    for doc_id in ("aa", "bb"):
        d = domain_dir / doc_id
        (d / "auto").mkdir(parents=True)
        items = [{"type": "text", "text": f"文档 {doc_id}", "page_idx": 0}]
        (d / "auto" / f"{doc_id}_content_list_v2.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8"
        )
    target_root = tmp_path / "target"
    results = adapt_corpus(mineru_root, target_root, domain="insurance")

    assert len(results) == 2
    assert all(r.reconstruction_mode == "content_list_v2" for r in results)
    assert {r.doc_id for r in results} == {"aa", "bb"}


# ── 10. nested page-grouped content_list_v2 (real MinerU auto/ layout) ─
#
# Real MinerU 3.4 ``auto/<doc_id>_content_list_v2.json`` is page-grouped: a
# list of page objects ``{"page_idx": N, "blocks": [...]}`` rather than a flat
# list of items each carrying ``page_idx``. The previous adapter rendered each
# page-group as empty text (no ``type``/``text``), produced zero pages, and
# degraded to markdown_fallback. These tests pin the non-degraded multi-page
# rebuild.


@pytest.fixture
def nested_page_grouped_doc(tmp_path: Path) -> Path:
    """A MinerU dir whose content list is nested page-grouped under auto/."""
    mineru_dir = tmp_path / "mineru" / "text01"
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir(parents=True)
    pages = [
        {
            "page_idx": 0,
            "blocks": [
                {"type": "title", "text": "年度报告封面", "level": 1},
                {"type": "text", "text": "本报告涵盖2024年度经营情况。"},
                {
                    "type": "table",
                    "table_body": "<table><tr><td>项目</td><td>金额</td></tr>"
                                  "<tr><td>营收</td><td>100</td></tr></table>",
                },
            ],
        },
        {
            "page_idx": 1,
            "blocks": [
                {"type": "title", "text": "财务摘要", "level": 2},
                {"type": "text", "text": "营业收入同比增长。"},
            ],
        },
    ]
    (auto_dir / "text01_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    return mineru_dir


def test_nested_page_grouped_rebuilds_multiple_pages_not_degraded(
    nested_page_grouped_doc: Path, tmp_path: Path
):
    target = tmp_path / "target"
    result = adapt_document(
        nested_page_grouped_doc, target, domain="financial_reports", doc_id="text01"
    )

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 2
    # Page 1 carries page 0's blocks; page 2 carries page 1's blocks.
    page1 = (target / "page_0001.md").read_text(encoding="utf-8")
    page2 = (target / "page_0002.md").read_text(encoding="utf-8")
    assert "年度报告封面" in page1
    assert "经营情况" in page1
    assert "| 项目 | 金额 |" in page1  # table rendered as GFM
    assert "财务摘要" in page2
    assert "营业收入同比增长" in page2
    assert "年度报告封面" not in page2  # no cross-page leakage


def test_nested_page_grouped_document_structure(nested_page_grouped_doc: Path, tmp_path: Path):
    target = tmp_path / "target"
    adapt_document(
        nested_page_grouped_doc, target, domain="financial_reports", doc_id="text01"
    )
    struct = json.loads((target / "document_structure.json").read_text(encoding="utf-8"))
    assert struct["reconstruction_mode"] == "content_list_v2"
    assert struct["degraded"] is False
    assert struct["page_count"] == 2
    assert "text01_content_list_v2.json" in struct["source_files"][0]


def test_nested_page_grouped_with_explicit_page_type(tmp_path: Path):
    """A container that explicitly declares ``type: "page"`` is still recursed."""
    mineru_dir = tmp_path / "mineru" / "doc_p"
    mineru_dir.mkdir(parents=True)
    pages = [
        {"type": "page", "page_idx": 0, "items": [
            {"type": "text", "text": "第一页内容。"}
        ]},
        {"type": "page", "page_idx": 1, "items": [
            {"type": "text", "text": "第二页内容。"}
        ]},
    ]
    (mineru_dir / "content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="doc_p")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 2
    assert "第一页内容" in (target / "page_0001.md").read_text(encoding="utf-8")
    assert "第二页内容" in (target / "page_0002.md").read_text(encoding="utf-8")


def test_nested_children_inherit_parent_page_idx(tmp_path: Path):
    """Children without their own page_idx inherit the page-group's page_idx."""
    mineru_dir = tmp_path / "mineru" / "doc_i"
    mineru_dir.mkdir(parents=True)
    pages = [
        {"page_idx": 3, "blocks": [
            {"type": "text", "text": "属于第4页(0-based idx 3)的内容。"}
        ]},
    ]
    (mineru_dir / "content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="doc_i")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.page_count == 1
    # page_idx 3 -> 1-based page_0004.md
    assert (target / "page_0004.md").is_file()
    assert "第4页" in (target / "page_0004.md").read_text(encoding="utf-8")


def test_nested_block_with_sublist_is_not_misread_as_page_group(tmp_path: Path):
    """A real block that carries a nested list (e.g. type:list) is rendered as a
    block, not recursed as a page-group — no double counting, no loss."""
    from structure.mineru_adapter import _flatten_content_list

    raw = [
        {"type": "list", "text": "条款列表", "page_idx": 0, "items": ["a", "b"]},
        {"type": "text", "text": "正文。", "page_idx": 0},
    ]
    flat = _flatten_content_list(raw)
    # The list block is kept as-is (not expanded into its string children),
    # and the text block is preserved.
    assert len(flat) == 2
    assert flat[0]["type"] == "list"
    assert flat[1]["text"] == "正文。"


def test_flat_layout_unaffected_by_flattener(tmp_path: Path):
    """A flat content_list (items each with page_idx) passes through unchanged."""
    from structure.mineru_adapter import _flatten_content_list

    raw = [
        {"type": "title", "text": "标题", "page_idx": 0, "level": 1},
        {"type": "text", "text": "段落", "page_idx": 0},
        {"type": "text", "text": "第二页", "page_idx": 1},
    ]
    flat = _flatten_content_list(raw)
    assert flat == raw


# ── 11. real MinerU 3.4 schema: list-of-lists + nested content object ─
#
# Real MinerU 3.4 ``auto/<doc_id>_content_list_v2.json`` is a top-level
# list-of-lists: each outer element is the list of blocks for one page, and the
# outer index IS the page index. Each block nests its text under a ``content``
# object, e.g. ``{"type":"paragraph","content":{"paragraph_content":[
# {"type":"text","content":"..."}]}}``. The previous adapter (a) did not
# recognize list-of-lists as pages and (b) could not read text from the nested
# ``content`` object, so it rendered zero pages and degraded to
# markdown_fallback. These tests pin the real-schema handling.


def _real_block(block_type: str, text: str, **extra) -> dict:
    """Build a block in the real MinerU 3.4 nested-content schema.

    ``text`` is placed under ``content.<type>_content[].content`` so the
    adapter must recurse to find it.
    """
    content_key = f"{block_type}_content"
    return {
        "type": block_type,
        "content": {content_key: [{"type": "text", "content": text}]},
        **extra,
    }


def test_list_of_lists_assigns_outer_index_as_page_idx(tmp_path: Path):
    """Top-level list-of-lists: outer index becomes page_idx, one page_XXXX.md
    per outer list, content_list_v2 mode, NOT degraded."""
    from structure.mineru_adapter import _flatten_content_list

    raw = [
        [_real_block("title", "第一页标题"), _real_block("paragraph", "第一页正文。")],
        [_real_block("title", "第二页标题"), _real_block("paragraph", "第二页正文。")],
        [_real_block("paragraph", "第三页正文。")],
    ]
    flat = _flatten_content_list(raw)
    # Each inner block inherits the outer index as page_idx.
    pages = {_extract_page_idx_pub(b) for b in flat}
    assert pages == {0, 1, 2}
    assert len(flat) == 5  # 2 + 2 + 1 blocks


def _extract_page_idx_pub(item) -> int:
    from structure.mineru_adapter import _extract_page_idx
    return _extract_page_idx(item)


def test_list_of_lists_rebuilds_multiple_pages_not_degraded(tmp_path: Path):
    """End-to-end: list-of-lists content_list_v2 produces N page files in
    content_list_v2 mode (not markdown_fallback), matching the text01 smoke
    shape (3 pages)."""
    mineru_dir = tmp_path / "mineru" / "text01"
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir(parents=True)
    pages = [
        [_real_block("title", "封面标题"), _real_block("paragraph", "封面正文。")],
        [_real_block("title", "第一章"), _real_block("paragraph", "第一章正文。")],
        [_real_block("paragraph", "附录正文。")],
    ]
    (auto_dir / "text01_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(
        mineru_dir, target, domain="financial_contracts", doc_id="text01"
    )

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 3  # text01 smoke expectation
    assert "封面标题" in (target / "page_0001.md").read_text(encoding="utf-8")
    assert "第一章" in (target / "page_0002.md").read_text(encoding="utf-8")
    assert "附录正文" in (target / "page_0003.md").read_text(encoding="utf-8")


def test_list_of_lists_five_pages_matches_annual_report_smoke(tmp_path: Path):
    """Annual report smoke shape: 5 pages, content_list_v2 mode, not degraded."""
    mineru_dir = tmp_path / "mineru" / "annual_cscec_2024_report"
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir(parents=True)
    pages = [
        [_real_block("title", f"第{i}页标题"), _real_block("paragraph", f"第{i}页正文。")]
        for i in range(1, 6)
    ]
    (auto_dir / "annual_cscec_2024_report_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(
        mineru_dir, target,
        domain="financial_reports", doc_id="annual_cscec_2024_report",
    )

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 5  # annual report smoke expectation
    for i in range(1, 6):
        assert f"第{i}页标题" in (target / f"page_{i:04d}.md").read_text(encoding="utf-8")


def test_nested_content_paragraph_text_extracted(tmp_path: Path):
    """A paragraph block whose text is nested under
    content.paragraph_content[].content is extracted and rendered (not dropped
    as empty)."""
    from structure.mineru_adapter import _extract_item_text

    block = _real_block("paragraph", "这是真实的段落文本。")
    assert _extract_item_text(block) == "这是真实的段落文本。"


def test_nested_content_title_text_extracted_as_heading(tmp_path: Path):
    """A title block whose text is nested under content.title_content[].content
    is extracted and rendered as a Markdown heading."""
    block = {
        "type": "title",
        "content": {"title_content": [{"type": "text", "content": "财务摘要"}]},
        "level": 2,
    }
    from structure.mineru_adapter import _extract_item_text, _extract_level, _item_to_markdown

    assert _extract_item_text(block) == "财务摘要"
    assert _extract_level(block) == 2
    md = _item_to_markdown(block, [])
    assert md.startswith("## ")
    assert "财务摘要" in md


def test_nested_content_multi_segment_paragraph_concatenated(tmp_path: Path):
    """A paragraph with multiple text segments under paragraph_content has them
    concatenated, preserving all content."""
    block = {
        "type": "paragraph",
        "content": {
            "paragraph_content": [
                {"type": "text", "content": "第一段。"},
                {"type": "text", "content": "第二段。"},
            ]
        },
    }
    from structure.mineru_adapter import _extract_item_text

    text = _extract_item_text(block)
    assert "第一段。" in text
    assert "第二段。" in text


def test_nested_content_does_not_leak_type_or_bbox(tmp_path: Path):
    """Scalar metadata (type, bbox) is never emitted as text."""
    block = {
        "type": "paragraph",
        "content": {"paragraph_content": [{"type": "text", "content": "仅此文本。"}]},
        "bbox": [10, 20, 30, 40],
    }
    from structure.mineru_adapter import _extract_item_text

    text = _extract_item_text(block)
    assert text == "仅此文本。"
    assert "paragraph" not in text.lower()
    assert "10" not in text  # bbox values do not leak


def test_list_of_lists_block_with_own_page_idx_preserved(tmp_path: Path):
    """If an inner block already carries its own page_idx it is kept (not
    overwritten by the outer index)."""
    from structure.mineru_adapter import _flatten_content_list

    raw = [
        [{"type": "text", "text": "a", "page_idx": 9}],  # outer idx 0, but block says 9
    ]
    flat = _flatten_content_list(raw)
    assert _extract_page_idx_pub(flat[0]) == 9


def test_mixed_flat_and_list_of_lists_not_breaking_flat_path(tmp_path: Path):
    """A flat list (dicts, not lists) is still handled by the flat/dict path —
    the list-of-lists branch only triggers when an outer element is a list."""
    from structure.mineru_adapter import _flatten_content_list

    raw = [
        {"type": "text", "text": "扁平项", "page_idx": 0},
        {"type": "text", "text": "另一项", "page_idx": 1},
    ]
    flat = _flatten_content_list(raw)
    assert flat == raw


# ── 12. image-only pages must be preserved (no page-number gaps) ──────
#
# Real MinerU 3.4 annual-report smoke has an image-only page (page index 3 ->
# page_0004.md) whose single block is an image with no textual Markdown. The
# previous adapter dropped it (rendered "" -> skipped -> page_0004.md never
# written -> page count 4 instead of 5, with a gap 0001..0003, 0005). These
# tests pin image-only page preservation: every source page gets a page_XXXX.md.


def _real_image_block(path: str = "", caption: str = "", **extra) -> dict:
    """Build an image block in the real MinerU 3.4 nested-content schema."""
    content: dict = {}
    if path:
        content["image_source"] = {"path": path}
    if caption:
        content["image_caption"] = [{"type": "text", "content": caption}]
    return {"type": "image", "content": content, **extra}


def test_extract_image_info_from_nested_content():
    """Real MinerU 3.4 image path/caption live under content.image_source.path
    and content.image_caption[].content."""
    from structure.mineru_adapter import _extract_image_info

    block = _real_image_block(path="images/fig1.jpg", caption="图1 营收趋势")
    path, caption = _extract_image_info(block)
    assert path == "images/fig1.jpg"
    assert "图1 营收趋势" in caption


def test_extract_image_info_flat_schema_still_works():
    """Flat img_path / img_caption still extract (back-compat with fixtures)."""
    from structure.mineru_adapter import _extract_image_info

    block = {"type": "image", "img_path": "flat.jpg", "img_caption": ["图A", "图B"]}
    path, caption = _extract_image_info(block)
    assert path == "flat.jpg"
    assert "图A" in caption and "图B" in caption


def test_image_only_page_with_path_renders_image_ref(tmp_path: Path):
    """An image-only page (single image block, no text) writes page_XXXX.md with
    an image reference instead of being dropped."""
    mineru_dir = tmp_path / "mineru" / "doc_img"
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir(parents=True)
    pages = [
        [_real_block("paragraph", "第一页有文字。")],
        [_real_image_block(path="images/p2.png", caption="图2")],  # image-only page
    ]
    (auto_dir / "doc_img_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="doc_img")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 2  # both pages preserved, no gap
    p2 = (target / "page_0002.md").read_text(encoding="utf-8")
    assert "images/p2.png" in p2
    assert "图2" in p2


def test_image_only_page_without_path_still_preserved(tmp_path: Path):
    """Even when the image path cannot be extracted, the page is still written
    with a placeholder so page numbering has no gaps."""
    mineru_dir = tmp_path / "mineru" / "doc_nop"
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir(parents=True)
    # Image block with no path/caption and no nested content.
    pages = [
        [_real_block("paragraph", "第一页。")],
        [{"type": "image"}],  # image-only, no path extractable
        [_real_block("paragraph", "第三页。")],
    ]
    (auto_dir / "doc_nop_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(mineru_dir, target, domain="insurance", doc_id="doc_nop")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 3  # all 3 pages, NO gap at page 2
    assert (target / "page_0001.md").is_file()
    assert (target / "page_0002.md").is_file()  # would be missing pre-fix
    assert (target / "page_0003.md").is_file()
    p2 = (target / "page_0002.md").read_text(encoding="utf-8")
    # Placeholder preserves the page without fabricating content.
    assert "image" in p2.lower() or "no renderable text" in p2.lower()


def test_annual_report_smoke_shape_5_pages_with_image_only_page(tmp_path: Path):
    """Annual report smoke shape: 5 page groups where page 4 (index 3) is
    image-only. Must output page_0001..page_0005 with page_count=5,
    content_list_v2, not degraded — matching the L2 acceptance criteria."""
    mineru_dir = tmp_path / "mineru" / "annual_cscec_2024_report"
    auto_dir = mineru_dir / "auto"
    auto_dir.mkdir(parents=True)
    pages = [
        [_real_block("title", "封面"), _real_block("paragraph", "封面正文"),
         _real_block("paragraph", "第三块")],  # page 0: 3 blocks
        [_real_block("paragraph", f"第{i}段") for i in range(1, 4)],  # page 1: 3 (subset)
        [_real_block("paragraph", "页2a"), _real_block("paragraph", "页2b")],  # page 2: 2
        [_real_image_block(path="images/p4.png", caption="图4")],  # page 3: image-only (1)
        [_real_block("paragraph", "页4a"), _real_block("paragraph", "页4b")],  # page 4: 2
    ]
    (auto_dir / "annual_cscec_2024_report_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "target"
    result = adapt_document(
        mineru_dir, target,
        domain="financial_reports", doc_id="annual_cscec_2024_report",
    )

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 5  # all 5 pages, no gap
    for i in range(1, 6):
        assert (target / f"page_{i:04d}.md").is_file(), f"page_{i:04d}.md missing"
    # The image-only page 4 carries the image reference.
    p4 = (target / "page_0004.md").read_text(encoding="utf-8")
    assert "images/p4.png" in p4
