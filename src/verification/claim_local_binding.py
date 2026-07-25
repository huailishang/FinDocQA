"""Source-local option binding and multi-slot verifier-scope calibration.

The legacy typed certifier remains the first-line semantic checker.  This module
adds two production-safe capabilities around it:

* choose verifier evidence from validated source refs or solver-used documents,
  never from the whole candidate scope without an explicit fail-closed reason;
* calibrate one option against atoms that close in one explainable source
  location (or one same-document ordered series), so naked years and short
  numeric tokens cannot become evidence by themselves.

All logic is qid-agnostic and provider-free.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from contracts import EvidenceBundle, EvidenceCandidate, SolverResult, get_verification_candidates
from .typed_claim_binding import (
    ClaimAtoms,
    TypedClaimAtoms,
    certify_option_claim,
    certify_typed_option_claim,
    extract_claim_atoms,
    extract_typed_claim_atoms,
    source_windows,
)


FINAL_BINDING_STATUSES = {
    "supported",
    "contradicted",
    "unresolved_missing_atoms",
    "unresolved_adapter_unavailable",
    "lineage_invalid",
    "unsafe_model_answer",
}

_UNSAFE_ANSWER_SOURCES = {
    "unsupported_guess",
    "fallback",
    "fallback_answer",
    "default",
    "dry_run",
    "placeholder",
}

_GENERIC_NUMERIC_TOKEN_RE = re.compile(
    r"^(?:\d{1,2}|(?:19|20)\d{2}\s*年?|\d+(?:\.\d+)?\s*(?:%|％|个百分点))$"
)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?(?!\d)")
_VALUE_RE = re.compile(
    r"(?<!\d)(?P<number>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>个百分点|%|％|亿元|万元|元|倍|个月|个工作日|工作日|天|年)?(?!\d)"
)
_DOC_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_SOURCE_DOC_RE = re.compile(
    r"/(?:financial_contracts|financial_reports|insurance|regulatory|research)/([^/#]+)(?:/|#|$)",
    re.IGNORECASE,
)

# Domain vocabulary used only to establish non-numeric semantic anchors.  It is
# deliberately broad and contains no question ids, answers, or fixture values.
_SUBJECT_ANCHORS = (
    "发行人",
    "标的公司",
    "上市公司",
    "本集团",
    "本公司",
    "公司",
    "管理团队",
    "核心人员",
    "员工",
    "客户",
)
_METRIC_OR_CLAUSE_ANCHORS = (
    "超额业绩奖励",
    "奖励总额",
    "奖励对象",
    "交易作价",
    "专项资管计划",
    "现金形式",
    "净利润",
    "资产负债率",
    "流动比率",
    "速动比率",
    "不良贷款率",
    "拨备覆盖率",
    "贷款拨备率",
    "资本充足率",
    "营业收入",
    "境外收入",
    "市场份额",
    "责任免除",
    "等待期",
    "违约责任",
)
_OPERATOR_ANCHORS = (
    "不超过",
    "不低于",
    "不少于",
    "高于",
    "低于",
    "下降",
    "上升",
    "增长",
    "减少",
    "分别为",
    "用于",
    "直接发放",
    "购买",
    "=",
    "-",
    "*",
)


@dataclass(frozen=True)
class OptionBindingScope:
    """Auditable evidence scope selected before option certification."""

    option_binding_scope_doc_ids: tuple[str, ...]
    option_binding_scope_source: str
    option_binding_scope_source_refs: tuple[str, ...]
    option_binding_scope_expanded: bool
    option_binding_scope_expansion_reason: str
    option_binding_outside_solver_docs: tuple[str, ...]
    solver_used_doc_ids: tuple[str, ...]
    invalid_source_refs: tuple[str, ...]
    lineage_valid: bool
    fail_closed: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: list(value) if isinstance(value, tuple) else value for key, value in payload.items()}


def _stable(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _normalise_source(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def source_doc_id(value: Any) -> str:
    """Extract a document id from a corpus path or return a doc-level ref."""
    normalised = _normalise_source(value)
    match = _SOURCE_DOC_RE.search(normalised)
    if match:
        return match.group(1)
    if "/" not in normalised and "#" not in normalised and normalised:
        return normalised
    return ""


def _candidate_context(candidate: EvidenceCandidate) -> str:
    return "\n\n".join(
        part.strip()
        for part in (candidate.before_text, candidate.text, candidate.after_text)
        if str(part or "").strip()
    )


def _candidate_source(candidate: EvidenceCandidate) -> str:
    return _normalise_source(candidate.source)


def _extract_explicit_source_refs(metadata: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in (
        "used_source_refs",
        "source_refs",
        "solver_source_refs",
        "evidence_refs",
        "resolved_evidence_refs",
    ):
        value = metadata.get(key)
        values = [value] if isinstance(value, (str, Mapping)) else value
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            continue
        for item in values:
            if isinstance(item, Mapping):
                ref = (
                    item.get("canonical_ref")
                    or item.get("resolved_path")
                    or item.get("evidence_ref")
                    or item.get("doc_id")
                    or item.get("source")
                )
                if ref:
                    refs.append(str(ref))
            elif item:
                refs.append(str(item))
    return _stable(refs)


def _candidate_matches_ref(candidate: EvidenceCandidate, ref: str) -> bool:
    normalised_ref = _normalise_source(ref)
    doc_ref = source_doc_id(normalised_ref)
    if doc_ref and doc_ref == str(candidate.doc_id):
        return True
    source = _candidate_source(candidate)
    if not source or not normalised_ref:
        return False
    return source == normalised_ref or source.endswith("/" + normalised_ref.lstrip("/"))


def select_option_binding_scope(
    bundle: EvidenceBundle,
    result: SolverResult,
) -> tuple[OptionBindingScope, tuple[EvidenceCandidate, ...]]:
    """Select verifier candidates by explicit precedence and preserve failures.

    Scope expansion is observable and always marked fail-closed.  It is useful
    for diagnostics/corrective retrieval, never automatic answer authority.
    """
    metadata = dict(result.metadata or {})
    candidates = tuple(get_verification_candidates(bundle))
    used_raw = metadata.get("used_doc_ids")
    used_docs = tuple(
        _stable(used_raw)
        if isinstance(used_raw, Sequence) and not isinstance(used_raw, (str, bytes, bytearray))
        else ()
    )
    explicit_refs = tuple(_extract_explicit_source_refs(metadata))

    if explicit_refs:
        matched = tuple(
            candidate
            for candidate in candidates
            if any(_candidate_matches_ref(candidate, ref) for ref in explicit_refs)
        )
        invalid_refs = tuple(
            ref
            for ref in explicit_refs
            if not any(_candidate_matches_ref(candidate, ref) for candidate in candidates)
        )
        if matched and not invalid_refs:
            docs = tuple(_stable(candidate.doc_id for candidate in matched))
            outside = tuple(doc for doc in docs if doc not in set(used_docs))
            scope = OptionBindingScope(
                option_binding_scope_doc_ids=docs,
                option_binding_scope_source="validated_solver_source_refs",
                option_binding_scope_source_refs=explicit_refs,
                option_binding_scope_expanded=False,
                option_binding_scope_expansion_reason="",
                option_binding_outside_solver_docs=outside,
                solver_used_doc_ids=used_docs,
                invalid_source_refs=(),
                lineage_valid=not outside,
                fail_closed=bool(outside),
            )
            return scope, matched

    if used_docs:
        matched = tuple(candidate for candidate in candidates if str(candidate.doc_id) in set(used_docs))
        if matched:
            scope = OptionBindingScope(
                option_binding_scope_doc_ids=tuple(_stable(candidate.doc_id for candidate in matched)),
                option_binding_scope_source=(
                    "solver_used_doc_ids_after_unresolved_source_refs"
                    if explicit_refs else "solver_used_doc_ids"
                ),
                option_binding_scope_source_refs=explicit_refs,
                option_binding_scope_expanded=False,
                option_binding_scope_expansion_reason="",
                option_binding_outside_solver_docs=(),
                solver_used_doc_ids=used_docs,
                invalid_source_refs=tuple(
                    ref
                    for ref in explicit_refs
                    if not any(_candidate_matches_ref(candidate, ref) for candidate in candidates)
                ),
                lineage_valid=True,
                fail_closed=False,
            )
            return scope, matched

    authorised_raw = (bundle.metadata or {}).get("authorized_typed_sidecar_doc_ids")
    authorised_docs = tuple(
        _stable(authorised_raw)
        if isinstance(authorised_raw, Sequence)
        and not isinstance(authorised_raw, (str, bytes, bytearray))
        else ()
    )
    if authorised_docs:
        matched = tuple(candidate for candidate in candidates if str(candidate.doc_id) in set(authorised_docs))
        if matched:
            docs = tuple(_stable(candidate.doc_id for candidate in matched))
            outside = tuple(doc for doc in docs if doc not in set(used_docs))
            scope = OptionBindingScope(
                option_binding_scope_doc_ids=docs,
                option_binding_scope_source="authorized_domain_typed_sidecar",
                option_binding_scope_source_refs=explicit_refs,
                option_binding_scope_expanded=False,
                option_binding_scope_expansion_reason="",
                option_binding_outside_solver_docs=outside,
                solver_used_doc_ids=used_docs,
                invalid_source_refs=explicit_refs,
                lineage_valid=True,
                fail_closed=False,
            )
            return scope, matched

    reason = (
        "validated_source_refs_and_solver_used_docs_not_resolvable"
        if explicit_refs and used_docs
        else "validated_source_refs_not_resolvable"
        if explicit_refs
        else "solver_used_docs_not_resolvable"
        if used_docs
        else "solver_lineage_unavailable"
    )
    docs = tuple(_stable(candidate.doc_id for candidate in candidates))
    scope = OptionBindingScope(
        option_binding_scope_doc_ids=docs,
        option_binding_scope_source="candidate_scope_expansion",
        option_binding_scope_source_refs=explicit_refs,
        option_binding_scope_expanded=True,
        option_binding_scope_expansion_reason=reason,
        option_binding_outside_solver_docs=tuple(doc for doc in docs if doc not in set(used_docs)),
        solver_used_doc_ids=used_docs,
        invalid_source_refs=explicit_refs,
        lineage_valid=False,
        fail_closed=True,
    )
    return scope, candidates


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff%％=+*<>-]+", "", str(value or "")).replace("％", "%").lower()


def _lexical_bigrams(value: Any) -> set[str]:
    compact = re.sub(r"[0-9.%％=+*<>-]+", "", _compact(value))
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1)) if len(compact[index : index + 2]) == 2}


def _lexical_coverage(option_text: str, window: str) -> float:
    required = _lexical_bigrams(option_text)
    if len(required) < 3:
        return 0.0
    actual = _lexical_bigrams(window)
    return len(required & actual) / len(required)


def _anchors(text: str, vocabulary: Sequence[str]) -> list[str]:
    compact = _compact(text)
    return [anchor for anchor in vocabulary if _compact(anchor) in compact]


def _periods(text: str) -> list[str]:
    return _stable(_YEAR_RE.findall(str(text or "")))


def _doc_year(candidate: EvidenceCandidate) -> int | None:
    values = [int(value) for value in _DOC_YEAR_RE.findall(str(candidate.doc_id))]
    return max(values) if values else None


def _value_tokens(text: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for match in _VALUE_RE.finditer(str(text or "")):
        number = match.group("number")
        unit = (match.group("unit") or "").replace("％", "%")
        if re.fullmatch(r"(?:19|20)\d{2}", number) and unit in {"", "年"}:
            continue
        # Unitless integers are usually structural noise.  Unitless decimals
        # remain valid for ratios such as 1.83, but still require lexical atoms.
        if not unit and "." not in number:
            continue
        tokens.append({"number": float(number), "raw": number + unit, "unit": unit})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for token in tokens:
        key = (float(token["number"]), str(token["unit"]))
        if key not in seen:
            seen.add(key)
            unique.append(token)
    return unique


def _number_present(token: Mapping[str, Any], window: str) -> bool:
    number = str(token.get("number"))
    if number.endswith(".0"):
        number = number[:-2]
    unit = str(token.get("unit") or "")
    if unit == "%":
        pattern = rf"(?<!\d){re.escape(number)}\s*[%％](?!\d)"
    elif unit == "个百分点":
        pattern = rf"(?<!\d){re.escape(number)}\s*个百分点(?!\d)"
    elif unit:
        pattern = rf"(?<!\d){re.escape(number)}\s*{re.escape(unit)}(?!\d)"
    else:
        pattern = rf"(?<![\d.]){re.escape(number)}(?![\d.])"
    return re.search(pattern, str(window or "")) is not None


def _period_match(option_text: str, window: str, candidate: EvidenceCandidate) -> tuple[bool, list[str], str]:
    required = _periods(option_text)
    if not required:
        return True, [], "not_required"
    found = set(_periods(window))
    missing = [period for period in required if period not in found]
    if not missing:
        return True, [], "explicit_period"
    report_year = _doc_year(candidate)
    if (
        report_year is not None
        and len(missing) == 1
        and int(missing[0]) == report_year - 1
        and any(marker in window for marker in ("较上年末", "比上年", "同比"))
    ):
        return True, [], "prior_year_inferred_from_report_and_relative_period"
    return False, missing, "missing_period"


def _operator_match(option_text: str, window: str) -> tuple[bool, list[str]]:
    required = _anchors(option_text, _OPERATOR_ANCHORS)
    if not required:
        return True, []
    compact_window = _compact(window)
    missing = [value for value in required if _compact(value) not in compact_window]
    # Formula multiplication is commonly rendered with ASCII or full-width x.
    if "*" in missing and any(value in window for value in ("×", "x", "X")):
        missing.remove("*")
    return not missing, missing


def _structured_gaps(missing: Sequence[str], conflicts: Sequence[str]) -> dict[str, Any]:
    values = [str(value) for value in missing]
    conflict_values = [str(value) for value in conflicts]
    return {
        "missing_subject": any(value.startswith(("subject", "entity")) for value in values),
        "missing_metric": any(value.startswith(("metric", "clause", "proposition")) for value in values),
        "missing_period": any(value.startswith(("period", "date", "scenario")) for value in values),
        "missing_value_unit": any(value.startswith(("value", "unit", "operator", "comparator")) for value in values),
        "missing_source_location": any(value.startswith(("source", "local_window")) for value in values),
        "conflicting_polarity": any("polarity" in value or "scope" in value for value in conflict_values),
        "missing_atoms": _stable(values),
        "contradiction_atoms": _stable(conflict_values),
    }


def _binding_row(
    *,
    status: str,
    basis: str,
    candidate: EvidenceCandidate | None,
    matched: Sequence[str],
    missing: Sequence[str],
    conflicts: Sequence[str],
    confidence: float,
    adapter: str,
    evidence_refs: Sequence[str] | None = None,
    local_window: str = "",
) -> dict[str, Any]:
    refs = list(evidence_refs or ())
    if candidate is not None and not refs:
        refs = [_candidate_source(candidate)]
    source = refs[0] if refs else ""
    gaps = _structured_gaps(missing, conflicts)
    return {
        "status": status,
        "source_local_verdict": status,
        "certification_basis": basis,
        "matched_atoms": _stable(matched),
        "missing_atoms": _stable(missing),
        "contradiction_atoms": _stable(conflicts),
        "conflicting_atoms": _stable(conflicts),
        "evidence_refs": _stable(refs),
        "resolved_evidence_refs": _stable(refs),
        "canonical_source": source,
        "local_window": local_window or (_candidate_context(candidate) if candidate is not None else ""),
        "confidence": round(float(confidence), 6),
        "trust_failures": [] if status in {"supported", "contradicted"} else [basis],
        "corrective_retrieval_gaps": gaps,
        "binding_adapter": adapter,
        "trusted_for_option_gate": status in {"supported", "contradicted"},
        "required_atoms_complete": status in {"supported", "contradicted"},
    }


def _typed_first_pass(
    *,
    bundle: EvidenceBundle,
    option_text: str,
    candidates: Sequence[EvidenceCandidate],
) -> list[tuple[dict[str, Any], EvidenceCandidate]]:
    rows: list[tuple[dict[str, Any], EvidenceCandidate]] = []
    for candidate in candidates:
        source = _candidate_source(candidate)
        context = _candidate_context(candidate)
        payload = {
            "option_text": option_text,
            "question_doc_ids": [str(value) for value in bundle.question.doc_ids],
            "resolved_evidence_refs": [source] if source else [],
            "evidence_refs": [source] if source else [],
            "source_resolution": [
                {
                    "canonical_ref": source,
                    "resolved_path": str(candidate.source or ""),
                    "read_status": "read" if context else "unresolved",
                    "bounded_context": context,
                    "page_or_lineage": source,
                }
            ],
        }
        try:
            row = dict(certify_typed_option_claim(payload))
        except Exception as exc:  # pragma: no cover - preserved in diagnostics
            row = {
                "claim_certification_status": "ambiguous",
                "certification_basis": f"typed_certifier_error:{exc.__class__.__name__}",
                "matched_atoms": [],
                "missing_atoms": ["adapter_execution"],
                "conflicting_atoms": [],
                "canonical_source": source,
                "local_window": context,
            }
        rows.append((row, candidate))
    return rows


def _ordered_ratio_series_binding(
    *,
    option_text: str,
    question_options: Mapping[str, str],
    candidates: Sequence[EvidenceCandidate],
) -> dict[str, Any] | None:
    if "资产负债率" not in option_text or "流动比率" not in option_text:
        return None
    year_match = _YEAR_RE.search(option_text)
    debt_match = re.search(r"资产负债率[^。；]{0,45}?(\d+(?:\.\d+)?)\s*[%％]", option_text)
    current_match = re.search(r"流动比率[^。；]{0,30}?(\d+(?:\.\d+)?)", option_text)
    if not year_match or not debt_match or not current_match:
        return None
    option_year = int(year_match.group(1))
    all_years = [
        int(value)
        for text in question_options.values()
        for value in _YEAR_RE.findall(str(text or ""))
    ]
    latest_year = max(all_years) if all_years else None
    if latest_year is None:
        return None

    for candidate in candidates:
        context = _candidate_context(candidate)
        if "最近三年末" not in context or "资产负债率" not in context or "流动比率" not in context:
            continue
        debt_series_match = re.search(
            r"资产负债率[^。；]{0,80}?分别为\s*([^。；]+)", context
        )
        current_series_match = re.search(
            r"流动比率[^。；]{0,80}?分别为\s*([^。；]+)", context
        )
        if not debt_series_match or not current_series_match:
            continue
        debts = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*[%％]", debt_series_match.group(1))[:3]]
        currents = [float(value) for value in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", current_series_match.group(1))[:3]]
        if len(debts) != 3 or len(currents) != 3:
            continue
        first_year = latest_year - 2
        index = option_year - first_year
        if index not in {0, 1, 2}:
            return _binding_row(
                status="unresolved_missing_atoms",
                basis="ordered series found but option period is outside inferred three-year range",
                candidate=candidate,
                matched=["subject_or_entity", "metric:debt_asset_ratio", "metric:current_ratio", "source_location"],
                missing=["period_mapping"],
                conflicts=[],
                confidence=0.4,
                adapter="same_document_ordered_ratio_series",
            )
        expected_debt = float(debt_match.group(1))
        expected_current = float(current_match.group(1))
        actual_debt = debts[index]
        actual_current = currents[index]
        matched = [
            "subject_or_entity",
            "metric:debt_asset_ratio",
            "metric:current_ratio",
            f"period:{option_year}",
            "ordered_period_mapping",
            "unit:%",
            "unit:ratio",
            "source_location",
        ]
        conflicts: list[str] = []
        if abs(expected_debt - actual_debt) <= 1e-9:
            matched.append(f"value:{expected_debt}%")
        else:
            conflicts.append(f"value:debt_asset_ratio expected={expected_debt}% actual={actual_debt}%")
        if abs(expected_current - actual_current) <= 1e-9:
            matched.append(f"value:current_ratio={expected_current}")
        else:
            conflicts.append(f"value:current_ratio expected={expected_current} actual={actual_current}")
        status = "supported" if not conflicts else "contradicted"
        return _binding_row(
            status=status,
            basis=(
                "same-document ordered three-year series binds period, debt ratio and current ratio"
                if not conflicts
                else "same-document ordered three-year series conflicts with option values"
            ),
            candidate=candidate,
            matched=matched,
            missing=[],
            conflicts=conflicts,
            confidence=0.98,
            adapter="same_document_ordered_ratio_series",
        )
    return None


def _quantifier_scope_conflict(option_text: str, window: str) -> list[str]:
    option_compact = _compact(option_text)
    window_compact = _compact(window)
    broad = any(value in option_compact for value in ("所有员工", "全体员工", "全部员工"))
    narrower = any(value in window_compact for value in ("管理团队及核心人员", "管理层及核心人员", "经营层员工"))
    if broad and narrower and not any(value in window_compact for value in ("所有员工", "全体员工", "全部员工")):
        return ["scope_polarity:all_employees_conflicts_with_management_and_core_personnel"]
    return []


def _direct_source_local_binding(
    *,
    option_text: str,
    candidates: Sequence[EvidenceCandidate],
) -> dict[str, Any]:
    best_unresolved: dict[str, Any] | None = None
    option_values = _value_tokens(option_text)
    option_subjects = _anchors(option_text, _SUBJECT_ANCHORS)
    option_metrics = _anchors(option_text, _METRIC_OR_CLAUSE_ANCHORS)

    for candidate in candidates:
        payload = {
            "source_resolution": [
                {
                    "read_status": "read",
                    "canonical_ref": _candidate_source(candidate),
                    "bounded_context": _candidate_context(candidate),
                }
            ]
        }
        windows = source_windows(payload) or [
            {"canonical_source": _candidate_source(candidate), "local_window": _candidate_context(candidate)}
        ]
        for window_row in windows:
            window = str(window_row.get("local_window") or "")
            coverage = _lexical_coverage(option_text, window)
            matched: list[str] = ["source_location"] if window else []
            missing: list[str] = []
            conflicts = _quantifier_scope_conflict(option_text, window)

            subject_hits = [value for value in option_subjects if _compact(value) in _compact(window)]
            metric_hits = [value for value in option_metrics if _compact(value) in _compact(window)]
            if option_subjects:
                if subject_hits:
                    matched.extend(f"subject:{value}" for value in subject_hits)
                else:
                    missing.append("subject_or_entity")
            elif coverage >= 0.72:
                matched.append("subject_or_entity:source_local_proposition")
            else:
                missing.append("subject_or_entity")
            if option_metrics:
                if metric_hits:
                    matched.extend(f"metric_or_clause:{value}" for value in metric_hits)
                else:
                    missing.append("metric_or_clause")
            elif coverage >= 0.78:
                matched.append("metric_or_clause:source_local_proposition")
            else:
                missing.append("metric_or_clause")

            period_ok, missing_periods, period_basis = _period_match(option_text, window, candidate)
            if period_ok:
                matched.append(f"period:{period_basis}")
            else:
                missing.extend(f"period:{value}" for value in missing_periods)

            missing_values = [token for token in option_values if not _number_present(token, window)]
            if option_values and not missing_values:
                matched.extend(f"value_unit:{token['raw']}" for token in option_values)
            elif missing_values:
                missing.extend(f"value_unit:{token['raw']}" for token in missing_values)

            operator_ok, missing_operators = _operator_match(option_text, window)
            if operator_ok:
                matched.append("operator_or_polarity")
            else:
                missing.extend(f"operator:{value}" for value in missing_operators)

            nonnumeric_anchor_count = len(subject_hits) + len(metric_hits)
            exact_or_high_coverage = _compact(option_text) in _compact(window) or coverage >= 0.72
            values_complete = not missing_values
            semantic_complete = bool(
                window
                and exact_or_high_coverage
                and nonnumeric_anchor_count >= 1
                and period_ok
                and values_complete
                and operator_ok
            )
            if conflicts and coverage >= 0.45 and nonnumeric_anchor_count >= 1:
                return _binding_row(
                    status="contradicted",
                    basis="same-source subject/clause is present but option scope or polarity conflicts",
                    candidate=candidate,
                    matched=matched,
                    missing=[],
                    conflicts=conflicts,
                    confidence=0.96,
                    adapter="source_local_lexical_atom_binding",
                    local_window=window,
                )
            if semantic_complete:
                return _binding_row(
                    status="supported",
                    basis="one source-local window closes non-numeric anchors, period, value/unit and operator",
                    candidate=candidate,
                    matched=[*matched, f"lexical_coverage:{coverage:.3f}"],
                    missing=[],
                    conflicts=[],
                    confidence=min(0.99, 0.75 + coverage * 0.24),
                    adapter="source_local_lexical_atom_binding",
                    local_window=window,
                )

            status = (
                "unresolved_adapter_unavailable"
                if not option_metrics and coverage < 0.72
                else "unresolved_missing_atoms"
            )
            row = _binding_row(
                status=status,
                basis=(
                    "no typed metric/clause adapter and lexical proposition coverage is insufficient"
                    if status == "unresolved_adapter_unavailable"
                    else "source-local evidence exists but required atoms are incomplete"
                ),
                candidate=candidate,
                matched=[*matched, f"lexical_coverage:{coverage:.3f}"],
                missing=missing,
                conflicts=conflicts,
                confidence=max(0.05, min(0.7, coverage)),
                adapter="source_local_lexical_atom_binding",
                local_window=window,
            )
            if best_unresolved is None or (
                len(row["matched_atoms"]), -len(row["missing_atoms"]), row["confidence"]
            ) > (
                len(best_unresolved["matched_atoms"]),
                -len(best_unresolved["missing_atoms"]),
                best_unresolved["confidence"],
            ):
                best_unresolved = row

    return best_unresolved or _binding_row(
        status="unresolved_adapter_unavailable",
        basis="no readable source-local candidate is available to the generic binder",
        candidate=None,
        matched=[],
        missing=["source_location", "subject_or_entity", "metric_or_clause"],
        conflicts=[],
        confidence=0.0,
        adapter="source_local_lexical_atom_binding",
    )


def certify_option_in_binding_scope(
    *,
    bundle: EvidenceBundle,
    result: SolverResult,
    option_label: str,
    option_text: str,
    candidates: Sequence[EvidenceCandidate],
    scope: OptionBindingScope,
    question_options: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return one calibrated source-local verdict with structured gaps."""
    metadata = dict(result.metadata or {})
    answer_source = str(metadata.get("answer_source") or "").strip().lower()
    unsafe = answer_source in _UNSAFE_ANSWER_SOURCES or (
        metadata.get("fallback_answer") is not None
        and metadata.get("no_supported_options") is True
    )
    if unsafe:
        row = _binding_row(
            status="unsafe_model_answer",
            basis=f"model answer source is not authoritative:{answer_source or 'fallback'}",
            candidate=None,
            matched=[],
            missing=["authoritative_model_answer", "structured_judgment_closure"],
            conflicts=[],
            confidence=0.0,
            adapter="answer_authority_gate",
        )
        row.update({"option_label": option_label, "model_answer_source": answer_source})
        return row
    if _GENERIC_NUMERIC_TOKEN_RE.fullmatch(str(option_text or "").strip()):
        row = _binding_row(
            status="unresolved_adapter_unavailable",
            basis="generic numeric token cannot certify an option without subject and metric atoms",
            candidate=None,
            matched=[],
            missing=["subject_or_entity", "metric_or_clause", "source_local_proposition"],
            conflicts=[],
            confidence=0.0,
            adapter="generic_numeric_token_guard",
        )
        row.update({"option_label": option_label})
        return row
    if not scope.lineage_valid or scope.fail_closed:
        row = _binding_row(
            status="lineage_invalid",
            basis=scope.option_binding_scope_expansion_reason or "binding scope lineage is invalid",
            candidate=None,
            matched=[],
            missing=["validated_solver_source_or_used_doc_lineage"],
            conflicts=[],
            confidence=0.0,
            adapter="option_binding_scope_gate",
        )
        row.update({"option_label": option_label})
        return row

    typed_rows = _typed_first_pass(bundle=bundle, option_text=option_text, candidates=candidates)
    authoritative = [
        (row, candidate)
        for row, candidate in typed_rows
        if str(row.get("claim_certification_status") or "") in {"supported", "contradicted"}
    ]
    if authoritative:
        row, candidate = max(
            authoritative,
            key=lambda item: (
                len(item[0].get("matched_atoms") or []),
                -len(item[0].get("missing_atoms") or []),
                float(item[1].score or 0.0),
            ),
        )
        status = str(row.get("claim_certification_status"))
        output = _binding_row(
            status=status,
            basis=str(row.get("certification_basis") or "typed source-local certification"),
            candidate=candidate,
            matched=row.get("matched_atoms") or [],
            missing=row.get("missing_atoms") or [],
            conflicts=row.get("conflicting_atoms") or [],
            confidence=1.0,
            adapter="existing_typed_claim_certifier",
            evidence_refs=row.get("evidence_refs_considered") or [],
            local_window=str(row.get("local_window") or ""),
        )
        output.update({"option_label": option_label, "claim_atoms": row.get("claim_atoms") or {}})
        return output

    ordered = _ordered_ratio_series_binding(
        option_text=option_text,
        question_options=dict(question_options or bundle.question.options),
        candidates=candidates,
    )
    if ordered is not None:
        ordered.update({"option_label": option_label})
        return ordered

    direct = _direct_source_local_binding(option_text=option_text, candidates=candidates)
    direct.update({"option_label": option_label})
    # Preserve the strongest typed first-pass diagnostics even when the
    # calibrated adapter remains unresolved.
    if typed_rows:
        best_typed, _candidate = max(
            typed_rows,
            key=lambda item: (
                len(item[0].get("matched_atoms") or []),
                -len(item[0].get("missing_atoms") or []),
                -len(item[0].get("conflicting_atoms") or []),
            ),
        )
        direct["typed_first_pass"] = {
            "status": best_typed.get("claim_certification_status"),
            "matched_atoms": list(best_typed.get("matched_atoms") or []),
            "missing_atoms": list(best_typed.get("missing_atoms") or []),
            "conflicting_atoms": list(best_typed.get("conflicting_atoms") or []),
            "certification_basis": best_typed.get("certification_basis"),
            "canonical_source": best_typed.get("canonical_source"),
        }
    return direct


def is_generic_numeric_only_legacy(payload: Mapping[str, Any] | None) -> bool:
    """Return true when legacy support consists only of generic numeric tokens."""
    raw = dict(payload or {})
    terms: list[str] = []
    for key in ("matched_terms", "coherent_terms"):
        value = raw.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            terms.extend(str(item).strip() for item in value if str(item).strip())
    return bool(terms) and all(_GENERIC_NUMERIC_TOKEN_RE.fullmatch(term) for term in terms)


__all__ = [
    "ClaimAtoms",
    "TypedClaimAtoms",
    "OptionBindingScope",
    "FINAL_BINDING_STATUSES",
    "certify_option_claim",
    "certify_typed_option_claim",
    "certify_option_in_binding_scope",
    "extract_claim_atoms",
    "extract_typed_claim_atoms",
    "is_generic_numeric_only_legacy",
    "select_option_binding_scope",
    "source_doc_id",
    "source_windows",
]
