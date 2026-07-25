from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.classifier import RuleBasedQuestionClassifier
from agent.workflow import BlockingAnswerValidationError, EnhancedBaselineWorkflow
from contracts import (
    ClassificationResult,
    EvidenceCandidate,
    PipelineResult,
    Question,
    QuestionLabel,
    SolverResult,
    VerificationResult,
)
from evidence.assembler import GroupedEvidenceAssembler
from run import load_checkpoint, save_checkpoint
from solvers.calculation import CalculationSolver
from utils.llm_client import ChatResult, ChatUsage
from verification.production_integrity import assess_final_state


class FakeClient:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.call_count = 0

    def chat(self, messages, max_tokens: int = 256) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=self.content,
            model="fake-freeform-workflow",
            finish_reason=self.finish_reason,
            usage=ChatUsage(prompt_tokens=11, completion_tokens=22, total_tokens=33),
            provider="fake",
        )


class FixtureRetriever:
    def retrieve(self, question, classification):
        return [
            EvidenceCandidate(
                domain=question.domain,
                doc_id="doc1",
                source="doc1#page=1",
                text="x=10，y=2。",
            )
        ]


def _payload(values: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "qid": "wf_freeform",
            "answers": [
                {
                    "value": value,
                    "kind": kind,
                    "formula_text": "x / y" if kind in {"number", "percentage"} else "",
                    "variables": {"x": 10, "y": 2} if kind in {"number", "percentage"} else {},
                    "computed_result": 5 if kind in {"number", "percentage"} else value,
                    "percentage_result_semantics": "display_percentage_points" if kind == "percentage" else None,
                    "evidence_refs": ["doc1"],
                }
                for value, kind in values
            ],
            "used_doc_ids": ["doc1"],
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )


def _question(slot_count: int | None = 2) -> Question:
    return Question(
        qid="wf_freeform",
        domain="financial_reports",
        text="分别填写数值与百分比，保留两位小数。",
        options={},
        answer_format="freeform",
        doc_ids=(),
        submission_slot_count=slot_count,
    )


def _workflow(client: FakeClient) -> EnhancedBaselineWorkflow:
    return EnhancedBaselineWorkflow(
        classifier=RuleBasedQuestionClassifier(),
        retriever=FixtureRetriever(),
        assembler=GroupedEvidenceAssembler(),
        solver=CalculationSolver(llm_client=client),
        verifier=None,
        self_check_verifier=None,
        fallback_solver=None,
        enforce_production_integrity=True,
    )


def test_workflow_propagates_multislot_and_checkpoint_roundtrip(tmp_path: Path) -> None:
    client = FakeClient(_payload([("5", "number"), ("5%", "percentage")]))

    result = _workflow(client).process_one(_question())

    assert result.answer == "5.00"
    assert result.submission_answers == ("5.00", "5.00%")
    assert result.metadata["submission_answers"] == ["5.00", "5.00%"]
    assert result.metadata["production_integrity_path"] == "freeform"
    checkpoint = tmp_path / "run_checkpoint.json"
    save_checkpoint(checkpoint, [result])
    restored = load_checkpoint(checkpoint)
    assert restored[0].submission_answers == result.submission_answers


def test_missing_freeform_slot_contract_blocks_before_provider_call() -> None:
    client = FakeClient(_payload([("5", "number")]))

    with pytest.raises(BlockingAnswerValidationError, match="missing_submission_slot_contract"):
        _workflow(client).process_one(_question(slot_count=None))

    assert client.call_count == 0


def _solver_result(*, answer: str, truncated: bool = False, llm_error: bool = False) -> SolverResult:
    return SolverResult(
        qid="q",
        answer=answer,
        solver="calculation",
        metadata={
            "submission_answers": [answer],
            "expected_submission_slots": 1,
            "freeform_parse_valid": True,
            "freeform_slot_bindings": [{
                "slot": 1,
                "value": answer,
                "kind": "ordering" if ">" in answer else "number",
                "format_valid": True,
                "binding_valid": True,
                "blocking_reasons": [],
            }],
            "freeform_all_slot_formats_valid": True,
            "freeform_all_slot_results_match": True,
            "freeform_all_slot_bindings_valid": True,
            "freeform_binding_blocking_reasons": [],
            "freeform_binding_auditable": True,
            "computation_complete": True,
            "truncation_risk": truncated,
            "finish_reason": "length" if truncated else "stop",
            "llm_error": llm_error,
            "answer_source": "freeform_structured",
            "used_doc_ids": ["doc1"],
            "used_docs_source": "freeform_explicit_model_declaration",
        },
    )


def test_freeform_company_a_ordering_does_not_trigger_option_gate() -> None:
    verification = VerificationResult(
        qid="q",
        answer="公司A>公司B",
        changed=False,
        verifier="legacy_option_verifier",
        metadata={
            "self_check": {
                "option_verdicts": {
                    "A": {"status": "unresolved"},
                    "B": {"status": "unresolved"},
                }
            }
        },
    )

    integrity = assess_final_state(
        labels=[QuestionLabel.CALCULATION],
        requested_docs=[],
        retrieved_docs=["doc1"],
        solver_result=_solver_result(answer="公司A>公司B"),
        verification=verification,
        typed_option_evidence=None,
        final_answer="公司A>公司B",
        answer_format="freeform",
        submission_answers=("公司A>公司B",),
        expected_submission_slots=1,
    )

    assert integrity["production_integrity_path"] == "freeform"
    assert integrity["selected_unresolved_options"] == []
    assert "option_evidence_review_required" not in integrity["blocking_reasons"]
    assert integrity["final_state"] == "accepted"


@pytest.mark.parametrize(
    ("truncated", "llm_error", "expected_reason"),
    [
        (True, False, "truncation_risk"),
        (False, True, "llm_error"),
    ],
)
def test_freeform_truncation_or_provider_error_blocks(truncated, llm_error, expected_reason) -> None:
    integrity = assess_final_state(
        labels=[QuestionLabel.CALCULATION],
        requested_docs=[],
        retrieved_docs=["doc1"],
        solver_result=_solver_result(answer="5", truncated=truncated, llm_error=llm_error),
        verification=None,
        final_answer="5",
        answer_format="freeform",
        submission_answers=("5",),
        expected_submission_slots=1,
    )

    assert expected_reason in integrity["blocking_reasons"]
    assert integrity["final_state"] == "blocked"
