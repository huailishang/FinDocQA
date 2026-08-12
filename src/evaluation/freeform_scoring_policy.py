"""Conservative L1 routing for freeform E4 scoring.

This module does not attempt unrestricted semantic correctness. It only promotes
facts that can be proven by the frozen V1 policy boundary and abstains from all
other cases for semantic review.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata


AUTO_CORRECT = "AUTO_CORRECT"
AUTO_INCORRECT = "AUTO_INCORRECT"
SEMANTIC_REVIEW_REQUIRED = "SEMANTIC_REVIEW_REQUIRED"

_NORMALIZED_WHITESPACE_RE = re.compile(r"\s+")

# L1 authority must be narrower than the legacy semantic scorer's diagnostic
# refusal signal. These patterns require answerability/evidence context rather
# than treating ordinary factual modality such as bare ``cannot`` as refusal.
_ANSWERABILITY_REFUSAL_PATTERNS = (
    re.compile(
        r"^(?:i\s+)?cannot\s+(?:confirm|determine|calculate|identify|answer)\b"
        r".*\b(?:from|based\s+on)\b.*\b(?:evidence|information)\b",
        re.I | re.S,
    ),
    re.compile(
        r"^(?:unable\s+to|i\s+am\s+unable\s+to)\s+"
        r"(?:confirm|determine|calculate|identify|answer)\b"
        r".*\b(?:from|based\s+on)\b.*\b(?:evidence|information)\b",
        re.I | re.S,
    ),
    re.compile(
        r"^(?:insufficient|not\s+enough)\s+(?:evidence|information)\b"
        r".*\b(?:confirm|determine|calculate|identify|answer)\b",
        re.I | re.S,
    ),
    re.compile(r"^无法从(?:现有|提供的)?(?:证据|信息)(?:中)?(?:确认|确定|判断|计算|识别|回答)", re.S),
    re.compile(r"^(?:证据|信息)(?:不足|不充分|缺失).*无法(?:确认|确定|判断|计算|识别|回答)", re.S),
)


@dataclass(frozen=True)
class FreeformScoringPolicyDecision:
    route: str
    reason_code: str
    exact_match_signal: bool
    refusal_signal: bool
    gold_refusal_signal: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalize_exact_text(value: object) -> str:
    """Normalize only representation details safe for exact-equality proof."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return _NORMALIZED_WHITESPACE_RE.sub(" ", text)


def _detect_answerability_refusal(value: object) -> bool:
    """Detect only explicit answerer-level inability caused by missing evidence."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return any(pattern.search(text) for pattern in _ANSWERABILITY_REFUSAL_PATTERNS)


def route_freeform_scoring_policy(
    predicted_answer: object,
    gold_answer: object,
) -> FreeformScoringPolicyDecision:
    """Route one prediction using the frozen V1 L1 authority boundary.

    V1 can auto-decide only two cases:
    - non-empty normalized exact equality -> AUTO_CORRECT;
    - explicit incompatible refusal against non-refusal Gold -> AUTO_INCORRECT.

    Everything else abstains to SEMANTIC_REVIEW_REQUIRED.
    """
    normalized_prediction = _normalize_exact_text(predicted_answer)
    normalized_gold = _normalize_exact_text(gold_answer)
    exact_match_signal = bool(
        normalized_prediction
        and normalized_gold
        and normalized_prediction == normalized_gold
    )

    # Refusal authority is policy-owned and intentionally narrower than the
    # legacy semantic scorer's diagnostic refusal detector.
    refusal_signal = _detect_answerability_refusal(predicted_answer)
    gold_refusal_signal = _detect_answerability_refusal(gold_answer)

    if exact_match_signal:
        return FreeformScoringPolicyDecision(
            route=AUTO_CORRECT,
            reason_code="normalized_exact_equality",
            exact_match_signal=True,
            refusal_signal=refusal_signal,
            gold_refusal_signal=gold_refusal_signal,
        )

    if refusal_signal and not gold_refusal_signal:
        return FreeformScoringPolicyDecision(
            route=AUTO_INCORRECT,
            reason_code="incompatible_explicit_refusal",
            exact_match_signal=False,
            refusal_signal=True,
            gold_refusal_signal=False,
        )

    return FreeformScoringPolicyDecision(
        route=SEMANTIC_REVIEW_REQUIRED,
        reason_code="semantic_review_required",
        exact_match_signal=False,
        refusal_signal=refusal_signal,
        gold_refusal_signal=gold_refusal_signal,
    )
