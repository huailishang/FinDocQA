"""Composite solver capability and routing contracts (P7D-C offline foundation).

This module defines parser-neutral, deterministic contracts for *composite*
questions — questions that combine multiple capabilities such as
multi-select plus calculation, or cross-document plus calculation.

The contracts are intentionally separate from the live ``RoutedSolver``:
they describe what a composite solver *would* need, so that routing
decisions and option/document support can be unit-tested without an LLM.
They are NOT wired into the production pipeline.

See ``docs/p7d-workstream-c-implementation.md`` for the design notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Capability(str, Enum):
    """A single question capability that may combine with others."""

    MULTI_CHOICE = "multi_choice"
    CALCULATION = "calculation"
    CROSS_DOC = "cross_doc"


# Recognized composite types. ``none`` means no overlap — the question is
# handled by a single-capability solver and does not need a composite path.
class CompositeType(str, Enum):
    NONE = "none"
    MULTI_CHOICE_PLUS_CALCULATION = "multi_choice+calculation"
    CROSS_DOC_PLUS_CALCULATION = "cross_doc+calculation"


# Map a composite type to the solver that should handle it. ``routed`` is a
# proposed label only — it is NOT wired into ``RoutedSolver``. Promoting a
# composite solver requires Final Reviewer approval + A/B evaluation.
_RECOMMENDED_SOLVER = {
    CompositeType.NONE: "current_router",
    CompositeType.MULTI_CHOICE_PLUS_CALCULATION: "composite_multi_calc",
    CompositeType.CROSS_DOC_PLUS_CALCULATION: "composite_cross_calc",
}


@dataclass(frozen=True)
class CapabilityProfile:
    """Deterministic description of a question's composite capability.

    Built by :func:`classify_composite` from the boolean signals already
    produced by :class:`RuleBasedQuestionClassifier`. The profile is the
    input to routing decisions and to option/document support metadata.

    Attributes:
        capabilities: the set of single capabilities present.
        composite_type: the recognized overlap, or ``NONE``.
        recommended_solver: a *proposed* solver label (not wired in).
    """

    capabilities: frozenset[Capability]
    composite_type: CompositeType
    recommended_solver: str

    @property
    def is_composite(self) -> bool:
        """True when the question combines two or more capabilities."""
        return self.composite_type is not CompositeType.NONE


def classify_composite(
    *,
    is_multi_select: bool,
    needs_calculation: bool,
    is_cross_doc: bool = False,
) -> CapabilityProfile:
    """Build a :class:`CapabilityProfile` from classifier boolean signals.

    This is a pure function: the same inputs always produce the same profile.
    It does not call any LLM, retrieve any evidence, or inspect any document.

    Routing rules (proposed, not wired into ``RoutedSolver``):

    - ``multi_select + calculation`` -> ``composite_multi_calc``
    - ``cross_doc + calculation`` -> ``composite_cross_calc``
    - any other combination -> ``current_router`` (no composite path)

    The current production router priority
    ``multi_choice -> calculation -> cross_doc -> direct`` means a
    multi-select-plus-calculation question is routed to ``MultiChoiceSolver``
    and never sees per-option numeric verification. This contract makes that
    gap explicit so a later composite solver can be evaluated.
    """
    caps: set[Capability] = set()
    if is_multi_select:
        caps.add(Capability.MULTI_CHOICE)
    if needs_calculation:
        caps.add(Capability.CALCULATION)
    if is_cross_doc:
        caps.add(Capability.CROSS_DOC)

    if is_multi_select and needs_calculation:
        ct = CompositeType.MULTI_CHOICE_PLUS_CALCULATION
    elif is_cross_doc and needs_calculation:
        ct = CompositeType.CROSS_DOC_PLUS_CALCULATION
    else:
        ct = CompositeType.NONE

    return CapabilityProfile(
        capabilities=frozenset(caps),
        composite_type=ct,
        recommended_solver=_RECOMMENDED_SOLVER[ct],
    )
