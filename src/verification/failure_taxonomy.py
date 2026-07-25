"""Deterministic failure taxonomy for offline recovery planning.

BB-P0-12 deliberately stops at classification.  This module never performs
retrieval, provider calls, retries, answer mutation, or production recovery.
It converts structured failure observations into one auditable failure class
so downstream policy can choose a bounded action without conflating evidence,
lineage, binding, model, provider, budget, and integrity failures.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class FailureClass(str, Enum):
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    LINEAGE_LOST = "LINEAGE_LOST"
    BINDING_FAILED = "BINDING_FAILED"
    CALCULATION_BINDING_FAILED = "CALCULATION_BINDING_FAILED"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    EMPTY_VISIBLE_OUTPUT = "EMPTY_VISIBLE_OUTPUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    ANSWER_CONTRACT_FAILED = "ANSWER_CONTRACT_FAILED"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"
    RUNTIME_INTEGRITY_FAILED = "RUNTIME_INTEGRITY_FAILED"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class SafetySeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class FailureTraits:
    stage: str
    retryable: bool
    retrieval_related: bool
    provider_related: bool
    safety_severity: SafetySeverity


_TRAITS: dict[FailureClass, FailureTraits] = {
    FailureClass.MISSING_EVIDENCE: FailureTraits("retrieval", True, True, False, SafetySeverity.MEDIUM),
    FailureClass.LINEAGE_LOST: FailureTraits("evidence_lineage", True, False, False, SafetySeverity.HIGH),
    FailureClass.BINDING_FAILED: FailureTraits("verification_binding", True, False, False, SafetySeverity.HIGH),
    FailureClass.CALCULATION_BINDING_FAILED: FailureTraits("calculation_verification", True, False, False, SafetySeverity.HIGH),
    FailureClass.MODEL_OUTPUT_INVALID: FailureTraits("model_output_parse", True, False, False, SafetySeverity.MEDIUM),
    FailureClass.EMPTY_VISIBLE_OUTPUT: FailureTraits("visible_output", True, False, False, SafetySeverity.HIGH),
    FailureClass.PROVIDER_ERROR: FailureTraits("provider", False, False, True, SafetySeverity.HIGH),
    FailureClass.ANSWER_CONTRACT_FAILED: FailureTraits("answer_contract", False, False, False, SafetySeverity.HIGH),
    FailureClass.BUDGET_BLOCKED: FailureTraits("budget_guard", False, False, False, SafetySeverity.CRITICAL),
    FailureClass.RUNTIME_INTEGRITY_FAILED: FailureTraits("runtime_integrity", False, False, False, SafetySeverity.CRITICAL),
    FailureClass.UNKNOWN_FAILURE: FailureTraits("unknown", False, False, False, SafetySeverity.CRITICAL),
}


_DEFAULT_REASONS: dict[FailureClass, str] = {
    FailureClass.MISSING_EVIDENCE: "required evidence is absent or the target page was not recovered",
    FailureClass.LINEAGE_LOST: "evidence exists but canonical source lineage is not auditable",
    FailureClass.BINDING_FAILED: "available evidence could not be bound to the decisive claim",
    FailureClass.CALCULATION_BINDING_FAILED: "calculation result could not be deterministically executed or bound",
    FailureClass.MODEL_OUTPUT_INVALID: "non-empty model output could not be parsed into a valid structured result",
    FailureClass.EMPTY_VISIBLE_OUTPUT: "provider completed with usage but visible model content is empty",
    FailureClass.PROVIDER_ERROR: "provider or LLM client returned a terminal error",
    FailureClass.ANSWER_CONTRACT_FAILED: "candidate answer violates the answer contract",
    FailureClass.BUDGET_BLOCKED: "execution was stopped by the token or cost budget guard",
    FailureClass.RUNTIME_INTEGRITY_FAILED: "runtime integrity checks failed and execution must fail closed",
    FailureClass.UNKNOWN_FAILURE: "failure signal does not match a safe deterministic recovery class",
}


@dataclass(frozen=True)
class FailureRecord:
    failure_class: FailureClass
    stage: str
    retryable: bool
    retrieval_related: bool
    provider_related: bool
    safety_severity: SafetySeverity
    evidence_refs: tuple[str, ...]
    reason: str
    matched_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failure_class"] = self.failure_class.value
        payload["safety_severity"] = self.safety_severity.value
        payload["evidence_refs"] = list(self.evidence_refs)
        payload["matched_signals"] = list(self.matched_signals)
        return payload


def _truthy(signal: Mapping[str, Any], *keys: str) -> bool:
    return any(signal.get(key) is True for key in keys)


def _text(signal: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = signal.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _code_text(signal: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "failure_code",
        "failure_classification",
        "blocking_reason",
        "stop_reason",
        "error_class",
        "result",
    ):
        value = signal.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(values).upper()


def _error_text(signal: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("raw_error", "error", "message", "provider_error_message"):
        value = signal.get(key)
        if value is not None:
            values.append(str(value))
    return " ".join(values).upper()


def _usage_tokens(signal: Mapping[str, Any]) -> int:
    for key in ("total_tokens", "actual_total_tokens", "token_usage", "reported_tokens"):
        value = signal.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    usage = signal.get("usage")
    if isinstance(usage, Mapping):
        for key in ("total_tokens", "total"):
            try:
                if usage.get(key) is not None:
                    return int(usage[key])
            except (TypeError, ValueError):
                continue
    return 0


def _visible_output(signal: Mapping[str, Any]) -> tuple[bool, str]:
    for key in ("visible_output", "visible_content", "response_content", "model_content", "content"):
        if key in signal:
            value = signal.get(key)
            return True, "" if value is None else str(value)
    return False, ""


def _refs(signal: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in (
        "evidence_refs",
        "used_doc_ids",
        "solver_raw_used_doc_ids",
        "retrieved_doc_ids",
        "source_refs",
    ):
        value = signal.get(key)
        if isinstance(value, str):
            if value.strip():
                refs.append(value.strip())
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            refs.extend(str(item).strip() for item in value if str(item).strip())
    return tuple(dict.fromkeys(refs))


def _provider_error(signal: Mapping[str, Any], codes: str, errors: str) -> tuple[bool, tuple[str, ...]]:
    matched: list[str] = []
    if _truthy(signal, "provider_error", "llm_error", "client_unavailable"):
        matched.append("provider_error_flag")
    status = signal.get("http_status")
    try:
        if status is not None and int(status) >= 500:
            matched.append(f"http_status:{int(status)}")
    except (TypeError, ValueError):
        pass
    if any(token in errors for token in ("HTTP 5", "TIMEOUT", "LLM_CLIENT_UNAVAILABLE", "CLIENT UNAVAILABLE")):
        matched.append("provider_error_text")
    if any(token in codes for token in ("PROVIDER_ERROR", "PROVIDER_HTTP_", "PROVIDER_INSTABILITY", "LLM_CLIENT_UNAVAILABLE")):
        matched.append("provider_error_code")
    return bool(matched), tuple(matched)


def _lineage_lost(signal: Mapping[str, Any], codes: str) -> tuple[bool, tuple[str, ...]]:
    matched: list[str] = []
    if _truthy(signal, "lineage_lost", "lineage_failed"):
        matched.append("lineage_flag")
    if any(token in codes for token in ("LINEAGE_LOST", "USED_DOC_LINEAGE_UNKNOWN", "LINEAGE_NOT_AUDITABLE")):
        matched.append("lineage_code")
    raw_refs = signal.get("solver_raw_used_doc_ids") or []
    canonical_refs = signal.get("used_doc_ids") or []
    if raw_refs and not canonical_refs:
        matched.append("raw_refs_without_canonical_lineage")
    if str(signal.get("used_docs_source") or "").lower() == "unknown" and raw_refs:
        matched.append("used_docs_source_unknown")
    return bool(matched), tuple(matched)


def _binding_failed(signal: Mapping[str, Any], codes: str) -> tuple[bool, tuple[str, ...]]:
    matched: list[str] = []
    if _truthy(signal, "binding_failed"):
        matched.append("binding_failed_flag")
    if signal.get("binding_auditable") is False or signal.get("all_slot_bindings_valid") is False:
        matched.append("binding_not_auditable")
    if any(token in codes for token in ("BINDING_FAILED", "BINDING_NOT_AUDITABLE", "SLOT_BINDING_MISSING")):
        matched.append("binding_code")
    return bool(matched), tuple(matched)


def _calculation_binding_failed(signal: Mapping[str, Any], codes: str) -> tuple[bool, tuple[str, ...]]:
    matched: list[str] = []
    if _truthy(signal, "calculation_binding_failed", "formula_execution_failed"):
        matched.append("calculation_binding_flag")
    if signal.get("formula_execution_valid") is False:
        matched.append("formula_execution_invalid")
    if signal.get("freeform_all_slot_results_match") is False and signal.get("freeform_all_slot_bindings_valid") is False:
        matched.append("slot_result_binding_failed")
    if any(token in codes for token in (
        "CALCULATION_BINDING",
        "FORMULA_EXECUTION_FAILED",
        "SLOT_RESULT_BINDING_FAILED",
    )):
        matched.append("calculation_binding_code")
    return bool(matched), tuple(matched)


def _missing_evidence(signal: Mapping[str, Any], codes: str) -> tuple[bool, tuple[str, ...]]:
    matched: list[str] = []
    if _truthy(signal, "missing_evidence", "missing_target_page"):
        matched.append("missing_evidence_flag")
    if signal.get("evidence_available") is False:
        matched.append("evidence_unavailable")
    evidence_count = signal.get("evidence_count")
    try:
        if evidence_count is not None and int(evidence_count) == 0:
            matched.append("zero_evidence_count")
    except (TypeError, ValueError):
        pass
    if any(token in codes for token in ("MISSING_EVIDENCE", "MISSING_TARGET_PAGE", "TARGET_PAGE_NOT_FOUND")):
        matched.append("missing_evidence_code")
    return bool(matched), tuple(matched)


def _build_failure_record(
    failure_class: FailureClass,
    signal: Mapping[str, Any],
    *,
    matched: Sequence[str] = (),
    reason: str = "",
) -> FailureRecord:
    traits = _TRAITS[failure_class]
    return FailureRecord(
        failure_class=failure_class,
        stage=_text(signal, "stage") or traits.stage,
        retryable=traits.retryable,
        retrieval_related=traits.retrieval_related,
        provider_related=traits.provider_related,
        safety_severity=traits.safety_severity,
        evidence_refs=_refs(signal),
        reason=reason or _text(signal, "reason", "raw_error", "error", "blocking_reason", "stop_reason") or _DEFAULT_REASONS[failure_class],
        matched_signals=tuple(matched),
    )


def observe_failure_records(signal: Mapping[str, Any]) -> tuple[FailureRecord, ...]:
    """Return every failure class directly supported by structured signals.

    Unlike :func:`classify_failure_signal`, this function does not collapse a
    composite observation to one class.  It is used by P14A arbitration to
    preserve upstream causes and downstream symptoms simultaneously.
    """
    codes = _code_text(signal)
    errors = _error_text(signal)
    has_visible_field, visible_output = _visible_output(signal)
    tokens = _usage_tokens(signal)
    observed: list[FailureRecord] = []

    runtime_issues = signal.get("runtime_integrity_issues") or []
    if _truthy(signal, "runtime_integrity_failed") or runtime_issues or "RUNTIME_INTEGRITY" in codes:
        observed.append(_build_failure_record(
            FailureClass.RUNTIME_INTEGRITY_FAILED,
            signal,
            matched=("runtime_integrity_failed",),
        ))

    if _truthy(signal, "budget_blocked", "token_budget_blocked") or any(
        token in codes for token in ("BUDGET_BLOCKED", "TOKEN_BUDGET", "BUDGET_EXHAUSTED", "TOKEN_CAP")
    ):
        observed.append(_build_failure_record(
            FailureClass.BUDGET_BLOCKED,
            signal,
            matched=("budget_guard",),
        ))

    provider_failed, provider_signals = _provider_error(signal, codes, errors)
    if provider_failed:
        observed.append(_build_failure_record(
            FailureClass.PROVIDER_ERROR,
            signal,
            matched=provider_signals,
        ))

    if _truthy(signal, "empty_visible_output") or (
        has_visible_field
        and not visible_output.strip()
        and bool(signal.get("completed") or str(signal.get("provider_status") or "").upper() == "COMPLETED")
        and (tokens > 0 or signal.get("usage_positive") is True)
    ):
        observed.append(_build_failure_record(
            FailureClass.EMPTY_VISIBLE_OUTPUT,
            signal,
            matched=("completed_with_usage_and_empty_visible_output",),
        ))

    calc_failed, calc_signals = _calculation_binding_failed(signal, codes)
    if calc_failed:
        observed.append(_build_failure_record(
            FailureClass.CALCULATION_BINDING_FAILED,
            signal,
            matched=calc_signals,
        ))

    lineage_failed, lineage_signals = _lineage_lost(signal, codes)
    if lineage_failed:
        observed.append(_build_failure_record(
            FailureClass.LINEAGE_LOST,
            signal,
            matched=lineage_signals,
        ))

    binding_failed, binding_signals = _binding_failed(signal, codes)
    if binding_failed:
        observed.append(_build_failure_record(
            FailureClass.BINDING_FAILED,
            signal,
            matched=binding_signals,
        ))

    missing_evidence, missing_signals = _missing_evidence(signal, codes)
    if missing_evidence:
        observed.append(_build_failure_record(
            FailureClass.MISSING_EVIDENCE,
            signal,
            matched=missing_signals,
        ))

    if _truthy(signal, "answer_contract_failed") or signal.get("answer_contract_valid") is False or "ANSWER_CONTRACT" in codes:
        observed.append(_build_failure_record(
            FailureClass.ANSWER_CONTRACT_FAILED,
            signal,
            matched=("answer_contract_failed",),
        ))

    if _truthy(signal, "model_output_invalid", "parse_failed") or any(
        token in codes for token in ("MODEL_OUTPUT_INVALID", "MODEL_PARSE_FAILED", "VISIBLE_OUTPUT_PARSE_FAILED")
    ):
        observed.append(_build_failure_record(
            FailureClass.MODEL_OUTPUT_INVALID,
            signal,
            matched=("model_output_invalid",),
        ))

    if not observed:
        observed.append(_build_failure_record(FailureClass.UNKNOWN_FAILURE, signal))

    # Preserve detector order but do not emit the same class twice.
    unique: list[FailureRecord] = []
    seen: set[FailureClass] = set()
    for record in observed:
        if record.failure_class in seen:
            continue
        seen.add(record.failure_class)
        unique.append(record)
    return tuple(unique)


def classify_failure_signal(signal: Mapping[str, Any]) -> FailureRecord:
    """Classify one structured failure observation with fail-closed precedence.

    Specific non-retrieval failures intentionally outrank generic evidence
    absence.  This prevents empty output, provider faults, lineage loss, and
    binding failures from being misrouted into broad retrieval.
    """
    codes = _code_text(signal)
    errors = _error_text(signal)
    has_visible_field, visible_output = _visible_output(signal)
    tokens = _usage_tokens(signal)

    failure_class = FailureClass.UNKNOWN_FAILURE
    matched: tuple[str, ...] = ()

    runtime_issues = signal.get("runtime_integrity_issues") or []
    if _truthy(signal, "runtime_integrity_failed") or runtime_issues or "RUNTIME_INTEGRITY" in codes:
        failure_class = FailureClass.RUNTIME_INTEGRITY_FAILED
        matched = ("runtime_integrity_failed",)
    elif _truthy(signal, "budget_blocked", "token_budget_blocked") or any(
        token in codes for token in ("BUDGET_BLOCKED", "TOKEN_BUDGET", "BUDGET_EXHAUSTED", "TOKEN_CAP")
    ):
        failure_class = FailureClass.BUDGET_BLOCKED
        matched = ("budget_guard",)
    else:
        provider_failed, provider_signals = _provider_error(signal, codes, errors)
        if provider_failed:
            failure_class = FailureClass.PROVIDER_ERROR
            matched = provider_signals
        elif _truthy(signal, "empty_visible_output") or (
            has_visible_field
            and not visible_output.strip()
            and bool(signal.get("completed") or str(signal.get("provider_status") or "").upper() == "COMPLETED")
            and (tokens > 0 or signal.get("usage_positive") is True)
        ):
            failure_class = FailureClass.EMPTY_VISIBLE_OUTPUT
            matched = ("completed_with_usage_and_empty_visible_output",)
        else:
            calc_failed, calc_signals = _calculation_binding_failed(signal, codes)
            lineage_failed, lineage_signals = _lineage_lost(signal, codes)
            binding_failed, binding_signals = _binding_failed(signal, codes)
            missing_evidence, missing_signals = _missing_evidence(signal, codes)

            if calc_failed:
                failure_class = FailureClass.CALCULATION_BINDING_FAILED
                matched = calc_signals
            elif _truthy(signal, "answer_contract_failed") or signal.get("answer_contract_valid") is False or "ANSWER_CONTRACT" in codes:
                failure_class = FailureClass.ANSWER_CONTRACT_FAILED
                matched = ("answer_contract_failed",)
            elif _truthy(signal, "model_output_invalid", "parse_failed") or any(
                token in codes for token in ("MODEL_OUTPUT_INVALID", "MODEL_PARSE_FAILED", "VISIBLE_OUTPUT_PARSE_FAILED")
            ):
                failure_class = FailureClass.MODEL_OUTPUT_INVALID
                matched = ("model_output_invalid",)
            elif lineage_failed:
                failure_class = FailureClass.LINEAGE_LOST
                matched = lineage_signals
            elif binding_failed:
                failure_class = FailureClass.BINDING_FAILED
                matched = binding_signals
            elif missing_evidence:
                failure_class = FailureClass.MISSING_EVIDENCE
                matched = missing_signals

    traits = _TRAITS[failure_class]
    stage = _text(signal, "stage") or traits.stage
    reason = _text(signal, "reason", "raw_error", "error", "blocking_reason", "stop_reason") or _DEFAULT_REASONS[failure_class]
    return FailureRecord(
        failure_class=failure_class,
        stage=stage,
        retryable=traits.retryable,
        retrieval_related=traits.retrieval_related,
        provider_related=traits.provider_related,
        safety_severity=traits.safety_severity,
        evidence_refs=_refs(signal),
        reason=reason,
        matched_signals=matched,
    )


def taxonomy_matrix() -> list[dict[str, Any]]:
    """Return the stable class metadata used by audits and evaluator review."""
    rows: list[dict[str, Any]] = []
    for failure_class in FailureClass:
        traits = _TRAITS[failure_class]
        rows.append({
            "failure_class": failure_class.value,
            "stage": traits.stage,
            "retryable": traits.retryable,
            "retrieval_related": traits.retrieval_related,
            "provider_related": traits.provider_related,
            "safety_severity": traits.safety_severity.value,
            "default_reason": _DEFAULT_REASONS[failure_class],
        })
    return rows


__all__ = [
    "FailureClass",
    "SafetySeverity",
    "FailureRecord",
    "observe_failure_records",
    "classify_failure_signal",
    "taxonomy_matrix",
]
