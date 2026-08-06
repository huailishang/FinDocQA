from __future__ import annotations

import json
from pathlib import Path

from evaluation.generic_local_option_adjudicator import (
    EvidenceWindow,
    adjudicate_option,
    adjudicate_question,
    retrieve_option_windows,
)


ROOT = Path(__file__).resolve().parents[1]


def page_ev(text: str, *, doc_id: str = "doc", page_number: int = 1) -> EvidenceWindow:
    return EvidenceWindow(
        doc_id=doc_id,
        source_path=f"page_{page_number:04d}.md",
        score=10.0,
        text=text,
        matched_terms=(),
        page_number=page_number,
        source_page_index=page_number - 1,
    )


def load_questions() -> dict[str, dict[str, object]]:
    questions: dict[str, dict[str, object]] = {}
    for path in sorted((ROOT / "data/raw_dataset/questions/group_a").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            questions[str(row["qid"])] = dict(row)
    return questions


def test_page_level_retrieval_wins_over_whole_document_markdown(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    page_dir = data_root / "processed_mineru_retrieval" / "financial_reports" / "doc"
    page_dir.mkdir(parents=True)
    (page_dir / "page_0007.md").write_text(
        "公司2025年营业收入为100亿元。",
        encoding="utf-8",
    )
    whole_dir = data_root / "processed_mineru" / "financial_reports" / "doc" / "auto"
    whole_dir.mkdir(parents=True)
    (whole_dir / "doc.md").write_text(
        "整份文档中的无关营业收入为999亿元。",
        encoding="utf-8",
    )

    windows = retrieve_option_windows(
        data_root=data_root,
        domain="financial_reports",
        doc_ids=("doc",),
        option_text="公司2025年营业收入为100亿元",
    )

    assert windows
    assert all(Path(row.source_path).name == "page_0007.md" for row in windows)
    assert all(row.page_number == 7 for row in windows)
    assert all(row.source_page_index == 6 for row in windows)
    assert all("999亿元" not in row.text for row in windows)


def test_page_less_evidence_fails_closed_with_explicit_gap() -> None:
    row = adjudicate_option(
        label="A",
        option_text="公司2025年营业收入为100亿元",
        windows=[
            EvidenceWindow(
                doc_id="doc",
                source_path="doc.md",
                score=10.0,
                text="公司2025年营业收入为200亿元",
                matched_terms=(),
            )
        ],
    )

    assert row.relation == "UNRESOLVED"
    assert row.reason == "page_resolution_gap_no_page_level_evidence"
    assert row.evidence == ()
    assert row.page_resolution_gaps == ("doc.md:page_identity_missing",)


def test_unrelated_direction_word_cannot_create_high_contradiction() -> None:
    row = adjudicate_option(
        label="A",
        option_text="2025年营业总收入的增长率高于2024年的增长率",
        windows=[
            page_ev(
                "2025年国内经济下行压力增大，行业零售规模下降；"
                "该段没有公司营业总收入增长率。"
            )
        ],
    )

    assert row.relation == "UNRESOLVED"
    assert row.relation != "CONTRADICTED"


def test_year_mismatch_cannot_create_metric_value_contradiction() -> None:
    row = adjudicate_option(
        label="B",
        option_text="公司2025年新签合同额达到2.19万亿元",
        windows=[page_ev("公司2024年新签合同额为4.5万亿元")],
    )

    assert row.relation == "UNRESOLVED"
    assert row.reason == "period_binding_gate_no_matching_period_evidence"


def test_unit_scaling_is_normalized_before_comparison() -> None:
    row = adjudicate_option(
        label="A",
        option_text="中国移动2025年营业收入超过1万亿元人民币",
        windows=[page_ev("中国移动2025年营业收入达到人民币10,502亿元")],
    )

    assert row.relation == "SUPPORTED"
    assert row.reason.startswith("amount_comparator:")


def test_frozen_false_contradictions_are_closed_without_qid_branches() -> None:
    questions = load_questions()
    probes = (("reg_a_007", "A"), ("fin_a_020", "B"), ("fin_a_004", "A"))

    for qid, label in probes:
        question = questions[qid]
        result = adjudicate_question(
            data_root=ROOT / "data",
            domain=str(question["domain"]),
            doc_ids=tuple(str(item) for item in question["doc_ids"]),
            options={str(key): str(value) for key, value in dict(question["options"]).items()},
        )
        row = next(item for item in result["options"] if item["label"] == label)
        assert row["relation"] != "CONTRADICTED", (qid, label, row)
        assert all(item["page_number"] is not None for item in row["evidence"])
        assert all(item["source_page_index"] is not None for item in row["evidence"])
        assert all(Path(item["source_path"]).name.startswith("page_") for item in row["evidence"])

    source = (ROOT / "src/evaluation/generic_local_option_adjudicator.py").read_text(encoding="utf-8")
    assert "reg_a_007" not in source
    assert "fin_a_020" not in source
    assert "fin_a_004" not in source
    assert "candidate_answer" not in source
    assert "gold_answer" not in source


def test_frozen_supporting_facts_remain_in_original_sources() -> None:
    regulatory = (
        ROOT
        / "data/processed_mineru_retrieval/regulatory"
        / "strict_v3_009_中国人民银行_国家金融监督管理总局_中国证券监督管理委员会令〔2025〕第11号（金融机构客户尽职调查和客户身份资料及交易记录保存管理办法）"
        / "page_0001.md"
    ).read_text(encoding="utf-8")
    for phrase in ("本办法自2026年1月1日起施行", "〔2007〕第2号", "〔2022〕第1号", "同时废止"):
        assert phrase in regulatory

    catl = (
        ROOT
        / "data/processed_mineru_retrieval/financial_reports/annual_catl_2025_report/page_0020.md"
    ).read_text(encoding="utf-8")
    assert "经营活动产生的现金流量净额 1,332 亿元" in catl

    midea_2024 = (
        ROOT
        / "data/processed_mineru_retrieval/financial_reports/annual_midea_2024_report/page_0009.md"
    ).read_text(encoding="utf-8")
    midea_2025 = (
        ROOT
        / "data/processed_mineru_retrieval/financial_reports/annual_midea_2025_report/page_0009.md"
    ).read_text(encoding="utf-8")
    assert "9.44%" in midea_2024
    assert "12.11%" in midea_2025
    assert 12.11 > 9.44
