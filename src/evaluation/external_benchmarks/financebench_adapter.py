"""Research-only FinanceBench adapter with strict runtime/Gold separation."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


RESEARCH_ONLY_USE_SCOPE = "RESEARCH_ONLY_NONCOMMERCIAL"
FINANCEBENCH_LICENSE_ID = "CC-BY-NC-4.0"
FINANCEBENCH_DATASET_ID = "financebench"
FINANCEBENCH_DOMAIN = "financial_reports"
FINANCEBENCH_ANSWER_FORMAT = "freeform"

FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "answer",
        "gold_answer",
        "justification",
        "evidence",
        "evidence_text",
        "evidence_page_num",
        "evidence_text_full_page",
    }
)


@dataclass(frozen=True)
class FinanceBenchEvidence:
    """Evaluation-only evidence annotation from the frozen FinanceBench source."""

    doc_name: str
    page_num: int
    evidence_text: str
    full_page_text: str


@dataclass(frozen=True)
class FinanceBenchGoldLabel:
    """Evaluation-only answer material that must never enter runtime payloads."""

    answer: str
    justification: str | None
    evidence: tuple[FinanceBenchEvidence, ...]


@dataclass(frozen=True)
class FinanceBenchDocumentRef:
    """Document identity needed by the runtime retrieval path."""

    doc_name: str
    pdf_relative_path: str
    company: str


@dataclass(frozen=True)
class FinanceBenchCase:
    """One normalized FinanceBench case with runtime and Gold kept separate."""

    case_id: str
    question: str
    question_type: str
    company: str
    document: FinanceBenchDocumentRef
    runtime_question_payload: Mapping[str, Any]
    gold_label: FinanceBenchGoldLabel
    use_scope: str
    license_id: str


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _require_string(row: Mapping[str, Any], key: str, *, context: str) -> str:
    if key not in row:
        raise ValueError(f"{context} missing required field: {key}")
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} field {key} must be a non-empty string")
    return value.strip()


def _parse_justification(row: Mapping[str, Any], *, context: str) -> str | None:
    if "justification" not in row:
        raise ValueError(f"{context} missing required field: justification")
    value = row["justification"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{context} field justification must be a string or null")
    return value


def _parse_evidence(row: Mapping[str, Any], *, context: str) -> tuple[FinanceBenchEvidence, ...]:
    if "evidence" not in row:
        raise ValueError(f"{context} missing required field: evidence")
    raw_evidence = row["evidence"]
    if not isinstance(raw_evidence, list):
        raise ValueError(f"{context} field evidence must be a list")
    if not raw_evidence:
        raise ValueError(f"{context} field evidence must not be empty")

    parsed: list[FinanceBenchEvidence] = []
    for index, raw_item in enumerate(raw_evidence):
        item_context = f"{context} evidence[{index}]"
        item = _require_mapping(raw_item, context=item_context)
        doc_name = _require_string(item, "doc_name", context=item_context)
        evidence_text = _require_string(item, "evidence_text", context=item_context)
        full_page_text = _require_string(item, "evidence_text_full_page", context=item_context)
        if "evidence_page_num" not in item:
            raise ValueError(f"{item_context} missing required field: evidence_page_num")
        page_num = item["evidence_page_num"]
        if isinstance(page_num, bool) or not isinstance(page_num, int) or page_num < 0:
            raise ValueError(
                f"{item_context} field evidence_page_num must be a non-negative integer"
            )
        parsed.append(
            FinanceBenchEvidence(
                doc_name=doc_name,
                page_num=page_num,
                evidence_text=evidence_text,
                full_page_text=full_page_text,
            )
        )
    return tuple(parsed)


def _validate_use_scope(use_scope: str) -> str:
    if use_scope != RESEARCH_ONLY_USE_SCOPE:
        raise ValueError(
            "FinanceBench adapter is restricted to "
            f"{RESEARCH_ONLY_USE_SCOPE}; requested use_scope={use_scope!r}"
        )
    return use_scope


def _runtime_payload(
    *,
    case_id: str,
    question: str,
    question_type: str,
    company: str,
    doc_name: str,
    use_scope: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "qid": case_id,
        "domain": FINANCEBENCH_DOMAIN,
        "question": question,
        "answer_format": FINANCEBENCH_ANSWER_FORMAT,
        "candidate_doc_ids": [doc_name],
        "company": company,
        "question_type": question_type,
        "dataset": FINANCEBENCH_DATASET_ID,
        "use_scope": use_scope,
    }
    leaked = FORBIDDEN_RUNTIME_KEYS.intersection(payload)
    if leaked:
        raise AssertionError(f"runtime payload contains forbidden Gold keys: {sorted(leaked)}")
    return payload


def _parse_case(
    raw_row: Any,
    *,
    line_number: int,
    use_scope: str,
) -> FinanceBenchCase:
    context = f"FinanceBench row {line_number}"
    row = _require_mapping(raw_row, context=context)

    case_id = _require_string(row, "financebench_id", context=context)
    question = _require_string(row, "question", context=context)
    answer = _require_string(row, "answer", context=context)
    doc_name = _require_string(row, "doc_name", context=context)
    company = _require_string(row, "company", context=context)
    question_type = _require_string(row, "question_type", context=context)
    justification = _parse_justification(row, context=context)
    evidence = _parse_evidence(row, context=context)

    document = FinanceBenchDocumentRef(
        doc_name=doc_name,
        pdf_relative_path=f"pdfs/{doc_name}.pdf",
        company=company,
    )
    gold_label = FinanceBenchGoldLabel(
        answer=answer,
        justification=justification,
        evidence=evidence,
    )
    return FinanceBenchCase(
        case_id=case_id,
        question=question,
        question_type=question_type,
        company=company,
        document=document,
        runtime_question_payload=_runtime_payload(
            case_id=case_id,
            question=question,
            question_type=question_type,
            company=company,
            doc_name=doc_name,
            use_scope=use_scope,
        ),
        gold_label=gold_label,
        use_scope=use_scope,
        license_id=FINANCEBENCH_LICENSE_ID,
    )


def load_financebench_cases(
    path: str | Path,
    *,
    use_scope: str = RESEARCH_ONLY_USE_SCOPE,
) -> tuple[FinanceBenchCase, ...]:
    """Load a frozen FinanceBench JSONL source without network/PDF/provider access.

    The returned case intentionally keeps runtime question data separate from the
    evaluation-only Gold label. Any explicit non-research use scope fails closed.
    """

    approved_scope = _validate_use_scope(use_scope)
    source_path = Path(path)
    seen_case_ids: set[str] = set()
    cases: list[FinanceBenchCase] = []

    with source_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw_row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"FinanceBench row {line_number} is not valid JSON: {exc.msg}"
                ) from exc
            case = _parse_case(
                raw_row,
                line_number=line_number,
                use_scope=approved_scope,
            )
            if case.case_id in seen_case_ids:
                raise ValueError(f"duplicate FinanceBench financebench_id: {case.case_id}")
            seen_case_ids.add(case.case_id)
            cases.append(case)

    return tuple(cases)
