"""Generic result/output contracts for FinDocQA.

The core QA pipeline should produce a result independent of any benchmark CSV,
leaderboard schema, HTTP response shape, or UI representation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from contracts import PipelineResult, result_answer_values


@dataclass(frozen=True)
class ResultRecord:
    """Output-neutral representation of one completed QA result."""

    question_id: str
    answer_values: tuple[str, ...]
    primary_answer: str
    reasoning: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_pipeline_result(cls, result: PipelineResult) -> "ResultRecord":
        values = result_answer_values(result)
        primary = values[0] if values else str(result.answer or "").strip()
        return cls(
            question_id=str(result.qid),
            answer_values=values,
            primary_answer=primary,
            reasoning=str(result.reasoning or ""),
            prompt_tokens=int(result.prompt_tokens or 0),
            completion_tokens=int(result.completion_tokens or 0),
            total_tokens=int(result.total_tokens or 0),
            error=str(result.error) if result.error is not None else None,
            metadata=dict(result.metadata or {}),
        )


class OutputAdapter(Protocol):
    """Convert generic ResultRecord objects into one external representation."""

    name: str

    def write(self, results: Sequence[ResultRecord]) -> None:
        ...
