from contracts import EvidenceCandidate, Question
from verification.structured_claims import route_structured_claim


def candidate(text: str, source: str = "page.md") -> EvidenceCandidate:
    return EvidenceCandidate(
        domain="research", doc_id="doc", source=source, text=text,
        before_text="", after_text="", section_title="", score=1.0,
        retriever="test", metadata={},
    )


def question(option_text: str) -> Question:
    return Question(
        qid="case_001", domain="research", text="根据材料判断。",
        options={"A": option_text}, answer_format="multi", doc_ids=["doc"], raw={},
    )


def test_non_opaque_claim_uses_generic_verifier_path() -> None:
    q = question("公司营业收入同比增长 8.47%")
    verdict = route_structured_claim(
        q, "A", q.options["A"], [candidate("公司营业收入同比增长 8.47%")],
    )
    assert verdict is None


def test_opaque_option_stays_unresolved() -> None:
    q = question("正确")
    verdict = route_structured_claim(q, "A", "正确", [])
    assert verdict["status"] == "unresolved"
    assert verdict["claim_route"] == "question_level_required"
