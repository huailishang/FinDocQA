from __future__ import annotations

from pathlib import Path

from agent.classifier import RuleBasedQuestionClassifier
from agent.factory import PipelineFactory
from agent.workflow import EnhancedBaselineWorkflow
from contracts import EvidenceBundle, EvidenceCandidate, QuestionLabel, SolverResult
from evidence.assembler import GroupedEvidenceAssembler
from question.preparation import QuestionPreparationPipeline
from solvers.calculation import CalculationSolver
from solvers.cross_doc import CrossDocSolver
from solvers.direct import DirectSolver
from solvers.router import RoutedSolver


class StubSolver:
    def __init__(self, name: str) -> None:
        self.name = name

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        return SolverResult(qid=bundle.question.qid, answer=self.name, solver=self.name)


class OpenQARetriever:
    def retrieve(self, question, classification):
        return [
            EvidenceCandidate(
                domain=question.domain,
                doc_id="annual_demo_2024_report",
                source="canonical://financial_reports/annual_demo_2024_report/page/1",
                text="2024年不良贷款率为1.25%。",
                metadata={"page_number": 1},
            )
        ]


class OpenQASolver:
    name = "direct"

    def solve(self, bundle: EvidenceBundle) -> SolverResult:
        return SolverResult(
            qid=bundle.question.qid,
            answer="1.25%",
            solver=self.name,
            raw_output="1.25%",
            confidence=0.9,
            metadata={
                "answer_source": "generated",
                "provider_call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "truncation_risk": False,
            },
        )


def _bundle(prepared):
    classifier = RuleBasedQuestionClassifier()
    classification = classifier.classify(prepared.question)
    return EvidenceBundle(
        question=prepared.question,
        classification=classification,
        candidates=(),
        prompt_context="",
        estimated_tokens=0,
    )


def test_plain_query_becomes_freeform_financial_calculation() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "2024年招商银行营业收入同比增长多少？"
    )

    assert prepared.question.qid.startswith("query_")
    assert prepared.question.options == {}
    assert prepared.question.answer_format == "freeform"
    assert prepared.question.domain == "financial_reports"
    assert prepared.understanding.base_type == "calculation"
    assert prepared.understanding.answer_shape == "number"
    assert "calculation" in prepared.understanding.traits
    assert "temporal_scope" in prepared.understanding.traits


def test_same_company_year_comparison_is_not_forced_cross_document() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "比较招商银行2024年和2023年不良贷款率，是否上升？"
    )
    bundle = _bundle(prepared)

    assert prepared.question.domain == "financial_reports"
    assert prepared.understanding.answer_shape == "boolean"
    assert "comparison" in prepared.understanding.traits
    assert "cross_document" not in prepared.understanding.traits
    assert QuestionLabel.CROSS_DOC not in bundle.classification.labels


def test_explicit_multi_document_natural_query_routes_cross_doc_solver() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "比较两家公司2024年年报的净利润，哪家更高？"
    )
    bundle = _bundle(prepared)
    router = RoutedSolver(
        solvers={
            "calculation": StubSolver("calculation"),
            "cross_doc": StubSolver("cross_doc"),
        },
        default_solver=StubSolver("direct"),
    )

    result = router.solve(bundle)

    assert prepared.question.domain == "financial_reports"
    assert "cross_document" in prepared.understanding.traits
    assert QuestionLabel.CROSS_DOC in bundle.classification.labels
    assert result.solver == "cross_doc"


def test_natural_calculation_query_routes_calculation_solver_without_options() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "某公司2024年营业收入为120亿元，2023年为100亿元，同比增长率是多少？"
    )
    bundle = _bundle(prepared)
    router = RoutedSolver(
        solvers={
            "calculation": StubSolver("calculation"),
            "cross_doc": StubSolver("cross_doc"),
        },
        default_solver=StubSolver("direct"),
    )

    result = router.solve(bundle)

    assert QuestionLabel.CALCULATION in bundle.classification.labels
    assert QuestionLabel.CROSS_DOC not in bundle.classification.labels
    assert result.solver == "calculation"


def test_afac_structured_question_preserves_explicit_contract() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        {
            "qid": "demo_multi",
            "domain": "regulatory",
            "question": "根据规定，下列哪些说法正确？",
            "type": "多选题",
            "answer_format": "multi",
            "options": {"A": "说法A", "B": "说法B", "C": "说法C"},
            "doc_ids": ["reg001"],
        }
    )

    assert prepared.question.qid == "demo_multi"
    assert prepared.question.domain == "regulatory"
    assert prepared.question.answer_format == "multi"
    assert prepared.question.options["A"] == "说法A"
    assert prepared.understanding.base_type == "multi_choice"
    assert prepared.understanding.answer_shape == "choice_set"


def test_factory_exposes_question_preparation_entrypoint(tmp_path: Path) -> None:
    factory = PipelineFactory(config={}, project_root=tmp_path)

    prepared = factory.prepare_question("保险合同等待期内发生事故是否赔付？")

    assert prepared.question.domain == "insurance"
    assert prepared.question.answer_format == "freeform"
    assert prepared.understanding.base_type == "judgement"
    assert prepared.understanding.answer_shape == "boolean"


def test_direct_solver_uses_open_qa_prompt_without_options() -> None:
    prepared = QuestionPreparationPipeline().prepare("招商银行2024年不良贷款率是多少？")
    bundle = EvidenceBundle(
        question=prepared.question,
        classification=RuleBasedQuestionClassifier().classify(prepared.question),
        candidates=(),
        prompt_context="证据示例",
        estimated_tokens=0,
    )

    prompt = DirectSolver()._build_prompt(bundle)

    assert "金融长文档问答助手" in prompt
    assert "不要强行转换成 A/B/C/D" in prompt
    assert "证据不足时明确说明" in prompt
    assert "选项：" not in prompt


def test_cross_doc_freeform_keeps_natural_final_answer() -> None:
    assert CrossDocSolver._extract_answer(
        "比较结论: A公司更高\n最终答案: A公司",
        "freeform",
    ) == "A公司"


def test_scope_identity_groups_can_promote_natural_comparison_to_cross_doc() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "比较甲公司和乙公司2024年净利润，哪家更高？"
    )
    classification = RuleBasedQuestionClassifier().classify(prepared.question)
    groups = [
        {"kind": "entity_year", "identity": "甲公司", "doc_ids": ["annual_a_2024"]},
        {"kind": "entity_year", "identity": "乙公司", "doc_ids": ["annual_b_2024"]},
    ]
    candidates = tuple(
        EvidenceCandidate(
            domain="financial_reports",
            doc_id=doc_id,
            source=f"canonical://financial_reports/{doc_id}/page/1",
            text="净利润证据",
            metadata={"document_scope_coverage_groups": groups},
        )
        for doc_id in ("annual_a_2024", "annual_b_2024")
    )
    bundle = EvidenceBundle(
        question=prepared.question,
        classification=classification,
        candidates=candidates,
        prompt_context="",
        estimated_tokens=0,
    )
    router = RoutedSolver(
        solvers={"cross_doc": StubSolver("cross_doc")},
        default_solver=StubSolver("direct"),
    )

    assert QuestionLabel.CROSS_DOC not in classification.labels
    assert router.solve(bundle).solver == "cross_doc"


def test_long_form_natural_question_gets_larger_direct_budget() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "为什么这家公司的净利润下降？请分析主要原因。"
    )
    bundle = _bundle(prepared)

    assert prepared.understanding.answer_shape == "long_text"
    # Keep long-form Open QA below the current freeform output-length gate while
    # still allowing materially more room than a normal direct answer.
    assert DirectSolver._max_tokens(bundle) == 384
    assert "关键事实解释原因" in DirectSolver()._build_prompt(bundle)


def test_generic_calculation_does_not_require_competition_slot_contract() -> None:
    prepared = QuestionPreparationPipeline().prepare(
        "某公司2024年营业收入为120亿元，2023年为100亿元，同比增长率是多少？"
    )
    bundle = _bundle(prepared)

    result = CalculationSolver().solve(bundle)

    assert result.metadata["freeform_parse_reason"] == "llm_client_unavailable"
    assert result.metadata["expected_submission_slots"] == 1


def test_generic_open_qa_runs_through_production_workflow() -> None:
    prepared = QuestionPreparationPipeline().prepare("招商银行2024年不良贷款率是多少？")
    workflow = EnhancedBaselineWorkflow(
        classifier=RuleBasedQuestionClassifier(),
        retriever=OpenQARetriever(),
        assembler=GroupedEvidenceAssembler(),
        solver=OpenQASolver(),
        verifier=None,
        self_check_verifier=None,
        fallback_solver=None,
        enforce_production_integrity=True,
    )

    result = workflow.process_one(prepared.question)

    assert result.answer == "1.25%"
    assert result.answer_values == ("1.25%",)
    assert result.submission_answers == ("1.25%",)
    assert result.metadata["production_integrity_path"] == "generic_open_qa"
    assert result.metadata["final_state"] == "accepted"
    assert result.metadata["submission_slot_count"] == 1
