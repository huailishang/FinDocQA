"""Focused tests for BB-P0-04B read-only corpus quality audit."""

from __future__ import annotations

import json
from pathlib import Path

from structure.quality_audit import (
    RULESET_VERSION,
    THRESHOLDS,
    _page_findings,
    audit_corpus,
    audit_document,
    corpus_integrity_snapshot,
)


def _text_block(text: str, *, y: int = 100) -> dict:
    return {
        "type": "paragraph",
        "content": {"paragraph_content": [{"type": "text", "content": text}]},
        "bbox": [100, y, 800, y + 20],
    }


def _title_block(text: str, level: int, *, y: int = 80) -> dict:
    return {
        "type": "title",
        "content": {"title_content": [{"type": "text", "content": text}], "level": level},
        "bbox": [100, y, 800, y + 20],
    }


def _image_block(*, y: int = 200, path: str | None = None) -> dict:
    content = {"image_caption": [], "image_footnote": []}
    if path is not None:
        content["image_source"] = {"path": path}
    return {
        "type": "image",
        "content": content,
        "bbox": [100, y, 800, y + 200],
    }


def _table_block(html: str | None, *, y: int = 200) -> dict:
    content = {"table_caption": [], "table_footnote": []}
    if html is not None:
        content["html"] = html
    return {"type": "table", "content": content, "bbox": [100, y, 800, y + 160]}


def _formula_block(math: str | None, *, y: int = 200) -> dict:
    content = {"math_type": "latex"}
    if math is not None:
        content["math_content"] = math
    return {"type": "equation_interline", "content": content, "bbox": [100, y, 800, y + 40]}


def _write_doc(
    tmp_path: Path,
    *,
    domain: str = "insurance",
    doc_id: str = "doc1",
    pages: list[list[dict]],
    markdown_pages: list[str] | None = None,
) -> tuple[Path, Path]:
    root = tmp_path / "corpus"
    doc_dir = root / domain / doc_id
    doc_dir.mkdir(parents=True)
    source = tmp_path / "mineru" / domain / doc_id / "auto" / f"{doc_id}_content_list_v2.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    if markdown_pages is None:
        markdown_pages = [
            "\n".join(
                value
                for block in page
                for value in _strings(block.get("content"))
                if not value.startswith("images/")
            )
            or "<!-- page: no renderable text -->"
            for page in pages
        ]
    for idx, text in enumerate(markdown_pages, start=1):
        (doc_dir / f"page_{idx:04d}.md").write_text(text, encoding="utf-8")
    (doc_dir / "document_structure.json").write_text(
        json.dumps(
            {
                "domain": domain,
                "doc_id": doc_id,
                "parser": "mineru",
                "reconstruction_mode": "content_list_v2",
                "degraded": False,
                "page_count": len(pages),
                "warnings": [],
                "source_files": [str(source)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root, doc_dir


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _page_rules(doc, page_number: int, classification: str) -> set[str]:
    page = next(p for p in doc.high_risk_pages if p.page_number == page_number)
    values = page.confirmed_anomalies if classification == "confirmed" else page.review_flags
    return {f.rule_id for f in values}


def _inspect_page(
    blocks: list[dict],
    *,
    previous_has_table: bool = False,
    next_has_table: bool = False,
    previous_title_level: int | None = None,
):
    text = "\n".join(
        value
        for block in blocks
        for value in _strings(block.get("content"))
        if not value.startswith("images/")
    )
    page, _ = _page_findings(
        domain="insurance",
        doc_id="synthetic",
        page_number=1,
        page_path=Path("synthetic/page_0001.md"),
        page_text=text,
        blocks=blocks,
        source_file=None,
        previous_has_table=previous_has_table,
        next_has_table=next_has_table,
        previous_title_level=previous_title_level,
    )
    return page


def test_ruleset_and_thresholds_are_explicit():
    assert RULESET_VERSION == "bb_p0_04b_v1_20260722"
    assert THRESHOLDS["low_text_chars"] == 30
    assert THRESHOLDS["numeric_table_min_numbers"] == 4
    assert THRESHOLDS["reading_order_inversion_ratio"] == 0.20


def test_low_text_and_scan_threshold_boundary(tmp_path: Path):
    # 29 chars + visual => review risks. 40 chars + visual => neither low-text
    # nor scan-like at the strict '< threshold' boundary.
    page1 = _inspect_page([_text_block("a" * 29), _image_block()])
    page2 = _inspect_page([_text_block("b" * 40), _image_block()])
    assert {f.rule_id for f in page1.review_flags} >= {"very_low_text_density", "scan_like_page"}
    assert "very_low_text_density" not in {f.rule_id for f in page2.review_flags}
    assert "scan_like_page" not in {f.rule_id for f in page2.review_flags}


def test_table_missing_html_is_confirmed_not_heuristic(tmp_path: Path):
    pages = [[_text_block("正文" * 30), _table_block(None)]]
    _, doc_dir = _write_doc(tmp_path, pages=pages)
    doc = audit_document(doc_dir, domain="insurance", project_root=tmp_path)
    assert "table_machine_structure_missing" in _page_rules(doc, 1, "confirmed")
    assert "table_machine_structure_missing" not in _page_rules(doc, 1, "review")


def test_numeric_table_unit_unclear_boundary_and_unit_suppression(tmp_path: Path):
    no_unit = "<table><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr><tr><td>C</td><td>3</td></tr><tr><td>D</td><td>4</td></tr></table>"
    with_unit = "<table><tr><td>单位：万元</td><td>1</td></tr><tr><td>B</td><td>2</td></tr><tr><td>C</td><td>3</td></tr><tr><td>D</td><td>4</td></tr></table>"
    page1 = _inspect_page([_text_block("正文" * 30), _table_block(no_unit)])
    page2 = _inspect_page([_text_block("正文" * 30), _table_block(with_unit)])
    assert "numeric_table_unit_unclear" in {f.rule_id for f in page1.review_flags}
    assert "numeric_table_unit_unclear" not in {f.rule_id for f in page2.review_flags}


def test_adjacent_tables_are_review_only_cross_page_candidates(tmp_path: Path):
    html = "<table><tr><td>单位：万元</td><td>值</td></tr><tr><td>A</td><td>1</td></tr></table>"
    page = _inspect_page(
        [_text_block("正文" * 30), _table_block(html)],
        next_has_table=True,
    )
    assert "cross_page_table_candidate" in {f.rule_id for f in page.review_flags}
    assert "cross_page_table_candidate" not in {f.rule_id for f in page.confirmed_anomalies}


def test_formula_without_math_content_is_confirmed(tmp_path: Path):
    pages = [[_text_block("正文" * 30), _formula_block(None)]]
    _, doc_dir = _write_doc(tmp_path, pages=pages)
    doc = audit_document(doc_dir, domain="insurance", project_root=tmp_path)
    assert "formula_machine_content_missing" in _page_rules(doc, 1, "confirmed")


def test_reading_order_inversion_is_review_only(tmp_path: Path):
    blocks = [
        _text_block("A" * 20, y=100),
        _text_block("B" * 20, y=220),
        _text_block("C" * 20, y=140),
        _text_block("D" * 20, y=300),
        _text_block("E" * 20, y=160),
    ]
    page = _inspect_page(blocks)
    assert "reading_order_suspect" in {f.rule_id for f in page.review_flags}
    assert "reading_order_suspect" not in {f.rule_id for f in page.confirmed_anomalies}


def test_heading_jump_is_review_only(tmp_path: Path):
    page = _inspect_page(
        [_title_block("一级", 1), _text_block("正文" * 30), _title_block("三级", 3, y=300)]
    )
    assert "heading_level_jump" in {f.rule_id for f in page.review_flags}
    assert "heading_level_jump" not in {f.rule_id for f in page.confirmed_anomalies}


def test_duplicate_substantive_pages_are_confirmed(tmp_path: Path):
    repeated = "重复内容" * 30
    pages = [[_text_block(repeated)], [_text_block(repeated)]]
    _, doc_dir = _write_doc(tmp_path, pages=pages, markdown_pages=[repeated, repeated])
    doc = audit_document(doc_dir, domain="insurance", project_root=tmp_path)
    assert any(f.rule_id == "duplicate_substantive_page" for f in doc.confirmed_anomalies)
    assert {p.page_number for p in doc.high_risk_pages} == {1, 2}


def test_missing_structure_is_confirmed_and_scan_continues(tmp_path: Path):
    corpus = tmp_path / "corpus"
    bad = corpus / "regulatory" / "orphan"
    bad.mkdir(parents=True)
    (bad / "page_0001.md").write_text("orphan text", encoding="utf-8")
    good_root, _ = _write_doc(tmp_path, domain="insurance", doc_id="good", pages=[[_text_block("正文" * 30)]])
    # _write_doc uses the same corpus root.
    assert good_root == corpus
    docs = audit_corpus(corpus, project_root=tmp_path)
    assert len(docs) == 2
    orphan = next(d for d in docs if d.doc_id == "orphan")
    assert any(f.rule_id == "missing_document_structure" for f in orphan.confirmed_anomalies)
    assert orphan.risk_level == "critical"


def test_integrity_snapshot_is_stable_for_read_only_audit(tmp_path: Path):
    root, doc_dir = _write_doc(tmp_path, pages=[[_text_block("正文" * 30)]])
    before = corpus_integrity_snapshot(root, project_root=tmp_path)
    audit_document(doc_dir, domain="insurance", project_root=tmp_path)
    after = corpus_integrity_snapshot(root, project_root=tmp_path)
    assert before == after
    assert before["file_count"] == 3  # source JSON + structure + one page


def test_image_directory_sentinel_is_not_reported_as_missing_file(tmp_path: Path):
    # Real MinerU output sometimes uses "images/" as a sentinel rather than a
    # concrete file. It must not be mistaken for an unresolved asset.
    page = _inspect_page([_text_block("正文" * 30), _image_block(path="images/")])
    assert "unresolved_visual_asset" not in {f.rule_id for f in page.confirmed_anomalies}


def test_concrete_missing_image_is_confirmed(tmp_path: Path):
    page = _inspect_page([_text_block("正文" * 30), _image_block(path="images/missing.jpg")])
    assert "unresolved_visual_asset" in {f.rule_id for f in page.confirmed_anomalies}
