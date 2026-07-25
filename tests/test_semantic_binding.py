from __future__ import annotations

from verification.semantic_binding import SemanticConcept, audit_semantic_binding


def _profile() -> tuple[SemanticConcept, ...]:
    return (
        SemanticConcept.build("delegated_party", ("第三方",)),
        SemanticConcept.build("due_diligence", ("客户尽职调查", "尽调")),
        SemanticConcept.build("institution", ("金融机构",)),
        SemanticConcept.build("responsibility", ("法律责任", "未履行客户尽职调查义务")),
    )


def test_semantic_binding_passes_direct_support() -> None:
    result = audit_semantic_binding(
        claim_text="金融机构依托第三方开展客户尽职调查，第三方未依法尽调时金融机构承担法律责任。DOC:1",
        evidence_by_ref={
            "DOC:1": "金融机构依托第三方开展客户尽职调查。第三方未采取符合法律要求的客户尽职调查措施的，由金融机构承担未履行客户尽职调查义务的法律责任。"
        },
        required_concepts=_profile(),
    )
    assert result.valid is True
    assert result.reason == "semantic_binding_pass"
    assert result.unsupported_refs == ()


def test_semantic_binding_rejects_traceable_but_unrelated_evidence() -> None:
    result = audit_semantic_binding(
        claim_text="金融机构依托第三方开展客户尽职调查，第三方未依法尽调时金融机构承担法律责任。DOC:5",
        evidence_by_ref={
            "DOC:5": "违法单位被撤销、注销的，直接负责的主管人员和其他直接责任人员仍承担行政法律责任。"
        },
        required_concepts=_profile(),
    )
    assert result.valid is False
    assert result.reason == "cited_evidence_semantically_unrelated"
    assert result.unsupported_refs == ("DOC:5",)


def test_semantic_binding_fails_without_citation() -> None:
    result = audit_semantic_binding(
        claim_text="金融机构依托第三方开展客户尽职调查，第三方未依法尽调时金融机构承担法律责任。",
        evidence_by_ref={"DOC:1": "金融机构依托第三方开展客户尽职调查并承担法律责任。"},
        required_concepts=_profile(),
    )
    assert result.valid is False
    assert result.reason == "missing_cited_doc_ref"


def test_semantic_binding_fails_when_claim_is_too_vague() -> None:
    result = audit_semantic_binding(
        claim_text="因此应由相关主体负责。DOC:1",
        evidence_by_ref={
            "DOC:1": "金融机构依托第三方开展客户尽职调查。第三方未依法尽调的，由金融机构承担法律责任。"
        },
        required_concepts=_profile(),
    )
    assert result.valid is False
    assert result.reason == "claim_semantic_coverage_insufficient"
