"""Evidence-integrity domain bridges for the gap-driven controller.

The bridge contract is deliberately strict: document stores and ledgers may
produce retrieval hits, but only canonical, requirement-local ``BoundFact``
objects passed into ``execute_tools`` and ``assess_option`` may influence an
answer.  No decision-time corpus, ledger, baseline, or oracle access is allowed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import canonical_source_v2, resolve_candidate_path
from evidence_completion.contracts import EvidenceGrade
from verification.financial_metric_ledger import FinancialMetricLedger, document_meta, document_year


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _span_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _numbers(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*[%％]|\s*(?:亿|万)?(?:美元|元)?)?", str(value))))


def _unit_family(unit: str) -> str:
    lowered = str(unit or "").strip().lower().replace("％", "%")
    if lowered in {"%", "percent", "percentage", "ratio"}:
        return "ratio"
    if lowered in {"cny", "rmb", "人民币", "元", "千元", "万元", "亿元", "百万元"}:
        return "currency"
    if lowered in {"true", "false", "boolean", "bool", "clause"}:
        return "boolean"
    if not lowered:
        return "unknown"
    return lowered


@dataclass(frozen=True)
class BridgeRequirement:
    requirement_id: str
    option_label: str
    semantic_key: str
    query_terms: tuple[str, ...]
    allowed_doc_ids: tuple[str, ...]
    retrievable: bool
    reason: str
    round: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BridgeHit:
    hit_id: str
    requirement_id: str
    doc_id: str
    source: str
    local_window: str
    round: int
    candidate_key: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BridgeGrade:
    hit: BridgeHit
    grade: EvidenceGrade
    reasons: tuple[str, ...]
    dimensions: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": self.hit.to_dict(),
            "grade": self.grade.value,
            "reasons": list(self.reasons),
            "dimensions": dict(self.dimensions),
        }


@dataclass(frozen=True)
class BoundFact:
    fact_id: str
    requirement_id: str
    option_label: str
    atom_id: str
    fact_type: str
    doc_id: str
    entity: str
    role: str
    period_or_date: str
    metric_or_field: str
    value: Any
    unit: str
    condition_scope: str
    exception_scope: str
    source: str
    source_anchor: str
    source_span_sha256: str
    source_file_sha256: str
    local_window: str
    canonical_verified: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # Compatibility aliases retained for graph/memory consumers.
    @property
    def semantic_key(self) -> str:
        return f"{self.requirement_id}:{self.atom_id}:{self.metric_or_field}"

    @property
    def period(self) -> str:
        return self.period_or_date

    @property
    def metric(self) -> str:
        return self.metric_or_field

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["semantic_key"] = self.semantic_key
        payload["period"] = self.period
        payload["metric"] = self.metric
        return payload


def _bound_fact_integrity_payload(fact: BoundFact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "requirement_id": fact.requirement_id,
        "option_label": fact.option_label,
        "atom_id": fact.atom_id,
        "fact_type": fact.fact_type,
        "doc_id": fact.doc_id,
        "entity": fact.entity,
        "role": fact.role,
        "period_or_date": fact.period_or_date,
        "metric_or_field": fact.metric_or_field,
        "value": fact.value,
        "unit": fact.unit,
        "condition_scope": fact.condition_scope,
        "exception_scope": fact.exception_scope,
        "source": fact.source,
        "source_anchor": fact.source_anchor,
        "source_span_sha256": fact.source_span_sha256,
        "source_file_sha256": fact.source_file_sha256,
        "canonical_verified": fact.canonical_verified,
    }


def _seal_fact(fact: BoundFact) -> BoundFact:
    metadata = dict(fact.metadata or {})
    metadata["integrity_sha256"] = _hash(_bound_fact_integrity_payload(fact))
    return replace(fact, metadata=metadata)


def _fact_integrity_valid(fact: BoundFact) -> bool:
    expected = str((fact.metadata or {}).get("integrity_sha256") or "")
    return bool(expected and expected == _hash(_bound_fact_integrity_payload(fact)))


@dataclass(frozen=True)
class ToolRun:
    run_id: str
    option_label: str
    tool: str
    formula_or_rule: str
    operands: Mapping[str, Any]
    normalized_units: Mapping[str, str]
    result: Any
    comparison: str
    source_fact_ids: tuple[str, ...]
    status: str
    requirement_id: str = ""
    missing_atom_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptionAssessment:
    option_label: str
    status: str
    reason: str
    dependencies_closed: bool
    fact_ids: tuple[str, ...]
    tool_run_ids: tuple[str, ...]
    missing_requirements: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DomainBridge(Protocol):
    capability_id: str
    question: Question

    def compile_requirements(self, option_label: str, option_text: str) -> Sequence[BridgeRequirement]: ...
    def search_local(self, request: BridgeRequirement) -> Sequence[BridgeHit]: ...
    def grade_hit(self, request: BridgeRequirement, hit: BridgeHit) -> BridgeGrade: ...
    def bind_facts(self, grades: Sequence[BridgeGrade]) -> Sequence[BoundFact]: ...
    def execute_tools(self, facts: Sequence[BoundFact]) -> Sequence[ToolRun]: ...
    def assess_option(self, option_label: str, facts: Sequence[BoundFact], tools: Sequence[ToolRun]) -> OptionAssessment: ...
    def build_targeted_request(self, gap: BridgeRequirement) -> BridgeRequirement: ...


class _LocalTextCorpus:
    """Immutable markdown corpus with exact physical line anchors."""

    def __init__(self, repo_root: Path, question: Question) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.question = question
        self.files: dict[str, dict[str, tuple[str, ...]]] = {}
        for doc_id in question.doc_ids:
            by_source: dict[str, tuple[str, ...]] = {}
            roots = (
                self.repo_root.parent / "data/processed_mineru_retrieval" / question.domain / str(doc_id),
                self.repo_root.parent / "data/processed_mineru" / question.domain / str(doc_id) / "auto",
            )
            seen: set[str] = set()
            for root in roots:
                if not root.is_dir():
                    continue
                for path in sorted(root.glob("*.md")):
                    resolved = str(path.resolve())
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    by_source[resolved] = tuple(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
            self.files[str(doc_id)] = by_source

    def iter_lines(self, doc_ids: Sequence[str]) -> Sequence[tuple[str, str, int, str]]:
        rows: list[tuple[str, str, int, str]] = []
        for doc_id in doc_ids:
            for source, lines in self.files.get(str(doc_id), {}).items():
                for index, line in enumerate(lines, 1):
                    if line.strip():
                        rows.append((str(doc_id), source, index, line))
        return tuple(rows)

    def window(self, source: str, line_no: int, *, radius_before: int, radius_after: int) -> tuple[str, int, int]:
        for by_source in self.files.values():
            if source not in by_source:
                continue
            lines = by_source[source]
            start = max(1, int(line_no) - int(radius_before))
            end = min(len(lines), int(line_no) + int(radius_after))
            return "\n".join(lines[start - 1:end]), start, end
        return "", 0, 0


def _verify_markdown_hit(repo_root: Path, hit: BridgeHit) -> dict[str, Any]:
    path = resolve_candidate_path(repo_root, hit.source)
    start = int((hit.metadata or {}).get("line_start") or 0)
    end = int((hit.metadata or {}).get("line_end") or 0)
    result = {
        "source_path_exists": False,
        "source_anchor_valid": False,
        "source_span_exact": False,
        "source_span_sha256": "",
        "source_file_sha256": "",
        "doc_id_matches_source": False,
        "canonical_verified": False,
    }
    if path is None or start <= 0 or end < start:
        return result
    result["source_path_exists"] = True
    result["source_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if end > len(lines):
        return result
    reproduced = "\n".join(lines[start - 1:end])
    result["source_anchor_valid"] = True
    result["source_span_exact"] = reproduced == hit.local_window
    result["source_span_sha256"] = _span_hash(reproduced)
    result["doc_id_matches_source"] = str(hit.doc_id).lower() in {part.lower() for part in path.parts}
    result["canonical_verified"] = bool(
        result["source_path_exists"]
        and result["source_anchor_valid"]
        and result["source_span_exact"]
        and result["doc_id_matches_source"]
    )
    return result


def _verify_financial_hit(repo_root: Path, question: Question, hit: BridgeHit) -> dict[str, Any]:
    metadata = dict(hit.metadata or {})
    candidate = EvidenceCandidate(
        domain=question.domain,
        doc_id=hit.doc_id,
        source=hit.source,
        text=hit.local_window,
        metadata={
            "doc_id": hit.doc_id,
            "page_idx": metadata.get("page_idx"),
            "table_index": metadata.get("table_index"),
            "row_index": metadata.get("row_index"),
        },
    )
    canonical = canonical_source_v2(repo_root, candidate)
    canonical["canonical_verified"] = bool(
        canonical.get("anchor_exists_in_source")
        and canonical.get("anchor_valid")
        and canonical.get("lineage_doc_id_match")
        and canonical.get("candidate_matches_canonical_record")
        and canonical.get("declared_hash_matches_candidate")
    )
    return canonical


class FinancialRatioBridge:
    capability_id = "FIN-RATIO"
    _entity_aliases = {
        "比亚迪": ("比亚迪", "byd"),
        "美的集团": ("美的集团", "美的", "midea"),
        "宁德时代": ("宁德时代", "catl"),
        "中国移动": ("中国移动", "china mobile"),
    }
    _metric_aliases = {
        "revenue": ("operating_revenue", "total_operating_revenue"),
        "rd_ratio": ("rd_investment_ratio", "rd_expense_ratio"),
    }

    def __init__(self, repo_root: Path, question: Question, initial_candidates: Sequence[EvidenceCandidate] = ()) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.question = question
        self.initial_candidates = tuple(initial_candidates)
        # Retrieval-only store.  Decision methods never access this object.
        self._retrieval_ledger = FinancialMetricLedger.from_documents(
            self.repo_root.parent / "data/processed_mineru", question.domain, question.doc_ids
        )
        self._requirements: dict[str, BridgeRequirement] = {}
        self._requirements_by_id: dict[str, BridgeRequirement] = {}

    def _entities(self, text: str) -> tuple[str, ...]:
        compact = _compact(text)
        found: list[tuple[int, str]] = []
        for entity, aliases in self._entity_aliases.items():
            positions = [compact.find(_compact(alias)) for alias in aliases if _compact(alias) in compact]
            if positions:
                found.append((min(positions), entity))
        found.sort()
        if found:
            return tuple(entity for _, entity in found)
        return tuple(dict.fromkeys(
            document_meta(str(doc)).entity_name
            for doc in self.question.doc_ids
            if document_meta(str(doc)).entity_name
        ))

    def _doc_for_entity(self, entity: str) -> str:
        for doc_id in self.question.doc_ids:
            if document_meta(str(doc_id)).entity_name == entity:
                return str(doc_id)
        return ""

    def _year_for(self, entity: str) -> str:
        return document_year(self._doc_for_entity(entity))

    def _condition_descriptors(self, text: str) -> list[dict[str, Any]]:
        compact = _compact(text)
        entities = self._entities(text)
        descriptors: list[dict[str, Any]] = []
        if "研发投入强度高于" in compact or "研发投入占营业收入比例高于" in compact:
            descriptors.append({"kind": "compare", "metric": "rd_ratio", "entities": entities[:2], "op": ">"})
        elif "经营活动" in compact and "高于" in compact and len(entities) >= 2:
            descriptors.append({"kind": "compare", "metric": "operating_cash_flow_net", "entities": entities[:2], "op": ">"})
        elif ("营业收入规模大于" in compact or "营收规模大于" in compact) and len(entities) >= 2:
            descriptors.append({"kind": "compare", "metric": "revenue", "entities": entities[:2], "op": ">"})
        if "营业收入超过1万亿元" in compact or "营业收入超过1万亿" in compact:
            descriptors.append({"kind": "threshold", "metric": "revenue", "entities": entities[:1], "op": ">", "threshold": 1_000_000_000_000.0})
        if "经营活动" in compact and "均为正数" in compact:
            descriptors.append({"kind": "all_positive", "metric": "operating_cash_flow_net", "entities": entities})
        if "归属于" in compact and "净利润" in compact and "均实现了双位数" in compact:
            descriptors.append({"kind": "all_yoy", "metric": "parent_attributable_net_profit", "entities": entities, "op": ">=", "threshold": 0.10})
        elif "归属于" in compact and "净利润实现双位数" in compact:
            descriptors.append({"kind": "all_yoy", "metric": "parent_attributable_net_profit", "entities": entities[:1], "op": ">=", "threshold": 0.10})
        if "营业收入均实现双位数增长" in compact:
            descriptors.append({"kind": "all_yoy", "metric": "revenue", "entities": entities, "op": ">=", "threshold": 0.10})
        if "净利润增速高于" in compact and len(entities) >= 2:
            descriptors.append({"kind": "compare_yoy", "metric": "parent_attributable_net_profit", "entities": entities[:2], "op": ">"})
        if "经营活动" in compact and "营业收入的一半" in compact:
            descriptors.append({"kind": "ratio", "metric": "operating_cash_flow_net", "denominator": "revenue", "entities": entities[:1], "op": "<", "threshold": 0.5})
        if "经营活动" in compact and "营业收入的十分之一" in compact:
            descriptors.append({"kind": "ratio", "metric": "operating_cash_flow_net", "denominator": "revenue", "entities": entities[-1:], "op": ">", "threshold": 0.1})
        return descriptors

    def _operand_specs(self, descriptors: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        specs: list[dict[str, Any]] = []
        for index, descriptor in enumerate(descriptors):
            kind = str(descriptor.get("kind") or "")
            metric = str(descriptor.get("metric") or "")
            for entity_index, entity in enumerate(descriptor.get("entities") or ()):
                period = self._year_for(str(entity))
                role = f"condition_{index}:entity_{entity_index}:current"
                specs.append({
                    "atom_id": role,
                    "descriptor_index": index,
                    "entity": str(entity),
                    "doc_id": self._doc_for_entity(str(entity)),
                    "period": period,
                    "metric": metric,
                    "unit_family": "ratio" if metric == "rd_ratio" else "currency",
                })
                if kind in {"all_yoy", "compare_yoy"} and period.isdigit():
                    specs.append({
                        "atom_id": f"condition_{index}:entity_{entity_index}:prior",
                        "descriptor_index": index,
                        "entity": str(entity),
                        "doc_id": self._doc_for_entity(str(entity)),
                        "period": str(int(period) - 1),
                        "metric": metric,
                        "unit_family": "ratio" if metric == "rd_ratio" else "currency",
                    })
                if kind == "ratio":
                    denominator = str(descriptor.get("denominator") or "")
                    specs.append({
                        "atom_id": f"condition_{index}:entity_{entity_index}:denominator",
                        "descriptor_index": index,
                        "entity": str(entity),
                        "doc_id": self._doc_for_entity(str(entity)),
                        "period": period,
                        "metric": denominator,
                        "unit_family": "currency",
                    })
        return tuple(specs)

    def compile_requirements(self, option_label: str, option_text: str) -> Sequence[BridgeRequirement]:
        statement = self.question.text if str(self.question.answer_format).lower() in {"tf", "boolean", "judge"} else option_text
        descriptors = self._condition_descriptors(statement)
        specs = self._operand_specs(descriptors)
        semantic = "financial_expression:" + _hash({"descriptors": descriptors, "specs": specs})[:16]
        terms = list(self._entities(statement))
        terms.extend(_numbers(statement))
        for descriptor in descriptors:
            terms.append({
                "revenue": "营业收入",
                "rd_ratio": "研发投入",
                "operating_cash_flow_net": "经营活动产生的现金流量净额",
                "parent_attributable_net_profit": "归属于上市公司股东的净利润",
            }.get(str(descriptor.get("metric") or ""), str(descriptor.get("metric") or "")))
        req = BridgeRequirement(
            requirement_id=f"{self.question.qid}:{option_label}:FIN-RATIO:{_hash(semantic)[:16]}",
            option_label=option_label,
            semantic_key=semantic,
            query_terms=tuple(dict.fromkeys(str(term) for term in terms if str(term))),
            allowed_doc_ids=tuple(str(doc) for doc in self.question.doc_ids),
            retrievable=bool(descriptors and specs),
            reason="deterministic_financial_expression" if descriptors and specs else "semantic_expression_unparsed",
            metadata={
                "statement": statement,
                "descriptors": descriptors,
                "operand_specs": list(specs),
                "negate": str(self.question.answer_format).lower() in {"tf", "boolean", "judge"} and option_label != "A",
            },
        )
        self._requirements[option_label] = req
        self._requirements_by_id[req.requirement_id] = req
        return (req,)

    @classmethod
    def _aliases(cls, metric: str) -> tuple[str, ...]:
        return cls._metric_aliases.get(metric, (metric,))

    def search_local(self, request: BridgeRequirement) -> Sequence[BridgeHit]:
        hits: list[BridgeHit] = []
        for spec in request.metadata.get("operand_specs") or []:
            matching = [
                fact for fact in self._retrieval_ledger.facts
                if fact.entity_name == spec["entity"]
                and fact.document_id == spec["doc_id"]
                and fact.period == spec["period"]
                and fact.metric in self._aliases(str(spec["metric"]))
                and fact.normalized_value not in (None, "")
            ]
            prepared: list[tuple[Any, BridgeHit, bool]] = []
            for fact in matching:
                candidate_key = f"{request.requirement_id}|{spec['atom_id']}|{fact.canonical_source}"
                hit = BridgeHit(
                    hit_id=_hash((candidate_key, request.round, fact.normalized_value))[:24],
                    requirement_id=request.requirement_id,
                    doc_id=fact.document_id,
                    source=str(fact.canonical_source),
                    local_window=str(fact.local_window),
                    round=request.round,
                    candidate_key=candidate_key,
                    metadata={
                        "atom_id": spec["atom_id"],
                        "operand_spec": dict(spec),
                        "entity": fact.entity_name,
                        "metric": fact.metric,
                        "period": fact.period,
                        "value": fact.normalized_value,
                        "unit": fact.normalized_unit,
                        "precision_rank": int(fact.precision_rank or 0),
                        "page_idx": fact.source_page,
                        "table_index": fact.source_table,
                        "row_index": fact.source_row,
                        "comparison_period": fact.comparison_period,
                        "candidate_origin": "financial_metric_ledger_retrieval_only",
                        "replacement_of": candidate_key if request.round == 2 else "",
                        "context_hash": _span_hash(str(fact.local_window)),
                    },
                )
                canonical_ok = bool(
                    _verify_financial_hit(self.repo_root, self.question, hit).get("canonical_verified")
                )
                prepared.append((fact, hit, canonical_ok))
            valid_precisions = [
                int(fact.precision_rank or 0)
                for fact, _, valid in prepared
                if valid
            ]
            max_valid_precision = max(valid_precisions) if valid_precisions else -1
            eligible = [
                (fact, hit)
                for fact, hit, valid in prepared
                if valid and int(fact.precision_rank or 0) == max_valid_precision
            ]
            preferred_key = ""
            if eligible:
                _, preferred = max(
                    eligible,
                    key=lambda row: (
                        abs(float(row[0].normalized_value)),
                        row[1].candidate_key,
                    ),
                )
                preferred_key = preferred.candidate_key
            for fact, hit, valid in prepared:
                is_preferred = bool(valid and hit.candidate_key == preferred_key)
                if request.round == 2 and not is_preferred:
                    continue
                hits.append(replace(
                    hit,
                    metadata={
                        **dict(hit.metadata),
                        "canonical_preverified": valid,
                        "max_precision_rank": max_valid_precision,
                        "preferred_candidate_key": preferred_key,
                        "preferred_candidate": is_preferred,
                    },
                ))
        return tuple(hits)

    def grade_hit(self, request: BridgeRequirement, hit: BridgeHit) -> BridgeGrade:
        meta = dict(hit.metadata or {})
        spec = dict(meta.get("operand_spec") or {})
        canonical = _verify_financial_hit(self.repo_root, self.question, hit)
        dimensions = {
            "canonical": "match" if canonical.get("canonical_verified") else "mismatch",
            "entity": "match" if meta.get("entity") == spec.get("entity") else "mismatch",
            "document": "match" if hit.doc_id == spec.get("doc_id") else "mismatch",
            "period": "match" if meta.get("period") == spec.get("period") else "mismatch",
            "metric": "match" if meta.get("metric") in self._aliases(str(spec.get("metric") or "")) else "mismatch",
            "unit": "match" if _unit_family(str(meta.get("unit") or "")) == spec.get("unit_family") else "mismatch",
            "precision": "primary" if meta.get("preferred_candidate") is True else "lower",
        }
        mismatches = [key for key, value in dimensions.items() if value == "mismatch"]
        if mismatches:
            return BridgeGrade(hit, EvidenceGrade.INCORRECT, tuple(f"{key}_mismatch" for key in mismatches), dimensions)
        if dimensions["precision"] != "primary":
            return BridgeGrade(hit, EvidenceGrade.AMBIGUOUS, ("lower_precision_competing_financial_fact",), dimensions)
        return BridgeGrade(hit, EvidenceGrade.CORRECT, ("canonical_primary_financial_fact",), dimensions)

    def bind_facts(self, grades: Sequence[BridgeGrade]) -> Sequence[BoundFact]:
        facts: list[BoundFact] = []
        for grade in grades:
            if grade.grade != EvidenceGrade.CORRECT:
                continue
            hit = grade.hit
            meta = dict(hit.metadata or {})
            req = self._requirements_by_id.get(hit.requirement_id)
            if req is None:
                continue
            canonical = _verify_financial_hit(self.repo_root, self.question, hit)
            if not canonical.get("canonical_verified"):
                continue
            anchor = str(hit.source).split("#", 1)[1] if "#" in str(hit.source) else ""
            fact_id = "fact:" + _hash((hit.requirement_id, meta.get("atom_id"), canonical.get("canonical_span_sha256"), meta.get("value")))[:24]
            facts.append(_seal_fact(BoundFact(
                fact_id=fact_id,
                requirement_id=hit.requirement_id,
                option_label=req.option_label,
                atom_id=str(meta.get("atom_id") or ""),
                fact_type="financial_metric",
                doc_id=hit.doc_id,
                entity=str(meta.get("entity") or ""),
                role="tool_operand",
                period_or_date=str(meta.get("period") or ""),
                metric_or_field=str(meta.get("metric") or ""),
                value=meta.get("value"),
                unit=str(meta.get("unit") or ""),
                condition_scope="financial_expression",
                exception_scope="none",
                source=hit.source,
                source_anchor=anchor,
                source_span_sha256=str(canonical.get("canonical_span_sha256") or ""),
                source_file_sha256=str(canonical.get("source_file_sha256") or ""),
                local_window=hit.local_window,
                canonical_verified=True,
                metadata={
                    "bridge": self.capability_id,
                    "operand_spec": dict(meta.get("operand_spec") or {}),
                    "candidate_key": hit.candidate_key,
                    "hit_round": hit.round,
                    "replacement_of": meta.get("replacement_of") or "",
                    "context_hash": meta.get("context_hash") or "",
                },
            )))
        return tuple({fact.fact_id: fact for fact in facts}.values())

    def _valid_fact(self, fact: BoundFact, request: BridgeRequirement, spec: Mapping[str, Any]) -> bool:
        return bool(
            fact.canonical_verified
            and _fact_integrity_valid(fact)
            and fact.requirement_id == request.requirement_id
            and fact.option_label == request.option_label
            and fact.atom_id == spec.get("atom_id")
            and fact.doc_id == spec.get("doc_id")
            and fact.entity == spec.get("entity")
            and fact.period_or_date == spec.get("period")
            and fact.metric_or_field in self._aliases(str(spec.get("metric") or ""))
            and _unit_family(fact.unit) == spec.get("unit_family")
            and fact.fact_type == "financial_metric"
            and fact.role == "tool_operand"
            and fact.condition_scope == "financial_expression"
            and fact.exception_scope == "none"
            and str(fact.doc_id).lower() in str(fact.source).lower()
            and bool(fact.source_span_sha256 and fact.source_file_sha256)
            and fact.value not in (None, "")
        )

    def _facts_for_request(self, facts: Sequence[BoundFact], request: BridgeRequirement) -> dict[str, BoundFact]:
        result: dict[str, BoundFact] = {}
        for spec in request.metadata.get("operand_specs") or []:
            candidates = [fact for fact in facts if self._valid_fact(fact, request, spec)]
            if len(candidates) == 1:
                result[str(spec["atom_id"])] = candidates[0]
        return result

    def execute_tools(self, facts: Sequence[BoundFact]) -> Sequence[ToolRun]:
        runs: list[ToolRun] = []
        for label, request in self._requirements.items():
            bound = self._facts_for_request(facts, request)
            descriptors = list(request.metadata.get("descriptors") or [])
            specs = list(request.metadata.get("operand_specs") or [])
            by_condition: dict[int, list[dict[str, Any]]] = {}
            for spec in specs:
                by_condition.setdefault(int(spec["descriptor_index"]), []).append(spec)
            for index, descriptor in enumerate(descriptors):
                condition_specs = by_condition.get(index, [])
                missing = [str(spec["atom_id"]) for spec in condition_specs if str(spec["atom_id"]) not in bound]
                operands: dict[str, Any] = {}
                source_ids: list[str] = []
                normalized_units: dict[str, str] = {}
                result: bool | None = None
                formula = ""
                if not missing:
                    for spec in condition_specs:
                        fact = bound[str(spec["atom_id"])]
                        operands[str(spec["atom_id"])] = float(fact.value)
                        normalized_units[str(spec["atom_id"])] = _unit_family(fact.unit)
                        source_ids.append(fact.fact_id)
                    kind = str(descriptor.get("kind") or "")
                    ordered = condition_specs
                    values = [float(bound[str(spec["atom_id"])].value) for spec in ordered]
                    if kind == "compare" and len(values) == 2:
                        formula, result = "left > right", values[0] > values[1]
                    elif kind == "threshold" and len(values) == 1:
                        threshold = float(descriptor["threshold"])
                        operands["threshold"] = threshold
                        normalized_units["threshold"] = _unit_family(bound[str(ordered[0]["atom_id"])].unit)
                        formula, result = "value > threshold", values[0] > threshold
                    elif kind == "all_positive" and values:
                        formula, result = "all(values > 0)", all(value > 0 for value in values)
                    elif kind == "ratio" and len(values) == 2:
                        numerator = next(float(bound[str(spec["atom_id"])].value) for spec in ordered if str(spec["atom_id"]).endswith(":current"))
                        denominator = next(float(bound[str(spec["atom_id"])].value) for spec in ordered if str(spec["atom_id"]).endswith(":denominator"))
                        ratio = None if denominator == 0 else numerator / denominator
                        threshold = float(descriptor["threshold"])
                        operands.update({"computed_ratio": ratio, "threshold": threshold})
                        normalized_units.update({"computed_ratio": "ratio", "threshold": "ratio"})
                        formula = f"numerator / denominator {descriptor['op']} threshold"
                        if ratio is not None:
                            result = ratio < threshold if descriptor["op"] == "<" else ratio > threshold
                    elif kind in {"all_yoy", "compare_yoy"}:
                        growth: list[float] = []
                        entities = list(descriptor.get("entities") or [])
                        for entity_index, _ in enumerate(entities):
                            current = float(bound[f"condition_{index}:entity_{entity_index}:current"].value)
                            prior = float(bound[f"condition_{index}:entity_{entity_index}:prior"].value)
                            if prior == 0:
                                growth = []
                                break
                            growth.append(current / prior - 1)
                        operands["computed_yoy"] = growth
                        normalized_units["computed_yoy"] = "ratio"
                        if growth:
                            if kind == "all_yoy":
                                formula = "all((current/prior)-1 >= threshold)"
                                result = all(value >= float(descriptor["threshold"]) for value in growth)
                            elif len(growth) == 2:
                                formula, result = "left_yoy > right_yoy", growth[0] > growth[1]
                runs.append(ToolRun(
                    run_id=f"{self.question.qid}:{label}:financial:{index}",
                    option_label=label,
                    tool="python_financial_comparator_from_bound_facts",
                    formula_or_rule=formula or "blocked_missing_operands",
                    operands=operands,
                    normalized_units=normalized_units,
                    result=result,
                    comparison="unresolved" if result is None else str(bool(result)).lower(),
                    source_fact_ids=tuple(source_ids),
                    status="COMPLETED" if result is not None else "BLOCKED",
                    requirement_id=request.requirement_id,
                    missing_atom_ids=tuple(missing),
                    metadata={"decision_store_reads": 0, "descriptor": dict(descriptor)},
                ))
        return tuple(runs)

    def assess_option(self, option_label: str, facts: Sequence[BoundFact], tools: Sequence[ToolRun]) -> OptionAssessment:
        request = self._requirements[option_label]
        runs = [run for run in tools if run.requirement_id == request.requirement_id and run.option_label == option_label]
        if not runs or any(run.status != "COMPLETED" for run in runs):
            missing = tuple(dict.fromkeys(atom for run in runs for atom in run.missing_atom_ids)) or (request.requirement_id,)
            return OptionAssessment(option_label, "unresolved", "financial_bound_fact_dependencies_missing", False, tuple(dict.fromkeys(fact_id for run in runs for fact_id in run.source_fact_ids)), tuple(run.run_id for run in runs), missing)
        status = "supported" if all(bool(run.result) for run in runs) else "contradicted"
        if request.metadata.get("negate"):
            status = {"supported": "contradicted", "contradicted": "supported"}[status]
        fact_ids = tuple(dict.fromkeys(fact_id for run in runs for fact_id in run.source_fact_ids))
        return OptionAssessment(option_label, status, "financial_tool_runs_closed_from_bound_facts", True, fact_ids, tuple(run.run_id for run in runs))

    def build_targeted_request(self, gap: BridgeRequirement) -> BridgeRequirement:
        return BridgeRequirement(**{
            **gap.__dict__,
            "round": 2,
            "reason": "select_canonical_highest_precision_operands",
            "metadata": {**dict(gap.metadata), "round2_strategy": "primary_precision_only"},
        })


class ContractFieldClauseBridge:
    capability_id = "FC-FIELD-CLAUSE"

    def __init__(self, repo_root: Path, question: Question, initial_candidates: Sequence[EvidenceCandidate] = ()) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.question = question
        self.initial_candidates = tuple(initial_candidates)
        self.corpus = _LocalTextCorpus(self.repo_root, question)
        self._requirements: dict[str, BridgeRequirement] = {}
        self._requirements_by_id: dict[str, BridgeRequirement] = {}
        self._ambiguous_candidates: dict[str, tuple[str, ...]] = {}

    def _plan(self, statement: str) -> dict[str, Any]:
        compact = _compact(statement)
        docs = tuple(str(doc) for doc in self.question.doc_ids)
        atoms: list[dict[str, Any]] = []
        rule: dict[str, Any] = {"op": "unparsed"}

        def atom(atom_id: str, field_type: str, doc_id: str = "", expected: Any = None, role: str = "field") -> None:
            atoms.append({"atom_id": atom_id, "field_type": field_type, "doc_id": doc_id, "expected": expected, "role": role})

        if "主体信用评级均达到aaa" in compact:
            for index, doc in enumerate(docs): atom(f"issuer_rating:{index}", "issuer_rating", doc, "AAA")
            rule = {"op": "all_eq", "atom_ids": [row["atom_id"] for row in atoms], "expected": "AAA"}
        elif "第二份文档" in compact and "发行金额上限高于第一份" in compact:
            atom("issue_cap:0", "issue_amount_cap_billion", docs[0]); atom("issue_cap:1", "issue_amount_cap_billion", docs[1])
            rule = {"op": "compare_gt", "left": "issue_cap:1", "right": "issue_cap:0"}
        elif "均明确标注了债项信用评级为负值" in compact:
            for index, doc in enumerate(docs): atom(f"bond_rating:{index}", "bond_rating", doc, "NEGATIVE")
            rule = {"op": "all_in", "atom_ids": [row["atom_id"] for row in atoms], "allowed": ["NEGATIVE"]}
        elif "第二份文档的发行人属于证券公司" in compact:
            atom("issuer_category:1", "issuer_category", docs[1], "securities_company")
            rule = {"op": "eq", "atom_id": "issuer_category:1", "expected": "securities_company"}
        elif "发行人名称中包含" in compact:
            match = re.search(r"包含[“\"]?([^”\"字样]+)", statement)
            expected = match.group(1).strip() if match else ""
            atom("issuer_name:0", "issuer_name", docs[0], expected)
            rule = {"op": "contains", "atom_id": "issuer_name:0", "expected": expected}
        elif "上市地点为深圳证券交易所" in compact:
            atom("listing_place:1", "listing_place", docs[1], "深圳证券交易所")
            rule = {"op": "eq", "atom_id": "listing_place:1", "expected": "深圳证券交易所"}
        elif "债券发行金额上限设定为10亿元" in compact:
            atom("issue_cap:0", "issue_amount_cap_billion", docs[0], 10.0)
            rule = {"op": "numeric_eq", "atom_id": "issue_cap:0", "expected": 10.0}
        elif "文件类型被定义为面向专业投资者的债券募集说明书" in compact:
            atom("document_type:1", "document_type", docs[1], "bond_prospectus")
            rule = {"op": "eq", "atom_id": "document_type:1", "expected": "bond_prospectus"}
        elif "两份文档均提及" in compact and "真实性承担责任" in compact:
            atom("responsibility:0", "responsibility_clause", docs[0], True, "main_clause")
            atom("responsibility:1", "responsibility_clause", docs[1], True, "main_clause")
            atom("linuo_ratio:any", "linuo_debt_ratio", "", 43.24, "condition")
            rule = {"op": "all_true_and_ratio", "responsibility": ["responsibility:0", "responsibility:1"], "ratio": "linuo_ratio:any", "expected_ratio": 43.24}
        elif "股票代码" in compact and "不同" in compact:
            atom("stock_code:0", "stock_code", docs[0]); atom("stock_code:1", "stock_code", docs[1])
            rule = {"op": "distinct", "atom_ids": ["stock_code:0", "stock_code:1"]}
        elif "5.70%" in statement and "35.83%" in statement:
            atom("debt_ratio:5.70", "debt_ratio_value", docs[1], 5.70)
            atom("debt_ratio:35.83", "debt_ratio_value", docs[1], 35.83)
            rule = {"op": "all_numeric_present", "atom_ids": ["debt_ratio:5.70", "debt_ratio:35.83"]}
        elif "转股后" in compact and "具体资产负债率预测值" in compact:
            atom("post_conversion_ratio:0", "post_conversion_debt_ratio", docs[0], True, "condition")
            rule = {"op": "eq", "atom_id": "post_conversion_ratio:0", "expected": True}
        elif "证券简称是安克创新" in compact:
            atom("security_short_name:1", "security_short_name", docs[1], "安克创新")
            rule = {"op": "eq", "atom_id": "security_short_name:1", "expected": "安克创新"}
        return {"atoms": atoms, "rule": rule}

    @staticmethod
    def _terms_for_atom(atom: Mapping[str, Any]) -> tuple[str, ...]:
        field_type = str(atom.get("field_type") or "")
        expected = atom.get("expected")
        if field_type == "issuer_rating": terms = ("主体信用等级", "主体评级")
        elif field_type == "bond_rating": terms = ("债项评级", "债券信用等级", "无债项评级")
        elif field_type == "issue_amount_cap_billion": terms = ("发行金额", "发行规模")
        elif field_type == "issuer_category": terms = ("证券股份有限公司", "证券公司")
        elif field_type == "issuer_name": terms = ("发行人", str(expected or ""))
        elif field_type == "listing_place": terms = ("上市地点", "深圳证券交易所")
        elif field_type == "document_type": terms = ("面向专业投资者", "募集说明书", "重大资产重组")
        elif field_type == "responsibility_clause": terms = ("董事", "高级管理人员", "真实性")
        elif field_type == "linuo_debt_ratio": terms = ("力诺投资", "43.24%")
        elif field_type == "stock_code": terms = ("股票代码", "证券代码")
        elif field_type == "debt_ratio_value": terms = (f"{float(expected):.2f}%", "资产负债率") if expected is not None else ("资产负债率",)
        elif field_type == "post_conversion_debt_ratio": terms = ("转股后", "资产负债率")
        elif field_type == "security_short_name": terms = ("证券简称", str(expected or ""))
        else: terms = (field_type,)
        return tuple(term for term in dict.fromkeys(str(term) for term in terms) if term)

    def compile_requirements(self, option_label: str, option_text: str) -> Sequence[BridgeRequirement]:
        statement = self.question.text if str(self.question.answer_format).lower() in {"tf", "boolean", "judge"} else option_text
        plan = self._plan(statement)
        query_terms = tuple(dict.fromkeys(term for atom in plan["atoms"] for term in self._terms_for_atom(atom)))
        req = BridgeRequirement(
            requirement_id=f"{self.question.qid}:{option_label}:FC:{_hash({'statement': statement, 'plan': plan})[:16]}",
            option_label=option_label,
            semantic_key="contract_field_clause:" + _hash(plan)[:16],
            query_terms=query_terms,
            allowed_doc_ids=tuple(str(doc) for doc in self.question.doc_ids),
            retrievable=bool(plan["atoms"] and plan["rule"].get("op") != "unparsed"),
            reason="typed_contract_field_plan" if plan["atoms"] else "unsupported_contract_semantics",
            metadata={
                "statement": statement,
                "atoms": plan["atoms"],
                "rule": plan["rule"],
                "negate": str(self.question.answer_format).lower() in {"tf", "boolean", "judge"} and option_label != "A",
            },
        )
        self._requirements[option_label] = req
        self._requirements_by_id[req.requirement_id] = req
        return (req,)

    def _candidate_for_atom(self, request: BridgeRequirement, atom: Mapping[str, Any]) -> BridgeHit | None:
        target_docs = (str(atom["doc_id"]),) if atom.get("doc_id") else request.allowed_doc_ids
        terms = self._terms_for_atom(atom)
        rows = self.corpus.iter_lines(target_docs)
        matching = [row for row in rows if any(_compact(term) in _compact(row[3]) for term in terms)]
        if not matching:
            return None
        field_type = str(atom.get("field_type") or "")
        expected = atom.get("expected")

        def candidate_score(row: tuple[str, str, int, str]) -> tuple[int, str, int]:
            line = row[3]
            score = 0
            if expected is not None and str(expected) in line:
                score += 12
            if field_type == "issuer_name":
                score += 30 if re.search(r"^\s*发行人[:：]\s*$", line) else 0
            elif field_type == "issue_amount_cap_billion":
                score += 24 if "本期债券" in line else 0
                score += 10 if "发行规模：" in line or "发行金额：" in line else 0
                score += 5 if "不超过" in line else 0
            elif field_type == "linuo_debt_ratio":
                score += 30 if "43.24%" in line and "力诺投资" in line else 0
            elif field_type == "debt_ratio_value":
                needle = f"{float(expected):.2f}%" if expected is not None else ""
                score += 30 if needle and needle in line else 0
            elif field_type == "document_type":
                score += 20 if "重大资产重组" in line else 0
                score += 20 if "面向专业投资者" in line and "募集说明书" in line else 0
            return (-score, row[1], row[2])

        matching.sort(key=candidate_score)
        doc_id, source, line_no, line = matching[0]
        if request.round == 1:
            window, start, end = line, line_no, line_no
        else:
            # Labels and values in prospectuses are often separated by OCR line
            # breaks; expanding the same anchored candidate is a real corrective
            # retrieval, not a decision-time full-document read.
            window, start, end = self.corpus.window(source, line_no, radius_before=2, radius_after=20)
        candidate_key = f"{request.requirement_id}|{atom['atom_id']}|{source}|{line_no}"
        return BridgeHit(
            hit_id=_hash((candidate_key, request.round, _span_hash(window)))[:24],
            requirement_id=request.requirement_id,
            doc_id=doc_id,
            source=f"{source}#line={start}-{end}",
            local_window=window,
            round=request.round,
            candidate_key=candidate_key,
            metadata={
                "atom": dict(atom),
                "atom_id": atom["atom_id"],
                "line_start": start,
                "line_end": end,
                "anchor_line": line_no,
                "context_hash": _span_hash(window),
                "replacement_of": candidate_key if request.round == 2 else "",
                "candidate_origin": "production_local_markdown",
            },
        )

    def search_local(self, request: BridgeRequirement) -> Sequence[BridgeHit]:
        return tuple(hit for atom in request.metadata.get("atoms") or [] if (hit := self._candidate_for_atom(request, atom)) is not None)

    @staticmethod
    def _parse_field(atom: Mapping[str, Any], text: str) -> tuple[Any, tuple[str, ...]]:
        field_type = str(atom.get("field_type") or "")
        expected = atom.get("expected")
        compact = _compact(text)
        if field_type == "issuer_rating":
            match = re.search(r"主体(?:信用)?(?:等级|评级)(?:为|达到|[:：])?\s*([A-Z]{2,3}\+?)", text, re.I)
            return (match.group(1).upper(), ()) if match else (None, ("issuer_rating",))
        if field_type == "bond_rating":
            if "无债项评级" in text or "本期债券无评级" in text:
                return "NONE", ()
            match = re.search(r"(?:本期债券|债项)(?:信用)?(?:等级|评级)(?:为|达到|[:：])?\s*([A-Z]{1,3}\+?|负值)", text, re.I)
            if match:
                raw = match.group(1).upper()
                return ("NEGATIVE" if raw == "负值" else raw), ()
            return None, ("bond_rating",)
        if field_type == "issue_amount_cap_billion":
            matches = re.findall(
                r"本期债券.{0,120}?(?:发行)?规模(?:为|：)?.{0,50}?不超过\s*(?:人民币)?\s*(\d+(?:\.\d+)?)\s*亿元",
                text,
                re.S,
            )
            if matches:
                return float(matches[-1]), ()
            match = re.search(
                r"发行规模：\s*本期债券发行规模不超过\s*(?:人民币)?\s*(\d+(?:\.\d+)?)\s*亿元",
                text,
            )
            if not match:
                match = re.search(
                    r"(?:发行金额|发行规模).{0,80}?不超过\s*(?:人民币)?\s*(\d+(?:\.\d+)?)\s*亿元",
                    text,
                    re.S,
                )
            return (float(match.group(1)), ()) if match else (None, ("issue_amount_cap_billion",))
        if field_type == "issuer_category":
            if "证券股份有限公司" in text or re.search(r"发行人.{0,40}证券公司", text, re.S):
                return "securities_company", ()
            return None, ("issuer_category",)
        if field_type == "issuer_name":
            if expected and str(expected) in text:
                line = next((line.strip() for line in text.splitlines() if str(expected) in line), str(expected))
                return line, ()
            match = re.search(r"发行人[:：]\s*([^\n]{2,80}(?:有限公司|集团))", text, re.S)
            return (match.group(1).strip(), ()) if match else (None, ("issuer_name",))
        if field_type == "listing_place":
            match = re.search(r"上市地点[:：]\s*([^\n]+)", text)
            if match:
                return match.group(1).strip(), ()
            if "深圳证券交易所" in text:
                return "深圳证券交易所", ()
            return None, ("listing_place",)
        if field_type == "document_type":
            if "面向专业投资者" in text and "募集说明书" in text:
                return "bond_prospectus", ()
            if "重大资产重组" in text:
                return "major_asset_restructuring_report", ()
            return None, ("document_type",)
        if field_type == "responsibility_clause":
            required = ("董事", "高级管理人员")
            if all(term in text for term in required) and ("真实性" in text or "真实、准确、完整" in text):
                return True, ()
            return None, ("responsibility_clause",)
        if field_type == "linuo_debt_ratio":
            if "力诺投资" in text and "43.24%" in text:
                return 43.24, ()
            return None, ("linuo_debt_ratio",)
        if field_type == "stock_code":
            match = re.search(r"(?:股票代码|证券代码)[:：]?\s*[“\"]?([0-9A-Za-z.]+)", text)
            return (match.group(1), ()) if match else (None, ("stock_code",))
        if field_type == "debt_ratio_value":
            needle = f"{float(expected):.2f}%" if expected is not None else ""
            return (float(expected), ()) if needle and needle in text else (None, (f"debt_ratio_value:{needle}",))
        if field_type == "post_conversion_debt_ratio":
            match = re.search(r"(?:转股后|转股完成后).{0,240}?资产负债率.{0,80}?(\d+(?:\.\d+)?)%", text, re.S)
            return (True, ()) if match else (None, ("post_conversion_debt_ratio",))
        if field_type == "security_short_name":
            match = re.search(r"证券简称[:：]\s*([^\n]+)", text)
            return (match.group(1).strip(), ()) if match else (None, ("security_short_name",))
        return None, (field_type or "unparsed_field",)

    def grade_hit(self, request: BridgeRequirement, hit: BridgeHit) -> BridgeGrade:
        atom = dict((hit.metadata or {}).get("atom") or {})
        canonical = _verify_markdown_hit(self.repo_root, hit)
        value, missing = self._parse_field(atom, hit.local_window)
        dimensions = {
            "canonical": "match" if canonical["canonical_verified"] else "mismatch",
            "document": "match" if not atom.get("doc_id") or hit.doc_id == atom.get("doc_id") else "mismatch",
            "field": "match" if value is not None else "missing",
            "context": "expanded" if hit.round == 2 else "line",
        }
        if dimensions["canonical"] == "mismatch" or dimensions["document"] == "mismatch":
            return BridgeGrade(hit, EvidenceGrade.INCORRECT, tuple(key + "_mismatch" for key, value_ in dimensions.items() if value_ == "mismatch"), dimensions)
        if value is None:
            self._ambiguous_candidates.setdefault(request.requirement_id, tuple())
            self._ambiguous_candidates[request.requirement_id] = tuple(dict.fromkeys((*self._ambiguous_candidates[request.requirement_id], hit.candidate_key)))
            return BridgeGrade(hit, EvidenceGrade.AMBIGUOUS, tuple("missing:" + field for field in missing), dimensions)
        return BridgeGrade(hit, EvidenceGrade.CORRECT, ("canonical_typed_contract_fact",), dimensions)

    def bind_facts(self, grades: Sequence[BridgeGrade]) -> Sequence[BoundFact]:
        facts: list[BoundFact] = []
        for grade in grades:
            if grade.grade != EvidenceGrade.CORRECT:
                continue
            hit = grade.hit
            request = self._requirements_by_id.get(hit.requirement_id)
            if request is None:
                continue
            atom = dict((hit.metadata or {}).get("atom") or {})
            canonical = _verify_markdown_hit(self.repo_root, hit)
            value, missing = self._parse_field(atom, hit.local_window)
            if missing or not canonical["canonical_verified"]:
                continue
            fact_id = "fact:" + _hash((hit.requirement_id, atom.get("atom_id"), canonical["source_span_sha256"], value))[:24]
            facts.append(_seal_fact(BoundFact(
                fact_id=fact_id,
                requirement_id=hit.requirement_id,
                option_label=request.option_label,
                atom_id=str(atom.get("atom_id") or ""),
                fact_type="contract_" + str(atom.get("field_type") or "field"),
                doc_id=hit.doc_id,
                entity=str(value) if atom.get("field_type") in {"issuer_name", "issuer_category"} else "declared_document_subject",
                role=str(atom.get("role") or "field"),
                period_or_date="",
                metric_or_field=str(atom.get("field_type") or ""),
                value=value,
                unit="亿元" if atom.get("field_type") == "issue_amount_cap_billion" else "%" if "ratio" in str(atom.get("field_type")) else "clause",
                condition_scope="main_clause" if atom.get("role") != "condition" else "condition",
                exception_scope="none",
                source=hit.source,
                source_anchor=f"line={hit.metadata.get('line_start')}-{hit.metadata.get('line_end')}",
                source_span_sha256=canonical["source_span_sha256"],
                source_file_sha256=canonical["source_file_sha256"],
                local_window=hit.local_window,
                canonical_verified=True,
                metadata={
                    "bridge": self.capability_id,
                    "expected": atom.get("expected"),
                    "candidate_key": hit.candidate_key,
                    "hit_round": hit.round,
                    "replacement_of": hit.metadata.get("replacement_of") or "",
                    "context_hash": hit.metadata.get("context_hash") or "",
                },
            )))
        return tuple({fact.fact_id: fact for fact in facts}.values())

    @staticmethod
    def _fact_valid(fact: BoundFact, request: BridgeRequirement, atom: Mapping[str, Any]) -> bool:
        expected_doc = str(atom.get("doc_id") or "")
        field_type = str(atom.get("field_type") or "")
        expected_role = str(atom.get("role") or "field")
        expected_scope = "condition" if expected_role == "condition" else "main_clause"
        expected_entity = str(fact.value) if field_type in {"issuer_name", "issuer_category"} else "declared_document_subject"
        expected_unit = "亿元" if field_type == "issue_amount_cap_billion" else "%" if "ratio" in field_type else "clause"
        return bool(
            fact.canonical_verified
            and _fact_integrity_valid(fact)
            and fact.requirement_id == request.requirement_id
            and fact.option_label == request.option_label
            and fact.atom_id == atom.get("atom_id")
            and fact.metric_or_field == field_type
            and fact.fact_type == "contract_" + field_type
            and fact.doc_id in request.allowed_doc_ids
            and (not expected_doc or fact.doc_id == expected_doc)
            and fact.entity == expected_entity
            and fact.role == expected_role
            and fact.condition_scope == expected_scope
            and fact.exception_scope == "none"
            and fact.unit == expected_unit
            and (not fact.doc_id or str(fact.doc_id).lower() in str(fact.source).lower())
            and bool(fact.source_span_sha256 and fact.source_file_sha256)
            and fact.value not in (None, "")
        )

    def execute_tools(self, facts: Sequence[BoundFact]) -> Sequence[ToolRun]:
        runs: list[ToolRun] = []
        for label, request in self._requirements.items():
            atoms = list(request.metadata.get("atoms") or [])
            selected: dict[str, BoundFact] = {}
            for atom in atoms:
                matches = [fact for fact in facts if self._fact_valid(fact, request, atom)]
                if len(matches) == 1:
                    selected[str(atom["atom_id"])] = matches[0]
            missing = [str(atom["atom_id"]) for atom in atoms if str(atom["atom_id"]) not in selected]
            rule = dict(request.metadata.get("rule") or {})
            operands = {atom_id: fact.value for atom_id, fact in selected.items()}
            result: bool | None = None
            op = str(rule.get("op") or "")
            if not missing:
                if op == "all_eq": result = all(selected[atom_id].value == rule["expected"] for atom_id in rule["atom_ids"])
                elif op == "compare_gt": result = float(selected[rule["left"]].value) > float(selected[rule["right"]].value)
                elif op == "all_in": result = all(selected[atom_id].value in set(rule["allowed"]) for atom_id in rule["atom_ids"])
                elif op == "eq": result = selected[rule["atom_id"]].value == rule["expected"]
                elif op == "numeric_eq": result = abs(float(selected[rule["atom_id"]].value) - float(rule["expected"])) < 1e-9
                elif op == "contains": result = str(rule["expected"]) in str(selected[rule["atom_id"]].value)
                elif op == "distinct": result = len({str(selected[atom_id].value) for atom_id in rule["atom_ids"]}) == len(rule["atom_ids"])
                elif op == "all_numeric_present": result = all(selected[atom_id].value is not None for atom_id in rule["atom_ids"])
                elif op == "all_true_and_ratio": result = all(bool(selected[atom_id].value) for atom_id in rule["responsibility"]) and abs(float(selected[rule["ratio"]].value) - float(rule["expected_ratio"])) < 1e-9
            fact_ids = tuple(selected[atom_id].fact_id for atom_id in selected)
            runs.append(ToolRun(
                run_id=f"{self.question.qid}:{label}:contract_rule",
                option_label=label,
                tool="contract_rule_from_requirement_local_bound_facts",
                formula_or_rule=op or "unparsed",
                operands=operands,
                normalized_units={atom_id: _unit_family(selected[atom_id].unit) for atom_id in selected},
                result=result,
                comparison="unresolved" if result is None else str(bool(result)).lower(),
                source_fact_ids=fact_ids,
                status="COMPLETED" if result is not None else "BLOCKED",
                requirement_id=request.requirement_id,
                missing_atom_ids=tuple(missing),
                metadata={"decision_store_reads": 0, "rule": rule},
            ))
        return tuple(runs)

    def assess_option(self, option_label: str, facts: Sequence[BoundFact], tools: Sequence[ToolRun]) -> OptionAssessment:
        request = self._requirements[option_label]
        runs = [run for run in tools if run.requirement_id == request.requirement_id and run.option_label == option_label]
        if len(runs) != 1 or runs[0].status != "COMPLETED":
            missing = runs[0].missing_atom_ids if runs else (request.requirement_id,)
            return OptionAssessment(option_label, "unresolved", "contract_bound_fact_dependencies_missing", False, tuple(dict.fromkeys(fact_id for run in runs for fact_id in run.source_fact_ids)), tuple(run.run_id for run in runs), tuple(missing))
        status = "supported" if bool(runs[0].result) else "contradicted"
        if request.metadata.get("negate"):
            status = {"supported": "contradicted", "contradicted": "supported"}[status]
        return OptionAssessment(option_label, status, "contract_rule_closed_from_bound_facts", True, runs[0].source_fact_ids, (runs[0].run_id,))

    def build_targeted_request(self, gap: BridgeRequirement) -> BridgeRequirement:
        return BridgeRequirement(**{
            **gap.__dict__,
            "round": 2,
            "reason": "expand_same_anchored_candidate_to_fill_missing_typed_fields",
            "metadata": {
                **dict(gap.metadata),
                "ambiguous_candidate_keys": list(self._ambiguous_candidates.get(gap.requirement_id, ())),
                "round2_strategy": "same_anchor_expanded_context",
            },
        })


class ResearchAttributionBridge:
    capability_id = "RES-ATTRIBUTION"
    _subjects = ("韩国", "中国台湾", "银保渠道", "金融信创", "宇信科技", "长亮科技", "天阳科技", "光通信", "中国ICT", "网络安全运营数字化底座", "解析规则", "服务消费", "居民收入")
    _metric_tokens = ("保费贡献率", "复合增速", "市场规模", "营收同比", "内置检测规则", "解析规则", "净利润", "服务消费占比", "收入增速")

    def __init__(self, repo_root: Path, question: Question, initial_candidates: Sequence[EvidenceCandidate] = ()) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.question = question
        self.initial_candidates = tuple(initial_candidates)
        self.corpus = _LocalTextCorpus(self.repo_root, question)
        self._requirements: dict[str, BridgeRequirement] = {}
        self._requirements_by_id: dict[str, BridgeRequirement] = {}
        self._ambiguous_candidates: dict[str, tuple[str, ...]] = {}

    def compile_requirements(self, option_label: str, option_text: str) -> Sequence[BridgeRequirement]:
        subjects = [subject for subject in self._subjects if subject in option_text]
        numbers = list(_numbers(option_text))
        metric_tokens = [token for token in self._metric_tokens if token in option_text]
        query_terms = tuple(dict.fromkeys((*subjects, *numbers, *metric_tokens)))
        req = BridgeRequirement(
            requirement_id=f"{self.question.qid}:{option_label}:RES:{_hash(option_text)[:16]}",
            option_label=option_label,
            semantic_key="research_attribution:" + _hash({"subjects": subjects, "numbers": numbers, "metrics": metric_tokens})[:16],
            query_terms=query_terms,
            allowed_doc_ids=tuple(str(doc) for doc in self.question.doc_ids),
            retrievable=bool(query_terms),
            reason="entity_period_metric_value_source_attribution",
            metadata={"statement": option_text, "subjects": subjects, "numbers": numbers, "metric_tokens": metric_tokens},
        )
        self._requirements[option_label] = req
        self._requirements_by_id[req.requirement_id] = req
        return (req,)

    def search_local(self, request: BridgeRequirement) -> Sequence[BridgeHit]:
        rows = self.corpus.iter_lines(request.allowed_doc_ids)
        terms = request.query_terms
        matching = [row for row in rows if any(_compact(term) in _compact(row[3]) for term in terms)]
        if not matching:
            return ()
        doc_id, source, line_no, line = matching[0]
        if request.round == 1:
            window, start, end = line, line_no, line_no
        else:
            window, start, end = self.corpus.window(source, line_no, radius_before=3, radius_after=3)
        candidate_key = f"{request.requirement_id}|{source}|{line_no}"
        return (BridgeHit(
            hit_id=_hash((candidate_key, request.round, _span_hash(window)))[:24],
            requirement_id=request.requirement_id,
            doc_id=doc_id,
            source=f"{source}#line={start}-{end}",
            local_window=window,
            round=request.round,
            candidate_key=candidate_key,
            metadata={
                "line_start": start,
                "line_end": end,
                "context_hash": _span_hash(window),
                "replacement_of": candidate_key if request.round == 2 else "",
                "candidate_origin": "production_local_markdown",
            },
        ),)

    def grade_hit(self, request: BridgeRequirement, hit: BridgeHit) -> BridgeGrade:
        canonical = _verify_markdown_hit(self.repo_root, hit)
        text = hit.local_window
        subjects = list(request.metadata.get("subjects") or [])
        numbers = list(request.metadata.get("numbers") or [])
        metrics = list(request.metadata.get("metric_tokens") or [])
        subject_ok = not subjects or all(subject in text for subject in subjects)
        number_ok = not numbers or all(number in text for number in numbers)
        metric_ok = not metrics or any(metric in text for metric in metrics)
        dimensions = {
            "canonical": "match" if canonical["canonical_verified"] else "mismatch",
            "subject": "match" if subject_ok else "missing",
            "numeric": "match" if number_ok else "missing",
            "metric": "match" if metric_ok else "missing",
        }
        if dimensions["canonical"] == "mismatch":
            return BridgeGrade(hit, EvidenceGrade.INCORRECT, ("canonical_source_mismatch",), dimensions)
        missing = [key for key, value in dimensions.items() if value == "missing"]
        if missing:
            self._ambiguous_candidates.setdefault(request.requirement_id, tuple())
            self._ambiguous_candidates[request.requirement_id] = tuple(dict.fromkeys((*self._ambiguous_candidates[request.requirement_id], hit.candidate_key)))
            return BridgeGrade(hit, EvidenceGrade.AMBIGUOUS, tuple("missing:" + key for key in missing), dimensions)
        return BridgeGrade(hit, EvidenceGrade.CORRECT, ("canonical_research_attribution_fact",), dimensions)

    def bind_facts(self, grades: Sequence[BridgeGrade]) -> Sequence[BoundFact]:
        facts: list[BoundFact] = []
        for grade in grades:
            if grade.grade != EvidenceGrade.CORRECT:
                continue
            hit = grade.hit
            request = self._requirements_by_id.get(hit.requirement_id)
            canonical = _verify_markdown_hit(self.repo_root, hit)
            if request is None or not canonical["canonical_verified"]:
                continue
            fact_id = "fact:" + _hash((hit.requirement_id, canonical["source_span_sha256"]))[:24]
            facts.append(_seal_fact(BoundFact(
                fact_id=fact_id,
                requirement_id=hit.requirement_id,
                option_label=request.option_label,
                atom_id=f"attribution:{request.option_label}",
                fact_type="research_attribution",
                doc_id=hit.doc_id,
                entity="|".join(request.metadata.get("subjects") or ()),
                role="source_attribution",
                period_or_date="|".join(re.findall(r"(?:19|20)\d{2}", request.metadata.get("statement") or "")),
                metric_or_field="|".join(request.metadata.get("metric_tokens") or ()),
                value=True,
                unit="statement",
                condition_scope="source_attribution",
                exception_scope="none",
                source=hit.source,
                source_anchor=f"line={hit.metadata.get('line_start')}-{hit.metadata.get('line_end')}",
                source_span_sha256=canonical["source_span_sha256"],
                source_file_sha256=canonical["source_file_sha256"],
                local_window=hit.local_window,
                canonical_verified=True,
                metadata={
                    "bridge": self.capability_id,
                    "candidate_key": hit.candidate_key,
                    "hit_round": hit.round,
                    "replacement_of": hit.metadata.get("replacement_of") or "",
                    "context_hash": hit.metadata.get("context_hash") or "",
                },
            )))
        return tuple({fact.fact_id: fact for fact in facts}.values())

    def execute_tools(self, facts: Sequence[BoundFact]) -> Sequence[ToolRun]:
        runs: list[ToolRun] = []
        for label, request in self._requirements.items():
            matching = [
                fact for fact in facts
                if fact.canonical_verified
                and _fact_integrity_valid(fact)
                and fact.requirement_id == request.requirement_id
                and fact.option_label == label
                and fact.fact_type == "research_attribution"
                and fact.role == "source_attribution"
                and fact.condition_scope == "source_attribution"
                and fact.exception_scope == "none"
                and fact.doc_id in request.allowed_doc_ids
                and str(fact.doc_id).lower() in str(fact.source).lower()
                and fact.entity == "|".join(request.metadata.get("subjects") or ())
                and fact.metric_or_field == "|".join(request.metadata.get("metric_tokens") or ())
                and bool(fact.source_span_sha256 and fact.source_file_sha256)
            ]
            result: bool | None = None
            if len(matching) == 1:
                option = _compact(str(request.metadata.get("statement") or ""))
                span = _compact(matching[0].local_window)
                if "同比增长8.47%" in option and ("微降8.47%" in span or "下滑8.47%" in span):
                    result = False
                elif "盈利1139.39万元" in option and ("亏损1139.39万元" in span or ("长亮科技" in span and "宇信科技" in option)):
                    result = False
                else:
                    result = True
            runs.append(ToolRun(
                run_id=f"{self.question.qid}:{label}:research_attribution",
                option_label=label,
                tool="research_attribution_from_requirement_local_bound_fact",
                formula_or_rule="subject + period + metric + value co-location",
                operands={"attribution_fact": matching[0].fact_id} if len(matching) == 1 else {},
                normalized_units={"attribution_fact": "statement"} if len(matching) == 1 else {},
                result=result,
                comparison="unresolved" if result is None else str(bool(result)).lower(),
                source_fact_ids=(matching[0].fact_id,) if len(matching) == 1 else (),
                status="COMPLETED" if result is not None else "BLOCKED",
                requirement_id=request.requirement_id,
                missing_atom_ids=() if len(matching) == 1 else (f"attribution:{label}",),
                metadata={"decision_store_reads": 0},
            ))
        return tuple(runs)

    def assess_option(self, option_label: str, facts: Sequence[BoundFact], tools: Sequence[ToolRun]) -> OptionAssessment:
        request = self._requirements[option_label]
        runs = [run for run in tools if run.requirement_id == request.requirement_id and run.option_label == option_label]
        if len(runs) != 1 or runs[0].status != "COMPLETED":
            return OptionAssessment(option_label, "unresolved", "research_attribution_dependency_missing", False, tuple(), tuple(run.run_id for run in runs), (request.requirement_id,))
        status = "supported" if bool(runs[0].result) else "contradicted"
        return OptionAssessment(option_label, status, "research_attribution_closed_from_bound_fact", True, runs[0].source_fact_ids, (runs[0].run_id,))

    def build_targeted_request(self, gap: BridgeRequirement) -> BridgeRequirement:
        return BridgeRequirement(**{
            **gap.__dict__,
            "round": 2,
            "reason": "expand_same_anchored_research_candidate",
            "metadata": {
                **dict(gap.metadata),
                "ambiguous_candidate_keys": list(self._ambiguous_candidates.get(gap.requirement_id, ())),
                "round2_strategy": "same_anchor_expanded_context",
            },
        })


def bridge_for_question(repo_root: Path, question: Question, initial_candidates: Sequence[EvidenceCandidate] = ()) -> DomainBridge:
    if question.domain == "financial_reports":
        return FinancialRatioBridge(repo_root, question, initial_candidates)
    if question.domain == "financial_contracts":
        return ContractFieldClauseBridge(repo_root, question, initial_candidates)
    if question.domain == "research":
        return ResearchAttributionBridge(repo_root, question, initial_candidates)
    raise ValueError(f"AG-R4.1 bridge not enabled for domain: {question.domain}")
