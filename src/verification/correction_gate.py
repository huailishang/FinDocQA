"""Conservative gate for applying option self-check corrections.

The gate is default-off at workflow configuration level.  It only permits a
correction when every actual option is closed by an auditable route and no
source/evidence state is missing.  This keeps lexical self-check proposals in
review-only mode while allowing narrowly validated regulatory clauses to be
applied in focused runs.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SAFE_REGULATORY_ROUTES = frozenset({
    "regulatory_exact_clause",
    "question_scope_exclusion",
})


def assess_self_check_correction(
    *,
    domain: str,
    original_answer: str,
    self_check_metadata: Mapping[str, Any],
    allowed_routes: Sequence[str] = tuple(SAFE_REGULATORY_ROUTES),
) -> dict[str, Any]:
    proposal = str(self_check_metadata.get("correction_proposal") or "").strip().upper()
    verdicts = self_check_metadata.get("option_verdicts") or {}
    routes = frozenset(str(route) for route in allowed_routes)

    reasons: list[str] = []
    if domain != "regulatory":
        reasons.append("domain_not_regulatory")
    if not proposal or proposal == original_answer:
        reasons.append("no_distinct_proposal")
    if not isinstance(verdicts, Mapping) or not verdicts:
        reasons.append("missing_option_verdicts")
    else:
        for option, raw in verdicts.items():
            verdict = raw if isinstance(raw, Mapping) else {}
            status = str(verdict.get("status") or "")
            route = str(verdict.get("claim_route") or "")
            if status not in {"supported", "contradicted"}:
                reasons.append(f"{option}:evidence_not_closed:{status or 'missing'}")
            if route not in routes:
                reasons.append(f"{option}:unsafe_route:{route or 'missing'}")
            if not verdict.get("evidence_matches"):
                reasons.append(f"{option}:missing_source_location")

    applied = not reasons
    return {
        "applied": applied,
        "answer": proposal if applied else original_answer,
        "proposal": proposal or None,
        "blocking_reasons": reasons,
        "allowed_routes": sorted(routes),
    }
