from contracts import Question
from evaluation.evidence_sufficiency_controller import assess_evidence_sufficiency
from evaluation.platform_validation_adapter import adapt_evidence_sufficiency_to_platform


def test_blocked_evidence_sufficiency_maps_to_platform_contract() -> None:
    result = adapt_evidence_sufficiency_to_platform(
        {
            "schema_version": "evidence_sufficiency_controller_v1",
            "gap_count": 1,
            "answer_ready_by_evidence": False,
            "question_required_docs_covered": False,
            "all_option_closure": False,
            "gaps": [
                {
                    "gap_type": "MISSING_REQUIRED_DOC",
                    "action": "DOC_SPECIFIC_RETRIEVAL",
                    "doc_ids": ["doc-amex-2023"],
                }
            ],
        }
    )

    assert result == {
        "schema_version": "validation-result-v0.1",
        "verdict": "BLOCKED",
        "validator_id": "findocqa.evidence_sufficiency",
        "validator_version": "evidence_sufficiency_controller_v1",
        "findings": [
            {
                "code": "MISSING_REQUIRED_DOC",
                "message": "DOC_SPECIFIC_RETRIEVAL",
                "severity": None,
                "evidence_refs": [
                    {
                        "ref_type": "document",
                        "ref": "doc-amex-2023",
                        "locator": None,
                    }
                ],
            }
        ],
        "evidence_refs": [
            {
                "ref_type": "document",
                "ref": "doc-amex-2023",
                "locator": None,
            }
        ],
        "limitations": [
            "required_documents_not_fully_covered",
            "option_evidence_not_fully_closed",
        ],
    }
    assert "metadata" not in result
    assert "context" not in result
    assert "extensions" not in result


def test_ready_evidence_maps_to_pass_without_inventing_findings() -> None:
    result = adapt_evidence_sufficiency_to_platform(
        {
            "schema_version": "evidence_sufficiency_controller_v1",
            "gap_count": 0,
            "answer_ready_by_evidence": True,
            "question_required_docs_covered": True,
            "all_option_closure": True,
            "gaps": [],
        }
    )

    assert result["verdict"] == "PASS"
    assert result["findings"] == []
    assert result["evidence_refs"] == []
    assert result["limitations"] == []


def test_real_sufficiency_controller_output_flows_through_platform_adapter() -> None:
    question = Question(
        qid="q-platform-adapter",
        domain="financial_reports",
        text="Which option is supported?",
        options={"A": "Option A", "B": "Option B"},
        answer_format="single",
        doc_ids=("doc-required",),
    )
    sufficiency = assess_evidence_sufficiency(
        question=question,
        verdicts={
            "A": {"status": "supported", "trusted_for_option_gate": True},
            "B": {"status": "contradicted", "trusted_for_option_gate": True},
        },
        required_doc_coverage={
            "question_required_docs_covered": False,
            "missing_required_doc_ids": ["doc-required"],
        },
        semantic_completeness={
            "A": {"full_semantic_atoms_bound": True},
            "B": {"full_semantic_atoms_bound": True},
        },
    )

    result = adapt_evidence_sufficiency_to_platform(sufficiency)

    assert result["verdict"] == "BLOCKED"
    assert result["validator_version"] == "evidence_sufficiency_controller_v1"
    assert any(item["code"] == "MISSING_REQUIRED_DOC" for item in result["findings"])
    assert result["evidence_refs"] == [
        {"ref_type": "document", "ref": "doc-required", "locator": None}
    ]
