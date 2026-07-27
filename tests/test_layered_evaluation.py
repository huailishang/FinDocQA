from decimal import Decimal

from contracts import EvidenceCandidate
from document import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalFormula,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from evaluation.layers import (
    ParserGoldPage,
    ReasoningGold,
    RetrievalGold,
    evaluate_answer,
    evaluate_parser,
    evaluate_reasoning,
    evaluate_retrieval,
    evaluate_retrieval_strategy,
)


def test_four_evaluation_layers() -> None:
    lineage = SourceLineage(
        source_type="test",
        source_path="doc.pdf",
        parser_name="fixture",
        page_number=1,
        source_page_index=0,
    )
    table = CanonicalTable(
        table_id="t1",
        page_number=1,
        headers=("项目", "金额"),
        rows=(("收入", "100"),),
        lineage=lineage,
    )
    formula = CanonicalFormula(
        formula_id="f1",
        page_number=1,
        expression="growth=(100-80)/80",
        lineage=lineage,
    )
    block = CanonicalBlock(
        block_id="b1",
        page_number=1,
        block_type=CanonicalBlockType.TEXT,
        text="2025年营业收入100亿元",
        lineage=lineage,
    )
    page = CanonicalPage(
        page_number=1,
        text="2025年营业收入100亿元",
        blocks=(block,),
        tables=(table,),
        formulas=(formula,),
        lineage=lineage,
    )
    document = CanonicalDocument(
        document_id="d1",
        domain="financial_reports",
        title="年报",
        source_type="test",
        source_uri="doc.pdf",
        parser_name="fixture",
        parser_version="1",
        pages=(page,),
    )

    parser_result = evaluate_parser(
        document,
        [
            ParserGoldPage(
                page_number=1,
                text_anchors=("营业收入",),
                table_headers=(("项目", "金额"),),
                formula_anchors=("growth=",),
            )
        ],
    )
    assert parser_result.page_recall == 1.0
    assert parser_result.table_header_recall == 1.0

    candidate = EvidenceCandidate(
        domain="financial_reports",
        doc_id="d1",
        source="canonical://financial_reports/d1/page/1",
        text="2025年营业收入100亿元",
        metadata={"page_number": 1},
    )
    retrieval_result = evaluate_retrieval(
        [candidate],
        RetrievalGold(
            required_doc_ids=("d1",),
            required_pages={"d1": (1,)},
            evidence_text_anchors=("营业收入",),
        ),
        k=5,
    )
    assert retrieval_result.document_recall_at_k == 1.0
    assert retrieval_result.page_recall_at_k == 1.0

    reasoning_result = evaluate_reasoning(
        predicted_claim_verdicts={"A": "SUPPORT"},
        predicted_numeric_values={"x": "100.0000001"},
        predicted_formulas={"g": "growth = (100-80) / 80"},
        gold=ReasoningGold(
            claim_verdicts={"A": "SUPPORT"},
            numeric_values={"x": "100"},
            formulas={"g": "growth=(100-80)/80"},
        ),
        numeric_tolerance=Decimal("0.001"),
    )
    assert reasoning_result.claim_accuracy == 1.0
    assert reasoning_result.numeric_accuracy == 1.0
    assert reasoning_result.formula_accuracy == 1.0

    answer_result = evaluate_answer("AC", "AC")
    assert answer_result.exact_match == 1.0
    assert answer_result.set_f1 == 1.0


def test_retrieval_ranking_and_equivalent_page_groups() -> None:
    candidates = [
        EvidenceCandidate(
            domain="financial_contracts",
            doc_id="doc1",
            source="canonical://financial_contracts/doc1/page/5",
            text="目录和声明页",
            metadata={"page_number": 5},
        ),
        EvidenceCandidate(
            domain="financial_contracts",
            doc_id="doc1",
            source="canonical://financial_contracts/doc1/page/153",
            text="评估增值率为1468.47%，加期评估增值率为740.58%。",
            metadata={"page_number": 153},
        ),
        EvidenceCandidate(
            domain="financial_contracts",
            doc_id="doc2",
            source="canonical://financial_contracts/doc2/page/1",
            text="无关文本",
            metadata={"page_number": 1},
        ),
    ]
    gold = RetrievalGold(
        required_doc_ids=("doc1",),
        evidence_text_anchors=("1468.47", "740.58"),
        acceptable_page_groups=(
            (("doc1", 30), ("doc1", 153), ("doc1", 220)),
        ),
    )

    result = evaluate_retrieval(candidates, gold, k=3)

    assert result.acceptable_page_group_recall_at_k == 1.0
    assert result.evidence_anchor_recall_at_k == 1.0
    assert result.reciprocal_rank_at_k == 0.5
    assert result.ndcg_at_k is not None
    assert 0.0 < result.ndcg_at_k < 1.0

    strategy = evaluate_retrieval_strategy(
        "lexical",
        candidates,
        gold,
        k=3,
        latency_ms=12.5,
        api_calls=0,
        estimated_cost=0.0,
    )
    payload = strategy.to_dict()
    assert payload["strategy"] == "lexical"
    assert payload["latency_ms"] == 12.5
    assert payload["api_calls"] == 0
    assert payload["acceptable_page_group_recall_at_k"] == 1.0


def test_retrieval_ranking_does_not_reward_anchor_on_wrong_gold_page() -> None:
    candidates = [
        EvidenceCandidate(
            domain="financial_reports",
            doc_id="doc1",
            source="canonical://financial_reports/doc1/page/1",
            text="营业收入为100亿元，但这是摘要重复页。",
            metadata={"page_number": 1},
        ),
        EvidenceCandidate(
            domain="financial_reports",
            doc_id="doc1",
            source="canonical://financial_reports/doc1/page/8",
            text="营业收入为100亿元。",
            metadata={"page_number": 8},
        ),
    ]
    gold = RetrievalGold(
        required_doc_ids=("doc1",),
        required_pages={"doc1": (8,)},
        evidence_text_anchors=("营业收入",),
    )

    result = evaluate_retrieval(candidates, gold, k=2)

    assert result.page_recall_at_k == 1.0
    assert result.evidence_anchor_recall_at_k == 1.0
    assert result.reciprocal_rank_at_k == 0.5
    assert result.ndcg_at_k is not None
    assert 0.0 < result.ndcg_at_k < 1.0
