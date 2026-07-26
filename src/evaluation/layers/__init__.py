"""Layered offline evaluation for FinDocQA modules."""
from .answer_quality import AnswerQualityResult, evaluate_answer
from .parser_quality import ParserGoldPage, ParserQualityResult, evaluate_parser
from .reasoning_quality import ReasoningGold, ReasoningQualityResult, evaluate_reasoning
from .retrieval_quality import RetrievalGold, RetrievalQualityResult, evaluate_retrieval

__all__ = [
    "AnswerQualityResult",
    "ParserGoldPage",
    "ParserQualityResult",
    "ReasoningGold",
    "ReasoningQualityResult",
    "RetrievalGold",
    "RetrievalQualityResult",
    "evaluate_answer",
    "evaluate_parser",
    "evaluate_reasoning",
    "evaluate_retrieval",
]
