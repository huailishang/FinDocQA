"""Offline shadow provenance for facts explicitly supplied by a question stem.

This module is intentionally not wired into production validation.  It provides
narrow, deterministic checks that can prove when a submitted answer is derived
entirely from facts stated in the question itself.  Unsupported shapes fail
closed rather than being guessed as stem-grounded.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Sequence


_DOC_REF = re.compile(r"\bDOC:\d+\b", re.IGNORECASE)
_DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日"),
    re.compile(r"(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})"),
)
_ADVANCE_DURATION = re.compile(
    r"(?:至少\s*)?提前\s*(?P<value>\d+)\s*(?:个\s*)?(?P<unit>自然日|日|天)"
)
_REASONING_DURATION = re.compile(
    r"(?P<value>\d+)\s*(?:个\s*)?(?P<unit>自然日|日|天)"
)


@dataclass(frozen=True)
class StemFact:
    fact_id: str
    kind: str
    normalized_value: str
    raw_text: str
    relation: str | None = None
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StemBindingResult:
    stem_facts: tuple[StemFact, ...]
    reasoning_bound_fact_ids: tuple[str, ...]
    unbound_fact_ids: tuple[str, ...]
    binding_complete: bool
    calculation_replay_pass: bool
    replay_kind: str | None
    replay_expected_answer: str | None
    replay_submitted_answer: str | None
    replay_trace: tuple[str, ...]
    external_evidence_refs: tuple[str, ...]
    external_evidence_dependency: bool
    question_stem_grounded: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stem_facts": [fact.to_dict() for fact in self.stem_facts],
            "reasoning_bound_fact_ids": list(self.reasoning_bound_fact_ids),
            "unbound_fact_ids": list(self.unbound_fact_ids),
            "binding_complete": self.binding_complete,
            "calculation_replay_pass": self.calculation_replay_pass,
            "replay_kind": self.replay_kind,
            "replay_expected_answer": self.replay_expected_answer,
            "replay_submitted_answer": self.replay_submitted_answer,
            "replay_trace": list(self.replay_trace),
            "external_evidence_refs": list(self.external_evidence_refs),
            "external_evidence_dependency": self.external_evidence_dependency,
            "question_stem_grounded": self.question_stem_grounded,
            "reason": self.reason,
        }


def _parse_date_parts(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def extract_dates(text: Any) -> tuple[date, ...]:
    source = str(text or "")
    values: list[date] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(source):
            parsed = _parse_date_parts(match.group("year"), match.group("month"), match.group("day"))
            if parsed is not None and parsed not in values:
                values.append(parsed)
    return tuple(values)


def format_cn_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def extract_question_stem_facts(question: Any) -> tuple[StemFact, ...]:
    """Extract only facts needed by supported deterministic stem operations.

    Currently the safe supported operation is a calendar-date offset explicitly
    stated as "提前 N 个自然日/日/天".  General numbers, clauses and domain facts
    are deliberately not promoted to stem provenance because their semantics
    cannot be proven by syntax alone.
    """

    text = str(question or "").strip()
    facts: list[StemFact] = []

    advance_matches = list(_ADVANCE_DURATION.finditer(text))
    dates = extract_dates(text)
    if not advance_matches or not dates:
        return ()

    # A deterministic date-offset replay needs one unambiguous anchor date and
    # one unambiguous advance duration.  Ambiguous multi-date/multi-offset stems
    # fail closed instead of selecting a convenient pair.
    unique_dates = tuple(dict.fromkeys(dates))
    unique_durations = {
        (int(match.group("value")), match.group("unit")) for match in advance_matches
    }
    if len(unique_dates) != 1 or len(unique_durations) != 1:
        return ()

    anchor = unique_dates[0]
    duration_value, duration_unit = next(iter(unique_durations))
    facts.append(
        StemFact(
            fact_id="stem_date_anchor_1",
            kind="date",
            normalized_value=anchor.isoformat(),
            raw_text=format_cn_date(anchor),
            relation="effective_date",
        )
    )
    facts.append(
        StemFact(
            fact_id="stem_duration_1",
            kind="duration_days",
            normalized_value=str(duration_value),
            raw_text=f"提前{duration_value}个{duration_unit}",
            relation="advance_before",
            unit=duration_unit,
        )
    )
    return tuple(facts)


def _reasoning_binds_fact(reasoning: str, fact: StemFact) -> bool:
    if fact.kind == "date":
        target = date.fromisoformat(fact.normalized_value)
        return target in extract_dates(reasoning)
    if fact.kind == "duration_days":
        target_value = int(fact.normalized_value)
        target_unit = str(fact.unit or "")
        for match in _REASONING_DURATION.finditer(reasoning):
            value = int(match.group("value"))
            unit = match.group("unit")
            if value != target_value:
                continue
            if target_unit == "自然日" and unit != "自然日":
                continue
            return True
        return False
    return False


def _normalized_answer_date(answers: Sequence[str]) -> date | None:
    if len(tuple(answers)) != 1:
        return None
    dates = extract_dates(str(tuple(answers)[0]))
    if len(dates) != 1:
        return None
    return dates[0]


def audit_question_stem_grounding(
    *,
    question: Any,
    reasoning: Any,
    answers: Sequence[str],
    raw_evidence_refs: Sequence[str] = (),
) -> StemBindingResult:
    """Audit whether the answer is deterministically grounded in question stem facts.

    The audit is qid-agnostic: it uses only question/reasoning/answer/provenance
    content.  Any unsupported derivation, missing binding, ambiguous stem, or DOC
    dependency remains non-grounded.
    """

    question_text = str(question or "").strip()
    reasoning_text = str(reasoning or "").strip()
    normalized_answers = tuple(str(value).strip() for value in answers if str(value).strip())
    stem_facts = extract_question_stem_facts(question_text)

    bound_ids = tuple(
        fact.fact_id for fact in stem_facts if _reasoning_binds_fact(reasoning_text, fact)
    )
    unbound_ids = tuple(fact.fact_id for fact in stem_facts if fact.fact_id not in bound_ids)
    binding_complete = bool(stem_facts) and not unbound_ids

    refs = tuple(
        dict.fromkeys(
            [str(value).strip() for value in raw_evidence_refs if str(value).strip()]
            + _DOC_REF.findall(reasoning_text)
        )
    )
    external_dependency = bool(refs)

    replay_kind: str | None = None
    replay_expected: str | None = None
    replay_submitted: str | None = None
    replay_trace: list[str] = []
    replay_pass = False

    date_facts = [fact for fact in stem_facts if fact.kind == "date"]
    duration_facts = [fact for fact in stem_facts if fact.kind == "duration_days"]
    answer_date = _normalized_answer_date(normalized_answers)
    if len(date_facts) == 1 and len(duration_facts) == 1 and answer_date is not None:
        anchor = date.fromisoformat(date_facts[0].normalized_value)
        days = int(duration_facts[0].normalized_value)
        expected = anchor - timedelta(days=days)
        replay_kind = "calendar_date_advance_days"
        replay_expected = format_cn_date(expected)
        replay_submitted = format_cn_date(answer_date)
        replay_trace = (
            f"anchor_date={format_cn_date(anchor)}",
            f"advance_days={days}",
            f"python_datetime={anchor.isoformat()}-{days}d={expected.isoformat()}",
            f"expected_answer={format_cn_date(expected)}",
            f"submitted_answer={format_cn_date(answer_date)}",
        )
        replay_pass = expected == answer_date

    if not reasoning_text:
        reason = "reasoning_missing"
    elif not normalized_answers:
        reason = "answer_missing"
    elif not stem_facts:
        reason = "unsupported_or_insufficient_stem_facts"
    elif not binding_complete:
        reason = "stem_fact_not_fully_bound_in_reasoning"
    elif external_dependency:
        reason = "external_doc_dependency_present"
    elif replay_kind is None:
        reason = "deterministic_replay_not_supported"
    elif not replay_pass:
        reason = "deterministic_replay_mismatch"
    else:
        reason = "question_stem_binding_auditable"

    grounded = (
        bool(reasoning_text)
        and bool(normalized_answers)
        and binding_complete
        and replay_pass
        and not external_dependency
    )

    return StemBindingResult(
        stem_facts=stem_facts,
        reasoning_bound_fact_ids=bound_ids,
        unbound_fact_ids=unbound_ids,
        binding_complete=binding_complete,
        calculation_replay_pass=replay_pass,
        replay_kind=replay_kind,
        replay_expected_answer=replay_expected,
        replay_submitted_answer=replay_submitted,
        replay_trace=tuple(replay_trace),
        external_evidence_refs=refs,
        external_evidence_dependency=external_dependency,
        question_stem_grounded=grounded,
        reason=reason,
    )
