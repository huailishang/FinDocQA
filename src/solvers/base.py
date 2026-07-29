"""Shared solver helpers."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from answer_contract import (
    AnswerContractValidation,
    contract_from_answer_format,
    contract_from_mapping,
    validate_answer_against_contract,
)
from contracts import EvidenceBundle, QuestionAnswerContract

LETTERS_RE = re.compile(r"[ABCD]+")
AnswerValidation = AnswerContractValidation


def validate_submission_answer(
    answer: str,
    answer_format: str,
    answer_contract: QuestionAnswerContract | Mapping[str, Any] | None = None,
) -> AnswerValidation:
    contract = contract_from_mapping(answer_contract) or contract_from_answer_format(answer_format)
    result = validate_answer_against_contract(answer, contract)
    if result.reason == "unknown_answer_contract":
        return AnswerValidation(result.answer, False, f"unknown_answer_format:{answer_format!r}", result.violations)
    return result


def is_valid_submission_answer(answer: str, answer_format: str) -> bool:
    return validate_submission_answer(answer, answer_format).valid


def safe_submission_answer(answer: str, answer_format: str, default: str = "A") -> tuple[str, bool]:
    validation = validate_submission_answer(answer, answer_format)
    return (validation.answer, True) if validation.valid else (default, False)


def normalize_answer(raw: str, answer_format: str) -> str:
    """Extract a final answer without capturing incidental letters from prose.

    Priority: explicit final-answer marker, answer-only line, then the last
    standalone answer token. The legacy first-character scan is retained only
    as a final compatibility fallback for compact raw outputs.
    """
    if answer_format == "freeform":
        return str(raw or "").strip()

    text = (raw or "").strip().upper()
    allowed = "AB" if answer_format == "tf" else "ABCD"

    explicit = re.findall(
        r"(?:FINAL\s+ANSWER|最终答案|答案)\s*[:：]?\s*([ABCD]+)",
        text,
    )
    candidates = explicit
    if not candidates:
        candidates = re.findall(r"(?m)^\s*([ABCD]+)\s*[。.!！]?\s*$", text)
    if not candidates:
        standalone = re.findall(r"(?<![A-Z])([ABCD]+)(?![A-Z])", text)
        # Multiple prose-level answer tokens are ambiguous. Preserve the safe
        # legacy default rather than selecting an incidental first/last token.
        if len(standalone) > 1:
            return "A"
        candidates = standalone

    if candidates:
        token = candidates[-1]
        if answer_format in ("mcq", "tf"):
            valid = [letter for letter in token if letter in allowed]
            if len(valid) == 1:
                return valid[0]
        elif answer_format == "multi":
            unique = "".join(sorted(set(letter for letter in token if letter in allowed)))
            if unique:
                return unique

    letters = "".join(LETTERS_RE.findall(text))
    if answer_format in ("mcq", "tf"):
        for letter in reversed(letters):
            if letter in allowed:
                return letter
        return "A"
    if answer_format == "multi":
        unique = "".join(sorted(set(letter for letter in letters if letter in allowed)))
        return unique or "A"
    return "A"


def render_question(bundle: EvidenceBundle) -> str:
    question = bundle.question
    parts = [f"题目ID：{question.qid}", f"题目：{question.text}"]
    if question.options:
        options = "\n".join(f"{key}. {value}" for key, value in sorted(question.options.items()))
        parts.append(f"选项：\n{options}")
    parts.append(f"答案格式：{question.answer_format}")
    understanding = question.raw.get("_query_understanding") if isinstance(question.raw, Mapping) else None
    if isinstance(understanding, Mapping):
        answer_shape = str(understanding.get("answer_shape") or "").strip()
        if answer_shape:
            parts.append(f"答案形态：{answer_shape}")
    return "\n\n".join(parts)


def answer_format_instruction(answer_format: str) -> str:
    if answer_format == "freeform":
        return "直接输出与问题匹配的简洁答案；不要强行转换成 A/B/C/D。证据不足时明确说明无法从现有证据确认。"
    if answer_format == "multi":
        return "最终只输出多选答案字母，按字母顺序排列，例如 ABD。不要输出解释。"
    if answer_format == "tf":
        return "最终只输出 A 或 B，其中 A=正确，B=错误。不要输出解释。"
    return "最终只输出一个答案字母 A/B/C/D。不要输出解释。"


def dry_run_answer(answer_format: str) -> str:
    return "" if answer_format == "freeform" else "A"


def candidate_doc_ids(bundle: EvidenceBundle) -> list[str]:
    """Return distinct document ids supplied to a solver in stable order."""
    seen: set[str] = set()
    result: list[str] = []
    for candidate in bundle.candidates:
        doc_id = str(candidate.doc_id)
        if doc_id not in seen:
            seen.add(doc_id)
            result.append(doc_id)
    return result


def solver_available_doc_ids(bundle: EvidenceBundle) -> list[str]:
    """Return the actual solver-visible lineage, never the wider candidate scope."""
    actual = candidate_doc_ids(bundle)
    declared = list((bundle.metadata or {}).get("solver_available_doc_ids") or [])
    if declared and declared != actual:
        # The immutable candidate view is authoritative. A mismatching metadata
        # field remains visible to the audit layer, but must not alter solver use.
        return actual
    return actual


def conservative_used_doc_lineage(bundle: EvidenceBundle) -> tuple[list[str], str]:
    """Only infer usage automatically for a single-document solver input."""
    docs = candidate_doc_ids(bundle)
    if len(docs) == 1:
        return docs, "single_document_prompt_context"
    return [], "unknown"


def _candidate_page_number(source: str, metadata: Mapping[str, Any]) -> int | None:
    """Recover a page number from candidate metadata/source without guessing content."""
    raw_page = metadata.get("page_number")
    if isinstance(raw_page, int) and not isinstance(raw_page, bool):
        return raw_page
    if isinstance(raw_page, str) and raw_page.strip().isdigit():
        return int(raw_page.strip())
    source_text = str(source or "")
    for pattern in (r"page[_=-]?(\d+)", r"[#?&]page=(\d+)"):
        match = re.search(pattern, source_text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def candidate_doc_lineage(bundle: EvidenceBundle, doc_id: str) -> list[dict[str, Any]]:
    """Return real solver-visible source/page lineage for one canonical document id."""
    canonical = str(doc_id)
    lineage: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for candidate in bundle.candidates:
        if str(candidate.doc_id) != canonical:
            continue
        source = str(candidate.source or "").strip()
        page_number = _candidate_page_number(source, candidate.metadata or {})
        key = (source, page_number)
        if key in seen:
            continue
        seen.add(key)
        lineage.append(
            {
                "doc_id": canonical,
                "page_number": page_number,
                "source": source,
                "domain": str(candidate.domain or ""),
                "section_title": candidate.section_title,
                "retriever": str(candidate.retriever or "unknown"),
            }
        )
    return lineage


def build_solver_prompt_alias_map(bundle: EvidenceBundle) -> dict[str, str]:
    """Build deterministic question-local short aliases for solver-visible docs.

    Aliases are deliberately scoped to the current immutable solver packet and
    therefore never depend on a global catalog, qid, answer, or fuzzy matching.
    The first distinct solver-visible canonical document is ``DOC:1``, the next
    is ``DOC:2``, and so on.
    """
    return {
        f"DOC:{index}": canonical
        for index, canonical in enumerate(candidate_doc_ids(bundle), start=1)
    }


def normalize_solver_evidence_refs(
    raw_refs: Sequence[Any] | None,
    bundle: EvidenceBundle,
    *,
    prompt_alias_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize exact prompt-local evidence refs to canonical solver doc ids.

    Legacy mode (``prompt_alias_map is None``) preserves the P0-05 contract and
    accepts exact canonical spellings: ``id``, ``DOC:id`` and ``[DOC:id]``.

    Short-alias mode accepts only exact local aliases from a deterministic map,
    e.g. ``DOC:1`` / ``[DOC:1]``.  The alias is resolved exactly to a canonical
    solver-visible document and then to its real source/page lineage. Repeated use
    of the same valid local alias is deterministically deduplicated and reuses the
    same lineage. Unknown, out-of-range, ambiguous, or mixed valid+invalid refs
    still fail closed.
    """
    raw_values = (
        [str(value).strip() for value in raw_refs if str(value).strip()]
        if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, (str, bytes))
        else []
    )
    available = candidate_doc_ids(bundle)
    aliases: dict[str, set[str]] = {}
    alias_mode = prompt_alias_map is not None
    alias_map_error: str | None = None
    normalized_alias_map: dict[str, str] = {}

    if alias_mode:
        normalized_alias_map = {
            str(alias).strip(): str(canonical).strip()
            for alias, canonical in dict(prompt_alias_map or {}).items()
            if str(alias).strip() and str(canonical).strip()
        }
        expected_alias_map = build_solver_prompt_alias_map(bundle)
        if normalized_alias_map != expected_alias_map:
            alias_map_error = "LINEAGE_ALIAS_MAP_INVALID"
        elif len(set(normalized_alias_map.values())) != len(normalized_alias_map):
            alias_map_error = "LINEAGE_ALIAS_MAP_INVALID"
        else:
            for alias, canonical in normalized_alias_map.items():
                if not re.fullmatch(r"DOC:[1-9][0-9]*", alias):
                    alias_map_error = "LINEAGE_ALIAS_MAP_INVALID"
                    break
                aliases.setdefault(alias, set()).add(canonical)
                aliases.setdefault(f"[{alias}]", set()).add(canonical)
    else:
        for canonical in available:
            for alias in (canonical, f"DOC:{canonical}", f"[DOC:{canonical}]"):
                aliases.setdefault(alias, set()).add(canonical)

    normalized: list[str] = []
    resolutions: list[dict[str, Any]] = []
    failure_class: str | None = None
    lineage_by_doc: dict[str, list[dict[str, Any]]] = {}

    if not raw_values:
        return {
            "raw_refs": [],
            "normalized_refs": [],
            "resolutions": [],
            "lineage_by_doc": {},
            "prompt_alias_map": normalized_alias_map,
            "alias_map_valid": alias_map_error is None,
            "all_resolved": False,
            "lineage_complete": False,
            "failure_class": "MISSING_EVIDENCE",
        }

    if alias_map_error is not None:
        return {
            "raw_refs": raw_values,
            "normalized_refs": [],
            "resolutions": [
                {
                    "raw_ref": raw_ref,
                    "normalized_candidate": None,
                    "resolution_status": "ALIAS_MAP_INVALID",
                    "blocking_reason": "LINEAGE_REF_FORMAT_MISMATCH",
                    "lineage": [],
                }
                for raw_ref in raw_values
            ],
            "lineage_by_doc": {},
            "prompt_alias_map": normalized_alias_map,
            "alias_map_valid": False,
            "all_resolved": False,
            "lineage_complete": False,
            "failure_class": "LINEAGE_REF_FORMAT_MISMATCH",
        }

    seen_raw_refs: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for raw_ref in raw_values:
        if alias_mode and raw_ref in seen_raw_refs:
            canonical, lineage = seen_raw_refs[raw_ref]
            resolutions.append(
                {
                    "raw_ref": raw_ref,
                    "normalized_candidate": canonical,
                    "resolution_status": "RESOLVED_DUPLICATE",
                    "blocking_reason": None,
                    "lineage": lineage,
                }
            )
            continue

        candidates = sorted(aliases.get(raw_ref, set()))
        if not candidates:
            resolutions.append(
                {
                    "raw_ref": raw_ref,
                    "normalized_candidate": None,
                    "resolution_status": "UNKNOWN_REF",
                    "blocking_reason": "LINEAGE_REF_FORMAT_MISMATCH",
                    "lineage": [],
                }
            )
            failure_class = failure_class or "LINEAGE_REF_FORMAT_MISMATCH"
            continue
        if len(candidates) != 1:
            resolutions.append(
                {
                    "raw_ref": raw_ref,
                    "normalized_candidate": None,
                    "resolution_status": "AMBIGUOUS_REF",
                    "blocking_reason": "LINEAGE_REF_FORMAT_MISMATCH",
                    "lineage": [],
                    "candidate_matches": candidates,
                }
            )
            failure_class = failure_class or "LINEAGE_REF_FORMAT_MISMATCH"
            continue

        canonical = candidates[0]
        lineage = candidate_doc_lineage(bundle, canonical)
        if alias_mode:
            seen_raw_refs[raw_ref] = (canonical, lineage)
        lineage_by_doc.setdefault(canonical, lineage)
        if canonical not in normalized:
            normalized.append(canonical)
        if not any(str(item.get("source") or "").strip() for item in lineage):
            resolutions.append(
                {
                    "raw_ref": raw_ref,
                    "normalized_candidate": canonical,
                    "resolution_status": "LINEAGE_MISSING",
                    "blocking_reason": "LINEAGE_LOST",
                    "lineage": lineage,
                }
            )
            if failure_class is None:
                failure_class = "LINEAGE_LOST"
            continue
        resolutions.append(
            {
                "raw_ref": raw_ref,
                "normalized_candidate": canonical,
                "resolution_status": "RESOLVED",
                "blocking_reason": None,
                "lineage": lineage,
            }
        )

    all_resolved = bool(resolutions) and all(
        item.get("resolution_status") in {"RESOLVED", "RESOLVED_DUPLICATE"}
        for item in resolutions
    )
    lineage_complete = bool(normalized) and all(
        any(str(item.get("source") or "").strip() for item in lineage_by_doc.get(doc_id, []))
        for doc_id in normalized
    )
    return {
        "raw_refs": raw_values,
        "normalized_refs": normalized,
        "resolutions": resolutions,
        "lineage_by_doc": lineage_by_doc,
        "prompt_alias_map": normalized_alias_map,
        "alias_map_valid": alias_map_error is None,
        "all_resolved": all_resolved,
        "lineage_complete": lineage_complete,
        "failure_class": failure_class,
    }


def extract_declared_used_doc_ids(raw: str, bundle: EvidenceBundle) -> list[str]:
    """Parse an explicit 使用文档 declaration and keep only supplied doc ids."""
    available = candidate_doc_ids(bundle)
    if not available:
        return []
    match = re.search(r"(?m)^\s*使用文档\s*[:：]\s*(.+?)\s*$", raw or "")
    if not match:
        return []
    declared = match.group(1)
    used: list[str] = []
    for doc_id in available:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(doc_id)}(?![A-Za-z0-9_])"
        if re.search(pattern, declared) and doc_id not in used:
            used.append(doc_id)
    return used
