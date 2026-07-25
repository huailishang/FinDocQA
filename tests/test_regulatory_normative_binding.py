from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from verification.regulatory_normative_binding import (
    closure_distance_rank,
    compare_normative_propositions,
    extract_normative_proposition,
    regulatory_complete_scope_absence_shadow,
)


def relation(claim: str, source: str):
    return compare_normative_propositions(
        extract_normative_proposition(claim),
        extract_normative_proposition(source),
    )


def test_modal_must_is_not_proved_by_may_only():
    row = relation("金融机构必须披露相关信息", "金融机构可以披露相关信息")
    assert row.status == "CONTRADICTED"
    assert row.modal_relation == "CONFLICT"


def test_modal_may_conflicts_with_explicit_must_not():
    row = relation("金融机构可以披露相关信息", "金融机构不得披露相关信息")
    assert row.status == "CONTRADICTED"
    assert row.modal_relation == "CONFLICT"


def test_no_invalid_converse_for_negated_dividend_condition():
    row = relation(
        "上市公司可以在不具备现金分红条件而不进行现金分红的情况下，不披露具体原因",
        "上市公司具备现金分红条件而不进行现金分红的，应当披露具体原因",
    )
    assert row.status == "UNRESOLVED"
    assert row.invalid_converse_blocked is True
    assert row.condition_relation == "CONVERSE_NOT_INFERRED"


def test_before_after_relation_conflicts():
    row = relation(
        "上市公司必须在年度审计报告出具前完成支付现金分红",
        "上市公司在年度审计报告出具后支付现金分红",
    )
    assert row.status == "CONTRADICTED"
    assert row.time_relation == "CONFLICT"


def test_seven_days_conflicts_with_thirty_days():
    row = relation(
        "银行卡清算机构撤并分支机构的，应当至少提前7日报告",
        "银行卡清算机构撤并分支机构的，应当至少提前30日报告",
    )
    assert row.status == "CONTRADICTED"
    assert row.time_relation == "CONFLICT"


def test_negative_operation_forms_are_not_swallowed_by_positive_substrings():
    assert extract_normative_proposition("上市公司可以不披露具体原因").operation == "omit_disclosure"
    assert extract_normative_proposition("分类评价得分不会受到影响").operation == "no_score_effect"
    assert extract_normative_proposition("本规定自该日起停止施行").operation == "repeal"


def test_closure_distance_is_primary_over_accumulated_secondary_score():
    ranked = closure_distance_rank([
        {
            "qid": "far", "unresolved_options_after": ["A", "B", "C", "D"],
            "verified_source_option_count": 4, "missing_semantic_lte2_option_count": 4,
            "same_mechanism_cluster_value": 10,
        },
        {
            "qid": "near", "unresolved_options_after": ["C"],
            "verified_source_option_count": 0, "missing_semantic_lte2_option_count": 0,
            "same_mechanism_cluster_value": 0,
        },
    ])
    assert [row["qid"] for row in ranked] == ["near", "far"]
    assert ranked[0]["closure_distance_tier"] == 0
    assert ranked[1]["closure_distance_tier"] == 3


def test_regulatory_complete_scope_absence_is_shadow_only_and_fail_closed():
    row = regulatory_complete_scope_absence_shadow(
        option_text="某义务必须存在",
        declared_documents_complete=True,
        option_required_docs_complete=True,
        relevant_section_boundaries_identified=False,
        target_relation_defined=True,
        alias_family_scanned=True,
        full_relevant_scope_scanned=True,
        supporting_or_contradicting_clause_found=False,
    )
    assert row["status"] == "FAIL_CLOSED_NOT_SHADOW_ABSENCE"
    assert row["tier_a_allowed"] is False
    assert row["candidate_gate_only"] is True


def test_production_module_contains_no_qid_specific_answer_literals():
    source = (ROOT / "src/verification/regulatory_normative_binding.py").read_text(encoding="utf-8")
    assert not re.findall(r"(?:fin|fc|ins|reg|res)_a_\d{3}", source)
