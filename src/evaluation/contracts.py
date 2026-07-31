"""Module-agnostic contracts for FinDocQA evaluation and reliability checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping


class GateStatus(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class MetricSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class MetricKind(str, Enum):
    METRIC = "METRIC"
    INVARIANT = "INVARIANT"


def _jsonable(value: Any) -> Any:
    """Convert common contract values into deterministic JSON-friendly objects."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _require_id(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


@dataclass(frozen=True)
class EvaluationCase:
    """One offline evaluation case, independent from any business module schema."""

    case_id: str
    module_id: str
    input: Any = None
    expected: Any = None
    oracle_ref: str | None = None
    tags: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    slice: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _require_id("case_id", self.case_id))
        object.__setattr__(self, "module_id", _require_id("module_id", self.module_id))
        object.__setattr__(self, "tags", tuple(str(item) for item in self.tags))
        object.__setattr__(self, "risk_tags", tuple(str(item) for item in self.risk_tags))
        object.__setattr__(self, "provenance", dict(self.provenance))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class EvaluationObservation:
    """Normalized observation of what one business module actually produced."""

    module_id: str
    case_id: str
    output: Any = None
    status: str = ""
    trace: Any = None
    lineage: Any = None
    latency_ms: float | None = None
    token_usage: Mapping[str, Any] = field(default_factory=dict)
    cost: float | None = None
    failure: Any = None
    runtime: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_id", _require_id("module_id", self.module_id))
        object.__setattr__(self, "case_id", _require_id("case_id", self.case_id))
        object.__setattr__(self, "token_usage", dict(self.token_usage))
        object.__setattr__(self, "runtime", dict(self.runtime))
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.cost is not None and self.cost < 0:
            raise ValueError("cost must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class MetricResult:
    """One numeric metric or boolean invariant result."""

    metric_name: str
    value: Any
    kind: MetricKind = MetricKind.METRIC
    threshold: float | int | None = None
    passed: bool | None = None
    severity: MetricSeverity = MetricSeverity.WARNING
    details: Mapping[str, Any] = field(default_factory=dict)
    comparison: str = ">="

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_name", _require_id("metric_name", self.metric_name))
        object.__setattr__(self, "kind", MetricKind(self.kind))
        object.__setattr__(self, "severity", MetricSeverity(self.severity))
        object.__setattr__(self, "details", dict(self.details))
        if self.comparison not in {">=", ">", "<=", "<", "=="}:
            raise ValueError(f"unsupported comparison: {self.comparison}")

        if self.kind is MetricKind.INVARIANT:
            if not isinstance(self.value, bool):
                raise ValueError("invariant value must be bool")
            if not isinstance(self.passed, bool):
                raise ValueError("invariant passed must be bool")
            if self.threshold is not None:
                raise ValueError("invariant threshold must be None")
            if self.comparison != "==":
                raise ValueError("invariant comparison must be ==")
            if self.value is not self.passed:
                raise ValueError("invariant value and passed must agree")
            return

        if self.threshold is not None:
            if self.value is not None and (
                isinstance(self.value, bool) or not isinstance(self.value, (int, float))
            ):
                raise ValueError("threshold metric value must be numeric or None")
            expected = False if self.value is None else _compare_numeric(
                self.value,
                self.threshold,
                self.comparison,
            )
            if self.passed is None:
                object.__setattr__(self, "passed", expected)
            elif self.passed is not expected:
                raise ValueError("passed is inconsistent with value / threshold / comparison")

    @classmethod
    def threshold_metric(
        cls,
        metric_name: str,
        *,
        value: float | int | None,
        threshold: float | int,
        comparison: str = ">=",
        severity: MetricSeverity = MetricSeverity.WARNING,
        details: Mapping[str, Any] | None = None,
    ) -> "MetricResult":
        passed = False if value is None else _compare_numeric(value, threshold, comparison)
        return cls(
            metric_name=metric_name,
            value=value,
            kind=MetricKind.METRIC,
            threshold=threshold,
            passed=passed,
            severity=severity,
            details=dict(details or {}),
            comparison=comparison,
        )

    @classmethod
    def invariant(
        cls,
        metric_name: str,
        *,
        passed: bool,
        details: Mapping[str, Any] | None = None,
        severity: MetricSeverity = MetricSeverity.CRITICAL,
    ) -> "MetricResult":
        return cls(
            metric_name=metric_name,
            value=bool(passed),
            kind=MetricKind.INVARIANT,
            passed=bool(passed),
            severity=severity,
            details=dict(details or {}),
            comparison="==",
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _compare_numeric(value: float | int, threshold: float | int, comparison: str) -> bool:
    if comparison == ">=":
        return value >= threshold
    if comparison == ">":
        return value > threshold
    if comparison == "<=":
        return value <= threshold
    if comparison == "<":
        return value < threshold
    if comparison == "==":
        return value == threshold
    raise ValueError(f"unsupported comparison: {comparison}")


@dataclass(frozen=True)
class EvaluationResult:
    """All metric and invariant observations for one evaluation case."""

    case_id: str
    module_id: str
    metrics: tuple[MetricResult, ...] = ()
    violations: tuple[str, ...] = ()
    gate_status: GateStatus | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _require_id("case_id", self.case_id))
        object.__setattr__(self, "module_id", _require_id("module_id", self.module_id))
        object.__setattr__(self, "metrics", tuple(self.metrics))
        object.__setattr__(self, "violations", tuple(str(item) for item in self.violations))
        object.__setattr__(self, "diagnostics", dict(self.diagnostics))
        if self.gate_status is not None:
            object.__setattr__(self, "gate_status", GateStatus(self.gate_status))

    def metric(self, metric_name: str) -> MetricResult | None:
        return next((item for item in self.metrics if item.metric_name == metric_name), None)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))
