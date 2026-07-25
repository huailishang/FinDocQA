"""Corpus validator tests (Lane A, remote-offline).

Tests cover:
1. checked-in synthetic fixture: adapt then validate end-to-end.
2. page continuity gap detection (missing page_XXXX.md).
3. image-only page detection.
4. table / formula block counting.
5. doc-id mapping mismatch.
6. degraded flag from document_structure.json.
7. validate_corpus across multiple documents.
8. markdown report rendering.
9. CLI exit code (non-zero on structural break).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from structure.corpus_validator import (
    validate_corpus,
    validate_doc,
    main as validator_main,
)
from structure.mineru_adapter import adapt_document, adapt_corpus

FIXTURE_DOC = Path(__file__).parent / "fixtures" / "mineru" / "sample_doc"


# ── 1. checked-in fixture: adapt then validate ───────────────────────


def test_checked_in_fixture_adapts_and_validates(tmp_path: Path):
    target = tmp_path / "target" / "insurance" / "sample_doc"
    result = adapt_document(FIXTURE_DOC, target, domain="insurance", doc_id="sample_doc")

    assert result.reconstruction_mode == "content_list_v2"
    assert result.degraded is False
    assert result.page_count == 3  # 3 list-of-lists pages

    v = validate_doc(target, domain="insurance", doc_id="sample_doc")
    assert v.structure_found is True
    assert v.doc_id_matches is True
    assert v.degraded is False
    assert v.declared_page_count == 3
    assert v.actual_page_files == 3
    assert v.page_gaps == ()
    # Page 2 is image-only (single image block).
    assert 2 in v.image_only_pages
    # Page 1 has one GFM table.
    assert v.table_count == 1
    # Page 3 has one $$ formula block.
    assert v.formula_count == 1


# ── 2. page continuity gap ───────────────────────────────────────────


def test_page_gap_detected_when_middle_page_missing(tmp_path: Path):
    target = tmp_path / "target" / "insurance" / "doc_g"
    target.mkdir(parents=True)
    # Write structure claiming 3 pages but only write 1 and 3.
    (target / "page_0001.md").write_text("# P1\n\ntext", encoding="utf-8")
    (target / "page_0003.md").write_text("# P3\n\ntext", encoding="utf-8")
    (target / "document_structure.json").write_text(
        json.dumps({
            "domain": "insurance", "doc_id": "doc_g", "parser": "mineru",
            "reconstruction_mode": "content_list_v2", "degraded": False,
            "page_count": 3, "warnings": [], "source_files": [],
        }),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="doc_g")
    assert v.page_gaps == (2,)
    assert any("page continuity gap" in w for w in v.warnings)


def test_no_gap_when_pages_contiguous(tmp_path: Path):
    target = tmp_path / "doc_ok"
    target.mkdir(parents=True)
    for i in (1, 2, 3):
        (target / f"page_{i:04d}.md").write_text(f"# P{i}\n\ntext", encoding="utf-8")
    (target / "document_structure.json").write_text(
        json.dumps({"doc_id": "doc_ok", "reconstruction_mode": "content_list_v2",
                    "degraded": False, "page_count": 3, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="doc_ok")
    assert v.page_gaps == ()


# ── 3. image-only page detection ─────────────────────────────────────


def test_image_only_page_detected(tmp_path: Path):
    target = tmp_path / "doc_img"
    target.mkdir(parents=True)
    (target / "page_0001.md").write_text("# 标题\n\n正文内容。", encoding="utf-8")
    (target / "page_0002.md").write_text(
        "![图1](images/p2.png)\n\n*图1 说明*\n", encoding="utf-8"
    )
    (target / "document_structure.json").write_text(
        json.dumps({"doc_id": "doc_img", "reconstruction_mode": "content_list_v2",
                    "degraded": False, "page_count": 2, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="doc_img")
    assert v.image_only_pages == (2,)


def test_placeholder_only_page_is_image_only(tmp_path: Path):
    target = tmp_path / "doc_ph"
    target.mkdir(parents=True)
    (target / "page_0001.md").write_text("正文。", encoding="utf-8")
    (target / "page_0002.md").write_text(
        "<!-- page 1: no renderable text (image-only or empty blocks) -->\n",
        encoding="utf-8",
    )
    (target / "document_structure.json").write_text(
        json.dumps({"doc_id": "doc_ph", "reconstruction_mode": "content_list_v2",
                    "degraded": False, "page_count": 2, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="doc_ph")
    assert 2 in v.image_only_pages


# ── 4. table / formula counting ──────────────────────────────────────


def test_table_and_formula_counts(tmp_path: Path):
    target = tmp_path / "doc_tf"
    target.mkdir(parents=True)
    (target / "page_0001.md").write_text(
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
        "| X | Y |\n| --- | --- |\n| 9 | 8 |\n",
        encoding="utf-8",
    )
    (target / "page_0002.md").write_text(
        "$$\na + b = c\n$$\n\n正文。\n",
        encoding="utf-8",
    )
    (target / "document_structure.json").write_text(
        json.dumps({"doc_id": "doc_tf", "reconstruction_mode": "content_list_v2",
                    "degraded": False, "page_count": 2, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="doc_tf")
    assert v.table_count == 2  # two GFM tables on page 1
    assert v.formula_count == 1  # one $$ block on page 2


# ── 5. doc-id mapping mismatch ───────────────────────────────────────


def test_doc_id_mismatch_flagged(tmp_path: Path):
    target = tmp_path / "dir_name"
    target.mkdir(parents=True)
    (target / "page_0001.md").write_text("text", encoding="utf-8")
    (target / "document_structure.json").write_text(
        json.dumps({"doc_id": "DIFFERENT_ID", "reconstruction_mode": "content_list_v2",
                    "degraded": False, "page_count": 1, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="dir_name")
    assert v.doc_id_matches is False
    assert any("doc_id mismatch" in w for w in v.warnings)


# ── 6. degraded flag ─────────────────────────────────────────────────


def test_degraded_flag_propagates(tmp_path: Path):
    target = tmp_path / "doc_deg"
    target.mkdir(parents=True)
    (target / "page_0001.md").write_text("# fallback\n\ntext", encoding="utf-8")
    (target / "document_structure.json").write_text(
        json.dumps({"doc_id": "doc_deg", "reconstruction_mode": "markdown_fallback",
                    "degraded": True, "page_count": 1, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    v = validate_doc(target, domain="insurance", doc_id="doc_deg")
    assert v.degraded is True
    assert v.reconstruction_mode == "markdown_fallback"
    assert any("degraded=True" in w for w in v.warnings)


def test_missing_structure_is_warning(tmp_path: Path):
    target = tmp_path / "doc_nostruct"
    target.mkdir(parents=True)
    (target / "page_0001.md").write_text("text", encoding="utf-8")
    v = validate_doc(target, domain="insurance", doc_id="doc_nostruct")
    assert v.structure_found is False
    assert any("document_structure.json not found" in w for w in v.warnings)


# ── 7. validate_corpus across multiple docs ──────────────────────────


def test_validate_corpus_multiple_docs(tmp_path: Path):
    target_root = tmp_path / "target"
    domain = "insurance"
    fixture_cl = FIXTURE_DOC / "auto" / "sample_doc_content_list_v2.json"
    items = json.loads(fixture_cl.read_text(encoding="utf-8"))
    # Build two mineru doc dirs with doc-id-prefixed content lists.
    for doc_id in ("d1", "d2"):
        d = tmp_path / "mineru" / domain / doc_id / "auto"
        d.mkdir(parents=True)
        (d / f"{doc_id}_content_list_v2.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8"
        )
    adapt_corpus(tmp_path / "mineru", target_root, domain=domain)
    report = validate_corpus(target_root, domain=domain)
    assert report.doc_count == 2
    assert all(d.doc_id_matches for d in report.docs)
    assert report.total_image_only_pages == 2  # one image-only page per doc
    assert report.total_degraded_docs == 0


def test_validate_corpus_empty_domain(tmp_path: Path):
    target_root = tmp_path / "target"
    (target_root / "insurance").mkdir(parents=True)
    report = validate_corpus(target_root, domain="insurance")
    assert report.doc_count == 0
    assert "no documents found" in report.text


def test_validate_corpus_skips_manifest_file(tmp_path: Path):
    """_adapt_manifest.json starts with underscore and must not be treated as a doc."""
    target_root = tmp_path / "target"
    domain = "insurance"
    adapt_document(FIXTURE_DOC, target_root / domain / "real_doc",
                   domain=domain, doc_id="real_doc")
    # Drop a manifest file alongside the doc dir.
    (target_root / domain / "_adapt_manifest.json").write_text("{}", encoding="utf-8")
    report = validate_corpus(target_root, domain=domain)
    assert report.doc_count == 1
    assert report.docs[0].doc_id == "real_doc"


# ── 8. markdown report ───────────────────────────────────────────────


def test_report_renders_markdown_table(tmp_path: Path):
    target_root = tmp_path / "target"
    adapt_document(FIXTURE_DOC, target_root / "insurance" / "sample_doc",
                   domain="insurance", doc_id="sample_doc")
    report = validate_corpus(target_root, domain="insurance")
    assert "# Corpus Validation Report" in report.text
    assert "sample_doc" in report.text
    assert "content_list_v2" in report.text


# ── 9. CLI exit code ─────────────────────────────────────────────────


def test_cli_exit_zero_when_clean(tmp_path: Path, capsys):
    target_root = tmp_path / "target"
    adapt_document(FIXTURE_DOC, target_root / "insurance" / "sample_doc",
                   domain="insurance", doc_id="sample_doc")
    rc = validator_main([str(target_root), "--domain", "insurance"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sample_doc" in out


def test_cli_exit_nonzero_on_page_gap(tmp_path: Path):
    target_root = tmp_path / "target"
    domain = "insurance"
    doc_dir = target_root / domain / "broken"
    doc_dir.mkdir(parents=True)
    (doc_dir / "page_0001.md").write_text("text", encoding="utf-8")
    (doc_dir / "document_structure.json").write_text(
        json.dumps({"doc_id": "broken", "reconstruction_mode": "content_list_v2",
                    "degraded": False, "page_count": 3, "warnings": [], "source_files": []}),
        encoding="utf-8",
    )
    rc = validator_main([str(target_root), "--domain", domain])
    assert rc == 1  # page gap is a structural break
