from __future__ import annotations

from decimal import Decimal

from calculation import (
    FormulaSourceRef,
    SemanticBindingCandidate,
    SemanticBindingRequest,
    SemanticBindingStatus,
    SemanticVariableBinder,
)


def _ref(*, doc_id: str = "doc-a", source: str = "doc://a/page/1") -> FormulaSourceRef:
    return FormulaSourceRef(doc_id=doc_id, page_number=1, source=source, block_id="b1")


def _request(**changes: object) -> SemanticBindingRequest:
    values: dict[str, object] = {
        "name": "revenue",
        "metric": " Revenue ",
        "entity": "Acme",
        "period": "2024Q1",
        "unit": "元",
        "document_id": "doc-a",
    }
    values.update(changes)
    return SemanticBindingRequest(**values)


def _candidate(**changes: object) -> SemanticBindingCandidate:
    values: dict[str, object] = {
        "value": "12",
        "metric": "revenue",
        "entity": "acme",
        "period": "2024q1",
        "unit": "万元",
        "document_id": "doc-a",
        "source_ref": _ref(),
    }
    values.update(changes)
    return SemanticBindingCandidate(**values)


def test_public_contract_binds_one_exact_normalized_cny_candidate() -> None:
    result = SemanticVariableBinder().bind(_request(), (_candidate(),))

    assert result.status is SemanticBindingStatus.BOUND
    assert result.bound is not None
    assert result.bound.value == Decimal("120000")
    assert result.bound.unit == "万元"
    assert result.bound.source_ref == _ref()
    assert result.bound.metric == "revenue"
    assert result.bound.entity == "acme"
    assert result.bound.period == "2024q1"


def test_percent_units_are_normalized_with_existing_safe_policy() -> None:
    result = SemanticVariableBinder().bind(
        _request(name="margin", metric="margin", unit="%"),
        (_candidate(value="80", metric="margin", unit="％"),),
    )

    assert result.status is SemanticBindingStatus.BOUND
    assert result.bound is not None
    assert result.bound.value == Decimal("0.8")
    assert result.bound.unit == "ratio"


def test_missing_dimension_and_no_match_fail_closed() -> None:
    binder = SemanticVariableBinder()

    assert binder.bind(_request(entity=""), (_candidate(),)).status is SemanticBindingStatus.MISSING
    assert binder.bind(_request(metric="profit"), (_candidate(),)).status is SemanticBindingStatus.MISSING


def test_conflicting_or_multiple_candidates_are_ambiguous_not_order_selected() -> None:
    binder = SemanticVariableBinder()
    first = _candidate(value="12")
    conflicting = _candidate(value="13", source_ref=FormulaSourceRef("doc-a", 1, "doc://a/page/2", "b2"))

    result = binder.bind(_request(), (first, conflicting))

    assert result.status is SemanticBindingStatus.AMBIGUOUS
    assert result.bound is None
    assert result.candidate_count == 2


def test_incompatible_units_do_not_bind() -> None:
    result = SemanticVariableBinder().bind(_request(unit="%"), (_candidate(unit="元"),))

    assert result.status is SemanticBindingStatus.INCOMPATIBLE_UNIT
    assert result.bound is None


def test_blank_request_unit_is_missing_not_scalar() -> None:
    result = SemanticVariableBinder().bind(_request(unit=""), (_candidate(unit="元"),))

    assert result.status is SemanticBindingStatus.MISSING
    assert result.bound is None
    assert "request_unit_missing" in result.reasons


def test_blank_same_document_candidate_unit_is_missing_without_fallback() -> None:
    complete = _candidate(value="12", unit="元")
    unitless = _candidate(
        value="13",
        unit="",
        source_ref=FormulaSourceRef("doc-a", 1, "doc://a/page/2", "b2"),
    )

    result = SemanticVariableBinder().bind(_request(unit="元"), (complete, unitless))

    assert result.status is SemanticBindingStatus.MISSING
    assert result.bound is None
    assert "candidate_unit_missing" in result.reasons


def test_invalid_lineage_fails_closed() -> None:
    invalid = _candidate(source_ref=FormulaSourceRef("", 1, "", "b1"))

    result = SemanticVariableBinder().bind(_request(), (invalid,))

    assert result.status is SemanticBindingStatus.LINEAGE_INVALID
    assert result.bound is None


def test_cross_document_candidate_never_completes_binding() -> None:
    foreign = _candidate(document_id="doc-b", source_ref=_ref(doc_id="doc-b", source="doc://b/page/1"))

    result = SemanticVariableBinder().bind(_request(), (foreign,))

    assert result.status is SemanticBindingStatus.MISSING
    assert result.bound is None
    assert any(reason.startswith("cross_document") for reason in result.reasons)
