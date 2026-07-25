from __future__ import annotations

from contracts import EvidenceBundle, EvidenceCandidate, Question, SolverResult
from verification.production_integrity import assess_final_state
from verification.structured_claims import route_structured_claim


def _verification(option_verdicts: dict[str, dict[str, str]]):
    class V:
        metadata = {"self_check": {"option_verdicts": option_verdicts, "issues": []}}
    return V()


def _solver(answer: str = "A") -> SolverResult:
    return SolverResult(qid="q", answer=answer, solver="test", metadata={"used_doc_ids": ["d1"]})


def _state(option_verdicts: dict[str, dict[str, str]], answer: str = "A") -> dict:
    return assess_final_state(
        labels=[],
        requested_docs=["d1"],
        retrieved_docs=["d1"],
        solver_result=_solver(answer),
        verification=_verification(option_verdicts),
    )


def _state_no_retrieval(option_verdicts: dict[str, dict[str, str]], answer: str = "A") -> dict:
    return assess_final_state(
        labels=[],
        requested_docs=["d1"],
        retrieved_docs=[],
        solver_result=_solver(answer),
        verification=_verification(option_verdicts),
    )


def test_unselected_missing_options_do_not_block_single_choice() -> None:
    state = _state({
        "A": {"status": "supported"},
        "B": {"status": "missing"},
        "C": {"status": "missing"},
        "D": {"status": "contradicted"},
    }, answer="A")
    assert "option_evidence_unresolved" not in state["blocking_reasons"]
    assert state["unresolved_options"] == []
    assert set(state["benign_unselected_missing_options"]) == {"B", "C"}


def test_selected_missing_option_blocks() -> None:
    state = _state_no_retrieval({
        "A": {"status": "missing"},
        "B": {"status": "missing"},
        "C": {"status": "missing"},
        "D": {"status": "missing"},
    }, answer="A")
    assert "option_evidence_unresolved" in state["blocking_reasons"]
    assert state["option_evidence_unresolved_hard"] is True
    assert state["selected_unresolved_options"] == ["A"]
    assert "A" in state["unresolved_options"]




def test_selected_missing_with_retrieved_doc_becomes_review_required_not_hard_unresolved() -> None:
    state = assess_final_state(
        labels=[],
        requested_docs=["d1"],
        retrieved_docs=["d1"],
        solver_result=_solver("A"),
        verification=_verification({
            "A": {"status": "missing"},
            "B": {"status": "missing"},
            "C": {"status": "missing"},
            "D": {"status": "missing"},
        }),
    )
    assert "option_evidence_unresolved" not in state["blocking_reasons"]
    assert "option_evidence_review_required" in state["blocking_reasons"]
    assert state["option_evidence_review_required"] is True
    assert state["option_evidence_unresolved_hard"] is False
    assert state["selected_unresolved_options"] == ["A"]


def test_unselected_supported_option_blocks() -> None:
    state = _state({
        "A": {"status": "supported"},
        "B": {"status": "missing"},
        "C": {"status": "contradicted"},
        "D": {"status": "supported"},
    }, answer="A")
    assert "option_evidence_unresolved" in state["blocking_reasons"]
    assert state["unselected_supported_options"] == ["D"]
    assert "D" in state["unresolved_options"]


def test_selected_contradicted_option_blocks() -> None:
    state = _state({
        "A": {"status": "contradicted"},
        "B": {"status": "missing"},
        "C": {"status": "missing"},
        "D": {"status": "missing"},
    }, answer="A")
    assert "option_evidence_unresolved" in state["blocking_reasons"]
    assert state["selected_contradicted_options"] == ["A"]


def test_case_001_d_is_not_supported_by_subject_only_exact_fact_anchor() -> None:
    question = Question(
        qid="case_001",
        domain="financial_contracts",
        text="测试",
        options={"D": "发行人是厦门金圆投资集团有限公司"},
        answer_format="mcq",
        doc_ids=["text03"],
    )
    bundle = EvidenceBundle(
        question=question,
        classification=None,
        candidates=[
            EvidenceCandidate(
                domain="financial_contracts",
                doc_id="text03",
                source="text03/page_0026.md",
                text="发行人于2025年5月26日发行债券。发行人为厦门金圆投资集团有限公司。",
            )
        ],
        prompt_context="",
        estimated_tokens=10,
    )
    verdict = route_structured_claim(bundle.question, "D", question.options["D"], bundle.candidates)
    assert verdict is None or verdict["status"] != "supported"


def test_correction_proposal_differs_requires_reconcile_without_verdict_group_trigger() -> None:
    class V:
        metadata = {"self_check": {
            "option_verdicts": {
                "A": {"status": "supported"},
                "B": {"status": "missing"},
                "C": {"status": "missing"},
                "D": {"status": "missing"},
            },
            "correction_proposal": "AB",
            "correction_differs": True,
            "issues": [],
        }}
    state = assess_final_state(
        labels=[],
        requested_docs=["d1"],
        retrieved_docs=["d1"],
        solver_result=_solver("A"),
        verification=V(),
    )
    assert state["correction_proposal"] == "AB"
    assert state["correction_differs"] is True
    assert state["correction_gate_required"] is True
    assert "correction_reconcile_required" in state["blocking_reasons"]
