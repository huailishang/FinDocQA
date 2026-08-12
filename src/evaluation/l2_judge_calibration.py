"""Offline, provider-agnostic calibration accounting for L2 semantic judges.

The harness validates already-produced judge records. It does not generate
semantic decisions itself and deliberately reuses the repository evaluation
case/observation contracts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from evaluation.contracts import EvaluationCase, EvaluationObservation


CORRECT = "CORRECT"
INCORRECT = "INCORRECT"
CANNOT_ASSESS = "CANNOT_ASSESS"

REAL_CALIBRATION = "REAL_CALIBRATION"
TRUST_TEST = "TRUST_TEST"

_L2_LABELS = frozenset({CORRECT, INCORRECT, CANNOT_ASSESS})
_REAL_REFERENCE_LABELS = frozenset({CORRECT, INCORRECT})
_VALID_STRATA = frozenset({REAL_CALIBRATION, TRUST_TEST})
_REQUIRED_AUDIT_FIELDS = (
    "decision",
    "reason_code",
    "short_rationale",
    "required_gold_elements_covered",
    "contradiction_found",
    "uncertainty_reason",
)


@dataclass(frozen=True)
class L2JudgeCalibrationSummary:
    """Deterministic aggregate metrics for offline L2 judge calibration."""

    real_total: int
    judge_decided: int
    judge_abstain: int
    judge_coverage: float
    agreement: float
    false_accept: int
    false_reject: int
    cannot_assess: int
    unresolved_rate: float
    trust_total: int
    trust_pass: int
    trust_fail: int
    trust_abstain: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _index_unique_cases(cases: Sequence[EvaluationCase]) -> dict[str, EvaluationCase]:
    indexed: dict[str, EvaluationCase] = {}
    for case in cases:
        if not isinstance(case, EvaluationCase):
            raise TypeError("cases must contain EvaluationCase records")
        if case.case_id in indexed:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        if case.slice not in _VALID_STRATA:
            raise ValueError(f"invalid or missing calibration stratum for {case.case_id}: {case.slice!r}")
        if case.slice == REAL_CALIBRATION and case.expected not in _REAL_REFERENCE_LABELS:
            raise ValueError(
                f"REAL_CALIBRATION reference must be CORRECT or INCORRECT for {case.case_id}"
            )
        if case.slice == TRUST_TEST and case.expected not in _L2_LABELS:
            raise ValueError(f"TRUST_TEST expected decision is invalid for {case.case_id}")
        indexed[case.case_id] = case
    return indexed


def _index_unique_observations(
    observations: Sequence[EvaluationObservation],
) -> dict[str, EvaluationObservation]:
    indexed: dict[str, EvaluationObservation] = {}
    for observation in observations:
        if not isinstance(observation, EvaluationObservation):
            raise TypeError("observations must contain EvaluationObservation records")
        if observation.case_id in indexed:
            raise ValueError(f"duplicate observation case_id: {observation.case_id}")
        indexed[observation.case_id] = observation
    return indexed


def _validated_output(observation: EvaluationObservation) -> Mapping[str, Any]:
    output = observation.output
    if not isinstance(output, Mapping):
        raise TypeError(f"observation output must be a mapping for {observation.case_id}")

    missing = [field for field in _REQUIRED_AUDIT_FIELDS if field not in output]
    if missing:
        raise ValueError(f"missing required audit fields for {observation.case_id}: {missing}")

    decision = output["decision"]
    if decision not in _L2_LABELS:
        raise ValueError(f"invalid L2 decision for {observation.case_id}: {decision!r}")

    for field in ("reason_code", "short_rationale"):
        value = output[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string for {observation.case_id}")

    if output["required_gold_elements_covered"] is None:
        raise ValueError(
            f"required_gold_elements_covered must be recorded for {observation.case_id}"
        )
    if not isinstance(output["contradiction_found"], bool):
        raise TypeError(f"contradiction_found must be bool for {observation.case_id}")

    uncertainty_reason = output["uncertainty_reason"]
    if not isinstance(uncertainty_reason, str):
        raise TypeError(f"uncertainty_reason must be str for {observation.case_id}")
    if decision == CANNOT_ASSESS and not uncertainty_reason.strip():
        raise ValueError(
            f"CANNOT_ASSESS requires non-empty uncertainty_reason for {observation.case_id}"
        )

    return output


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def evaluate_l2_judge_calibration(
    cases: Sequence[EvaluationCase],
    observations: Sequence[EvaluationObservation],
) -> L2JudgeCalibrationSummary:
    """Validate offline L2 records and compute frozen real/trust metrics.

    Case identity is keyed by ``case_id``. REAL_CALIBRATION and TRUST_TEST are
    accounted independently; trust rows never enter real metric denominators.
    Malformed, missing, extra, or mismatched records fail closed.
    """
    case_index = _index_unique_cases(tuple(cases))
    observation_index = _index_unique_observations(tuple(observations))

    case_ids = set(case_index)
    observation_ids = set(observation_index)
    missing_observations = sorted(case_ids - observation_ids)
    extra_observations = sorted(observation_ids - case_ids)
    if missing_observations:
        raise ValueError(f"missing observations for cases: {missing_observations}")
    if extra_observations:
        raise ValueError(f"extra observations without cases: {extra_observations}")

    real_total = 0
    judge_decided = 0
    judge_abstain = 0
    agreement_count = 0
    false_accept = 0
    false_reject = 0
    cannot_assess = 0

    trust_total = 0
    trust_pass = 0
    trust_abstain = 0

    for case_id, case in case_index.items():
        observation = observation_index[case_id]
        if observation.module_id != case.module_id:
            raise ValueError(
                f"module_id mismatch for {case_id}: "
                f"case={case.module_id!r}, observation={observation.module_id!r}"
            )
        output = _validated_output(observation)
        decision = output["decision"]

        if case.slice == REAL_CALIBRATION:
            real_total += 1
            if decision == CANNOT_ASSESS:
                judge_abstain += 1
                cannot_assess += 1
                continue

            judge_decided += 1
            if decision == case.expected:
                agreement_count += 1
            elif case.expected == INCORRECT and decision == CORRECT:
                false_accept += 1
            elif case.expected == CORRECT and decision == INCORRECT:
                false_reject += 1
            continue

        trust_total += 1
        if decision == case.expected:
            trust_pass += 1
        if decision == CANNOT_ASSESS:
            trust_abstain += 1

    return L2JudgeCalibrationSummary(
        real_total=real_total,
        judge_decided=judge_decided,
        judge_abstain=judge_abstain,
        judge_coverage=_ratio(judge_decided, real_total),
        agreement=_ratio(agreement_count, real_total),
        false_accept=false_accept,
        false_reject=false_reject,
        cannot_assess=cannot_assess,
        unresolved_rate=_ratio(cannot_assess, real_total),
        trust_total=trust_total,
        trust_pass=trust_pass,
        trust_fail=trust_total - trust_pass,
        trust_abstain=trust_abstain,
    )
