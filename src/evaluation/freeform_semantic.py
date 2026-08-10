"""Deterministic zero-provider semantic-anchor scoring for freeform evaluation.

The scorer is evaluator-only. It deliberately accepts only prediction and Gold
answer text; it has no access to qid, question, evidence, retrieval, delivery
state, provider output metadata, or evaluator labels.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Iterable


CONTENT_RECALL_THRESHOLD = 0.55

# Generic language-only stopwords. No benchmark terms, qids, entities, metrics,
# years, identifiers, or answer-specific aliases belong here.
_ENGLISH_STOPWORDS = frozenset(
    """
    a an and are as at be been being by for from had has have he her hers him his
    i if in into is it its me my of on or our ours she that the their theirs them
    they this those to under was we were what when where which who why will with
    you your yours following answer question company according based using use
    only also not than then these those such each same do does did can could
    should would may might must shall
    """.split()
)

_REFUSAL_PATTERNS = (
    re.compile(r"\b(?:cannot|can't|unable\s+to|insufficient\s+(?:evidence|information)|not\s+enough\s+(?:evidence|information)|cannot\s+confirm|cannot\s+determine|cannot\s+calculate|cannot\s+identify)\b", re.I),
    re.compile(r"无法(?:从现有证据)?确认"),
    re.compile(r"证据(?:不足|不充分|中缺乏)"),
    re.compile(r"无法(?:计算|判断|确定|识别|回答)"),
    re.compile(r"不能(?:确认|判断|确定|计算)"),
)

# Mixed alphanumeric tokens such as 3M, FY2022, MMM26, 1x. A token must contain
# at least one ASCII letter and at least one digit.
_MIXED_TOKEN_RE = re.compile(r"\b[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*\d[A-Za-z0-9]*\b|\b[A-Za-z0-9]*\d[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*\b")
# Standalone numbers/percentages only. Digits touching ASCII letters are excluded
# so 3M/FY2022/MMM26 are handled as mixed identifiers rather than split numbers.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:%|％)?(?![A-Za-z0-9])")
_CONTENT_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")
_ORDINAL_RE = re.compile(r"(?<![A-Za-z0-9])(\d+)(?:st|nd|rd|th)\b", re.I)


@dataclass(frozen=True)
class FreeformSemanticResult:
    semantic_correct: bool
    refusal_detected: bool
    gold_refusal_detected: bool
    protected_gold_anchors: tuple[str, ...]
    matched_protected_anchors: tuple[str, ...]
    protected_anchor_recall: float | None
    content_token_recall: float | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _nfkc(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def _detect_refusal(value: object) -> bool:
    text = _nfkc(value)
    return any(pattern.search(text) for pattern in _REFUSAL_PATTERNS)


def _canonical_decimal(raw: str) -> str | None:
    value = raw.replace(",", "").replace("％", "%").strip()
    suffix = "%" if value.endswith("%") else ""
    if suffix:
        value = value[:-1]
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"-0", "+0", ""}:
        normalized = "0"
    return f"num:{normalized}{suffix}"


def _protected_anchors(value: object) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    # Numeric coupon-rate bullets such as "-1.500% Notes due 2026" use the
    # leading hyphen as a list marker, not as a negative sign. Normalize only
    # this generic line-start/capitalized-label shape; ordinary negative values
    # remain signed anchors.
    text = re.sub(
        r"(?m)^(\s*)-(?=\d+(?:\.\d+)?%\s+[A-Z])",
        r"\1",
        text,
    )
    anchors: list[str] = []
    for match in _MIXED_TOKEN_RE.finditer(text):
        anchors.append(f"id:{match.group(0).casefold()}")
    for match in _ORDINAL_RE.finditer(text):
        canonical = _canonical_decimal(match.group(1))
        if canonical:
            anchors.append(canonical)
    for match in _NUMBER_RE.finditer(text):
        canonical = _canonical_decimal(match.group(0))
        if canonical:
            anchors.append(canonical)
    return tuple(dict.fromkeys(anchors))


def _normalize_content_token(token: str) -> str:
    token = token.casefold().strip("'")
    if token.endswith("'s") and len(token) > 3:
        token = token[:-2]
    # Lightweight morphology normalization, intentionally generic and shared
    # by Gold/prediction: increasing/increases -> increas, years -> year.
    if token.isascii() and token.isalpha():
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("es") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            token = token[:-1]
    return token


def _content_tokens(value: object) -> tuple[str, ...]:
    text = _nfkc(value)
    tokens: list[str] = []
    for match in _CONTENT_RE.finditer(text):
        token = _normalize_content_token(match.group(0))
        if not token or token in _ENGLISH_STOPWORDS:
            continue
        if token.isdigit():
            # Protected numeric anchors already enforce number preservation;
            # do not double-weight bare integers in lexical content coverage.
            continue
        if len(token) == 1 and token.isascii():
            continue
        tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def _is_numeric_only_gold(gold: object) -> bool:
    text = unicodedata.normalize("NFKC", str(gold or "")).strip()
    if not text:
        return False
    # Remove common currency markers and whitespace, then require one standalone
    # numeric expression. This covers answers such as "$8.70" and "$1577.00".
    stripped = re.sub(r"[$€£¥￥\s]", "", text)
    return bool(re.fullmatch(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:%|％)?", stripped))


def _recall(required: Iterable[str], observed: Iterable[str]) -> float | None:
    required_set = set(required)
    if not required_set:
        return None
    observed_set = set(observed)
    return len(required_set & observed_set) / len(required_set)


def score_freeform_semantic(
    predicted_answer: object,
    gold_answer: object,
    *,
    content_recall_threshold: float = CONTENT_RECALL_THRESHOLD,
) -> FreeformSemanticResult:
    """Score one freeform prediction against one Gold answer without providers.

    The only semantic approximation is the frozen evaluator contract:
    incompatible refusal detection + complete protected-anchor preservation +
    generic Gold content-token recall. No benchmark-specific feature is read.
    """
    threshold = float(content_recall_threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("content_recall_threshold must be between 0 and 1")

    predicted_refusal = _detect_refusal(predicted_answer)
    gold_refusal = _detect_refusal(gold_answer)
    incompatible_refusal = predicted_refusal and not gold_refusal

    gold_anchors = _protected_anchors(gold_answer)
    predicted_anchors = _protected_anchors(predicted_answer)
    matched_anchors = tuple(anchor for anchor in gold_anchors if anchor in set(predicted_anchors))
    anchor_recall = _recall(gold_anchors, predicted_anchors)

    gold_tokens = _content_tokens(gold_answer)
    predicted_tokens = _content_tokens(predicted_answer)
    token_recall = _recall(gold_tokens, predicted_tokens)

    if incompatible_refusal:
        return FreeformSemanticResult(
            semantic_correct=False,
            refusal_detected=True,
            gold_refusal_detected=gold_refusal,
            protected_gold_anchors=gold_anchors,
            matched_protected_anchors=matched_anchors,
            protected_anchor_recall=anchor_recall,
            content_token_recall=token_recall,
            reason="incompatible_refusal",
        )

    if _is_numeric_only_gold(gold_answer):
        correct = bool(gold_anchors) and anchor_recall == 1.0
        return FreeformSemanticResult(
            semantic_correct=correct,
            refusal_detected=predicted_refusal,
            gold_refusal_detected=gold_refusal,
            protected_gold_anchors=gold_anchors,
            matched_protected_anchors=matched_anchors,
            protected_anchor_recall=anchor_recall,
            content_token_recall=token_recall,
            reason="numeric_anchor_match" if correct else "missing_numeric_anchor",
        )

    if gold_anchors and anchor_recall != 1.0:
        return FreeformSemanticResult(
            semantic_correct=False,
            refusal_detected=predicted_refusal,
            gold_refusal_detected=gold_refusal,
            protected_gold_anchors=gold_anchors,
            matched_protected_anchors=matched_anchors,
            protected_anchor_recall=anchor_recall,
            content_token_recall=token_recall,
            reason="missing_protected_anchor",
        )

    effective_token_recall = token_recall if token_recall is not None else 0.0
    correct = effective_token_recall >= threshold
    return FreeformSemanticResult(
        semantic_correct=correct,
        refusal_detected=predicted_refusal,
        gold_refusal_detected=gold_refusal,
        protected_gold_anchors=gold_anchors,
        matched_protected_anchors=matched_anchors,
        protected_anchor_recall=anchor_recall,
        content_token_recall=token_recall,
        reason="semantic_anchor_match" if correct else "insufficient_content_token_recall",
    )
