"""High-risk answer verifier with deterministic consistency checks.

Only triggers on high-risk routes: multi-option, calculation, cross-document,
empty evidence, or default answers. All checks are rule-based (no extra LLM calls).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from contracts import (
    EvidenceBundle,
    EvidenceCandidate,
    QuestionLabel,
    SolverResult,
    VerificationResult,
    get_verification_candidates,
)

# Terms that indicate negation or exception in Chinese financial/legal text
_NEGATION_TERMS = (
    "不适用", "不承担", "不负责", "不包括", "不包含", "不属于",
    "除外", "免除", "免赔", "不得", "不应", "不能",
    "例外", "但书", "以下情形", "以下情况",
)
# Regex for extracting numeric values with units
_AMOUNT_RE = re.compile(r"(\d[\d,]*\.?\d*)\s*(元|万元|亿|%|％|万|亿)")
_YEAR_RE = re.compile(r"(20\d{2})\s*年")


class HighRiskVerifier:
    name = "high_risk"

    def verify(self, bundle: EvidenceBundle, result: SolverResult) -> VerificationResult:
        """Run consistency checks on high-risk questions. Conservative — rarely changes answers."""
        labels = set(bundle.classification.labels)
        question = bundle.question

        # Determine if this is high-risk
        high_risk = self._is_high_risk(labels, bundle)
        if not high_risk:
            return VerificationResult(
                qid=question.qid,
                answer=result.answer,
                changed=False,
                verifier=self.name,
                notes=[],
                metadata={"high_risk": False, "checks_run": []},
            )

        # Run checks
        notes: List[str] = []
        checks_run: List[str] = []
        warn_flags: List[str] = []

        # Check 1: Evidence adequacy
        if self._check_evidence_adequacy(bundle, checks_run, notes):
            warn_flags.append("weak_evidence")

        # Check 2: Negation/exception terms in evidence
        negation_found = self._check_negation_terms(bundle, checks_run, notes)
        if negation_found:
            warn_flags.append("has_negation")

        # Check 3: Year consistency
        year_mismatch = self._check_years(bundle, checks_run, notes)
        if year_mismatch:
            warn_flags.append("year_mismatch")

        # Check 4: Amount/unit consistency
        amount_mismatch = self._check_amounts(bundle, checks_run, notes)
        if amount_mismatch:
            warn_flags.append("amount_mismatch")

        # Check 5: Multi-option coverage
        if QuestionLabel.MULTI_OPTION in labels:
            self._check_multi_option_coverage(bundle, checks_run, notes, warn_flags)

        # Check 6: Document boundary
        if QuestionLabel.CROSS_DOC in labels:
            self._check_document_boundary(bundle, checks_run, notes, warn_flags)

        # Decision: only change answer if multiple strong warnings fire
        changed = False
        final_answer = result.answer
        # Currently conservative: log warnings, do not change answer automatically

        return VerificationResult(
            qid=question.qid,
            answer=final_answer,
            changed=changed,
            verifier=self.name,
            notes=notes,
            metadata={
                "high_risk": True,
                "checks_run": checks_run,
                "warnings": warn_flags,
                "placeholder": False,
            },
        )

    # ── risk detection ──────────────────────────────────────────────

    @staticmethod
    def _is_high_risk(labels: set, bundle: EvidenceBundle) -> bool:
        """Determine if verifier should run."""
        if labels & {QuestionLabel.MULTI_OPTION, QuestionLabel.CALCULATION, QuestionLabel.CROSS_DOC}:
            return True
        if not get_verification_candidates(bundle):
            return True
        return False

    # ── check 1: evidence adequacy ─────────────────────────────────

    @staticmethod
    def _check_evidence_adequacy(
        bundle: EvidenceBundle,
        checks_run: List[str],
        notes: List[str],
    ) -> bool:
        """Check if there's enough evidence for the question."""
        checks_run.append("evidence_adequacy")
        if not get_verification_candidates(bundle):
            notes.append("No retrieved evidence for this question.")
            return True

        # Check if any requested doc is entirely missing
        question = bundle.question
        doc_ids_in_evidence = {str(c.doc_id) for c in get_verification_candidates(bundle)}
        missing = [str(d) for d in question.doc_ids if str(d) not in doc_ids_in_evidence]
        if missing:
            notes.append(f"Missing evidence for doc_ids: {', '.join(missing)}")
            return True

        return False

    # ── check 2: negation terms ────────────────────────────────────

    @staticmethod
    def _check_negation_terms(
        bundle: EvidenceBundle,
        checks_run: List[str],
        notes: List[str],
    ) -> bool:
        """Flag if evidence contains negation terms that the answer might have missed."""
        checks_run.append("negation_terms")

        combined_evidence = " ".join(
            f"{c.text} {c.before_text} {c.after_text}" for c in get_verification_candidates(bundle)
        )
        found_negations = [t for t in _NEGATION_TERMS if t in combined_evidence]
        if not found_negations:
            return False

        # Cross-reference with the question — if question itself asks about negation,
        # the solver likely handled it. Only flag if evidence has negation but question
        # doesn't explicitly ask about it.
        question_text = f"{bundle.question.text} {' '.join(bundle.question.options.values())}"
        has_explicit_negation_question = any(t in question_text for t in _NEGATION_TERMS)

        if not has_explicit_negation_question and found_negations:
            notes.append(
                f"Evidence contains negation terms ({', '.join(found_negations[:5])}) "
                f"but question does not explicitly reference them. Answer may be incorrect."
            )
            return True

        return False

    # ── check 3: year consistency ──────────────────────────────────

    @staticmethod
    def _check_years(
        bundle: EvidenceBundle,
        checks_run: List[str],
        notes: List[str],
    ) -> bool:
        """Flag if evidence contains year references that could cause confusion."""
        checks_run.append("year_consistency")

        years_by_doc: Dict[str, set] = {}
        for c in get_verification_candidates(bundle):
            doc = str(c.doc_id)
            if doc not in years_by_doc:
                years_by_doc[doc] = set()
            combined = f"{c.text} {c.before_text} {c.after_text}"
            years_by_doc[doc].update(_YEAR_RE.findall(combined))

        # Only flag if multiple years appear in the same doc
        multi_year_docs = [doc for doc, years in years_by_doc.items() if len(years) >= 2]
        if not multi_year_docs:
            return False

        year_detail = "; ".join(
            f"doc {doc}: {', '.join(sorted(years_by_doc[doc]))}"
            for doc in multi_year_docs[:3]
        )
        notes.append(f"Multiple years found in same document ({year_detail}). Check year consistency.")
        return True

    # ── check 4: amount/unit consistency ───────────────────────────

    @staticmethod
    def _check_amounts(
        bundle: EvidenceBundle,
        checks_run: List[str],
        notes: List[str],
    ) -> bool:
        """Flag if evidence contains diverse amounts/units that could cause confusion."""
        checks_run.append("amount_consistency")

        all_amounts: List[str] = []
        for c in get_verification_candidates(bundle):
            combined = f"{c.text} {c.before_text} {c.after_text}"
            all_amounts.extend(m[0] + m[1] for m in _AMOUNT_RE.findall(combined))

        # Only flag if there are 3+ distinct amounts — suggests the evidence
        # covers multiple financial figures requiring careful matching
        if len(set(all_amounts)) >= 3:
            notes.append(
                f"Evidence contains {len(set(all_amounts))} distinct amount/unit "
                f"references. Verify amount matching."
            )
            return True

        return False

    # ── check 5: multi-option coverage ─────────────────────────────

    @staticmethod
    def _check_multi_option_coverage(
        bundle: EvidenceBundle,
        checks_run: List[str],
        notes: List[str],
        warn_flags: List[str],
    ) -> None:
        """For multi-option questions, check if evidence covers all options."""
        checks_run.append("multi_option_coverage")

        options = bundle.question.options
        option_texts = {k: v for k, v in options.items()}
        if not option_texts:
            return

        combined_evidence = " ".join(c.text for c in get_verification_candidates(bundle))
        covered = []
        not_covered = []
        for key, text in option_texts.items():
            # Check if key terms from this option appear in evidence
            option_terms = set(re.findall(r"[一-鿿]{2,}", text))
            matched_terms = [t for t in option_terms if t in combined_evidence and len(t) >= 3]
            if matched_terms:
                covered.append(key)
            else:
                not_covered.append(key)

        if not_covered:
            notes.append(
                f"Multi-option question: option(s) {', '.join(not_covered)} "
                f"have no supporting terms in retrieved evidence."
            )
            warn_flags.append("option_coverage")

    # ── check 6: document boundary ─────────────────────────────────

    @staticmethod
    def _check_document_boundary(
        bundle: EvidenceBundle,
        checks_run: List[str],
        notes: List[str],
        warn_flags: List[str],
    ) -> None:
        """For cross-doc questions, check evidence distribution across docs."""
        checks_run.append("document_boundary")

        doc_ids = {str(c.doc_id) for c in get_verification_candidates(bundle)}
        if len(doc_ids) <= 1:
            notes.append(
                f"Cross-document question but evidence only covers {len(doc_ids)} "
                f"document(s): {', '.join(sorted(doc_ids))}."
            )
            warn_flags.append("document_boundary")
