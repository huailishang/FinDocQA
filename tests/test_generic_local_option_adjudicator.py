from evaluation.generic_local_option_adjudicator import (
    EvidenceWindow,
    adjudicate_option,
    extract_option_entities,
)


def ev(text: str, *, doc_id: str = "doc", page_number: int = 1) -> EvidenceWindow:
    return EvidenceWindow(
        doc_id=doc_id,
        source_path=f"page_{page_number:04d}.md",
        score=10.0,
        text=text,
        matched_terms=(),
        page_number=page_number,
        source_page_index=page_number - 1,
    )


def test_metric_value_binding_supports_same_metric_number():
    row = adjudicate_option(
        label="A",
        option_text="公司2024年研发投入占营业收入比例为6.97%",
        windows=[ev("公司2024年研发投入占营业收入比例 6.97%，上年为6.63%")],
    )
    assert row.relation == "SUPPORTED"


def test_metric_value_binding_rejects_number_borrowed_from_other_metric():
    row = adjudicate_option(
        label="B",
        option_text="公司2024年新签合同额达到2.19万亿元",
        windows=[ev("公司2024年全年新签合同额4.5万亿元，完成营业收入2.19万亿元")],
    )
    assert row.relation == "CONTRADICTED"
    assert row.bound_evidence_number == "4.5"


def test_directional_contradiction_detects_decrease_vs_growth():
    row = adjudicate_option(
        label="C",
        option_text="公司2024年经营活动产生的现金流量净额较上年有所增长",
        windows=[ev("公司2024年经营活动产生的现金流量净额为133453873000元，同比下降21.37%")],
    )
    assert row.relation == "CONTRADICTED"


def test_profit_loss_contradiction_uses_numeric_anchor_with_same_entity():
    row = adjudicate_option(
        label="D",
        option_text="某公司2025年前三季度净利润盈利1139.39万元",
        windows=[ev("某公司2025年前三季度净利润亏损1139.39万元")],
    )
    assert row.relation == "CONTRADICTED"


def test_cross_entity_profit_loss_does_not_contradict():
    row = adjudicate_option(
        label="D",
        option_text="甲公司2025年前三季度净利润盈利1139.39万元",
        windows=[ev("乙公司2025年前三季度净利润亏损1139.39万元")],
    )
    assert extract_option_entities(row.option_text) == ("甲公司",)
    assert row.relation == "UNRESOLVED"
    assert row.reason == "entity_binding_gate_no_same_entity_evidence"


def test_same_entity_profit_loss_can_contradict():
    row = adjudicate_option(
        label="D",
        option_text="甲公司2025年前三季度净利润盈利1139.39万元",
        windows=[ev("甲公司2025年前三季度净利润亏损1139.39万元")],
    )
    assert row.relation == "CONTRADICTED"


def test_threshold_comparison_supports_value_above_threshold():
    row = adjudicate_option(
        label="D",
        option_text="公司2025年经营活动产生的现金流量净额同比增长超过30%",
        windows=[ev("公司2025年经营活动产生的现金流量净额同比增长37.35%")],
    )
    assert row.relation == "SUPPORTED"


def test_negated_bonus_share_claim_is_contradicted():
    row = adjudicate_option(
        label="D",
        option_text="利润分配预案中包含了送红股方案",
        windows=[ev("2024年度，公司不实施资本公积金转增股本，不送红股")],
    )
    assert row.relation == "CONTRADICTED"


def test_regulatory_no_impact_is_contradicted_by_penalty_deduction_rule():
    row = adjudicate_option(
        label="C",
        option_text="被监管机构实施行政处罚的公司，其分类评价得分不会受到影响",
        windows=[ev("评价期内公司因违法违规行为被实施行政处罚的，按以下原则给予相应扣分")],
    )
    assert row.relation == "CONTRADICTED"


def test_percentage_threshold_does_not_bind_unrelated_tax_rate():
    row = adjudicate_option(
        label="D",
        option_text="中国移动2025年研发费用占营业收入比重超过5%",
        windows=[ev("中国移动通信集团海南有限公司适用15%的企业所得税优惠税率")],
    )
    assert row.relation == "UNRESOLVED"
    assert row.relation != "SUPPORTED"


def test_micro_decrease_contradicts_revenue_growth_claim():
    row = adjudicate_option(
        label="D",
        option_text="宇信科技2025年营收同比增长8.47%",
        windows=[ev("宇信科技2025年公司营收微降8.47%")],
    )
    assert row.relation == "CONTRADICTED"
    assert row.reason == "opposite_marker:增长->微降"


def test_directional_claim_requires_directional_compatible_evidence_for_semantic_fallback():
    row = adjudicate_option(
        label="B",
        option_text="中国移动2025年营业收入同比减少了0.9%",
        windows=[ev("2025年营业收入1,050,187，2024年1,040,759，变化0.9%")],
    )
    assert row.relation == "UNRESOLVED"


def test_revenue_amount_does_not_bind_revenue_growth_rate_clause():
    row = adjudicate_option(
        label="A",
        option_text="中国移动2025年营业收入超过1万亿元人民币",
        windows=[ev("公司营运支出较上年增长0.3%，低于营业收入增幅0.6pp")],
    )
    assert row.relation == "UNRESOLVED"


def test_amount_threshold_converts_trillion_and_hundred_million_units():
    row = adjudicate_option(
        label="A",
        option_text="中国移动2025年营业收入超过1万亿元人民币",
        windows=[ev("中国移动2025年营业收入达到人民币10,502亿元")],
    )
    assert row.relation == "SUPPORTED"
    assert row.reason.startswith("amount_comparator:")
