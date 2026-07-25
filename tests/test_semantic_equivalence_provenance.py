from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from verification.semantic_equivalence_provenance import (
    AUTHORITATIVE_DEFINITION,
    COMPILER_INTERNAL_ALIAS_ONLY,
    EVALUATOR_CALIBRATED_EQUIVALENCE,
    EXACT_ATOM_BINDING,
    INSUFFICIENT_PROVENANCE,
    MODEL_PARAPHRASE_ONLY,
    NO_EQUIVALENCE_REQUIRED_TYPED_BINDING,
    classify_semantic_equivalence,
    extract_compact_alias_rules,
    promotion_decision,
)

COMPILER = ROOT / "src/verification/regulatory_option_evidence.py"


def rules():
    return extract_compact_alias_rules(COMPILER)


def test_reg016_internal_alias_chain_is_not_canonical_proof():
    row = classify_semantic_equivalence(
        option_text="证监会应以名义业务收入为基数作出处罚决定",
        source_texts=["没收业务收入的范围既包括已经取得的业务收入,也包括尚未取得的业务收入。"],
        rules=rules(),
        compiler_caveats=["nominal_income_term_equivalence"],
    )
    assert row["provenance_class"] == COMPILER_INTERNAL_ALIAS_ONLY
    assert row["promotion_allowed"] is False
    assert promotion_decision(row) == "DOWNGRADE_UNRESOLVED"
    assert any(dep["raw_option_term"] == "名义业务收入" for dep in row["alias_dependencies"])


def test_sensitive_data_internal_alias_is_also_audited():
    row = classify_semantic_equivalence(
        option_text="照片等敏感数据项原则上不得在终端设备中存储",
        source_texts=["高敏感性数据项原则上不在终端设备和移动介质中存储"],
        rules=rules(),
    )
    assert row["provenance_class"] == COMPILER_INTERNAL_ALIAS_ONLY
    assert row["promotion_allowed"] is False


def test_no_alias_dependency_without_atom_audit_fails_closed():
    row = classify_semantic_equivalence(
        option_text="客户身份资料至少保存10年",
        source_texts=["客户身份资料自业务关系结束后至少保存10年"],
        rules=rules(),
    )
    assert row["provenance_class"] == INSUFFICIENT_PROVENANCE
    assert row["promotion_allowed"] is False


def test_explicit_atom_audit_can_enter_exact_atom_lane():
    row = classify_semantic_equivalence(
        option_text="客户身份资料至少保存10年",
        source_texts=["客户身份资料自业务关系结束后至少保存10年"],
        rules=rules(),
        atom_coverage_audit={"exact_atom_binding_pass": True},
    )
    assert row["provenance_class"] == EXACT_ATOM_BINDING
    assert row["promotion_allowed"] is True


def test_typed_relation_lane_is_distinct_from_exact_atom_lane():
    row = classify_semantic_equivalence(
        option_text="应当至少提前7日报告",
        source_texts=["应当至少提前30日报告"],
        rules=rules(),
        atom_coverage_audit={"exact_atom_binding_pass": False},
        typed_relation_audit={"pass": True},
    )
    assert row["provenance_class"] == NO_EQUIVALENCE_REQUIRED_TYPED_BINDING
    assert row["promotion_allowed"] is True


def test_authoritative_definition_can_override_internal_alias_dependency():
    row = classify_semantic_equivalence(
        option_text="名义业务收入",
        source_texts=["已经取得的业务收入,也包括尚未取得的业务收入"],
        rules=rules(),
        authoritative_definition_sources=[{"source": "official", "definition": "verified"}],
    )
    assert row["provenance_class"] == AUTHORITATIVE_DEFINITION
    assert row["promotion_allowed"] is True


def test_evaluator_calibration_can_override_exact_alias_pair_only_when_explicit():
    row = classify_semantic_equivalence(
        option_text="名义业务收入",
        source_texts=["已经取得的业务收入,也包括尚未取得的业务收入"],
        rules=rules(),
        evaluator_calibrated_pairs=[("名义业务收入", "已经取得的业务收入,也包括尚未取得的业务收入")],
    )
    assert row["provenance_class"] == EVALUATOR_CALIBRATED_EQUIVALENCE
    assert row["promotion_allowed"] is True


def test_model_paraphrase_only_never_becomes_strong_fact():
    row = classify_semantic_equivalence(
        option_text="某术语",
        source_texts=["另一表述"],
        rules=rules(),
        model_paraphrase_only=True,
    )
    assert row["provenance_class"] == MODEL_PARAPHRASE_ONLY
    assert row["promotion_allowed"] is False
    assert promotion_decision(row) == "KEEP_TIER_B_SHADOW"


def test_compiler_alias_table_is_parsed_without_executing_compiler():
    parsed = rules()
    assert any(row.before == "名义业务收入" and row.after == "全部业务收入" for row in parsed)
    assert any(row.before == "敏感数据项" and row.after == "高敏感性数据项" for row in parsed)
