"""Generic local-corpus option adjudication for evaluation-only evidence audits.

The adjudicator is qid-agnostic: it receives a question, resolves declared
local documents, retrieves high-overlap windows, and judges option claims from
metric/value/polarity bindings.  It is intentionally separate from production
answer routing.  Its output can reconfirm a baseline or identify an evidence
conflict, but it cannot self-authorize a leaderboard delta.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


_NUM_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")
_PERCENT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*[%％]")
_AMOUNT_RE = re.compile(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(万亿元|亿元|万元|元)")
_AMOUNT_UNIT_MULTIPLIER = {"元": 1.0, "万元": 1e4, "亿元": 1e8, "万亿元": 1e12}
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_PAGE_FILE_RE = re.compile(r"^page_(\d+)\.md$", re.IGNORECASE)

# Cross-domain metric/clause phrases.  These are semantic vocabulary, not qid
# or final-answer rules.  Longest match wins when binding a numeric claim.
_METRIC_PHRASES = tuple(sorted({
    "研发投入占营业收入比例",
    "研发费用占营业收入比重",
    "研发费用占营业收入比例",
    "研发支出占营业收入比重",
    "研发支出占营业收入比例",
    "营业收入同比增长率",
    "营业收入同比",
    "营收同比增长率",
    "营收同比",
    "营业收入",
    "营收",
    "经营活动产生的现金流量净额",
    "筹资活动产生的现金流量净额",
    "现金分红占合并报表归属于上市公司股东净利润的比例",
    "现金分红占归母净利润比例",
    "每10股派发现金分红",
    "现金分红方案",
    "新签合同总额",
    "新签合同额",
    "金融信创市场规模",
    "内置检测规则",
    "解析规则",
    "净利润",
    "送红股",
    "行政处罚",
    "分类评价得分",
    "关联交易事项",
    "重大资产重组",
    "直接负责的主管人员",
    "发行公告日期",
    "公告日期",
    "发行日期",
    "施行",
}, key=len, reverse=True))

_OPPOSITE_MARKERS = (
    ("增长", ("下降", "减少", "下滑", "微降", "降低", "负增长")),
    ("上升", ("下降", "减少", "下滑", "微降", "降低", "负增长")),
    ("增加", ("下降", "减少", "下滑", "微降", "降低")),
    ("盈利", ("亏损",)),
    ("包含", ("不送红股", "不包含")),
    ("送红股", ("不送红股",)),
    ("不会受到影响", ("扣分", "下调", "受到影响")),
    ("不受影响", ("扣分", "下调", "受到影响")),
)

_POSITIVE_DIRECTION_MARKERS = ("增长", "上升", "增加", "提高", "提升", "扩大")
_NEGATIVE_DIRECTION_MARKERS = ("下降", "减少", "下滑", "微降", "降低", "负增长", "缩减")


def _direction_polarity(text: str) -> int:
    compact = _normalize(text)
    positive = any(_normalize(marker) in compact for marker in _POSITIVE_DIRECTION_MARKERS)
    negative = any(_normalize(marker) in compact for marker in _NEGATIVE_DIRECTION_MARKERS)
    if positive and not negative:
        return 1
    if negative and not positive:
        return -1
    return 0

_ENTITY_SUFFIX_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{2,12}(?:科技|时代|建筑|银行|证券|保险|租赁|集团))"
    r"(?=\s*(?:(?:19|20)\d{2}\s*年|[，,:：；。]|$))"
)
_ENTITY_PLACEHOLDER_RE = re.compile(r"([甲乙丙丁某]公司)")
_ENTITY_TITLE_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9]{2,10})(?=时任(?:董事长|总经理|董事|监事|法定代表人))"
)
_ENTITY_YEAR_PREFIX_RE = re.compile(r"^\s*([\u4e00-\u9fffA-Za-z0-9]{2,8})\s*(?=(?:19|20)\d{2}\s*年)")
_ENTITY_PREFIX_STOP_TERMS = (
    "公司", "规定", "新规", "办法", "规则", "评价", "报告", "方案", "证券", "分类", "年度", "关于",
)


@dataclass(frozen=True)
class EvidenceWindow:
    doc_id: str
    source_path: str
    score: float
    text: str
    matched_terms: tuple[str, ...]
    page_number: int | None = None
    source_page_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _EvidenceSource:
    path: Path
    page_number: int | None
    source_page_index: int | None
    page_resolution_gap: str = ""


@dataclass(frozen=True)
class OptionAdjudication:
    label: str
    option_text: str
    relation: str
    confidence: str
    reason: str
    metric_phrase: str
    option_numbers: tuple[str, ...]
    bound_evidence_number: str
    evidence: tuple[EvidenceWindow, ...]
    page_resolution_gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["evidence"] = [item.to_dict() for item in self.evidence]
        row["page_resolution_gaps"] = list(self.page_resolution_gaps)
        return row


def _normalize(text: str) -> str:
    text = _TAG_RE.sub("", str(text or ""))
    text = text.replace("％", "%")
    return _SPACE_RE.sub("", text).lower()


def _numbers(text: str) -> tuple[str, ...]:
    return tuple(_NUM_RE.findall(str(text or "")))


def extract_option_entities(text: str) -> tuple[str, ...]:
    """Extract explicit option-side entities conservatively.

    The gate only activates for high-confidence entity mentions such as named
    companies/organizations, placeholder companies used in tests, or a named
    issuer before a year/title phrase. Generic regulatory subjects remain
    unbound so clause-based adjudication still works.
    """
    raw = str(text or "")
    candidates: list[str] = []
    candidates.extend(_ENTITY_PLACEHOLDER_RE.findall(raw))
    candidates.extend(_ENTITY_SUFFIX_RE.findall(raw))
    candidates.extend(_ENTITY_TITLE_RE.findall(raw))
    prefix = _ENTITY_YEAR_PREFIX_RE.search(raw)
    if prefix:
        candidate = prefix.group(1)
        if not any(term in candidate for term in _ENTITY_PREFIX_STOP_TERMS):
            candidates.append(candidate)
    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        key = _normalize(candidate)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return tuple(output)


@lru_cache(maxsize=512)
def _document_subject_head(source_path: str, doc_id: str) -> str:
    path = Path(source_path)
    candidates: list[Path] = [path]
    parts = list(path.parts)
    for marker in ("processed_pymupdf4llm", "processed_mineru_retrieval"):
        if marker in parts:
            idx = parts.index(marker)
            if len(parts) > idx + 2:
                data_root = Path(*parts[:idx])
                domain = parts[idx + 1]
                canonical = data_root / "processed_mineru" / domain / doc_id / "auto" / f"{doc_id}.md"
                candidates.insert(0, canonical)
            break
    for candidate in candidates:
        if candidate.is_file():
            return _read_text_cached(str(candidate))[:5000]
    return ""


def _document_subject_matches_entities(option_entities: Sequence[str], evidence: EvidenceWindow) -> bool:
    if not option_entities:
        return True
    head = _document_subject_head(evidence.source_path, evidence.doc_id)
    compact_head = _normalize(head)
    is_subject_document = "年度报告" in compact_head or "annualreport" in compact_head
    return bool(is_subject_document and all(_normalize(entity) in compact_head for entity in option_entities))


def _evidence_matches_entities(option_entities: Sequence[str], evidence: EvidenceWindow) -> bool:
    if not option_entities:
        return True
    compact = _normalize(evidence.text)
    if all(_normalize(entity) in compact for entity in option_entities):
        return True
    return _document_subject_matches_entities(option_entities, evidence)


def _specific_period_tokens(text: str) -> tuple[str, ...]:
    compact = _normalize(text)
    tokens = re.findall(
        r"((?:19|20)\d{2}年?(?:前三季度|前3季度|第一季度|第二季度|第三季度|第四季度|一季度|二季度|三季度|四季度|上半年|下半年|半年度))",
        compact,
    )
    return tuple(dict.fromkeys(tokens))


def _evidence_matches_period(option_text: str, evidence: EvidenceWindow) -> bool:
    required = _specific_period_tokens(option_text)
    if not required:
        return True
    compact = _normalize(evidence.text)
    return all(_normalize(period) in compact for period in required)


def _evidence_matches_binding_context(option_text: str, option_entities: Sequence[str], evidence: EvidenceWindow) -> bool:
    return _evidence_matches_entities(option_entities, evidence) and _evidence_matches_period(option_text, evidence)


def _metric_phrases(text: str) -> tuple[str, ...]:
    compact = _normalize(text)
    return tuple(phrase for phrase in _METRIC_PHRASES if _normalize(phrase) in compact)


def _metric_phrase(text: str) -> str:
    phrases = _metric_phrases(text)
    return phrases[0] if phrases else ""


def _query_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = list(extract_option_entities(text))
    compact = _normalize(text)
    for phrase in _METRIC_PHRASES:
        if _normalize(phrase) in compact:
            terms.append(phrase)
    terms.extend(_PERCENT_RE.findall(text))
    terms.extend(_YEAR_RE.findall(text))
    # Numeric values are valuable anchors for financial/regulatory claims.
    terms.extend(_numbers(text))
    # Add medium-length Chinese runs as fallback semantic anchors.
    for run in re.findall(r"[\u4e00-\u9fff]{4,18}", text):
        if run not in terms:
            terms.append(run)
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = _normalize(term)
        if key and key not in seen:
            seen.add(key)
            out.append(term)
    return tuple(out[:18])


def _page_identity(path: Path) -> tuple[int | None, int | None]:
    match = _PAGE_FILE_RE.match(path.name)
    if not match:
        return None, None
    page_number = int(match.group(1))
    return page_number, max(0, page_number - 1)


def _candidate_sources(data_root: Path, domain: str, doc_id: str) -> tuple[list[_EvidenceSource], tuple[str, ...]]:
    """Return page-addressable sources, preferring adapted page contracts.

    Whole-document canonical Markdown is intentionally not returned as an
    adjudication source. It may help subject identity lookup elsewhere, but it
    cannot support or contradict an option because it has no stable page
    identity.
    """
    page_roots = (
        data_root / "processed_mineru_retrieval" / domain / doc_id,
        data_root / "processed_pymupdf4llm" / domain / doc_id,
    )
    sources: list[_EvidenceSource] = []
    seen: set[str] = set()
    for root in page_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("page_*.md")):
            page_number, source_page_index = _page_identity(path)
            if page_number is None or source_page_index is None:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                _EvidenceSource(
                    path=path,
                    page_number=page_number,
                    source_page_index=source_page_index,
                )
            )
    if sources:
        return sources, ()

    fallbacks = (
        data_root / "processed_mineru" / domain / doc_id / "auto" / f"{doc_id}.md",
        data_root / "processed_mineru" / domain / doc_id / f"{doc_id}.md",
    )
    existing = [path for path in fallbacks if path.is_file()]
    if existing:
        return [], (f"{doc_id}:page_level_source_unavailable",)
    return [], (f"{doc_id}:source_unavailable",)


@lru_cache(maxsize=512)
def _read_text_cached(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8", errors="ignore")


@lru_cache(maxsize=512)
def _windows_cached(path_str: str, radius: int = 850) -> tuple[str, ...]:
    text = _read_text_cached(path_str)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|(?<=。)\s+", text) if part.strip()]
    output: list[str] = []
    if paragraphs:
        for part in paragraphs:
            if len(part) <= radius * 2:
                output.append(part)
            else:
                for start in range(0, len(part), radius):
                    output.append(part[max(0, start - radius // 2): start + radius])
    else:
        for start in range(0, len(text), radius):
            output.append(text[max(0, start - radius // 2): start + radius])
    return tuple(output)


def _retrieve_option_windows_with_gaps(
    *,
    data_root: Path,
    domain: str,
    doc_ids: Sequence[str],
    option_text: str,
    top_k: int = 5,
) -> tuple[list[EvidenceWindow], tuple[str, ...]]:
    terms = _query_terms(option_text)
    option_entities = extract_option_entities(option_text)
    scored: list[EvidenceWindow] = []
    page_resolution_gaps: list[str] = []
    for doc_id in doc_ids:
        sources, gaps = _candidate_sources(data_root, domain, str(doc_id))
        page_resolution_gaps.extend(gaps)
        for source in sources:
            for window in _windows_cached(str(source.path)):
                compact = _normalize(window)
                matched = tuple(term for term in terms if _normalize(term) in compact)
                if not matched:
                    continue
                score = 0.0
                for term in matched:
                    normalized = _normalize(term)
                    score += 4.0 if _NUM_RE.fullmatch(normalized) else min(8.0, 1.0 + len(normalized) / 4.0)
                metric = _metric_phrase(option_text)
                if metric and _normalize(metric) in compact:
                    score += 10.0
                entity_probe = EvidenceWindow(
                    doc_id=str(doc_id),
                    source_path=str(source.path),
                    score=0.0,
                    text=window,
                    matched_terms=matched,
                    page_number=source.page_number,
                    source_page_index=source.source_page_index,
                )
                if option_entities and _evidence_matches_entities(option_entities, entity_probe):
                    score += 20.0
                scored.append(EvidenceWindow(
                    doc_id=str(doc_id),
                    source_path=str(source.path),
                    score=round(score, 4),
                    text=window,
                    matched_terms=matched,
                    page_number=source.page_number,
                    source_page_index=source.source_page_index,
                ))
    scored.sort(key=lambda row: (-row.score, row.source_path, row.text[:30]))
    unique: list[EvidenceWindow] = []
    seen: set[tuple[str, str]] = set()
    for row in scored:
        key = (row.source_path, _normalize(row.text))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) >= top_k:
            break
    return unique, tuple(dict.fromkeys(page_resolution_gaps))


def retrieve_option_windows(
    *,
    data_root: Path,
    domain: str,
    doc_ids: Sequence[str],
    option_text: str,
    top_k: int = 5,
) -> list[EvidenceWindow]:
    windows, _ = _retrieve_option_windows_with_gaps(
        data_root=data_root,
        domain=domain,
        doc_ids=doc_ids,
        option_text=option_text,
        top_k=top_k,
    )
    return windows


@dataclass(frozen=True)
class _Quantity:
    raw: str
    value: float
    normalized_value: float
    unit: str
    kind: str


def _page_resolved(evidence: EvidenceWindow) -> bool:
    return (
        evidence.page_number is not None
        and evidence.source_page_index is not None
        and bool(evidence.source_path)
        and _PAGE_FILE_RE.match(Path(evidence.source_path).name) is not None
    )


def _explicit_years(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_YEAR_RE.findall(str(text or ""))))


def _evidence_matches_explicit_years(option_text: str, evidence: EvidenceWindow) -> bool:
    required = _explicit_years(option_text)
    if not required:
        return True
    combined = _normalize(evidence.text + " " + _document_subject_head(evidence.source_path, evidence.doc_id))
    return all(year in combined for year in required)


def _evidence_matches_high_confidence_context(
    option_text: str,
    option_entities: Sequence[str],
    evidence: EvidenceWindow,
) -> bool:
    return (
        _page_resolved(evidence)
        and _evidence_matches_binding_context(option_text, option_entities, evidence)
        and _evidence_matches_explicit_years(option_text, evidence)
    )


def _compatible_metric_index(metric: str, window: str) -> int:
    if not metric:
        return -1
    compact = _normalize(window)
    metric_compact = _normalize(metric)
    start = 0
    incompatible_suffixes = ("增幅", "增长率", "同比", "占比", "比例", "比重")
    while True:
        idx = compact.find(metric_compact, start)
        if idx < 0:
            return -1
        suffix = compact[idx + len(metric_compact): idx + len(metric_compact) + 8]
        if metric in {"营业收入", "营收"} and any(suffix.startswith(item) for item in incompatible_suffixes):
            start = idx + len(metric_compact)
            continue
        return idx


def _metric_local_section(metric: str, window: str, max_chars: int = 320) -> str:
    compact = _normalize(window)
    idx = _compatible_metric_index(metric, window)
    if idx < 0:
        return ""
    metric_compact = _normalize(metric)
    tail = compact[idx + len(metric_compact): idx + len(metric_compact) + max_chars]
    cut_points: list[int] = []
    for delimiter in ("。", "；", ";"):
        pos = tail.find(delimiter)
        if pos > 0:
            cut_points.append(pos)
    for other in _METRIC_PHRASES:
        other_compact = _normalize(other)
        if not other_compact or other_compact == metric_compact:
            continue
        pos = tail.find(other_compact)
        if pos > 0:
            cut_points.append(pos)
    if cut_points:
        tail = tail[: min(cut_points)]
    return tail


def _semantic_anchor_section(anchor: str, window: str, radius: int = 180) -> str:
    compact = _normalize(window)
    anchor_compact = _normalize(anchor)
    idx = compact.find(anchor_compact)
    if idx < 0:
        return ""
    return compact[max(0, idx - radius): idx + len(anchor_compact) + radius]


def _quantities(text: str) -> tuple[_Quantity, ...]:
    output: list[_Quantity] = []
    for raw, unit in _AMOUNT_RE.findall(str(text or "")):
        value = float(raw.replace(",", ""))
        output.append(
            _Quantity(
                raw=raw,
                value=value,
                normalized_value=value * _AMOUNT_UNIT_MULTIPLIER[unit],
                unit=unit,
                kind="amount",
            )
        )
    for raw in _PERCENT_RE.findall(str(text or "")):
        normalized = raw.replace("％", "%").replace("%", "")
        output.append(
            _Quantity(
                raw=normalized,
                value=float(normalized.replace(",", "")),
                normalized_value=float(normalized.replace(",", "")),
                unit="%",
                kind="percent",
            )
        )
    return tuple(output)


def _unique_quantities(values: Sequence[_Quantity]) -> tuple[_Quantity, ...]:
    output: list[_Quantity] = []
    seen: set[tuple[str, float]] = set()
    for value in values:
        key = (value.kind, round(value.normalized_value, 8))
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return tuple(output)


def _quantity_equal(left: _Quantity, right: _Quantity) -> bool:
    if left.kind != right.kind:
        return False
    tolerance = max(1e-6, abs(left.normalized_value) * 1e-9, abs(right.normalized_value) * 1e-9)
    return abs(left.normalized_value - right.normalized_value) <= tolerance


def _bound_number(metric: str, window: str) -> str:
    section = _metric_local_section(metric, window)
    quantities = _unique_quantities(_quantities(section))
    return quantities[0].raw if len(quantities) == 1 else ""


def _numeric_comparator(
    option_text: str,
    metric: str,
    windows: Sequence[EvidenceWindow],
    option_entities: Sequence[str],
) -> tuple[str, str, str, EvidenceWindow] | None:
    match = re.search(r"(?:超过|大于|高于)\s*([-+]?\d(?:[\d,]*\d)?(?:\.\d+)?)\s*[%％]", option_text)
    if not match or not metric:
        return None
    threshold = float(match.group(1).replace(",", ""))
    for evidence in windows:
        if not _evidence_matches_high_confidence_context(option_text, option_entities, evidence):
            continue
        section = _metric_local_section(metric, evidence.text)
        values = _unique_quantities([value for value in _quantities(section) if value.kind == "percent"])
        if len(values) != 1:
            continue
        value = values[0]
        relation = "SUPPORTED" if value.normalized_value > threshold else "CONTRADICTED"
        return relation, value.raw, f"threshold={threshold}%;evidence={value.raw}", evidence
    return None


def _amount_threshold_comparator(
    option_text: str,
    metric: str,
    windows: Sequence[EvidenceWindow],
    option_entities: Sequence[str],
) -> tuple[str, str, str, EvidenceWindow] | None:
    match = re.search(r"(?:超过|大于|高于)\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(万亿元|亿元|万元|元)", option_text)
    if not match or not metric:
        return None
    threshold_raw, threshold_unit = match.groups()
    threshold = float(threshold_raw.replace(",", "")) * _AMOUNT_UNIT_MULTIPLIER[threshold_unit]
    for evidence in windows:
        if not _evidence_matches_high_confidence_context(option_text, option_entities, evidence):
            continue
        section = _metric_local_section(metric, evidence.text)
        values = _unique_quantities([value for value in _quantities(section) if value.kind == "amount"])
        if len(values) != 1:
            continue
        value = values[0]
        relation = "SUPPORTED" if value.normalized_value > threshold else "CONTRADICTED"
        reason = f"threshold={threshold_raw}{threshold_unit};evidence={value.raw}"
        return relation, value.raw, reason, evidence
    return None


def _metric_quantity_binding(
    option_text: str,
    metric: str,
    windows: Sequence[EvidenceWindow],
    option_entities: Sequence[str],
) -> tuple[str, str, str, EvidenceWindow] | None:
    expected = _unique_quantities(_quantities(option_text))
    if not metric or len(expected) != 1:
        return None
    expected_value = expected[0]
    for evidence in windows:
        if not _evidence_matches_high_confidence_context(option_text, option_entities, evidence):
            continue
        section = _metric_local_section(metric, evidence.text)
        observed = _unique_quantities(
            [value for value in _quantities(section) if value.kind == expected_value.kind]
        )
        if any(_quantity_equal(expected_value, value) for value in observed):
            match = next(value for value in observed if _quantity_equal(expected_value, value))
            return "SUPPORTED", match.raw, "metric_quantity_bound", evidence
        if len(observed) == 1:
            return "CONTRADICTED", observed[0].raw, "metric_quantity_bound_to_different_value", evidence
    return None


def _directional_contradiction(
    option_text: str,
    metric: str,
    windows: Sequence[EvidenceWindow],
    option_entities: Sequence[str],
) -> tuple[str, str, EvidenceWindow] | None:
    semantic_anchors = _metric_phrases(option_text)
    if not semantic_anchors:
        return None
    for positive, negatives in _OPPOSITE_MARKERS:
        if positive not in option_text:
            continue
        for evidence in windows:
            if not _evidence_matches_high_confidence_context(option_text, option_entities, evidence):
                continue
            for anchor in semantic_anchors:
                section = _semantic_anchor_section(anchor, evidence.text)
                if not section:
                    continue
                for negative in negatives:
                    if _normalize(negative) in section:
                        return f"opposite_marker:{positive}->{negative}", _bound_number(metric, evidence.text), evidence
    return None


def adjudicate_option(
    *,
    label: str,
    option_text: str,
    windows: Sequence[EvidenceWindow],
    page_resolution_gaps: Sequence[str] = (),
) -> OptionAdjudication:
    metric = _metric_phrase(option_text)
    option_numbers = tuple(number for number in _numbers(option_text) if len(number) > 0)
    option_entities = extract_option_entities(option_text)
    compact_option = _normalize(option_text)
    unresolved_gaps = list(page_resolution_gaps)
    valid_windows: list[EvidenceWindow] = []
    for evidence in windows:
        if _page_resolved(evidence):
            valid_windows.append(evidence)
        else:
            unresolved_gaps.append(f"{evidence.source_path or evidence.doc_id}:page_identity_missing")
    unresolved_gaps = list(dict.fromkeys(unresolved_gaps))

    if not valid_windows:
        reason = "page_resolution_gap_no_page_level_evidence" if unresolved_gaps else "no_local_evidence"
        return OptionAdjudication(
            label, option_text, "UNRESOLVED", "LOW", reason, metric, option_numbers, "", (), tuple(unresolved_gaps)
        )

    # Strongest support: the full proposition is reproduced on an addressable page.
    for evidence in valid_windows:
        compact_window = _normalize(evidence.text)
        if compact_option and compact_option in compact_window:
            return OptionAdjudication(
                label, option_text, "SUPPORTED", "HIGH", "full_proposition_reproduced",
                metric, option_numbers, "", (evidence,), tuple(unresolved_gaps)
            )

    comparator = _numeric_comparator(option_text, metric, valid_windows, option_entities)
    if comparator:
        relation, value, reason, evidence = comparator
        return OptionAdjudication(
            label, option_text, relation, "HIGH", f"numeric_comparator:{reason}",
            metric, option_numbers, value, (evidence,), tuple(unresolved_gaps)
        )

    amount_comparator = _amount_threshold_comparator(option_text, metric, valid_windows, option_entities)
    if amount_comparator:
        relation, value, reason, evidence = amount_comparator
        return OptionAdjudication(
            label, option_text, relation, "HIGH", f"amount_comparator:{reason}",
            metric, option_numbers, value, (evidence,), tuple(unresolved_gaps)
        )

    contradiction = _directional_contradiction(option_text, metric, valid_windows, option_entities)
    if contradiction:
        reason, bound, evidence = contradiction
        return OptionAdjudication(
            label, option_text, "CONTRADICTED", "HIGH", reason, metric, option_numbers, bound,
            (evidence,), tuple(unresolved_gaps)
        )

    quantity_binding = _metric_quantity_binding(option_text, metric, valid_windows, option_entities)
    if quantity_binding:
        relation, value, reason, evidence = quantity_binding
        return OptionAdjudication(
            label, option_text, relation, "HIGH", reason, metric, option_numbers, value,
            (evidence,), tuple(unresolved_gaps)
        )

    meaningful_numbers = [
        number for number in option_numbers
        if not (len(number) == 4 and number.startswith(("19", "20")))
    ]
    terms = [term for term in _query_terms(option_text) if not _NUM_RE.fullmatch(_normalize(term))]
    option_polarity = _direction_polarity(option_text)
    for evidence in valid_windows:
        if not _evidence_matches_binding_context(option_text, option_entities, evidence):
            continue
        if not _evidence_matches_explicit_years(option_text, evidence):
            continue
        evidence_polarity = _direction_polarity(evidence.text)
        if option_polarity and evidence_polarity and evidence_polarity != option_polarity:
            continue
        compact_window = _normalize(evidence.text)
        matched = [term for term in terms if _normalize(term) in compact_window]
        evidence_numbers = set(_numbers(evidence.text))
        numeric_ok = not meaningful_numbers or all(number in evidence_numbers for number in meaningful_numbers)
        if numeric_ok and matched and len(matched) >= max(1, min(2, len(terms))):
            return OptionAdjudication(
                label, option_text, "SUPPORTED", "MEDIUM", "semantic_anchor_bundle_present",
                metric, option_numbers, _bound_number(metric, evidence.text), (evidence,), tuple(unresolved_gaps)
            )

    same_entity_windows = [
        evidence for evidence in valid_windows
        if _evidence_matches_entities(option_entities, evidence)
    ]
    binding_context_windows = [
        evidence for evidence in same_entity_windows
        if _evidence_matches_period(option_text, evidence)
        and _evidence_matches_explicit_years(option_text, evidence)
    ]
    if option_entities and not same_entity_windows:
        unresolved_reason = "entity_binding_gate_no_same_entity_evidence"
    elif (_specific_period_tokens(option_text) or _explicit_years(option_text)) and not binding_context_windows:
        unresolved_reason = "period_binding_gate_no_matching_period_evidence"
    elif unresolved_gaps:
        unresolved_reason = "page_resolution_gap_incomplete_source_set"
    else:
        unresolved_reason = "insufficient_generic_local_entailment"
    evidence_tail = (binding_context_windows or same_entity_windows or valid_windows)[:2]
    return OptionAdjudication(
        label, option_text, "UNRESOLVED", "LOW", unresolved_reason, metric, option_numbers, "",
        tuple(evidence_tail), tuple(unresolved_gaps)
    )


def adjudicate_question(
    *,
    data_root: Path,
    domain: str,
    doc_ids: Sequence[str],
    options: Mapping[str, str],
) -> dict[str, Any]:
    rows: list[OptionAdjudication] = []
    for label, option_text in options.items():
        windows, page_resolution_gaps = _retrieve_option_windows_with_gaps(
            data_root=data_root,
            domain=domain,
            doc_ids=doc_ids,
            option_text=option_text,
        )
        rows.append(
            adjudicate_option(
                label=str(label),
                option_text=str(option_text),
                windows=windows,
                page_resolution_gaps=page_resolution_gaps,
            )
        )
    supported = [row.label for row in rows if row.relation == "SUPPORTED"]
    unresolved = [row.label for row in rows if row.relation == "UNRESOLVED"]
    return {
        "answer": "".join(supported) if supported and not unresolved else "",
        "all_options_closed": not unresolved,
        "supported_labels": supported,
        "contradicted_labels": [row.label for row in rows if row.relation == "CONTRADICTED"],
        "unresolved_labels": unresolved,
        "options": [row.to_dict() for row in rows],
        "evaluation_only": True,
        "production_override": False,
        "submission_authorized": False,
    }


__all__ = [
    "EvidenceWindow",
    "OptionAdjudication",
    "extract_option_entities",
    "retrieve_option_windows",
    "adjudicate_option",
    "adjudicate_question",
]
