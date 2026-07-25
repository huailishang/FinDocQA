from verification.correction_gate import assess_self_check_correction


def verdict(status, route, source=True):
    return {
        "status": status,
        "claim_route": route,
        "evidence_matches": [{"source": "doc/page.md"}] if source else [],
    }


def test_exact_regulatory_closure_is_applied():
    result = assess_self_check_correction(
        domain="regulatory",
        original_answer="B",
        self_check_metadata={
            "correction_proposal": "ABD",
            "option_verdicts": {
                "A": verdict("supported", "regulatory_exact_clause"),
                "B": verdict("supported", "regulatory_exact_clause"),
                "C": verdict("contradicted", "regulatory_exact_clause"),
                "D": verdict("supported", "regulatory_exact_clause"),
            },
        },
    )
    assert result["applied"] is True
    assert result["answer"] == "ABD"


def test_scope_exclusion_is_safe_when_source_is_frozen():
    result = assess_self_check_correction(
        domain="regulatory",
        original_answer="ACD",
        self_check_metadata={
            "correction_proposal": "AC",
            "option_verdicts": {
                "A": verdict("supported", "regulatory_exact_clause"),
                "B": verdict("contradicted", "regulatory_exact_clause"),
                "C": verdict("supported", "regulatory_exact_clause"),
                "D": verdict("contradicted", "question_scope_exclusion"),
            },
        },
    )
    assert result["applied"] is True
    assert result["answer"] == "AC"


def test_lexical_or_missing_evidence_is_blocked():
    result = assess_self_check_correction(
        domain="regulatory",
        original_answer="A",
        self_check_metadata={
            "correction_proposal": "AB",
            "option_verdicts": {
                "A": verdict("supported", "lexical", source=False),
                "B": verdict("supported", "regulatory_exact_clause"),
            },
        },
    )
    assert result["applied"] is False
    assert result["answer"] == "A"
    assert any("unsafe_route" in reason for reason in result["blocking_reasons"])
    assert any("missing_source_location" in reason for reason in result["blocking_reasons"])


def test_non_regulatory_domain_is_blocked():
    result = assess_self_check_correction(
        domain="insurance",
        original_answer="A",
        self_check_metadata={
            "correction_proposal": "AB",
            "option_verdicts": {
                "A": verdict("supported", "regulatory_exact_clause"),
                "B": verdict("supported", "regulatory_exact_clause"),
            },
        },
    )
    assert result["applied"] is False
    assert "domain_not_regulatory" in result["blocking_reasons"]
