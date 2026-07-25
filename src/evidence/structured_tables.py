"""MinerU structured-table evidence extraction and bounded candidate augmentation.

The live retrieval corpus is page Markdown, but MinerU 3.4 also preserves tables
as structured HTML in ``*_content_list_v2.json`` / ``*_content_list.json``.
This module reads those existing assets without reparsing PDFs and exposes each
data row as a page-local, row-local :class:`EvidenceCandidate`.

Safety properties:

* content_list_v2 is preferred over the legacy content list;
* table captions and footnotes are metadata, never standalone data facts;
* one candidate contains one data row plus its column headers;
* source lineage identifies page, table and row uniquely;
* numeric tokens are matched exactly (2500 is not 250 or 25000);
* rows already present in retrieval Markdown are not injected again;
* augmentation is enabled only for questions with explicit numeric/year/unit
  anchors and only for documents that actually contain structured tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from verification.compound_claims import raw_table_certification_guard


_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\d.])\d+(?:\.\d+)?\s*(?:%|％|亿元|万元|元|倍|个百分点|年|月|日|万户|亿户)?(?![\d.])",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*年?(?!\d)")
_EXPLICIT_UNIT_RE = re.compile(r"(?:%|％|亿元|万元|元|倍|个百分点|GWh|GW|万户|亿户)", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_TAG_SPACE_RE = re.compile(r"\s+")
_PAGE_CHILD_KEYS = ("items", "blocks", "sub_blocks", "preproc_blocks")
_DERIVED_OR_COMPOUND_TOKENS = (
    "两家公司",
    "两份文档",
    "均",
    "分别",
    "同时",
    "双位数",
    "同比",
    "增长",
    "下降",
    "降幅",
    "总金额",
    "合计",
    "之和",
    "超过一成",
    "超过了",
)


def compact_text(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "")).replace("％", "%").lower()


def exact_numeric_tokens(value: Any) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _NUMERIC_TOKEN_RE.finditer(str(value or "")):
        token = compact_text(match.group(0))
        if token and token not in tokens:
            tokens.append(token)
    return tuple(tokens)


def _nested_text(value: Any) -> str:
    if isinstance(value, str):
        return _TAG_SPACE_RE.sub(" ", value).strip()
    if isinstance(value, Mapping):
        for key in ("text", "text_content"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return _TAG_SPACE_RE.sub(" ", raw).strip()
        raw_content = value.get("content")
        if isinstance(raw_content, str) and raw_content.strip():
            return _TAG_SPACE_RE.sub(" ", raw_content).strip()
        parts = [
            _nested_text(child)
            for child in value.values()
            if isinstance(child, (Mapping, list, tuple))
        ]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, (list, tuple)):
        parts = [_nested_text(child) for child in value]
        return " ".join(part for part in parts if part).strip()
    return ""


@dataclass(frozen=True)
class TableLayoutAudit:
    supported: bool
    issues: tuple[str, ...] = ()
    header_mode: str = "unknown"
    header_row_count: int = 0


class _TableHTMLParser(HTMLParser):
    """Collect raw HTML cells before deterministic rowspan/colspan expansion."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw_rows: list[list[dict[str, Any]]] = []
        self.issues: list[str] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_type = "td"
        self._cell_colspan = 1
        self._cell_rowspan = 1
        self._nested_table_depth = 0

    @staticmethod
    def _span_value(raw: str | None, *, name: str, issues: list[str]) -> int:
        try:
            value = int(raw or 1)
        except (TypeError, ValueError):
            issues.append(f"invalid_{name}")
            return 1
        if value < 1 or value > 100:
            issues.append(f"invalid_{name}")
            return 1
        return value

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "table":
            self._nested_table_depth += 1
            if self._nested_table_depth > 1:
                self.issues.append("nested_table")
            return
        if lowered == "tr":
            if self._row is not None:
                self.issues.append("overlapping_rows")
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            if self._cell_parts is not None:
                self.issues.append("overlapping_cells")
            self._cell_parts = []
            self._cell_type = lowered
            attrs_map = {str(key).lower(): value for key, value in attrs}
            self._cell_colspan = self._span_value(
                attrs_map.get("colspan"), name="colspan", issues=self.issues
            )
            self._cell_rowspan = self._span_value(
                attrs_map.get("rowspan"), name="rowspan", issues=self.issues
            )
        elif lowered == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "table":
            self._nested_table_depth = max(0, self._nested_table_depth - 1)
            return
        if lowered in {"td", "th"} and self._cell_parts is not None and self._row is not None:
            text = _TAG_SPACE_RE.sub(" ", "".join(self._cell_parts)).strip()
            self._row.append(
                {
                    "text": text,
                    "type": self._cell_type,
                    "colspan": self._cell_colspan,
                    "rowspan": self._cell_rowspan,
                }
            )
            self._cell_parts = None
            self._cell_colspan = 1
            self._cell_rowspan = 1
        elif lowered == "tr" and self._row is not None:
            if any(str(cell.get("text") or "").strip() for cell in self._row):
                self.raw_rows.append(self._row)
            self._row = None


def _expand_table_grid(
    raw_rows: Sequence[Sequence[Mapping[str, Any]]],
) -> tuple[list[list[str]], list[list[str]], list[str]]:
    rows: list[list[str]] = []
    types: list[list[str]] = []
    issues: list[str] = []
    pending: dict[int, tuple[int, str, str]] = {}
    for raw_row in raw_rows:
        occupied: dict[int, tuple[str, str]] = {}
        next_pending: dict[int, tuple[int, str, str]] = {}
        for column, (remaining, text, cell_type) in sorted(pending.items()):
            occupied[column] = (text, cell_type)
            if remaining > 1:
                next_pending[column] = (remaining - 1, text, cell_type)

        column = 0
        for raw_cell in raw_row:
            while column in occupied:
                column += 1
            colspan = int(raw_cell.get("colspan") or 1)
            rowspan = int(raw_cell.get("rowspan") or 1)
            span_columns = range(column, column + colspan)
            if any(index in occupied for index in span_columns):
                issues.append("span_collision")
                break
            text = str(raw_cell.get("text") or "").strip()
            cell_type = str(raw_cell.get("type") or "td")
            for offset, index in enumerate(span_columns):
                # Preserve the value across rowspan columns; for colspan only
                # the first column carries text to avoid inventing duplicates.
                value = text if offset == 0 or rowspan > 1 or cell_type == "th" else ""
                occupied[index] = (value, cell_type)
                if rowspan > 1:
                    next_pending[index] = (rowspan - 1, value, cell_type)
            column += colspan
        pending = next_pending
        if occupied:
            width = max(occupied) + 1
            rows.append([occupied.get(index, ("", "td"))[0] for index in range(width)])
            types.append([occupied.get(index, ("", "td"))[1] for index in range(width)])
    if pending:
        issues.append("rowspan_extends_beyond_table")
    return rows, types, issues


def parse_html_table_with_audit(
    html: str,
) -> tuple[list[list[str]], list[list[str]], TableLayoutAudit]:
    parser = _TableHTMLParser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:
        return [], [], TableLayoutAudit(False, ("html_parse_error",))
    rows, types, expansion_issues = _expand_table_grid(parser.raw_rows)
    issues = tuple(sorted(set(parser.issues + expansion_issues)))
    fatal = {
        "nested_table",
        "overlapping_rows",
        "overlapping_cells",
        "invalid_colspan",
        "invalid_rowspan",
        "span_collision",
        "rowspan_extends_beyond_table",
    }
    supported = bool(rows) and not any(issue in fatal for issue in issues)
    if not rows:
        issues = tuple(sorted(set(issues + ("empty_or_image_table",))))
    return rows, types, TableLayoutAudit(supported, issues)


def parse_html_table(html: str) -> tuple[list[list[str]], list[list[str]]]:
    rows, types, _audit = parse_html_table_with_audit(html)
    return rows, types


@dataclass(frozen=True)
class StructuredTableRow:
    domain: str
    doc_id: str
    page_idx: int
    table_index: int
    table_caption: str
    table_footnote: str
    row_index: int
    headers: tuple[str, ...]
    cell_texts: tuple[str, ...]
    normalized_row_text: str
    canonical_source: str
    mineru_json_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "doc_id": self.doc_id,
            "page_idx": self.page_idx,
            "table_index": self.table_index,
            "table_caption": self.table_caption,
            "table_footnote": self.table_footnote,
            "row_index": self.row_index,
            "headers": list(self.headers),
            "cell_texts": list(self.cell_texts),
            "normalized_row_text": self.normalized_row_text,
            "canonical_source": self.canonical_source,
            "mineru_json_source": self.mineru_json_source,
        }


def _content_list_paths(document_root: Path, doc_id: str) -> tuple[Path, ...]:
    roots = (document_root / "auto", document_root)
    names = (
        f"{doc_id}_content_list_v2.json",
        "content_list_v2.json",
        f"{doc_id}_content_list.json",
        "content_list.json",
    )
    return tuple(root / name for root in roots for name in names)


def find_content_list(structured_root: Path, domain: str, doc_id: str) -> Path | None:
    document_root = Path(structured_root) / str(domain) / str(doc_id)
    for path in _content_list_paths(document_root, str(doc_id)):
        if path.is_file():
            return path
    return None


def _page_idx(item: Mapping[str, Any], default: int) -> int:
    for key in ("page_idx", "page_index", "page"):
        raw = item.get(key)
        if isinstance(raw, int) and raw >= 0:
            return raw
    return default


def _flatten_items(raw: Any) -> list[tuple[int, Mapping[str, Any]]]:
    """Flatten MinerU flat, page-grouped and top-level-list-of-lists layouts."""
    if not isinstance(raw, list):
        return []
    flattened: list[tuple[int, Mapping[str, Any]]] = []
    for outer_idx, entry in enumerate(raw):
        if isinstance(entry, list):
            for child in entry:
                if isinstance(child, Mapping):
                    flattened.append((_page_idx(child, outer_idx), child))
            continue
        if not isinstance(entry, Mapping):
            continue
        children: Sequence[Any] | None = None
        for key in _PAGE_CHILD_KEYS:
            value = entry.get(key)
            if isinstance(value, list):
                children = value
                break
        if children is None:
            flattened.append((_page_idx(entry, outer_idx), entry))
            continue
        parent_page = _page_idx(entry, outer_idx)
        for child in children:
            if isinstance(child, Mapping):
                flattened.append((_page_idx(child, parent_page), child))
    return flattened


def _table_payload(item: Mapping[str, Any]) -> tuple[str, str, str]:
    content = item.get("content") if isinstance(item.get("content"), Mapping) else {}
    html = (
        item.get("table_body")
        or item.get("html")
        or content.get("table_body")
        or content.get("html")
        or ""
    )
    caption = _nested_text(
        item.get("table_caption")
        or item.get("caption")
        or content.get("table_caption")
        or content.get("caption")
        or ""
    )
    footnote = _nested_text(
        item.get("table_footnote")
        or item.get("footnote")
        or content.get("table_footnote")
        or content.get("footnote")
        or ""
    )
    return str(html or ""), caption, footnote


def _normalise_columns(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    return [list(row) + [""] * (width - len(row)) for row in rows]


_HEADER_HINT_RE = re.compile(
    r"(?:项目|指标|名称|字段|数值|金额|比例|年份|年度|本年|上年|日期|单位|预测|类型|公司|主体)"
)


def _looks_like_header_row(row: Sequence[str], next_row: Sequence[str] | None) -> bool:
    values = [str(cell or "").strip() for cell in row]
    if not any(values):
        return False

    numeric_tokens = [
        token
        for value in values
        for token in exact_numeric_tokens(value)
    ]
    non_year_numeric = [
        token
        for token in numeric_tokens
        if not re.fullmatch(r"(?:19|20)\d{2}", str(token).replace(",", ""))
    ]
    # A TD-only first row containing an amount, ratio or count is data even if
    # its metric text includes words such as "本年" or "金额".  Only year-like
    # numbers are allowed in an inferred header row.
    if non_year_numeric:
        return False
    if any(_HEADER_HINT_RE.search(value) for value in values):
        return True
    if numeric_tokens:
        return False
    if next_row is None:
        return False
    next_numeric = sum(
        bool(exact_numeric_tokens(str(value or "")))
        for value in next_row
        if str(value or "").strip()
    )
    return next_numeric > 0 and sum(bool(value) for value in values) >= 2


def _headers_for_table(
    rows: Sequence[Sequence[str]],
    cell_types: Sequence[Sequence[str]],
) -> tuple[tuple[str, ...], int, str, int]:
    if not rows:
        return (), 0, "empty", 0

    # Explicit TH rows are authoritative. Consecutive leading TH rows form a
    # multi-level header path; values are joined per column and remain
    # traceable in the row-local evidence text.
    explicit_header_rows = 0
    for types in cell_types:
        if any(str(value).lower() == "th" for value in types):
            explicit_header_rows += 1
        else:
            break
    if explicit_header_rows:
        width = max(len(row) for row in rows[:explicit_header_rows])
        headers: list[str] = []
        for column in range(width):
            path: list[str] = []
            for row in rows[:explicit_header_rows]:
                value = str(row[column] if column < len(row) else "").strip()
                if value and (not path or path[-1] != value):
                    path.append(value)
            headers.append(" / ".join(path) or f"column_{column + 1}")
        return tuple(headers), explicit_header_rows, "explicit_multi_header", explicit_header_rows

    first = tuple(str(cell).strip() for cell in rows[0])
    next_row = rows[1] if len(rows) > 1 else None
    if len(rows) > 1 and _looks_like_header_row(first, next_row):
        return first, 1, "inferred_single_header", 1

    # Headerless tables keep the first row as data. Generic column names are
    # safer than silently dropping the first observation.
    width = max(len(row) for row in rows)
    return (
        tuple(f"column_{index + 1}" for index in range(width)),
        0,
        "headerless",
        0,
    )


def _row_text(caption: str, headers: Sequence[str], cells: Sequence[str]) -> str:
    fields: list[str] = []
    for index, cell in enumerate(cells):
        value = _TAG_SPACE_RE.sub(" ", str(cell or "")).strip()
        if not value:
            continue
        header = str(headers[index] if index < len(headers) else f"column_{index + 1}").strip()
        fields.append(f"{header}={value}" if header else value)
    # A vertical bar keeps all cells in one row-local typed window. Semicolons
    # are sentence boundaries in the claim certifier and would incorrectly
    # separate a field-name cell from its adjacent value cell.
    prefix = f"表格={caption} | " if caption else ""
    return prefix + " | ".join(fields)


@lru_cache(maxsize=256)
def load_structured_table_rows_with_audit(
    structured_root: Path,
    domain: str,
    doc_id: str,
) -> tuple[tuple[StructuredTableRow, ...], Mapping[str, Any]]:
    source = find_content_list(Path(structured_root), domain, doc_id)
    if source is None:
        return (), {
            "source_found": False,
            "tables_seen": 0,
            "tables_loaded": 0,
            "unsupported_table_layouts": [],
        }
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return (), {
            "source_found": True,
            "source": source.as_posix(),
            "read_error": exc.__class__.__name__,
            "tables_seen": 0,
            "tables_loaded": 0,
            "unsupported_table_layouts": [],
        }

    table_count_by_page: dict[int, int] = {}
    output: list[StructuredTableRow] = []
    unsupported: list[dict[str, Any]] = []
    table_layouts: list[dict[str, Any]] = []
    source_rel = source.as_posix()
    marker = "/data/processed_mineru/"
    lowered = source_rel.lower()
    marker_idx = lowered.find(marker)
    if marker_idx >= 0:
        source_rel = source_rel[marker_idx + 1 :]

    tables_seen = 0
    tables_loaded = 0
    for page_idx, item in _flatten_items(raw):
        if str(item.get("type") or item.get("block_type") or "").lower() != "table":
            continue
        table_index = table_count_by_page.get(page_idx, 0)
        table_count_by_page[page_idx] = table_index + 1
        tables_seen += 1
        html, caption, footnote = _table_payload(item)
        if not html.strip():
            unsupported.append({
                "page_idx": page_idx,
                "table_index": table_index,
                "issues": ["empty_or_image_table"],
            })
            continue
        rows, cell_types, layout_audit = parse_html_table_with_audit(html)
        if not layout_audit.supported:
            unsupported.append({
                "page_idx": page_idx,
                "table_index": table_index,
                "issues": list(layout_audit.issues),
            })
            continue
        rows = _normalise_columns(rows)
        cell_types = _normalise_columns(cell_types)
        headers, first_data_index, header_mode, header_row_count = _headers_for_table(
            rows, cell_types
        )
        table_layouts.append({
            "page_idx": page_idx,
            "table_index": table_index,
            "header_mode": header_mode,
            "header_row_count": header_row_count,
            "issues": list(layout_audit.issues),
        })
        tables_loaded += 1
        for row_index, cells in enumerate(rows[first_data_index:], start=0):
            if not any(str(cell).strip() for cell in cells):
                continue
            normalised = _row_text(caption, headers, cells)
            canonical = (
                f"{source_rel}#page_idx={page_idx}&table_index={table_index}&row_index={row_index}"
            )
            output.append(
                StructuredTableRow(
                    domain=str(domain),
                    doc_id=str(doc_id),
                    page_idx=int(page_idx),
                    table_index=int(table_index),
                    table_caption=caption,
                    table_footnote=footnote,
                    row_index=int(row_index),
                    headers=tuple(headers),
                    cell_texts=tuple(str(cell).strip() for cell in cells),
                    normalized_row_text=normalised,
                    canonical_source=canonical,
                    mineru_json_source=source_rel,
                )
            )
    return tuple(output), {
        "source_found": True,
        "source": source_rel,
        "tables_seen": tables_seen,
        "tables_loaded": tables_loaded,
        "rows_loaded": len(output),
        "unsupported_table_layouts": unsupported,
        "table_layouts": table_layouts,
    }


def load_structured_table_rows(
    structured_root: Path,
    domain: str,
    doc_id: str,
) -> list[StructuredTableRow]:
    rows, _audit = load_structured_table_rows_with_audit(
        Path(structured_root), str(domain), str(doc_id)
    )
    return list(rows)


def question_has_structured_table_anchors(question: Question) -> bool:
    text = " ".join([question.text, *question.options.values()])
    return bool(_YEAR_RE.search(text) or _EXPLICIT_UNIT_RE.search(text) or exact_numeric_tokens(text))


def _lexical_grams(value: str) -> set[str]:
    compact = compact_text(value)
    # Remove digits to prevent a numeric-only match from becoming sufficient.
    lexical = re.sub(r"\d+(?:\.\d+)?", "", compact)
    if len(lexical) < 2:
        return {lexical} if lexical else set()
    return {lexical[index : index + 2] for index in range(len(lexical) - 1)}


def option_row_relevance_score(option_text: str, row: StructuredTableRow) -> float:
    row_compact = compact_text(row.normalized_row_text)
    row_numbers = set(exact_numeric_tokens(row.normalized_row_text))
    row_grams = _lexical_grams(row.normalized_row_text)
    option_compact = compact_text(option_text)
    option_numbers = set(exact_numeric_tokens(option_text))
    exact_number_hits = len(option_numbers & row_numbers)
    lexical_hits = len(_lexical_grams(option_text) & row_grams)
    exact_phrase = bool(option_compact and option_compact in row_compact)
    year_hits = sum(
        1
        for year in _YEAR_RE.findall(option_text)
        if compact_text(year) in row_compact
    )
    return (
        exact_number_hits * 100.0
        + lexical_hits
        + year_hits * 15.0
        + (1000.0 if exact_phrase else 0.0)
    )


def structured_table_row_eligible_for_option(
    option_text: str,
    row_text: str,
    question_doc_ids: Sequence[str] = (),
) -> bool:
    """Whether one raw row may directly certify the complete option claim."""
    guard = raw_table_certification_guard(
        option_text, row_text, question_doc_ids
    )
    if guard.get("allowed") is not True:
        return False
    option_compact = compact_text(option_text)
    row_compact = compact_text(row_text)
    option_numbers = set(exact_numeric_tokens(option_text))
    row_numbers = set(exact_numeric_tokens(row_text))
    non_year_numbers = {
        token
        for token in option_numbers
        if not re.fullmatch(r"(?:19|20)\d{2}年?", token)
    }
    if non_year_numbers and not (non_year_numbers & row_numbers):
        return False
    exact_phrase = bool(option_compact and option_compact in row_compact)
    return exact_phrase or bool(_lexical_grams(option_text) & _lexical_grams(row_text))


def structured_table_certification_complete(
    option_text: str,
    row_text: str,
    certification: Mapping[str, Any],
    question_doc_ids: Sequence[str] = (),
) -> bool:
    if not structured_table_row_eligible_for_option(
        option_text, row_text, question_doc_ids
    ):
        return False
    status = str(certification.get("claim_certification_status") or "")
    if status not in {"supported", "contradicted"}:
        return False
    if certification.get("missing_atoms"):
        return False
    if certification.get("conflicting_atoms"):
        return False
    option_numbers = set(exact_numeric_tokens(option_text))
    non_year_numbers = {
        token
        for token in option_numbers
        if not re.fullmatch(r"(?:19|20)\d{2}年?", token)
    }
    matched = set(str(value) for value in certification.get("matched_atoms") or [])
    if non_year_numbers and not {"metric_value", "unit"}.issubset(matched):
        return False
    if non_year_numbers and "comparator" not in matched:
        return False
    return True

def candidate_rows_for_option(
    option_text: str,
    rows: Sequence[StructuredTableRow],
    *,
    limit: int = 24,
) -> list[StructuredTableRow]:
    """Return a bounded set of table rows worth running through the certifier.

    Numeric options require one exact numeric token plus lexical overlap. Purely
    textual options use a stricter lexical threshold. This prevents a full scan
    of thousands of annual-report table rows while retaining deterministic
    option-row matching.
    """
    option_numbers = set(exact_numeric_tokens(option_text))
    option_grams = _lexical_grams(option_text)
    ranked: list[tuple[float, StructuredTableRow]] = []
    for row in rows:
        row_numbers = set(exact_numeric_tokens(row.normalized_row_text))
        lexical_overlap = len(option_grams & _lexical_grams(row.normalized_row_text))
        if option_numbers:
            if not (option_numbers & row_numbers) or lexical_overlap <= 0:
                continue
        elif lexical_overlap < 4:
            continue
        score = option_row_relevance_score(option_text, row)
        ranked.append((score, row))
    ranked.sort(
        key=lambda item: (
            -item[0],
            item[1].page_idx,
            item[1].table_index,
            item[1].row_index,
        )
    )
    return [row for _score, row in ranked[: max(1, int(limit))]]


def row_relevance_score(question: Question, row: StructuredTableRow) -> float:
    return max(
        (option_row_relevance_score(option, row) for option in question.options.values()),
        default=0.0,
    )


def _row_already_present(row: StructuredTableRow, existing_text: str) -> bool:
    existing = compact_text(existing_text)
    if not existing:
        return False
    meaningful_cells = [compact_text(cell) for cell in row.cell_texts if len(compact_text(cell)) >= 8]
    if meaningful_cells and all(cell in existing for cell in meaningful_cells):
        return True
    row_numbers = set(exact_numeric_tokens(row.normalized_row_text))
    if not row_numbers:
        return False
    longest = max(meaningful_cells, key=len, default="")
    return bool(longest and longest in existing and row_numbers <= set(exact_numeric_tokens(existing_text)))


class StructuredTableEvidenceAugmenter:
    """Add bounded, relevant MinerU table rows to an evidence candidate list."""

    def __init__(self, structured_root: Path, *, max_rows_per_doc: int = 12) -> None:
        self.structured_root = Path(structured_root)
        self.max_rows_per_doc = max(1, int(max_rows_per_doc))

    def augment(
        self,
        question: Question,
        candidates: Sequence[EvidenceCandidate],
    ) -> tuple[list[EvidenceCandidate], dict[str, Any]]:
        base = list(candidates)
        audit: dict[str, Any] = {
            "enabled": False,
            "reason": "question_has_no_explicit_numeric_year_or_unit_anchor",
            "documents_scanned": 0,
            "table_rows_available": 0,
            "table_rows_added": 0,
            "duplicate_rows_skipped": 0,
            "unsupported_table_layouts": [],
            "table_layouts": [],
            "added_sources": [],
        }
        if not question_has_structured_table_anchors(question):
            return base, audit

        audit["enabled"] = True
        audit["reason"] = "explicit_numeric_year_or_unit_anchor"
        existing_by_doc: dict[str, str] = {}
        for candidate in base:
            existing_by_doc.setdefault(str(candidate.doc_id), "")
            existing_by_doc[str(candidate.doc_id)] += (
                f"\n{candidate.before_text}\n{candidate.text}\n{candidate.after_text}"
            )

        additions: list[EvidenceCandidate] = []
        for doc_id in map(str, question.doc_ids):
            loaded_rows, load_audit = load_structured_table_rows_with_audit(
                self.structured_root, question.domain, doc_id
            )
            rows = list(loaded_rows)
            audit["unsupported_table_layouts"].extend(
                {
                    "doc_id": doc_id,
                    **dict(item),
                }
                for item in load_audit.get("unsupported_table_layouts", [])
            )
            audit["table_layouts"].extend(
                {
                    "doc_id": doc_id,
                    **dict(item),
                }
                for item in load_audit.get("table_layouts", [])
            )
            if not rows:
                continue
            audit["documents_scanned"] += 1
            audit["table_rows_available"] += len(rows)
            relevant_by_source: dict[str, StructuredTableRow] = {}
            for option in question.options.values():
                for row in candidate_rows_for_option(
                    option,
                    rows,
                    limit=max(self.max_rows_per_doc * 2, 24),
                ):
                    # Keep bounded row-local facts for the derived evidence
                    # builder even when a raw row is forbidden from directly
                    # certifying the complete option.
                    relevant_by_source[row.canonical_source] = row
            ranked: list[tuple[float, StructuredTableRow]] = []
            for row in relevant_by_source.values():
                score = row_relevance_score(question, row)
                if _row_already_present(row, existing_by_doc.get(doc_id, "")):
                    audit["duplicate_rows_skipped"] += 1
                    continue
                ranked.append((score, row))
            ranked.sort(key=lambda item: (-item[0], item[1].page_idx, item[1].table_index, item[1].row_index))
            for score, row in ranked[: self.max_rows_per_doc]:
                metadata = {
                    **row.to_dict(),
                    "source_kind": "mineru_structured_table",
                    "structured_table_evidence": True,
                    "row_relevance_score": score,
                }
                additions.append(
                    EvidenceCandidate(
                        domain=question.domain,
                        doc_id=doc_id,
                        source=row.canonical_source,
                        text=row.normalized_row_text,
                        before_text=f"[TABLE CAPTION] {row.table_caption}" if row.table_caption else "",
                        after_text="",
                        section_title=row.table_caption or None,
                        score=10000.0 + score,
                        retriever="mineru_structured_table",
                        metadata=metadata,
                    )
                )
                audit["added_sources"].append(row.canonical_source)

        # Structured rows are placed first only inside the verification
        # candidate view. Package L keeps them out of the solver prompt unless
        # prompt injection is explicitly enabled by a separate gate.
        audit["table_rows_added"] = len(additions)
        return additions + base, audit


def iter_document_table_rows(
    structured_root: Path,
    documents: Iterable[tuple[str, str]],
) -> Iterable[StructuredTableRow]:
    for domain, doc_id in documents:
        yield from load_structured_table_rows(structured_root, domain, doc_id)
