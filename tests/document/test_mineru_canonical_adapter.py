from __future__ import annotations

import json
from pathlib import Path

from document import CanonicalBlockType
from document.adapters.mineru import canonical_from_adapted_mineru, canonical_from_raw_mineru
from structure.mineru_adapter import adapt_document


def _raw_mineru_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "mineru" / "sample"
    root.mkdir(parents=True)
    items = [
        {"type": "title", "text": "年度报告", "page_idx": 0, "level": 1},
        {"type": "text", "text": "营业收入如下。", "page_idx": 0},
        {
            "type": "table",
            "table_body": "<table><tr><th>项目</th><th>金额</th></tr><tr><td>营业收入</td><td>100</td></tr></table>",
            "page_idx": 0,
        },
        {"type": "formula", "text": "growth=(100-80)/80", "page_idx": 1},
        {"type": "image", "img_path": "images/chart.png", "img_caption": "收入图", "page_idx": 1},
    ]
    (root / "content_list_v2.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return root


def test_adapted_mineru_becomes_canonical_document(tmp_path: Path) -> None:
    raw = _raw_mineru_fixture(tmp_path)
    adapted = tmp_path / "adapted"
    adapt_document(raw, adapted, domain="financial_reports", doc_id="sample")

    doc = canonical_from_adapted_mineru(adapted)

    assert doc.document_id == "sample"
    assert doc.domain == "financial_reports"
    assert doc.page_count == 2
    assert doc.title == "年度报告"
    assert doc.page(1) is not None
    assert doc.page(1).tables[0].headers == ("项目", "金额")
    assert doc.page(1).tables[0].rows == (("营业收入", "100"),)
    assert doc.page(2).formulas[0].expression == "growth=(100-80)/80"
    assert doc.page(2).figures[0].uri == "images/chart.png"
    assert all(block.lineage is not None for block in doc.iter_blocks())


def test_raw_mineru_adapter_does_not_require_permanent_intermediate_files(tmp_path: Path) -> None:
    raw = _raw_mineru_fixture(tmp_path)
    doc = canonical_from_raw_mineru(raw, domain="financial_reports", doc_id="sample")

    assert doc.page_count == 2
    assert doc.source_type == "mineru_raw"
    assert doc.source_uri == str(raw)
    assert any(block.block_type == CanonicalBlockType.TABLE for block in doc.iter_blocks())
