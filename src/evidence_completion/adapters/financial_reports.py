"""Financial-report adapter for true typed evidence completion."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import Question
from evidence_completion.contracts import (
    CompletionResult,
    EvidenceGrade,
    EvidenceHit,
    EvidenceRequest,
    TypedEvidenceFact,
)
from retrieval.evidence_quality_grader import grade_evidence_hit
from retrieval.financial_source_locator import (
    infer_policy_stage,
    resolve_financial_context,
    source_line_number,
)
from verification.claim_fact_binding import (
    assess_claim_fact_bindings,
    canonical_unit_for_family,
    fact_identity,
    fact_mapping,
    metric_unit_family,
    per_share_basis_from_unit,
    unique_facts,
    unit_family,
)
from verification.derived_option_evidence import DerivedOptionEvidence, SourceFact
from verification.evidence_gap_classifier import classify_financial_gaps, retrievable_atoms
from verification.evidence_sufficiency import (
    FinancialEvidenceSufficiency,
    assess_financial_evidence_sufficiency,
)
from verification.financial_claim_ast import FinancialClaimSpec
from verification.financial_claim_evaluator import evaluate_financial_claim_from_source_facts
from verification.financial_metric_ledger import (
    FinancialFact,
    FinancialMetricLedger,
    document_meta,
)

SCHEMA_VERSION = "financial_true_evidence_completion_v1"
MAX_ROUNDS = 2

_METRIC_TERMS: Mapping[str, tuple[str, ...]] = {
    "operating_revenue": ("营业收入", "营收"),
    "total_operating_revenue": ("营业总收入",),
    "parent_attributable_net_profit": (
        "归属于上市公司股东的净利润", "归属于母公司股东的净利润",
        "归母净利润", "母公司股东应占利润",
    ),
    "operating_cash_flow_net": (
        "经营活动产生的现金流量净额", "经营活动现金净流入", "经营现金流",
    ),
    "financing_cash_flow_net": ("筹资活动产生的现金流量净额",),
    "rd_investment": ("研发投入",),
    "rd_expense": ("研发费用",),
    "rd_investment_ratio": ("研发投入占营业收入比例", "研发投入强度"),
    "rd_expense_ratio": ("研发费用占营业收入比重", "研发费用率"),
    "cash_dividend_amount": ("现金分红金额", "派发现金分红"),
    "cash_dividend_profit_ratio": ("现金分红", "净利润"),
    "cash_dividend_per_share": ("每股", "派发现金"),
    "cash_dividend_per_10_shares": ("每10股", "现金分红"),
    "overseas_revenue": ("境外收入",),
    "overseas_revenue_ratio": ("境外收入", "占比"),
    "new_contract_amount": ("新签合同额",),
    "share_repurchase_history": ("回购", "连续四年"),
    "cash_dividend_policy": ("现金分红", "实施"),
}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _source_fact_from_mapping(value: Mapping[str, Any]) -> SourceFact:
    return SourceFact(
        doc_id=str(value.get("doc_id") or ""),
        entity_scope=str(value.get("entity_scope") or ""),
        period_scope=str(value.get("period_scope") or ""),
        metric=str(value.get("metric") or ""),
        value=value.get("value"),
        unit=str(value.get("unit") or ""),
        canonical_source=str(value.get("canonical_source") or ""),
        local_window=str(value.get("local_window") or ""),
        fact_state=str(value.get("fact_state") or "reported"),
        metadata=dict(value.get("metadata") or {}),
    )


def _fact_entity(fact: SourceFact) -> str:
    metadata = dict(fact.metadata or {})
    return str(
        metadata.get("entity_name")
        or metadata.get("entity")
        or str(fact.entity_scope).split(" / ", 1)[0]
        or ""
    ).strip()


def _financial_fact_mapping(fact: FinancialFact) -> dict[str, Any]:
    return {
        "entity": fact.entity_name,
        "entity_name": fact.entity_name,
        "metric": fact.metric,
        "period": fact.period,
        "comparison_period": fact.comparison_period,
        "value": fact.normalized_value,
        "unit": fact.normalized_unit,
        "statement_scope": fact.statement_scope,
        "attribution_scope": fact.attribution_scope,
        "fact_state": fact.fact_state,
        "doc_id": fact.document_id,
        "canonical_source": fact.canonical_source,
        "local_window": fact.local_window,
        "metadata": {
            **dict(fact.metadata or {}),
            "raw_value": fact.raw_value,
            "raw_unit": fact.raw_unit,
            "precision_rank": fact.precision_rank,
            "per_share_basis": fact.per_share_basis,
        },
    }


def _typed_from_candidate(candidate: Mapping[str, Any], grade: EvidenceGrade) -> TypedEvidenceFact:
    return TypedEvidenceFact(
        entity=str(candidate.get("entity") or candidate.get("entity_name") or ""),
        metric=str(candidate.get("metric") or ""),
        period=str(candidate.get("period") or ""),
        comparison_period=str(candidate.get("comparison_period") or ""),
        value=candidate.get("value"),
        unit=str(candidate.get("unit") or ""),
        statement_scope=str(candidate.get("statement_scope") or ""),
        attribution_scope=str(candidate.get("attribution_scope") or ""),
        fact_state=str(candidate.get("fact_state") or "reported"),
        doc_id=str(candidate.get("doc_id") or ""),
        canonical_source=str(candidate.get("canonical_source") or ""),
        local_window=str(candidate.get("local_window") or ""),
        parse_method="financial_metric_ledger_or_policy_narrative",
        quality_grade=grade,
        metadata=dict(candidate.get("metadata") or {}),
    )


def _source_from_typed(fact: TypedEvidenceFact) -> SourceFact:
    return SourceFact(
        doc_id=fact.doc_id,
        entity_scope=f"{fact.entity} / {fact.statement_scope} / {fact.attribution_scope}",
        period_scope=fact.period,
        metric=fact.metric,
        value=fact.value,
        unit=fact.unit,
        canonical_source=fact.canonical_source,
        local_window=fact.local_window,
        fact_state=fact.fact_state,
        metadata={
            **dict(fact.metadata or {}),
            "entity_name": fact.entity,
            "comparison_period": fact.comparison_period,
            "statement_scope": fact.statement_scope,
            "attribution_scope": fact.attribution_scope,
            "parse_method": fact.parse_method,
            "quality_grade": fact.quality_grade.value,
        },
    )


def _expected_entity_metric_period(
    spec: FinancialClaimSpec,
    atom: str,
) -> tuple[str, str, str]:
    if atom in {"comparison_value", "comparison_period"}:
        if spec.relation in {"multiplier_gt", "multiplier_lt"} and len(spec.entity_refs) >= 2:
            return spec.entity_refs[1], spec.metric, spec.current_period
        if spec.relation in {"ratio_gt", "ratio_lt"} and spec.comparator_metric:
            return spec.entity_refs[0], spec.comparator_metric, spec.current_period
        return spec.entity_refs[0] if spec.entity_refs else "", spec.metric, spec.comparison_period
    return spec.entity_refs[0] if spec.entity_refs else "", spec.metric, spec.current_period


def _unit_expectation(spec: FinancialClaimSpec, atom: str, metric: str) -> str:
    direct_scalar = bool(
        spec.relation in {"eq", "approx_eq", "gt", "lt", "gte", "lte"}
        and spec.value is not None
        and metric == spec.metric
        and atom not in {"comparison_value", "comparison_period"}
    )
    if direct_scalar and spec.value_unit:
        return spec.value_unit
    return canonical_unit_for_family(metric_unit_family(metric))


def _request_attribution_scope(spec: FinancialClaimSpec, metric: str) -> str:
    if metric == "parent_attributable_net_profit":
        return "parent_attributable"
    return spec.attribution_scope if metric == spec.metric else "not_applicable"


def build_financial_request(
    spec: FinancialClaimSpec,
    atom: str,
    *,
    round_number: int,
) -> EvidenceRequest:
    entity, metric, period = _expected_entity_metric_period(spec, atom)
    expected_family = metric_unit_family(metric)
    expected_unit = _unit_expectation(spec, atom, metric)
    peer_metric = (
        spec.comparator_metric
        if spec.relation in {"ratio_gt", "ratio_lt"} and spec.comparator_metric
        else spec.metric
    )
    peer_family = metric_unit_family(peer_metric)
    terms = [entity, *_METRIC_TERMS.get(metric, (metric,)), period]
    refinement_dimensions: tuple[str, ...] = ()
    if round_number == 2:
        terms.extend((
            spec.comparison_period, spec.policy_stage, spec.statement_scope,
            "合并口径", "母公司口径", "单位", "本期", "上期",
        ))
        refinement_dimensions = (
            "parent_heading", "table_header", "adjacent_rows",
            "comparison_period_header", "unit_header", "statement_scope_title",
            "attribution_scope_title", "policy_stage_context", "metric_synonyms",
        )
    per_basis = (
        "per_share" if expected_family == "currency_per_share"
        else "per_10_shares" if expected_family == "currency_per_10_shares"
        else "not_applicable"
    )
    return EvidenceRequest(
        atom=atom,
        entity=entity,
        metric=metric,
        period=period,
        comparison_period=spec.comparison_period if atom == "comparison_period" else "",
        statement_scope=spec.statement_scope,
        attribution_scope=_request_attribution_scope(spec, metric),
        unit_expectation=expected_unit,
        expected_unit_family=expected_family,
        peer_unit_family=peer_family,
        unit_compatibility_required=expected_family not in {"", "unknown"},
        per_share_basis_expectation=per_basis,
        policy_stage_expectation=(
            spec.policy_stage
            if spec.relation == "policy_state_is" or atom == "policy_stage"
            else ""
        ),
        query_terms=tuple(dict.fromkeys(term for term in terms if term)),
        allowed_doc_ids=tuple(spec.required_doc_ids),
        reason=f"missing_atom:{atom}",
        round=round_number,
        metadata={
            "claim_schema_version": spec.schema_version,
            "refinement_dimensions": refinement_dimensions,
        },
    )


def _policy_candidates(
    request: EvidenceRequest,
    *,
    structured_root: Path,
    domain: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for doc_id in request.allowed_doc_ids:
        paths = (
            structured_root / domain / doc_id / "auto" / f"{doc_id}.md",
            structured_root / domain / doc_id / f"{doc_id}.md",
        )
        path = next((item for item in paths if item.is_file()), None)
        if path is None:
            continue
        entity = document_meta(doc_id).entity_name
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for index, line in enumerate(lines, 1):
            compact = _compact(line)
            if request.metric == "share_repurchase_history":
                hit = "回购" in compact and ("连续四年" in compact or "自2019年起" in compact)
                state = "historical_series"
            else:
                state = infer_policy_stage((line,))
                hit = "现金分红" in compact and bool(state)
            if not hit:
                continue
            candidates.append({
                "entity": entity,
                "metric": request.metric,
                "period": request.period,
                "comparison_period": request.comparison_period,
                "value": None,
                "unit": "policy_state",
                "statement_scope": request.statement_scope or "consolidated",
                "attribution_scope": request.attribution_scope or "not_applicable",
                "fact_state": state,
                "doc_id": doc_id,
                "canonical_source": str(path).replace("\\", "/") + f"#line={index}",
                "local_window": line[:3000],
                "metadata": {"policy_stage": state},
            })
            if len(candidates) >= 8:
                return candidates
    return candidates


def _source_line_number(candidate: Mapping[str, Any]) -> int | None:
    return source_line_number(candidate)


def refine_financial_candidate_with_context(
    candidate: Mapping[str, Any],
    *,
    structured_root: str | Path,
    domain: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = dict(candidate)
    refined = dict(candidate)
    metadata = dict(refined.get("metadata") or {})
    pack = resolve_financial_context(
        refined, structured_root=structured_root, domain=domain
    )
    location = pack.location
    round1_context_hash = _hash_text(str(before.get("local_window") or ""))
    context = pack.context_text if location.resolver_status == "resolved" else str(
        before.get("local_window") or ""
    )
    round2_context_hash = _hash_text(context)
    dimensions: list[str] = []
    if location.resolver_status == "resolved":
        element_types = {str(row.get("type") or "") for row in pack.context_elements}
        if "parent_heading" in element_types:
            dimensions.append("parent_heading")
        if "table_header" in element_types:
            dimensions.append("table_header")
        if "target_row" in element_types:
            dimensions.append("target_row")
        if "adjacent_row" in element_types or "adjacent_line" in element_types:
            dimensions.append("adjacent_rows")
        if context:
            refined["local_window"] = context
        if not str(refined.get("unit") or "") and pack.unit_header:
            refined["unit"] = pack.unit_header
            dimensions.append("unit_header")
        if not str(refined.get("period") or "") and pack.period_header:
            refined["period"] = pack.period_header
            dimensions.append("period_header")
        if not str(refined.get("statement_scope") or "") and pack.statement_scope_header:
            refined["statement_scope"] = pack.statement_scope_header
            dimensions.append("statement_scope_header")
        if not str(refined.get("attribution_scope") or "") and pack.attribution_scope_header:
            refined["attribution_scope"] = pack.attribution_scope_header
            dimensions.append("attribution_scope_header")
        if (
            str(refined.get("metric") or "")
            in {"cash_dividend_policy", "share_repurchase_history"}
            and str(refined.get("fact_state") or "") in {"", "reported", "unknown"}
            and pack.policy_stage_context
        ):
            refined["fact_state"] = pack.policy_stage_context
            dimensions.append("policy_stage_context")
    dimensions = list(dict.fromkeys(dimensions))
    metadata.update({
        "corpus_lineage_capability": "financial_reports:corpus_lineage_corrective_retrieval_v2",
        "source_location": location.to_dict(),
        "context_pack": pack.to_dict(),
        "round2_refinement_dimensions": tuple(dimensions),
        "round1_candidate_hash": _hash_mapping(before),
        "round1_context_hash": round1_context_hash,
        "round2_context_hash": round2_context_hash,
        "fixture_context_used": False,
    })
    refined["metadata"] = metadata
    metadata["round2_candidate_hash"] = _hash_mapping(refined)
    audit = {
        "round1_candidate_hash": metadata["round1_candidate_hash"],
        "round2_candidate_hash": metadata["round2_candidate_hash"],
        "round1_context_hash": round1_context_hash,
        "round2_context_hash": round2_context_hash,
        "resolved_context_hash": location.resolved_context_hash,
        "refinement_dimensions": dimensions,
        "candidate_changed": metadata["round1_candidate_hash"] != metadata["round2_candidate_hash"],
        "context_changed": (
            location.resolver_status == "resolved"
            and round1_context_hash != round2_context_hash
        ),
        "source_path": location.source_path,
        "anchor_type": location.anchor_type,
        "page_idx": location.page_idx,
        "table_index": location.table_index,
        "row_index": location.row_index,
        "line_number": location.line_number,
        "resolver_status": location.resolver_status,
        "resolver_failure_reason": location.resolver_failure_reason,
        "context_source_paths": list(pack.context_source_paths),
        "context_elements": list(pack.context_elements),
        "column_role_map": dict(pack.column_role_map),
        "unit_header": pack.unit_header,
        "period_header": pack.period_header,
        "statement_scope_header": pack.statement_scope_header,
        "attribution_scope_header": pack.attribution_scope_header,
        "policy_stage_context": pack.policy_stage_context,
        "target_column_index": pack.target_column_index,
        "parsed_unit": str(refined.get("unit") or ""),
        "parsed_period": str(refined.get("period") or ""),
        "parsed_scope": str(refined.get("statement_scope") or ""),
        "parsed_attribution_scope": str(refined.get("attribution_scope") or ""),
        "fixture_context_used": False,
    }
    return refined, audit


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _hash_mapping(value: Mapping[str, Any]) -> str:
    import hashlib
    import json

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def search_financial_candidates(
    request: EvidenceRequest,
    *,
    structured_root: str | Path,
    domain: str,
    ledger: FinancialMetricLedger,
) -> tuple[EvidenceHit, ...]:
    if request.metric in {"cash_dividend_policy", "share_repurchase_history"}:
        raw_candidates = _policy_candidates(
            request, structured_root=Path(structured_root), domain=domain
        )
    else:
        all_candidates = [
            _financial_fact_mapping(fact)
            for fact in ledger.facts
            if fact.document_id in set(request.allowed_doc_ids)
            and fact.metric == request.metric
        ]
        best_rank: dict[tuple[str, str, str, str], int] = {}
        for candidate in all_candidates:
            key = (
                str(candidate.get("entity") or ""),
                str(candidate.get("metric") or ""),
                str(candidate.get("period") or ""),
                str(candidate.get("unit") or ""),
            )
            rank = int((candidate.get("metadata") or {}).get("precision_rank") or 0)
            best_rank[key] = max(best_rank.get(key, -1), rank)
        raw_candidates = [
            candidate for candidate in all_candidates
            if int((candidate.get("metadata") or {}).get("precision_rank") or 0)
            == best_rank[(
                str(candidate.get("entity") or ""),
                str(candidate.get("metric") or ""),
                str(candidate.get("period") or ""),
                str(candidate.get("unit") or ""),
            )]
        ]
    hits: list[EvidenceHit] = []
    for raw_candidate in raw_candidates:
        candidate = dict(raw_candidate)
        refinement_audit: Mapping[str, Any] = {}
        if request.round == 2:
            candidate, refinement_audit = refine_financial_candidate_with_context(
                candidate,
                structured_root=structured_root,
                domain=domain,
            )
        compact = _compact(candidate.get("local_window"))
        matched = tuple(term for term in request.query_terms if _compact(term) in compact)
        hits.append(EvidenceHit(
            doc_id=str(candidate.get("doc_id") or ""),
            source=str(candidate.get("canonical_source") or ""),
            local_window=str(candidate.get("local_window") or ""),
            matched_terms=matched,
            round=request.round,
            metadata={
                "candidate_fact": candidate,
                "refinement_audit": dict(refinement_audit),
            },
        ))
    return tuple(hits[:20])


def _deduplicate_source_facts(facts: Sequence[SourceFact]) -> tuple[SourceFact, ...]:
    dedup: dict[tuple[Any, ...], SourceFact] = {}
    for fact in facts:
        key = (
            fact.doc_id, _fact_entity(fact), fact.metric, fact.period_scope,
            str(fact.value), fact.unit, fact.fact_state, fact.canonical_source,
        )
        dedup.setdefault(key, fact)
    return tuple(dedup.values())


def _detect_conflicts(facts: Sequence[SourceFact]) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    groups: dict[tuple[str, str, str], list[SourceFact]] = {}
    for fact in facts:
        data = fact_mapping(fact)
        key = (data["entity"], data["metric"], data["period"])
        groups.setdefault(key, []).append(fact)
    conflicts: list[str] = []
    sources: list[Mapping[str, Any]] = []
    for key, rows in groups.items():
        mapped = [fact_mapping(row) for row in rows]
        families = {row["unit_family"] for row in mapped if row["unit_family"] != "unknown"}
        statements = {row["statement_scope"] for row in mapped if row["statement_scope"] != "unknown"}
        attributions = {row["attribution_scope"] for row in mapped if row["attribution_scope"] != "unknown"}
        bases = {row["per_share_basis"] for row in mapped if row["per_share_basis"] != "not_applicable"}
        states = {row["fact_state"] for row in mapped}
        contracts: dict[tuple[str, str, str, str], set[str]] = {}
        for row in mapped:
            contract = (
                row["unit_family"], row["statement_scope"],
                row["attribution_scope"], row["per_share_basis"],
            )
            if row["value"] not in (None, ""):
                contracts.setdefault(contract, set()).add(str(row["value"]))
        typed: list[str] = []
        if len(families) > 1:
            typed.append("unit_family_conflict")
        if len(statements) > 1:
            typed.append("statement_scope_conflict")
        if len(attributions) > 1:
            typed.append("attribution_scope_conflict")
        if len(bases) > 1:
            typed.append("per_share_basis_conflict")
        if "proposal" in states and "executed" in states:
            typed.append("policy_stage_conflict")
        if any(len(values) > 1 for values in contracts.values()):
            typed.append("same_contract_value_conflict")
        for conflict_type in typed:
            conflicts.append(conflict_type + ":" + "|".join(key))
            sources.append({
                "conflict_type": conflict_type,
                "claim_binding": {"entity": key[0], "metric": key[1], "period": key[2]},
                "facts": [row.to_dict() for row in rows],
                "resolution_status": "unresolved",
            })
    return tuple(sorted(set(conflicts))), tuple(sources)



class FinancialEvidenceCompletionAdapter:
    def __init__(
        self,
        *,
        question: Question,
        option_label: str,
        claim_spec: FinancialClaimSpec,
        structured_root: str | Path,
    ) -> None:
        self.question = question
        self.option_label = option_label
        self.claim_spec = claim_spec
        self.structured_root = str(Path(structured_root))
        self.ledger = FinancialMetricLedger.from_documents(
            self.structured_root, question.domain, question.doc_ids
        )

    def classify_gaps(
        self,
        initial_sufficiency: FinancialEvidenceSufficiency,
    ):
        return classify_financial_gaps(
            self.claim_spec,
            initial_sufficiency.missing_atoms,
            initial_sufficiency.conflicting_atoms,
        )

    def complete(
        self,
        *,
        initial_evidence: DerivedOptionEvidence,
        initial_sufficiency: FinancialEvidenceSufficiency,
        max_rounds: int = MAX_ROUNDS,
    ) -> CompletionResult:
        gaps = self.classify_gaps(initial_sufficiency)
        blocking_gaps = [gap for gap in gaps if not gap.retrievable]
        atoms = () if blocking_gaps else retrievable_atoms(gaps)
        requests: list[Mapping[str, Any]] = []
        raw_hits: list[Mapping[str, Any]] = []
        graded_hits: list[Mapping[str, Any]] = []
        typed_facts: list[TypedEvidenceFact] = []
        rejected: list[Mapping[str, Any]] = []
        rejected_policy_conflicts: list[Mapping[str, Any]] = []
        visited: list[str] = []
        refinements: list[Mapping[str, Any]] = []
        round1_grades: dict[tuple[str, ...], str] = {}
        rounds_run = 0
        pending = list(atoms)
        for round_number in range(1, min(max(int(max_rounds), 0), MAX_ROUNDS) + 1):
            if not pending:
                break
            rounds_run = round_number
            next_pending: list[str] = []
            for atom in pending:
                request = build_financial_request(
                    self.claim_spec, atom, round_number=round_number
                )
                requests.append(request.to_dict())
                visited.extend(request.allowed_doc_ids)
                hits = search_financial_candidates(
                    request,
                    structured_root=self.structured_root,
                    domain=self.question.domain,
                    ledger=self.ledger,
                )
                atom_correct = False
                atom_ambiguous = False
                for hit in hits:
                    hit_payload = hit.to_dict()
                    raw_hits.append(hit_payload)
                    hit_metadata = dict(hit.metadata or {})
                    candidate = hit_metadata.get("candidate_fact")
                    grade = grade_evidence_hit(request, hit_payload, candidate)
                    graded_hits.append({"atom": atom, **grade.to_dict()})
                    candidate_map = dict(candidate or {})
                    # The round-2 parser may legitimately fill entity, period,
                    # scope, or unit.  Source lineage is the stable identity
                    # across rounds; using mutable parsed fields would turn a
                    # real correction into an unrelated candidate.
                    candidate_key = (
                        atom,
                        str(hit.doc_id),
                        str(hit.source),
                    )
                    if round_number == 1:
                        round1_grades[candidate_key] = grade.grade.value
                    else:
                        audit = dict(hit_metadata.get("refinement_audit") or {})
                        before_grade = round1_grades.get(candidate_key, "not_seen")
                        transition = f"{before_grade}_to_{grade.grade.value}"
                        audit.update({
                            "atom": atom,
                            "candidate_key": list(candidate_key),
                            "round1_grade": before_grade,
                            "round2_grade": grade.grade.value,
                            "transition": transition,
                            "valid_corrective_progress": bool(
                                before_grade == EvidenceGrade.AMBIGUOUS.value
                                and grade.grade == EvidenceGrade.CORRECT
                                and audit.get("resolver_status") == "resolved"
                                and audit.get("context_changed") is True
                                and bool(audit.get("context_elements"))
                                and bool(audit.get("source_path"))
                                and Path(str(audit.get("source_path"))).is_file()
                                and audit.get("fixture_context_used") is False
                            ),
                        })
                        refinements.append(audit)
                    if grade.grade == EvidenceGrade.CORRECT and isinstance(candidate, Mapping):
                        typed_facts.append(_typed_from_candidate(candidate, grade.grade))
                        atom_correct = True
                    else:
                        rejection = {
                            "atom": atom,
                            "candidate_fact": candidate_map,
                            "grade": grade.grade.value,
                            "reasons": list(grade.reasons),
                            "source": hit.source,
                        }
                        rejected.append(rejection)
                        if (
                            "conflict:policy_stage_match" in grade.reasons
                            and candidate_map
                        ):
                            rejected_policy_conflicts.append(rejection)
                        atom_ambiguous = atom_ambiguous or grade.grade == EvidenceGrade.AMBIGUOUS
                # Round 2 is corrective, not repetitive: it runs only when the
                # first round has ambiguous evidence and zero correct evidence.
                if not atom_correct and atom_ambiguous and round_number < MAX_ROUNDS:
                    next_pending.append(atom)
            pending = next_pending

        raw_typed = tuple(typed_facts)
        unique_typed = tuple(unique_facts(raw_typed))
        initial_facts = tuple(initial_evidence.source_facts)
        candidate_source_facts = tuple(_source_from_typed(fact) for fact in unique_typed)
        raw_accepted_count = len(raw_typed)
        unique_accepted_source_facts = tuple(unique_facts(candidate_source_facts))
        merged = _deduplicate_source_facts((*initial_facts, *unique_accepted_source_facts))

        binding_summary = assess_claim_fact_bindings(self.claim_spec, merged)
        binding_rows = tuple(binding_summary.get("bindings") or [])
        safe_merged = tuple(
            fact for fact, binding in zip(merged, binding_rows)
            if binding.get("safe_for_formula") is True
        )
        conflicts, conflict_sources = _detect_conflicts(merged)
        rejected_policy_conflict_rows: list[Mapping[str, Any]] = []
        rejected_policy_conflict_names: list[str] = []
        seen_policy_conflicts: set[tuple[str, str, str, str]] = set()
        for rejection in rejected_policy_conflicts:
            candidate = dict(rejection.get("candidate_fact") or {})
            key = (
                str(candidate.get("entity") or candidate.get("entity_name") or ""),
                str(candidate.get("metric") or ""),
                str(candidate.get("period") or ""),
                str(candidate.get("fact_state") or ""),
            )
            if key in seen_policy_conflicts:
                continue
            seen_policy_conflicts.add(key)
            rejected_policy_conflict_names.append(
                "policy_stage_conflict:"
                + "|".join(key[:3])
                + f"|expected={self.claim_spec.policy_stage}|actual={key[3]}"
            )
            rejected_policy_conflict_rows.append({
                "conflict_type": "policy_stage_conflict",
                "claim_binding": {
                    "entity": key[0],
                    "metric": key[1],
                    "period": key[2],
                    "expected_stage": self.claim_spec.policy_stage,
                    "actual_stage": key[3],
                },
                "fact": candidate,
                "source": rejection.get("source"),
                "resolution_status": "unresolved",
            })
        conflict_sources = tuple((*conflict_sources, *rejected_policy_conflict_rows))
        conflicts = tuple(sorted(set((
            *conflicts,
            *rejected_policy_conflict_names,
            *tuple(str(value) for value in binding_summary.get("cross_operand_conflicts") or []),
            *tuple(
                f"claim_fact_binding:{failure}"
                for binding in binding_rows
                if binding.get("binding_status") == "conflict"
                for failure in binding.get("binding_failures") or []
            ),
        ))))

        final_evidence = (
            evaluate_financial_claim_from_source_facts(
                self.question, self.option_label, self.claim_spec, safe_merged
            )
            if safe_merged
            else initial_evidence
        )
        if final_evidence is None:
            final_evidence = initial_evidence
        final_diagnostics = {
            **dict(final_evidence.diagnostics or {}),
            "claim_fact_binding": binding_summary,
            "initial_and_completion_facts_share_binding_gate": True,
        }
        final_evidence = DerivedOptionEvidence(**{
            **final_evidence.__dict__,
            "diagnostics": final_diagnostics,
        })
        if conflicts:
            final_evidence = DerivedOptionEvidence(**{
                **final_evidence.__dict__,
                "conflicts": tuple(sorted(set((*final_evidence.conflicts, *conflicts)))),
                "trusted_for_option_gate": False,
            })
        final_sufficiency = assess_financial_evidence_sufficiency(
            self.claim_spec,
            final_evidence,
            declared_doc_ids=self.question.doc_ids,
            option_contract_valid=True,
        )
        semantic_gaps = [gap for gap in gaps if not gap.retrievable]
        if semantic_gaps:
            stopped = "semantic_or_nonretrievable_gap"
        elif conflicts:
            stopped = "claim_fact_binding_or_fact_conflict"
        elif final_sufficiency.safe_to_override:
            stopped = "completed"
        elif rounds_run >= min(max(int(max_rounds), 0), MAX_ROUNDS):
            stopped = "max_rounds_reached"
        else:
            stopped = "no_acceptable_typed_fact"
        resolution = (
            "resolved" if final_sufficiency.safe_to_override
            else "conflicted" if conflicts
            else "unresolved"
        )
        binding_counts = {
            "correct": int(binding_summary.get("correct_binding_count") or 0),
            "ambiguous": int(binding_summary.get("ambiguous_binding_count") or 0),
            "conflict": int(binding_summary.get("conflict_binding_count") or 0),
        }
        return CompletionResult(
            schema_version="financial_corpus_lineage_corrective_retrieval_v2",
            initial_sufficiency=initial_sufficiency.to_dict(),
            classified_gaps=tuple(gap.to_dict() for gap in gaps),
            requests=tuple(requests),
            raw_hits=tuple(raw_hits),
            graded_hits=tuple(graded_hits),
            typed_facts=tuple(fact.to_dict() for fact in raw_typed),
            accepted_facts=tuple(fact.to_dict() for fact in unique_typed),
            rejected_facts=tuple(rejected),
            claim_fact_bindings=binding_rows,
            binding_counts=binding_counts,
            round_refinements=tuple(refinements),
            raw_typed_fact_count=len(raw_typed),
            unique_typed_fact_count=len(unique_typed),
            raw_accepted_fact_count=raw_accepted_count,
            unique_accepted_fact_count=len(unique_accepted_source_facts),
            duplicate_fact_count=max(raw_accepted_count - len(unique_accepted_source_facts), 0),
            merged_source_facts=tuple(fact.to_dict() for fact in merged),
            conflicting_atoms=conflicts,
            conflict_sources=conflict_sources,
            resolution_status=resolution,
            post_completion_evidence=final_evidence.to_dict(),
            post_completion_sufficiency=final_sufficiency.to_dict(),
            post_completion_status=final_evidence.status,
            post_completion_safe_to_override=final_sufficiency.safe_to_override,
            rounds_run=rounds_run,
            stopped_reason=stopped,
            provider_calls=0,
            declared_doc_boundary_pass=set(visited) <= set(self.question.doc_ids),
            whole_corpus_scan=False,
            visited_doc_ids=tuple(dict.fromkeys(visited)),
        )
