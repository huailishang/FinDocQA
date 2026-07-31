"""Reliability profiles describe risk-aware evaluation requirements per module."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from evaluation.contracts import GateStatus, _jsonable, _require_id

_ALLOWED_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_ALLOWED_POLICY_KEYS = {
    "metric_failure",
    "missing_required_metric",
    "missing_required_invariant",
}


@dataclass(frozen=True)
class ReliabilityProfile:
    """Module-specific risk and test strategy consumed by the generic gate."""

    module_id: str
    risk_level: str
    failure_modes: tuple[str, ...] = ()
    required_metrics: tuple[str, ...] = ()
    required_invariants: tuple[str, ...] = ()
    test_techniques: tuple[str, ...] = ()
    gate_policy: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_id", _require_id("module_id", self.module_id))
        risk_level = str(self.risk_level or "").strip().upper()
        if risk_level not in _ALLOWED_RISK_LEVELS:
            raise ValueError(f"unsupported risk_level: {self.risk_level}")
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "failure_modes", self._normalized_names(self.failure_modes))
        object.__setattr__(self, "required_metrics", self._normalized_names(self.required_metrics))
        object.__setattr__(self, "required_invariants", self._normalized_names(self.required_invariants))
        object.__setattr__(self, "test_techniques", self._normalized_names(self.test_techniques))

        normalized_policy: dict[str, str] = {}
        for key, raw_status in dict(self.gate_policy).items():
            policy_key = str(key).strip()
            if policy_key not in _ALLOWED_POLICY_KEYS:
                raise ValueError(f"unsupported gate_policy key: {policy_key}")
            status = (
                raw_status
                if isinstance(raw_status, GateStatus)
                else GateStatus(str(raw_status).strip().upper())
            )
            if policy_key in {"metric_failure", "missing_required_metric"}:
                if status not in {GateStatus.REVIEW, GateStatus.FAIL}:
                    raise ValueError(f"{policy_key} must be REVIEW or FAIL")
            elif status is not GateStatus.FAIL:
                raise ValueError("missing_required_invariant must be FAIL")
            normalized_policy[policy_key] = status.value
        object.__setattr__(self, "gate_policy", normalized_policy)

    @staticmethod
    def _normalized_names(values: tuple[str, ...]) -> tuple[str, ...]:
        result: list[str] = []
        for raw in values:
            value = str(raw or "").strip()
            if value and value not in result:
                result.append(value)
        return tuple(result)

    def policy_status(self, key: str, default: GateStatus) -> GateStatus:
        raw = self.gate_policy.get(key)
        return default if raw is None else GateStatus(raw)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))
