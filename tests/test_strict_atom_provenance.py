from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from verification.strict_atom_provenance import (
    EXACT_ATOM_BINDING,
    INSUFFICIENT_PROVENANCE,
    NO_EQUIVALENCE_REQUIRED_TYPED_BINDING,
    audit_strict_atom_provenance,
)


def test_exact_atom_binding_requires_decisive_raw_coverage():
    row = audit_strict_atom_provenance(
        option_text="证券公司分类评价新规自2025年8月22日起施行",
        source_texts=["证券公司分类评价新规自2025年8月22日起施行。"],
        fact_status="SUPPORTED",
    )
    assert row["provenance_class"] == EXACT_ATOM_BINDING
    assert row["promotion_allowed"] is True
    assert row["exact_atom_binding_pass"] is True
    assert row["required_field_count"] > 0


def test_7_days_vs_30_days_is_typed_contradiction_not_exact_text():
    row = audit_strict_atom_provenance(
        option_text="银行卡清算机构撤并分支机构的，应当至少提前7日报告",
        source_texts=["银行卡清算机构撤并分支机构的，应当至少提前30日报告。"],
        fact_status="CONTRADICTED",
    )
    assert row["exact_atom_binding_pass"] is False
    assert row["typed_relation_audit"]["contradiction_relation_pass"] is True
    assert row["provenance_class"] == NO_EQUIVALENCE_REQUIRED_TYPED_BINDING
    assert row["promotion_allowed"] is True


def test_support_does_not_accept_partial_modal_overlap():
    row = audit_strict_atom_provenance(
        option_text="会计师事务所未勤勉尽责可能面临行政处罚",
        source_texts=["会计师事务所未勤勉尽责，应当受到行政处罚。"],
        fact_status="SUPPORTED",
    )
    assert row["typed_relation_audit"]["support_relation_pass"] is False
    assert row["provenance_class"] == INSUFFICIENT_PROVENANCE
    assert row["promotion_allowed"] is False


def test_semantic_alias_dependency_blocks_strong_lane_even_with_raw_overlap():
    row = audit_strict_atom_provenance(
        option_text="敏感数据项不得存储",
        source_texts=["敏感数据项不得存储"],
        fact_status="SUPPORTED",
        semantic_alias_dependencies=[{"raw_option_term": "敏感数据项", "raw_source_term": "高敏感性数据项"}],
    )
    assert row["provenance_class"] == INSUFFICIENT_PROVENANCE
    assert row["promotion_allowed"] is False
