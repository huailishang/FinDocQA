"""Layered offline evaluation for FinDocQA modules."""
from .answer_quality import AnswerQualityResult, evaluate_answer
from .parser_quality import ParserGoldPage, ParserQualityResult, evaluate_parser
from .reasoning_quality import ReasoningGold, ReasoningQualityResult, evaluate_reasoning
from .retrieval_benchmark import (
    RetrievalBenchmarkCase,
    RetrievalBenchmarkStrategy,
    RetrievalBenchmarkSummary,
    run_retrieval_benchmark,
)
from .retrieval_quality import (
    RetrievalGold,
    RetrievalQualityResult,
    RetrievalStrategyResult,
    evaluate_retrieval,
    evaluate_retrieval_strategy,
)

__all__ = [
    "AnswerQualityResult",
    "ParserGoldPage",
    "ParserQualityResult",
    "ReasoningGold",
    "ReasoningQualityResult",
    "RetrievalBenchmarkCase",
    "RetrievalBenchmarkStrategy",
    "RetrievalBenchmarkSummary",
    "RetrievalGold",
    "RetrievalQualityResult",
    "RetrievalStrategyResult",
    "evaluate_answer",
    "evaluate_parser",
    "evaluate_reasoning",
    "evaluate_retrieval",
    "evaluate_retrieval_strategy",
    "run_retrieval_benchmark",
]
