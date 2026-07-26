import json
from pathlib import Path

from contracts import (
    ClassificationResult,
    EvidenceBundle,
    PipelineResult,
    Question,
    QuestionLabel,
    SolverResult,
)
from document.adapters.text import canonical_from_markdown_file
from document.store import InMemoryDocumentStore
from result_output import JsonResultWriter, ResultRecord
from retrieval.canonical_lexical import CanonicalLexicalEvidenceRetriever


def test_shadow_modular_pipeline_connects_end_to_end(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text(
        "# 2025年度报告\n\n2025年营业收入为100亿元，净利润为20亿元。\n",
        encoding="utf-8",
    )

    # A. Input/Document module
    document = canonical_from_markdown_file(
        source,
        domain="financial_reports",
        doc_id="report_2025",
    )
    store = InMemoryDocumentStore.from_documents([document])

    question = Question(
        qid="local_shadow",
        domain="financial_reports",
        text="2025年营业收入是多少？",
        options={},
        answer_format="freeform",
        doc_ids=("report_2025",),
    )
    classification = ClassificationResult(labels=(QuestionLabel.FACT_LOOKUP,))

    # B. Retrieval module
    candidates = CanonicalLexicalEvidenceRetriever(store=store).retrieve(
        question,
        classification,
    )
    assert candidates and "营业收入" in candidates[0].text

    # C. Solver/verification boundary. Use deterministic fixture output here;
    # the purpose is interface integration, not LLM quality.
    bundle = EvidenceBundle(
        question=question,
        classification=classification,
        candidates=candidates,
        prompt_context=candidates[0].text,
        estimated_tokens=20,
    )
    assert "100亿元" in bundle.prompt_context
    solver = SolverResult(
        qid=question.qid,
        answer="100",
        solver="deterministic_fixture",
        metadata={"used_doc_ids": ["report_2025"]},
    )
    pipeline = PipelineResult(
        qid=question.qid,
        answer="100",
        answer_values=("100",),
        classification=classification,
        solver_result=solver,
        reasoning="Evidence states 2025 revenue is 100亿元.",
        metadata={"answer_format": "freeform"},
    )

    # D. Output module
    record = ResultRecord.from_pipeline_result(pipeline)
    output_path = tmp_path / "result.json"
    JsonResultWriter(output_path).write([record])
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload[0]["question_id"] == "local_shadow"
    assert payload[0]["primary_answer"] == "100"
    assert payload[0]["answer_values"] == ["100"]
    assert "submission" not in json.dumps(payload[0], ensure_ascii=False).lower()
