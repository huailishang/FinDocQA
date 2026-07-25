"""Shared mechanics for physically isolated Package AG domain adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from evaluation.domain_adapters.common import (
    CLOSED_STATES,
    DecisiveFieldProvenance,
    SourceSpanRef,
    canonical_answer,
)
from evaluation.independent_option_fact_binding import extract_corpus_span


@dataclass(frozen=True)
class DomainOptionResult:
    option: str
    status: str
    source_spans: tuple[SourceSpanRef, ...]
    decisive_field_provenance: tuple[DecisiveFieldProvenance, ...]
    blockers: tuple[str, ...]
    adapter_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DomainQuestionResult:
    qid: str
    domain: str
    production_answer: str
    option_statuses: Mapping[str, str]
    option_results: Mapping[str, DomainOptionResult]
    all_options_closed: bool
    production_trusted: bool
    production_capability: str
    blockers: tuple[str, ...]
    payload_answer_for_audit: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["option_results"] = {
            key: value.to_dict() for key, value in self.option_results.items()
        }
        return payload


def normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"supported", "support", "true", "matched"}:
        return "supported"
    if text in {"contradicted", "contradiction", "false", "conflict"}:
        return "contradicted"
    return "unresolved"


def _source_record(value: Any, *, doc_id: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        row = dict(value)
    else:
        row = {"canonical_source": str(value or "")}
    if doc_id and not row.get("doc_id"):
        row["doc_id"] = doc_id
    return row


def _source_records_from_row(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in row.get("source_facts") or []:
        records.append(_source_record(value, doc_id=str(value.get("doc_id") or "") if isinstance(value, Mapping) else ""))
    refs = row.get("source_refs") or row.get("evidence_refs") or row.get("resolved_evidence_refs") or []
    if isinstance(refs, (str, Mapping)):
        refs = [refs]
    for value in refs:
        records.append(_source_record(value))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        source = next((str(record.get(key) or "") for key in (
            "source_path", "source_relpath", "canonical_source", "source", "path"
        ) if str(record.get(key) or "").strip()), "")
        span = str(record.get("local_window") or record.get("source_span_text") or record.get("excerpt") or "")
        key = (source, span)
        if source and key not in seen:
            unique.append(record)
            seen.add(key)
    return unique


def payload_option_rows(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in payload.get("production_derived_option_evidence") or []:
        label = str(row.get("option_label") or row.get("label") or "")
        if label:
            rows[label] = dict(row)
    for label, row in (payload.get("option_verdicts") or {}).items():
        if isinstance(row, Mapping):
            rows[str(label)] = dict(row)
    return rows


def _query_terms(option_text: str) -> list[str]:
    terms = re.findall(r"(?:19|20)\d{2}|\d+(?:\.\d+)?%?|[\u4e00-\u9fff]{2,10}", str(option_text or ""))
    return list(dict.fromkeys(terms))[:24]


def materialize_sources(
    *,
    repo_root: Path,
    domain: str,
    option_text: str,
    source_records: Sequence[Mapping[str, Any]],
) -> tuple[SourceSpanRef, ...]:
    output: list[SourceSpanRef] = []
    for record in source_records:
        doc_id = str(record.get("doc_id") or record.get("source_doc_id") or "")
        source = next((str(record.get(key) or "") for key in (
            "source_path", "source_relpath", "canonical_source", "source", "path"
        ) if str(record.get(key) or "").strip()), "")
        path, anchor, span = extract_corpus_span(
            record,
            repo_root=repo_root,
            domain=domain,
            doc_id=doc_id,
            query_terms=_query_terms(option_text),
        )
        if path is None or not span:
            continue
        output.append(SourceSpanRef.build(
            source_doc_id=doc_id or Path(source.split("#", 1)[0]).stem,
            source_path=str(path),
            source_span=span,
            source_anchor=anchor,
            verified_local_extraction=True,
        ))
    unique: list[SourceSpanRef] = []
    seen: set[tuple[str, str]] = set()
    for row in output:
        key = (row.source_path, row.source_span_sha256)
        if key not in seen:
            unique.append(row)
            seen.add(key)
    return tuple(unique)


def _field_provenance(option_text: str, source: SourceSpanRef) -> tuple[DecisiveFieldProvenance, ...]:
    rows: list[DecisiveFieldProvenance] = []
    option = str(option_text or "")
    span = source.source_span
    years = re.findall(r"(?:19|20)\d{2}", option)
    numbers = re.findall(r"\d+(?:\.\d+)?%?", option)
    units = [token for token in ("每10股", "每股", "%", "亿元", "万元", "元", "天", "年") if token in option]
    negations = [token for token in ("不", "未", "不得", "无", "不包括") if token in option]
    for field_name, values, rule in (
        ("period", years, "option_year_exact_span_match_v1"),
        ("value", numbers, "option_number_exact_span_match_v1"),
        ("unit", units, "option_unit_exact_span_match_v1"),
        ("negation", negations, "option_negation_exact_span_match_v1"),
    ):
        for value in values:
            if value in span:
                rows.append(DecisiveFieldProvenance.locate(
                    field_name=field_name,
                    field_value=value,
                    source=source,
                    source_text=value,
                    extraction_rule=rule,
                ))
    metric_terms = [term for term in re.findall(r"[\u4e00-\u9fff]{3,12}", option) if term in span]
    if metric_terms:
        term = max(metric_terms, key=len)
        rows.append(DecisiveFieldProvenance.locate(
            field_name="action_or_metric",
            field_value=term,
            source=source,
            source_text=term,
            extraction_rule="longest_option_metric_phrase_in_span_v1",
        ))
    return tuple(rows)


def evaluate_from_current_production(*args: Any, **kwargs: Any) -> DomainQuestionResult:
    """Disabled historical Package AG adapter entry.

    Package AG-R1 requires every domain to decide truth from verification
    candidates through truth_adapter.py.  Reusing legacy option statuses,
    typed answers or trust flags is a production error.
    """
    raise RuntimeError(
        "historical Package AG status-wrapper is disabled; "
        "use the domain truth_adapter with EvidenceBundle verification candidates"
    )
