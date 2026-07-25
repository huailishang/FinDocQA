"""Financial-report target-page augmentation with selective same-page fallback.

The locator operates strictly inside the document scope already resolved by the
retriever.  It scans only candidate financial-report documents, identifies
high-value financial summary/statement pages from question-derived
entity/year/metric signals, and appends those pages without removing baseline
candidates.  When a target MinerU page is visibly sparse, the exact same
``doc_id/page`` may be read from a configured fallback parser (normally
PyMuPDF).  No provider client is constructed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question


_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PAGE_RE = re.compile(r"page_(\d+)\.md$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"(?<![A-Za-z])[-(]?\d[\d,，.]*\d(?:%|％)?\)?")
_NO_RENDERABLE_RE = re.compile(r"no renderable text|image-only|empty blocks", re.IGNORECASE)

# High-value financial structure signals from the evaluator contract.  The
# implementation is intentionally generic: no qid or page number appears here.
_STRUCTURE_SIGNALS = (
    "主要会计数据和财务指标",
    "主要财务指标",
    "合并利润表",
    "母公司利润表",
    "公司利润表",
    "合并及公司利润表",
    "合并现金流量表",
    "母公司现金流量表",
    "公司现金流量表",
    "合并及公司现金流量表",
)

_METRIC_GROUPS: Mapping[str, tuple[str, ...]] = {
    "revenue": ("营业收入", "营业总收入"),
    "ocf": (
        "经营活动产生的现金流量净额",
        "经营活动产生的现金流量",
        "经营现金流量净额",
        "经营现金流",
    ),
    "eps": ("基本每股收益", "每股收益"),
    "net_profit": (
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
        "归母净利润",
        "净利润",
    ),
}

_ENTITY_STOPWORDS = (
    "年度报告",
    "合并财务报表",
    "母公司财务报表",
    "财务报表",
    "营业收入",
    "经营活动",
    "现金流量",
    "净利润",
    "基本每股收益",
)


@dataclass(frozen=True)
class PageQuality:
    chars: int
    numeric_count: int
    table_like: bool
    no_renderable_marker: bool
    metric_hits: tuple[str, ...]
    structure_hits: tuple[str, ...]
    sparse: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocatorPageAudit:
    doc_id: str
    page: str
    score: float
    entity_matches: tuple[str, ...]
    year_matches: tuple[str, ...]
    metric_hits: tuple[str, ...]
    structure_hits: tuple[str, ...]
    parser_used: str
    fallback_used: bool
    mineru_quality: PageQuality
    fallback_quality: PageQuality | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mineru_quality"] = self.mineru_quality.to_dict()
        payload["fallback_quality"] = (
            self.fallback_quality.to_dict() if self.fallback_quality is not None else None
        )
        return payload


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("％", "%").lower()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _question_text(question: Question) -> str:
    return "\n".join(
        part for part in (question.text, *question.options.values()) if str(part).strip()
    )


def _extract_entities(question: Question) -> tuple[str, ...]:
    """Extract explicit entity phrases near common report-question lead-ins.

    This deliberately uses only question text and never qid/history.  The
    patterns cover natural forms such as "根据甲公司与乙公司 2025 年..." and
    "查阅甲公司 2024 年和 2025 年年度报告...".
    """
    text = re.sub(r"[\n\r]+", " ", question.text)
    captures: list[str] = []
    for match in re.finditer(
        r"(?:根据|查阅|依据|结合)\s*(.+?)(?=(?:19|20)\d{2}\s*年|年度报告|报告中的|报告中)",
        text,
    ):
        captures.append(match.group(1))

    entities: list[str] = []
    for capture in captures:
        cleaned = re.sub(r"^(?:计算题|选择题|多选题)[：:]?", "", capture).strip(" ：:，,。")
        for part in re.split(r"\s*(?:与|和|及|、|/|；|;)\s*", cleaned):
            part = part.strip(" ：:，,。的")
            part = re.sub(r"^(?:根据|查阅|依据|结合)", "", part).strip()
            if not (2 <= len(part) <= 20):
                continue
            if any(stop in part for stop in _ENTITY_STOPWORDS):
                continue
            entities.append(part)
    return tuple(dict.fromkeys(entities))


def _question_years(question: Question) -> tuple[str, ...]:
    # Use the question stem, not option numbers, to avoid mistaking amounts for
    # target report years.
    return tuple(dict.fromkeys(_YEAR_RE.findall(question.text)))


def _active_metrics(question: Question) -> Mapping[str, tuple[str, ...]]:
    compact = _compact(_question_text(question))
    active: dict[str, tuple[str, ...]] = {}
    for key, aliases in _METRIC_GROUPS.items():
        if any(_compact(alias) in compact for alias in aliases):
            active[key] = aliases
    return active


def _document_identity_text(doc_dir: Path, max_pages: int = 10, chars_per_page: int = 2200) -> str:
    parts: list[str] = []
    for page in sorted(doc_dir.glob("page_*.md"))[:max_pages]:
        text = _read(page)
        if text:
            parts.append(text[:chars_per_page])
    return "\n".join(parts)


def _document_year(doc_id: str, identity_text: str) -> str:
    # Prefer an annual-report year encoded in the canonical doc id.  Fall back
    # to the earliest identity-page year only when the id carries none.
    years = _YEAR_RE.findall(str(doc_id))
    if years:
        return years[-1]
    identity_years = _YEAR_RE.findall(identity_text)
    return identity_years[0] if identity_years else ""


def _direct_statement_kind(text: str) -> str:
    """Return a statement kind when the page itself is a financial statement.

    Audit-report prose often mentions several statements in one sentence.  A
    target statement page instead exposes the statement name as one of its first
    headings/lines, so only the leading lines receive this strong structural
    bonus.
    """
    leading = [line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()][:8]
    for line in leading:
        compact = _compact(line)
        # A statement title is short and label-like.  Long audit prose such as
        # "我们审计了……包括合并及公司利润表……" must not be treated as a
        # direct statement page merely because it mentions the same words.
        if len(compact) > 60 or any(noise in compact for noise in ("我们审计", "包括", "财务报表在")):
            continue
        if "利润表" in compact and any(marker in compact for marker in ("合并", "母公司", "公司利润表")):
            return "profit_statement"
        if "现金流量表" in compact and any(marker in compact for marker in ("合并", "母公司", "公司现金流量表")):
            return "cashflow_statement"
    return ""


def _quality(text: str, active_metrics: Mapping[str, tuple[str, ...]]) -> PageQuality:
    compact = _compact(text)
    metric_hits: list[str] = []
    for aliases in active_metrics.values():
        for alias in aliases:
            if _compact(alias) in compact:
                metric_hits.append(alias)
                break
    structure_hits = [signal for signal in _STRUCTURE_SIGNALS if _compact(signal) in compact]
    numeric_count = len(_NUMERIC_RE.findall(text))
    table_like = "|" in text or "<table" in text.lower() or "</tr>" in text.lower()
    no_renderable = bool(_NO_RENDERABLE_RE.search(text))
    sparse = bool(
        no_renderable
        or len(compact) < 180
        or (
            bool(structure_hits or metric_hits)
            and not table_like
            and (numeric_count < 3 or len(compact) < 2200)
        )
    )
    return PageQuality(
        chars=len(text),
        numeric_count=numeric_count,
        table_like=table_like,
        no_renderable_marker=no_renderable,
        metric_hits=tuple(metric_hits),
        structure_hits=tuple(structure_hits),
        sparse=sparse,
    )


def _page_score(
    text: str,
    *,
    question: Question,
    entities: Sequence[str],
    years: Sequence[str],
    active_metrics: Mapping[str, tuple[str, ...]],
) -> tuple[float, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    compact = _compact(text)
    structure_hits = tuple(signal for signal in _STRUCTURE_SIGNALS if _compact(signal) in compact)

    metric_hits: list[str] = []
    for aliases in active_metrics.values():
        for alias in aliases:
            if _compact(alias) in compact:
                metric_hits.append(alias)
                break

    entity_matches = tuple(entity for entity in entities if _compact(entity) in compact)
    year_matches = tuple(year for year in years if year in text)

    # Score structure by semantic group rather than by every overlapping alias.
    # For example, "合并及公司利润表" also contains "公司利润表"; counting both
    # independently would over-promote generic statement/contents pages.
    has_summary = (
        "主要会计数据和财务指标" in compact
        or "主要财务指标" in compact
    )
    has_profit_statement = "利润表" in compact and any(
        marker in compact for marker in ("合并", "母公司", "公司利润表")
    )
    has_cashflow_statement = "现金流量表" in compact and any(
        marker in compact for marker in ("合并", "母公司", "公司现金流量表")
    )

    score = len(metric_hits) * 16.0
    if has_summary:
        score += 80.0
    if has_profit_statement:
        score += 40.0
    if has_cashflow_statement:
        score += 40.0
    direct_statement_kind = _direct_statement_kind(text)
    if direct_statement_kind:
        score += 70.0
    score += min(len(entity_matches), 2) * 4.0
    score += min(len(year_matches), 2) * 2.0

    qcompact = _compact(_question_text(question))
    scope_sensitive = "合并" in qcompact and ("母公司" in qcompact or "公司口径" in qcompact)
    if scope_sensitive:
        has_consolidated = "合并" in compact
        has_parent = "母公司" in compact or "公司" in compact
        if has_consolidated and has_parent:
            score += 24.0
        if has_profit_statement or has_cashflow_statement:
            score += 12.0

    # Annual-report summary pages are especially valuable when several required
    # metrics co-occur, even if the parser lost the table values.
    if len(metric_hits) >= 2:
        score += 18.0
    if len(metric_hits) >= 3:
        score += 18.0

    return score, entity_matches, year_matches, tuple(metric_hits), structure_hits


def _fallback_page(
    fallback_roots: Sequence[Path],
    *,
    domain: str,
    doc_id: str,
    page_name: str,
) -> Path | None:
    for root in fallback_roots:
        candidate = Path(root) / domain / doc_id / page_name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _fallback_has_gain(mineru: PageQuality, fallback: PageQuality) -> bool:
    if not mineru.sparse:
        return False
    numeric_gain = fallback.numeric_count >= max(3, mineru.numeric_count + 3)
    text_gain = fallback.chars >= max(300, int(mineru.chars * 1.35))
    semantic_preserved = bool(fallback.metric_hits or fallback.structure_hits)
    return semantic_preserved and (numeric_gain or (fallback.table_like and text_gain))


def _focused_excerpt(text: str, active_metrics: Mapping[str, tuple[str, ...]], limit: int = 9000) -> str:
    if len(text) <= limit:
        return text.strip()
    compact_terms = [alias for aliases in active_metrics.values() for alias in aliases]
    positions = [text.find(term) for term in compact_terms if text.find(term) >= 0]
    for structure in _STRUCTURE_SIGNALS:
        pos = text.find(structure)
        if pos >= 0:
            positions.append(pos)
    center = min(positions) if positions else 0
    start = max(0, center - limit // 5)
    end = min(len(text), start + limit)
    return text[start:end].strip()


def locate_financial_target_pages(
    question: Question,
    doc_dirs: Mapping[str, Path],
    *,
    baseline_candidates: Sequence[EvidenceCandidate] = (),
    fallback_roots: Sequence[Path] = (),
    max_pages_per_doc: int = 4,
) -> tuple[list[EvidenceCandidate], dict[str, Any]]:
    """Return baseline-preserving target-page candidates and an offline audit.

    ``doc_dirs`` must already be the retriever's resolved document scope.  The
    function never discovers or opens a new document id.
    """
    if question.domain != "financial_reports":
        return [], {"enabled": False, "reason": "non_financial_domain", "provider_calls": 0}
    split = str(dict(question.raw or {}).get("split") or "").strip().upper()
    if split and split != "B":
        return [], {"enabled": False, "reason": "non_multi_slot_split", "provider_calls": 0}

    entities = _extract_entities(question)
    years = _question_years(question)
    active_metrics = _active_metrics(question)
    if not active_metrics:
        return [], {
            "enabled": True,
            "reason": "no_financial_metric_signal",
            "provider_calls": 0,
            "entities": list(entities),
            "years": list(years),
        }
    qcompact = _compact(_question_text(question))
    scope_sensitive = "合并" in qcompact and ("母公司" in qcompact or "公司口径" in qcompact)
    if len(active_metrics) < 2 and not scope_sensitive:
        return [], {
            "enabled": True,
            "reason": "insufficient_multi_metric_target_page_signal",
            "provider_calls": 0,
            "entities": list(entities),
            "years": list(years),
            "active_metric_groups": list(active_metrics),
        }

    baseline_sources = {str(candidate.source) for candidate in baseline_candidates}
    baseline_identities = {
        (str(candidate.doc_id), Path(str(candidate.source)).name)
        for candidate in baseline_candidates
    }

    additions: list[EvidenceCandidate] = []
    page_audits: list[LocatorPageAudit] = []
    rejected_docs: list[dict[str, Any]] = []
    target_docs: list[str] = []

    for doc_id, raw_doc_dir in doc_dirs.items():
        doc_dir = Path(raw_doc_dir)
        identity_text = _document_identity_text(doc_dir)
        compact_identity = _compact(identity_text)
        entity_matches = tuple(entity for entity in entities if _compact(entity) in compact_identity)
        doc_year = _document_year(str(doc_id), identity_text)

        if entities and not entity_matches:
            rejected_docs.append({"doc_id": str(doc_id), "reason": "entity_mismatch"})
            continue
        if years and doc_year and doc_year not in years:
            rejected_docs.append({"doc_id": str(doc_id), "reason": "year_mismatch", "doc_year": doc_year})
            continue

        target_docs.append(str(doc_id))
        scored: list[tuple[float, Path, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
        for page in sorted(doc_dir.glob("page_*.md")):
            text = _read(page)
            if not text.strip():
                continue
            score, page_entities, page_years, metric_hits, structure_hits = _page_score(
                text,
                question=question,
                entities=entities,
                years=years,
                active_metrics=active_metrics,
            )
            if score <= 0:
                continue
            scored.append((score, page, text, page_entities, page_years, metric_hits, structure_hits))

        # Select complementary page roles instead of a pure Top-K.  The
        # selected page's same-page fallback may already cover several metrics;
        # only metrics still missing after that recovery get an extra page.
        scored.sort(key=lambda row: (-row[0], str(row[1])))
        selected: list[tuple[float, Path, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
        selected_names: set[str] = set()
        effective_text_cache: dict[str, str] = {}

        def add_first(predicate: Any, *, limit: int = 1) -> None:
            added = 0
            for row in scored:
                if row[1].name in selected_names or row[0] < 16.0:
                    continue
                if not predicate(row):
                    continue
                selected.append(row)
                selected_names.add(row[1].name)
                added += 1
                if added >= limit:
                    break

        def effective_row_text(row: tuple[float, Path, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]) -> str:
            page = row[1]
            cached = effective_text_cache.get(page.name)
            if cached is not None:
                return cached
            mineru_text = row[2]
            mineru_quality = _quality(mineru_text, active_metrics)
            fallback_path = _fallback_page(
                fallback_roots,
                domain=question.domain,
                doc_id=str(doc_id),
                page_name=page.name,
            )
            fallback_text = _read(fallback_path) if fallback_path is not None else ""
            fallback_quality = _quality(fallback_text, active_metrics) if fallback_text else None
            if (
                fallback_quality is not None
                and _fallback_has_gain(mineru_quality, fallback_quality)
            ):
                effective_text_cache[page.name] = fallback_text
            else:
                effective_text_cache[page.name] = mineru_text
            return effective_text_cache[page.name]

        def covered_metric_groups() -> set[str]:
            covered: set[str] = set()
            for row in selected:
                compact_text = _compact(effective_row_text(row))
                for group, aliases in active_metrics.items():
                    if any(_compact(alias) in compact_text for alias in aliases):
                        covered.add(group)
            return covered

        # Prefer the earliest canonical annual-report summary page.  The generic
        # section title "公司简介和主要财务指标" is only a fallback signal because
        # it usually precedes the actual data table by one or more pages.
        exact_summary_rows = [
            row for row in scored
            if row[0] >= 16.0 and "主要会计数据和财务指标" in _compact(row[2])
        ]
        generic_summary_rows = [
            row for row in scored
            if row[0] >= 16.0 and "主要财务指标" in _compact(row[2])
        ]
        summary_rows = exact_summary_rows or generic_summary_rows
        summary_row = None
        if summary_rows:
            summary_row = min(summary_rows, key=lambda row: str(row[1]))
            selected.append(summary_row)
            selected_names.add(summary_row[1].name)

        by_name = {row[1].name: row for row in scored}

        # Controlled one-page continuation for summary tables.  Only keep the
        # next page when its effective same-page text covers a metric still
        # absent from the summary page, which catches split tables without
        # turning neighborhood expansion into a general page sweep.
        if summary_row is not None:
            covered_after_summary = covered_metric_groups()
            missing_groups = set(active_metrics) - covered_after_summary
            match = _PAGE_RE.search(summary_row[1].name)
            if missing_groups and match:
                next_name = f"page_{int(match.group(1)) + 1:04d}.md"
                neighbor = by_name.get(next_name)
                if neighbor is not None and next_name not in selected_names:
                    neighbor_text = _compact(effective_row_text(neighbor))
                    if any(
                        any(_compact(alias) in neighbor_text for alias in active_metrics[group])
                        for group in missing_groups
                    ):
                        selected.append(neighbor)
                        selected_names.add(next_name)

        # Consolidated-vs-parent questions require formal statement pages even
        # when an annual-summary page already contains headline metrics.  Keep
        # a direct continuation of the first profit statement before looking for
        # another unrelated profit-statement mention.
        if scope_sensitive:
            add_first(lambda row: _direct_statement_kind(row[2]) == "profit_statement")
            profit_seeds = [row for row in selected if _direct_statement_kind(row[2]) == "profit_statement"]
            if profit_seeds:
                match = _PAGE_RE.search(profit_seeds[0][1].name)
                if match:
                    next_name = f"page_{int(match.group(1)) + 1:04d}.md"
                    neighbor = by_name.get(next_name)
                    if (
                        neighbor is not None
                        and next_name not in selected_names
                        and _direct_statement_kind(neighbor[2]) == "profit_statement"
                    ):
                        selected.append(neighbor)
                        selected_names.add(next_name)
            if sum(1 for row in selected if _direct_statement_kind(row[2]) == "profit_statement") < 2:
                add_first(lambda row: _direct_statement_kind(row[2]) == "profit_statement")
            add_first(lambda row: _direct_statement_kind(row[2]) == "cashflow_statement")

        covered = covered_metric_groups()
        for group, aliases in active_metrics.items():
            if group in covered:
                continue
            add_first(
                lambda row, aliases=aliases: any(
                    _compact(alias) in _compact(row[2]) for alias in aliases
                )
            )
            covered = covered_metric_groups()

        # Fail-closed fallback for unusual report layouts: retain one strongest
        # target-signal page if none of the structured slots matched.  Do not
        # fill an arbitrary quota; unnecessary candidates dilute later ranking.
        if not selected:
            add_first(lambda row: bool(row[5] or row[6]))

        selected = selected[: max(max_pages_per_doc + 3, 1)]

        for score, page, mineru_text, page_entities, page_years, metric_hits, structure_hits in selected:
            mineru_quality = _quality(mineru_text, active_metrics)
            fallback_path = _fallback_page(
                fallback_roots,
                domain=question.domain,
                doc_id=str(doc_id),
                page_name=page.name,
            )
            fallback_text = _read(fallback_path) if fallback_path is not None else ""
            fallback_quality = _quality(fallback_text, active_metrics) if fallback_text else None
            use_fallback = bool(
                fallback_path is not None
                and fallback_quality is not None
                and _fallback_has_gain(mineru_quality, fallback_quality)
            )

            source = fallback_path if use_fallback and fallback_path is not None else page
            chosen_text = fallback_text if use_fallback else mineru_text
            parser = "pymupdf4llm" if use_fallback else "mineru"
            identity = (str(doc_id), page.name)

            # If the exact MinerU page is already in baseline and no parser gain
            # exists, preserve it without adding a duplicate full-page candidate.
            should_add = use_fallback or identity not in baseline_identities
            if should_add and chosen_text.strip():
                additions.append(
                    EvidenceCandidate(
                        domain=question.domain,
                        doc_id=str(doc_id),
                        source=str(source),
                        text=_focused_excerpt(chosen_text, active_metrics),
                        before_text="",
                        after_text="",
                        section_title=(structure_hits[0] if structure_hits else "financial target page"),
                        score=1000.0 + score,
                        retriever="financial_target_page_locator",
                        metadata={
                            "financial_target_page": True,
                            "target_page_name": page.name,
                            "target_page_score": score,
                            "target_page_entities": list(page_entities or entity_matches),
                            "target_page_years": list(page_years or ((doc_year,) if doc_year else ())),
                            "target_page_metric_hits": list(metric_hits),
                            "target_page_structure_hits": list(structure_hits),
                            "target_page_parser": parser,
                            "target_page_parser_fallback_used": use_fallback,
                            "target_page_mineru_source": str(page),
                            "target_page_baseline_identity_present": identity in baseline_identities,
                            "target_page_baseline_source_present": str(page) in baseline_sources,
                            "provider_calls": 0,
                        },
                    )
                )

            page_audits.append(
                LocatorPageAudit(
                    doc_id=str(doc_id),
                    page=page.name,
                    score=score,
                    entity_matches=tuple(page_entities or entity_matches),
                    year_matches=tuple(page_years or ((doc_year,) if doc_year else ())),
                    metric_hits=tuple(metric_hits),
                    structure_hits=tuple(structure_hits),
                    parser_used=parser,
                    fallback_used=use_fallback,
                    mineru_quality=mineru_quality,
                    fallback_quality=fallback_quality,
                )
            )

    # De-duplicate additions without changing baseline ordering or contents.
    unique: list[EvidenceCandidate] = []
    seen_additions: set[tuple[str, str, str]] = set()
    for candidate in additions:
        key = (
            str(candidate.doc_id),
            str(candidate.metadata.get("target_page_name") or Path(candidate.source).name),
            str(candidate.metadata.get("target_page_parser") or ""),
        )
        if key in seen_additions:
            continue
        seen_additions.add(key)
        unique.append(candidate)

    audit = {
        "enabled": True,
        "provider_calls": 0,
        "entities": list(entities),
        "years": list(years),
        "active_metric_groups": list(active_metrics),
        "target_docs": target_docs,
        "rejected_docs": rejected_docs,
        "baseline_candidate_count": len(baseline_candidates),
        "augmentation_candidate_count": len(unique),
        "fallback_used_count": sum(1 for row in page_audits if row.fallback_used),
        "pages": [row.to_dict() for row in page_audits],
    }
    return unique, audit
