"""Resolve financial evidence lineage into corpus-backed corrective context.

The locator is deliberately fail-closed. It understands the canonical source
anchors emitted by the MinerU financial ledger and never treats a page index as
an unrelated Markdown line number.  When an anchor cannot be resolved, callers
receive an explicit unresolved result and must keep the evidence ambiguous.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_PAGE_TABLE_ROW_RE = re.compile(
    r"(?:^|&)page_idx=(?P<page>\d+)&table_index=(?P<table>\d+)&row_index=(?P<row>\d+)(?:&|$)"
)
_LINE_ANCHOR_RE = re.compile(r"^(?:line=)?(?P<line>\d+)$")
_PAGE_MARKDOWN_RE = re.compile(r"page[_-]?(?P<page>\d+)\.md$", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_UNIT_RE = re.compile(
    r"(?:单位|金额单位)\s*[：:]?\s*(?:为)?\s*(人民币)?\s*"
    r"(万亿元|亿元|百万元|万元|千元|元)(?:\s*[（(]?含税[）)]?)?"
)
_EXPLICIT_CELL_UNIT_RE = re.compile(
    r"(?:（|\()\s*(人民币)?\s*(万亿元|亿元|百万元|万元|千元|元|%|％|元\s*/\s*股|元\s*/\s*10\s*股)\s*(?:）|\))",
    re.IGNORECASE,
)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _normalise_number(value: Any) -> str:
    return re.sub(r"[^0-9.+-]", "", str(value or ""))


def _item_text(item: Mapping[str, Any]) -> str:
    output: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            content = value.get("content")
            if isinstance(content, str):
                output.append(content)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for nested in value:
                walk(nested)

    walk(item.get("content") or {})
    return " ".join(part.strip() for part in output if part and part.strip())


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell_parts is not None:
            self._row.append("".join(self._cell_parts).strip())
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell_parts = None


def parse_table_rows(html: str) -> tuple[tuple[str, ...], ...]:
    parser = _TableHTMLParser()
    parser.feed(str(html or ""))
    return tuple(tuple(cell for cell in row) for row in parser.rows)


@dataclass(frozen=True)
class SourceLocation:
    source_path: str
    anchor_type: str
    page_idx: int | None
    table_index: int | None
    row_index: int | None
    line_number: int | None
    resolver_status: str
    resolver_failure_reason: str
    resolved_context_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinancialContextPack:
    location: SourceLocation
    context_source_paths: tuple[str, ...]
    context_elements: tuple[Mapping[str, Any], ...]
    column_role_map: Mapping[str, Any]
    unit_header: str
    period_header: str
    statement_scope_header: str
    attribution_scope_header: str
    policy_stage_context: str
    target_column_index: int | None
    target_row: tuple[str, ...]
    header_row: tuple[str, ...]
    adjacent_rows: tuple[tuple[str, ...], ...]
    context_text: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["location"] = self.location.to_dict()
        return payload


def _source_and_fragment(canonical_source: str) -> tuple[str, str]:
    source, separator, fragment = str(canonical_source or "").partition("#")
    return source, fragment if separator else ""


def _resolve_source_path(
    source_text: str,
    *,
    structured_root: Path,
    domain: str,
    doc_id: str,
) -> Path | None:
    source = Path(source_text)
    candidates: list[Path] = []
    if source_text:
        candidates.append(source)
    normalized = source_text.replace("\\", "/")
    marker = "/processed_mineru/"
    if marker in normalized:
        tail = normalized.split(marker, 1)[1]
        candidates.append(structured_root / Path(tail))
    elif normalized.startswith("data/processed_mineru/"):
        candidates.append(structured_root / Path(normalized[len("data/processed_mineru/"):]))
    if doc_id:
        filename = Path(source_text).name
        if filename:
            candidates.extend((
                structured_root / domain / doc_id / "auto" / filename,
                structured_root / domain / doc_id / filename,
            ))
        candidates.extend((
            structured_root / domain / doc_id / "auto" / f"{doc_id}_content_list_v2.json",
            structured_root / domain / doc_id / "auto" / f"{doc_id}.md",
            structured_root / domain / doc_id / f"{doc_id}.md",
        ))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()
    return None


def _anchor(canonical_source: str) -> tuple[str, int | None, int | None, int | None, int | None]:
    source_text, fragment = _source_and_fragment(canonical_source)
    match = _PAGE_TABLE_ROW_RE.search(fragment)
    if match:
        return (
            "page_table_row",
            int(match.group("page")),
            int(match.group("table")),
            int(match.group("row")),
            None,
        )
    line_match = _LINE_ANCHOR_RE.fullmatch(fragment)
    if line_match:
        return "markdown_line", None, None, None, int(line_match.group("line"))
    page_match = _PAGE_MARKDOWN_RE.search(Path(source_text).name)
    if page_match and not fragment:
        return "page_markdown", int(page_match.group("page")), None, None, None
    return "none", None, None, None, None


def _unresolved(
    *,
    path: Path | None,
    anchor_type: str,
    page_idx: int | None,
    table_index: int | None,
    row_index: int | None,
    line_number: int | None,
    reason: str,
) -> FinancialContextPack:
    location = SourceLocation(
        source_path=str(path or ""),
        anchor_type=anchor_type,
        page_idx=page_idx,
        table_index=table_index,
        row_index=row_index,
        line_number=line_number,
        resolver_status="unresolved",
        resolver_failure_reason=reason,
        resolved_context_hash="",
    )
    return FinancialContextPack(
        location=location,
        context_source_paths=tuple(filter(None, (str(path or ""),))),
        context_elements=(),
        column_role_map={},
        unit_header="",
        period_header="",
        statement_scope_header="",
        attribution_scope_header="",
        policy_stage_context="",
        target_column_index=None,
        target_row=(),
        header_row=(),
        adjacent_rows=(),
        context_text="",
    )


def _header_row(rows: Sequence[Sequence[str]], row_index: int) -> tuple[str, ...]:
    for index in range(min(row_index - 1, len(rows) - 1), -1, -1):
        row = tuple(str(cell or "") for cell in rows[index])
        compact = "|".join(_compact(cell) for cell in row)
        if any(token in compact for token in ("本年比上年", "同比", "变化", "项目")) or len(_YEAR_RE.findall(compact)) >= 1:
            return row
    return ()


def _target_column(
    target_row: Sequence[str],
    header_row: Sequence[str],
    candidate: Mapping[str, Any],
) -> int | None:
    metadata = dict(candidate.get("metadata") or {})
    raw_value = metadata.get("raw_value")
    if raw_value in (None, ""):
        raw_value = candidate.get("value")
    normalized = _normalise_number(raw_value)
    if normalized:
        exact = [
            index for index, cell in enumerate(target_row)
            if _normalise_number(cell) == normalized
        ]
        if exact:
            return exact[0]
    column_role = str(metadata.get("column_role") or "")
    period = str(candidate.get("period") or "")
    comparison_period = str(candidate.get("comparison_period") or metadata.get("comparison_period") or "")
    preferred_year = period if column_role != "prior" else comparison_period or period
    if header_row and preferred_year:
        for index, cell in enumerate(header_row):
            if preferred_year in str(cell):
                return index
    if len(target_row) > 1:
        return 1
    return None


def _column_roles(
    header_row: Sequence[str],
    target_column: int | None,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    for index, cell in enumerate(header_row):
        compact = _compact(cell)
        years = _YEAR_RE.findall(compact)
        if index == 0:
            role = "metric"
        elif any(token in compact for token in ("增减", "同比", "变化", "change")):
            role = "change_rate"
        elif years:
            role = f"period:{years[0]}"
        else:
            role = "unknown"
        roles[str(index)] = {"header": str(cell), "role": role}
    if target_column is not None:
        roles.setdefault(str(target_column), {"header": "", "role": "unknown"})
        roles[str(target_column)] = {
            **roles[str(target_column)],
            "target_value": True,
            "candidate_column_role": str((candidate.get("metadata") or {}).get("column_role") or ""),
        }
    return roles


def _canonical_unit(raw: str) -> str:
    compact = _compact(raw)
    if not compact:
        return ""
    if "每10股" in compact or "/10shares" in compact or "元/10股" in compact:
        return "CNY/10 shares"
    if "每股" in compact or "/share" in compact or "元/股" in compact:
        return "CNY/share"
    if "%" in compact:
        return "%"
    if any(token in compact for token in ("人民币", "万亿元", "亿元", "百万元", "万元", "千元", "元")):
        return "CNY"
    return ""


def _unit_from_context(
    *,
    target_row: Sequence[str],
    target_column: int | None,
    preceding_texts: Sequence[str],
) -> str:
    metric_cell = str(target_row[0]) if target_row else ""
    compact_metric = _compact(metric_cell)
    # Per-share basis is part of the metric contract and outranks the generic
    # parenthetical currency token inside labels such as 每10股派息数（元）.
    if "每10股" in compact_metric:
        return "CNY/10 shares"
    if "每股" in compact_metric:
        return "CNY/share"
    explicit = _EXPLICIT_CELL_UNIT_RE.search(metric_cell)
    if explicit:
        return _canonical_unit(explicit.group(0))
    for text in reversed(tuple(preceding_texts)):
        match = _UNIT_RE.search(str(text))
        if match:
            return _canonical_unit(match.group(0))
    if target_column is not None and 0 <= target_column < len(target_row):
        target_cell = str(target_row[target_column])
        if "%" in target_cell or "％" in target_cell:
            return "%"
    return ""


def _period_from_context(
    header_row: Sequence[str],
    target_column: int | None,
) -> str:
    if target_column is None or target_column >= len(header_row):
        return ""
    years = _YEAR_RE.findall(str(header_row[target_column]))
    return years[0] if years else ""


def _scope_from_text(texts: Sequence[str]) -> str:
    compact = _compact("\n".join(texts))
    if any(
        token in compact
        for token in (
            "母公司资产负债表", "母公司利润表", "母公司现金流量表",
            "母公司口径", "年度公司", "本期公司",
        )
    ):
        return "company_only"
    if any(
        token in compact
        for token in (
            "合并资产负债表", "合并利润表", "合并现金流量表",
            "合并口径", "合并报表", "年度合并", "本期合并",
        )
    ):
        return "consolidated"
    return ""


def _attribution_from_row(target_row: Sequence[str]) -> str:
    compact = _compact(target_row[0] if target_row else "")
    if any(token in compact for token in ("归属于上市公司股东", "归属于母公司股东", "归属于母公司所有者", "母公司股东应占")):
        return "parent_attributable"
    return ""


def _policy_stage(texts: Sequence[str]) -> str:
    compact = _compact("\n".join(texts))
    if any(token in compact for token in ("实施完毕", "已实施", "实施了", "完成股份回购", "分红完成")):
        return "executed"
    # A board-approved proposal that still requires shareholder approval is
    # still a proposal for evidence purposes.  Proposal markers therefore take
    # precedence over the generic phrase "审议通过".
    if any(token in compact for token in ("尚需提交", "利润分配预案", "拟以", "拟向", "预案")):
        return "proposal"
    if any(token in compact for token in ("审议通过", "股东大会通过")):
        return "approved"
    return ""


def infer_policy_stage(texts: Sequence[str]) -> str:
    """Return a fail-closed policy stage from real source text."""
    return _policy_stage(texts)


def _context_text(elements: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for element in elements:
        kind = str(element.get("type") or "context")
        text = str(element.get("text") or "").strip()
        if text:
            lines.append(f"[{kind}] {text}")
    return "\n".join(lines)


def _resolve_json_context(
    path: Path,
    *,
    page_idx: int,
    table_index: int,
    row_index: int,
    candidate: Mapping[str, Any],
) -> FinancialContextPack:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _unresolved(
            path=path, anchor_type="page_table_row", page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=None,
            reason=f"json_read_failed:{type(exc).__name__}",
        )
    if not isinstance(payload, list) or not 0 <= page_idx < len(payload):
        return _unresolved(
            path=path, anchor_type="page_table_row", page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=None,
            reason="page_idx_out_of_range",
        )
    page = payload[page_idx]
    if not isinstance(page, list):
        return _unresolved(
            path=path, anchor_type="page_table_row", page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=None,
            reason="page_payload_not_list",
        )
    tables = [(index, item) for index, item in enumerate(page) if isinstance(item, Mapping) and item.get("type") == "table"]
    if not 0 <= table_index < len(tables):
        return _unresolved(
            path=path, anchor_type="page_table_row", page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=None,
            reason="table_index_out_of_range",
        )
    item_index, table_item = tables[table_index]
    html = str((table_item.get("content") or {}).get("html") or "")
    rows = parse_table_rows(html)
    if not rows:
        return _unresolved(
            path=path, anchor_type="page_table_row", page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=None,
            reason="table_html_has_no_rows",
        )
    if not 0 <= row_index < len(rows):
        return _unresolved(
            path=path, anchor_type="page_table_row", page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=None,
            reason="row_index_out_of_range",
        )
    target_row = rows[row_index]
    header = _header_row(rows, row_index)
    target_column = _target_column(target_row, header, candidate)
    preceding_items = [
        item for item in page[max(0, item_index - 16):item_index]
        if isinstance(item, Mapping) and item.get("type") not in {"page_header", "page_number"}
    ]
    preceding_texts = [text for item in preceding_items if (text := _item_text(item))]
    titles = [
        _item_text(item) for item in preceding_items
        if item.get("type") == "title" and _item_text(item)
    ]
    unit_header = _unit_from_context(
        target_row=target_row,
        target_column=target_column,
        preceding_texts=preceding_texts,
    )
    period_header = _period_from_context(header, target_column)
    scope_header = _scope_from_text((*titles, *preceding_texts, *header))
    attribution_header = _attribution_from_row(target_row)
    adjacent = tuple(
        rows[index] for index in range(max(0, row_index - 2), min(len(rows), row_index + 3))
        if index != row_index
    )
    elements: list[Mapping[str, Any]] = []
    for title in titles[-3:]:
        elements.append({"type": "parent_heading", "text": title, "source_path": str(path)})
    for text in preceding_texts[-6:]:
        if _UNIT_RE.search(text):
            elements.append({"type": "unit_header", "text": text, "source_path": str(path)})
    if header:
        elements.append({"type": "table_header", "text": " | ".join(header), "source_path": str(path)})
    elements.append({"type": "target_row", "text": " | ".join(target_row), "source_path": str(path)})
    for row in adjacent:
        elements.append({"type": "adjacent_row", "text": " | ".join(row), "source_path": str(path)})
    table_caption = (table_item.get("content") or {}).get("table_caption") or []
    caption_text = " ".join(
        str(item.get("content") or "") if isinstance(item, Mapping) else str(item)
        for item in table_caption
    ).strip()
    if caption_text:
        elements.insert(0, {"type": "table_title", "text": caption_text, "source_path": str(path)})
    context_text = _context_text(elements)
    location = SourceLocation(
        source_path=str(path),
        anchor_type="page_table_row",
        page_idx=page_idx,
        table_index=table_index,
        row_index=row_index,
        line_number=None,
        resolver_status="resolved",
        resolver_failure_reason="",
        resolved_context_hash=_stable_hash(context_text),
    )
    return FinancialContextPack(
        location=location,
        context_source_paths=(str(path),),
        context_elements=tuple(elements),
        column_role_map=_column_roles(header, target_column, candidate),
        unit_header=unit_header,
        period_header=period_header,
        statement_scope_header=scope_header,
        attribution_scope_header=attribution_header,
        policy_stage_context=_policy_stage((*preceding_texts, " | ".join(target_row))),
        target_column_index=target_column,
        target_row=tuple(target_row),
        header_row=tuple(header),
        adjacent_rows=adjacent,
        context_text=context_text,
    )


def _resolve_markdown_context(
    path: Path,
    *,
    anchor_type: str,
    page_idx: int | None,
    line_number: int | None,
) -> FinancialContextPack:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as exc:
        return _unresolved(
            path=path, anchor_type=anchor_type, page_idx=page_idx,
            table_index=None, row_index=None, line_number=line_number,
            reason=f"markdown_read_failed:{type(exc).__name__}",
        )
    if anchor_type == "markdown_line":
        if line_number is None or not 1 <= line_number <= len(lines):
            return _unresolved(
                path=path, anchor_type=anchor_type, page_idx=page_idx,
                table_index=None, row_index=None, line_number=line_number,
                reason="line_number_out_of_range",
            )
        index = line_number - 1
    elif lines:
        index = 0
    else:
        return _unresolved(
            path=path, anchor_type=anchor_type, page_idx=page_idx,
            table_index=None, row_index=None, line_number=line_number,
            reason="markdown_empty",
        )
    selected: list[tuple[int, str, str]] = []
    for offset in range(max(0, index - 4), min(len(lines), index + 5)):
        selected.append((offset, "target_line" if offset == index else "adjacent_line", lines[offset]))
    for offset in range(index - 1, max(-1, index - 40), -1):
        if lines[offset].lstrip().startswith("#"):
            selected.append((offset, "parent_heading", lines[offset]))
            break
    selected = sorted({(offset, kind, text) for offset, kind, text in selected})
    elements = tuple(
        {"type": kind, "text": text, "source_path": str(path), "line_number": offset + 1}
        for offset, kind, text in selected if text.strip()
    )
    texts = [str(element["text"]) for element in elements]
    target_text = lines[index]
    target_row = tuple(cell.strip() for cell in target_text.strip().strip("|").split("|")) if "|" in target_text else (target_text,)
    header: tuple[str, ...] = ()
    for offset in range(index - 1, max(-1, index - 15), -1):
        if "|" in lines[offset] and any(token in _compact(lines[offset]) for token in ("项目", "本期", "上期", "同比", "变化")):
            header = tuple(cell.strip() for cell in lines[offset].strip().strip("|").split("|"))
            break
    target_column = 1 if len(target_row) > 1 else None
    unit_header = _unit_from_context(target_row=target_row, target_column=target_column, preceding_texts=texts)
    period_header = _period_from_context(header, target_column)
    context_text = _context_text(elements)
    location = SourceLocation(
        source_path=str(path),
        anchor_type=anchor_type,
        page_idx=page_idx,
        table_index=None,
        row_index=None,
        line_number=line_number,
        resolver_status="resolved",
        resolver_failure_reason="",
        resolved_context_hash=_stable_hash(context_text),
    )
    return FinancialContextPack(
        location=location,
        context_source_paths=(str(path),),
        context_elements=elements,
        column_role_map=_column_roles(header, target_column, {}),
        unit_header=unit_header,
        period_header=period_header,
        statement_scope_header=_scope_from_text(texts),
        attribution_scope_header=_attribution_from_row(target_row),
        policy_stage_context=_policy_stage(texts),
        target_column_index=target_column,
        target_row=target_row,
        header_row=header,
        adjacent_rows=tuple(),
        context_text=context_text,
    )


def resolve_financial_context(
    candidate: Mapping[str, Any],
    *,
    structured_root: str | Path,
    domain: str,
) -> FinancialContextPack:
    canonical_source = str(candidate.get("canonical_source") or "")
    doc_id = str(candidate.get("doc_id") or "")
    source_text, _fragment = _source_and_fragment(canonical_source)
    anchor_type, page_idx, table_index, row_index, line_number = _anchor(canonical_source)
    path = _resolve_source_path(
        source_text,
        structured_root=Path(structured_root),
        domain=domain,
        doc_id=doc_id,
    )
    if path is None:
        return _unresolved(
            path=None, anchor_type=anchor_type, page_idx=page_idx,
            table_index=table_index, row_index=row_index, line_number=line_number,
            reason="source_path_not_found",
        )
    if anchor_type == "page_table_row":
        if path.suffix.lower() != ".json":
            return _unresolved(
                path=path, anchor_type=anchor_type, page_idx=page_idx,
                table_index=table_index, row_index=row_index, line_number=line_number,
                reason="page_table_row_requires_json",
            )
        assert page_idx is not None and table_index is not None and row_index is not None
        return _resolve_json_context(
            path,
            page_idx=page_idx,
            table_index=table_index,
            row_index=row_index,
            candidate=candidate,
        )
    if anchor_type in {"markdown_line", "page_markdown"}:
        if path.suffix.lower() != ".md":
            return _unresolved(
                path=path, anchor_type=anchor_type, page_idx=page_idx,
                table_index=table_index, row_index=row_index, line_number=line_number,
                reason="markdown_anchor_requires_markdown",
            )
        return _resolve_markdown_context(
            path,
            anchor_type=anchor_type,
            page_idx=page_idx,
            line_number=line_number,
        )
    return _unresolved(
        path=path, anchor_type=anchor_type, page_idx=page_idx,
        table_index=table_index, row_index=row_index, line_number=line_number,
        reason="source_anchor_missing_or_unsupported",
    )


def source_line_number(candidate: Mapping[str, Any]) -> int | None:
    """Compatibility helper for existing review scripts.

    It resolves only genuine Markdown line anchors.  Page/table/row anchors are
    intentionally not converted into line numbers.
    """
    anchor_type, _page, _table, _row, line_number = _anchor(
        str(candidate.get("canonical_source") or "")
    )
    return line_number if anchor_type == "markdown_line" else None
