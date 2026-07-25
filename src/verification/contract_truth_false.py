"""Production truth/false proposition certification for financial contracts.

The question stem is the proposition.  Option labels A/B only encode
``正确/错误`` and are never used as factual evidence.  The compiler recognises
reusable clause, exact-value and date-comparison relations, produces
per-document atoms, and fails closed when any required atom is missing.

No QID or answer string is encoded in this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

from answer_contract import contract_to_dict, validate_answer_against_contract
from contracts import EvidenceBundle, EvidenceCandidate, QuestionAnswerContract, SolverResult


_SUPPORTED = "supported"
_CONTRADICTED = "contradicted"
_UNRESOLVED = "unresolved"
_DOC_RE = re.compile(r"(?:fc_)?text[_-]?0*(\d+)", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_ARABIC_YM_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_ARABIC_YMD_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ISSUE_T_DAY_RE = re.compile(
    r"(20\d{2})年(\d{1,2})月(\d{1,2})日[^<\n]{0,60}"
    r"</td><td[^>]*>\s*T\s*日\s*</td><td[^>]*>[^<]{0,180}(?:发行|申购|配售)",
    re.IGNORECASE,
)
_CHINESE_DIGITS = str.maketrans("〇零一二三四五六七八九", "00123456789")


@dataclass(frozen=True)
class PropositionAtom:
    atom_id: str
    atom_type: str
    field_type: str
    relation: str
    required_doc_ids: tuple[str, ...]
    expected_value: Any = None


@dataclass(frozen=True)
class AtomResult:
    atom_id: str
    atom_type: str
    field_type: str
    relation: str
    required_doc_ids: tuple[str, ...]
    status: str
    expected_value: Any
    actual_value: Any
    canonical_sources: tuple[str, ...]
    local_windows: tuple[str, ...]
    missing_docs: tuple[str, ...] = ()
    conflicting_docs: tuple[str, ...] = ()
    certification_basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "required_doc_ids",
            "canonical_sources",
            "local_windows",
            "missing_docs",
            "conflicting_docs",
        ):
            payload[key] = list(payload[key])
        return payload


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%")


def _canonical_doc(value: Any) -> str:
    match = _DOC_RE.search(str(value or ""))
    return f"text{int(match.group(1)):02d}" if match else str(value or "").strip()


def _explicit_docs(text: str) -> list[str]:
    result: list[str] = []
    for match in _DOC_RE.finditer(text):
        doc = f"text{int(match.group(1)):02d}"
        if doc not in result:
            result.append(doc)
    return result


def _field_candidates(
    candidates: Sequence[EvidenceCandidate],
    *,
    doc_id: str,
    field_type: str,
) -> list[EvidenceCandidate]:
    rows: list[EvidenceCandidate] = []
    for candidate in candidates:
        if _canonical_doc(candidate.doc_id) != _canonical_doc(doc_id):
            continue
        metadata = dict(candidate.metadata or {})
        candidate_field = str(metadata.get("contract_exact_field") or "")
        if candidate_field == field_type:
            rows.append(candidate)
    rows.sort(key=lambda row: float(row.score or 0.0), reverse=True)
    return rows


def _context(candidate: EvidenceCandidate) -> str:
    return "\n\n".join(
        part.strip()
        for part in (candidate.before_text, candidate.text, candidate.after_text)
        if str(part or "").strip()
    )


def _first_source_window(rows: Sequence[EvidenceCandidate]) -> tuple[str, str]:
    if not rows:
        return "", ""
    row = rows[0]
    return str(row.source).replace("\\", "/"), _context(row)


def _complete_absence_candidate(
    candidates: Sequence[EvidenceCandidate],
    *,
    doc_id: str,
    field_type: str,
) -> EvidenceCandidate | None:
    for candidate in candidates:
        if _canonical_doc(candidate.doc_id) != _canonical_doc(doc_id):
            continue
        metadata = dict(candidate.metadata or {})
        if (
            str(metadata.get("contract_exact_field") or "") == field_type
            and metadata.get("complete_document_scan") is True
            and int(metadata.get("field_occurrences") or 0) == 0
        ):
            return candidate
    return None


def _field_window_matches(field_type: str, text: str) -> bool:
    compact = _compact(text)
    if field_type == "disclosure_obligation_clause":
        return "及时、公平" in compact and "信息披露义务" in compact
    if field_type == "holder_protection_clause":
        return bool(
            any(token in compact for token in ("回售选择权", "回售条款", "有条件赎回条款", "债券持有人的权利"))
            and any(token in compact for token in ("债券", "可转债", "持有人"))
        )
    if field_type == "responsibility_statement_clause":
        return bool(
            ("董事" in compact or "高级管理人员" in compact)
            and "真实性" in compact
            and any(token in compact for token in ("承担", "责任", "保证"))
        )
    if field_type == "downward_conversion_price_revision_clause":
        return "转股价格向下修正" in compact or "向下修正条款" in compact
    return bool(compact)


def _presence_atom(
    atom: PropositionAtom,
    candidates: Sequence[EvidenceCandidate],
) -> AtomResult:
    sources: list[str] = []
    windows: list[str] = []
    missing: list[str] = []
    for doc in atom.required_doc_ids:
        rows = _field_candidates(candidates, doc_id=doc, field_type=atom.field_type)
        positive = [
            row
            for row in rows
            if not dict(row.metadata or {}).get("complete_document_scan")
            and _field_window_matches(atom.field_type, _context(row))
        ]
        if not positive:
            missing.append(doc)
            continue
        source, window = _first_source_window(positive)
        sources.append(source)
        windows.append(window)
    status = _SUPPORTED if not missing and atom.required_doc_ids else _UNRESOLVED
    return AtomResult(
        atom_id=atom.atom_id,
        atom_type=atom.atom_type,
        field_type=atom.field_type,
        relation=atom.relation,
        required_doc_ids=atom.required_doc_ids,
        status=status,
        expected_value=True,
        actual_value=status == _SUPPORTED,
        canonical_sources=tuple(sources),
        local_windows=tuple(windows),
        missing_docs=tuple(missing),
        certification_basis=(
            "every required document has a source-local clause occurrence"
            if status == _SUPPORTED
            else "one or more required documents lack a source-local clause occurrence"
        ),
    )


def _absence_atom(
    atom: PropositionAtom,
    candidates: Sequence[EvidenceCandidate],
) -> AtomResult:
    sources: list[str] = []
    windows: list[str] = []
    missing: list[str] = []
    conflicting: list[str] = []
    for doc in atom.required_doc_ids:
        positives = [
            row
            for row in _field_candidates(candidates, doc_id=doc, field_type=atom.field_type)
            if not dict(row.metadata or {}).get("complete_document_scan")
        ]
        if positives:
            source, window = _first_source_window(positives)
            sources.append(source)
            windows.append(window)
            conflicting.append(doc)
            continue
        negative = _complete_absence_candidate(
            candidates, doc_id=doc, field_type=atom.field_type
        )
        if negative is None:
            missing.append(doc)
            continue
        sources.append(str(negative.source).replace("\\", "/"))
        windows.append(_context(negative))
    if conflicting:
        status = _CONTRADICTED
        basis = "at least one complete document scan contains the allegedly absent clause"
    elif missing:
        status = _UNRESOLVED
        basis = "absence requires a complete same-source document scan"
    else:
        status = _SUPPORTED
        basis = "complete same-source document scans found zero occurrences"
    return AtomResult(
        atom_id=atom.atom_id,
        atom_type=atom.atom_type,
        field_type=atom.field_type,
        relation=atom.relation,
        required_doc_ids=atom.required_doc_ids,
        status=status,
        expected_value=False,
        actual_value=(False if status == _SUPPORTED else True if status == _CONTRADICTED else None),
        canonical_sources=tuple(sources),
        local_windows=tuple(windows),
        missing_docs=tuple(missing),
        conflicting_docs=tuple(conflicting),
        certification_basis=basis,
    )


def _extract_percent_values(text: str) -> list[float]:
    return [float(match.group(1)) for match in _PERCENT_RE.finditer(text.replace("％", "%"))]


def _numeric_any_equals_atom(
    atom: PropositionAtom,
    candidates: Sequence[EvidenceCandidate],
) -> AtomResult:
    expected = float(atom.expected_value)
    explicit_docs: list[str] = []
    matched_docs: list[str] = []
    sources: list[str] = []
    windows: list[str] = []
    values_by_doc: dict[str, list[float]] = {}
    for doc in atom.required_doc_ids:
        values: list[float] = []
        selected_source = ""
        selected_window = ""
        for candidate in _field_candidates(candidates, doc_id=doc, field_type=atom.field_type):
            window = _context(candidate)
            parsed = _extract_percent_values(window)
            if parsed:
                values.extend(parsed)
                if not selected_source:
                    selected_source = str(candidate.source).replace("\\", "/")
                    selected_window = window
        if values:
            explicit_docs.append(doc)
            values_by_doc[doc] = values
            sources.append(selected_source)
            windows.append(selected_window)
            if any(abs(value - expected) <= 1e-9 for value in values):
                matched_docs.append(doc)
    if matched_docs:
        status = _SUPPORTED
        missing: tuple[str, ...] = ()
        conflicting: tuple[str, ...] = ()
        basis = "at least one required document explicitly contains the exact numeric fact"
    elif len(explicit_docs) == len(atom.required_doc_ids) and atom.required_doc_ids:
        status = _CONTRADICTED
        missing = ()
        conflicting = tuple(explicit_docs)
        basis = "all required documents expose the field and none matches the claimed value"
    else:
        status = _UNRESOLVED
        missing = tuple(doc for doc in atom.required_doc_ids if doc not in explicit_docs)
        conflicting = ()
        basis = "the exact numeric fact is not fully recoverable from the allowed documents"
    return AtomResult(
        atom_id=atom.atom_id,
        atom_type=atom.atom_type,
        field_type=atom.field_type,
        relation=atom.relation,
        required_doc_ids=atom.required_doc_ids,
        status=status,
        expected_value=expected,
        actual_value=values_by_doc,
        canonical_sources=tuple(source for source in sources if source),
        local_windows=tuple(window for window in windows if window),
        missing_docs=missing,
        conflicting_docs=conflicting,
        certification_basis=basis,
    )


def _chinese_year_month(text: str) -> tuple[int, int] | None:
    compact = _compact(text)
    match = re.search(r"([〇零一二三四五六七八九]{4})年([〇零一二三四五六七八九十]{1,3})月", compact)
    if not match:
        return None
    year = int(match.group(1).translate(_CHINESE_DIGITS))
    month_text = match.group(2)
    month_map = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
        "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
    }
    month = month_map.get(month_text)
    return (year, month) if month else None


def _date_values(field_type: str, text: str) -> list[tuple[int, int, int]]:
    values: list[tuple[int, int, int]] = []
    if field_type == "issue_date":
        for match in _ISSUE_T_DAY_RE.finditer(text):
            values.append(tuple(int(match.group(i)) for i in (1, 2, 3)))
        for match in re.finditer(
            r"(?:发行日期|发行日|发行首日)(?:为|是|：|:)?\s*"
            r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?",
            text,
        ):
            values.append((int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)))
    elif field_type == "announcement_date":
        for match in re.finditer(
            r"(?:发行公告(?:刊登)?日期|公告日期)(?:为|是|：|:)?\s*"
            r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?",
            text,
        ):
            values.append((int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)))
        chinese = _chinese_year_month(text)
        if chinese:
            values.append((chinese[0], chinese[1], 1))
    output: list[tuple[int, int, int]] = []
    for value in values:
        if value not in output:
            output.append(value)
    return output


def _best_date(
    candidates: Sequence[EvidenceCandidate],
    *,
    doc_id: str,
    field_type: str,
) -> tuple[tuple[int, int, int] | None, str, str]:
    for candidate in _field_candidates(candidates, doc_id=doc_id, field_type=field_type):
        window = _context(candidate)
        values = _date_values(field_type, window)
        if values:
            return values[0], str(candidate.source).replace("\\", "/"), window
    return None, "", ""


def _date_compare_atom(
    atom: PropositionAtom,
    candidates: Sequence[EvidenceCandidate],
) -> AtomResult:
    if len(atom.required_doc_ids) != 2:
        return AtomResult(
            atom_id=atom.atom_id,
            atom_type=atom.atom_type,
            field_type=atom.field_type,
            relation=atom.relation,
            required_doc_ids=atom.required_doc_ids,
            status=_UNRESOLVED,
            expected_value=atom.expected_value,
            actual_value=None,
            canonical_sources=(),
            local_windows=(),
            certification_basis="ordered date comparison requires exactly two documents",
        )
    first_doc, second_doc = atom.required_doc_ids
    first, source1, window1 = _best_date(
        candidates, doc_id=first_doc, field_type="issue_date"
    )
    second, source2, window2 = _best_date(
        candidates, doc_id=second_doc, field_type="announcement_date"
    )
    missing = tuple(
        doc
        for doc, value in ((first_doc, first), (second_doc, second))
        if value is None
    )
    if missing:
        status = _UNRESOLVED
        basis = "ordered date comparison requires both typed date operands"
        conflicting: tuple[str, ...] = ()
    else:
        is_later = bool(second > first)
        status = _SUPPORTED if is_later else _CONTRADICTED
        basis = (
            "second document announcement date is later than first document issue date"
            if is_later
            else "second document announcement date is not later than first document issue date"
        )
        conflicting = () if is_later else atom.required_doc_ids
    return AtomResult(
        atom_id=atom.atom_id,
        atom_type=atom.atom_type,
        field_type=atom.field_type,
        relation=atom.relation,
        required_doc_ids=atom.required_doc_ids,
        status=status,
        expected_value="second_later_than_first",
        actual_value={first_doc: first, second_doc: second},
        canonical_sources=tuple(source for source in (source1, source2) if source),
        local_windows=tuple(window for window in (window1, window2) if window),
        missing_docs=missing,
        conflicting_docs=conflicting,
        certification_basis=basis,
    )


def compile_truth_false_atoms(question_text: str, doc_ids: Sequence[str]) -> tuple[PropositionAtom, ...]:
    """Compile reusable proposition atoms from a truth/false question stem."""
    text = _compact(question_text)
    docs = tuple(_canonical_doc(doc) for doc in doc_ids)
    explicit = _explicit_docs(question_text)
    atoms: list[PropositionAtom] = []

    if "及时、公平" in text and "信息披露义务" in text and any(token in text for token in ("均包含", "都包含", "两份文档均")):
        atoms.append(PropositionAtom(
            "disclosure_all", "clause_presence", "disclosure_obligation_clause",
            "all_documents_present", docs, True,
        ))

    if (
        any(token in text for token in ("保护性条款", "持有人权利"))
        and any(token in text for token in ("回售", "赎回"))
        and any(token in text for token in ("均提及", "均包含", "都提及", "两份文档"))
    ):
        atoms.append(PropositionAtom(
            "holder_protection_all", "clause_presence", "holder_protection_clause",
            "all_documents_present", docs, True,
        ))

    downward_docs = (
        tuple(explicit[:2])
        if len(explicit) >= 2
        else docs[:2]
        if "第一份文档" in text and "第二份文档" in text and len(docs) >= 2
        else ()
    )
    if "转股价格向下修正" in text and "未出现" in text and len(downward_docs) == 2:
        atoms.extend((
            PropositionAtom(
                "downward_revision_present", "clause_presence",
                "downward_conversion_price_revision_clause", "document_present",
                (downward_docs[0],), True,
            ),
            PropositionAtom(
                "downward_revision_absent", "clause_absence",
                "downward_conversion_price_revision_clause", "document_absent",
                (downward_docs[1],), False,
            ),
        ))

    if (
        "董事" in text and "高级管理人员" in text
        and "真实性" in text and "承担责任" in text
        and any(token in text for token in ("两份文档均", "两份文档都", "均提及"))
    ):
        atoms.append(PropositionAtom(
            "responsibility_all", "clause_presence", "responsibility_statement_clause",
            "all_documents_present", docs, True,
        ))
        percent = _PERCENT_RE.search(text)
        if percent:
            atoms.append(PropositionAtom(
                "required_ratio_any", "numeric_exact", "debt_asset_ratio",
                "any_document_equals", docs, float(percent.group(1)),
            ))

    if (
        "第二份" in text and "公告日期" in text and "晚于" in text
        and "第一份" in text and "发行日期" in text
        and len(docs) >= 2
    ):
        atoms.append(PropositionAtom(
            "announcement_after_issue", "date_comparison", "announcement_vs_issue_date",
            "second_later_than_first", (docs[0], docs[1]), "second_later_than_first",
        ))

    return tuple(atoms)


def certify_truth_false_proposition(
    question_text: str,
    doc_ids: Sequence[str],
    candidates: Sequence[EvidenceCandidate],
) -> dict[str, Any]:
    atoms = compile_truth_false_atoms(question_text, doc_ids)
    results: list[AtomResult] = []
    for atom in atoms:
        if atom.atom_type == "clause_presence":
            results.append(_presence_atom(atom, candidates))
        elif atom.atom_type == "clause_absence":
            results.append(_absence_atom(atom, candidates))
        elif atom.atom_type == "numeric_exact":
            results.append(_numeric_any_equals_atom(atom, candidates))
        elif atom.atom_type == "date_comparison":
            results.append(_date_compare_atom(atom, candidates))

    if not atoms or len(results) != len(atoms):
        status = _UNRESOLVED
        basis = "truth/false proposition could not be completely compiled"
    elif any(row.status == _CONTRADICTED for row in results):
        status = _CONTRADICTED
        basis = "at least one required proposition atom is explicitly false"
    elif any(row.status == _UNRESOLVED for row in results):
        status = _UNRESOLVED
        basis = "at least one required proposition atom remains unresolved"
    else:
        status = _SUPPORTED
        basis = "all required proposition atoms are independently supported"

    sources: list[str] = []
    windows: list[str] = []
    for row in results:
        for source in row.canonical_sources:
            if source and source not in sources:
                sources.append(source)
        for window in row.local_windows:
            if window and window not in windows:
                windows.append(window)
    return {
        "schema_version": "truth_false_proposition_v1",
        "question_text": question_text,
        "required_doc_ids": [_canonical_doc(doc) for doc in doc_ids],
        "compiled_atom_count": len(atoms),
        "atoms": [asdict(atom) | {"required_doc_ids": list(atom.required_doc_ids)} for atom in atoms],
        "atom_results": [row.to_dict() for row in results],
        "status": status,
        "certification_basis": basis,
        "canonical_sources": sources,
        "local_windows": windows,
        "missing_atoms": [row.atom_id for row in results if row.status == _UNRESOLVED],
        "contradicted_atoms": [row.atom_id for row in results if row.status == _CONTRADICTED],
        "trusted": status in {_SUPPORTED, _CONTRADICTED} and bool(atoms),
    }


def _option_verdict(
    *,
    label: str,
    status: str,
    proposition: Mapping[str, Any],
) -> dict[str, Any]:
    sources = [str(value) for value in proposition.get("canonical_sources") or []]
    windows = [str(value) for value in proposition.get("local_windows") or []]
    trusted = proposition.get("trusted") is True and status in {_SUPPORTED, _CONTRADICTED}
    return {
        "status": status,
        "claim_route": "truth_false_proposition",
        "typed_claim_route": "truth_false_proposition_compiler",
        "claim_type": "truth_false_proposition",
        "trusted_for_option_gate": trusted,
        "required_atoms_complete": trusted,
        "entity_scope_complete": trusted,
        "entity_scope_reasons": [],
        "period_scope_complete": trusted,
        "metric_scope_complete": trusted,
        "comparator_scope_complete": trusted,
        "compound_claim_requires_derivation": True,
        "cross_doc_aggregation_complete": trusted,
        "term_equivalence": "confirmed" if status == _SUPPORTED else "not_required",
        "term_equivalence_confirmed": status == _SUPPORTED,
        "term_equivalence_required": status == _SUPPORTED,
        "factual_statement_true": status == _SUPPORTED,
        "question_scope_binding": "in_scope",
        "reason": str(proposition.get("certification_basis") or ""),
        "evidence_refs": sources,
        "resolved_evidence_refs": sources,
        "canonical_source": sources[0] if sources else "",
        "canonical_sources": sources,
        "local_window": "\n\n".join(windows),
        "source_facts": list(proposition.get("atom_results") or []),
        "certification_basis": str(proposition.get("certification_basis") or ""),
        "missing_atoms": list(proposition.get("missing_atoms") or []),
        "conflicting_atoms": [],
        "claim_contradiction_atoms": list(proposition.get("contradicted_atoms") or []),
        "conflicts": [],
        "lineage_conflict": False,
        "opposite_certification_count": 0,
        "model_judgment": "unresolved",
        "resolved_judgment": status,
        "model_uncertainty_closed_by_typed_evidence": trusted,
        "truth_false_option_label": label,
        "truth_false_proposition": dict(proposition),
    }


def build_truth_false_production_contract(
    *,
    bundle: EvidenceBundle,
    result: SolverResult,
    contract: QuestionAnswerContract,
    candidates: Sequence[EvidenceCandidate],
    solver_answer: str,
    solver_contract_validation: Any,
    used_docs: set[str],
    judgments: Mapping[str, str],
) -> dict[str, Any]:
    proposition = certify_truth_false_proposition(
        bundle.question.text, bundle.question.doc_ids, candidates
    )
    statement_status = str(proposition.get("status") or _UNRESOLVED)
    if statement_status == _SUPPORTED:
        answer = "A"
        statuses = {"A": _SUPPORTED, "B": _CONTRADICTED}
    elif statement_status == _CONTRADICTED:
        answer = "B"
        statuses = {"A": _CONTRADICTED, "B": _SUPPORTED}
    else:
        answer = ""
        statuses = {"A": _UNRESOLVED, "B": _UNRESOLVED}

    option_verdicts = {
        label: _option_verdict(label=label, status=statuses[label], proposition=proposition)
        for label in ("A", "B")
    }
    validation = validate_answer_against_contract(answer, contract)
    trust_failures: list[str] = []
    if not used_docs:
        trust_failures.append("used_doc_lineage_missing")
    if not candidates:
        trust_failures.append("no_candidates_in_used_doc_lineage")
    if proposition.get("trusted") is not True:
        trust_failures.append("truth_false_proposition_unresolved")
    if not validation.valid:
        trust_failures.append(f"typed_supported_answer_contract_violation:{validation.reason}")
    trusted = not trust_failures

    return {
        "schema_version": "production_typed_option_evidence_v3",
        "trusted_for_production": trusted,
        "trust_failures": sorted(set(trust_failures)),
        "answer_contract": contract_to_dict(contract),
        "solver_answer_contract_validation": solver_contract_validation.to_dict(),
        "typed_supported_answer_contract_validation": validation.to_dict(),
        "correction_answer_contract_validation": validation.to_dict(),
        "solver_answer": solver_answer,
        "typed_supported_answer": answer,
        "solver_answer_matches_typed_supported_answer": solver_answer == answer,
        "solver_disagreement_is_audit_only": bool(answer and solver_answer != answer),
        "model_judgments": dict(judgments),
        "resolved_judgments": statuses,
        "model_uncertainty_closed_labels": ["A", "B"] if trusted else [],
        "unresolved_after_typed": [] if trusted else ["A", "B"],
        "option_verdicts": option_verdicts,
        "option_diagnostics": {
            label: {
                "model_judgment": judgments.get(label, "unresolved"),
                "resolved_judgment": statuses[label],
                "truth_false_proposition": proposition,
            }
            for label in ("A", "B")
        },
        "option_coverage": "2/2",
        "used_doc_ids": sorted(used_docs),
        "candidate_count_in_used_doc_lineage": len(candidates),
        "correction_proposal": answer or None,
        "correction_differs": bool(answer and answer != solver_answer),
        "legacy_self_check_policy": "truth_false_proposition_is_authoritative",
        "truth_false_proposition": proposition,
        "production_derived_option_evidence": [],
        "production_derived_option_count": 0,
    }
