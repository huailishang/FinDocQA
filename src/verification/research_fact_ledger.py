"""Generic declared-document fact ledger and option evidence assembly.

This production module contains schemas and reusable matching/scope mechanics only.
Dataset-specific facts and proposition templates must be supplied by an explicit
registry outside src/.  A fact can support an option only when its source
document belongs to the question's declared document set.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from verification.scope_absence import (
    ScopeAbsenceProof,
    TrustedDocumentSource,
    build_scope_absence_proof,
    validate_scope_absence_proof,
)

RESEARCH_SOURCE_ROOT_IDENTITY = "afac_data_root.v1"
VERDICTS = {"supported", "contradicted", "scope_absent", "unresolved"}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _tuple_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item) for item in value)
    return ()


@dataclass(frozen=True)
class ResearchFactSeed:
    fact_id: str
    document_id: str
    source_relpath: str
    source_anchor: str
    entity: str
    entity_role: str
    metric: str
    metric_scope: str
    period: str
    comparison_period: str
    raw_value: Any
    normalized_value: Any
    raw_unit: str
    normalized_unit: str
    polarity: str
    fact_state: str
    forecast_or_actual: str
    subject_text: str
    predicate_text: str
    object_text: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ResearchFactSeed":
        return cls(**{field: row.get(field) for field in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ResearchFact:
    fact_id: str
    document_id: str
    source_path: str
    source_sha256: str
    page_or_line: int
    entity: str
    entity_role: str
    metric: str
    metric_scope: str
    period: str
    comparison_period: str
    raw_value: Any
    normalized_value: Any
    raw_unit: str
    normalized_unit: str
    polarity: str
    fact_state: str
    forecast_or_actual: str
    subject_text: str
    predicate_text: str
    object_text: str
    local_window: str
    source_anchor: str
    question_scope_binding: str
    rejection_reasons: tuple[str, ...]

    @property
    def fact_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.entity,
            self.metric,
            self.period,
            str(self.normalized_value),
            self.normalized_unit,
            self.polarity,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fact_key"] = list(self.fact_key)
        return payload


@dataclass(frozen=True)
class ClaimTemplate:
    template_id: str
    required_terms: tuple[str, ...]
    fact_ids: tuple[str, ...]
    verdict_when_in_scope: str
    entity: str
    metric: str
    period: str
    raw_value: Any
    unit: str
    polarity: str
    forecast_or_actual: str
    reason: str
    absence_terms: tuple[str, ...] = ()
    query_alias_groups: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ClaimTemplate":
        return cls(
            template_id=str(row.get("template_id") or ""),
            required_terms=_tuple_strings(row.get("required_terms")),
            fact_ids=_tuple_strings(row.get("fact_ids")),
            verdict_when_in_scope=str(row.get("verdict_when_in_scope") or ""),
            entity=str(row.get("entity") or ""),
            metric=str(row.get("metric") or ""),
            period=str(row.get("period") or ""),
            raw_value=row.get("raw_value"),
            unit=str(row.get("unit") or ""),
            polarity=str(row.get("polarity") or ""),
            forecast_or_actual=str(row.get("forecast_or_actual") or ""),
            reason=str(row.get("reason") or ""),
            absence_terms=_tuple_strings(row.get("absence_terms")),
            query_alias_groups=tuple(
                _tuple_strings(group)
                for group in (row.get("query_alias_groups") or [])
                if _tuple_strings(group)
            ),
        )

    def matches(self, text: str) -> bool:
        compact = _compact(text)
        return bool(self.required_terms) and all(_compact(term) in compact for term in self.required_terms)


@dataclass(frozen=True)
class TruthStatementTemplate:
    template_id: str
    required_terms: tuple[str, ...]
    fact_ids: tuple[str, ...]
    statement_truth: bool
    reason: str
    query_alias_groups: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "TruthStatementTemplate":
        return cls(
            template_id=str(row.get("template_id") or ""),
            required_terms=_tuple_strings(row.get("required_terms")),
            fact_ids=_tuple_strings(row.get("fact_ids")),
            statement_truth=row.get("statement_truth") is True,
            reason=str(row.get("reason") or ""),
            query_alias_groups=tuple(
                _tuple_strings(group)
                for group in (row.get("query_alias_groups") or [])
                if _tuple_strings(group)
            ),
        )

    def matches(self, text: str) -> bool:
        compact = _compact(text)
        return bool(self.required_terms) and all(_compact(term) in compact for term in self.required_terms)


@dataclass(frozen=True)
class OracleRegistry:
    metadata: dict[str, Any]
    fact_seeds: tuple[ResearchFactSeed, ...]
    claim_templates: tuple[ClaimTemplate, ...]
    truth_templates: tuple[TruthStatementTemplate, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OracleRegistry":
        return cls(
            metadata=dict(payload.get("metadata") or {}),
            fact_seeds=tuple(
                ResearchFactSeed.from_mapping(row)
                for row in payload.get("fact_seeds") or []
                if isinstance(row, Mapping)
            ),
            claim_templates=tuple(
                ClaimTemplate.from_mapping(row)
                for row in payload.get("claim_templates") or []
                if isinstance(row, Mapping)
            ),
            truth_templates=tuple(
                TruthStatementTemplate.from_mapping(row)
                for row in payload.get("truth_templates") or []
                if isinstance(row, Mapping)
            ),
        )


def load_oracle_registry(path: Path) -> OracleRegistry:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("oracle registry must be a JSON object")
    registry = OracleRegistry.from_mapping(payload)
    if registry.metadata.get("CURATED_EVALUATOR_ORACLE") != "YES":
        raise ValueError("registry must be explicitly marked CURATED_EVALUATOR_ORACLE=YES")
    if registry.metadata.get("PRODUCTION_AUTO_EXTRACTED") != "NO":
        raise ValueError("curated registry must be marked PRODUCTION_AUTO_EXTRACTED=NO")
    if registry.metadata.get("FIXED_DATASET_REGRESSION_ONLY") != "YES":
        raise ValueError("curated registry must be fixed-dataset regression only")
    return registry


@dataclass(frozen=True)
class DeclaredDocumentScope:
    required_doc_ids: tuple[str, ...]
    scanned_doc_ids: tuple[str, ...]
    matched_doc_ids: tuple[str, ...]
    out_of_scope_match_doc_ids: tuple[str, ...]
    missing_required_doc_ids: tuple[str, ...]
    scan_complete: bool
    scope_verdict: str
    reasons: tuple[str, ...]
    scope_absence_proof: dict[str, Any] | None
    scope_absence_proof_valid: bool
    scope_absence_proof_errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchFactLedger:
    """Materialize an explicit registry and audit claims within declared docs."""

    def __init__(
        self,
        data_root: Path,
        *,
        registry: OracleRegistry,
        scope_scan_run_id: str,
    ):
        self.data_root = Path(data_root)
        self.registry = registry
        self.scope_scan_run_id = str(scope_scan_run_id)
        if not self.scope_scan_run_id:
            raise ValueError("scope_scan_run_id is required")
        self._documents: dict[str, tuple[Path, str, str]] = {}
        self._facts: dict[str, ResearchFact] = {}
        for seed in registry.fact_seeds:
            if seed.fact_id in self._facts:
                raise ValueError(f"duplicate fact_id: {seed.fact_id}")
            self._facts[seed.fact_id] = self._materialize(seed)

    def _document_path(self, doc_id: str) -> Path:
        if doc_id in self._documents:
            return self._documents[doc_id][0]
        return self.data_root / f"processed_mineru/research/{doc_id}/auto/{doc_id}.md"

    def _load_document(self, doc_id: str) -> tuple[Path, str, str]:
        if doc_id in self._documents:
            return self._documents[doc_id]
        path = self._document_path(doc_id)
        data = path.read_bytes()
        text = data.decode("utf-8-sig")
        item = (path, text, sha256(data).hexdigest())
        self._documents[doc_id] = item
        return item

    def _materialize(self, seed: ResearchFactSeed) -> ResearchFact:
        path = self.data_root / seed.source_relpath
        data = path.read_bytes()
        text = data.decode("utf-8-sig")
        lines = text.splitlines()
        hits = [index for index, line in enumerate(lines) if seed.source_anchor in line]
        if not hits:
            raise ValueError(f"research fact anchor missing: {seed.fact_id}: {path}: {seed.source_anchor}")
        index = hits[0]
        local_window = "\n".join(lines[max(0, index - 1):min(len(lines), index + 2)]).strip()
        self._documents[seed.document_id] = (path, text, sha256(data).hexdigest())
        return ResearchFact(
            fact_id=seed.fact_id,
            document_id=seed.document_id,
            source_path=path.as_posix(),
            source_sha256=sha256(data).hexdigest(),
            page_or_line=index + 1,
            entity=seed.entity,
            entity_role=seed.entity_role,
            metric=seed.metric,
            metric_scope=seed.metric_scope,
            period=seed.period,
            comparison_period=seed.comparison_period,
            raw_value=seed.raw_value,
            normalized_value=seed.normalized_value,
            raw_unit=seed.raw_unit,
            normalized_unit=seed.normalized_unit,
            polarity=seed.polarity,
            fact_state=seed.fact_state,
            forecast_or_actual=seed.forecast_or_actual,
            subject_text=seed.subject_text,
            predicate_text=seed.predicate_text,
            object_text=seed.object_text,
            local_window=local_window,
            source_anchor=seed.source_anchor,
            question_scope_binding="unbound",
            rejection_reasons=(),
        )

    @property
    def facts(self) -> tuple[ResearchFact, ...]:
        return tuple(self._facts[key] for key in sorted(self._facts))

    def fact(self, fact_id: str) -> ResearchFact:
        return self._facts[fact_id]

    def document_text(self, doc_id: str) -> str:
        return self._load_document(doc_id)[1]

    def document_path(self, doc_id: str) -> Path:
        return self._document_path(doc_id)

    def trusted_document_sources(
        self,
        doc_ids: Sequence[str],
    ) -> dict[str, TrustedDocumentSource]:
        return {
            str(doc_id): TrustedDocumentSource(
                canonical_doc_id=str(doc_id),
                source_root_identity=RESEARCH_SOURCE_ROOT_IDENTITY,
                source_root=str(self.data_root),
                source_relpath=self._document_path(str(doc_id)).relative_to(self.data_root).as_posix(),
            )
            for doc_id in doc_ids
        }

    def _scope(
        self,
        *,
        declared_doc_ids: Sequence[str],
        fact_ids: Sequence[str],
        query_terms: Sequence[str],
        query_alias_groups: Sequence[Sequence[str]] = (),
        explicit_absence_claim: bool = False,
    ) -> DeclaredDocumentScope:
        required = tuple(_dedupe(str(doc_id) for doc_id in declared_doc_ids))
        scanned: list[str] = []
        missing: list[str] = []
        for doc_id in required:
            try:
                self._load_document(doc_id)
                scanned.append(doc_id)
            except FileNotFoundError:
                missing.append(doc_id)

        fact_doc_ids = _dedupe(self.fact(fact_id).document_id for fact_id in fact_ids)
        matched = [doc_id for doc_id in fact_doc_ids if doc_id in required]
        out_of_scope = [doc_id for doc_id in fact_doc_ids if doc_id not in required]
        scan_complete = bool(required) and set(scanned) == set(required) and not missing

        trusted_sources = self.trusted_document_sources(required)
        proof: ScopeAbsenceProof | None = None
        proof_validation = validate_scope_absence_proof(
            None,
            trusted_declared_documents=trusted_sources,
        )
        should_build_proof = bool(query_terms or query_alias_groups) and scan_complete and (
            explicit_absence_claim or not matched or bool(out_of_scope)
        )
        if should_build_proof:
            proof = build_scope_absence_proof(
                trusted_declared_documents=trusted_sources,
                query_terms=query_terms,
                query_alias_groups=query_alias_groups,
                out_of_scope_match_doc_ids=out_of_scope,
                scan_timestamp_or_run_id=self.scope_scan_run_id,
            )
            proof_validation = validate_scope_absence_proof(
                proof,
                trusted_declared_documents=trusted_sources,
            )

        reasons: list[str] = []
        if missing or not required:
            verdict = "incomplete"
            reasons.append("declared_document_missing_or_empty")
        elif explicit_absence_claim:
            if proof_validation.valid:
                verdict = "scope_absent"
                reasons.append("complete_declared_document_scan_found_no_coherent_match")
            elif proof is not None and proof.coherent_match_count > 0:
                verdict = "in_scope_coherent_match"
                reasons.append("coherent_claim_found_in_declared_documents")
            else:
                verdict = "incomplete"
                reasons.append("scope_absence_proof_invalid")
        elif matched:
            verdict = "in_scope_match"
            reasons.append("fact_source_is_declared")
        elif proof_validation.valid:
            verdict = "scope_absent"
            reasons.append("complete_declared_document_scan_found_no_coherent_match")
            if out_of_scope:
                reasons.append("fact_exists_only_outside_declared_documents")
        elif proof is not None and proof.coherent_match_count > 0:
            verdict = "unresolved_in_scope_text_match"
            reasons.append("coherent_text_match_requires_fact_binding")
        else:
            verdict = "incomplete"
            reasons.append("scope_absence_proof_invalid_or_not_run")

        return DeclaredDocumentScope(
            required_doc_ids=required,
            scanned_doc_ids=tuple(scanned),
            matched_doc_ids=tuple(matched),
            out_of_scope_match_doc_ids=tuple(out_of_scope),
            missing_required_doc_ids=tuple(missing),
            scan_complete=scan_complete,
            scope_verdict=verdict,
            reasons=tuple(reasons),
            scope_absence_proof=proof.to_dict() if proof else None,
            scope_absence_proof_valid=proof_validation.valid if proof else False,
            scope_absence_proof_errors=proof_validation.errors if proof else (),
        )

    def _find_claim_template(self, option_text: str) -> ClaimTemplate | None:
        matches = [template for template in self.registry.claim_templates if template.matches(option_text)]
        if not matches:
            return None
        matches.sort(key=lambda item: sum(len(_compact(term)) for term in item.required_terms), reverse=True)
        return matches[0]

    def _find_truth_template(self, statement: str) -> TruthStatementTemplate | None:
        matches = [template for template in self.registry.truth_templates if template.matches(statement)]
        if not matches:
            return None
        matches.sort(key=lambda item: sum(len(_compact(term)) for term in item.required_terms), reverse=True)
        return matches[0]

    def audit_option(
        self,
        *,
        qid: str,
        question_text: str,
        declared_doc_ids: Sequence[str],
        answer_format: str,
        option_label: str,
        option_text: str,
    ) -> dict[str, Any]:
        if answer_format == "tf":
            statement = re.sub(r"^判断以下陈述是否正确[:：]?", "", question_text).strip()
            template = self._find_truth_template(statement)
            if template is not None:
                expected_docs = {self.fact(fid).document_id for fid in template.fact_ids}
                scope = self._scope(
                    declared_doc_ids=declared_doc_ids,
                    fact_ids=template.fact_ids,
                    query_terms=template.required_terms,
                    query_alias_groups=template.query_alias_groups,
                )
                option_means_true = _compact(option_text) in {"正确", "对"}
                if scope.scope_verdict == "in_scope_match" and expected_docs.issubset(set(scope.matched_doc_ids)):
                    verdict = "supported" if template.statement_truth == option_means_true else "contradicted"
                    authoritative = True
                    reason = template.reason
                elif scope.scope_verdict == "scope_absent" and scope.scope_absence_proof_valid:
                    verdict = "scope_absent"
                    authoritative = True
                    reason = "truth-statement facts are absent from the declared document set"
                else:
                    verdict = "unresolved"
                    authoritative = False
                    reason = "truth-statement facts are not completely bound in declared documents"
                facts = [self.fact(fid).to_dict() for fid in template.fact_ids]
                return self._option_payload(
                    qid, option_label, option_text, declared_doc_ids, scope, verdict,
                    authoritative, template.template_id, facts,
                    {
                        "entity": [fact["entity"] for fact in facts],
                        "metric": [fact["metric"] for fact in facts],
                        "period": [fact["period"] for fact in facts],
                        "raw_value": [fact["raw_value"] for fact in facts],
                        "unit": [fact["raw_unit"] for fact in facts],
                        "polarity": [fact["polarity"] for fact in facts],
                        "forecast_or_actual": [fact["forecast_or_actual"] for fact in facts],
                    },
                    reason,
                )

        template = self._find_claim_template(option_text)
        if template is None:
            scope = self._scope(
                declared_doc_ids=declared_doc_ids,
                fact_ids=(),
                query_terms=(),
            )
            return self._option_payload(
                qid, option_label, option_text, declared_doc_ids, scope, "unresolved",
                False, "unmatched_claim_shape", [],
                {
                    "entity": "", "metric": "", "period": "", "raw_value": None,
                    "unit": "", "polarity": "", "forecast_or_actual": "",
                },
                "no curated proposition template matched; fail closed",
            )

        explicit_absence = template.verdict_when_in_scope == "scope_absent"
        query_terms = template.absence_terms if explicit_absence and template.absence_terms else template.required_terms
        scope = self._scope(
            declared_doc_ids=declared_doc_ids,
            fact_ids=template.fact_ids,
            query_terms=query_terms,
            query_alias_groups=template.query_alias_groups,
            explicit_absence_claim=explicit_absence,
        )
        facts = [self.fact(fact_id).to_dict() for fact_id in template.fact_ids]

        if explicit_absence:
            if scope.scope_verdict == "scope_absent" and scope.scope_absence_proof_valid:
                verdict = "scope_absent"
                authoritative = True
            else:
                verdict = "unresolved"
                authoritative = False
        elif scope.scope_verdict == "in_scope_match":
            verdict = template.verdict_when_in_scope
            authoritative = verdict in {"supported", "contradicted"}
        elif scope.scope_verdict == "scope_absent" and scope.scope_absence_proof_valid:
            verdict = "scope_absent"
            authoritative = True
        else:
            verdict = "unresolved"
            authoritative = False

        return self._option_payload(
            qid, option_label, option_text, declared_doc_ids, scope, verdict,
            authoritative, template.template_id, facts,
            {
                "entity": template.entity,
                "metric": template.metric,
                "period": template.period,
                "raw_value": template.raw_value,
                "unit": template.unit,
                "polarity": template.polarity,
                "forecast_or_actual": template.forecast_or_actual,
            },
            template.reason,
        )

    @staticmethod
    def _option_payload(
        qid: str,
        option_label: str,
        option_text: str,
        declared_doc_ids: Sequence[str],
        scope: DeclaredDocumentScope,
        verdict: str,
        authoritative: bool,
        template_id: str,
        facts: Sequence[Mapping[str, Any]],
        atoms: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if verdict not in VERDICTS:
            raise ValueError(f"invalid research option verdict: {verdict}")
        declared = set(declared_doc_ids)
        in_scope_facts = [fact for fact in facts if fact.get("document_id") in declared]
        if verdict == "scope_absent":
            question_scope_binding = "scope_absent"
            factual_statement_true = None
        elif verdict in {"supported", "contradicted"}:
            question_scope_binding = "in_scope"
            factual_statement_true = verdict == "supported"
        else:
            question_scope_binding = "unresolved"
            factual_statement_true = None
        return {
            "qid": qid,
            "option": str(option_label).upper(),
            "option_text": option_text,
            "declared_doc_ids": list(declared_doc_ids),
            "scanned_doc_ids": list(scope.scanned_doc_ids),
            "matched_doc_ids": list(scope.matched_doc_ids),
            "out_of_scope_matches": list(scope.out_of_scope_match_doc_ids),
            "missing_required_doc_ids": list(scope.missing_required_doc_ids),
            "scan_complete": scope.scan_complete,
            "scope_verdict": scope.scope_verdict,
            "scope_absence_proof": scope.scope_absence_proof,
            "scope_absence_proof_valid": scope.scope_absence_proof_valid,
            "scope_absence_proof_errors": list(scope.scope_absence_proof_errors),
            "template_id": template_id,
            "entity": atoms.get("entity"),
            "metric": atoms.get("metric"),
            "period": atoms.get("period"),
            "raw_value": atoms.get("raw_value"),
            "unit": atoms.get("unit"),
            "polarity": atoms.get("polarity"),
            "forecast_or_actual": atoms.get("forecast_or_actual"),
            "source_path": [fact.get("source_path") for fact in in_scope_facts],
            "source_sha256": [fact.get("source_sha256") for fact in in_scope_facts],
            "page_or_line": [fact.get("page_or_line") for fact in in_scope_facts],
            "text_anchor": [fact.get("source_anchor") for fact in in_scope_facts],
            "verdict": verdict,
            "authoritative": authoritative,
            "claim_route": "scope_only" if verdict == "scope_absent" else ("contradiction" if verdict == "contradicted" else "direct_evidence"),
            "question_scope_binding": question_scope_binding,
            "factual_statement_true": factual_statement_true,
            "reason": reason,
            "scope_reasons": list(scope.reasons),
            "facts": list(facts),
        }

    def audit_question(self, question: Mapping[str, Any]) -> dict[str, Any]:
        qid = str(question["qid"])
        options = question.get("options") or {}
        rows = [
            self.audit_option(
                qid=qid,
                question_text=str(question.get("question") or ""),
                declared_doc_ids=[str(value) for value in question.get("doc_ids") or []],
                answer_format=str(question.get("answer_format") or ""),
                option_label=str(label),
                option_text=str(text),
            )
            for label, text in options.items()
        ]
        supported = "".join(row["option"] for row in rows if row["verdict"] == "supported")
        fully_closed = bool(rows) and all(row["authoritative"] for row in rows)
        generic_scope_slots = sum(bool(row["scan_complete"]) for row in rows)
        curated_authoritative_slots = sum(bool(row["authoritative"]) for row in rows)
        return {
            "qid": qid,
            "declared_doc_ids": [str(value) for value in question.get("doc_ids") or []],
            "answer_format": str(question.get("answer_format") or ""),
            "defined_option_slots": len(rows),
            "generic_scope_validator_slots": generic_scope_slots,
            "generic_scope_absence_proof_pass_slots": sum(
                row["verdict"] == "scope_absent" and row["scope_absence_proof_valid"]
                for row in rows
            ),
            "curated_oracle_authoritative_slots": curated_authoritative_slots,
            "unresolved_option_slots": sum(row["verdict"] == "unresolved" for row in rows),
            "curated_oracle_fully_closed": fully_closed,
            "derived_answer": supported if fully_closed else "",
            "option_evidence": rows,
            # Historical Package T replay aliases. They describe curated fixture
            # closure only and must not be reported as production auto-extraction.
            "scope_complete_slots": generic_scope_slots,
            "authoritative_option_slots": curated_authoritative_slots,
            "fully_trusted": fully_closed,
        }
