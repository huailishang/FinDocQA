from retrieval.crag_corrective_retrieval_v2 import rewrite_query, grade_retrieval, parent_context, classify_two_rounds

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
