"""Independent bounded oracle for the explicit C3 deterministic pipeline.

The types in this module are evaluation-only.  They deliberately do not import
or call ``ExplicitC3Pipeline``, ``C3InputAssembler`` or ``CalculationSolver`` and
do not inspect production diagnostic strings such as ``answer_source`` or
``audit_reasons``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Any, Mapping


_CANDIDATE_MEMBERSHIP = ("zero", "exactly_one", "duplicate_equal")
_FORMULA_STATE = ("one", "multiple")
_BINDING_STATE = ("complete", "missing", "ambiguous")
_QUESTION_MATCH = ("pass", "fail", "missing")
_UNRELATED_CANDIDATE = ("absent", "present")
_FACTOR_NAMES = (
    "candidate_membership",
    "material_formula_state",
    "semantic_binding_state",
    "question_formula_match",
    "unrelated_bundle_candidate",
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class C3PipelineFactors:
    """One explicit point in the bounded C3 pipeline input space."""

    candidate_membership: str
    material_formula_state: str
    semantic_binding_state: str
    question_formula_match: str
    unrelated_bundle_candidate: str

    def __post_init__(self) -> None:
        allowed = {
            "candidate_membership": set(_CANDIDATE_MEMBERSHIP),
            "material_formula_state": set(_FORMULA_STATE),
            "semantic_binding_state": set(_BINDING_STATE),
            "question_formula_match": set(_QUESTION_MATCH),
            "unrelated_bundle_candidate": set(_UNRELATED_CANDIDATE),
        }
        for name, values in allowed.items():
            value = getattr(self, name)
            if not isinstance(value, str) or value not in values:
                raise ValueError(f"unsupported {name}: {value!r}")

    def values(self) -> tuple[str, ...]:
        return tuple(str(getattr(self, name)) for name in _FACTOR_NAMES)


@dataclass(frozen=True)
class C3PipelineExpectation:
    """Expected authorization decision derived only from explicit factors."""

    should_execute: bool
    trace_required: bool
    lineage_required: bool


@dataclass(frozen=True)
class C3PipelineObservation:
    """Normalized real-pipeline outcome without production diagnostic coupling."""

    executed: bool
    answer: str = ""
    trace: tuple[Any, ...] = ()
    lineage: tuple[Any, ...] = ()
    legacy_call_count: int = 0
    provider_call_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "answer", str(self.answer or ""))
        object.__setattr__(self, "trace", tuple(_freeze(item) for item in self.trace))
        object.__setattr__(self, "lineage", tuple(_freeze(item) for item in self.lineage))
        if self.legacy_call_count < 0 or self.provider_call_count < 0:
            raise ValueError("call counts must be non-negative")
        if self.executed != bool(self.answer.strip()):
            raise ValueError("executed must agree with answer presence")

    def behavior_fingerprint(self) -> tuple[Any, ...]:
        return (self.executed, self.answer, self.trace, self.lineage)


@dataclass(frozen=True)
class C3PipelineDecisionRow:
    case_id: str
    factors: C3PipelineFactors
    expected: C3PipelineExpectation
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        case_id = str(self.case_id or "").strip()
        if not case_id:
            raise ValueError("case_id must be non-empty")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "tags", tuple(str(tag) for tag in self.tags))
        oracle_expected = evaluate_c3_pipeline_factors(self.factors)
        if self.expected != oracle_expected:
            raise ValueError(
                f"row expectation disagrees with independent oracle: {self.expected!r} != {oracle_expected!r}"
            )


def evaluate_c3_pipeline_factors(factors: C3PipelineFactors) -> C3PipelineExpectation:
    """Permit execution only for the single safe factor state."""

    should_execute = bool(
        factors.candidate_membership == "exactly_one"
        and factors.material_formula_state == "one"
        and factors.semantic_binding_state == "complete"
        and factors.question_formula_match == "pass"
    )
    return C3PipelineExpectation(
        should_execute=should_execute,
        trace_required=should_execute,
        lineage_required=should_execute,
    )


def evaluate_c3_pipeline_invariants(
    factors: C3PipelineFactors,
    observation: C3PipelineObservation,
    *,
    baseline_observation: C3PipelineObservation | None = None,
) -> dict[str, bool]:
    """Evaluate safety invariants from explicit factors and normalized behavior."""

    expected = evaluate_c3_pipeline_factors(factors)
    assembly_unsafe = bool(
        factors.material_formula_state != "one"
        or factors.semantic_binding_state != "complete"
    )
    question_non_pass = factors.question_formula_match != "pass"
    candidate_out_of_scope = factors.candidate_membership != "exactly_one"

    successful_result_valid = bool(
        (not expected.should_execute and not observation.executed)
        or (
            expected.should_execute
            and observation.executed
            and bool(observation.trace)
            and bool(observation.lineage)
        )
    )
    unrelated_stable = bool(
        baseline_observation is None
        or baseline_observation.behavior_fingerprint()
        == observation.behavior_fingerprint()
    )

    return {
        "candidate_scope_exactly_once_must_hold": not (
            candidate_out_of_scope and observation.executed
        ),
        "ambiguous_or_incomplete_assembly_must_not_execute": not (
            assembly_unsafe and observation.executed
        ),
        "non_pass_question_match_must_not_execute": not (
            question_non_pass and observation.executed
        ),
        "blocked_path_must_not_call_legacy_or_provider": (
            observation.legacy_call_count == 0 and observation.provider_call_count == 0
        ),
        "successful_result_must_preserve_trace_and_lineage": successful_result_valid,
        "unrelated_bundle_evidence_must_not_change_decision": unrelated_stable,
        "independent_oracle_decision_must_match": (
            observation.executed is expected.should_execute
        ),
    }


def all_valid_c3_pipeline_factors() -> tuple[C3PipelineFactors, ...]:
    """Return the finite V1 factor space; callers must not execute it wholesale."""

    return tuple(
        C3PipelineFactors(*values)
        for values in product(
            _CANDIDATE_MEMBERSHIP,
            _FORMULA_STATE,
            _BINDING_STATE,
            _QUESTION_MATCH,
            _UNRELATED_CANDIDATE,
        )
    )


def _pair_signatures(factors: C3PipelineFactors) -> set[tuple[str, str, str, str]]:
    pairs: set[tuple[str, str, str, str]] = set()
    for left, right in combinations(_FACTOR_NAMES, 2):
        pairs.add(
            (
                left,
                str(getattr(factors, left)),
                right,
                str(getattr(factors, right)),
            )
        )
    return pairs


def _build_pairwise_rows() -> tuple[C3PipelineDecisionRow, ...]:
    candidates = all_valid_c3_pipeline_factors()
    uncovered: set[tuple[str, str, str, str]] = set()
    for factors in candidates:
        uncovered.update(_pair_signatures(factors))

    selected: list[C3PipelineFactors] = []
    remaining = list(candidates)
    while uncovered:
        best = max(
            remaining,
            key=lambda item: (len(_pair_signatures(item) & uncovered), tuple(reversed(item.values()))),
        )
        gain = _pair_signatures(best) & uncovered
        if not gain:
            raise RuntimeError("pairwise builder could not cover the remaining factor pairs")
        selected.append(best)
        uncovered.difference_update(gain)
        remaining.remove(best)

    return tuple(
        C3PipelineDecisionRow(
            case_id=f"pairwise_{index:02d}",
            factors=factors,
            expected=evaluate_c3_pipeline_factors(factors),
            tags=("pairwise",),
        )
        for index, factors in enumerate(selected, start=1)
    )


def _row(case_id: str, factors: C3PipelineFactors, *tags: str) -> C3PipelineDecisionRow:
    return C3PipelineDecisionRow(
        case_id=case_id,
        factors=factors,
        expected=evaluate_c3_pipeline_factors(factors),
        tags=tuple(tags),
    )


C3_PIPELINE_DECISION_TABLE_V1 = (
    _row(
        "safe_single_candidate",
        C3PipelineFactors("exactly_one", "one", "complete", "pass", "absent"),
        "decision_table",
        "safe",
    ),
    _row(
        "candidate_missing",
        C3PipelineFactors("zero", "one", "complete", "pass", "absent"),
        "decision_table",
        "candidate_scope",
    ),
    _row(
        "candidate_duplicate_equal",
        C3PipelineFactors("duplicate_equal", "one", "complete", "pass", "absent"),
        "decision_table",
        "candidate_scope",
    ),
    _row(
        "multiple_material_formulas",
        C3PipelineFactors("exactly_one", "multiple", "complete", "pass", "absent"),
        "decision_table",
        "formula",
    ),
    _row(
        "semantic_binding_missing",
        C3PipelineFactors("exactly_one", "one", "missing", "pass", "absent"),
        "decision_table",
        "binding",
    ),
    _row(
        "semantic_binding_ambiguous",
        C3PipelineFactors("exactly_one", "one", "ambiguous", "pass", "absent"),
        "decision_table",
        "binding",
    ),
    _row(
        "question_formula_match_fail",
        C3PipelineFactors("exactly_one", "one", "complete", "fail", "absent"),
        "decision_table",
        "question_match",
    ),
    _row(
        "question_formula_match_missing",
        C3PipelineFactors("exactly_one", "one", "complete", "missing", "absent"),
        "decision_table",
        "question_match",
    ),
    _row(
        "safe_with_unrelated_candidate",
        C3PipelineFactors("exactly_one", "one", "complete", "pass", "present"),
        "decision_table",
        "metamorphic",
    ),
)


C3_PIPELINE_PAIRWISE_CASES_V1 = _build_pairwise_rows()


C3_PIPELINE_SELECTED_3WAY_CASES_V1 = (
    _row(
        "duplicate_multiple_missing_question",
        C3PipelineFactors("duplicate_equal", "multiple", "complete", "missing", "present"),
        "selected_3way",
        "candidate_formula_question",
    ),
    _row(
        "missing_candidate_ambiguous_binding_failed_question",
        C3PipelineFactors("zero", "one", "ambiguous", "fail", "present"),
        "selected_3way",
        "candidate_binding_question",
    ),
    _row(
        "multiple_formula_ambiguous_binding_unrelated",
        C3PipelineFactors("exactly_one", "multiple", "ambiguous", "pass", "present"),
        "selected_3way",
        "formula_binding_unrelated",
    ),
    _row(
        "duplicate_missing_binding_failed_question",
        C3PipelineFactors("duplicate_equal", "one", "missing", "fail", "absent"),
        "selected_3way",
        "candidate_binding_question",
    ),
)


__all__ = [
    "C3PipelineDecisionRow",
    "C3PipelineExpectation",
    "C3PipelineFactors",
    "C3PipelineObservation",
    "C3_PIPELINE_DECISION_TABLE_V1",
    "C3_PIPELINE_PAIRWISE_CASES_V1",
    "C3_PIPELINE_SELECTED_3WAY_CASES_V1",
    "all_valid_c3_pipeline_factors",
    "evaluate_c3_pipeline_factors",
    "evaluate_c3_pipeline_invariants",
]
