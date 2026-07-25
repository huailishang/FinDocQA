"""Independent raw-source normalization helpers for financial-report Oracles.

The helpers deliberately do not import the production financial metric ledger.
They derive units from raw MinerU page-local content and normalize raw values
before an Oracle formula is evaluated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_AMOUNT_MULTIPLIERS: Mapping[str, Decimal] = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "百万元": Decimal("1000000"),
    "亿元": Decimal("100000000"),
    "万亿元": Decimal("1000000000000"),
}
_UNIT_RE = re.compile(r"(?:金额单位为人民币|单位\s*[：:]?\s*(?:人民币)?)(万亿元|亿元|百万元|万元|千元|元)")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


@dataclass(frozen=True)
class NormalizedOperand:
    raw_value: str
    raw_unit: str
    normalized_value: str
    normalized_unit: str
    metric: str
    entity: str
    period: str
    source: str
    page: int
    item: int
    table_row: int | None
    unit_source: str

    def decimal(self) -> Decimal:
        return Decimal(self.normalized_value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def deep_text(value: Any) -> str:
    parts: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"content", "text", "html", "title_content", "paragraph_content"} and isinstance(child, str):
                parts.append(child)
            else:
                parts.append(deep_text(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts.extend(deep_text(child) for child in value)
    elif isinstance(value, str):
        parts.append(value)
    return " ".join(part for part in parts if part).strip()


def table_rows(item: Mapping[str, Any]) -> list[list[str]]:
    html = str((item.get("content") or {}).get("html") or "")
    if not html:
        return []
    parser = TableParser()
    parser.feed(html)
    return parser.rows


def parse_decimal(raw_value: Any) -> Decimal:
    text = str(raw_value or "").strip().replace("，", ",").replace("％", "%")
    text = text.replace(",", "").replace("%", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        raise ValueError(f"numeric value missing: {raw_value!r}")
    try:
        return Decimal(match.group(0))
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {raw_value!r}") from exc


def normalize_value(raw_value: Any, raw_unit: str, target_unit: str) -> Decimal:
    value = parse_decimal(raw_value)
    unit = str(raw_unit or "").strip().replace("％", "%")
    target = str(target_unit or "").strip()
    if target == "CNY":
        if unit not in _AMOUNT_MULTIPLIERS:
            raise ValueError(f"unsupported amount unit: {unit!r}")
        return value * _AMOUNT_MULTIPLIERS[unit]
    if target == "%":
        if unit not in {"%", "percent"}:
            raise ValueError(f"unsupported percent unit: {unit!r}")
        return value
    if target == "CNY/share":
        if unit == "CNY/10 shares":
            return value / Decimal("10")
        if unit == "CNY/share":
            return value
        raise ValueError(f"unsupported per-share unit: {unit!r}")
    if target == "years":
        if unit != "years":
            raise ValueError(f"unsupported duration unit: {unit!r}")
        return value
    if unit == target:
        return value
    raise ValueError(f"unsupported normalization: {unit!r} -> {target!r}")


def infer_raw_unit(
    *,
    page_items: Sequence[Mapping[str, Any]],
    item_index: int,
    raw_value: str,
    row_text: str,
    target_unit: str,
) -> tuple[str, str]:
    raw = str(raw_value or "").replace("％", "%")
    row = str(row_text or "").replace("％", "%")
    if "%" in raw or target_unit == "%":
        return "%", "raw_value_or_target_percent"
    if target_unit == "CNY/share":
        if "每10股" in row:
            return "CNY/10 shares", "table_row_every_10_shares"
        if "每股" in row:
            return "CNY/share", "table_row_per_share"
    local_items = page_items[max(0, item_index - 4): item_index + 1]
    local_text = "\n".join(deep_text(item) for item in local_items)
    matches = list(_UNIT_RE.finditer(local_text.replace("　", " ")))
    if matches:
        match = matches[-1]
        return match.group(1), match.group(0)
    if "千元" in row:
        return "千元", "table_row_inline_unit"
    if "百万元" in row:
        return "百万元", "table_row_inline_unit"
    if "亿元" in row:
        return "亿元", "table_row_inline_unit"
    if "万元" in row:
        return "万元", "table_row_inline_unit"
    if re.search(r"(?:人民币)?元", row):
        return "元", "table_row_inline_unit"
    raise ValueError(f"raw unit not found near item={item_index}: {row[:160]}")


def load_operand(
    *,
    project_root: Path,
    source: str,
    page: int,
    item: int,
    table_row: int | None,
    raw_value: str,
    target_unit: str,
    metric: str,
    entity: str,
    period: str,
) -> NormalizedOperand:
    path = project_root / source
    payload = json.loads(path.read_text(encoding="utf-8"))
    page_items = payload[int(page)]
    selected = page_items[int(item)]
    rows = table_rows(selected)
    row_text = deep_text(selected) if table_row is None else " | ".join(rows[int(table_row)])
    if str(raw_value) not in deep_text(selected):
        raise ValueError(f"raw value {raw_value!r} missing from {source} page={page} item={item}")
    raw_unit, unit_source = infer_raw_unit(
        page_items=page_items,
        item_index=int(item),
        raw_value=str(raw_value),
        row_text=row_text,
        target_unit=target_unit,
    )
    normalized = normalize_value(raw_value, raw_unit, target_unit)
    return NormalizedOperand(
        raw_value=str(raw_value),
        raw_unit=raw_unit,
        normalized_value=str(normalized),
        normalized_unit=target_unit,
        metric=metric,
        entity=entity,
        period=period,
        source=source,
        page=int(page),
        item=int(item),
        table_row=table_row,
        unit_source=unit_source,
    )
