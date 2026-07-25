from __future__ import annotations

from pathlib import Path

import json

import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contracts import EvidenceCandidate

from verification.atom_evidence_verifier import REFUTE, SUPPORT, UNRESOLVED, AtomVerdict, verify_atom

from verification.claim_atoms import ClaimAtom, atomize_claim

from verification.claim_verifier import (
    CLAIM_REFUTED,
    CLAIM_SUPPORTED,
    CLAIM_UNRESOLVED,
    aggregate_atom_verdicts,
    verify_claim,
    verify_options,
)

def _candidate(
    text: str,
    *,
    doc_id: str = "doc_a",
    page: int | None = 1,
    source: str | None = None,
    score: float = 10.0,
) -> EvidenceCandidate:
    metadata = {} if page is None else {"page_number": page, "canonical_doc_id": doc_id}
    return EvidenceCandidate(
        domain="fixture",
        doc_id=doc_id,
        source=source if source is not None else f"/corpus/fixture/{doc_id}/page_{page or 1:04d}.md",
        text=text,
        score=score,
        retriever="fixture",
        metadata=metadata,
    )

def _manual_atom(
    *,
    subject: str = "甲公司",
    metric: str = "营业收入",
    value: str = "100",
    unit: str = "亿元",
    relation: str = "=",
    polarity: str = "positive",
    time_scope: str = "",
    condition: str = "",
    exception: str = "",
    atom_text: str = "甲公司营业收入为100亿元",
    scope_confidence: str = "HIGH",
) -> ClaimAtom:
    return ClaimAtom(
        atom_id="atom_01",
        subject=subject,
        object_or_metric=metric,
        time_scope=time_scope,
        value=value,
        unit=unit,
        relation=relation,
        polarity=polarity,
        condition=condition,
        exception=exception,
        quantifier="",
        source_text=atom_text,
        atom_text=atom_text,
        clause_id="clause_01",
        scope_confidence=scope_confidence,
        scope_reason="fixture",
    )

def test_mixed_scope_condition_exception_and_time_do_not_cross_semicolon() -> None:
    text = (
        "在2025年满足监管条件的情况下，甲公司营业收入至少100亿元且净利润不低于20亿元；"
        "除无民事行为能力人的情形外，公司不得在2年内解除合同。"
    )
    atoms = atomize_claim(text).atoms
    assert len(atoms) == 3
    revenue, profit, termination = atoms
    for atom in (revenue, profit):
        assert atom.time_scope == "2025年"
        assert "监管条件" in atom.condition
        assert atom.exception == ""
        assert atom.clause_id == "clause_01"
    assert termination.time_scope == "2年内"
    assert termination.condition == ""
    assert "无民事行为能力" in termination.exception
    assert termination.clause_id == "clause_02"
    assert termination.object_or_metric == "解除合同"

def test_parallel_metric_inherits_subject_inside_same_clause() -> None:
    atoms = atomize_claim("甲公司营业收入至少100亿元且净利润不低于20亿元").atoms
    assert len(atoms) == 2
    assert atoms[0].subject == "甲公司"
    assert atoms[1].subject == "甲公司"

def test_ambiguous_internal_scope_is_low_confidence_and_fail_closed() -> None:
    atom = atomize_claim("甲公司营业收入100亿元，业务若满足特殊条件可能调整").atoms[0]
    assert atom.scope_confidence == "LOW"
    verdict = verify_atom(atom, [_candidate("甲公司营业收入100亿元，业务满足特殊条件")])
    assert verdict.verdict == UNRESOLVED
    assert "SCOPE_CONFIDENCE_LOW" in verdict.reason_codes

def test_atomization_is_deterministic() -> None:
    text = "除上述主体外的其他交易对方，若权益时间不足12个月，锁定期为12个月"
    assert atomize_claim(text).to_dict() == atomize_claim(text).to_dict()

def test_numeric_exact_support_and_contradiction() -> None:
    atom = _manual_atom()
    supported = verify_atom(atom, [_candidate("甲公司营业收入为100亿元")])
    contradicted = verify_atom(atom, [_candidate("甲公司营业收入为90亿元")])
    assert supported.verdict == SUPPORT and supported.binding_auditable is True
    assert contradicted.verdict == REFUTE and contradicted.binding_auditable is True
    assert supported.bound_doc_id == "doc_a" and supported.bound_page == "1"

def test_wrong_metric_number_cannot_support() -> None:
    atom = _manual_atom(value="100", relation=">=")
    verdict = verify_atom(atom, [_candidate("甲公司营业收入90亿元，净利润120亿元")])
    assert verdict.verdict == REFUTE
    assert "METRIC_LOCAL_NUMERIC_RELATION_REFUTE" in verdict.reason_codes
    assert "营业收入90亿元" in verdict.matched_span
    assert "净利润120亿元" not in verdict.matched_span

def test_metric_local_refute_with_other_larger_number() -> None:
    atom = _manual_atom(value="100", relation=">=")
    verdict = verify_atom(atom, [_candidate("甲公司营业收入90亿元，其他指标120亿元")])
    assert verdict.verdict == REFUTE
    assert "METRIC_LOCAL_NUMERIC_RELATION_REFUTE" in verdict.reason_codes

def test_metric_local_support_with_other_smaller_number() -> None:
    atom = _manual_atom(value="100", relation=">=")
    verdict = verify_atom(atom, [_candidate("甲公司营业收入110亿元，净利润90亿元")])
    assert verdict.verdict == SUPPORT
    assert "METRIC_LOCAL_NUMERIC_RELATION_SUPPORT" in verdict.reason_codes

def test_ambiguous_multiple_values_unresolved() -> None:
    atom = _manual_atom(value="100", relation=">=")
    verdict = verify_atom(atom, [_candidate("甲公司营业收入90亿元和110亿元")])
    assert verdict.verdict == UNRESOLVED
    assert "METRIC_LOCAL_MULTIPLE_VALUES_AMBIGUOUS" in verdict.reason_codes

def test_same_metric_same_unit_exact_support() -> None:
    atom = _manual_atom(value="100", relation="=")
    verdict = verify_atom(atom, [_candidate("甲公司营业收入100亿元，净利润999亿元")])
    assert verdict.verdict == SUPPORT

def test_same_metric_same_unit_exact_refute() -> None:
    atom = _manual_atom(value="100", relation="=")
    verdict = verify_atom(atom, [_candidate("甲公司营业收入90亿元，净利润100亿元")])
    assert verdict.verdict == REFUTE

@pytest.mark.parametrize(
    ("relation", "threshold", "actual", "expected"),
    [
        (">=", "100", "110", SUPPORT),
        (">=", "100", "90", REFUTE),
        ("<=", "100", "90", SUPPORT),
        ("<=", "100", "110", REFUTE),
        (">", "100", "101", SUPPORT),
        (">", "100", "100", REFUTE),
        ("<", "100", "99", SUPPORT),
        ("<", "100", "100", REFUTE),
    ],
)
def test_numeric_relations(relation: str, threshold: str, actual: str, expected: str) -> None:
    atom = _manual_atom(value=threshold, relation=relation)
    verdict = verify_atom(atom, [_candidate(f"甲公司营业收入为{actual}亿元")])
    assert verdict.verdict == expected

def test_percent_and_percentage_points_are_not_silently_equated() -> None:
    atom = _manual_atom(metric="增幅", value="3", unit="%", atom_text="甲公司增幅为3%")
    verdict = verify_atom(atom, [_candidate("甲公司增幅为3个百分点")])
    assert verdict.verdict == UNRESOLVED
    assert "UNIT_INCOMPATIBLE" in verdict.reason_codes

def test_negative_support_and_explicit_opposite_refute() -> None:
    atom = _manual_atom(
        subject="公司",
        metric="解除合同",
        value="",
        unit="",
        relation="prohibited",
        polarity="negative",
        atom_text="公司不得解除合同",
    )
    supported = verify_atom(atom, [_candidate("根据条款，公司不得解除合同")])
    refuted = verify_atom(atom, [_candidate("根据条款，公司可以解除合同")])
    assert supported.verdict == SUPPORT
    assert refuted.verdict == REFUTE

def test_condition_must_be_established_in_same_candidate() -> None:
    atom = _manual_atom(
        relation=">=",
        condition="在满足监管条件的情况下",
        atom_text="甲公司营业收入至少100亿元",
    )
    supported = verify_atom(atom, [_candidate("在满足监管条件的情况下，甲公司营业收入110亿元")])
    unresolved = verify_atom(atom, [_candidate("甲公司营业收入110亿元")])
    assert supported.verdict == SUPPORT
    assert unresolved.verdict == UNRESOLVED
    assert "CONDITION_NOT_ESTABLISHED" in unresolved.reason_codes

def test_exception_must_be_established_in_same_candidate() -> None:
    atom = _manual_atom(
        subject="公司",
        metric="解除合同",
        value="",
        unit="",
        relation="prohibited",
        polarity="negative",
        exception="除无民事行为能力人的情形外",
        atom_text="公司不得解除合同",
    )
    supported = verify_atom(atom, [_candidate("除无民事行为能力人的情形外，公司不得解除合同")])
    unresolved = verify_atom(atom, [_candidate("公司不得解除合同")])
    assert supported.verdict == SUPPORT
    assert unresolved.verdict == UNRESOLVED
    assert "EXCEPTION_NOT_ESTABLISHED" in unresolved.reason_codes

def test_time_mismatch_is_unresolved_not_guessed() -> None:
    atom = _manual_atom(time_scope="2025年", atom_text="2025年甲公司营业收入为100亿元")
    verdict = verify_atom(atom, [_candidate("2024年甲公司营业收入为100亿元")])
    assert verdict.verdict == UNRESOLVED
    assert "TIME_SCOPE_MISSING_OR_MISMATCH" in verdict.reason_codes

def test_wrong_entity_is_unresolved_not_refuted_by_guess() -> None:
    atom = _manual_atom(subject="甲公司")
    verdict = verify_atom(atom, [_candidate("乙公司营业收入为100亿元")])
    assert verdict.verdict == UNRESOLVED
    assert "SUBJECT_MISMATCH_OR_MISSING" in verdict.reason_codes

def test_missing_lineage_is_unresolved() -> None:
    atom = _manual_atom()
    candidate = _candidate("甲公司营业收入为100亿元", page=None, source="document.md")
    verdict = verify_atom(atom, [candidate])
    assert verdict.verdict == UNRESOLVED
    assert "LINEAGE_INCOMPLETE" in verdict.reason_codes
    assert verdict.binding_auditable is False

def test_cross_doc_frankenstein_binding_is_blocked() -> None:
    atom = _manual_atom()
    candidates = [
        _candidate("甲公司营业收入已披露", doc_id="doc_subject", page=1),
        _candidate("金额为100亿元", doc_id="doc_value", page=2),
    ]
    verdict = verify_atom(atom, candidates)
    assert verdict.verdict == UNRESOLVED
    assert "CROSS_DOC_FRANKENSTEIN_BLOCKED" in verdict.reason_codes
    assert verdict.binding_auditable is False

def test_subjectless_numeric_claim_cannot_authoritatively_bind_across_multiple_docs() -> None:
    atom = _manual_atom(
        subject="",
        metric="经营活动产生的现金流量净额",
        value="82.82",
        unit="%",
        relation="=",
        atom_text="经营活动产生的现金流量净额同比降幅约为82.82%",
    )
    verdict = verify_atom(
        atom,
        [
            _candidate("经营活动产生的现金流量净额同比降幅为82.82%", doc_id="company_a", page=1),
            _candidate("经营活动产生的现金流量净额同比降幅为20.00%", doc_id="company_b", page=2),
        ],
    )
    assert verdict.verdict == UNRESOLVED
    assert "SUBJECT_SCOPE_AMBIGUOUS_ACROSS_DOCS" in verdict.reason_codes

@pytest.mark.parametrize("claim", ["800", "国寿增益宝"])
def test_naked_number_or_label_is_not_promoted_by_exact_text_match(claim: str) -> None:
    atom = atomize_claim(claim).atoms[0]
    verdict = verify_atom(atom, [_candidate(f"材料中出现：{claim}")])
    assert verdict.verdict == UNRESOLVED
    assert "CLAIM_SEMANTIC_ANCHORS_INSUFFICIENT" in verdict.reason_codes

def _verdict(atom_id: str, status: str) -> AtomVerdict:
    return AtomVerdict(
        atom_id=atom_id,
        verdict=status,
        reason_codes=("fixture",),
        evidence_refs=(),
        bound_doc_id="",
        bound_page="",
        bound_source="",
        matched_span="",
        binding_auditable=False,
    )

def test_claim_aggregate_all_support_is_supported() -> None:
    assert aggregate_atom_verdicts((_verdict("a1", SUPPORT), _verdict("a2", SUPPORT))) == CLAIM_SUPPORTED

def test_claim_aggregate_any_refute_is_refuted() -> None:
    assert aggregate_atom_verdicts((_verdict("a1", SUPPORT), _verdict("a2", REFUTE))) == CLAIM_REFUTED

def test_claim_aggregate_any_unresolved_without_refute_is_unresolved() -> None:
    assert aggregate_atom_verdicts((_verdict("a1", SUPPORT), _verdict("a2", UNRESOLVED))) == CLAIM_UNRESOLVED

def test_option_labels_are_indexes_only_and_do_not_change_verdict() -> None:
    candidates = [_candidate("甲公司营业收入为100亿元")]
    options = {"A": "甲公司营业收入为100亿元", "D": "甲公司营业收入为100亿元"}
    results = verify_options(options, candidates)
    assert results["A"].aggregate_verdict == CLAIM_SUPPORTED
    assert results["D"].aggregate_verdict == CLAIM_SUPPORTED
    assert [row.verdict for row in results["A"].atom_verdicts] == [row.verdict for row in results["D"].atom_verdicts]

def test_claim_verifier_preserves_atoms_verdicts_and_lineage() -> None:
    result = verify_claim("甲公司营业收入为100亿元", [_candidate("甲公司营业收入为100亿元")], option_label="B")
    payload = result.to_dict()
    assert payload["option_label"] == "B"
    assert payload["aggregate_verdict"] == CLAIM_SUPPORTED
    assert payload["atoms"] and payload["atom_verdicts"]
    assert payload["supporting_evidence_lineage"][0]["doc_id"] == "doc_a"
    assert payload["provider_calls"] == 0

def test_p13_core_has_no_qid_or_answer_letter_hardcoding() -> None:
    import re

    pattern = re.compile(r"(?:fc|fin|ins|reg|res)_[ab]_\d{3}", re.IGNORECASE)
    for relative in (
        "src/verification/claim_atoms.py",
        "src/verification/atom_evidence_verifier.py",
        "src/verification/claim_verifier.py",
    ):
        assert pattern.search((ROOT / relative).read_text(encoding="utf-8")) is None, relative

def test_p13_shadow_modules_are_not_production_wired() -> None:
    forbidden_imports = ("atom_evidence_verifier", "claim_verifier")
    for relative in (
        "src/agent/factory.py",
        "src/agent/workflow.py",
        "src/evidence/enhanced_assembler.py",
        "src/verification/verifier.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert all(name not in text for name in forbidden_imports), relative
