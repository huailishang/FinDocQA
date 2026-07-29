"""Compose C0 question adaptation and C1 query understanding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from contracts import Question
from question.adapter import CanonicalQuestionAdapter
from question.understanding import QueryUnderstandingResult, RuleBasedQueryUnderstanding


@dataclass(frozen=True)
class PreparedQuestion:
    question: Question
    understanding: QueryUnderstandingResult


class QuestionPreparationPipeline:
    """Prepare structured benchmark rows or plain user queries for the core QA chain."""

    def __init__(
        self,
        adapter: CanonicalQuestionAdapter | None = None,
        understander: RuleBasedQueryUnderstanding | None = None,
    ) -> None:
        self.adapter = adapter or CanonicalQuestionAdapter()
        self.understander = understander or RuleBasedQueryUnderstanding()

    def prepare(self, payload: str | Mapping[str, Any]) -> PreparedQuestion:
        normalized = self.adapter.adapt(payload)
        understanding = self.understander.understand(normalized)
        question = self.understander.materialize(normalized, understanding)
        return PreparedQuestion(question=question, understanding=understanding)
