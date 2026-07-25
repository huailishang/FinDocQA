"""Verifiable truth-chain primitives for Package AG-R1.

Only retrieval candidates and question text enter this layer.  Previous option
statuses, answers, trust flags, API judgments and evaluator labels are not
accepted by any production function in this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from contracts import EvidenceCandidate, Question

CLOSED = {"supported", "contradicted"}
FORBIDDEN_DECISION_KEYS = {
    "status", "factual_status", "supported", "contradicted",
    "typed_supported_answer", "production_answer", "model_answer",
    "api_answer", "oracle_answer", "expected_answer",
    "historical_candidate_answer", "trusted_for_production",
    "local_compiler_status", "resolved_judgment", "model_judgment",
}


def compact(value: Any) -> str:
    return re.sub(r"[\s，。；：、（）()《》\[\]【】‘’“”\"']+", "", str(value or "")).replace("％", "%").lower()


def canonical_answer(labels: Iterable[str]) -> str:
    selected = set(labels)
    return "".join(label for label in "ABCD" if label in selected)


def resolve_candidate_path(repo_root: Path, source: str) -> Path | None:
    """Resolve candidate sources without assuming a Windows or WSL runtime.

    The source path as recorded by the retriever is always tried first.  Only
    when that form is unavailable do we add an equivalent Windows-drive or WSL
    mount form.  This keeps immutable source bytes stable across runtimes and
    avoids rewriting a valid ``/mnt/<drive>/...`` path into an unusable
    ``D:/...`` path while running under WSL.
    """
    raw = str(source or "").split("#", 1)[0].strip()
    if not raw:
        return None

    candidates: list[Path] = []

    def add(value: str | Path) -> None:
        path = value if isinstance(value, Path) else Path(value)
        if path not in candidates:
            candidates.append(path)

    # Preserve and test the exact source form before any cross-runtime mapping.
    original = Path(raw)
    add(original)

    # WSL mount -> Windows drive fallback.  This is deliberately second.
    mnt_match = re.match(r"^/mnt/([A-Za-z])/(.+)$", raw)
    if mnt_match:
        drive, remainder = mnt_match.groups()
        add(Path(f"{drive.upper()}:/{remainder}"))

    # Windows drive -> WSL mount fallback.  Under Windows the original drive
    # path resolves first; under WSL this gives access to the same source bytes.
    drive_match = re.match(r"^([A-Za-z]):[\\/](.+)$", raw)
    if drive_match:
        drive, remainder = drive_match.groups()
        add(Path(f"/mnt/{drive.lower()}/{remainder.replace(chr(92), '/') }"))

    # Relative sources retain the existing repo-root search contract.
    if not original.is_absolute() and not drive_match:
        add(repo_root / original)
        add(repo_root.parent / original)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _anchor_from_source(source: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in ("page_idx", "table_index", "row_index"):
        match = re.search(rf"(?:^|[&#]){key}=(\d+)", str(source or ""))
        if match:
            result[key] = int(match.group(1))
    return result


def candidate_anchor_valid(candidate: EvidenceCandidate) -> bool:
    encoded = _anchor_from_source(candidate.source)
    if not encoded:
        return True
    metadata = dict(candidate.metadata or {})
    return all(int(metadata.get(key, -1)) == value for key, value in encoded.items())


def _json_canonical_row(payload: Any, anchor: Mapping[str, int]) -> tuple[list[str], list[str], str] | None:
    """Read one anchored MinerU table row from the source payload itself."""
    required = {"page_idx", "table_index", "row_index"}
    if not required.issubset(anchor) or not isinstance(payload, list):
        return None
    page_idx, table_index, row_index = (anchor[key] for key in ("page_idx", "table_index", "row_index"))
    if page_idx < 0 or page_idx >= len(payload) or not isinstance(payload[page_idx], list):
        return None
    tables = [block for block in payload[page_idx] if isinstance(block, Mapping) and block.get("type") == "table"]
    if table_index < 0 or table_index >= len(tables):
        return None
    block = tables[table_index]
    content = block.get("content") or {}
    html = str(content.get("html") or "")
    # Use the production structured-table parser, but drive it from source
    # bytes loaded above.  This preserves rowspan/colspan and header semantics
    # while keeping candidate metadata outside the trust boundary.
    from evidence.structured_tables import (
        _headers_for_table,
        _normalise_columns,
        _row_text,
        parse_html_table_with_audit,
    )
    rows, cell_types, audit = parse_html_table_with_audit(html)
    if not audit.supported:
        return None
    rows = _normalise_columns(rows)
    cell_types = _normalise_columns(cell_types)
    headers, first_data_index, _, _ = _headers_for_table(rows, cell_types)
    data_rows = [row for row in rows[first_data_index:] if any(str(cell).strip() for cell in row)]
    if row_index < 0 or row_index >= len(data_rows):
        return None
    cells = [str(cell).strip() for cell in data_rows[row_index]]
    caption_raw = content.get("table_caption") or []
    caption = " ".join(str(value) for value in caption_raw) if isinstance(caption_raw, list) else str(caption_raw)
    return list(headers), cells, _row_text(caption, headers, cells)


def canonical_source_v2(repo_root: Path, candidate: EvidenceCandidate) -> dict[str, Any]:
    """Resolve a candidate against immutable source bytes, never its metadata."""
    path = resolve_candidate_path(repo_root, candidate.source)
    anchor = _anchor_from_source(candidate.source)
    result: dict[str, Any] = {
        "source_file_sha256": "",
        "canonical_anchor": dict(anchor),
        "canonical_record_sha256": "",
        "canonical_span_sha256": "",
        "candidate_span_sha256": hashlib.sha256(str(candidate.text or "").encode("utf-8")).hexdigest(),
        "declared_span_sha256": str(
            (candidate.metadata or {}).get("declared_candidate_span_sha256") or ""
        ),
        "declared_hash_matches_candidate": True,
        "candidate_matches_canonical_record": False,
        "anchor_exists_in_source": False,
        "anchor_valid": False,
        "lineage_doc_id_match": False,
        "canonical_span": "",
    }
    if result["declared_span_sha256"]:
        result["declared_hash_matches_candidate"] = (
            result["declared_span_sha256"] == result["candidate_span_sha256"]
        )
    if path is None or not candidate.text:
        return result
    result["source_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    doc_id = str(candidate.doc_id or "").lower()
    metadata_doc = str((candidate.metadata or {}).get("doc_id") or candidate.doc_id or "").lower()
    path_parts = {part.lower() for part in path.parts}
    result["lineage_doc_id_match"] = bool(doc_id and doc_id == metadata_doc and doc_id in path_parts)
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig", errors="strict"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return result
        canonical = _json_canonical_row(payload, anchor)
        if canonical is None:
            return result
        headers, cells, span = canonical
        record = {"headers": headers, "cells": cells, "normalized_row_text": span}
        result["anchor_exists_in_source"] = True
        result["anchor_valid"] = candidate_anchor_valid(candidate)
        result["canonical_span"] = span
        result["canonical_record_sha256"] = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        result["canonical_span_sha256"] = hashlib.sha256(span.encode("utf-8")).hexdigest()
        result["candidate_matches_canonical_record"] = compact(candidate.text) == compact(span)
        return result
    if suffix in {".md", ".txt", ".html"}:
        try:
            body = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return result
        # Exact raw substring is intentionally required; compact similarity is
        # insufficient for source-of-truth reconstruction.
        result["anchor_exists_in_source"] = not anchor
        result["anchor_valid"] = not anchor and candidate_anchor_valid(candidate)
        result["canonical_span"] = candidate.text if candidate.text in body else ""
        result["canonical_span_sha256"] = hashlib.sha256(result["canonical_span"].encode("utf-8")).hexdigest() if result["canonical_span"] else ""
        result["canonical_record_sha256"] = result["canonical_span_sha256"]
        result["candidate_matches_canonical_record"] = bool(result["canonical_span"])
        return result
    return result


def candidate_locally_reproduced(repo_root: Path, candidate: EvidenceCandidate) -> bool:
    row = canonical_source_v2(repo_root, candidate)
    return bool(
        row["anchor_exists_in_source"]
        and row["anchor_valid"]
        and row["lineage_doc_id_match"]
        and row["candidate_matches_canonical_record"]
        and row["declared_hash_matches_candidate"]
    )


@dataclass(frozen=True)
class TruthSource:
    doc_id: str
    source_path: str
    source_anchor: str
    source_span: str
    source_span_sha256: str
    anchor_valid: bool
    locally_reproduced: bool
    relevance_fields: tuple[str, ...]
    source_file_sha256: str = ""
    canonical_anchor: Mapping[str, int] = field(default_factory=dict)
    canonical_record_sha256: str = ""
    canonical_span_sha256: str = ""
    candidate_span_sha256: str = ""
    declared_span_sha256: str = ""
    declared_hash_matches_candidate: bool = True
    candidate_matches_canonical_record: bool = False
    anchor_exists_in_source: bool = False
    lineage_doc_id_match: bool = False

    @classmethod
    def from_candidate(
        cls,
        *,
        repo_root: Path,
        candidate: EvidenceCandidate,
        relevance_fields: Sequence[str],
    ) -> "TruthSource":
        path = resolve_candidate_path(repo_root, candidate.source)
        canonical = canonical_source_v2(repo_root, candidate)
        return cls(
            doc_id=str(candidate.doc_id),
            source_path=str(path or ""),
            source_anchor=str(candidate.source).split("#", 1)[1] if "#" in str(candidate.source) else "",
            source_span=str(candidate.text or ""),
            source_span_sha256=hashlib.sha256(str(candidate.text or "").encode("utf-8")).hexdigest(),
            anchor_valid=candidate_anchor_valid(candidate),
            locally_reproduced=candidate_locally_reproduced(repo_root, candidate),
            relevance_fields=tuple(relevance_fields),
            source_file_sha256=canonical["source_file_sha256"],
            canonical_anchor=canonical["canonical_anchor"],
            canonical_record_sha256=canonical["canonical_record_sha256"],
            canonical_span_sha256=canonical["canonical_span_sha256"],
            candidate_span_sha256=canonical["candidate_span_sha256"],
            declared_span_sha256=canonical["declared_span_sha256"],
            declared_hash_matches_candidate=canonical["declared_hash_matches_candidate"],
            candidate_matches_canonical_record=canonical["candidate_matches_canonical_record"],
            anchor_exists_in_source=canonical["anchor_exists_in_source"],
            lineage_doc_id_match=canonical["lineage_doc_id_match"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FieldProvenance:
    field_name: str
    field_value: Any
    source_path: str
    source_doc_id: str
    source_text: str
    start_char: int
    end_char: int
    source_text_sha256: str
    extraction_rule: str
    valid: bool

    @classmethod
    def locate(
        cls,
        *,
        field_name: str,
        field_value: Any,
        source: TruthSource,
        source_text: str,
        extraction_rule: str,
    ) -> "FieldProvenance":
        needle = str(source_text or "")
        start = source.source_span.find(needle) if needle else -1
        end = start + len(needle) if start >= 0 else -1
        valid = bool(
            start >= 0
            and source.source_span[start:end] == needle
            and source.anchor_valid
            and source.locally_reproduced
        )
        return cls(
            field_name=field_name,
            field_value=field_value,
            source_path=source.source_path,
            source_doc_id=source.doc_id,
            source_text=needle,
            start_char=start,
            end_char=end,
            source_text_sha256=hashlib.sha256(needle.encode("utf-8")).hexdigest() if needle else "",
            extraction_rule=extraction_rule,
            valid=valid,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TruthOptionResult:
    option: str
    claim: Mapping[str, Any]
    status: str
    sources: tuple[TruthSource, ...] = ()
    provenance: tuple[FieldProvenance, ...] = ()
    binding: Mapping[str, str] = field(default_factory=dict)
    rule_steps: tuple[Mapping[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "option": self.option,
            "claim": dict(self.claim),
            "status": self.status,
            "sources": [row.to_dict() for row in self.sources],
            "provenance": [row.to_dict() for row in self.provenance],
            "binding": dict(self.binding),
            "rule_steps": [dict(row) for row in self.rule_steps],
            "blockers": list(self.blockers),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TruthQuestionResult:
    qid: str
    domain: str
    task_type: str
    lane: str
    implementation_status: str
    option_results: Mapping[str, TruthOptionResult]
    production_answer: str
    all_options_closed: bool
    blockers: tuple[str, ...]
    capability: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "domain": self.domain,
            "task_type": self.task_type,
            "lane": self.lane,
            "implementation_status": self.implementation_status,
            "option_results": {key: value.to_dict() for key, value in self.option_results.items()},
            "option_statuses": {key: value.status for key, value in self.option_results.items()},
            "production_answer": self.production_answer,
            "all_options_closed": self.all_options_closed,
            "blockers": list(self.blockers),
            "capability": self.capability,
        }


def result_from_options(
    *,
    question: Question,
    option_results: Mapping[str, TruthOptionResult],
    task_type: str,
    lane: str,
    implementation_status: str,
    capability: str,
    extra_blockers: Sequence[str] = (),
) -> TruthQuestionResult:
    # A semantic adapter may find a matching fragment before canonical source
    # validation runs.  Such a verdict is not closed: downgrade it at the
    # shared production boundary instead of merely blanking the final answer.
    validated_options: dict[str, TruthOptionResult] = {}
    for label, row in option_results.items():
        invalid_closed = row.status in CLOSED and (
            not row.sources
            or not row.provenance
            or not all(item.valid for item in row.provenance)
            or not all(source.anchor_valid and source.locally_reproduced for source in row.sources)
        )
        if invalid_closed:
            validated_options[label] = replace(
                row,
                status="unresolved",
                blockers=tuple(dict.fromkeys((*row.blockers, "canonical_source_v2_failed"))),
                reason="canonical_source_v2_failed",
            )
        else:
            validated_options[label] = row
    option_results = validated_options
    all_closed = all(row.status in CLOSED for row in option_results.values())
    supported = canonical_answer(label for label, row in option_results.items() if row.status == "supported")
    blockers = list(extra_blockers)
    if not all_closed:
        blockers.append("option_slots_not_closed")
    answer = supported
    answer_format = question.answer_contract.answer_format if question.answer_contract else question.answer_format
    if answer_format in {"mcq", "tf"} and len(answer) != 1:
        blockers.append("unique_answer_contract_not_closed")
        answer = ""
    if answer_format == "multi" and not answer:
        blockers.append("multi_answer_empty")
    for label, row in option_results.items():
        if row.status in CLOSED:
            if not row.sources:
                blockers.append(f"{label}:closed_without_source")
            if not row.provenance or not all(item.valid for item in row.provenance):
                blockers.append(f"{label}:closed_without_valid_provenance")
            if not all(source.anchor_valid and source.locally_reproduced for source in row.sources):
                blockers.append(f"{label}:source_validation_failed")
    blockers = list(dict.fromkeys(blockers))
    if blockers and any(
        item.endswith("closed_without_source")
        or item.endswith("closed_without_valid_provenance")
        or item.endswith("source_validation_failed")
        for item in blockers
    ):
        answer = ""
    return TruthQuestionResult(
        qid=question.qid,
        domain=question.domain,
        task_type=task_type,
        lane=lane,
        implementation_status=implementation_status,
        option_results=option_results,
        production_answer=answer,
        all_options_closed=all_closed,
        blockers=tuple(blockers),
        capability=capability,
    )


def candidates_for_docs(candidates: Sequence[EvidenceCandidate], doc_ids: Sequence[str]) -> tuple[EvidenceCandidate, ...]:
    allowed = {str(value) for value in doc_ids}
    return tuple(candidate for candidate in candidates if str(candidate.doc_id) in allowed)


def exact_fragment(text: str, patterns: Sequence[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return match.group(0)
    return ""


def first_relevant_candidate(
    *,
    repo_root: Path,
    candidates: Sequence[EvidenceCandidate],
    required_terms: Sequence[str],
    forbidden_terms: Sequence[str] = (),
) -> tuple[EvidenceCandidate | None, tuple[str, ...]]:
    required = [compact(value) for value in required_terms if compact(value)]
    forbidden = [compact(value) for value in forbidden_terms if compact(value)]
    ranked: list[tuple[int, EvidenceCandidate, tuple[str, ...]]] = []
    for candidate in candidates:
        body = compact(candidate.text)
        matched = tuple(term for term, raw in zip(required, required_terms) if term in body)
        if required and len(matched) < len(required):
            continue
        if any(term in body for term in forbidden):
            continue
        if not candidate_locally_reproduced(repo_root, candidate):
            continue
        ranked.append((len(matched), candidate, tuple(str(value) for value in required_terms)))
    if not ranked:
        return None, ()
    ranked.sort(key=lambda row: (row[0], float(row[1].score or 0), len(row[1].text)), reverse=True)
    return ranked[0][1], ranked[0][2]


def provenance_for_fragments(
    *,
    source: TruthSource,
    fields: Mapping[str, tuple[Any, str, str]],
) -> tuple[FieldProvenance, ...]:
    rows = []
    for name, (value, source_text, rule) in fields.items():
        row = FieldProvenance.locate(
            field_name=name,
            field_value=value,
            source=source,
            source_text=source_text,
            extraction_rule=rule,
        )
        if row.valid:
            rows.append(row)
    return tuple(rows)


def payload_retrieval_candidates_only(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Sanitize legacy payload into non-decision retrieval hints.

    AG-R1 runner uses EvidenceBundle candidates directly.  This helper exists
    for dependency auditing and refuses to retain decision-bearing fields.
    """
    def cleanse(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): cleanse(item)
                for key, item in value.items()
                if str(key) not in FORBIDDEN_DECISION_KEYS
                and str(key) in {
                    "source_refs", "evidence_refs", "resolved_evidence_refs",
                    "canonical_source", "canonical_sources", "source_facts",
                    "source_path", "source_relpath", "local_window", "doc_id",
                    "source", "path", "metadata", "option_label",
                    "production_derived_option_evidence", "option_verdicts",
                }
            }
        if isinstance(value, (list, tuple)):
            return [cleanse(item) for item in value]
        return value
    result = cleanse(payload)
    serialized = json.dumps(result, ensure_ascii=False)
    assert not any(f'"{key}"' in serialized for key in FORBIDDEN_DECISION_KEYS)
    return result
