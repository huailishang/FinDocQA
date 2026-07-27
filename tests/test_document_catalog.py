from pathlib import Path

from retrieval.document_catalog import DocumentCatalog


def _write_doc(root: Path, domain: str, doc_id: str, text: str) -> Path:
    doc_dir = root / domain / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "page_0001.md").write_text(text, encoding="utf-8")
    return doc_dir


def test_catalog_builds_auditable_identity_and_aliases(tmp_path: Path) -> None:
    primary = tmp_path / "retrieval"
    raw = tmp_path / "raw"
    _write_doc(
        primary,
        "financial_reports",
        "annual_midea_2024_report",
        "# 美的集团股份有限公司\n\n# 2024 年年度报告\n",
    )
    raw_domain = raw / "financial_reports"
    raw_domain.mkdir(parents=True)
    (raw_domain / "annual_midea_2024_report.PDF").write_bytes(b"pdf")

    catalog = DocumentCatalog.from_roots(primary, raw_root=raw)

    entries = catalog.entries_for_domain("financial_reports")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.doc_id == "annual_midea_2024_report"
    assert "美的集团股份有限公司" in entry.title
    assert "美的集团" in entry.title_aliases
    assert any(path.endswith("annual_midea_2024_report.PDF") for path in entry.source_paths)
    assert "2024 年年度报告" in entry.identity_text


def test_catalog_primary_root_wins_and_fallback_can_add_missing_doc(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    _write_doc(primary, "insurance", "1", "# 主目录产品\n")
    _write_doc(fallback, "insurance", "1", "# 备用目录产品\n")
    _write_doc(fallback, "insurance", "2", "# 新增备用产品\n")
    _write_doc(fallback, "attachments", "helper", "# 不应成为业务 domain\n")

    catalog = DocumentCatalog.from_roots(primary, fallback_roots=(fallback,))

    entries = {entry.doc_id: entry for entry in catalog.entries_for_domain("insurance")}
    assert set(entries) == {"1", "2"}
    assert catalog.entries_for_domain("attachments") == ()
    assert "主目录产品" in entries["1"].identity_text
    assert "备用目录产品" not in entries["1"].identity_text
    assert "新增备用产品" in entries["2"].identity_text


def test_catalog_extracts_short_names_and_mixed_cover_identity_aliases(tmp_path: Path) -> None:
    primary = tmp_path / "retrieval"
    _write_doc(
        primary,
        "financial_reports",
        "annual_cscec_2025_report",
        "# CSCEc 中國建築CHINA STATE CONSTRUCTION\n",
    )
    _write_doc(
        primary,
        "financial_reports",
        "annual_cmb_2025_report",
        "# 招商银行CHINA MERCHANTS BANK招商银行股份有限公司股票代码：600036\n",
    )
    _write_doc(
        primary,
        "financial_contracts",
        "text11",
        "# 股票简称：普联软件\n\n# 证券代码：300996\n",
    )
    _write_doc(
        primary,
        "regulatory",
        "reg1",
        "# ABC 金融机构监督管理办法 DEF\n",
    )

    catalog = DocumentCatalog.from_roots(primary)
    reports = {entry.doc_id: entry for entry in catalog.entries_for_domain("financial_reports")}
    contracts = {entry.doc_id: entry for entry in catalog.entries_for_domain("financial_contracts")}
    regulatory = {entry.doc_id: entry for entry in catalog.entries_for_domain("regulatory")}

    assert "中国建筑" in reports["annual_cscec_2025_report"].title_aliases
    assert "招商银行" in reports["annual_cmb_2025_report"].title_aliases
    assert "普联软件" in contracts["text11"].title_aliases
    assert "金融机构监督管理办法" not in regulatory["reg1"].title_aliases


def test_catalog_lexical_profile_uses_full_budget_for_long_single_page(tmp_path: Path) -> None:
    primary = tmp_path / "retrieval"
    marker_a = "开头识别信息"
    marker_b = "中段处罚时效事实"
    marker_c = "尾段行政处罚结论"
    long_text = chr(10).join(
        [
            f"# {marker_a}",
            "甲" * 12000,
            marker_b,
            "乙" * 12000,
            marker_c,
            "丙" * 6000,
        ]
    )
    _write_doc(primary, "regulatory", "long_web", long_text)

    catalog = DocumentCatalog.from_roots(
        primary,
        max_lexical_chars=50000,
        lexical_chars_per_page=2400,
    )
    profile = catalog.entries_for_domain("regulatory")[0].lexical_profile

    assert marker_a in profile
    assert marker_b in profile
    assert marker_c in profile
