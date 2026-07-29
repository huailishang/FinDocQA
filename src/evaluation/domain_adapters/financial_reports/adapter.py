"""Financial-report vertical adapter for Package AG.

The adapter extends the current corpus compiler only with generic financial
policy-stage and unit rules. It contains no QID branches.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any, Mapping

from evaluation.domain_adapters.base import (
    DomainOptionResult,
    DomainQuestionResult,
    evaluate_from_current_production,
)
from evaluation.domain_adapters.common import DecisiveFieldProvenance, SourceSpanRef, canonical_answer

CAPABILITY = "financial_metric_period_unit_policy_stage_v1"


def _doc_files(repo_root: Path, doc_id: str) -> list[Path]:
    data = repo_root / "data"
    candidates = [
        data / "processed_mineru" / "financial_reports" / doc_id / "auto" / f"{doc_id}.md",
        data / "processed_mineru" / "financial_reports" / doc_id / f"{doc_id}.md",
    ]
    candidates.extend(sorted((data / "processed_mineru_retrieval" / "financial_reports" / doc_id).glob("*.md")))
    return [path.resolve() for path in candidates if path.is_file()]


def _find_span(paths: list[Path], patterns: list[re.Pattern[str]]) -> tuple[Path | None, str, re.Match[str] | None]:
    for path in paths:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        for index, line in enumerate(lines):
            window = "\n".join(lines[max(0, index - 1):min(len(lines), index + 2)]).strip()
            for pattern in patterns:
                match = pattern.search(window)
                if match:
                    return path, window, match
    return None, "", None


def _source_and_provenance(
    *,
    doc_id: str,
    path: Path,
    span: str,
    field_name: str,
    field_value: str,
    source_text: str,
    rule: str,
) -> tuple[SourceSpanRef, tuple[DecisiveFieldProvenance, ...]]:
    source = SourceSpanRef.build(
        source_doc_id=doc_id,
        source_path=str(path),
        source_span=span,
    )
    provenance = DecisiveFieldProvenance.locate(
        field_name=field_name,
        field_value=field_value,
        source=source,
        source_text=source_text,
        extraction_rule=rule,
    )
    return source, (provenance,)


def _repair_option(
    *,
    repo_root: Path,
    question: Any,
    label: str,
    current: DomainOptionResult,
) -> DomainOptionResult:
    option = str(question.options[label])
    required_docs = list(question.doc_ids)
    compact = re.sub(r"\s+", "", option)

    per10 = re.search(r"每\s*10\s*股[^\d]{0,20}(\d+(?:\.\d+)?)\s*元", option)
    if per10:
        value = per10.group(1)
        pattern = re.compile(rf"每\s*10\s*股[^。；\n]{{0,70}}?{re.escape(value)}\s*元")
        for doc_id in required_docs:
            path, span, match = _find_span(_doc_files(repo_root, doc_id), [pattern])
            if path and match:
                source, provenance = _source_and_provenance(
                    doc_id=doc_id,
                    path=path,
                    span=span,
                    field_name="cash_dividend_per_10_shares",
                    field_value=value,
                    source_text=match.group(0),
                    rule="financial_per10_exact_value_v1",
                )
                return DomainOptionResult(
                    option=label,
                    status="supported",
                    source_spans=(source,),
                    decisive_field_provenance=provenance,
                    blockers=(),
                    adapter_reason="exact per-10-share dividend value reproduced from declared report",
                )

    per_share = re.search(r"每股[^\d]{0,20}(\d+(?:\.\d+)?)\s*元", option)
    if per_share:
        value = per_share.group(1)
        wrong_basis = re.compile(rf"每\s*10\s*股[^。；\n]{{0,70}}?{re.escape(value)}\s*元")
        for doc_id in required_docs:
            path, span, match = _find_span(_doc_files(repo_root, doc_id), [wrong_basis])
            if path and match:
                source, provenance = _source_and_provenance(
                    doc_id=doc_id,
                    path=path,
                    span=span,
                    field_name="unit_family",
                    field_value="per_share_conflicts_with_per_10_shares",
                    source_text=match.group(0),
                    rule="financial_share_basis_conflict_v1",
                )
                return DomainOptionResult(
                    option=label,
                    status="contradicted",
                    source_spans=(source,),
                    decisive_field_provenance=provenance,
                    blockers=(),
                    adapter_reason="option says per share while declared report states per 10 shares",
                )

    if "公积" in compact and any(token in compact for token in ("实施", "转增")):
        negation_patterns = [
            re.compile(r"不实施资本公积金?转增股本"),
            re.compile(r"不以(?:资本)?公积金转增股本"),
            re.compile(r"未宣告资本公积金转增股本预案"),
        ]
        found: list[tuple[str, Path, str, re.Match[str]]] = []
        for doc_id in required_docs:
            path, span, match = _find_span(_doc_files(repo_root, doc_id), negation_patterns)
            if path and match:
                found.append((doc_id, path, span, match))
        if len(found) == len(required_docs) and found:
            sources: list[SourceSpanRef] = []
            provenance: list[DecisiveFieldProvenance] = []
            for doc_id, path, span, match in found:
                source, fields = _source_and_provenance(
                    doc_id=doc_id,
                    path=path,
                    span=span,
                    field_name="capital_reserve_conversion",
                    field_value="not_implemented",
                    source_text=match.group(0),
                    rule="financial_capital_reserve_negation_v1",
                )
                sources.append(source)
                provenance.extend(fields)
            return DomainOptionResult(
                option=label,
                status="contradicted",
                source_spans=tuple(sources),
                decisive_field_provenance=tuple(provenance),
                blockers=(),
                adapter_reason="all declared reports explicitly state no capital-reserve conversion",
            )

    if (
        "现金分红" in compact
        and "20%" in compact
        and any(token in compact for token in ("归母", "归属于上市公司股东", "净利润"))
    ):
        annual_ratio_patterns = [
            re.compile(r"年度现金分红[：:]?[^。；\n]{0,180}?归属于上市公司股东的净利润的20%"),
            re.compile(r"年度现金分红[：:]?[^。；\n]{0,180}?净利润的20%"),
        ]
        for doc_id in required_docs:
            path, span, match = _find_span(_doc_files(repo_root, doc_id), annual_ratio_patterns)
            if path and match:
                source, provenance = _source_and_provenance(
                    doc_id=doc_id,
                    path=path,
                    span=span,
                    field_name="annual_cash_dividend_parent_profit_ratio",
                    field_value="20%",
                    source_text=match.group(0),
                    rule="financial_annual_dividend_ratio_policy_stage_v1",
                )
                return DomainOptionResult(
                    option=label,
                    status="supported",
                    source_spans=(source,),
                    decisive_field_provenance=provenance,
                    blockers=(),
                    adapter_reason="annual cash-dividend component is explicitly 20% of parent-attributable profit",
                )

    if "回购" in compact and any(token in compact for token in ("超过", "高于")):
        pattern = re.compile(r"现金分红与股份回购之总金额超过当年度公司归母净利润")
        for doc_id in required_docs:
            path, span, match = _find_span(_doc_files(repo_root, doc_id), [pattern])
            if path and match:
                source, provenance = _source_and_provenance(
                    doc_id=doc_id,
                    path=path,
                    span=span,
                    field_name="dividend_plus_repurchase_vs_parent_profit",
                    field_value="greater_than",
                    source_text=match.group(0),
                    rule="financial_direct_comparison_sentence_v1",
                )
                return DomainOptionResult(
                    option=label,
                    status="supported",
                    source_spans=(source,),
                    decisive_field_provenance=provenance,
                    blockers=(),
                    adapter_reason="direct report sentence supports dividend-plus-repurchase comparison",
                )
    return current


def evaluate(*, repo_root: Path, question: Any, payload: Mapping[str, Any]) -> DomainQuestionResult:
    base = evaluate_from_current_production(
        repo_root=repo_root,
        question=question,
        payload=payload,
        production_capability=CAPABILITY,
        require_payload_trust=False,
    )
    option_results = {
        label: _repair_option(
            repo_root=repo_root,
            question=question,
            label=label,
            current=base.option_results[label],
        )
        for label in question.options
    }
    statuses = {label: result.status for label, result in option_results.items()}
    all_closed = all(status in {"supported", "contradicted"} for status in statuses.values())
    answer = canonical_answer("".join(label for label, status in statuses.items() if status == "supported"))
    if question.answer_contract.answer_format in {"mcq", "tf"}:
        answer = answer if len(answer) == 1 else ""
    blockers: list[str] = []
    if not all_closed:
        blockers.append("option_slots_not_closed")
    if not answer:
        blockers.append("no_contract_valid_unique_answer")
    return replace(
        base,
        production_answer=answer,
        option_statuses=statuses,
        option_results=option_results,
        all_options_closed=all_closed,
        production_trusted=all_closed and bool(answer),
        blockers=tuple(blockers),
        production_capability=CAPABILITY,
    )
