from pathlib import Path

from document.adapters.text import canonical_from_markdown_file, canonical_from_text_file


def test_markdown_and_text_share_canonical_contract(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# 标题\n\n正文\n\n| 指标 | 值 |\n| --- | --- |\n| 收入 | 100 |\n", encoding="utf-8")
    txt = tmp_path / "doc.txt"
    txt.write_text("普通文本", encoding="utf-8")

    md_doc = canonical_from_markdown_file(md, domain="financial_reports")
    txt_doc = canonical_from_text_file(txt, domain="financial_reports")

    assert md_doc.title == "标题"
    assert md_doc.page(1).tables[0].rows == (("收入", "100"),)
    assert txt_doc.page(1).text == "普通文本"
    assert type(md_doc) is type(txt_doc)
