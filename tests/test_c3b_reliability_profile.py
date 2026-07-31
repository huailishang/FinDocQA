"""C3-B ReliabilityProfile integration tests (offline only)."""
from __future__ import annotations

from dataclasses import replace
from itertools import combinations, product

from calculation import FormulaContextRecovery, FormulaEvidence, FormulaGateStatus, FormulaSourceRef
from calculation.contracts import FormulaGateResult
from calculation.recovery import FormulaRecoveryResult
from document.contracts import (
    CanonicalBlock,
    CanonicalBlockType,
    CanonicalDocument,
    CanonicalFormula,
    CanonicalPage,
    CanonicalTable,
    SourceLineage,
)
from document.store import InMemoryDocumentStore
from evaluation.adapters.calculation import adapt_c3b_recovery_result
from evaluation.c3b_profile import C3B_MODULE_ID, build_c3b_reliability_profile
from evaluation.contracts import EvaluationResult, GateStatus
from evaluation.gates import ReliabilityGate
from evaluation.oracles.c3b import (
    C3B_DECISION_TABLE_V1,
    C3B_PAIRWISE_CASES_V1,
    C3B_SELECTED_3WAY_CASES_V1,
    C3BDecisionInput,
    C3BSafetyExpectation,
    c3b_evidence_fingerprint,
    evaluate_c3b_decision,
)


def _lineage(page: int, *, source: str = "doc://insurance_demo") -> SourceLineage:
    return SourceLineage(
        source_type="test",
        source_path=f"{source}/page/{page}",
        parser_name="test",
        page_number=page,
    )


def _block(
    block_id: str,
    page: int,
    order: int,
    text: str,
    *,
    block_type: CanonicalBlockType = CanonicalBlockType.TEXT,
    formula_id: str | None = None,
    lineage: SourceLineage | None = None,
) -> CanonicalBlock:
    return CanonicalBlock(
        block_id=block_id,
        page_number=page,
        block_type=block_type,
        text=text,
        reading_order=order,
        formula_id=formula_id,
        lineage=_lineage(page) if lineage is None else lineage,
    )


def _document(*pages: CanonicalPage) -> CanonicalDocument:
    return CanonicalDocument(
        document_id="insurance_demo",
        domain="insurance",
        title="demo",
        source_type="test",
        source_uri="doc://insurance_demo",
        parser_name="test",
        parser_version="1",
        pages=tuple(pages),
    )


def _evidence(*, refs: tuple[FormulaSourceRef, ...] | None = None) -> FormulaEvidence:
    return FormulaEvidence(
        raw_formula="赔付金额 = expense * ratio",
        normalized_expression="expense * ratio",
        source_refs=refs
        or (
            FormulaSourceRef(
                doc_id="insurance_demo",
                page_number=1,
                source="doc://insurance_demo/page/1",
                block_id="formula",
                excerpt="赔付金额 = expense * ratio",
            ),
        ),
        metadata={"domain": "insurance", "formula_id": "f1"},
    )


def _recover(page: CanonicalPage, *, evidence: FormulaEvidence | None = None) -> FormulaRecoveryResult:
    store = InMemoryDocumentStore.from_documents((_document(page),))
    return FormulaContextRecovery(store).recover(evidence or _evidence())


def _safe_page(*, extra_text: str | None = None, formulas: tuple[CanonicalFormula, ...] = ()) -> CanonicalPage:
    blocks = [
        _block("vars", 1, 0, "expense = 100元\nratio = 80%"),
        _block(
            "formula",
            1,
            1,
            "赔付金额 = expense * ratio",
            block_type=CanonicalBlockType.FORMULA,
            formula_id="f1",
        ),
    ]
    if extra_text is not None:
        blocks.append(_block("unrelated", 1, 2, extra_text))
    return CanonicalPage(
        page_number=1,
        text="\n".join(block.text for block in blocks),
        blocks=tuple(blocks),
        formulas=formulas,
        lineage=_lineage(1),
    )


def _gate(result: EvaluationResult) -> GateStatus:
    decision = ReliabilityGate().evaluate([result], build_c3b_reliability_profile())
    return decision.status


def test_c3b_profile_declares_high_risk_contract_and_techniques() -> None:
    profile = build_c3b_reliability_profile()

    assert profile.module_id == C3B_MODULE_ID
    assert profile.risk_level == "HIGH"
    assert set(profile.failure_modes) >= {
        "ambiguous_formula_accepted",
        "unresolved_dependency_accepted",
        "cross_document_recovery",
        "physical_neighbor_as_binding",
        "incomplete_lineage_accepted",
    }
    assert set(profile.required_invariants) >= {
        "ambiguity_must_not_execute",
        "unresolved_dependency_must_not_pass",
        "cross_document_recovery_forbidden",
        "unrelated_evidence_must_not_change_decision",
        "incomplete_lineage_must_not_execute",
    }
    assert set(profile.test_techniques) >= {
        "decision_table",
        "combinatorial_2way",
        "selected_3way",
        "property_based",
        "metamorphic",
        "mutation_for_critical_gates",
    }


def test_duplicate_canonical_formula_maps_to_safe_invariants() -> None:
    formulas = (
        CanonicalFormula("f1", 1, "expense * ratio", lineage=_lineage(1)),
        CanonicalFormula("f1", 1, "expense * ratio", lineage=_lineage(1)),
    )
    recovery = _recover(_safe_page(formulas=formulas))

    assert recovery.status is FormulaGateStatus.REVIEW
    assert recovery.ready_for_execution is False

    evaluation = adapt_c3b_recovery_result(case_id="duplicate", result=recovery)
    assert evaluation.metric("ambiguity_must_not_execute").passed is True
    assert _gate(evaluation) is GateStatus.PASS


def test_zero_canonical_formula_with_sufficient_block_context_passes_without_false_ambiguity() -> None:
    recovery = _recover(_safe_page())

    assert recovery.status is FormulaGateStatus.PASS
    assert recovery.ready_for_execution is True

    evaluation = adapt_c3b_recovery_result(case_id="zero-formula", result=recovery)
    assert evaluation.metric("ambiguity_must_not_execute").passed is True
    assert evaluation.metric("unresolved_dependency_must_not_pass").passed is True
    assert _gate(evaluation) is GateStatus.PASS


def test_cross_document_recovery_rejection_maps_to_safe_invariant() -> None:
    refs = (
        FormulaSourceRef(
            doc_id="insurance_demo",
            page_number=1,
            source="doc://insurance_demo/page/1",
            block_id="formula",
        ),
        FormulaSourceRef(
            doc_id="other_doc",
            page_number=1,
            source="doc://other_doc/page/1",
            block_id="other",
        ),
    )
    recovery = _recover(_safe_page(), evidence=_evidence(refs=refs))

    assert recovery.status is FormulaGateStatus.FAIL
    assert recovery.ready_for_execution is False
    assert "cross_document_recovery_forbidden" in recovery.reasons

    evaluation = adapt_c3b_recovery_result(case_id="cross-doc", result=recovery)
    assert evaluation.metric("cross_document_recovery_forbidden").passed is True
    assert _gate(evaluation) is GateStatus.PASS


def test_unresolved_variable_rejection_maps_to_safe_invariant() -> None:
    page = CanonicalPage(
        page_number=1,
        text="expense = 100元\n赔付金额 = expense * ratio",
        blocks=(
            _block("vars", 1, 0, "expense = 100元"),
            _block(
                "formula",
                1,
                1,
                "赔付金额 = expense * ratio",
                block_type=CanonicalBlockType.FORMULA,
                formula_id="f1",
            ),
        ),
        lineage=_lineage(1),
    )
    recovery = _recover(page)

    assert recovery.status is not FormulaGateStatus.PASS
    assert recovery.ready_for_execution is False
    assert any(reason.startswith("missing_variable_binding:ratio") for reason in recovery.reasons)

    evaluation = adapt_c3b_recovery_result(case_id="missing-ratio", result=recovery)
    assert evaluation.metric("unresolved_dependency_must_not_pass").passed is True
    assert _gate(evaluation) is GateStatus.PASS


def test_unrelated_neighbor_does_not_change_safe_decision() -> None:
    baseline = _recover(_safe_page())
    mutated = _recover(_safe_page(extra_text="unrelated_metric = 999"))

    assert baseline.status is FormulaGateStatus.PASS
    assert mutated.status is FormulaGateStatus.PASS
    assert baseline.ready_for_execution is True
    assert mutated.ready_for_execution is True

    evaluation = adapt_c3b_recovery_result(
        case_id="unrelated-neighbor",
        result=mutated,
        baseline_result=baseline,
    )
    assert evaluation.metric("unrelated_evidence_must_not_change_decision").passed is True
    assert _gate(evaluation) is GateStatus.PASS


def test_physical_neighbor_same_variable_does_not_change_binding_identity() -> None:
    baseline = _recover(_safe_page())
    mutated = _recover(_safe_page(extra_text="ratio = 70%"))

    baseline_fingerprint = c3b_evidence_fingerprint(baseline)
    mutated_fingerprint = c3b_evidence_fingerprint(mutated)

    assert dict(baseline_fingerprint.variable_binding_identity)["ratio"].startswith("0.8|ratio|")
    assert dict(mutated_fingerprint.variable_binding_identity)["ratio"].startswith("0.8|ratio|")
    assert mutated_fingerprint == baseline_fingerprint


def test_cross_document_same_name_does_not_change_binding_identity() -> None:
    page = _safe_page()
    baseline = _recover(page)
    unrelated_page = CanonicalPage(
        page_number=1,
        text="ratio = 70%",
        blocks=(_block("other-ratio", 1, 0, "ratio = 70%", lineage=_lineage(1, source="doc://other")),),
        lineage=_lineage(1, source="doc://other"),
    )
    unrelated_document = CanonicalDocument(
        document_id="other",
        domain="insurance",
        title="other",
        source_type="test",
        source_uri="doc://other",
        parser_name="test",
        parser_version="1",
        pages=(unrelated_page,),
    )
    store = InMemoryDocumentStore.from_documents((_document(page), unrelated_document))
    mutated = FormulaContextRecovery(store).recover(_evidence())

    assert c3b_evidence_fingerprint(mutated) == c3b_evidence_fingerprint(baseline)


def _forged_ready_result(*, reasons: tuple[str, ...]) -> FormulaRecoveryResult:
    evidence = _evidence()
    return FormulaRecoveryResult(
        status=FormulaGateStatus.PASS,
        recovered_evidence=evidence,
        recovered_source_refs=evidence.source_refs,
        reasons=reasons,
        gate_result=FormulaGateResult(status=FormulaGateStatus.PASS),
    )


def test_false_accept_ambiguous_formula_is_blocked_by_generic_gate() -> None:
    forged = _forged_ready_result(reasons=())
    evaluation = adapt_c3b_recovery_result(
        case_id="false-ambiguity",
        result=forged,
        expectation=C3BSafetyExpectation(ambiguity_expected=True),
    )

    assert forged.ready_for_execution is True
    assert evaluation.metric("ambiguity_must_not_execute").passed is False
    assert _gate(evaluation) is GateStatus.FAIL


def test_false_accept_unresolved_dependency_is_blocked_by_generic_gate() -> None:
    forged = _forged_ready_result(reasons=())
    evaluation = adapt_c3b_recovery_result(
        case_id="false-unresolved",
        result=forged,
        expectation=C3BSafetyExpectation(unresolved_dependency_expected=True),
    )

    assert evaluation.metric("unresolved_dependency_must_not_pass").passed is False
    assert _gate(evaluation) is GateStatus.FAIL


def test_false_accept_cross_document_recovery_is_blocked_by_generic_gate() -> None:
    forged = _forged_ready_result(reasons=())
    evaluation = adapt_c3b_recovery_result(
        case_id="false-cross-doc",
        result=forged,
        expectation=C3BSafetyExpectation(cross_document_expected=True),
    )

    assert evaluation.metric("cross_document_recovery_forbidden").passed is False
    assert _gate(evaluation) is GateStatus.FAIL


def test_missing_required_c3b_invariant_is_blocked_by_generic_gate() -> None:
    evaluation = adapt_c3b_recovery_result(case_id="missing-invariant", result=_recover(_safe_page()))
    stripped = EvaluationResult(
        case_id=evaluation.case_id,
        module_id=evaluation.module_id,
        metrics=tuple(
            metric
            for metric in evaluation.metrics
            if metric.metric_name != "ambiguity_must_not_execute"
        ),
        diagnostics=evaluation.diagnostics,
    )

    assert _gate(stripped) is GateStatus.FAIL


def test_decision_table_v1_matches_explicit_expected_outcomes() -> None:
    for row in C3B_DECISION_TABLE_V1:
        actual = evaluate_c3b_decision(row.inputs)
        assert actual == row.expected, row.case_id


def _pair_universe(rows: tuple[C3BDecisionInput, ...]) -> set[tuple[str, str, str, str]]:
    factors = (
        "formula_count",
        "formula_identity",
        "lineage",
        "variable_binding",
        "cross_document",
        "linked_table",
        "continuation",
    )
    result: set[tuple[str, str, str, str]] = set()
    for row in rows:
        for left, right in combinations(factors, 2):
            result.add((left, str(getattr(row, left)), right, str(getattr(row, right))))
    return result


def _all_valid_input_rows() -> tuple[C3BDecisionInput, ...]:
    rows: list[C3BDecisionInput] = []
    for values in product(
        ("0", "1", "2+"),
        ("unique", "ambiguous"),
        ("complete", "missing"),
        ("complete", "incomplete"),
        ("no", "yes"),
        ("none", "unique", "ambiguous"),
        ("none", "unique", "ambiguous"),
    ):
        formula_count, formula_identity, *_ = values
        if formula_identity == "ambiguous" and formula_count != "2+":
            continue
        rows.append(C3BDecisionInput(*values))
    return tuple(rows)


def _recover_decision_input(inputs: C3BDecisionInput) -> FormulaRecoveryResult:
    """Bounded evaluation builder that drives the production recovery."""

    variables = "expense = 100元"
    if inputs.variable_binding == "complete":
        variables += "\nratio = 80%"
    variable_block = _block("vars", 1, 0, variables)
    formula_text = "赔付金额 = expense * ratio"
    if inputs.continuation != "none":
        formula_text += "。变量定义见下页"
    formula_block = _block(
        "formula",
        1,
        1,
        formula_text,
        block_type=CanonicalBlockType.FORMULA,
        formula_id="f1",
    )
    if inputs.lineage == "missing":
        formula_block = replace(formula_block, lineage=None)

    formula_metadata: dict[str, object] = {}
    tables: tuple[CanonicalTable, ...] = ()
    if inputs.linked_table == "unique":
        formula_metadata["linked_table_id"] = "t1"
        tables = (
            CanonicalTable("t1", 1, markdown="expense = 100元\nratio = 80%", lineage=_lineage(1)),
        )
    elif inputs.linked_table == "ambiguous":
        formula_metadata["linked_table_id"] = "t1"
        tables = (
            CanonicalTable("t1", 1, markdown="expense = 100元", lineage=_lineage(1)),
            CanonicalTable("t1", 1, markdown="ratio = 80%", lineage=_lineage(1)),
        )

    formulas: tuple[CanonicalFormula, ...] = ()
    if inputs.formula_count == "1":
        formulas = (CanonicalFormula("f1", 1, "expense * ratio", lineage=_lineage(1), metadata=formula_metadata),)
    elif inputs.formula_count == "2+":
        second_id = "f1" if inputs.formula_identity == "ambiguous" else "f2"
        formulas = (
            CanonicalFormula("f1", 1, "expense * ratio", lineage=_lineage(1), metadata=formula_metadata),
            CanonicalFormula(second_id, 1, "expense * ratio", lineage=_lineage(1)),
        )

    page1 = CanonicalPage(
        page_number=1,
        text=f"{variables}\n{formula_text}",
        blocks=(variable_block, formula_block),
        tables=tables,
        formulas=formulas,
        lineage=_lineage(1),
    )
    pages = [page1]
    if inputs.continuation != "none":
        continuation_blocks = [
            _block("continuation-a", 2, 0, "expense = 100元\nratio = 80%",),
        ]
        if inputs.continuation == "ambiguous":
            continuation_blocks.append(_block("continuation-b", 2, 1, "ratio = 70%"))
        pages.append(
            CanonicalPage(
                page_number=2,
                text="\n".join(block.text for block in continuation_blocks),
                blocks=tuple(continuation_blocks),
                lineage=_lineage(2),
            )
        )

    evidence = _evidence()
    if inputs.linked_table != "none":
        evidence = replace(
            evidence,
            linked_table_refs=("t1",),
        )
    if inputs.cross_document == "yes":
        evidence = _evidence(
            refs=(
                *tuple(evidence.source_refs),
                FormulaSourceRef("other_doc", 1, "doc://other_doc/page/1", "other"),
            )
        )
    store = InMemoryDocumentStore.from_documents((_document(*pages),))
    return FormulaContextRecovery(store).recover(evidence)


def test_pairwise_v1_covers_all_valid_factor_value_pairs_without_full_cartesian_product() -> None:
    valid_rows = _all_valid_input_rows()
    required_pairs = _pair_universe(valid_rows)
    actual_pairs = _pair_universe(C3B_PAIRWISE_CASES_V1)

    assert len(valid_rows) == 288
    assert len(C3B_PAIRWISE_CASES_V1) == 12
    assert actual_pairs >= required_pairs
    assert len(C3B_PAIRWISE_CASES_V1) < len(valid_rows) / 10


def test_pairwise_v1_each_case_has_a_deterministic_expected_decision() -> None:
    for inputs in C3B_PAIRWISE_CASES_V1:
        expected = evaluate_c3b_decision(inputs)
        assert expected.status in {FormulaGateStatus.PASS, FormulaGateStatus.REVIEW, FormulaGateStatus.FAIL}
        if expected.status is FormulaGateStatus.PASS:
            assert expected.ready_for_execution is True
        else:
            assert expected.ready_for_execution is False


def test_pairwise_v1_all_rows_drive_production_recovery() -> None:
    observed = []
    for inputs in C3B_PAIRWISE_CASES_V1:
        actual = _recover_decision_input(inputs)
        expected = evaluate_c3b_decision(inputs)
        observed.append((inputs, actual.status, actual.ready_for_execution))
        assert actual.status is expected.status, inputs
        assert actual.ready_for_execution is expected.ready_for_execution, inputs
    assert len(observed) == 12


def test_selected_3way_v1_covers_critical_safety_combinations() -> None:
    rows = {row.case_id: row for row in C3B_SELECTED_3WAY_CASES_V1}

    assert rows["identity_binding_lineage"].inputs.formula_identity == "ambiguous"
    assert rows["identity_binding_lineage"].inputs.variable_binding == "incomplete"
    assert rows["identity_binding_lineage"].inputs.lineage == "missing"

    assert rows["identity_table_continuation"].inputs.formula_identity == "ambiguous"
    assert rows["identity_table_continuation"].inputs.linked_table == "ambiguous"
    assert rows["identity_table_continuation"].inputs.continuation == "ambiguous"

    assert rows["crossdoc_binding_lineage"].inputs.cross_document == "yes"
    assert rows["crossdoc_binding_lineage"].inputs.variable_binding == "incomplete"
    assert rows["crossdoc_binding_lineage"].inputs.lineage == "missing"

    for row in rows.values():
        assert evaluate_c3b_decision(row.inputs) == row.expected
        assert row.expected.ready_for_execution is False


def test_selected_3way_v1_all_rows_drive_production_recovery() -> None:
    observed = []
    for row in C3B_SELECTED_3WAY_CASES_V1:
        actual = _recover_decision_input(row.inputs)
        observed.append((row.case_id, actual.status, actual.ready_for_execution))
        assert actual.status is row.expected.status, row.case_id
        assert actual.ready_for_execution is row.expected.ready_for_execution, row.case_id
    assert len(observed) == 3


def test_false_accept_incomplete_lineage_is_blocked_by_generic_gate() -> None:
    forged = _forged_ready_result(reasons=())
    evaluation = adapt_c3b_recovery_result(
        case_id="false-lineage",
        result=forged,
        expectation=C3BSafetyExpectation(lineage_complete_expected=False),
    )

    assert evaluation.metric("incomplete_lineage_must_not_execute").passed is False
    assert _gate(evaluation) is GateStatus.FAIL


def test_unrelated_evidence_decision_change_is_blocked_by_generic_gate() -> None:
    baseline = _forged_ready_result(reasons=())
    mutated = FormulaRecoveryResult(
        status=FormulaGateStatus.REVIEW,
        recovered_evidence=baseline.recovered_evidence,
        recovered_source_refs=baseline.recovered_source_refs,
        reasons=("unrelated_neighbor_changed_decision",),
        gate_result=FormulaGateResult(status=FormulaGateStatus.REVIEW),
    )
    evaluation = adapt_c3b_recovery_result(
        case_id="false-unrelated-change",
        result=mutated,
        baseline_result=baseline,
    )

    assert evaluation.metric("unrelated_evidence_must_not_change_decision").passed is False
    assert _gate(evaluation) is GateStatus.FAIL


def test_c3b_adapter_does_not_mutate_business_result() -> None:
    recovery = _recover(_safe_page())
    before = recovery.to_dict()

    adapt_c3b_recovery_result(case_id="immutability", result=recovery)

    assert recovery.to_dict() == before


def test_c3b_r1_evidence_manifest() -> None:
    """Emit bounded, reviewable AC evidence when this test is run with ``-s``."""

    attacks = {
        "ambiguity": C3BSafetyExpectation(ambiguity_expected=True),
        "unresolved_dependency": C3BSafetyExpectation(unresolved_dependency_expected=True),
        "incomplete_lineage": C3BSafetyExpectation(lineage_complete_expected=False),
        "cross_document": C3BSafetyExpectation(cross_document_expected=True),
    }
    for name, expectation in attacks.items():
        forged = _forged_ready_result(reasons=())
        evaluation = adapt_c3b_recovery_result(
            case_id=f"evidence-{name}",
            result=forged,
            expectation=expectation,
        )
        print(
            "AC01",
            name,
            f"expectation={expectation}",
            "forged_status=PASS",
            "forged_ready=True",
            "forged_reasons=()",
            f"violations={evaluation.violations}",
            f"gate={_gate(evaluation).value}",
        )

    for index, inputs in enumerate(C3B_PAIRWISE_CASES_V1, start=1):
        actual = _recover_decision_input(inputs)
        expected = evaluate_c3b_decision(inputs)
        print(
            "AC02",
            f"row={index}/12",
            "production_call=FormulaContextRecovery.recover",
            f"inputs={inputs}",
            f"actual=({actual.status.value},{actual.ready_for_execution})",
            f"expected=({expected.status.value},{expected.ready_for_execution})",
        )
    for row in C3B_SELECTED_3WAY_CASES_V1:
        actual = _recover_decision_input(row.inputs)
        print(
            "AC03",
            f"case={row.case_id}",
            "production_call=FormulaContextRecovery.recover",
            f"actual=({actual.status.value},{actual.ready_for_execution})",
            f"expected=({row.expected.status.value},{row.expected.ready_for_execution})",
        )

    baseline = _recover(_safe_page())
    neighbor = _recover(_safe_page(extra_text="ratio = 70%"))
    print("AC04", f"before={c3b_evidence_fingerprint(baseline)}")
    print("AC04", f"after={c3b_evidence_fingerprint(neighbor)}")

    unrelated_page = CanonicalPage(
        page_number=1,
        text="ratio = 70%",
        blocks=(_block("other-ratio", 1, 0, "ratio = 70%", lineage=_lineage(1, source="doc://other")),),
        lineage=_lineage(1, source="doc://other"),
    )
    unrelated_document = CanonicalDocument(
        document_id="other",
        domain="insurance",
        title="other",
        source_type="test",
        source_uri="doc://other",
        parser_name="test",
        parser_version="1",
        pages=(unrelated_page,),
    )
    store = InMemoryDocumentStore.from_documents((_document(_safe_page()), unrelated_document))
    cross_document_neighbor = FormulaContextRecovery(store).recover(_evidence())
    print("AC05", f"before={c3b_evidence_fingerprint(baseline)}")
    print("AC05", f"after={c3b_evidence_fingerprint(cross_document_neighbor)}")
