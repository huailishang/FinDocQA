from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from classification.question_strategy import QuestionStrategyMatrix
from contracts import ClassificationResult, EvidenceCandidate, Question, QuestionLabel
from evidence.minimal_sufficient_set import (
    CoverageRegressionError,
    MinimalSufficientEvidenceReducer,
    ensure_no_coverage_regression,
)
from evidence.prompt_evidence_selection import GlobalPromptEvidenceSelector, PromptEvidencePolicy
from verification.claim_atoms import atomize_claim


MATRIX_PATH = ROOT / "config" / "question_strategy_matrix.json"


def _question(
    *,
    qid: str = "opaque_identifier",
    domain: str = "financial_reports",
    text: str,
    raw_type: str,
    answer_format: str,
    options: dict[str, str] | None = None,
    doc_ids: tuple[str, ...] = (),
    candidate_doc_ids: tuple[str, ...] = (),
    submission_slot_count: int | None = None,
) -> Question:
    return Question(
        qid=qid,
        domain=domain,
        text=text,
        options=options or {},
        answer_format=answer_format,
        doc_ids=doc_ids,
        candidate_doc_ids=candidate_doc_ids,
        submission_slot_count=submission_slot_count,
        raw={"type": raw_type},
    )


def _candidate(
    doc_id: str,
    source: str,
    text: str,
    score: float,
    *,
    option_focus: str = "",
    matched_terms: tuple[str, ...] = (),
) -> EvidenceCandidate:
    metadata = {
        "page_number": int(re.search(r"(\d+)", source).group(1)) if re.search(r"(\d+)", source) else 1,
        "matched_terms": list(matched_terms),
    }
    if option_focus:
        metadata["option_focus"] = option_focus
    return EvidenceCandidate(
        domain="financial_reports",
        doc_id=doc_id,
        source=source,
        text=text,
        score=score,
        retriever="fixture",
        metadata=metadata,
    )


def _selection(question: Question, classification: ClassificationResult, candidates):
    selector = GlobalPromptEvidenceSelector(
        PromptEvidencePolicy(
            max_context_chars=30_000,
            max_candidates=20,
            min_candidates_per_doc=1,
            main_doc_max_candidates=10,
            other_doc_max_candidates=10,
        )
    )
    return selector.select(
        question,
        classification,
        candidates,
        scope_candidate_doc_ids=question.doc_ids or question.candidate_doc_ids,
    )


def test_strategy_supports_required_base_and_compound_tags() -> None:
    matrix = QuestionStrategyMatrix.from_file(MATRIX_PATH)
    question = _question(
        text=(
            "结合两份2024年和2025年报告，在满足监管条件的情况下，计算两家公司指标，"
            "比较后从高到低排序，并指出不得包括哪一项。"
        ),
        raw_type="计算题",
        answer_format="freeform",
        doc_ids=("doc_1", "doc_2"),
        submission_slot_count=2,
    )
    strategy = matrix.recommend(question)
    required = {
        "calculation",
        "cross_document",
        "comparison",
        "ranking",
        "negation",
        "exception_or_condition",
        "multi_slot",
    }
    assert required.issubset(set(strategy.question_tags))
    assert strategy.doc_top_k_hint >= 9
    assert strategy.window_top_k_hint >= 42
    assert strategy.solver_hint == "composite"
    assert strategy.evidence_budget_hint["prompt_chars"] >= 54_000
    assert "calculation_grounding" in strategy.verification_requirements
    assert "cross_doc_completeness" in strategy.verification_requirements
    assert strategy.production_enabled is False
    assert strategy.strategy_reason


@pytest.mark.parametrize(
    ("raw_type", "answer_format", "options", "expected"),
    [
        ("单选题", "single", {"A": "甲", "B": "乙"}, "single_choice"),
        ("多选题", "multi", {"A": "甲", "B": "乙"}, "multi_choice"),
        ("判断题", "tf", {"A": "正确", "B": "错误"}, "judgement"),
        ("抽取题", "freeform", {}, "extraction"),
    ],
)
def test_strategy_exposes_all_required_base_types(raw_type, answer_format, options, expected) -> None:
    strategy = QuestionStrategyMatrix.from_file(MATRIX_PATH).recommend(
        _question(text="根据材料作答。", raw_type=raw_type, answer_format=answer_format, options=options)
    )
    assert expected in strategy.question_tags


def test_candidate_scope_does_not_become_cross_document_truth() -> None:
    matrix = QuestionStrategyMatrix.from_file(MATRIX_PATH)
    question = _question(
        domain="research",
        text="根据材料判断说法是否正确。",
        raw_type="判断题",
        answer_format="tf",
        options={"A": "正确", "B": "错误"},
        candidate_doc_ids=("candidate_1", "candidate_2", "candidate_3"),
    )
    assert "cross_document" not in matrix.recommend(question).question_tags


def test_low_confidence_marks_but_still_returns_conservative_strategy() -> None:
    matrix = QuestionStrategyMatrix.from_file(MATRIX_PATH)
    strategy = matrix.recommend(
        _question(
            domain="unknown_domain",
            text="请处理该问题。",
            raw_type="mystery",
            answer_format="unknown",
        )
    )
    assert strategy.low_confidence is True
    assert "low_confidence" in strategy.question_tags
    assert strategy.doc_top_k_hint >= 10
    assert strategy.retrieval_depth_hint == "deep"


def test_minimal_evidence_preserves_lineage_options_numbers_formula_and_conditions() -> None:
    question = _question(
        text="比较2024年与2025年营业收入增长率；若满足条件，例外不得忽略。",
        raw_type="计算题",
        answer_format="multi",
        options={"A": "增长至少10%", "B": "下降5%"},
        doc_ids=("doc_a", "doc_b"),
    )
    classification = ClassificationResult(
        labels=(QuestionLabel.CROSS_DOC, QuestionLabel.CALCULATION, QuestionLabel.MULTI_OPTION)
    )
    candidates = [
        _candidate(
            "doc_a",
            "doc_a/page_0001.md",
            "2025年营业收入100亿元，2024年90亿元，增长率=(100-90)/90=11.11%，单位：亿元。",
            100,
            option_focus="A",
            matched_terms=("2025年", "营业收入", "增长率"),
        ),
        _candidate(
            "doc_a",
            "doc_a/page_0002.md",
            "2025年营业收入100亿元，增长率11.11%。",
            90,
            matched_terms=("2025年", "营业收入"),
        ),
        _candidate(
            "doc_b",
            "doc_b/page_0003.md",
            "2024年营业收入90亿元；若满足条件，应当核对，但例外不得忽略。",
            95,
            option_focus="B",
            matched_terms=("2024年", "营业收入"),
        ),
        _candidate("doc_b", "doc_b/page_0004.md", "普通背景，重复且无额外答案事实。", 10),
    ]
    baseline = _selection(question, classification, candidates)
    result = MinimalSufficientEvidenceReducer().reduce(question, classification, baseline)

    assert result.minimal_candidate_count < result.baseline_candidate_count
    assert result.coverage_regression_count == 0
    assert result.minimal_context_chars < result.baseline_context_chars
    assert {candidate.doc_id for candidate in result.selected_candidates} == {"doc_a", "doc_b"}
    retained = "\n".join(candidate.text for candidate in result.selected_candidates)
    assert "2024年" in retained and "2025年" in retained
    assert "增长率" in retained and "亿元" in retained
    assert "若满足条件" in retained
    assert "例外" in retained and "不得" in retained
    categories = {item.category for item in result.requirements}
    assert "lineage" in categories
    assert "option_focus" in categories
    assert "protected_original" in categories


def test_minimal_evidence_is_qid_invariant_and_subset_only() -> None:
    question = _question(
        qid="first_opaque_identifier",
        text="2025年收入100亿元，计算同比增长率。",
        raw_type="计算题",
        answer_format="freeform",
        doc_ids=("doc_a",),
    )
    classification = ClassificationResult(labels=(QuestionLabel.CALCULATION,))
    candidates = [
        _candidate(
            "doc_a",
            "doc_a/page_0001.md",
            "2025年收入100亿元，2024年收入90亿元，增长率=(100-90)/90。",
            50,
            matched_terms=("2025年", "收入", "增长率"),
        ),
        _candidate("doc_a", "doc_a/page_0002.md", "一般经营背景。", 20),
        _candidate("doc_a", "doc_a/page_0003.md", "一般经营背景重复。", 10),
    ]
    first_baseline = _selection(question, classification, candidates)
    second_question = replace(question, qid="second_opaque_identifier")
    second_baseline = _selection(second_question, classification, candidates)
    reducer = MinimalSufficientEvidenceReducer()
    first = reducer.reduce(question, classification, first_baseline)
    second = reducer.reduce(second_question, classification, second_baseline)
    assert first.retained_sources == second.retained_sources
    assert set(first.retained_sources).issubset(
        {candidate.source for candidate in first_baseline.selected_candidates}
    )


def test_coverage_regression_contract_is_fail_closed() -> None:
    with pytest.raises(CoverageRegressionError, match="lost mandatory coverage"):
        ensure_no_coverage_regression(("lineage:doc_a", "protected_original:negation"))


def test_claim_atomizer_splits_multi_fact_condition_quantifier_and_negation() -> None:
    text = (
        "在2025年满足监管条件的情况下，甲公司营业收入至少100亿元且净利润不低于20亿元；"
        "除无民事行为能力人的情形外，公司不得在2年内解除合同。"
    )
    result = atomize_claim(text)
    assert result.provider_calls == 0
    assert len(result.atoms) >= 3

    revenue = next(atom for atom in result.atoms if atom.object_or_metric == "营业收入")
    profit = next(atom for atom in result.atoms if atom.object_or_metric == "净利润")
    prohibition = next(atom for atom in result.atoms if atom.relation == "prohibited")

    assert revenue.subject == "甲公司"
    assert revenue.value == "100" and revenue.unit == "亿元"
    assert revenue.relation == ">="
    assert "2025年" in revenue.time_scope
    assert "监管条件" in revenue.condition

    assert profit.subject == "甲公司"
    assert profit.value == "20" and profit.unit == "亿元"
    assert profit.relation == ">="

    assert prohibition.polarity == "negative"
    assert "解除合同" in prohibition.object_or_metric
    assert "2年内" in prohibition.time_scope
    assert "无民事行为能力" in prohibition.exception
    assert prohibition.source_text == text


def test_claim_atomizer_handles_upper_and_lower_bounds_deterministically() -> None:
    first = atomize_claim("甲机构收费标准不超过5万元，利率高于3.5%。")
    second = atomize_claim("甲机构收费标准不超过5万元，利率高于3.5%。")
    assert first.to_dict() == second.to_dict()
    assert first.deterministic is True
    relations = {(atom.object_or_metric, atom.relation, atom.value, atom.unit) for atom in first.atoms}
    assert ("收费标准", "<=", "5", "万元") in relations
    assert ("利率", ">", "3.5", "%") in relations


def test_p11_modules_have_no_specific_qid_or_answer_hardcoding() -> None:
    pattern = re.compile(r"(?:fc|fin|ins|reg|res)_[ab]_\d{3}", re.IGNORECASE)
    paths = (
        ROOT / "src/classification/question_strategy.py",
        ROOT / "config/question_strategy_matrix.json",
        ROOT / "src/evidence/minimal_sufficient_set.py",
        ROOT / "src/verification/claim_atoms.py",
    )
    for path in paths:
        assert pattern.search(path.read_text(encoding="utf-8")) is None, path


def test_p11_shadow_components_are_not_production_wired() -> None:
    for relative in (
        "src/agent/factory.py",
        "src/agent/workflow.py",
        "src/evidence/enhanced_assembler.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "QuestionStrategyMatrix" not in text
        assert "minimal_sufficient_set" not in text
        assert "claim_atoms" not in text
