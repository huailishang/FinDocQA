"""Typed contracts for provider-free external Oracle-program evaluation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class TerminalClassification(str, Enum):
    INELIGIBLE_NON_NUMERIC = "INELIGIBLE_NON_NUMERIC"
    UNSUPPORTED_PROGRAM_SCHEMA = "UNSUPPORTED_PROGRAM_SCHEMA"
    UNSUPPORTED_OPERATOR = "UNSUPPORTED_OPERATOR"
    UNSUPPORTED_CONSTANT_OR_ARGUMENT = "UNSUPPORTED_CONSTANT_OR_ARGUMENT"
    UNSUPPORTED_SCALE_OR_UNIT = "UNSUPPORTED_SCALE_OR_UNIT"
    ADAPTER_PARSE_ERROR = "ADAPTER_PARSE_ERROR"
    C3_EXECUTION_ERROR = "C3_EXECUTION_ERROR"
    EXECUTED_CORRECT = "EXECUTED_CORRECT"
    EXECUTED_INCORRECT = "EXECUTED_INCORRECT"


@dataclass(frozen=True)
class RuntimeVariable:
    name: str
    value: str


@dataclass(frozen=True)
class OracleRuntime:
    """Gold-free runtime input passed to the unchanged C3 execution path."""

    dataset: str
    case_id: str
    question: str
    expression: str
    variables: Sequence[RuntimeVariable]
    source_id: str
    native_program: str = ""
    scale: str = ""
    output_multiplier: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "case_id": self.case_id,
            "question": self.question,
            "expression": self.expression,
            "variables": [asdict(item) for item in self.variables],
            "source_id": self.source_id,
            "native_program": self.native_program,
            "scale": self.scale,
            "output_multiplier": self.output_multiplier,
        }


@dataclass(frozen=True)
class OracleLabel:
    """Evaluation-only label; never supplied to C3 runtime construction."""

    answer: Any
    scale: str = ""
    answer_type: str = ""
    native_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OracleCase:
    dataset: str
    case_id: str
    question: str
    numeric_eligible: bool
    runtime: OracleRuntime | None
    label: OracleLabel
    preclassified: TerminalClassification | None = None
    failure_detail: str = ""
    parsed_program_schema: bool = False


@dataclass(frozen=True)
class C3ExecutionObservation:
    ok: bool
    answer: str = ""
    error: str = ""
    provider_call_count: int = 0
    legacy_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    trace: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    source_lineage: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExternalCaseRecord:
    dataset: str
    case_id: str
    numeric_eligible: bool
    terminal_classification: TerminalClassification
    failure_detail: str = ""
    answer_type: str = ""
    scale: str = ""
    predicted_answer: str = ""
    native_prediction_emitted: bool = False
    parsed_program_schema: bool = False
    c3_representable: bool = False
    provider_call_count: int = 0
    legacy_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["terminal_classification"] = self.terminal_classification.value
        return payload


@dataclass(frozen=True)
class SourceManifestEntry:
    dataset_name: str
    official_repository_url: str
    resolved_git_commit: str
    selected_split_path: str
    selected_split_sha256: str
    official_scorer_paths: Sequence[str]
    official_scorer_sha256: Mapping[str, str]
    license_identifier: str
    license_sha256: str
    retrieved_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["official_scorer_paths"] = list(self.official_scorer_paths)
        payload["official_scorer_sha256"] = dict(self.official_scorer_sha256)
        return payload


__all__ = [
    "C3ExecutionObservation",
    "ExternalCaseRecord",
    "OracleCase",
    "OracleLabel",
    "OracleRuntime",
    "RuntimeVariable",
    "SourceManifestEntry",
    "TerminalClassification",
]
