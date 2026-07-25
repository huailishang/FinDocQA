"""Reusable question-stem semantics for evidence-backed multi-select closure.

This module does not know qids or expected answers.  It only interprets a small
set of selection-stem contracts and option-level evidence states.  Unsupported
question shapes and ordinary unresolved facts remain fail-closed.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping


class SelectionStemMode(str, Enum):
    EXPLICIT_ATTRIBUTE = "EXPLICIT_ATTRIBUTE_SELECTION"
    REASONABLE_ANALYSIS = "REASONABLE_ANALYSIS_SELECTION"
    UNSUPPORTED = "UNSUPPORTED_SELECTION_STEM"


class OptionEvidenceState(str, Enum):
    SUPPORT = "SUPPORT"
    REFUTE = "REFUTE"
    EXPLICIT_OTHER_VALUE = "EXPLICIT_OTHER_VALUE"
    NO_EXPLICIT_TARGET = "NO_EXPLICIT_TARGET"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class OptionSelectionDecision:
    option: str
    select: bool | None
    decision: str
    evidence_state: str
    strong_assertion: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SelectionClosureResult:
    mode: str
    selected_options: tuple[str, ...]
    excluded_options: tuple[str, ...]
    unresolved_options: tuple[str, ...]
    decisions: tuple[OptionSelectionDecision, ...]
    complete: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "selected_options": list(self.selected_options),
            "excluded_options": list(self.excluded_options),
            "unresolved_options": list(self.unresolved_options),
            "decisions": [row.to_dict() for row in self.decisions],
            "complete": self.complete,
            "reason": self.reason,
        }


_EXPLICIT_STEM = re.compile(r"明确(?:约定|规定|写明|载明)")
_REASONABLE_STEM = re.compile(r"(?:哪些|下列哪些).{0,20}(?:合理|正确|成立)")
_STRONG_ASSERTION = re.compile(
    r"(?:主要由|主要因为|主要源于|主要归因于|主要反映|导致|造成|表明|证明|必然|一定)"
)


def detect_selection_stem_mode(question: object) -> SelectionStemMode:
    text = re.sub(r"\s+", "", str(question or ""))
    if _EXPLICIT_STEM.search(text):
        return SelectionStemMode.EXPLICIT_ATTRIBUTE
    if _REASONABLE_STEM.search(text):
        return SelectionStemMode.REASONABLE_ANALYSIS
    return SelectionStemMode.UNSUPPORTED


def is_strong_assertion(option_text: object) -> bool:
    return bool(_STRONG_ASSERTION.search(re.sub(r"\s+", "", str(option_text or ""))))


def close_selection_by_stem(
    *,
    question: object,
    option_texts: Mapping[str, object],
    evidence_states: Mapping[str, str],
) -> SelectionClosureResult:
    """Close a multi-select question only when its stem semantics justify it.

    Rules:
    * explicit-attribute selection: an option that does not explicitly state the
      requested attribute may be excluded by the stem's explicitness condition;
    * reasonable-analysis selection: selected claims require SUPPORT.  A strong
      causal/necessity assertion with no supporting evidence may be excluded as
      NOT_SUPPORTED_STRONG_ASSERTION, but a plain unresolved fact remains
      unresolved rather than being silently treated as false.
    """

    mode = detect_selection_stem_mode(question)
    decisions: list[OptionSelectionDecision] = []

    for option in sorted(option_texts):
        raw_state = str(evidence_states.get(option, OptionEvidenceState.UNRESOLVED.value))
        try:
            state = OptionEvidenceState(raw_state)
        except ValueError:
            state = OptionEvidenceState.UNRESOLVED
        strong = is_strong_assertion(option_texts[option])

        if state is OptionEvidenceState.SUPPORT:
            decision = OptionSelectionDecision(
                option, True, "SELECT_SUPPORT", state.value, strong, "positive evidence supports the option"
            )
        elif state in {OptionEvidenceState.REFUTE, OptionEvidenceState.EXPLICIT_OTHER_VALUE}:
            decision = OptionSelectionDecision(
                option, False, "EXCLUDE_REFUTED", state.value, strong, "direct evidence contradicts the option or states another value"
            )
        elif mode is SelectionStemMode.EXPLICIT_ATTRIBUTE and state is OptionEvidenceState.NO_EXPLICIT_TARGET:
            decision = OptionSelectionDecision(
                option,
                False,
                "REFUTE_BY_QUESTION_STEM_EXPLICITNESS",
                state.value,
                strong,
                "the stem requires an explicit statement and the option's own source does not explicitly state the target attribute",
            )
        elif (
            mode is SelectionStemMode.REASONABLE_ANALYSIS
            and state is OptionEvidenceState.UNRESOLVED
            and strong
        ):
            decision = OptionSelectionDecision(
                option,
                False,
                "NOT_SUPPORTED_STRONG_ASSERTION",
                state.value,
                strong,
                "a strong causal/necessity assertion lacks the positive support required by a reasonable-analysis selection stem",
            )
        else:
            decision = OptionSelectionDecision(
                option,
                None,
                "UNRESOLVED_FAIL_CLOSED",
                state.value,
                strong,
                "the available evidence and question-stem semantics do not justify selecting or excluding this option",
            )
        decisions.append(decision)

    selected = tuple(row.option for row in decisions if row.select is True)
    excluded = tuple(row.option for row in decisions if row.select is False)
    unresolved = tuple(row.option for row in decisions if row.select is None)
    complete = mode is not SelectionStemMode.UNSUPPORTED and not unresolved and bool(decisions)
    reason = "selection_stem_semantic_closure_pass" if complete else "selection_stem_semantic_closure_unresolved"
    return SelectionClosureResult(mode.value, selected, excluded, unresolved, tuple(decisions), complete, reason)
