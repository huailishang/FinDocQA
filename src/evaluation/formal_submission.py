"""Formal multi-slot submission compliance contracts for the 2026-07-23 R2 rules."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence


FORMAL_SUBMISSION_HEADER = (
    "qid",
    "answer_1",
    "answer_2",
    "answer_3",
    "answer_4",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning",
)

# The organizer-provided input/template historically used the same underscore
# answer columns without the new reasoning field. It remains schema-only input.
LEGACY_TEMPLATE_HEADER = (
    "qid",
    "answer_1",
    "answer_2",
    "answer_3",
    "answer_4",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)

# An earlier local scored-writer draft used answer1..answer4. R2 forbids emitting
# this header, but it can still be read as historical schema metadata.
LEGACY_SCORING_HEADER = (
    "qid",
    "answer1",
    "answer2",
    "answer3",
    "answer4",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning",
)

COMPATIBLE_TEMPLATE_HEADERS = (
    LEGACY_TEMPLATE_HEADER,
    LEGACY_SCORING_HEADER,
    FORMAL_SUBMISSION_HEADER,
)

FORMAL_MODEL_FAMILIES = ("qwen3.7", "qwen3.6", "qwen3.5")
FORMAL_PROVIDER_ALLOWLIST_ENV = "FINDOCQA_FORMAL_PROVIDER_ALLOWLIST"
FORMAL_EXECUTION_ENV = "FINDOCQA_FORMAL_EXECUTION"


class FormalSubmissionError(ValueError):
    """Fail-closed formal submission contract violation."""


class EmptyVisibleOutputError(FormalSubmissionError):
    """The provider completed but exposed no submission-visible message.content."""

    failure_class = "EMPTY_VISIBLE_OUTPUT"


class FormalRouteError(FormalSubmissionError):
    """Formal model/provider route is not allowlisted."""


@dataclass(frozen=True)
class ReasoningValidation:
    valid: bool
    reason: str
    normalized: str
    length: int
    violations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["violations"] = list(self.violations)
        return payload


@dataclass(frozen=True)
class FormalModelOutput:
    answers: tuple[str, ...]
    reasoning: str
    raw_visible_content: str


_PLACEHOLDER_MARKERS = (
    "n/a",
    "unknown",
    "unable",
    "placeholder",
    "error",
    "无法确定",
    "无法判断",
    "无法计算",
    "证据不足",
    "未知",
    "待补充",
    "占位",
)

_EXPLICIT_LETTER_CONCLUSION = re.compile(
    r"(?:答案|结论|正确选项|应选)\s*(?:是|为|[:：])?\s*([A-D]+)",
    re.IGNORECASE,
)
_DOC_ALIAS = re.compile(r"\[?DOC:\d+\]?", re.IGNORECASE)
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?%?")
_CONCLUSION_CUES = (
    "因此", "所以", "故", "结论", "最终", "可得", "得到", "应选", "选择", "答案", "结果",
)
_FACT_CUES = (
    "规定", "条款", "事实", "数值", "金额", "比例", "期限", "条件", "要求", "规则", "指标",
    "属于", "不属于", "支持", "排除", "符合", "不符合", "必须", "不得", "可以", "显示", "表明",
    "收入", "费用", "利润", "保费", "赔付", "责任", "监管", "合同", "报告",
)
_CALC_CUES = (
    "公式", "计算", "加总", "合计", "差额", "比例", "除以", "乘以", "减去", "加上", "得到", "可得",
    "=", "+", "-", "×", "÷", "/", "*",
)


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _normalized_answer_letters(answers: Sequence[str]) -> str:
    if len(answers) != 1:
        return ""
    value = str(answers[0] or "").strip().upper()
    if value and all(ch in "ABCD" for ch in value):
        return "".join(ch for ch in "ABCD" if ch in set(value))
    return ""


def _as_decimal(value: str) -> Decimal | None:
    raw = str(value or "").strip().replace(",", "")
    if raw.endswith("%"):
        raw = raw[:-1]
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _answer_is_mentioned(reasoning: str, answer: str) -> bool:
    text = str(reasoning or "")
    value = str(answer or "").strip()
    if not value:
        return False
    compact_text = _compact_text(text).upper()
    compact_value = _compact_text(value).upper()
    if compact_value and compact_value in compact_text:
        return True

    answer_decimal = _as_decimal(value)
    if answer_decimal is not None:
        for match in _NUMBER.findall(text):
            candidate = _as_decimal(match)
            if candidate is not None and candidate == answer_decimal:
                return True
    return False


def validate_reasoning_contract(
    reasoning: Any,
    *,
    answers: Sequence[str] = (),
    min_chars: int = 20,
) -> ReasoningValidation:
    """Validate the visible, auditable reasoning summary at the hard-contract layer.

    This validates a concise rationale, not hidden chain-of-thought. It rejects
    missing/short text, obvious placeholders, pure answer restatements, and an
    explicit multiple-choice conclusion that contradicts the submitted answer.
    """

    text = str(reasoning or "").strip()
    violations: list[str] = []
    if not text:
        violations.append("reasoning_missing")
    if len(text) < int(min_chars):
        violations.append("reasoning_too_short")

    lowered = text.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        violations.append("reasoning_placeholder_or_error")

    compact = _compact_text(text)
    answer_compact = "".join(_compact_text(value) for value in answers)
    answer_only_forms = {
        answer_compact,
        f"答案{answer_compact}",
        f"答案是{answer_compact}",
        f"答案为{answer_compact}",
        f"结论{answer_compact}",
        f"结论是{answer_compact}",
        f"结论为{answer_compact}",
        f"最终答案{answer_compact}",
    }
    if answer_compact and compact in answer_only_forms:
        violations.append("reasoning_is_answer_restatement")

    expected_letters = _normalized_answer_letters(tuple(str(value) for value in answers))
    if expected_letters:
        explicit = {
            "".join(ch for ch in "ABCD" if ch in set(match.group(1).upper()))
            for match in _EXPLICIT_LETTER_CONCLUSION.finditer(text)
        }
        explicit.discard("")
        if any(candidate != expected_letters for candidate in explicit):
            violations.append("reasoning_explicit_answer_contradiction")

    unique = tuple(dict.fromkeys(violations))
    return ReasoningValidation(
        valid=not unique,
        reason="valid_reasoning" if not unique else unique[0],
        normalized=text,
        length=len(text),
        violations=unique,
    )


def validate_reasoning_self_contained(
    reasoning: Any,
    *,
    answers: Sequence[str] = (),
    question_type: Any = "",
    expected_slots: int | None = None,
) -> ReasoningValidation:
    """R2 Canary quality gate for a reasoning-only judge.

    The judge receives only the reasoning field. This deterministic audit cannot
    prove semantic truth, but it blocks the known low-quality shapes: DOC-only
    lineage notes, missing concrete facts, missing conclusion/result, calculation
    summaries without numeric/relationship detail, and multi-slot summaries that
    omit one or more returned slot values.
    """

    basic = validate_reasoning_contract(reasoning, answers=answers)
    text = basic.normalized
    violations = list(basic.violations)
    without_alias = _DOC_ALIAS.sub("", text)
    compact_without_alias = re.sub(r"[\s\[\]（）()，,。.;；:：]+", "", without_alias)
    qtype = str(question_type or "").strip().lower()
    slots = int(expected_slots if expected_slots is not None else max(1, len(tuple(answers)) or 1))
    is_calculation = any(marker in qtype for marker in ("计算", "calculation"))
    normalized_answers = tuple(str(value).strip() for value in answers if str(value).strip())
    has_formula_or_relation = any(cue in without_alias for cue in _CALC_CUES)
    all_results_explicit = bool(normalized_answers) and all(
        _answer_is_mentioned(without_alias, value) for value in normalized_answers
    )

    if len(compact_without_alias) < 20:
        violations.append("reasoning_lineage_only_or_fact_too_thin")
    if not (_NUMBER.search(without_alias) or any(cue in without_alias for cue in _FACT_CUES)):
        violations.append("reasoning_missing_concrete_fact")
    if not any(cue in without_alias for cue in _CONCLUSION_CUES):
        # A calculation whose formula/relationship visibly ends in every submitted
        # numeric result is already an explicit conclusion; forcing a template word
        # such as "因此" or "最终" would reject valid, self-contained arithmetic.
        if not (is_calculation and has_formula_or_relation and all_results_explicit):
            violations.append("reasoning_missing_explicit_conclusion")

    if is_calculation:
        if not _NUMBER.search(without_alias):
            violations.append("reasoning_calculation_missing_numeric_input")
        if not has_formula_or_relation:
            violations.append("reasoning_calculation_missing_formula_or_relation")

    if normalized_answers:
        missing_answers = [value for value in normalized_answers if not _answer_is_mentioned(without_alias, value)]
        if slots > 1 and missing_answers:
            violations.append("reasoning_multi_slot_result_not_fully_covered")
        elif slots == 1 and missing_answers:
            violations.append("reasoning_final_result_not_explicit")

    unique = tuple(dict.fromkeys(violations))
    return ReasoningValidation(
        valid=not unique,
        reason="valid_reasoning_self_contained" if not unique else unique[0],
        normalized=text,
        length=len(text),
        violations=unique,
    )


def parse_formal_model_output(content: Any, *, expected_slots: int) -> FormalModelOutput:
    """Parse the submission-visible JSON contract: answers + reasoning.

    Only extracted ``message.content`` may be passed here. Hidden
    ``reasoning_content`` is never an admissible substitute for visible content.
    """

    text = str(content or "").strip()
    if not text:
        raise EmptyVisibleOutputError("EMPTY_VISIBLE_OUTPUT: formal message.content is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FormalSubmissionError("formal visible content must be strict JSON") from exc
    if not isinstance(payload, Mapping):
        raise FormalSubmissionError("formal visible content must be a JSON object")
    if set(payload) != {"answers", "reasoning"}:
        raise FormalSubmissionError("formal visible JSON must contain exactly answers and reasoning")
    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, list) or any(not isinstance(value, str) for value in raw_answers):
        raise FormalSubmissionError("formal answers must be an array of strings")
    if len(raw_answers) != int(expected_slots):
        raise FormalSubmissionError(
            f"formal answer slot mismatch: expected={expected_slots} actual={len(raw_answers)}"
        )
    answers = tuple(value.strip() for value in raw_answers)
    if any(not value for value in answers):
        raise FormalSubmissionError("formal answers must not contain empty values")
    reasoning_check = validate_reasoning_contract(payload.get("reasoning"), answers=answers)
    if not reasoning_check.valid:
        raise FormalSubmissionError(f"formal reasoning invalid: {reasoning_check.reason}")
    return FormalModelOutput(
        answers=answers,
        reasoning=reasoning_check.normalized,
        raw_visible_content=text,
    )


def normalize_provider_allowlist(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(value).strip().lower() for value in values if str(value).strip()))
    return normalized


def provider_allowlist_from_env() -> tuple[str, ...]:
    raw = os.getenv(FORMAL_PROVIDER_ALLOWLIST_ENV, "").strip()
    if not raw:
        return ()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FormalRouteError(f"invalid {FORMAL_PROVIDER_ALLOWLIST_ENV} JSON") from exc
        if not isinstance(parsed, list):
            raise FormalRouteError(f"{FORMAL_PROVIDER_ALLOWLIST_ENV} JSON must be an array")
        return normalize_provider_allowlist(str(value) for value in parsed)
    return normalize_provider_allowlist(raw.split(","))


def formal_execution_enabled() -> bool:
    return os.getenv(FORMAL_EXECUTION_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def model_family(model: Any) -> str:
    value = str(model or "").strip().lower()
    leaf = value.rsplit("/", 1)[-1]
    for family in FORMAL_MODEL_FAMILIES:
        if leaf == family or leaf.startswith(family + "-") or leaf.startswith(family + "."):
            return family
    return ""


def assert_formal_route(
    *,
    provider: Any,
    model: Any,
    approved_providers: Sequence[str] | None = None,
) -> dict[str, str]:
    """Fail closed unless both the exact provider alias and model family are allowed."""

    provider_value = str(provider or "").strip().lower()
    model_value = str(model or "").strip()
    allowlist = normalize_provider_allowlist(
        provider_allowlist_from_env() if approved_providers is None else approved_providers
    )
    family = model_family(model_value)
    if not provider_value:
        raise FormalRouteError("formal provider is missing")
    if provider_value not in set(allowlist):
        raise FormalRouteError(f"formal provider is not evaluator-approved: {provider_value}")
    if not family:
        raise FormalRouteError(
            f"formal model is not Qwen3.7/Qwen3.6/Qwen3.5 allowlisted: {model_value}"
        )
    return {"provider": provider_value, "model": model_value, "model_family": family}


def build_formal_output_instruction(*, question_type: Any, expected_slots: int) -> str:
    """Build the R2 visible-output instruction without embedding any prior answer."""

    qtype = str(question_type or "").strip().lower()
    slots = int(expected_slots)
    if slots < 1:
        raise FormalSubmissionError("formal expected_slots must be positive")

    if any(marker in qtype for marker in ("计算", "calculation")):
        reasoning_hint = "reasoning 必须写出关键输入值、公式/关系、计算过程要点和最终结果。"
    elif any(marker in qtype for marker in ("抽取", "extract")):
        reasoning_hint = "reasoning 必须写出被抽取的关键事实/数值、对应定位关系和最终结果。"
    else:
        reasoning_hint = "reasoning 必须写出关键规定/事实、支持或排除逻辑，以及最终选项/判断结论。"

    if slots > 1:
        slot_rule = (
            f"answers 数组必须恰好有 {slots} 项，并严格按提交槽位顺序一槽一项；"
            "reasoning 必须逐槽覆盖关键依据和每个槽位的最终结果；"
            "禁止把多个槽位用分号、顿号或其他分隔符合并进同一个字符串。"
        )
    elif any(marker in qtype for marker in ("选择", "单选", "多选", "判断", "single", "multi", "choice", "judge")):
        slot_rule = (
            "answers 数组必须恰好有 1 项。若为多选题，把全部正确选项按 A、B、C、D 顺序"
            "拼成同一个字符串，例如 [\"ACD\"]；绝不能拆成 [\"A\",\"C\",\"D\"]。"
        )
    else:
        slot_rule = "answers 数组必须恰好有 1 项，单个结果只放在这一项中。"

    return (
        f"请一次返回 {slots} 个答案槽位和可审计推理摘要。"
        f"{slot_rule}"
        f"{reasoning_hint}"
        "reasoning 会被独立 Judge 单独读取，Judge 不会看到题目、证据或 answers；"
        "因此 reasoning 必须自包含关键事实/数值/条款、必要比较/公式/判断和最终答案/结果。"
        "reasoning 的最后一句必须显式写出与 answers 完全一致的最终结论："
        "选择题写成“因此答案为 ABC”这类明确选项；计算/抽取题写成“因此最终结果为 …”。"
        "若答案槽位语义是百分比，answers 中必须显式带 % 符号，例如 0.09%，不能只写 0.09。"
        "answers 与 reasoning 中的最终结论必须逐槽一致，禁止出现互相矛盾的最终答案。"
        "DOC:n 只能作为附加血缘标记，不能替代关键事实。"
        "reasoning 建议约 80~320 个中文字符；20 字只是硬底线，不得高度模板化。"
        "最终 visible message.content 只能是严格 JSON："
        '{"answers":["..."],"reasoning":"..."}'
        "。不要输出 Markdown，不要引用任何历史答案或投票结果。"
    )
