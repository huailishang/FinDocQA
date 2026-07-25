from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verification.atom_evidence_verifier import AtomVerdict, REFUTE, SUPPORT, UNRESOLVED
from verification.claim_atoms import ClaimAtom
from verification.claim_verifier import (
    CLAIM_UNRESOLVED,
    ClaimVerificationResult,
)
from verification.verifier_failure_adapter import (
    BINDING_FAILED,
    LINEAGE_LOST,
    MISSING_EVIDENCE,
    MULTIPLE_FAILURE_SIGNALS,
    UNKNOWN_FAILURE,
    adapt_atom_verdict,
    adapt_claim_verdict,
)


def _atom(*, scope_confidence: str = "HIGH", atom_id: str = "atom_01") -> ClaimAtom:
    return ClaimAtom(
        atom_id=atom_id,
        subject="甲公司",
        object_or_metric="营业收入",
        time_scope="",
        value="100",
        unit="亿元",
        relation=">=",
        polarity="positive",
        condition="",
        exception="",
        quantifier="",
        source_text="甲公司营业收入至少100亿元",
        atom_text="甲公司营业收入至少100亿元",
        clause_id="clause_01",
        scope_confidence=scope_confidence,
        scope_reason="fixture",
    )


def _verdict(
    status: str,
    reasons: tuple[str, ...],
    *,
    atom_id: str = "atom_01",
    refs: tuple[str, ...] = (),
    auditable: bool = False,
) -> AtomVerdict:
    return AtomVerdict(
        atom_id=atom_id,
        verdict=status,
        reason_codes=reasons,
        evidence_refs=refs,
        bound_doc_id="doc_a" if auditable else "",
        bound_page="1" if auditable else "",
        bound_source="/doc_a/page_0001.md" if auditable else "",
        matched_span="甲公司营业收入100亿元" if auditable else "",
        binding_auditable=auditable,
    )


def test_empty_evidence_maps_to_missing_evidence() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("EMPTY_EVIDENCE",)),
        evidence_count=1,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == MISSING_EVIDENCE
    assert result.failure_signals == (MISSING_EVIDENCE,)
    assert result.evidence_available is False
    assert result.execution_authorized is False


def test_zero_evidence_count_maps_to_missing_evidence() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("NO_EVIDENCE_CANDIDATES",)),
        evidence_count=0,
    )
    assert result.failure_code == MISSING_EVIDENCE
    assert result.evidence_count == 0
    assert result.evidence_available is False


def test_evidence_exists_plus_lineage_incomplete_maps_to_lineage_lost() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("LINEAGE_INCOMPLETE",), refs=("raw/page.md",)),
        evidence_count=3,
        raw_evidence_refs=("raw/page.md",),
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == LINEAGE_LOST
    assert result.evidence_available is True
    assert result.evidence_count == 3
    assert result.failure_code != MISSING_EVIDENCE


def test_evidence_exists_plus_subject_mismatch_maps_to_binding_failed() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("SUBJECT_MISMATCH_OR_MISSING",), refs=("page_1.md",)),
        evidence_count=5,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == BINDING_FAILED
    assert result.failure_code != MISSING_EVIDENCE


def test_scope_low_with_evidence_is_unknown_not_missing() -> None:
    result = adapt_atom_verdict(
        _atom(scope_confidence="LOW"),
        _verdict(UNRESOLVED, ("SCOPE_CONFIDENCE_LOW",), refs=("page_1.md",)),
        evidence_count=4,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == UNKNOWN_FAILURE
    assert MISSING_EVIDENCE not in result.failure_signals
    assert result.scope_confidence == "LOW"


def test_numeric_multiple_values_is_binding_failed_not_missing() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("METRIC_LOCAL_MULTIPLE_VALUES_AMBIGUOUS",), refs=("page_1.md",)),
        evidence_count=2,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == BINDING_FAILED
    assert MISSING_EVIDENCE not in result.failure_signals


def test_cross_doc_frankenstein_is_binding_failed_not_missing_or_support() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(
            UNRESOLVED,
            ("CROSS_DOC_FRANKENSTEIN_BLOCKED",),
            refs=("doc_a/page_1.md", "doc_b/page_2.md"),
        ),
        evidence_count=2,
        used_doc_ids=("doc_a", "doc_b"),
    )
    assert result.verdict == UNRESOLVED
    assert result.failure_code == BINDING_FAILED
    assert MISSING_EVIDENCE not in result.failure_signals


def test_normal_support_has_no_failure_signal() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(SUPPORT, ("METRIC_LOCAL_NUMERIC_RELATION_SUPPORT",), refs=("page_1.md",), auditable=True),
        evidence_count=1,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == ""
    assert result.failure_signals == ()
    assert result.binding_auditable is True


def test_normal_refute_has_no_failure_signal() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(REFUTE, ("METRIC_LOCAL_NUMERIC_RELATION_REFUTE",), refs=("page_1.md",), auditable=True),
        evidence_count=1,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == ""
    assert result.failure_signals == ()
    assert result.binding_auditable is True


def test_unmapped_unresolved_reason_fails_closed_to_unknown_not_missing() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("FUTURE_REASON_NOT_YET_MAPPED",), refs=("page_1.md",)),
        evidence_count=1,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == UNKNOWN_FAILURE
    assert MISSING_EVIDENCE not in result.failure_signals


def test_same_input_is_deterministic_identical_output() -> None:
    kwargs = dict(
        atom=_atom(),
        verdict=_verdict(UNRESOLVED, ("CONDITION_NOT_ESTABLISHED",), refs=("page_1.md",)),
        evidence_count=3,
        raw_evidence_refs=("raw_1", "raw_2"),
        used_doc_ids=("doc_b", "doc_a"),
    )
    first = adapt_atom_verdict(**kwargs).to_dict()
    second = adapt_atom_verdict(**kwargs).to_dict()
    assert first == second


def test_atom_adapter_preserves_composite_lineage_and_binding_for_p14d() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(
            UNRESOLVED,
            ("LINEAGE_INCOMPLETE", "SUBJECT_MISMATCH_OR_MISSING"),
            refs=("raw/page.md",),
        ),
        evidence_count=2,
        raw_evidence_refs=("raw/page.md",),
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == MULTIPLE_FAILURE_SIGNALS
    assert result.failure_signals == (LINEAGE_LOST, BINDING_FAILED)
    assert MISSING_EVIDENCE not in result.failure_signals


def test_contradictory_no_candidate_reason_with_positive_count_fails_closed_unknown() -> None:
    result = adapt_atom_verdict(
        _atom(),
        _verdict(UNRESOLVED, ("NO_EVIDENCE_CANDIDATES",)),
        evidence_count=2,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == UNKNOWN_FAILURE
    assert MISSING_EVIDENCE not in result.failure_signals
    assert "contradictory_evidence_metadata_fail_closed" in result.adapter_reason


def test_claim_adapter_preserves_multiple_failure_classes_for_p14d() -> None:
    atoms = (_atom(atom_id="atom_01"), _atom(scope_confidence="LOW", atom_id="atom_02"))
    verdicts = (
        _verdict(
            UNRESOLVED,
            ("SUBJECT_MISMATCH_OR_MISSING",),
            atom_id="atom_01",
            refs=("page_1.md",),
        ),
        _verdict(
            UNRESOLVED,
            ("SCOPE_CONFIDENCE_LOW",),
            atom_id="atom_02",
            refs=("page_2.md",),
        ),
    )
    claim = ClaimVerificationResult(
        option_label="A",
        claim_text="fixture",
        atoms=atoms,
        atom_verdicts=verdicts,
        aggregate_verdict=CLAIM_UNRESOLVED,
        unresolved_atom_ids=("atom_01", "atom_02"),
        refuted_atom_ids=(),
        supporting_evidence_lineage=(),
        provider_calls=0,
    )
    result = adapt_claim_verdict(
        claim,
        evidence_count=2,
        used_doc_ids=("doc_a",),
    )
    assert result.failure_code == MULTIPLE_FAILURE_SIGNALS
    assert result.failure_signals == (BINDING_FAILED, UNKNOWN_FAILURE)
    assert result.adapter_reason == "multiple_atom_failure_signals_deferred_to_p14d"
    assert result.execution_authorized is False


def test_claim_unresolved_with_evidence_does_not_default_to_missing() -> None:
    atoms = (_atom(),)
    verdicts = (
        _verdict(
            UNRESOLVED,
            ("TEXTUAL_ENTAILMENT_NOT_DETERMINISTIC",),
            refs=("page_1.md",),
        ),
    )
    claim = ClaimVerificationResult(
        option_label="",
        claim_text="fixture",
        atoms=atoms,
        atom_verdicts=verdicts,
        aggregate_verdict=CLAIM_UNRESOLVED,
        unresolved_atom_ids=("atom_01",),
        refuted_atom_ids=(),
        supporting_evidence_lineage=(),
        provider_calls=0,
    )
    result = adapt_claim_verdict(claim, evidence_count=10, used_doc_ids=("doc_a",))
    assert result.failure_code == UNKNOWN_FAILURE
    assert MISSING_EVIDENCE not in result.failure_signals
