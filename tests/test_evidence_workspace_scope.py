from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import ClassificationResult, EvidenceBundle, Question, SolverResult
from evidence_completion.adapters.financial_reports import (
    FinancialEvidenceCompletionAdapter,
    search_financial_candidates,
)
from evidence_completion.contracts import EvidenceRequest
from verification.derived_option_evidence import DerivedOptionEvidence, SourceFact
from verification.evidence_sufficiency import assess_financial_evidence_sufficiency
from verification.evidence_workspace import (
    IDENTITY_CONFLICT,
    IN_SCOPE,
    OUTSIDE_SCOPE,
    UNKNOWN_PAGE,
    EvidenceWorkspaceScope,
)
from verification.financial_claim_ast import parse_financial_claim
from verification.financial_metric_ledger import FinancialFact, FinancialMetricLedger
from verification.financial_report_claims import FinancialContext
from verification.production_typed_evidence import build_production_typed_option_evidence


DOC = "annual_byd_2022"


def _question(option: str = "比亚迪2022年营业收入超过1元") -> Question:
    return Question(
        qid="scope-test",
        domain="financial_reports",
        text="判断下列说法是否正确",
        options={"A": option},
        answer_format="single",
        doc_ids=(DOC,),
    )


def _fact(page_idx: int, *, value: float = 100.0, precision_rank: int = 1) -> FinancialFact:
    return FinancialFact(
        entity_name="比亚迪",
        entity_scope="listed_group",
        statement_scope="consolidated",
        attribution_scope="not_applicable",
        document_id=DOC,
        document_year="2022",
        metric="operating_revenue",
        period="2022",
        comparison_period="",
        raw_value=str(value),
        normalized_value=value,
        raw_unit="元",
        normalized_unit="元",
        scale_multiplier=1.0,
        fact_state="reported",
        per_share_basis="not_applicable",
        source_page=page_idx,
        source_table=0,
        source_row=0,
        canonical_source=(
            f"data/processed_mineru/financial_reports/{DOC}/auto/content_list.json"
            f"#page_idx={page_idx}&table_index=0&row_index=0"
        ),
        local_window=f"营业收入={value}",
        precision_rank=precision_rank,
    )


def _request(metric: str = "operating_revenue") -> EvidenceRequest:
    return EvidenceRequest(
        atom="current_value",
        entity="比亚迪",
        metric=metric,
        period="2022",
        comparison_period="",
        statement_scope="consolidated",
        attribution_scope="not_applicable",
        unit_expectation="元" if metric == "operating_revenue" else "policy_state",
        expected_unit_family="currency" if metric == "operating_revenue" else "unknown",
        peer_unit_family="currency" if metric == "operating_revenue" else "unknown",
        unit_compatibility_required=metric == "operating_revenue",
        per_share_basis_expectation="not_applicable",
        policy_stage_expectation="executed" if metric == "cash_dividend_policy" else "",
        query_terms=("营业收入",) if metric == "operating_revenue" else ("现金分红",),
        allowed_doc_ids=(DOC,),
        reason="scope_test",
        round=1,
    )


def _initial_evidence(source_fact: SourceFact) -> DerivedOptionEvidence:
    return DerivedOptionEvidence(
        qid="scope-test",
        option_label="A",
        claim_type="financial_metric_claim",
        source_facts=(source_fact,),
        formula_or_aggregation="operating_revenue > 1",
        variables={"left": "100", "right": "1", "relation": ">"},
        units={"fact_0": "元"},
        entity_scope=("比亚迪",),
        period_scope=("2022",),
        document_scope=(DOC,),
        result=True,
        status="supported",
        canonical_sources=(source_fact.canonical_source,),
        conflicts=(),
        trusted_for_option_gate=True,
        diagnostics={},
    )


def test_ledger_or_context_scope_contract_is_immutable_bounded_and_fail_closed() -> None:
    scope = EvidenceWorkspaceScope.from_pages(
        [(DOC, 1), (DOC, 2), (DOC, 2)],
        provenance_by_page={(DOC, 1): {"lexical"}, (DOC, 2): {"semantic"}},
    )
    assert scope.allowed_pages == frozenset({(DOC, 1), (DOC, 2)})
    assert scope.provenance_by_page[(DOC, 1)] == frozenset({"lexical"})
    assert scope.classify(DOC, 1) == IN_SCOPE
    assert scope.classify(DOC, 3) == OUTSIDE_SCOPE
    assert scope.classify(DOC, 0) == UNKNOWN_PAGE
    with pytest.raises(TypeError):
        scope.provenance_by_page[(DOC, 1)] = frozenset({"semantic"})  # type: ignore[index]
    with pytest.raises(ValueError):
        EvidenceWorkspaceScope.from_pages([(DOC, page) for page in range(1, 12)])

    conflict = SourceFact(
        doc_id=DOC,
        entity_scope="比亚迪",
        period_scope="2022",
        metric="operating_revenue",
        value=100,
        unit="元",
        canonical_source=f"x#page_idx=1&table_index=0",
        local_window="营业收入=100",
        metadata={"source_page": 0},
    )
    assert scope.audit_source_fact(conflict)["status"] == IDENTITY_CONFLICT


def test_ledger_or_context_zero_based_transform_filters_scoped_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    import verification.financial_metric_ledger as ledger_module

    facts = (_fact(0, value=100.0, precision_rank=2), _fact(1, value=200.0, precision_rank=1))
    monkeypatch.setattr(ledger_module, "load_document_financial_facts", lambda *_args, **_kwargs: facts)

    unscoped = FinancialMetricLedger.from_documents("unused", "financial_reports", (DOC,))
    page1 = EvidenceWorkspaceScope.from_pages([(DOC, 1)])
    page2 = EvidenceWorkspaceScope.from_pages([(DOC, 2)])
    scoped1 = FinancialMetricLedger.from_documents(
        "unused", "financial_reports", (DOC,), workspace_scope=page1
    )
    scoped2 = FinancialMetricLedger.from_documents(
        "unused", "financial_reports", (DOC,), workspace_scope=page2
    )

    assert [fact.source_page for fact in unscoped.facts] == [0, 1]
    assert [fact.source_page for fact in scoped1.facts] == [0]
    assert [fact.source_page for fact in scoped2.facts] == [1]
    assert page1.financial_page_key(DOC, 0) == (DOC, 1)
    assert page1.classify_financial_page(DOC, 1) == OUTSIDE_SCOPE


def test_ledger_or_context_context_scoped_and_unscoped_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    import verification.financial_metric_ledger as ledger_module

    facts = (_fact(0, value=100.0, precision_rank=2), _fact(1, value=200.0, precision_rank=1))
    monkeypatch.setattr(ledger_module, "load_document_financial_facts", lambda *_args, **_kwargs: facts)
    question = _question()

    unscoped = FinancialContext(question, "unused")
    scoped = FinancialContext(
        question,
        "unused",
        workspace_scope=EvidenceWorkspaceScope.from_pages([(DOC, 2)]),
    )

    assert len(unscoped.ledger.facts) == 2
    assert unscoped.fact("比亚迪", "operating_revenue", "2022").source_page == 0
    assert len(scoped.ledger.facts) == 1
    assert scoped.fact("比亚迪", "operating_revenue", "2022").source_page == 1
    assert scoped.workspace_scope is not None


def _write_narrative(root: Path, payload: list[dict[str, object]]) -> None:
    target = root / "financial_reports" / DOC / "auto"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{DOC}_content_list.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_narrative_or_policy_or_completion_or_final_audit_mapped_narrative_respects_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import verification.financial_metric_ledger as ledger_module

    monkeypatch.setattr(ledger_module, "load_document_financial_facts", lambda *_args, **_kwargs: ())
    _write_narrative(
        tmp_path,
        [{"page_idx": 0, "text": "自2019年起连续四年实施股份回购，形成稳定历史记录。"}],
    )
    question = _question("自2019年起连续四年实施股份回购")

    in_scope = FinancialContext(
        question, tmp_path, workspace_scope=EvidenceWorkspaceScope.from_pages([(DOC, 1)])
    )
    out_scope = FinancialContext(
        question, tmp_path, workspace_scope=EvidenceWorkspaceScope.from_pages([(DOC, 2)])
    )
    assert in_scope.narrative(DOC, ("自2019年起", "连续四年", "回购")) is not None
    assert out_scope.narrative(DOC, ("自2019年起", "连续四年", "回购")) is None


def test_narrative_or_policy_or_completion_or_final_audit_unknown_narrative_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import verification.financial_metric_ledger as ledger_module

    monkeypatch.setattr(ledger_module, "load_document_financial_facts", lambda *_args, **_kwargs: ())
    _write_narrative(
        tmp_path,
        [{"text": "自2019年起连续四年实施股份回购，但该条目没有页码身份。"}],
    )
    context = FinancialContext(
        _question("自2019年起连续四年实施股份回购"),
        tmp_path,
        workspace_scope=EvidenceWorkspaceScope.from_pages([(DOC, 1)]),
    )
    assert context.narrative(DOC, ("自2019年起", "连续四年", "回购")) is None


def test_narrative_or_policy_or_completion_or_final_audit_policy_unknown_page_fails_closed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "financial_reports" / DOC / "auto"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{DOC}.md").write_text("公司已实施现金分红。\n", encoding="utf-8")
    request = _request("cash_dividend_policy")
    ledger = FinancialMetricLedger(())

    unscoped = search_financial_candidates(
        request, structured_root=tmp_path, domain="financial_reports", ledger=ledger
    )
    scoped = search_financial_candidates(
        request,
        structured_root=tmp_path,
        domain="financial_reports",
        ledger=ledger,
        workspace_scope=EvidenceWorkspaceScope.from_pages([(DOC, 1)]),
    )
    assert len(unscoped) == 1
    assert scoped == ()


def test_narrative_or_policy_or_completion_or_final_audit_completion_cannot_recover_outside_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import verification.financial_metric_ledger as ledger_module

    monkeypatch.setattr(
        ledger_module,
        "load_document_financial_facts",
        lambda *_args, **_kwargs: (_fact(1, value=200.0),),
    )
    question = _question()
    spec = parse_financial_claim(question, "A", question.options["A"])
    scope = EvidenceWorkspaceScope.from_pages([(DOC, 1)])
    adapter = FinancialEvidenceCompletionAdapter(
        question=question,
        option_label="A",
        claim_spec=spec,
        structured_root="unused",
        workspace_scope=scope,
    )
    hits = search_financial_candidates(
        _request(),
        structured_root="unused",
        domain="financial_reports",
        ledger=adapter.ledger,
        workspace_scope=scope,
    )
    assert adapter.workspace_scope is scope
    assert adapter.ledger.facts == ()
    assert hits == ()


def test_narrative_or_policy_or_completion_or_final_audit_same_scope_used_by_initial_and_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import verification.financial_metric_ledger as ledger_module

    monkeypatch.setattr(
        ledger_module,
        "load_document_financial_facts",
        lambda *_args, **_kwargs: (_fact(0, value=100.0), _fact(1, value=200.0)),
    )
    scope = EvidenceWorkspaceScope.from_pages([(DOC, 1)])
    question = _question()
    context = FinancialContext(question, "unused", workspace_scope=scope)
    spec = parse_financial_claim(question, "A", question.options["A"])
    adapter = FinancialEvidenceCompletionAdapter(
        question=question,
        option_label="A",
        claim_spec=spec,
        structured_root="unused",
        workspace_scope=scope,
    )
    assert context.workspace_scope is scope
    assert adapter.workspace_scope is scope
    assert [fact.source_page for fact in context.ledger.facts] == [0]
    assert [fact.source_page for fact in adapter.ledger.facts] == [0]


def test_narrative_or_policy_or_completion_or_final_audit_rejects_outside_initial_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import verification.financial_metric_ledger as ledger_module

    monkeypatch.setattr(ledger_module, "load_document_financial_facts", lambda *_args, **_kwargs: ())
    question = _question()
    spec = parse_financial_claim(question, "A", question.options["A"])
    outside = SourceFact(
        doc_id=DOC,
        entity_scope="比亚迪 / consolidated / not_applicable",
        period_scope="2022",
        metric="operating_revenue",
        value=100.0,
        unit="元",
        canonical_source=f"x#page_idx=1&table_index=0&row_index=0",
        local_window="营业收入=100",
        metadata={
            "entity_name": "比亚迪",
            "statement_scope": "consolidated",
            "attribution_scope": "not_applicable",
            "source_page": 1,
        },
    )
    initial = _initial_evidence(outside)
    initial_sufficiency = assess_financial_evidence_sufficiency(
        spec, initial, declared_doc_ids=question.doc_ids, option_contract_valid=True
    )
    adapter = FinancialEvidenceCompletionAdapter(
        question=question,
        option_label="A",
        claim_spec=spec,
        structured_root="unused",
        workspace_scope=EvidenceWorkspaceScope.from_pages([(DOC, 1)]),
    )
    result = adapter.complete(
        initial_evidence=initial,
        initial_sufficiency=initial_sufficiency,
        max_rounds=0,
    )

    final = dict(result.post_completion_evidence)
    sufficiency = dict(result.post_completion_sufficiency)
    audit = dict((final.get("diagnostics") or {}).get("workspace_initial_audit") or {})
    assert final["trusted_for_option_gate"] is False
    assert final["source_facts"] == []
    assert sufficiency["safe_to_decide"] is False
    assert audit["counts"][OUTSIDE_SCOPE] == 1
    assert all(
        row["metadata"].get("source_page") == 0
        for row in result.merged_source_facts
    )


_UNSET_SCOPE = object()


def _product_route_payload(
    monkeypatch: pytest.MonkeyPatch,
    facts: tuple[FinancialFact, ...],
    workspace_scope: object = _UNSET_SCOPE,
) -> dict[str, object]:
    import verification.financial_metric_ledger as ledger_module

    monkeypatch.setattr(
        ledger_module,
        "load_document_financial_facts",
        lambda *_args, **_kwargs: facts,
    )
    metadata: dict[str, object] = {"structured_table_root": "unused"}
    if workspace_scope is not _UNSET_SCOPE:
        metadata["evidence_workspace_scope"] = workspace_scope
    question = _question()
    bundle = EvidenceBundle(
        question=question,
        classification=ClassificationResult(labels=()),
        candidates=(),
        prompt_context="",
        estimated_tokens=0,
        metadata=metadata,
    )
    result = SolverResult(
        qid=question.qid,
        answer="",
        solver="scope-product-route-test",
        metadata={"judgments": {"A": "unresolved"}},
    )
    return build_production_typed_option_evidence(bundle, result)


def _product_route_derived(payload: dict[str, object]) -> dict[str, object]:
    rows = payload.get("production_derived_option_evidence") or []
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def test_product_route_scope_filters_outside_fact_and_keeps_in_scope_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside_scope = EvidenceWorkspaceScope.from_pages([(DOC, 1)])
    outside_payload = _product_route_payload(
        monkeypatch,
        (_fact(1, value=200.0),),
        outside_scope,
    )
    outside = _product_route_derived(outside_payload)
    assert outside["trusted_for_option_gate"] is False
    assert outside["source_facts"] == []
    assert (outside.get("diagnostics") or {}).get("workspace_scope_enabled") is True

    in_scope = EvidenceWorkspaceScope.from_pages([(DOC, 2)])
    in_scope_payload = _product_route_payload(
        monkeypatch,
        (_fact(1, value=200.0),),
        in_scope,
    )
    inside = _product_route_derived(in_scope_payload)
    assert inside["status"] == "supported"
    assert inside["trusted_for_option_gate"] is True
    assert len(inside["source_facts"]) == 1
    assert inside["source_facts"][0]["metadata"]["source_page"] == 1
    assert (inside.get("diagnostics") or {}).get("workspace_scope_enabled") is True


def test_product_route_scope_preserves_identity_and_rejects_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import verification.derived_claim_router as router_module

    captured: list[object] = []

    def fake_financial_builder(
        question: Question,
        structured_root: str | Path,
        workspace_scope: EvidenceWorkspaceScope | None = None,
    ) -> tuple[DerivedOptionEvidence, ...]:
        assert question.domain == "financial_reports"
        assert str(structured_root) == "unused"
        captured.append(workspace_scope)
        return ()

    monkeypatch.setattr(
        router_module,
        "build_financial_report_option_evidence",
        fake_financial_builder,
    )
    scope = EvidenceWorkspaceScope.from_pages([(DOC, 1)])
    _product_route_payload(monkeypatch, (), scope)
    assert captured[-1] is scope
    assert captured[-1].allowed_pages == frozenset({(DOC, 1)})

    _product_route_payload(
        monkeypatch,
        (),
        {"allowed_pages": [[DOC, 1]]},
    )
    assert captured[-1] is None


def test_product_route_unscoped_preserves_historical_financial_fact_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _product_route_payload(
        monkeypatch,
        (_fact(1, value=200.0),),
    )
    derived = _product_route_derived(payload)
    assert derived["status"] == "supported"
    assert derived["trusted_for_option_gate"] is True
    assert len(derived["source_facts"]) == 1
    assert derived["source_facts"][0]["metadata"]["source_page"] == 1
    assert (derived.get("diagnostics") or {}).get("workspace_scope_enabled") is False
