"""Final-answer A/B evaluation helpers.

The module keeps Gold truth outside the tested ``Question`` and accepts injected
runners, so the evaluation logic can be tested offline while real model calls
remain an explicit execution concern of the CLI layer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Callable, Mapping, Sequence

from agent.workflow import BlockingAnswerValidationError
from contracts import PipelineResult, Question, result_answer_values
from evaluation.layers import AnswerQualityResult, evaluate_answer


_GOLD_ONLY_KEYS = {
    "doc_ids",
    "candidate_doc_ids",
    "required_doc_ids",
    "required_documents",
    "answer",
    "expected_answer",
    "label",
    "gold",
    "ground_truth",
    "acceptable_page_groups",
    "required_pages",
    "evidence_anchors",
    "evidence_text_anchors",
}


@dataclass(frozen=True)
class AnswerABCase:
    case_id: str
    question: Question
    gold_answers: tuple[str, ...]


@dataclass(frozen=True)
class AnswerABStrategy:
    name: str
    runner: Callable[[Question], PipelineResult]


@dataclass(frozen=True)
class AnswerABCaseMeasurement:
    strategy: str
    case_id: str
    predicted_answers: tuple[str, ...]
    gold_answers: tuple[str, ...]
    slot_quality: tuple[AnswerQualityResult, ...]
    case_exact_match: float
    case_value_correct: float
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    provider_call_count: int
    fallback_used: bool
    blocked: bool = False
    blocking_reason: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "case_id": self.case_id,
            "predicted_answers": list(self.predicted_answers),
            "gold_answers": list(self.gold_answers),
            "slot_quality": [item.to_dict() for item in self.slot_quality],
            "case_exact_match": self.case_exact_match,
            "case_value_correct": self.case_value_correct,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "provider_call_count": self.provider_call_count,
            "fallback_used": self.fallback_used,
            "blocked": self.blocked,
            "blocking_reason": self.blocking_reason,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "AnswerABCaseMeasurement":
        quality_items: list[AnswerQualityResult] = []
        raw_quality = payload.get("slot_quality", ())
        if isinstance(raw_quality, Sequence) and not isinstance(raw_quality, (str, bytes)):
            for item in raw_quality:
                if not isinstance(item, Mapping):
                    continue
                quality_items.append(
                    AnswerQualityResult(
                        exact_match=float(item.get("exact_match", 0.0) or 0.0),
                        set_precision=_optional_float(item.get("set_precision")),
                        set_recall=_optional_float(item.get("set_recall")),
                        set_f1=_optional_float(item.get("set_f1")),
                        numeric_correct=_optional_float(item.get("numeric_correct")),
                    )
                )
        return cls(
            strategy=str(payload.get("strategy") or ""),
            case_id=str(payload.get("case_id") or ""),
            predicted_answers=_string_tuple(payload.get("predicted_answers", ())),
            gold_answers=_string_tuple(payload.get("gold_answers", ())),
            slot_quality=tuple(quality_items),
            case_exact_match=float(payload.get("case_exact_match", 0.0) or 0.0),
            case_value_correct=float(payload.get("case_value_correct", 0.0) or 0.0),
            latency_ms=float(payload.get("latency_ms", 0.0) or 0.0),
            prompt_tokens=int(payload.get("prompt_tokens", 0) or 0),
            completion_tokens=int(payload.get("completion_tokens", 0) or 0),
            total_tokens=int(payload.get("total_tokens", 0) or 0),
            provider_call_count=_inferred_provider_call_count(
                payload.get("provider_call_count"),
                total_tokens=int(payload.get("total_tokens", 0) or 0),
            ),
            fallback_used=bool(payload.get("fallback_used", False)),
            blocked=bool(payload.get("blocked", False)),
            blocking_reason=str(payload.get("blocking_reason") or ""),
            error=str(payload.get("error") or ""),
        )


@dataclass(frozen=True)
class AnswerABStrategySummary:
    name: str
    cases: tuple[AnswerABCaseMeasurement, ...]

    def to_dict(self) -> dict[str, object]:
        successful = tuple(item for item in self.cases if not item.error)
        slot_results = tuple(
            quality
            for item in successful
            for quality in item.slot_quality
        )
        correct_cases = tuple(item for item in successful if item.case_value_correct == 1.0)
        incorrect_cases = tuple(item for item in successful if item.case_value_correct == 0.0)
        correct_but_blocked = sum(item.blocked for item in correct_cases)
        incorrect_but_accepted = sum(not item.blocked for item in incorrect_cases)
        return {
            "strategy": self.name,
            "case_count": len(self.cases),
            "errors": sum(bool(item.error) for item in self.cases),
            "blocked_cases": sum(item.blocked for item in self.cases),
            "accepted_cases": sum(not item.blocked for item in successful),
            "correct_but_blocked_cases": correct_but_blocked,
            "incorrect_but_accepted_cases": incorrect_but_accepted,
            "false_reject_rate_on_correct": (
                correct_but_blocked / len(correct_cases) if correct_cases else None
            ),
            "false_accept_rate_on_incorrect": (
                incorrect_but_accepted / len(incorrect_cases) if incorrect_cases else None
            ),
            "case_exact_match": _mean(
                tuple(item.case_exact_match for item in successful)
            ),
            "case_value_accuracy": _mean(
                tuple(item.case_value_correct for item in successful)
            ),
            "slot_exact_match": _mean(
                tuple(item.exact_match for item in slot_results)
            ),
            "slot_value_accuracy": _mean(
                tuple(_answer_value_correct(item) for item in slot_results)
            ),
            "mean_set_f1": _mean(
                tuple(item.set_f1 for item in slot_results if item.set_f1 is not None)
            ),
            "mean_numeric_correct": _mean(
                tuple(
                    item.numeric_correct
                    for item in slot_results
                    if item.numeric_correct is not None
                )
            ),
            "mean_latency_ms": _mean(tuple(item.latency_ms for item in successful)),
            "prompt_tokens": sum(item.prompt_tokens for item in successful),
            "completion_tokens": sum(item.completion_tokens for item in successful),
            "total_tokens": sum(item.total_tokens for item in successful),
            "provider_call_count": sum(item.provider_call_count for item in successful),
            "fallback_cases": sum(item.fallback_used for item in successful),
            "cases": [item.to_dict() for item in self.cases],
        }


@dataclass(frozen=True)
class AnswerABReport:
    strategies: tuple[AnswerABStrategySummary, ...]

    def to_dict(self) -> dict[str, object]:
        has_errors = any(item.error for strategy in self.strategies for item in strategy.cases)
        return {
            "answer_quality_status": "partial" if has_errors else "completed",
            "strategies": [strategy.to_dict() for strategy in self.strategies],
        }


def _mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


def _inferred_provider_call_count(value: object, *, total_tokens: int) -> int:
    try:
        count = max(0, int(value or 0))
    except (TypeError, ValueError):
        count = 0
    if count == 0 and int(total_tokens or 0) > 0:
        return 1
    return count


def load_answer_ab_checkpoint(path: Path) -> tuple[AnswerABCaseMeasurement, ...]:
    checkpoint = Path(path)
    if not checkpoint.exists():
        return ()
    records: list[AnswerABCaseMeasurement] = []
    for line_number, raw_line in enumerate(
        checkpoint.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid answer A/B checkpoint line {line_number}")
        record = AnswerABCaseMeasurement.from_dict(payload)
        if not record.strategy or not record.case_id:
            raise ValueError(f"answer A/B checkpoint missing strategy/case_id at line {line_number}")
        records.append(record)
    return tuple(records)


def _append_answer_ab_checkpoint(path: Path, record: AnswerABCaseMeasurement) -> None:
    checkpoint = Path(path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def _answer_value_correct(result: AnswerQualityResult) -> float:
    if result.exact_match == 1.0:
        return 1.0
    if result.numeric_correct == 1.0:
        return 1.0
    if result.set_f1 == 1.0:
        return 1.0
    return 0.0


def _provider_call_count(result: PipelineResult) -> int:
    candidates = (
        result.metadata.get("provider_call_count") if result.metadata else None,
        result.solver_result.metadata.get("provider_call_count")
        if result.solver_result.metadata
        else None,
    )
    for value in candidates:
        try:
            if value is not None:
                return _inferred_provider_call_count(value, total_tokens=int(result.total_tokens or 0))
        except (TypeError, ValueError):
            continue
    return _inferred_provider_call_count(0, total_tokens=int(result.total_tokens or 0))


def _score_predicted_answers(
    predicted_answers: tuple[str, ...],
    gold_answers: tuple[str, ...],
) -> tuple[tuple[AnswerQualityResult, ...], float, float]:
    slot_quality = tuple(
        evaluate_answer(
            predicted_answers[index] if index < len(predicted_answers) else "",
            gold,
        )
        for index, gold in enumerate(gold_answers)
    )
    slot_count_matches = len(predicted_answers) == len(gold_answers)
    case_exact_match = float(
        slot_count_matches and all(item.exact_match == 1.0 for item in slot_quality)
    )
    case_value_correct = float(
        slot_count_matches
        and all(_answer_value_correct(item) == 1.0 for item in slot_quality)
    )
    return slot_quality, case_exact_match, case_value_correct


def _blocked_answers(
    exc: BlockingAnswerValidationError,
    *,
    expected_slots: int,
) -> tuple[str, ...]:
    metadata = dict(exc.metadata or {})
    raw_answers = metadata.get("submission_answers", ())
    answers = _string_tuple(raw_answers)
    if expected_slots <= 1:
        answer = str(exc.answer or "").strip()
        return (answer,) if answer else answers[:1]
    return answers


def _normalize_gold_answers(value: object) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        answers = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        raw = str(value or "").strip()
        answers = (raw,) if raw else ()
    if not answers:
        raise ValueError("answer Gold case requires at least one expected answer value")
    return answers


def load_answer_gold_cases(
    path: Path,
    *,
    questions: Sequence[Question],
) -> tuple[AnswerABCase, ...]:
    """Join private answer Gold to visible questions without leaking Gold scope.

    The source ``Question`` supplies the answer/output contract. Retrieval scope
    fields are intentionally cleared so each A/B strategy must rediscover the
    relevant documents from the visible question text.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", []) if isinstance(payload, Mapping) else payload
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
        raise ValueError("answer gold must contain a cases array")

    question_by_qid = {str(question.qid): question for question in questions}
    cases: list[AnswerABCase] = []
    for item in raw_cases:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or item.get("case_id") or "").strip()
        if not qid:
            raise ValueError("each answer Gold case requires qid/case_id")
        source_question = question_by_qid.get(qid)
        if source_question is None:
            raise ValueError(f"answer Gold qid not found in visible questions: {qid}")

        gold_answers = _normalize_gold_answers(
            item.get("expected_answer", item.get("gold_answer"))
        )
        slot_count = source_question.submission_slot_count
        if slot_count is not None and int(slot_count) != len(gold_answers):
            raise ValueError(
                f"answer slot count mismatch for {qid}: "
                f"question={slot_count} gold={len(gold_answers)}"
            )

        sanitized_raw = {
            str(key): value
            for key, value in dict(source_question.raw or {}).items()
            if str(key) not in _GOLD_ONLY_KEYS
        }
        tested_question = replace(
            source_question,
            doc_ids=(),
            candidate_doc_ids=(),
            raw=sanitized_raw,
        )
        cases.append(
            AnswerABCase(
                case_id=qid,
                question=tested_question,
                gold_answers=gold_answers,
            )
        )
    return tuple(cases)


def run_answer_ab(
    cases: Sequence[AnswerABCase],
    *,
    strategies: Sequence[AnswerABStrategy],
    checkpoint_path: Path | None = None,
    prior_measurements: Sequence[AnswerABCaseMeasurement] = (),
) -> AnswerABReport:
    """Compare end-to-end answer quality while varying only the injected strategy.

    Any prior ``(strategy, case_id)`` record is terminal for this run, including
    failures. This fail-closed resume behavior prevents accidental duplicate paid
    calls; intentional retries require removing that checkpoint record first.
    """
    prior_by_key = {
        (item.strategy, item.case_id): item
        for item in prior_measurements
    }
    summaries: list[AnswerABStrategySummary] = []
    for strategy in strategies:
        measurements: list[AnswerABCaseMeasurement] = []
        for case in cases:
            prior = prior_by_key.get((strategy.name, case.case_id))
            if prior is not None:
                measurements.append(prior)
                continue
            started = perf_counter()
            predicted_answers: tuple[str, ...] = ()
            prompt_tokens = completion_tokens = total_tokens = provider_calls = 0
            fallback_used = False
            blocked = False
            blocking_reason = ""
            error = ""
            slot_quality: tuple[AnswerQualityResult, ...] = ()
            case_exact_match = 0.0
            case_value_correct = 0.0
            try:
                result = strategy.runner(case.question)
                predicted_answers = result_answer_values(result)
                prompt_tokens = int(result.prompt_tokens or 0)
                completion_tokens = int(result.completion_tokens or 0)
                total_tokens = int(result.total_tokens or 0)
                provider_calls = _provider_call_count(result)
                fallback_used = bool(result.fallback_used)
                if result.error:
                    error = str(result.error)
                else:
                    slot_quality, case_exact_match, case_value_correct = _score_predicted_answers(
                        predicted_answers, case.gold_answers
                    )
            except BlockingAnswerValidationError as exc:
                blocked = True
                blocking_reason = str(exc.reason or "")
                metadata = dict(exc.metadata or {})
                predicted_answers = _blocked_answers(
                    exc,
                    expected_slots=len(case.gold_answers),
                )
                prompt_tokens = int(metadata.get("actual_prompt_tokens", 0) or 0)
                completion_tokens = int(metadata.get("actual_completion_tokens", 0) or 0)
                total_tokens = int(metadata.get("actual_total_tokens", 0) or 0)
                provider_calls = _inferred_provider_call_count(
                    metadata.get("provider_call_count"),
                    total_tokens=total_tokens,
                )
                solver_metadata = metadata.get("solver_metadata", {})
                if isinstance(solver_metadata, Mapping):
                    fallback_used = bool(solver_metadata.get("composite_fallback_used", False))
                if predicted_answers:
                    slot_quality, case_exact_match, case_value_correct = _score_predicted_answers(
                        predicted_answers, case.gold_answers
                    )
                else:
                    error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # keep the A/B batch comparable across failures
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = (perf_counter() - started) * 1000.0
            record = AnswerABCaseMeasurement(
                strategy=strategy.name,
                case_id=case.case_id,
                predicted_answers=predicted_answers,
                gold_answers=case.gold_answers,
                slot_quality=slot_quality,
                case_exact_match=case_exact_match,
                case_value_correct=case_value_correct,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                provider_call_count=provider_calls,
                fallback_used=fallback_used,
                blocked=blocked,
                blocking_reason=blocking_reason,
                error=error,
            )
            measurements.append(record)
            if checkpoint_path is not None:
                _append_answer_ab_checkpoint(checkpoint_path, record)
        summaries.append(
            AnswerABStrategySummary(name=strategy.name, cases=tuple(measurements))
        )
    return AnswerABReport(strategies=tuple(summaries))
