from contracts import ClassificationResult, EvidenceBundle, EvidenceCandidate, Question, QuestionLabel
from solvers.base import conservative_used_doc_lineage
from solvers.cross_doc import CrossDocSolver


def bundle(doc_ids):
    q = Question(
        qid="q",
        domain="test",
        text="compare",
        options={"A":"a","B":"b","C":"c","D":"d"},
        answer_format="mcq",
        doc_ids=doc_ids,
    )
    candidates = [
        EvidenceCandidate(domain="test", doc_id=doc_id, source=f"{doc_id}.md", text="x")
        for doc_id in doc_ids
    ]
    return EvidenceBundle(
        question=q,
        classification=ClassificationResult(labels=[QuestionLabel.CROSS_DOC]),
        candidates=candidates,
        prompt_context="context",
        estimated_tokens=1,
    )


def test_single_document_lineage_is_conservative_and_known():
    used, source = conservative_used_doc_lineage(bundle(["d1"]))
    assert used == ["d1"]
    assert source == "single_document_prompt_context"


def test_multi_document_lineage_is_unknown_without_explicit_output():
    used, source = conservative_used_doc_lineage(bundle(["d1", "d2"]))
    assert used == []
    assert source == "unknown"


def test_cross_doc_solver_extracts_only_explicit_document_summaries():
    raw = """文档要点:\n- 文档 d1: first\n- 文档 d2: second\n比较结论: ok\n最终答案: A"""
    assert CrossDocSolver._extract_used_doc_ids(raw, bundle(["d1", "d2", "d3"])) == ["d1", "d2"]


def test_explicit_used_doc_declaration_filters_unknown_ids():
    from solvers.base import extract_declared_used_doc_ids

    raw = "使用文档：d1, fake_doc, d2\n最终答案：A"
    assert extract_declared_used_doc_ids(raw, bundle(["d1", "d2", "d3"])) == ["d1", "d2"]


def test_multi_choice_prompt_requires_used_doc_declaration():
    from solvers.multi_choice import MultiChoiceSolver

    prompt = MultiChoiceSolver()._build_prompt(bundle(["d1", "d2"]))
    assert "第一行必须输出：使用文档" in prompt
    assert "d1, d2" in prompt


def test_calculation_prompt_requires_used_doc_declaration():
    from solvers.calculation import CalculationSolver

    prompt = CalculationSolver()._build_extract_prompt(bundle(["d1", "d2"]))
    assert "第一行必须输出：使用文档" in prompt
    assert "d1, d2" in prompt
