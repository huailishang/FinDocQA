from retrieval.crag_corrective_retrieval_v2 import (
    classify_two_rounds,
    directed_child_retrieval,
    grade_retrieval,
    parent_context,
    rewrite_query,
)

def test_query_rewrite_is_two_round_bounded_and_qid_agnostic():
    q1=rewrite_query("research","2026 年一季度国内电动车累计销量同比增长 3.6%",round_number=1)
    q2=rewrite_query("research","2026 年一季度国内电动车累计销量同比增长 3.6%",round_number=2)
    assert q1["round_number"]==1 and q2["round_number"]==2
    assert any("3.6" in x for x in q1["numeric_terms"])
    assert q2["strategy"].startswith("ROUND2_")
    assert "qid" not in q1 and "answer" not in q1

def test_quality_grading_and_parent_context_do_not_assign_truth():
    q=rewrite_query("financial_contracts","发行金额不超过 5 亿元",round_number=1)
    src=[{"span":"本期债券发行金额为不超过（含）5亿元。"}]
    grade=grade_retrieval(q,src)
    assert grade in {"CORRECT","AMBIGUOUS"}
    expanded=parent_context(src)
    assert expanded[0]["parent_context_expanded"] is True
    blob=str(expanded)
    assert "SUPPORTED" not in blob and "CONTRADICTED" not in blob

def test_two_round_classifier():
    assert classify_two_rounds([{"retrieval_quality":"INCORRECT"},{"retrieval_quality":"AMBIGUOUS"}])=="AMBIGUOUS"
    assert classify_two_rounds([{"retrieval_quality":"CORRECT"}])=="CORRECT"


def test_directed_child_retrieval_reads_data_under_repo_root(tmp_path):
    page = (
        tmp_path
        / "data"
        / "processed_mineru_retrieval"
        / "financial_contracts"
        / "text01"
        / "page_0001.md"
    )
    page.parent.mkdir(parents=True)
    page.write_text("本期债券发行金额为5亿元。", encoding="utf-8")

    hits = directed_child_retrieval(
        repo_root=tmp_path,
        domain="financial_contracts",
        required_doc_ids=("text01",),
        rewritten_query={"terms": ["发行金额", "5亿元"], "semantic_terms": ["发行金额"], "numeric_terms": ["5亿元"]},
        round_number=1,
    )

    assert hits
    assert hits[0]["doc_id"] == "text01"
    assert str(page) == hits[0]["source_path"]
