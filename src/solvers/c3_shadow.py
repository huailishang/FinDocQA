"""Default-off, fail-closed shadow observer for the explicit C3 pipeline.

The observer is deliberately not a production solver.  It reads one strictly
versioned, caller-supplied metadata record from the sole solver-visible evidence
candidate, executes the already isolated ``ExplicitC3Pipeline`` when every
required authority fact is present, and returns bounded audit metadata only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from calculation import (
    C3InputAssemblyInput,
    ExecutionGateFact,
    FormulaSourceRef,
    MaterialFormulaExtractor,
    SafeFormulaCompiler,
    SemanticBindingCandidate,
    SemanticBindingRequest,
)
from contracts import (
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    QuestionLabel,
    question_answer_slot_count,
)
from solvers.c3_deterministic import ExplicitC3Pipeline


_INPUT_KEY = "c3_shadow_input_v1"
_INPUT_SCHEMA = "c3-shadow-input/v1"
_OBSERVATION_SCHEMA = "c3-shadow-observation/v1"
_SUPPORTED_UNITS = frozenset({"%", "％", "ratio", "元", "万", "万元", "亿", "亿元"})
_PAGE_SOURCE_RE = re.compile(r"page(?:[_=/:-]?)(\d+)", re.IGNORECASE)


class C3ShadowState(str, Enum):
    DISABLED = "DISABLED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    EXECUTED = "EXECUTED"
    ERROR = "ERROR"


def _freeze_public_value(value: Any) -> Any:
    """Create a detached recursively immutable snapshot for public records."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_public_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_public_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze_public_value(item) for item in value), key=repr))
    return value


def _thaw_public_value(value: Any) -> Any:
    """Return a detached JSON-compatible copy of an immutable public snapshot."""
    if isinstance(value, Mapping):
        return {key: _thaw_public_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_public_value(item) for item in value]
    return value


def _sanitize_input_reason_codes(reasons: Sequence[str]) -> tuple[str, ...]:
    """Collapse parser details into bounded categories without names or text."""
    categories: list[str] = []
    for raw in reasons:
        reason = str(raw or "")
        if reason in {
            "c3_shadow_formula_count_not_one",
            "c3_shadow_formula_unparseable",
            "c3_shadow_formula_variable_records_mismatch",
            "c3_shadow_semantic_candidate_cardinality_invalid",
        }:
            categories.append(reason)
        elif reason.startswith("c3_shadow_schema"):
            categories.append("c3_shadow_input_schema_invalid")
        elif "fingerprint" in reason:
            categories.append("c3_shadow_input_fingerprint_invalid")
        elif reason.startswith("question_match") or reason.startswith("question_formula_match"):
            categories.append("c3_shadow_question_match_record_invalid")
        elif reason.startswith("semantic_"):
            categories.append("c3_shadow_semantic_records_invalid")
        else:
            categories.append("c3_shadow_input_invalid")
    return tuple(dict.fromkeys(categories)) or ("c3_shadow_input_invalid",)


def _sanitize_pipeline_block_reasons(metadata: Mapping[str, Any], *, has_answer: bool) -> tuple[str, ...]:
    """Map dynamic pipeline details to a small static observation vocabulary."""
    if has_answer:
        return ("c3_shadow_executed_contract_incomplete",)
    answer_source = str(metadata.get("answer_source") or "")
    if answer_source == "c3_deterministic_execution_not_ready":
        return ("c3_shadow_execution_gate_not_ready",)
    if answer_source == "c3_input_assembly_not_ready":
        return ("c3_shadow_input_assembly_not_ready",)
    return ("c3_shadow_explicit_pipeline_blocked",)


def _safe_numeric_text(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return str(number)


def _sanitize_trace(items: Any) -> tuple[Mapping[str, Any], ...]:
    """Keep bounded numeric execution facts without formula symbols or free text."""
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return ()
    allowed_ops = {
        "add", "subtract", "multiply", "divide", "power",
        "min", "max", "abs", "identity",
    }
    rows: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        op = str(item.get("op") or "")
        raw_step = str(item.get("step") or "")
        row: dict[str, Any] = {
            "step": raw_step if re.fullmatch(r"#[1-9]\d*", raw_step) else "unknown",
            "op": op if op in allowed_ops else "unknown",
        }
        resolved = item.get("resolved_args")
        if isinstance(resolved, Sequence) and not isinstance(resolved, (str, bytes)):
            row["resolved_args"] = tuple(
                _safe_numeric_text(value) or "redacted" for value in resolved
            )
        if item.get("result") is not None:
            row["result"] = _safe_numeric_text(item.get("result")) or "redacted"
        rows.append(_freeze_public_value(row))
    return tuple(rows)


@dataclass(frozen=True)
class C3QuestionFormulaMatchAuthority:
    """Explicit authority record for the question-to-formula match fact."""

    authority_type: str
    rule_id: str
    passed: bool | None
    reasons: Sequence[str]
    question_fingerprint: str
    candidate_fingerprint: str
    document_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))


@dataclass(frozen=True)
class C3ShadowInputRecord:
    """Strictly parsed inputs used to construct ``C3InputAssemblyInput``."""

    schema_version: str
    question_fingerprint: str
    candidate_fingerprint: str
    semantic_requests: Mapping[str, SemanticBindingRequest]
    semantic_candidates: Mapping[str, Sequence[SemanticBindingCandidate]]
    question_formula_match: C3QuestionFormulaMatchAuthority

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_requests",
            MappingProxyType(dict(self.semantic_requests)),
        )
        object.__setattr__(
            self,
            "semantic_candidates",
            MappingProxyType(
                {name: tuple(rows) for name, rows in self.semantic_candidates.items()}
            ),
        )


@dataclass(frozen=True)
class C3ShadowObservation:
    """Bounded, JSON-compatible shadow result with no production authority."""

    state: C3ShadowState
    reason_codes: Sequence[str] = field(default_factory=tuple)
    applicable: bool = False
    pipeline_invoked: bool = False
    candidate_count: int = 0
    question_fingerprint: str = ""
    candidate_fingerprint: str = ""
    match_rule_id: str = ""
    would_execute: bool = False
    shadow_answer: str = ""
    computation_status: str = ""
    trace: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    source_refs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    legacy_execution_invoked: bool = False
    provider_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_type: str = ""
    schema_version: str = _OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason_codes", tuple(str(item) for item in self.reason_codes)
        )
        object.__setattr__(
            self,
            "trace",
            tuple(_freeze_public_value(item) for item in self.trace),
        )
        object.__setattr__(
            self,
            "source_refs",
            tuple(_freeze_public_value(item) for item in self.source_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "reason_codes": list(self.reason_codes),
            "applicable": self.applicable,
            "pipeline_invoked": self.pipeline_invoked,
            "candidate_count": self.candidate_count,
            "question_fingerprint": self.question_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "match_rule_id": self.match_rule_id,
            "would_execute": self.would_execute,
            "shadow_answer": self.shadow_answer,
            "computation_status": self.computation_status,
            "trace": [_thaw_public_value(item) for item in self.trace],
            "source_refs": [_thaw_public_value(item) for item in self.source_refs],
            "legacy_execution_invoked": self.legacy_execution_invoked,
            "provider_call_count": self.provider_call_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "error_type": self.error_type,
        }


class C3ShadowInputError(ValueError):
    """Expected fail-closed input error carrying stable reason codes."""

    def __init__(self, *reason_codes: str) -> None:
        cleaned = tuple(dict.fromkeys(str(code) for code in reason_codes if str(code)))
        self.reason_codes = cleaned or ("c3_shadow_input_invalid",)
        super().__init__(",".join(self.reason_codes))


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _stable_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _page_number(candidate: EvidenceCandidate) -> int | None:
    for key in ("page_number", "page", "page_index"):
        value = candidate.metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value + 1 if key == "page_index" else value
        if str(value or "").isdigit():
            number = int(str(value))
            return number + 1 if key == "page_index" else number
    match = _PAGE_SOURCE_RE.search(str(candidate.source or ""))
    return int(match.group(1)) if match else None


def question_fingerprint(question: Question) -> str:
    """Fingerprint question semantics without qid or expected-answer leakage."""
    payload = {
        "text": _normalized_text(question.text),
        "answer_format": _normalized_text(question.answer_format),
        "options": [
            [str(key), _normalized_text(value)]
            for key, value in sorted(question.options.items(), key=lambda item: str(item[0]))
        ],
        "doc_ids": [str(value) for value in question.doc_ids if str(value)],
        "candidate_doc_ids": [
            str(value) for value in question.candidate_doc_ids if str(value)
        ],
    }
    return _stable_digest(payload)


def candidate_fingerprint(candidate: EvidenceCandidate) -> str:
    """Fingerprint candidate identity and lineage without exposing its text."""
    page_number = _page_number(candidate)
    payload = {
        "domain": _normalized_text(candidate.domain),
        "doc_id": _normalized_text(candidate.doc_id),
        "source": _normalized_text(candidate.source),
        "page_number": page_number,
        "block_id": _normalized_text(candidate.metadata.get("block_id")),
        "text_sha256": sha256(str(candidate.text or "").encode("utf-8")).hexdigest(),
    }
    return _stable_digest(payload)


def _require_mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise C3ShadowInputError(reason)
    return value


def _require_exact_keys(
    mapping: Mapping[str, Any],
    *,
    required: frozenset[str],
    reason_prefix: str,
) -> None:
    keys = {str(key) for key in mapping}
    missing = sorted(required - keys)
    unknown = sorted(keys - required)
    reasons = [f"{reason_prefix}_missing:{key}" for key in missing]
    reasons.extend(f"{reason_prefix}_unknown:{key}" for key in unknown)
    if reasons:
        raise C3ShadowInputError(*reasons)


def _require_nonempty(value: Any, reason: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise C3ShadowInputError(reason)
    return text


def _require_sequence(value: Any, reason: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise C3ShadowInputError(reason)
    return value


def _parse_source_ref(value: Any, *, candidate: EvidenceCandidate) -> FormulaSourceRef:
    mapping = _require_mapping(value, "semantic_source_ref_invalid")
    allowed = frozenset({"doc_id", "page_number", "source", "block_id", "excerpt"})
    required = frozenset({"doc_id", "page_number", "source"})
    keys = {str(key) for key in mapping}
    missing = sorted(required - keys)
    unknown = sorted(keys - allowed)
    if missing or unknown:
        raise C3ShadowInputError(
            *(f"semantic_source_ref_missing:{key}" for key in missing),
            *(f"semantic_source_ref_unknown:{key}" for key in unknown),
        )
    doc_id = _require_nonempty(mapping.get("doc_id"), "semantic_source_doc_missing")
    source = _require_nonempty(mapping.get("source"), "semantic_source_missing")
    page_number = mapping.get("page_number")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number <= 0:
        raise C3ShadowInputError("semantic_source_page_invalid")
    if doc_id != str(candidate.doc_id):
        raise C3ShadowInputError("semantic_source_document_mismatch")
    return FormulaSourceRef(
        doc_id=doc_id,
        page_number=page_number,
        source=source,
        block_id=str(mapping.get("block_id") or ""),
        excerpt=str(mapping.get("excerpt") or "")[:500],
    )


def _parse_semantic_request(name: str, value: Any, *, candidate: EvidenceCandidate) -> SemanticBindingRequest:
    mapping = _require_mapping(value, f"semantic_request_invalid:{name}")
    required = frozenset({"name", "metric", "entity", "period", "unit", "document_id"})
    _require_exact_keys(mapping, required=required, reason_prefix=f"semantic_request:{name}")
    request_name = _require_nonempty(mapping.get("name"), f"semantic_request_name_missing:{name}")
    if request_name != name:
        raise C3ShadowInputError(f"semantic_request_name_mismatch:{name}")
    unit = _require_nonempty(mapping.get("unit"), f"semantic_request_unit_missing:{name}")
    if unit not in _SUPPORTED_UNITS:
        raise C3ShadowInputError(f"semantic_request_unit_unsupported:{name}")
    document_id = _require_nonempty(
        mapping.get("document_id"), f"semantic_request_document_missing:{name}"
    )
    if document_id != str(candidate.doc_id):
        raise C3ShadowInputError(f"semantic_request_document_mismatch:{name}")
    return SemanticBindingRequest(
        name=request_name,
        metric=_require_nonempty(mapping.get("metric"), f"semantic_request_metric_missing:{name}"),
        entity=_require_nonempty(mapping.get("entity"), f"semantic_request_entity_missing:{name}"),
        period=_require_nonempty(mapping.get("period"), f"semantic_request_period_missing:{name}"),
        unit=unit,
        document_id=document_id,
    )


def _parse_semantic_candidate(
    name: str,
    value: Any,
    *,
    candidate: EvidenceCandidate,
) -> SemanticBindingCandidate:
    mapping = _require_mapping(value, f"semantic_candidate_invalid:{name}")
    required = frozenset(
        {"value", "metric", "entity", "period", "unit", "document_id", "source_ref"}
    )
    _require_exact_keys(mapping, required=required, reason_prefix=f"semantic_candidate:{name}")
    unit = _require_nonempty(mapping.get("unit"), f"semantic_candidate_unit_missing:{name}")
    if unit not in _SUPPORTED_UNITS:
        raise C3ShadowInputError(f"semantic_candidate_unit_unsupported:{name}")
    document_id = _require_nonempty(
        mapping.get("document_id"), f"semantic_candidate_document_missing:{name}"
    )
    if document_id != str(candidate.doc_id):
        raise C3ShadowInputError(f"semantic_candidate_document_mismatch:{name}")
    raw_value = mapping.get("value")
    if isinstance(raw_value, bool) or raw_value is None or not str(raw_value).strip():
        raise C3ShadowInputError(f"semantic_candidate_value_invalid:{name}")
    return SemanticBindingCandidate(
        value=raw_value,
        metric=_require_nonempty(mapping.get("metric"), f"semantic_candidate_metric_missing:{name}"),
        entity=_require_nonempty(mapping.get("entity"), f"semantic_candidate_entity_missing:{name}"),
        period=_require_nonempty(mapping.get("period"), f"semantic_candidate_period_missing:{name}"),
        unit=unit,
        document_id=document_id,
        source_ref=_parse_source_ref(mapping.get("source_ref"), candidate=candidate),
    )


def _parse_match_authority(
    value: Any,
    *,
    candidate: EvidenceCandidate,
    expected_question_fingerprint: str,
    expected_candidate_fingerprint: str,
    approved_rule_ids: frozenset[str],
) -> C3QuestionFormulaMatchAuthority:
    mapping = _require_mapping(value, "question_formula_match_invalid")
    required = frozenset(
        {
            "authority_type",
            "rule_id",
            "passed",
            "reasons",
            "question_fingerprint",
            "candidate_fingerprint",
            "document_id",
        }
    )
    _require_exact_keys(mapping, required=required, reason_prefix="question_formula_match")
    authority_type = _require_nonempty(
        mapping.get("authority_type"), "question_match_authority_missing"
    )
    if authority_type != "deterministic_rule":
        raise C3ShadowInputError("question_match_authority_not_deterministic")
    rule_id = _require_nonempty(mapping.get("rule_id"), "question_match_rule_missing")
    if rule_id not in approved_rule_ids:
        raise C3ShadowInputError("question_match_rule_unapproved")
    passed = mapping.get("passed")
    if passed is not None and not isinstance(passed, bool):
        raise C3ShadowInputError("question_match_passed_invalid")
    reasons = tuple(
        str(item)
        for item in _require_sequence(mapping.get("reasons"), "question_match_reasons_invalid")
        if str(item)
    )
    supplied_question_fingerprint = _require_nonempty(
        mapping.get("question_fingerprint"), "question_match_question_fingerprint_missing"
    )
    supplied_candidate_fingerprint = _require_nonempty(
        mapping.get("candidate_fingerprint"), "question_match_candidate_fingerprint_missing"
    )
    if supplied_question_fingerprint != expected_question_fingerprint:
        raise C3ShadowInputError("question_match_question_fingerprint_stale")
    if supplied_candidate_fingerprint != expected_candidate_fingerprint:
        raise C3ShadowInputError("question_match_candidate_fingerprint_stale")
    document_id = _require_nonempty(
        mapping.get("document_id"), "question_match_document_missing"
    )
    if document_id != str(candidate.doc_id):
        raise C3ShadowInputError("question_match_document_mismatch")
    return C3QuestionFormulaMatchAuthority(
        authority_type=authority_type,
        rule_id=rule_id,
        passed=passed,
        reasons=reasons,
        question_fingerprint=supplied_question_fingerprint,
        candidate_fingerprint=supplied_candidate_fingerprint,
        document_id=document_id,
    )


def parse_shadow_input_record(
    raw: Any,
    *,
    bundle: EvidenceBundle,
    candidate: EvidenceCandidate,
    approved_rule_ids: frozenset[str],
) -> C3ShadowInputRecord:
    """Parse the sole allowed metadata schema without semantic inference."""
    mapping = _require_mapping(raw, "c3_shadow_input_missing_or_invalid")
    required = frozenset(
        {
            "schema_version",
            "question_fingerprint",
            "candidate_fingerprint",
            "semantic_requests",
            "semantic_candidates",
            "question_formula_match",
        }
    )
    _require_exact_keys(mapping, required=required, reason_prefix="c3_shadow_input")
    schema_version = _require_nonempty(mapping.get("schema_version"), "c3_shadow_schema_missing")
    if schema_version != _INPUT_SCHEMA:
        raise C3ShadowInputError("c3_shadow_schema_unsupported")

    expected_question_fingerprint = question_fingerprint(bundle.question)
    expected_candidate_fingerprint = candidate_fingerprint(candidate)
    supplied_question_fingerprint = _require_nonempty(
        mapping.get("question_fingerprint"), "c3_shadow_question_fingerprint_missing"
    )
    supplied_candidate_fingerprint = _require_nonempty(
        mapping.get("candidate_fingerprint"), "c3_shadow_candidate_fingerprint_missing"
    )
    if supplied_question_fingerprint != expected_question_fingerprint:
        raise C3ShadowInputError("c3_shadow_question_fingerprint_stale")
    if supplied_candidate_fingerprint != expected_candidate_fingerprint:
        raise C3ShadowInputError("c3_shadow_candidate_fingerprint_stale")

    raw_requests = _require_mapping(mapping.get("semantic_requests"), "semantic_requests_invalid")
    raw_candidates = _require_mapping(mapping.get("semantic_candidates"), "semantic_candidates_invalid")
    if any(not isinstance(name, str) for name in raw_requests) or any(
        not isinstance(name, str) for name in raw_candidates
    ):
        raise C3ShadowInputError("semantic_variable_name_not_string")
    request_names = set(raw_requests)
    candidate_names = set(raw_candidates)
    if not request_names:
        raise C3ShadowInputError("semantic_requests_empty")
    if request_names != candidate_names:
        raise C3ShadowInputError("semantic_record_name_sets_mismatch")

    requests: dict[str, SemanticBindingRequest] = {}
    candidates: dict[str, tuple[SemanticBindingCandidate, ...]] = {}
    for name in sorted(request_names):
        if not name.strip():
            raise C3ShadowInputError("semantic_variable_name_empty")
        requests[name] = _parse_semantic_request(
            name, raw_requests[name], candidate=candidate
        )
        raw_rows = _require_sequence(
            raw_candidates[name], f"semantic_candidates_sequence_invalid:{name}"
        )
        candidates[name] = tuple(
            _parse_semantic_candidate(name, row, candidate=candidate) for row in raw_rows
        )

    try:
        formulas = MaterialFormulaExtractor().extract_from_candidate(candidate)
    except Exception as exc:
        raise C3ShadowInputError("c3_shadow_formula_unparseable") from exc
    if len(formulas) != 1:
        raise C3ShadowInputError("c3_shadow_formula_count_not_one")
    try:
        formula_names = set(
            SafeFormulaCompiler.referenced_symbols(formulas[0].normalized_expression)
        )
    except (TypeError, ValueError, SyntaxError) as exc:
        raise C3ShadowInputError("c3_shadow_formula_unparseable") from exc
    if formula_names != request_names or formula_names != candidate_names:
        raise C3ShadowInputError("c3_shadow_formula_variable_records_mismatch")
    if any(len(rows) != 1 for rows in candidates.values()):
        raise C3ShadowInputError("c3_shadow_semantic_candidate_cardinality_invalid")

    match = _parse_match_authority(
        mapping.get("question_formula_match"),
        candidate=candidate,
        expected_question_fingerprint=expected_question_fingerprint,
        expected_candidate_fingerprint=expected_candidate_fingerprint,
        approved_rule_ids=approved_rule_ids,
    )
    return C3ShadowInputRecord(
        schema_version=schema_version,
        question_fingerprint=supplied_question_fingerprint,
        candidate_fingerprint=supplied_candidate_fingerprint,
        semantic_requests=requests,
        semantic_candidates=candidates,
        question_formula_match=match,
    )


_RESULT_MISSING = object()


def _result_non_negative_int(metadata: Mapping[str, Any], key: str) -> int:
    value = metadata.get(key, _RESULT_MISSING)
    if value is _RESULT_MISSING:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError("invalid_result_counter")
    return value


def _result_exact_bool(metadata: Mapping[str, Any], key: str) -> bool:
    value = metadata.get(key, _RESULT_MISSING)
    if value is _RESULT_MISSING:
        return False
    if type(value) is not bool:
        raise ValueError("invalid_result_boolean")
    return value


def _result_mapping_sequence(
    metadata: Mapping[str, Any],
    key: str,
    *,
    fallback_key: str = "",
) -> tuple[Mapping[str, Any], ...]:
    if key in metadata:
        value = metadata[key]
    elif fallback_key and fallback_key in metadata:
        value = metadata[fallback_key]
    else:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("invalid_result_container")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError("invalid_result_container_item")
    return tuple(value)


def _post_call_metadata_error(record: C3ShadowInputRecord) -> C3ShadowObservation:
    return C3ShadowObservation(
        state=C3ShadowState.ERROR,
        reason_codes=("c3_shadow_result_metadata_invalid",),
        applicable=True,
        pipeline_invoked=True,
        candidate_count=1,
        question_fingerprint=record.question_fingerprint,
        candidate_fingerprint=record.candidate_fingerprint,
        match_rule_id=record.question_formula_match.rule_id,
        error_type="ResultMetadataError",
    )


def _observe_pipeline_result(
    result: Any,
    *,
    candidate: EvidenceCandidate,
    record: C3ShadowInputRecord,
) -> C3ShadowObservation:
    raw_metadata = result.metadata
    if raw_metadata is None:
        metadata: dict[str, Any] = {}
    elif isinstance(raw_metadata, Mapping):
        metadata = dict(raw_metadata)
    else:
        raise ValueError("invalid_result_metadata")

    trace_items = _result_mapping_sequence(metadata, "result_trace")
    source_items = _result_mapping_sequence(
        metadata, "source_refs", fallback_key="source_lineage"
    )
    trace = _sanitize_trace(trace_items)
    source_refs = tuple(
        {
            "doc_id": str(item.get("doc_id") or ""),
            "page_number": item.get("page_number"),
            "source": str(item.get("source") or ""),
        }
        for item in source_items
    )
    provider_calls = _result_non_negative_int(metadata, "provider_call_count")
    prompt_tokens = _result_non_negative_int(metadata, "prompt_tokens")
    completion_tokens = _result_non_negative_int(metadata, "completion_tokens")
    total_tokens = _result_non_negative_int(metadata, "total_tokens")
    legacy_invoked = _result_exact_bool(metadata, "legacy_execution_invoked")

    raw_computation_status = metadata.get("computation_status", "")
    if not isinstance(raw_computation_status, str):
        raise ValueError("invalid_computation_status")
    computation_status = (
        raw_computation_status
        if raw_computation_status in {"completed", "blocked", "failed"}
        else "unknown"
    )
    candidate_page = _page_number(candidate)
    valid_lineage = bool(source_refs) and all(
        str(item.get("doc_id") or "") == str(candidate.doc_id)
        and str(item.get("source") or "") == str(candidate.source)
        and item.get("page_number") == candidate_page
        and isinstance(item.get("page_number"), int)
        and not isinstance(item.get("page_number"), bool)
        and int(item.get("page_number")) > 0
        for item in source_refs
    )
    safe_answer = _safe_numeric_text(result.answer)
    executed = bool(
        safe_answer is not None
        and computation_status == "completed"
        and trace
        and valid_lineage
        and provider_calls == 0
        and prompt_tokens == 0
        and completion_tokens == 0
        and total_tokens == 0
        and not legacy_invoked
    )
    if provider_calls or prompt_tokens or completion_tokens or total_tokens or legacy_invoked:
        return C3ShadowObservation(
            state=C3ShadowState.ERROR,
            reason_codes=("c3_shadow_side_effect_contract_violated",),
            applicable=True,
            pipeline_invoked=True,
            candidate_count=1,
            question_fingerprint=record.question_fingerprint,
            candidate_fingerprint=record.candidate_fingerprint,
            match_rule_id=record.question_formula_match.rule_id,
            computation_status=computation_status,
            trace=(),
            source_refs=(),
            legacy_execution_invoked=legacy_invoked,
            provider_call_count=provider_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    if executed:
        return C3ShadowObservation(
            state=C3ShadowState.EXECUTED,
            applicable=True,
            pipeline_invoked=True,
            candidate_count=1,
            question_fingerprint=record.question_fingerprint,
            candidate_fingerprint=record.candidate_fingerprint,
            match_rule_id=record.question_formula_match.rule_id,
            would_execute=True,
            shadow_answer=safe_answer or "",
            computation_status=computation_status,
            trace=trace,
            source_refs=source_refs,
        )

    reasons = _sanitize_pipeline_block_reasons(
        metadata, has_answer=bool(result.answer)
    )
    return C3ShadowObservation(
        state=C3ShadowState.BLOCKED,
        reason_codes=reasons,
        applicable=True,
        pipeline_invoked=True,
        candidate_count=1,
        question_fingerprint=record.question_fingerprint,
        candidate_fingerprint=record.candidate_fingerprint,
        match_rule_id=record.question_formula_match.rule_id,
        computation_status=(
            computation_status if computation_status != "unknown" else "blocked"
        ),
        trace=(),
        source_refs=(),
    )


class C3ShadowObserver:
    """Observe explicit C3 behavior without obtaining production answer authority."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        approved_match_rule_ids: Sequence[str] = (),
        pipeline: ExplicitC3Pipeline | None = None,
    ) -> None:
        self.enabled = enabled is True
        raw_rule_ids: Sequence[str] = (
            approved_match_rule_ids
            if isinstance(approved_match_rule_ids, (list, tuple, set, frozenset))
            else ()
        )
        self.approved_match_rule_ids = frozenset(
            value.strip()
            for value in raw_rule_ids
            if isinstance(value, str) and value.strip()
        )
        self._pipeline = pipeline or ExplicitC3Pipeline()

    @staticmethod
    def _not_applicable_reason(bundle: EvidenceBundle) -> str:
        labels = set(bundle.classification.labels)
        question = bundle.question
        if QuestionLabel.CALCULATION not in labels:
            return "question_not_calculation"
        if question.answer_format != "freeform":
            return "answer_format_not_freeform"
        if question.options:
            return "options_not_supported"
        if question_answer_slot_count(question) != 1:
            return "answer_cardinality_not_one"
        return ""

    @staticmethod
    def _lineage_reason(candidate: EvidenceCandidate) -> str:
        if not str(candidate.doc_id or "").strip():
            return "candidate_document_missing"
        if not str(candidate.source or "").strip():
            return "candidate_source_missing"
        page = _page_number(candidate)
        if page is None or page <= 0:
            return "candidate_page_missing"
        return ""

    def observe(self, bundle: EvidenceBundle) -> C3ShadowObservation:
        """Contain every shadow-only failure before it reaches the main chain."""
        try:
            return self._observe(bundle)
        except Exception as exc:  # final observer boundary; never disturb production flow
            try:
                candidate_count = len(tuple(bundle.candidates or ()))
            except Exception:
                candidate_count = 0
            return C3ShadowObservation(
                state=C3ShadowState.ERROR,
                reason_codes=("c3_shadow_observer_error",),
                candidate_count=candidate_count,
                error_type=type(exc).__name__,
            )

    def _observe(self, bundle: EvidenceBundle) -> C3ShadowObservation:
        candidate_count = len(tuple(bundle.candidates or ()))
        if not self.enabled:
            return C3ShadowObservation(
                state=C3ShadowState.DISABLED,
                reason_codes=("c3_shadow_disabled",),
                candidate_count=candidate_count,
            )

        applicability_reason = self._not_applicable_reason(bundle)
        if applicability_reason:
            return C3ShadowObservation(
                state=C3ShadowState.NOT_APPLICABLE,
                reason_codes=(applicability_reason,),
                candidate_count=candidate_count,
            )

        current_question_fingerprint = question_fingerprint(bundle.question)
        if candidate_count != 1:
            reason = (
                "candidate_scope_zero"
                if candidate_count == 0
                else "candidate_scope_not_exactly_one"
            )
            return C3ShadowObservation(
                state=C3ShadowState.BLOCKED,
                reason_codes=(reason,),
                applicable=True,
                candidate_count=candidate_count,
                question_fingerprint=current_question_fingerprint,
            )

        candidate = tuple(bundle.candidates)[0]
        current_candidate_fingerprint = candidate_fingerprint(candidate)
        lineage_reason = self._lineage_reason(candidate)
        if lineage_reason:
            return C3ShadowObservation(
                state=C3ShadowState.BLOCKED,
                reason_codes=(lineage_reason,),
                applicable=True,
                candidate_count=1,
                question_fingerprint=current_question_fingerprint,
                candidate_fingerprint=current_candidate_fingerprint,
            )

        try:
            record = parse_shadow_input_record(
                candidate.metadata.get(_INPUT_KEY),
                bundle=bundle,
                candidate=candidate,
                approved_rule_ids=self.approved_match_rule_ids,
            )
        except C3ShadowInputError as exc:
            return C3ShadowObservation(
                state=C3ShadowState.BLOCKED,
                reason_codes=_sanitize_input_reason_codes(exc.reason_codes),
                applicable=True,
                candidate_count=1,
                question_fingerprint=current_question_fingerprint,
                candidate_fingerprint=current_candidate_fingerprint,
            )
        except Exception as exc:  # defensive containment around metadata parsing
            return C3ShadowObservation(
                state=C3ShadowState.ERROR,
                reason_codes=("c3_shadow_input_unexpected_error",),
                applicable=True,
                candidate_count=1,
                question_fingerprint=current_question_fingerprint,
                candidate_fingerprint=current_candidate_fingerprint,
                error_type=type(exc).__name__,
            )

        assembly_input = C3InputAssemblyInput(
            candidate=candidate,
            semantic_requests=record.semantic_requests,
            semantic_candidates=record.semantic_candidates,
            question_formula_match=ExecutionGateFact(
                record.question_formula_match.passed,
                tuple(record.question_formula_match.reasons),
            ),
        )
        try:
            result = self._pipeline.solve(bundle, assembly_input)
        except Exception as exc:  # shadow failure must never escape into the main chain
            return C3ShadowObservation(
                state=C3ShadowState.ERROR,
                reason_codes=("c3_shadow_pipeline_error",),
                applicable=True,
                pipeline_invoked=True,
                candidate_count=1,
                question_fingerprint=record.question_fingerprint,
                candidate_fingerprint=record.candidate_fingerprint,
                match_rule_id=record.question_formula_match.rule_id,
                error_type=type(exc).__name__,
            )

        try:
            return _observe_pipeline_result(
                result, candidate=candidate, record=record
            )
        except Exception:
            return _post_call_metadata_error(record)



__all__ = [
    "C3QuestionFormulaMatchAuthority",
    "C3ShadowInputError",
    "C3ShadowInputRecord",
    "C3ShadowObservation",
    "C3ShadowObserver",
    "C3ShadowState",
    "candidate_fingerprint",
    "parse_shadow_input_record",
    "question_fingerprint",
]
