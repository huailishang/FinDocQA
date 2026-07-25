"""Option-level self-check verifier (Lane 3 — evaluation only, default off).

After the solver produces a first-pass answer, this verifier examines each
option A/B/C/D against the retrieved evidence and classifies each as:

- **supported**: key terms from the option text appear in evidence without
  nearby negation/exception clauses.
- **contradicted**: key terms appear but a negation/exception clause is found
  nearby, or counter-evidence directly refutes the option.
- **missing**: no supporting evidence could be found for this option's key
  terms.

The verifier preserves the original first-pass answer in metadata and does
NOT modify the emitted answer. It flags risks for Reviewer evaluation.

Design constraints (from the Lane 3 spec):

- No new model call — purely rule-based.
- Same Qwen chain and temperature if any LLM were involved (none here).
- Multi-select output remains sorted, deduplicated and legal.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from contracts import (
    EvidenceBundle,
    EvidenceCandidate,
    SolverResult,
    VerificationResult,
    get_verification_candidates,
)
from verification.structured_claims import route_structured_claim


# Terms that indicate negation or exception in Chinese financial/legal text.
_NEGATION_TERMS = frozenset({
    "不适用", "不承担", "不负责", "不包括", "不包含", "不属于",
    "除外", "免除", "免赔", "不得", "不应", "不能",
    "例外", "但书", "以下情形", "以下情况", "不赔付", "不赔偿",
    "不负", "不予", "禁止", "严禁",
})

# Terms that indicate the option describes a positive coverage/entitlement.
_AFFIRMATIVE_TERMS = frozenset({
    "承担", "负责", "包括", "包含", "属于", "应当", "可以",
    "赔付", "赔偿", "给付", "支付", "保障", "享有",
})


# Domain-specific boilerplate that is too generic to prove an option.
_DOMAIN_STOPWORDS = {
    "insurance": frozenset({
        "保险", "保险产品", "保险条款", "合同", "本合同", "产品", "条款",
        "现金价值", "保单年度末", "明确", "给出", "计算方法", "公式",
    }),
    "financial_reports": frozenset({
        "公司", "报告", "年度报告", "集团", "措施", "说法", "正确",
    }),
    "financial_contracts": frozenset({"合同", "协议", "条款", "规定", "约定"}),
    "regulatory": frozenset({"规定", "办法", "通知", "监管", "机构", "应当"}),
}

_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!?；;，,]")

# Regex for extracting numeric values with units.
_AMOUNT_RE = re.compile(r"(\d[\d,]*\.?\d*)\s*(元|万元|亿|%|％|万|亿)")
# Regex for percentage values.
_PERCENT_RE = re.compile(r"(\d+\.?\d*)\s*[%％]")


class OptionSelfCheckVerifier:
    """Option-level self-check: evaluate each A/B/C/D against evidence.

    This is an evaluation-only verifier. It flags risks and proposes
    corrections in metadata but never changes the emitted answer by itself.
    """

    name = "option_self_check"

    def __init__(
        self,
        min_term_match_ratio: float = 0.4,
        enable_correction_proposal: bool = True,
    ) -> None:
        self._min_term_match_ratio = min_term_match_ratio
        self._enable_correction_proposal = enable_correction_proposal

    def verify(
        self, bundle: EvidenceBundle, result: SolverResult
    ) -> VerificationResult:
        """Run option-level self-check.

        Args:
            bundle: evidence bundle with candidates.
            result: solver result containing the first-pass answer.

        Returns:
            VerificationResult with per-option verdicts and risk flags.
            The answer is unchanged (keeps the original solver answer).
        """
        original_answer = (result.answer or "").strip().upper()
        selected = {ch for ch in original_answer if ch in "ABCD"}
        all_options = dict(bundle.question.options)

        # Build combined evidence text (with source tracking)
        evidence_text = self._build_evidence_text(get_verification_candidates(bundle))

        # Evaluate each option. Exact duplicate option texts remain unresolved
        # because evidence cannot distinguish which letter should be selected.
        normalized_options = {
            key: "".join(str(value).split()) for key, value in all_options.items()
        }
        duplicate_groups: Dict[str, List[str]] = {}
        for key, normalized in normalized_options.items():
            duplicate_groups.setdefault(normalized, []).append(key)
        duplicate_keys = {
            key for keys in duplicate_groups.values() if len(keys) > 1 for key in keys
        }

        option_verdicts: Dict[str, Dict[str, Any]] = {}
        for opt_key in sorted(all_options):
            opt_text = all_options[opt_key]
            if opt_key in duplicate_keys:
                verdict = {
                    "status": "unresolved",
                    "match_ratio": 0.0,
                    "matched_terms": [],
                    "negation_found": [],
                    "reason": "duplicate option text cannot be mapped safely to one letter",
                    "evidence_matches": [],
                    "false_positive_type": "source_question_duplicate_option",
                    "claim_route": "source_question_integrity",
                }
            else:
                verdict = route_structured_claim(
                    bundle.question, opt_key, opt_text, get_verification_candidates(bundle)
                )
                if verdict is None:
                    verdict = self._evaluate_option(
                        opt_key, opt_text, evidence_text, get_verification_candidates(bundle),
                        domain=bundle.question.domain,
                        min_term_match_ratio=self._min_term_match_ratio,
                    )
            option_verdicts[opt_key] = verdict

        # Identify issues
        issues: List[str] = []
        proposed_corrections: List[str] = []
        selected_fixes: List[str] = []

        for opt_key, verdict in option_verdicts.items():
            status = verdict["status"]
            reason = verdict.get("reason", "")

            if opt_key in selected:
                if status == "contradicted":
                    msg = f"{opt_key}: selected but EVIDENCE CONTRADICTS — {reason}"
                    issues.append(msg)
                    # Proposed: drop this option
                    proposed_corrections.append(msg)
                elif status in {"missing", "unresolved"}:
                    msg = f"{opt_key}: selected but NO SUPPORTING EVIDENCE — {reason}"
                    issues.append(msg)
                    proposed_corrections.append(msg)
            else:
                if status == "supported":
                    msg = f"{opt_key}: not selected but EVIDENCE SUPPORTS — {reason}"
                    issues.append(msg)
                    # Proposed: add this option
                    proposed_corrections.append(msg)
                    selected_fixes.append(opt_key)
                elif status == "contradicted":
                    # Option not selected AND evidence contradicts it -> correct rejection
                    pass

        # Build correction proposal (if enabled and issues found)
        correction_proposal: Optional[str] = None
        if self._enable_correction_proposal and issues:
            # Start from original selection, apply corrections
            corrected = set(selected)
            for opt_key, verdict in option_verdicts.items():
                if opt_key in selected and verdict["status"] == "contradicted":
                    corrected.discard(opt_key)
                if opt_key not in selected and verdict["status"] == "supported":
                    corrected.add(opt_key)

            if corrected != selected:
                correction_proposal = "".join(sorted(corrected))
                # Validate: sorted, deduplicated, legal
                if not correction_proposal or not all(
                    ch in "ABCD" for ch in correction_proposal
                ):
                    correction_proposal = None

        # Build metadata
        meta: Dict[str, Any] = {
            "original_answer": original_answer,
            "option_verdicts": option_verdicts,
            "issues": issues,
            "issue_count": len(issues),
            "selected_count": len(selected),
            "option_count": len(all_options),
            "multi_option": bundle.question.answer_format == "multi",
        }

        if correction_proposal:
            meta["correction_proposal"] = correction_proposal
            meta["correction_differs"] = True
            meta["correction_selected_added"] = sorted(
                selected_fixes
            )
            meta["correction_selected_removed"] = sorted(
                k for k in selected
                if option_verdicts.get(k, {}).get("status") == "contradicted"
            )
        else:
            meta["correction_proposal"] = None
            meta["correction_differs"] = False

        notes = list(issues) if issues else ["All options verified — no issues detected"]

        return VerificationResult(
            qid=bundle.question.qid,
            answer=original_answer,  # Never changed by this verifier
            changed=False,
            verifier=self.name,
            notes=notes,
            metadata=meta,
        )

    # ── evidence text builder ─────────────────────────────────────────

    @staticmethod
    def _build_evidence_text(
        candidates: Sequence[EvidenceCandidate],
    ) -> Dict[str, str]:
        """Build combined evidence text with source tracking.

        Returns:
            Dict with 'combined': all evidence text joined.
        """
        parts: List[str] = []
        for c in candidates:
            parts.append(
                f"[doc={c.doc_id} source={c.source}]\n"
                f"{c.before_text}\n{c.text}\n{c.after_text}"
            )
        return {"combined": "\n\n".join(parts)}

    # ── per-option evaluation ─────────────────────────────────────────

    @staticmethod
    def _extract_key_terms(text: str, domain: str = "") -> Dict[str, List[str]]:
        """Extract meaningful terms while excluding domain boilerplate."""
        stopwords = _DOMAIN_STOPWORDS.get(domain, frozenset())
        chunks = re.findall(r"[一-鿿A-Za-z0-9]+", text)
        chinese_terms: List[str] = []
        separators = list(stopwords) + ["等于", "减去", "不同", "载明", "未给", "给出了"]
        split_pattern = "|".join(re.escape(word) for word in sorted(separators, key=len, reverse=True))
        for chunk in chunks:
            if len(chunk) < 2 or chunk in stopwords:
                continue
            pieces = re.split(split_pattern, chunk) if split_pattern else [chunk]
            chinese_terms.extend(piece for piece in pieces if len(piece) >= 2 and piece not in stopwords)

        numeric_terms = list(set(m[0] + m[1] for m in _AMOUNT_RE.findall(text)))
        percent_terms = list(set(m[0] + "%" for m in _PERCENT_RE.findall(text)))
        all_terms = list(dict.fromkeys(chinese_terms + numeric_terms + percent_terms))
        return {
            "chinese": chinese_terms,
            "numeric": numeric_terms,
            "percent": percent_terms,
            "all": all_terms,
        }

    @staticmethod
    def _sentence_window(text: str, match_pos: int) -> Tuple[int, int]:
        left = 0
        right = len(text)
        for m in _SENTENCE_BOUNDARY_RE.finditer(text):
            if m.start() < match_pos:
                left = m.end()
            elif m.start() >= match_pos:
                right = m.start()
                break
        return left, right

    @staticmethod
    def _find_nearby_negation(text: str, match_pos: int) -> List[str]:
        """Find negation only when it is close to the matched term in one clause."""
        start, end = OptionSelfCheckVerifier._sentence_window(text, match_pos)
        local_start = max(start, match_pos - 36)
        local_end = min(end, match_pos + 48)
        neighborhood = text[local_start:local_end]
        return [term for term in _NEGATION_TERMS if term in neighborhood]

    @staticmethod
    def _normalize_numeric_text(value: str) -> str:
        return re.sub(r"[,，\s]", "", value or "")

    @staticmethod
    def _match_evidence(
        term: str, candidates: Sequence[EvidenceCandidate], snippet_radius: int = 70
    ) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        term_is_numeric = bool(re.search(r"\d", term))
        normalized_term = OptionSelfCheckVerifier._normalize_numeric_text(term)
        for candidate in candidates:
            text = " ".join(
                part for part in (candidate.before_text, candidate.text, candidate.after_text) if part
            )
            if term_is_numeric:
                normalized_text = OptionSelfCheckVerifier._normalize_numeric_text(text)
                normalized_pos = normalized_text.find(normalized_term)
                if normalized_pos < 0:
                    continue
                # Use a digit anchor in original text for a readable snippet.
                first_digit = re.search(r"\d", term)
                anchor = first_digit.group(0) if first_digit else term[:1]
                pos = text.find(anchor)
                if pos < 0:
                    pos = 0
            else:
                pos = text.find(term)
                if pos < 0:
                    continue
            start = max(0, pos - snippet_radius)
            end = min(len(text), pos + len(term) + snippet_radius)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            matches.append({
                "term": term,
                "doc_id": candidate.doc_id,
                "source": candidate.source,
                "section_title": candidate.section_title,
                "snippet": snippet,
                "negation_found": OptionSelfCheckVerifier._find_nearby_negation(text, pos),
            })
        return matches

    @staticmethod
    def _evaluate_option(
        opt_key: str,
        opt_text: str,
        evidence_text: Dict[str, str],
        candidates: Sequence[EvidenceCandidate],
        domain: str = "",
        min_term_match_ratio: float = 0.4,
    ) -> Dict[str, Any]:
        """Evaluate a single option against all evidence.

        Returns a dict with:
            - status: 'supported' | 'contradicted' | 'missing'
            - match_ratio: float 0-1
            - matched_terms: list of matched terms
            - negation_found: list of negation terms found near matches
            - reason: short textual reason
        """
        combined = evidence_text.get("combined", "")

        # Fast path: empty option text
        if not opt_text.strip():
            return {
                "status": "missing",
                "match_ratio": 0.0,
                "matched_terms": [],
                "negation_found": [],
                "reason": "option text is empty",
            }

        terms = OptionSelfCheckVerifier._extract_key_terms(opt_text, domain=domain)
        all_terms = terms["all"]

        if not all_terms:
            # Fall back to presence of the entire option text as a substring
            if opt_text.strip() in combined:
                return {
                    "status": "supported",
                    "match_ratio": 1.0,
                    "matched_terms": [opt_text.strip()[:50]],
                    "negation_found": [],
                    "reason": "option text found verbatim in evidence",
                }
            return {
                "status": "missing",
                "match_ratio": 0.0,
                "matched_terms": [],
                "negation_found": [],
                "reason": "no extractable key terms and text not found verbatim",
            }

        # Search each term in source candidates and retain traceable snippets.
        # Support must be coherent within one source; terms found across unrelated
        # pages/entities are not combined into synthetic evidence.
        matched_terms: List[str] = []
        evidence_matches: List[Dict[str, Any]] = []
        matches_by_source: Dict[str, Dict[str, Any]] = {}
        total_terms = len(all_terms)

        for term in all_terms:
            term_matches = OptionSelfCheckVerifier._match_evidence(term, candidates)
            if term_matches:
                matched_terms.append(term)
                evidence_matches.extend(term_matches[:2])
            for match in term_matches:
                source = str(match.get("source") or "")
                bucket = matches_by_source.setdefault(
                    source,
                    {"terms": set(), "matches": [], "negations": set()},
                )
                bucket["terms"].add(term)
                bucket["matches"].append(match)
                bucket["negations"].update(match.get("negation_found", []))

        best_source = ""
        best_bucket: Dict[str, Any] = {"terms": set(), "matches": [], "negations": set()}
        if matches_by_source:
            best_source, best_bucket = max(
                matches_by_source.items(),
                key=lambda item: (len(item[1]["terms"]), item[0]),
            )
        coherent_terms = sorted(best_bucket.get("terms", set()))
        match_ratio = len(coherent_terms) / total_terms if total_terms > 0 else 0.0

        # Numeric support is valid only when the same source also contains at
        # least one non-numeric subject/metric term from the option.
        numeric_terms = set(terms["numeric"] + terms["percent"])
        non_numeric_terms = [term for term in all_terms if term not in numeric_terms]
        coherent_numeric = [term for term in coherent_terms if term in numeric_terms]
        coherent_context = [term for term in coherent_terms if term in non_numeric_terms]
        numeric_context_complete = not numeric_terms or bool(coherent_numeric and coherent_context)

        # A negation may contradict only when it is attached to a coherent
        # same-source match, not merely present elsewhere on the page set.
        negation_nearby = sorted(best_bucket.get("negations", set()))
        has_negation = bool(negation_nearby and len(coherent_terms) >= 2)
        unique_negations = list(negation_nearby)

        # Also check if the option itself contains negation
        option_has_negation = any(
            t in opt_text for t in _NEGATION_TERMS
        )

        temporal_only_match = bool(coherent_terms) and all(
            re.fullmatch(r"\d{4}(?:年)?", term) for term in coherent_terms
        )

        # Determine status
        if temporal_only_match:
            status = "missing"
            reason = "only period/year terms matched; no subject or metric evidence"
        elif numeric_terms and not numeric_context_complete:
            status = "missing"
            reason = (
                "numeric value found without coherent same-source subject/metric context"
                if coherent_numeric
                else "required numeric value not found with matching unit/context"
            )
        elif match_ratio >= 0.6:
            # Most key terms found
            if has_negation and not option_has_negation:
                # Evidence talks about this topic but negates it
                # The negation is NOT part of the option itself
                status = "contradicted"
                reason = (
                    f"key terms matched ({match_ratio:.0%}) but negation terms "
                    f"nearby: {', '.join(unique_negations[:3])}"
                )
            elif has_negation and option_has_negation:
                # The option itself talks about an exception; negation is expected
                status = "supported"
                reason = (
                    f"key terms matched ({match_ratio:.0%}) with expected negation "
                    f"(option describes an exception)"
                )
            else:
                status = "supported"
                reason = (
                    f"key terms matched ({match_ratio:.0%}) in evidence, "
                    f"no conflicting negation nearby"
                )
        elif match_ratio >= min_term_match_ratio:
            # Partial match — some terms found, some missing
            if has_negation:
                status = "contradicted"
                reason = (
                    f"partial key term match ({match_ratio:.0%}) with "
                    f"negation nearby: {', '.join(unique_negations[:3])}"
                )
            else:
                status = "missing"
                reason = (
                    f"partial key term match ({match_ratio:.0%}), "
                    f"some terms missing, no strong contradiction signal"
                )
        else:
            status = "missing"
            matched_sample = matched_terms[:3] if matched_terms else []
            if matched_sample:
                reason = (
                    f"low key term match ratio ({match_ratio:.0%}), "
                    f"only {', '.join(matched_sample)} found"
                )
            else:
                reason = "no key terms found in any evidence"

        return {
            "status": status,
            "match_ratio": round(match_ratio, 3),
            "matched_terms": matched_terms[:10],
            "negation_found": unique_negations[:5],
            "reason": reason,
            "evidence_matches": evidence_matches[:5],
            "coherent_source": best_source,
            "coherent_terms": coherent_terms[:10],
            "numeric_context_complete": numeric_context_complete,
            "source_term_counts": {
                source: len(bucket.get("terms", set()))
                for source, bucket in sorted(matches_by_source.items())
            },
        }
