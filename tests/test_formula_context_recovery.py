"""C3-B formula context recovery tests (offline, canonical-document only)."""
from __future__ import annotations

from calculation import (
    FormulaContextRecovery,
    FormulaEvidence,
    FormulaGateStatus,
    FormulaSourceRef,
)
from document.contracts import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalFormula,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from document.store import InMemoryDocumentStore


def _lineage(page: int) -> SourceLineage:
    return SourceLineage(
        source_type="test",
        source_path=f"doc://insurance_demo/page/{page}",
        parser_name="test",
        page_number=page,
    )


def _block(
    block_id: str,
    page: int,
    order: int,
    text: str,
    *,
    block_type: CanonicalBlockType = CanonicalBlockType.TEXT,
    formula_id: str | None = None,
    table_id: str | None = None,
    metadata: dict | None = None,
) -> CanonicalBlock:
    return CanonicalBlock(
        block_id=block_id,
        page_number=page,
        block_type=block_type,
        text=text,
        reading_order=order,
        table_id=table_id,
        formula_id=formula_id,
        lineage=_lineage(page),
        metadata=dict(metadata or {}),
    )


def _document(*pages: CanonicalPage) -> CanonicalDocument:
    return CanonicalDocument(
        document_id="insurance_demo",
        domain="insurance",
        title="demo",
        source_type="test",
        source_uri="doc://insurance_demo",
        parser_name="test",
        parser_version="1",
        pages=tuple(pages),
    )


def _evidence(*, block_id: str = "formula", page: int = 1, context: str = "") -> FormulaEvidence:
    return FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        context_text=context,
        source_refs=(
            FormulaSourceRef(
                doc_id="insurance_demo",
                page_number=page,
                source=f"doc://insurance_demo/page/{page}",
                block_id=block_id,
                excerpt="赔付金额 = expense * ratio",
            ),
        ),
        metadata={"domain": "insurance", "formula_id": "f1"},
    )


def test_same_page_previous_block_recovers_missing_variables_and_gate_passes():
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    store = InMemoryDocumentStore.from_documents((_document(page),))

    result = FormulaContextRecovery(store).recover(_evidence())

    assert result.status is FormulaGateStatus.PASS
    assert result.gate_result is not None
    assert result.gate_result.status is FormulaGateStatus.PASS
    assert "expense = 100元" in result.recovered_evidence.context_text
    assert {ref.block_id for ref in result.recovered_source_refs} >= {"vars", "formula"}


def test_same_page_following_block_can_recover_variables():
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio\nexpense = 100元\nratio = 80%",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("vars", 1, 1, "expense = 100元\nratio = 80%"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.PASS
    assert "ratio = 80%" in result.recovered_evidence.context_text


def test_non_contiguous_reading_order_stays_review():
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio\nratio = 80%",
        blocks=(
            _block("before", 1, 0, "expense = 100元"),
            _block("formula", 1, 2, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("after", 1, 3, "ratio = 80%"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert "reading_order_not_contiguous" in result.reasons


def test_explicit_table_id_recovers_only_linked_table():
    linked = CanonicalTable(
        table_id="t1",
        page_number=1,
        markdown="expense = 100元",
        caption="赔付基数",
        lineage=_lineage(1),
    )
    unrelated = CanonicalTable(
        table_id="t2",
        page_number=1,
        markdown="expense = 999元",
        caption="无关表",
        lineage=_lineage(1),
    )
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。按下表确定。\nratio = 80%",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio。按下表确定。", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("ratio", 1, 1, "ratio = 80%"),
            _block("table1", 1, 2, "expense = 100元", block_type=CanonicalBlockType.TABLE, table_id="t1"),
            _block("table2", 1, 3, "expense = 999元", block_type=CanonicalBlockType.TABLE, table_id="t2"),
        ),
        tables=(linked, unrelated),
        formulas=(CanonicalFormula("f1", 1, "expense * ratio", lineage=_lineage(1)),),
        lineage=_lineage(1),
    )
    evidence = FormulaEvidence(
        **{
            **_evidence().to_dict(),
            "source_refs": _evidence().source_refs,
            "linked_table_refs": ("t1",),
            "metadata": {"domain": "insurance", "formula_id": "f1"},
        }
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(evidence)
    assert result.status is FormulaGateStatus.PASS
    assert "expense = 100元" in result.recovered_evidence.context_text
    assert "expense = 999元" not in result.recovered_evidence.context_text
    assert any(step.action == "linked_table" for step in result.recovery_steps)


def test_unlinked_same_page_table_is_not_guessed():
    table = CanonicalTable(
        table_id="t1",
        page_number=1,
        markdown="expense = 100元",
        lineage=_lineage(1),
    )
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。按下表确定。\nratio = 80%\nexpense = 100元",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio。按下表确定。", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("ratio", 1, 1, "ratio = 80%"),
            _block("table1", 1, 2, "expense = 100元", block_type=CanonicalBlockType.TABLE, table_id="t1"),
        ),
        tables=(table,),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert "expense = 100元" not in result.recovered_evidence.context_text
    assert "linked_table_missing" in result.reasons


def test_missing_linked_table_stays_review():
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio\nexpense = 100元\nratio = 80%",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("vars", 1, 1, "expense = 100元\nratio = 80%"),
        ),
        lineage=_lineage(1),
    )
    base = _evidence()
    evidence = FormulaEvidence(
        raw_formula=base.raw_formula,
        normalized_expression=base.normalized_expression,
        context_text=base.context_text,
        source_refs=base.source_refs,
        linked_table_refs=("missing",),
        metadata=base.metadata,
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(evidence)
    assert result.status is FormulaGateStatus.REVIEW
    assert "linked_table_not_found:missing" in result.reasons
    assert result.ready_for_execution is False


def test_explicit_next_page_signal_recovers_unique_continuation():
    page1 = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。变量定义见下页",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio。变量定义见下页", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="expense = 100元\nratio = 80%",
        blocks=(_block("continuation", 2, 0, "expense = 100元\nratio = 80%", metadata={"continuation_of": "formula"}),),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence(context="变量定义见下页"))
    assert result.status is FormulaGateStatus.PASS
    assert "expense = 100元" in result.recovered_evidence.context_text
    assert any(ref.page_number == 2 for ref in result.recovered_source_refs)


def test_explicit_previous_page_signal_recovers_unique_continuation():
    page1 = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%",
        blocks=(_block("continuation", 1, 0, "expense = 100元\nratio = 80%", metadata={"continuation_of": "formula"}),),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="赔付金额 = expense * ratio。变量定义见上页",
        blocks=(_block("formula", 2, 0, "赔付金额 = expense * ratio。变量定义见上页", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence(page=2, context="变量定义见上页"))
    assert result.status is FormulaGateStatus.PASS
    assert "expense = 100元" in result.recovered_evidence.context_text
    assert any(ref.page_number == 1 for ref in result.recovered_source_refs)


def test_cross_document_continuation_is_rejected():
    page1 = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。变量定义见下页",
        blocks=(
            _block(
                "formula",
                1,
                0,
                "赔付金额 = expense * ratio。变量定义见下页",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
                metadata={"continuation_doc_id": "other_doc"},
            ),
        ),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="expense = 100元\nratio = 80%",
        blocks=(_block("continuation", 2, 0, "expense = 100元\nratio = 80%"),),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence(context="变量定义见下页"))
    assert result.status is FormulaGateStatus.FAIL
    assert "cross_document_recovery_forbidden" in result.reasons


def test_multiple_adjacent_page_continuations_stay_review():
    page1 = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。变量定义见下页",
        blocks=(_block("formula", 1, 0, "赔付金额 = expense * ratio。变量定义见下页", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="两个续块",
        blocks=(
            _block("c1", 2, 0, "expense = 100元", metadata={"continuation": True}),
            _block("c2", 2, 1, "ratio = 80%", metadata={"continuation": True}),
        ),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence(context="变量定义见下页"))
    assert result.status is FormulaGateStatus.REVIEW
    assert "adjacent_page_continuation_not_unique" in result.reasons


def test_recovered_source_refs_are_deduplicated_by_source_identity():
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    identities = [(ref.doc_id, ref.page_number, ref.source, ref.block_id) for ref in result.recovered_source_refs]
    assert len(identities) == len(set(identities))


def test_linked_table_footnote_is_recovered_with_lineage():
    table = CanonicalTable(
        table_id="t1",
        page_number=1,
        markdown="expense = 100元",
        footnote="ratio = 80%",
        lineage=_lineage(1),
    )
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio",
        blocks=(_block("formula", 1, 0, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),),
        tables=(table,),
        lineage=_lineage(1),
    )
    base = _evidence()
    evidence = FormulaEvidence(
        raw_formula=base.raw_formula,
        normalized_expression=base.normalized_expression,
        source_refs=base.source_refs,
        linked_table_refs=("t1",),
        metadata=base.metadata,
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(evidence)
    assert result.status is FormulaGateStatus.PASS
    assert "ratio = 80%" in result.recovered_evidence.context_text
    table_refs = [ref for ref in result.recovered_source_refs if ref.block_id == "t1"]
    assert len(table_refs) == 1
    assert table_refs[0].source == "doc://insurance_demo/page/1"


def test_recovery_does_not_bypass_gate_when_variable_still_missing():
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert result.gate_result is not None
    assert "missing_variable_binding:ratio" in result.gate_result.reasons
    assert result.ready_for_execution is False


# Executor self-check round 1: wrong-stitching attacks.


def test_neighbor_formula_is_not_stitched_without_linkage():
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio\nother = expense * 9",
        blocks=(
            _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("formula2", 1, 2, "other = expense * 9", block_type=CanonicalBlockType.FORMULA, formula_id="f2"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.PASS
    assert "other = expense * 9" not in result.recovered_evidence.context_text


def test_same_variable_name_in_unlinked_table_does_not_create_false_ambiguity():
    linked = CanonicalTable("t1", 1, markdown="expense = 100元", lineage=_lineage(1))
    unrelated = CanonicalTable("t2", 1, markdown="expense = 999元", lineage=_lineage(1))
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio\nratio = 80%",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("ratio", 1, 1, "ratio = 80%"),
            _block("t1b", 1, 2, "expense = 100元", block_type=CanonicalBlockType.TABLE, table_id="t1"),
            _block("t2b", 1, 3, "expense = 999元", block_type=CanonicalBlockType.TABLE, table_id="t2"),
        ),
        tables=(linked, unrelated),
        lineage=_lineage(1),
    )
    base = _evidence()
    evidence = FormulaEvidence(
        raw_formula=base.raw_formula,
        normalized_expression=base.normalized_expression,
        source_refs=base.source_refs,
        linked_table_refs=("t1",),
        metadata=base.metadata,
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(evidence)
    assert result.status is FormulaGateStatus.PASS
    assert "expense = 999元" not in result.recovered_evidence.context_text


def test_canonical_formula_block_table_linkage_is_written_back_to_recovered_evidence():
    table = CanonicalTable("t1", 1, markdown="expense = 100元", lineage=_lineage(1))
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。按下表确定。\nratio = 80%",
        blocks=(
            _block(
                "formula",
                1,
                0,
                "赔付金额 = expense * ratio。按下表确定。",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
                metadata={"linked_table_refs": ["t1"]},
            ),
            _block("ratio", 1, 1, "ratio = 80%"),
        ),
        tables=(table,),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.PASS
    assert result.recovered_evidence.linked_table_refs == ("t1",)


def test_participating_block_without_own_lineage_stays_review():
    missing_lineage = CanonicalBlock(
        block_id="vars",
        page_number=1,
        block_type=CanonicalBlockType.TEXT,
        text="expense = 100元\nratio = 80%",
        reading_order=0,
        lineage=None,
    )
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio",
        blocks=(
            missing_lineage,
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert "recovery_lineage_missing:block:vars" in result.reasons


def test_formula_id_that_is_not_unique_stays_review():
    page = CanonicalPage(
        page_number=1,
        text="duplicate formula ids",
        blocks=(
            _block("formula1", 1, 0, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("formula2", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    evidence = FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        source_refs=(FormulaSourceRef("insurance_demo", 1, "doc://insurance_demo/page/1"),),
        metadata={"domain": "insurance", "formula_id": "f1"},
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(evidence)
    assert result.status is FormulaGateStatus.REVIEW
    assert "formula_block_not_unique" in result.reasons


def test_formula_level_footnote_id_without_canonical_contract_stays_review():
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        lineage=_lineage(1),
    )
    base = _evidence()
    evidence = FormulaEvidence(
        raw_formula=base.raw_formula,
        normalized_expression=base.normalized_expression,
        source_refs=base.source_refs,
        metadata={**dict(base.metadata), "linked_footnote_ref": "fn1"},
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(evidence)
    assert result.status is FormulaGateStatus.REVIEW
    assert "unsupported_explicit_footnote_linkage" in result.reasons


# C3-B V1-R1 evaluator false-pass regressions.


def test_neighbor_next_page_marker_cannot_authorize_formula_cross_page_recovery():
    page1 = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio\n其他独立条款的说明见下页",
        blocks=(
            _block("expense", 1, 0, "expense = 100元"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("unrelated", 1, 2, "其他独立条款的说明见下页"),
        ),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="ratio = 80%",
        blocks=(_block("continuation", 2, 0, "ratio = 80%"),),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert result.ready_for_execution is False
    assert "missing_variable_binding:ratio" in result.reasons
    assert not any(step.action == "adjacent_page_continuation" for step in result.recovery_steps)


def test_neighbor_direction_conflict_is_not_formula_direction_ambiguity():
    page1 = CanonicalPage(
        page_number=1,
        text="见上页\n赔付金额 = expense * ratio\n见下页\nexpense = 100元",
        blocks=(
            _block("prev_note", 1, 0, "其他条款见上页"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("next_note", 1, 2, "其他条款见下页"),
            _block("expense", 1, 3, "expense = 100元"),
        ),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert "adjacent_page_direction_ambiguous" not in result.reasons
    assert "missing_variable_binding:ratio" in result.reasons


def test_canonical_formula_metadata_footnote_ref_must_review():
    formula = CanonicalFormula(
        "f1",
        1,
        "expense * ratio",
        lineage=_lineage(1),
        metadata={"linked_footnote_ref": "fn1"},
    )
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        formulas=(formula,),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert result.ready_for_execution is False
    assert "unsupported_explicit_footnote_linkage" in result.reasons


def test_canonical_formula_metadata_multiple_footnote_refs_must_review():
    formula = CanonicalFormula(
        "f1",
        1,
        "expense * ratio",
        lineage=_lineage(1),
        metadata={"linked_footnote_refs": ["fn1", "fn2"]},
    )
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\nratio = 80%\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        formulas=(formula,),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert "unsupported_explicit_footnote_linkage" in result.reasons


def test_canonical_formula_table_link_without_footnote_still_passes():
    table = CanonicalTable("t1", 1, markdown="expense = 100元", lineage=_lineage(1))
    formula = CanonicalFormula(
        "f1",
        1,
        "expense * ratio",
        lineage=_lineage(1),
        metadata={"linked_table_refs": ["t1"]},
    )
    page = CanonicalPage(
        page_number=1,
        text="赔付金额 = expense * ratio。按下表确定。\nratio = 80%",
        blocks=(
            _block("formula", 1, 0, "赔付金额 = expense * ratio。按下表确定。", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
            _block("ratio", 1, 1, "ratio = 80%"),
        ),
        tables=(table,),
        formulas=(formula,),
        lineage=_lineage(1),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.PASS
    assert result.ready_for_execution is True
    assert result.recovered_evidence.linked_table_refs == ("t1",)


def test_anchor_explicit_continuation_metadata_can_authorize_without_text_marker():
    page1 = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio",
        blocks=(
            _block("expense", 1, 0, "expense = 100元"),
            _block(
                "formula",
                1,
                1,
                "赔付金额 = expense * ratio",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
                metadata={"continuation_block_id": "ratio_cont"},
            ),
        ),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="ratio = 80%",
        blocks=(_block("ratio_cont", 2, 0, "ratio = 80%"),),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.PASS
    assert result.ready_for_execution is True
    assert any(step.action == "adjacent_page_continuation" for step in result.recovery_steps)


# R1 self-check round 1: authorization-source attacks.


def test_explicit_linked_continuation_ignores_unlinked_adjacent_block():
    page1 = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio。变量见下页",
        blocks=(
            _block("expense", 1, 0, "expense = 100元"),
            _block(
                "formula",
                1,
                1,
                "赔付金额 = expense * ratio。变量见下页",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
                metadata={"continuation_block_id": "ratio_cont"},
            ),
        ),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="ratio = 80%\nratio = 5%",
        blocks=(
            _block("ratio_cont", 2, 0, "ratio = 80%"),
            _block("unrelated", 2, 1, "ratio = 5%"),
        ),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.PASS
    assert "ratio = 80%" in result.recovered_evidence.context_text
    assert "ratio = 5%" not in result.recovered_evidence.context_text


def test_block_and_formula_continuation_metadata_conflict_stays_review():
    formula = CanonicalFormula(
        "f1",
        1,
        "expense * ratio",
        lineage=_lineage(1),
        metadata={"continuation_block_id": "formula_target"},
    )
    page1 = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio",
        blocks=(
            _block("expense", 1, 0, "expense = 100元"),
            _block(
                "formula",
                1,
                1,
                "赔付金额 = expense * ratio",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
                metadata={"continuation_block_id": "block_target"},
            ),
        ),
        formulas=(formula,),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="two explicit targets",
        blocks=(
            _block("block_target", 2, 0, "ratio = 80%"),
            _block("formula_target", 2, 1, "ratio = 70%"),
        ),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.REVIEW
    assert "adjacent_page_continuation_not_unique" in result.reasons
    assert result.ready_for_execution is False


def test_anchor_next_marker_does_not_follow_metadata_target_on_previous_page():
    page1 = CanonicalPage(
        page_number=1,
        text="ratio = 80%",
        blocks=(_block("ratio_prev", 1, 0, "ratio = 80%"),),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="expense = 100元\n赔付金额 = expense * ratio。变量见下页",
        blocks=(
            _block("expense", 2, 0, "expense = 100元"),
            _block(
                "formula",
                2,
                1,
                "赔付金额 = expense * ratio。变量见下页",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
                metadata={"continuation_block_id": "ratio_prev"},
            ),
        ),
        lineage=_lineage(2),
    )
    page3 = CanonicalPage(page_number=3, text="unrelated", blocks=(), lineage=_lineage(3))
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2, page3),))
    ).recover(_evidence(page=2))
    assert result.status is FormulaGateStatus.REVIEW
    assert "adjacent_page_continuation_not_found" in result.reasons
    assert "missing_variable_binding:ratio" in result.reasons


def test_canonical_formula_cross_document_continuation_metadata_is_rejected():
    formula = CanonicalFormula(
        "f1",
        1,
        "expense * ratio",
        lineage=_lineage(1),
        metadata={
            "continuation_doc_id": "other_doc",
            "continuation_block_id": "ratio_cont",
        },
    )
    page1 = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio",
        blocks=(
            _block("expense", 1, 0, "expense = 100元"),
            _block("formula", 1, 1, "赔付金额 = expense * ratio", block_type=CanonicalBlockType.FORMULA, formula_id="f1"),
        ),
        formulas=(formula,),
        lineage=_lineage(1),
    )
    page2 = CanonicalPage(
        page_number=2,
        text="ratio = 80%",
        blocks=(_block("ratio_cont", 2, 0, "ratio = 80%"),),
        lineage=_lineage(2),
    )
    result = FormulaContextRecovery(
        InMemoryDocumentStore.from_documents((_document(page1, page2),))
    ).recover(_evidence())
    assert result.status is FormulaGateStatus.FAIL
    assert "cross_document_recovery_forbidden" in result.reasons
    assert result.ready_for_execution is False
