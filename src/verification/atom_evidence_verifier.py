"""Fail-closed Atom -> Evidence verification for BB-P0-13.

The verifier consumes only caller-supplied ``EvidenceCandidate`` objects.  It
never retrieves, expands document scope, calls a model, or consumes qids/answer
letters.  SUPPORT/REFUTE require one auditable source-local candidate; otherwise
UNRESOLVED is returned.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable, Mapping, Sequence

from contracts import EvidenceCandidate
from .claim_atoms import ClaimAtom


SUPPORT = "SUPPORT"
REFUTE = "REFUTE"
UNRESOLVED = "UNRESOLVED"
_VALID_VERDICTS = {SUPPORT, REFUTE, UNRESOLVED}

_NEGATIVE_RE = re.compile(r"(?:不得|不能|不应|不可以|禁止|无需|无须|不包括|不属于|不是|未予|未能|未发生|未披露|无权|无效)")
_POSITIVE_MODAL_RE = re.compile(r"(?:可以|可(?:以)?|允许|有权|应当|必须|能够)")
_DIRECTION_GROUPS = {
    "increase": ("增加", "增长", "上升", "提高", "回升"),
    "decrease": ("减少", "下降", "降低", "下滑", "降幅"),
}
_VALUE_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>个百分点|万亿元|亿元|万元|千元|元|%|％|倍|个保单年度|保单年度|年|个月|月|个工作日|工作日|天|日|人|辆|笔)?"
)
_PAGE_RE = re.compile(r"(?:page[_-]?|p)(\d{1,6})(?:\D|$)", re.IGNORECASE)
_GENERIC_SUBJECTS = {"公司", "本公司", "本集团", "发行人", "上市公司", "交易对方", "其他交易对方"}
_SCOPE_STOPWORDS = (
    "在", "的情况下", "情况下", "若", "如果", "则", "时", "仅当", "只有", "才",
    "除", "除外", "除非", "例外", "的情形外", "情形外", "外",
)


@dataclass(frozen=True)
class AtomVerdict:
    atom_id: str
    verdict: str
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    bound_doc_id: str
    bound_page: str
    bound_source: str
    matched_span: str
    binding_auditable: bool

    def __post_init__(self) -> None:
        if self.verdict not in _VALID_VERDICTS:
            raise ValueError(f"invalid atom verdict: {self.verdict}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload["evidence_refs"] = list(self.evidence_refs)
        return payload


@dataclass(frozen=True)
class _Lineage:
    doc_id: str
    page: str
    source: str
    complete: bool


@dataclass(frozen=True)
class _CandidateAssessment:
    verdict: str
    reasons: tuple[str, ...]
    candidate: EvidenceCandidate
    lineage: _Lineage
    matched_span: str
    anchor_score: int


def verify_atom(
    atom: ClaimAtom,
    candidates: Sequence[EvidenceCandidate],
) -> AtomVerdict:
    """Verify one atom against caller-supplied evidence only."""
    if str(atom.scope_confidence or "").upper() != "HIGH":
        return _unresolved(atom, ("SCOPE_CONFIDENCE_LOW",), candidates)
    if not candidates:
        return _unresolved(atom, ("NO_EVIDENCE_CANDIDATES",), candidates)
    if _semantic_claim_is_incomplete(atom):
        return _unresolved(atom, ("CLAIM_SEMANTIC_ANCHORS_INSUFFICIENT",), candidates)

    candidate_docs = {str(candidate.doc_id).strip() for candidate in candidates if str(candidate.doc_id).strip()}
    numeric_relation = atom.relation in {"=", ">=", "<=", ">", "<"} and bool(atom.value)
    if numeric_relation and not str(atom.subject or "").strip() and len(candidate_docs) > 1:
        return _unresolved(atom, ("SUBJECT_SCOPE_AMBIGUOUS_ACROSS_DOCS",), candidates)

    assessments = [_assess_candidate(atom, candidate) for candidate in candidates]
    authoritative = [row for row in assessments if row.verdict in {SUPPORT, REFUTE}]
    support_rows = [row for row in authoritative if row.verdict == SUPPORT]
    refute_rows = [row for row in authoritative if row.verdict == REFUTE]

    if support_rows and refute_rows:
        refs = tuple(dict.fromkeys(row.lineage.source for row in authoritative if row.lineage.source))
        return AtomVerdict(
            atom_id=atom.atom_id,
            verdict=UNRESOLVED,
            reason_codes=("CONFLICTING_AUDITABLE_EVIDENCE",),
            evidence_refs=refs,
            bound_doc_id="",
            bound_page="",
            bound_source="",
            matched_span="",
            binding_auditable=False,
        )
    if support_rows:
        return _from_assessment(atom, max(support_rows, key=_assessment_rank))
    if refute_rows:
        return _from_assessment(atom, max(refute_rows, key=_assessment_rank))

    partial = max(assessments, key=_assessment_rank)
    reasons = list(partial.reasons or ("EVIDENCE_INSUFFICIENT",))
    if _frankenstein_possible(atom, candidates):
        reasons.append("CROSS_DOC_FRANKENSTEIN_BLOCKED")
    refs = tuple(
        dict.fromkeys(
            row.lineage.source
            for row in sorted(assessments, key=_assessment_rank, reverse=True)[:4]
            if row.lineage.source and row.anchor_score > 0
        )
    )
    return AtomVerdict(
        atom_id=atom.atom_id,
        verdict=UNRESOLVED,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence_refs=refs,
        bound_doc_id="",
        bound_page="",
        bound_source="",
        matched_span=partial.matched_span if partial.anchor_score else "",
        binding_auditable=False,
    )


def _assess_candidate(atom: ClaimAtom, candidate: EvidenceCandidate) -> _CandidateAssessment:
    lineage = _lineage(candidate)
    text = _candidate_text(candidate)
    if not lineage.complete:
        return _CandidateAssessment(
            UNRESOLVED,
            ("LINEAGE_INCOMPLETE",),
            candidate,
            lineage,
            _matched_span(text, atom),
            0,
        )
    if not text.strip():
        return _CandidateAssessment(UNRESOLVED, ("EMPTY_EVIDENCE",), candidate, lineage, "", 0)

    reasons: list[str] = []
    score = 0
    subject_ok = _subject_matches(atom.subject, text)
    if subject_ok:
        score += 1
    elif atom.subject:
        reasons.append("SUBJECT_MISMATCH_OR_MISSING")

    object_ok = _anchor_present(atom.object_or_metric, text)
    if object_ok:
        score += 2
    elif atom.object_or_metric:
        reasons.append("OBJECT_OR_METRIC_MISSING")

    time_ok, time_reason = _scope_matches(atom.time_scope, text, kind="time")
    if time_ok:
        score += int(bool(atom.time_scope))
    elif time_reason:
        reasons.append(time_reason)

    condition_ok, condition_reason = _scope_matches(atom.condition, text, kind="condition")
    if condition_ok:
        score += int(bool(atom.condition))
    elif condition_reason:
        reasons.append(condition_reason)

    exception_ok, exception_reason = _scope_matches(atom.exception, text, kind="exception")
    if exception_ok:
        score += int(bool(atom.exception))
    elif exception_reason:
        reasons.append(exception_reason)

    matched_span = _matched_span(text, atom)
    semantic_scope_ok = subject_ok and object_ok and time_ok and condition_ok and exception_ok
    if not semantic_scope_ok:
        return _CandidateAssessment(
            UNRESOLVED,
            tuple(reasons or ("SEMANTIC_SCOPE_INCOMPLETE",)),
            candidate,
            lineage,
            matched_span,
            score,
        )

    relation = str(atom.relation or "asserted")
    if relation in {"=", ">=", "<=", ">", "<"} and atom.value:
        numeric_verdict, numeric_reasons = _verify_numeric(atom, text)
        numeric_span = _metric_local_proposition(text, atom.object_or_metric) or matched_span
        return _CandidateAssessment(
            numeric_verdict,
            tuple(numeric_reasons),
            candidate,
            lineage,
            numeric_span,
            score + (3 if numeric_verdict in {SUPPORT, REFUTE} else 0),
        )
    if relation in {"prohibited", "negated"} or atom.polarity == "negative":
        polarity_verdict, polarity_reasons = _verify_negative(atom, text)
        return _CandidateAssessment(
            polarity_verdict,
            tuple(polarity_reasons),
            candidate,
            lineage,
            matched_span,
            score + (3 if polarity_verdict in {SUPPORT, REFUTE} else 0),
        )

    text_verdict, text_reasons = _verify_textual(atom, text)
    return _CandidateAssessment(
        text_verdict,
        tuple(text_reasons),
        candidate,
        lineage,
        matched_span,
        score + (3 if text_verdict in {SUPPORT, REFUTE} else 0),
    )


def _verify_numeric(atom: ClaimAtom, text: str) -> tuple[str, tuple[str, ...]]:
    """Compare only the value deterministically bound to the target metric.

    P13-R1 deliberately rejects the old "any compatible number in a nearby
    window" rule.  Numeric authority now requires one metric-local proposition
    and one unambiguous value/unit binding inside that proposition.
    """
    expected_unit = _normalize_unit(atom.unit)
    pairs, locality_reason = _metric_local_numeric_pairs(text, atom.object_or_metric)
    if not pairs:
        return UNRESOLVED, (locality_reason or "NUMERIC_VALUE_MISSING",)

    compatible = [(value, unit) for value, unit in pairs if _units_compatible(expected_unit, unit)]
    if not compatible:
        evidence_units = {unit for _, unit in pairs if unit}
        if expected_unit and evidence_units:
            return UNRESOLVED, ("UNIT_INCOMPATIBLE",)
        return UNRESOLVED, ("UNIT_OR_VALUE_UNRESOLVED",)

    unique_compatible = list(dict.fromkeys(compatible))
    if len(unique_compatible) != 1:
        return UNRESOLVED, ("METRIC_LOCAL_MULTIPLE_VALUES_AMBIGUOUS",)

    try:
        threshold = float(str(atom.value).replace(",", ""))
    except ValueError:
        return UNRESOLVED, ("CLAIM_VALUE_NON_NUMERIC",)

    actual, _unit = unique_compatible[0]
    outcome = _compare(actual, threshold, atom.relation)
    if outcome is True:
        return SUPPORT, ("METRIC_LOCAL_NUMERIC_RELATION_SUPPORT",)
    if outcome is False:
        return REFUTE, ("METRIC_LOCAL_NUMERIC_RELATION_REFUTE",)
    return UNRESOLVED, ("NUMERIC_RELATION_UNRESOLVED",)


def _verify_negative(atom: ClaimAtom, text: str) -> tuple[str, tuple[str, ...]]:
    window = _local_window(text, atom.object_or_metric)
    if _NEGATIVE_RE.search(window):
        return SUPPORT, ("SAME_SOURCE_NEGATIVE_POLARITY_SUPPORT",)
    if _POSITIVE_MODAL_RE.search(window):
        return REFUTE, ("SAME_SOURCE_EXPLICIT_OPPOSITE_POLARITY",)
    return UNRESOLVED, ("NEGATIVE_POLARITY_NOT_EXPLICIT",)


def _verify_textual(atom: ClaimAtom, text: str) -> tuple[str, tuple[str, ...]]:
    compact_text = _compact(text)
    compact_atom = _compact(atom.atom_text)
    if compact_atom and compact_atom in compact_text:
        return SUPPORT, ("EXACT_SOURCE_LOCAL_PROPOSITION",)

    direction = _direction(atom.atom_text)
    if atom.value and atom.unit and direction:
        local = _metric_local_proposition(text, atom.object_or_metric)
        if not local:
            return UNRESOLVED, ("METRIC_LOCAL_PROPOSITION_NOT_FOUND",)
        value_state = _metric_local_value_matches(atom.value, atom.unit, text, atom.object_or_metric)
        if value_state == "ambiguous":
            return UNRESOLVED, ("METRIC_LOCAL_MULTIPLE_VALUES_AMBIGUOUS",)
        value_present = value_state == "match"
        if value_present and any(token in local for token in _DIRECTION_GROUPS[direction]):
            return SUPPORT, ("METRIC_LOCAL_DIRECTION_VALUE_SUPPORT",)
        opposite = "decrease" if direction == "increase" else "increase"
        if value_present and any(token in local for token in _DIRECTION_GROUPS[opposite]):
            return REFUTE, ("METRIC_LOCAL_DIRECTION_CONTRADICTION",)

    # Textual entailment beyond exact source-local proposition is deliberately
    # not guessed; lexical similarity alone never becomes SUPPORT.
    return UNRESOLVED, ("TEXTUAL_ENTAILMENT_NOT_DETERMINISTIC",)


def _semantic_claim_is_incomplete(atom: ClaimAtom) -> bool:
    subject = str(atom.subject or "").strip()
    object_or_metric = str(atom.object_or_metric or "").strip()
    atom_text = str(atom.atom_text or "").strip()
    has_value_with_unit = bool(str(atom.value or "").strip() and str(atom.unit or "").strip())
    has_structured_relation = atom.relation in {"=", ">=", "<=", ">", "<", "prohibited", "negated"}
    has_predicate = bool(
        re.search(
            r"(?:为|是|包括|属于|列举|承诺|发生|达到|增加|减少|增长|下降|上升|提高|回升|不得|不能|应当|必须|允许|有权|用于|披露|解除|支付|保存|锁定)",
            atom_text,
        )
    )
    naked_number = bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", atom_text))
    naked_label = bool(atom_text and not has_predicate and not has_value_with_unit and not has_structured_relation and not subject)
    missing_object = not object_or_metric
    return naked_number or naked_label or missing_object


def _subject_matches(subject: str, text: str) -> bool:
    subject = str(subject or "").strip(" ，,：:的\"“”")
    if not subject:
        return True
    if subject in _GENERIC_SUBJECTS:
        return _compact(subject) in _compact(text) or "公司" in text
    return _compact(subject) in _compact(text)


def _anchor_present(anchor: str, text: str) -> bool:
    anchor = str(anchor or "").strip()
    if not anchor:
        return True
    compact_anchor = _compact(anchor)
    compact_text = _compact(text)
    if compact_anchor in compact_text:
        return True
    # For long action surfaces use conservative token intersection only to
    # establish candidate relevance, never enough by itself for SUPPORT.
    tokens = [token for token in re.split(r"[的、，,：:（）()\s]+", anchor) if len(token) >= 2]
    return bool(tokens) and all(_compact(token) in compact_text for token in tokens[:3])


def _scope_matches(scope: str, text: str, *, kind: str) -> tuple[bool, str]:
    scope = str(scope or "").strip()
    if not scope:
        return True, ""
    parts = [part for part in scope.split("；") if part]
    compact_text = _compact(text)
    for part in parts:
        compact_part = _compact(part)
        core = _scope_core(part)
        if compact_part and compact_part in compact_text:
            continue
        if core and core in compact_text:
            continue
        if kind == "time":
            return False, "TIME_SCOPE_MISSING_OR_MISMATCH"
        if kind == "condition":
            return False, "CONDITION_NOT_ESTABLISHED"
        return False, "EXCEPTION_NOT_ESTABLISHED"
    return True, ""


def _scope_core(value: str) -> str:
    core = _compact(value)
    for stopword in _SCOPE_STOPWORDS:
        core = core.replace(_compact(stopword), "")
    return core


def _metric_local_numeric_pairs(text: str, anchor: str) -> tuple[list[tuple[float, str]], str]:
    """Return numeric pairs from the smallest proposition containing ``anchor``.

    Comma/semicolon/full-stop/newline boundaries prevent values from adjacent
    metrics on the same page from being borrowed.  If the target metric occurs
    in multiple propositions with different local values, the caller receives
    all values and must fail closed as ambiguous.
    """
    anchor = str(anchor or "").strip()
    if not anchor:
        return [], "METRIC_LOCAL_ANCHOR_MISSING"

    spans = _anchor_spans(text, anchor)
    if not spans:
        return [], "METRIC_LOCAL_PROPOSITION_NOT_FOUND"

    pairs: list[tuple[float, str]] = []
    seen_propositions: set[tuple[int, int]] = set()
    for start, end in spans:
        left, right = _proposition_bounds(text, start, end)
        bounds = (left, right)
        if bounds in seen_propositions:
            continue
        seen_propositions.add(bounds)
        proposition = text[left:right]
        pairs.extend(_numeric_pairs_from_text(proposition))

    if not pairs:
        return [], "METRIC_LOCAL_VALUE_MISSING"
    return pairs, ""


def _anchor_spans(text: str, anchor: str) -> list[tuple[int, int]]:
    """Locate compact anchor matches while retaining original string offsets."""
    compact_anchor = _compact(anchor)
    if not compact_anchor:
        return []
    compact_chars: list[str] = []
    original_offsets: list[int] = []
    for index, char in enumerate(str(text or "")):
        compact_char = _compact(char)
        if not compact_char:
            continue
        compact_chars.append(compact_char)
        original_offsets.append(index)
    compact_text = "".join(compact_chars)
    spans: list[tuple[int, int]] = []
    search_from = 0
    while True:
        position = compact_text.find(compact_anchor, search_from)
        if position < 0:
            break
        compact_end = position + len(compact_anchor) - 1
        if compact_end < len(original_offsets):
            spans.append((original_offsets[position], original_offsets[compact_end] + 1))
        search_from = position + max(1, len(compact_anchor))
    return spans


def _proposition_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Bound one metric proposition by deterministic punctuation separators."""
    separators = "，,；;。！？!?\n\r"
    left = start
    while left > 0 and text[left - 1] not in separators:
        left -= 1
    right = end
    while right < len(text) and text[right] not in separators:
        right += 1
    return left, right


def _numeric_pairs_from_text(text: str) -> list[tuple[float, str]]:
    pairs: list[tuple[float, str]] = []
    for match in _VALUE_UNIT_RE.finditer(text):
        raw = match.group("value").replace(",", "")
        unit = _normalize_unit(str(match.group("unit") or ""))
        try:
            value = float(raw)
        except ValueError:
            continue
        if unit == "年" and 1900 <= value <= 2100 and float(value).is_integer():
            continue
        pairs.append((value, unit))
    return pairs


def _local_numeric_pairs(text: str, anchor: str) -> list[tuple[float, str]]:
    window = _local_window(text, anchor, radius=180)
    return _numeric_pairs_from_text(window)


def _compare(actual: float, threshold: float, relation: str) -> bool | None:
    epsilon = 1e-9
    if relation == "=":
        return abs(actual - threshold) <= epsilon
    if relation == ">=":
        return actual >= threshold - epsilon
    if relation == "<=":
        return actual <= threshold + epsilon
    if relation == ">":
        return actual > threshold + epsilon
    if relation == "<":
        return actual < threshold - epsilon
    return None


def _units_compatible(expected: str, actual: str) -> bool:
    expected = _normalize_unit(expected)
    actual = _normalize_unit(actual)
    if expected == actual:
        return True
    if not expected and not actual:
        return True
    # Percent and percentage point are intentionally never compatible.
    if {expected, actual} == {"%", "百分点"}:
        return False
    return False


def _normalize_unit(value: str) -> str:
    value = str(value or "").strip().replace("％", "%")
    aliases = {"工作日": "工作日", "个工作日": "工作日", "保单年度": "保单年度", "个保单年度": "保单年度"}
    return aliases.get(value, value)


def _metric_local_propositions(text: str, anchor: str) -> list[str]:
    spans = _anchor_spans(text, anchor)
    propositions: list[str] = []
    seen: set[tuple[int, int]] = set()
    for start, end in spans:
        left, right = _proposition_bounds(text, start, end)
        bounds = (left, right)
        if bounds in seen:
            continue
        seen.add(bounds)
        proposition = text[left:right].strip()
        if proposition:
            propositions.append(proposition)
    return propositions


def _metric_local_proposition(text: str, anchor: str) -> str:
    propositions = _metric_local_propositions(text, anchor)
    return propositions[0] if len(propositions) == 1 else ""


def _metric_local_value_matches(value: str, unit: str, text: str, anchor: str) -> str:
    """Return match/mismatch/ambiguous for one metric-local expected value."""
    expected_unit = _normalize_unit(unit)
    pairs, _reason = _metric_local_numeric_pairs(text, anchor)
    compatible = list(
        dict.fromkeys(
            (actual, actual_unit)
            for actual, actual_unit in pairs
            if _units_compatible(expected_unit, actual_unit)
        )
    )
    if len(compatible) != 1:
        return "ambiguous"
    try:
        expected_value = float(str(value).replace(",", ""))
    except ValueError:
        return "ambiguous"
    return "match" if abs(compatible[0][0] - expected_value) <= 1e-9 else "mismatch"


def _direction(text: str) -> str:
    for name, tokens in _DIRECTION_GROUPS.items():
        if any(token in str(text or "") for token in tokens):
            return name
    return ""


def _lineage(candidate: EvidenceCandidate) -> _Lineage:
    metadata: Mapping[str, Any] = candidate.metadata or {}
    doc_id = str(metadata.get("canonical_doc_id") or candidate.doc_id or "").strip()
    source = str(candidate.source or metadata.get("source") or "").strip()
    page_value = (
        metadata.get("page_number")
        or metadata.get("page")
        or metadata.get("source_page")
        or ""
    )
    page = str(page_value).strip()
    if not page and source:
        match = _PAGE_RE.search(source.replace("\\", "/"))
        if match:
            page = match.group(1)
    complete = bool(doc_id and source and page)
    return _Lineage(doc_id=doc_id, page=page, source=source, complete=complete)


def _candidate_text(candidate: EvidenceCandidate) -> str:
    return "\n".join(
        str(value).strip()
        for value in (candidate.before_text, candidate.text, candidate.after_text)
        if str(value or "").strip()
    )


def _local_window(text: str, anchor: str, radius: int = 220) -> str:
    if not text:
        return ""
    anchor = str(anchor or "").strip()
    if anchor:
        position = _compact_with_map(text).find(_compact(anchor))
        if position >= 0:
            # Compact-position to original-position mapping is approximate but
            # sufficient for a bounded audit span; fall back to raw find first.
            raw = text.find(anchor)
            center = raw if raw >= 0 else min(len(text) - 1, position)
            return text[max(0, center - radius) : min(len(text), center + len(anchor) + radius)]
    return text[: min(len(text), radius * 2)]


def _matched_span(text: str, atom: ClaimAtom) -> str:
    return _local_window(text, atom.object_or_metric, radius=180).strip()


def _compact(value: str) -> str:
    return re.sub(r"[\s，,。；;：:（）()\[\]【】\"'“”‘’]", "", str(value or "")).lower()


def _compact_with_map(value: str) -> str:
    return _compact(value)


def _assessment_rank(row: _CandidateAssessment) -> tuple[int, int, float]:
    authority = 2 if row.verdict in {SUPPORT, REFUTE} else 1
    return authority, row.anchor_score, float(row.candidate.score or 0.0)


def _from_assessment(atom: ClaimAtom, row: _CandidateAssessment) -> AtomVerdict:
    return AtomVerdict(
        atom_id=atom.atom_id,
        verdict=row.verdict,
        reason_codes=row.reasons,
        evidence_refs=(row.lineage.source,),
        bound_doc_id=row.lineage.doc_id,
        bound_page=row.lineage.page,
        bound_source=row.lineage.source,
        matched_span=row.matched_span,
        binding_auditable=True,
    )


def _unresolved(
    atom: ClaimAtom,
    reasons: Sequence[str],
    candidates: Sequence[EvidenceCandidate],
) -> AtomVerdict:
    refs = tuple(dict.fromkeys(str(candidate.source) for candidate in candidates if str(candidate.source)))
    return AtomVerdict(
        atom_id=atom.atom_id,
        verdict=UNRESOLVED,
        reason_codes=tuple(str(value) for value in reasons),
        evidence_refs=refs[:4],
        bound_doc_id="",
        bound_page="",
        bound_source="",
        matched_span="",
        binding_auditable=False,
    )


def _frankenstein_possible(atom: ClaimAtom, candidates: Sequence[EvidenceCandidate]) -> bool:
    docs = {str(candidate.doc_id) for candidate in candidates if str(candidate.doc_id)}
    if len(docs) < 2:
        return False
    text_by_candidate = [_candidate_text(candidate) for candidate in candidates]
    requirements: list[Callable[[str], bool]] = []
    if atom.subject:
        requirements.append(lambda text: _subject_matches(atom.subject, text))
    if atom.object_or_metric:
        requirements.append(lambda text: _anchor_present(atom.object_or_metric, text))
    if atom.time_scope:
        requirements.append(lambda text: _scope_matches(atom.time_scope, text, kind="time")[0])
    if atom.condition:
        requirements.append(lambda text: _scope_matches(atom.condition, text, kind="condition")[0])
    if atom.exception:
        requirements.append(lambda text: _scope_matches(atom.exception, text, kind="exception")[0])
    if atom.value:
        requirements.append(lambda text: bool(_local_numeric_pairs(text, atom.object_or_metric)))
    if not requirements:
        return False
    union_covers = all(any(requirement(text) for text in text_by_candidate) for requirement in requirements)
    one_covers = any(all(requirement(text) for requirement in requirements) for text in text_by_candidate)
    return union_covers and not one_covers


__all__ = [
    "AtomVerdict",
    "SUPPORT",
    "REFUTE",
    "UNRESOLVED",
    "verify_atom",
]
