"""Dataset-neutral structured option-claim routing.

FinDocQA keeps dataset-specific answer rules out of the production path.  This
module only handles claim shapes that can be classified without question IDs or
known answers.  Everything else falls back to the generic option verifier.
"""
from __future__ import annotations

from typing import Any, Dict, Sequence

from contracts import EvidenceCandidate, Question

_OPAQUE_OPTIONS = {"正确", "错误", "对", "错"}


def route_structured_claim(
    question: Question,
    option: str,
    option_text: str,
    candidates: Sequence[EvidenceCandidate],
) -> Dict[str, Any] | None:
    """Return a safe structured verdict, or ``None`` for generic verification.

    Opaque true/false labels cannot be validated from their option text alone,
    so they remain unresolved unless a dedicated question-level verifier closes
    the claim.  No dataset-, qid-, or answer-specific rules are registered here.
    """
    del option, candidates
    normalized = "".join(str(option_text or "").split())
    if normalized not in _OPAQUE_OPTIONS:
        return None

    return {
        "status": "unresolved",
        "match_ratio": 0.0,
        "matched_terms": [],
        "negation_found": [],
        "reason": "opaque option text requires question-level reasoning",
        "evidence_matches": [],
        "false_positive_type": "opaque_option_text",
        "claim_route": "question_level_required",
        "question_text_present": bool(str(question.text or "").strip()),
    }
