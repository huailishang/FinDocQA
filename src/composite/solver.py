"""Composite solver skeletons behind a default-off flag (Lane C, C1 prep).

``CompositeMultiCalcSolver`` and ``CompositeCrossCalcSolver`` assemble the
per-option / cross-document helpers from ``helpers.py`` into solver-shaped
objects. They are deterministic (no LLM, no retrieval, no I/O) and
**default-off**: when ``enabled=False`` they return a disabled sentinel and
never produce an answer or participate in routing.

They are **NOT wired into ``RoutedSolver``**. ``RoutedSolver`` is unchanged
and does not reference these classes. Promoting C1 (wiring a composite solver
into the live route) requires explicit Final Reviewer approval + A/B
evaluation per the dispatch card's Final Integration Rule.

See ``docs/p7d-workstream-c-c1-plan.md`` and the dispatch card Lane C.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .helpers import (
    CrossDocSupportResult,
    DocumentValue,
    OptionCheck,
    OptionCheckResult,
    build_cross_doc_support,
    check_option_against_values,
)


# Config key under the ``composite:`` section that gates C1.
C1_FLAG_KEY = "enable_c1"


def is_c1_enabled(config: Optional[Mapping[str, Any]]) -> bool:
    """Read the composite C1 enable flag from a config mapping; default False.

    Tolerates missing config, a missing ``composite`` section, and non-bool
    values: only an explicit ``True`` enables C1. This mirrors the project's
    conservative coercion pattern (invalid -> off, never silently on).
    """
    if not config:
        return False
    composite = config.get("composite")
    if not isinstance(composite, Mapping):
        return False
    return composite.get(C1_FLAG_KEY) is True


@dataclass(frozen=True)
class CompositeMultiCalcResult:
    """Outcome of a multi-select-plus-calculation composite solve.

    When disabled (``enabled=False``) every field is empty/false and
    ``answer`` is ``""`` so a caller can never mistake a disabled solver for
    one that produced an empty selection.
    """

    enabled: bool
    option_results: Tuple[OptionCheckResult, ...]
    supported_options: Tuple[str, ...]
    unsupported_options: Tuple[str, ...]
    errors: Tuple[str, ...]
    answer: str  # joined supported letters, "" when disabled or no support
    computation_complete: bool
    degraded: bool


@dataclass(frozen=True)
class CompositeCrossCalcResult:
    """Outcome of a cross-document-plus-calculation composite solve."""

    enabled: bool
    support: Optional[CrossDocSupportResult]
    computation_complete: bool
    degraded: bool


class CompositeMultiCalcSolver:
    """Deterministic multi-select-plus-calculation solver skeleton.

    Evaluates each option's numeric condition against computed values and
    selects the supported options. Pure: no LLM, no retrieval, no I/O.

    ``enabled`` defaults to ``False``. When disabled, :meth:`solve` returns a
    disabled sentinel (``answer=""``, ``enabled=False``) and does not
    participate in routing. ``RoutedSolver`` is unchanged.
    """

    name = "composite_multi_calc"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def solve(self, checks: Sequence[OptionCheck]) -> CompositeMultiCalcResult:
        if not self.enabled:
            return CompositeMultiCalcResult(
                enabled=False,
                option_results=(),
                supported_options=(),
                unsupported_options=(),
                errors=(),
                answer="",
                computation_complete=False,
                degraded=False,
            )
        results = [check_option_against_values(c) for c in checks]
        supported = tuple(sorted(r.option_key for r in results if r.supported))
        unsupported = tuple(sorted(r.option_key for r in results if not r.supported))
        errors = tuple(r.error for r in results if r.error)
        answer = "".join(supported)
        # computation_complete: every option evaluated without error.
        computation_complete = not errors
        # degraded: any option had an error (undefined symbol, eval failure).
        degraded = bool(errors)
        return CompositeMultiCalcResult(
            enabled=True,
            option_results=tuple(results),
            supported_options=supported,
            unsupported_options=unsupported,
            errors=errors,
            answer=answer,
            computation_complete=computation_complete,
            degraded=degraded,
        )


class CompositeCrossCalcSolver:
    """Deterministic cross-document-plus-calculation solver skeleton.

    Assembles extracted ``DocumentValue``s into cross-document support
    metadata. Pure: no LLM, no retrieval, no I/O.

    ``enabled`` defaults to ``False``. When disabled, :meth:`solve` returns a
    disabled sentinel.
    """

    name = "composite_cross_calc"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def solve(
        self,
        required_doc_ids: Sequence[str],
        values: Sequence[DocumentValue],
    ) -> CompositeCrossCalcResult:
        if not self.enabled:
            return CompositeCrossCalcResult(
                enabled=False,
                support=None,
                computation_complete=False,
                degraded=False,
            )
        support = build_cross_doc_support(required_doc_ids, values)
        return CompositeCrossCalcResult(
            enabled=True,
            support=support,
            computation_complete=support.computation_complete,
            degraded=support.degraded,
        )
