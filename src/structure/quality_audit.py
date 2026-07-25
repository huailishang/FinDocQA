"""Read-only MinerU / adapted-corpus quality audit for BB-P0-04B.

The audit intentionally does not repair or rewrite corpus files.  It combines
three existing sources of truth:

* ``document_structure.json`` for document/page lineage;
* referenced MinerU ``content_list_v2`` JSON for structured blocks/bboxes;
* adapted ``page_XXXX.md`` files used by retrieval.

Findings are deliberately split into two classes:

``confirmed_anomaly``
    A directly observable contract break or missing machine-readable payload
    (for example missing structure metadata, missing page files, unreadable
    source JSON, duplicate substantive page content, or a table block with no
    HTML representation).

``needs_review``
    A heuristic risk signal (for example image-dominant pages, likely reading
    order inversions, cross-page table continuation candidates, unclear units,
    or heading-level jumps).  These are never described as parser failures.

This module is standard-library only and makes no provider calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

RULESET_VERSION = "bb_p0_04b_v1_20260722"

# The thresholds are public/auditable on purpose.  Tests exercise the boundary
# conditions rather than hiding tuning inside implementation details.
THRESHOLDS: dict[str, float | int] = {
    "low_text_chars": 30,
    "scan_like_visual_blocks": 1,
    "scan_like_text_chars": 40,
    "reading_order_min_main_blocks": 5,
    "reading_order_inversion_ratio": 0.20,
    "reading_order_y_tolerance": 35,
    "reading_order_column_shift_x": 180,
    "numeric_table_min_numbers": 4,
    "duplicate_min_text_chars": 60,
    "high_risk_page_score": 50,
    "high_risk_document_score": 60,
}

UNIT_RE = re.compile(
    r"(?:%|％|百分点|bp(?:s)?|元|万元|亿元|万亿|人民币|美元|港元|欧元|"
    r"股|万股|亿股|人|家|个|户|笔|次|年|月|日|天|吨|公斤|千克|"
    r"平方米|公里|千米|兆瓦|千瓦|千瓦时|度|倍|亿元/年|元/股)",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?<![A-Za-z_])[+-]?\d[\d,]*(?:\.\d+)?(?:%|％)?")
TAG_RE = re.compile(r"<[^>]+>")
ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<(?:td|th)\b([^>]*)>(.*?)</(?:td|th)>", re.IGNORECASE | re.DOTALL)
COLSPAN_RE = re.compile(r"\bcolspan\s*=\s*[\"']?(\d+)", re.IGNORECASE)
PAGE_RE = re.compile(r"^page_(\d+)\.md$")
IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

CONFIRMED_WEIGHTS: dict[str, int] = {
    "missing_document_structure": 100,
    "invalid_document_structure": 100,
    "doc_id_mismatch": 90,
    "declared_page_missing": 90,
    "page_count_mismatch": 80,
    "missing_source_file": 100,
    "invalid_source_json": 100,
    "source_page_count_mismatch": 80,
    "empty_page": 80,
    "table_machine_structure_missing": 65,
    "formula_machine_content_missing": 65,
    "duplicate_substantive_page": 55,
    "unresolved_visual_asset": 55,
    "audit_read_failure": 100,
}

REVIEW_WEIGHTS: dict[str, int] = {
    "scan_like_page": 35,
    "scan_dominant_document": 25,
    "very_low_text_density": 15,
    "reading_order_suspect": 25,
    "heading_level_jump": 15,
    "cross_page_table_candidate": 20,
    "numeric_table_unit_unclear": 15,
    "table_row_width_inconsistent": 15,
    "source_block_bbox_incomplete": 10,
}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    classification: str
    severity: str
    score: int
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageAudit:
    domain: str
    doc_id: str
    page_number: int
    page_path: str
    source_file: str
    source_page_index: int | None
    nearest_heading: str
    text_chars: int
    numeric_tokens: int
    block_count: int
    visual_block_count: int
    table_count: int
    formula_count: int
    risk_score: int
    risk_level: str
    confirmed_anomalies: tuple[Finding, ...]
    review_flags: tuple[Finding, ...]


@dataclass(frozen=True)
class DocumentAudit:
    domain: str
    doc_id: str
    doc_dir: str
    structure_found: bool
    declared_page_count: int
    actual_page_count: int
    source_file: str
    source_found: bool
    risk_score: int
    risk_level: str
    confirmed_anomalies: tuple[Finding, ...]
    review_flags: tuple[Finding, ...]
    high_risk_pages: tuple[PageAudit, ...]
    page_risk_distribution: Mapping[str, int]
    metrics: Mapping[str, Any]


def _finding(
    rule_id: str,
    classification: str,
    reason: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    severity: str | None = None,
) -> Finding:
    weights = CONFIRMED_WEIGHTS if classification == "confirmed_anomaly" else REVIEW_WEIGHTS
    score = int(weights[rule_id])
    if severity is None:
        severity = "critical" if score >= 90 else "high" if score >= 55 else "medium" if score >= 25 else "low"
    return Finding(
        rule_id=rule_id,
        classification=classification,
        severity=severity,
        score=score,
        reason=reason,
        evidence=dict(evidence or {}),
    )


def _risk_level(score: int) -> str:
    if score >= 90:
        return "critical"
    if score >= int(THRESHOLDS["high_risk_document_score"]):
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "clean"


def _page_risk_level(score: int) -> str:
    if score >= 90:
        return "critical"
    if score >= int(THRESHOLDS["high_risk_page_score"]):
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "clean"


def _max_plus_review_score(
    confirmed: Sequence[Finding], review: Sequence[Finding]
) -> int:
    confirmed_max = max((f.score for f in confirmed), default=0)
    # Multiple heuristic flags are useful, but should not turn a weak heuristic
    # into a fabricated critical failure.  Page-level review contribution is capped at 50.
    review_score = min(50, sum(f.score for f in review))
    return min(100, max(confirmed_max, review_score))


def _document_score(
    confirmed: Sequence[Finding], review: Sequence[Finding]
) -> int:
    """Score document risk without multiplying the same page heuristic N times."""
    confirmed_max = max((f.score for f in confirmed), default=0)
    strongest_by_rule: dict[str, int] = {}
    for finding in review:
        strongest_by_rule[finding.rule_id] = max(
            strongest_by_rule.get(finding.rule_id, 0), finding.score
        )
    review_score = min(80, sum(strongest_by_rule.values()))
    return min(100, max(confirmed_max, review_score))


def _page_file_number(path: Path) -> int | None:
    match = PAGE_RE.match(path.name)
    return int(match.group(1)) if match else None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_project_path(raw: str, project_root: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_strings(item)


def _iter_text_payload_strings(value: Any, *, parent_key: str = "") -> Iterable[str]:
    """Yield human/machine-readable payload text but skip structural labels."""
    if isinstance(value, str):
        is_text_key = (
            parent_key in {"content", "html"}
            or parent_key.endswith("_content")
            or parent_key.endswith("_caption")
            or parent_key.endswith("_footnote")
        )
        if is_text_key:
            yield value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_text_payload_strings(item, parent_key=str(key))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_text_payload_strings(item, parent_key=parent_key)


def _block_text(block: Mapping[str, Any]) -> str:
    # Exclude structural labels (for example {"type": "text"}) and image paths
    # from text-density metrics. Only actual content/html/caption/footnote values
    # contribute to machine-readable text density.
    content = block.get("content")
    if content is None:
        return ""
    pieces: list[str] = []
    for value in _iter_text_payload_strings(content):
        if value.startswith("images/") or value.startswith("images\\"):
            continue
        pieces.append(TAG_RE.sub(" ", value))
    return " ".join(pieces).strip()


def _substantive_markdown(text: str) -> str:
    text = COMMENT_RE.sub("", text)
    text = IMAGE_MARKDOWN_RE.sub("", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def _normalize_page_text(text: str) -> str:
    text = COMMENT_RE.sub("", text)
    text = IMAGE_MARKDOWN_RE.sub("", text)
    lines = [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _title_level(block: Mapping[str, Any]) -> int | None:
    if str(block.get("type", "")) != "title":
        return None
    content = block.get("content")
    if not isinstance(content, Mapping):
        return None
    level = content.get("level")
    return int(level) if isinstance(level, int) and 1 <= level <= 6 else None


def _title_text(block: Mapping[str, Any]) -> str:
    if str(block.get("type", "")) != "title":
        return ""
    return _block_text(block)


def _bbox(block: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    value = block.get("bbox")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(x) for x in value)
    except (TypeError, ValueError):
        return None
    if x1 < x0 or y1 < y0:
        return None
    return (x0, y0, x1, y1)


def _main_blocks(page_blocks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ignored = {"page_header", "page_footer", "page_number", "page_footnote", "page_aside_text"}
    return [b for b in page_blocks if str(b.get("type", "")) not in ignored]


def _reading_order_inversions(page_blocks: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    blocks = [(b, _bbox(b)) for b in _main_blocks(page_blocks)]
    positioned = [(b, bb) for b, bb in blocks if bb is not None]
    if len(positioned) < int(THRESHOLDS["reading_order_min_main_blocks"]):
        return 0, max(0, len(positioned) - 1)
    y_tol = float(THRESHOLDS["reading_order_y_tolerance"])
    column_shift = float(THRESHOLDS["reading_order_column_shift_x"])
    inversions = 0
    for (_, prev), (_, cur) in zip(positioned, positioned[1:]):
        assert prev is not None and cur is not None
        # A y reset accompanied by a strong move to the right is a common
        # legitimate multi-column transition and is not flagged.
        y_reset = cur[1] + y_tol < prev[1]
        clear_column_shift = cur[0] > prev[0] + column_shift
        if y_reset and not clear_column_shift:
            inversions += 1
    return inversions, len(positioned) - 1


def _table_html(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if not isinstance(content, Mapping):
        return ""
    value = content.get("html")
    return str(value).strip() if isinstance(value, str) else ""


def _table_units_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if not isinstance(content, Mapping):
        return ""
    pieces: list[str] = []
    for key in ("html", "table_caption", "table_footnote"):
        if key in content:
            pieces.extend(_iter_strings(content[key]))
    return " ".join(TAG_RE.sub(" ", x) for x in pieces)


def _table_row_widths(html: str) -> list[int]:
    widths: list[int] = []
    for row in ROW_RE.findall(html):
        width = 0
        for attrs, _body in CELL_RE.findall(row):
            match = COLSPAN_RE.search(attrs)
            width += int(match.group(1)) if match else 1
        if width:
            widths.append(width)
    return widths


def _visual_path(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if not isinstance(content, Mapping):
        return ""
    source = content.get("image_source")
    if isinstance(source, Mapping):
        value = source.get("path")
        if isinstance(value, str):
            value = value.strip()
            # MinerU sometimes emits the directory sentinel ``images/`` when
            # no concrete visual file exists.  That is not an unresolved file
            # reference; the missing machine-readable table/formula payload is
            # audited separately.
            if value.endswith(("/", "\\")):
                return ""
            return value
    return ""


def _resolve_visual_asset(
    visual_path: str,
    *,
    doc_dir: Path,
    source_file: Path | None,
) -> Path | None:
    if not visual_path:
        return None
    candidate = doc_dir / visual_path
    if candidate.is_file():
        return candidate
    if source_file is not None:
        candidate = source_file.parent / visual_path
        if candidate.is_file():
            return candidate
    return None


def _source_pages(source_payload: Any) -> list[list[Mapping[str, Any]]] | None:
    # Real MinerU 3.4 content_list_v2 in this repository is a top-level list of
    # per-page block lists.  We deliberately fail closed for unexpected shapes
    # rather than pretending we have block/page lineage.
    if not isinstance(source_payload, list):
        return None
    pages: list[list[Mapping[str, Any]]] = []
    for page in source_payload:
        if not isinstance(page, list):
            return None
        pages.append([b for b in page if isinstance(b, Mapping)])
    return pages


def _page_findings(
    *,
    domain: str,
    doc_id: str,
    page_number: int,
    page_path: Path,
    page_text: str,
    blocks: Sequence[Mapping[str, Any]],
    source_file: Path | None,
    previous_has_table: bool,
    next_has_table: bool,
    previous_title_level: int | None,
) -> tuple[PageAudit, int | None]:
    confirmed: list[Finding] = []
    review: list[Finding] = []
    main = _main_blocks(blocks)
    block_texts = [_block_text(b) for b in main]
    text = " ".join(piece for piece in block_texts if piece)
    text_chars = len(re.sub(r"\s+", "", text))
    numeric_tokens = len(NUMBER_RE.findall(text))
    visual_blocks = [b for b in main if str(b.get("type", "")) in {"image", "chart", "table"}]
    tables = [b for b in main if str(b.get("type", "")) == "table"]
    formulas = [b for b in main if str(b.get("type", "")) in {"equation_interline", "equation", "formula"}]

    substantive_md = _substantive_markdown(page_text)
    if not substantive_md and not main:
        confirmed.append(
            _finding(
                "empty_page",
                "confirmed_anomaly",
                "page has neither substantive adapted text nor structured main blocks",
            )
        )

    if text_chars < int(THRESHOLDS["low_text_chars"]):
        review.append(
            _finding(
                "very_low_text_density",
                "needs_review",
                "structured page contains very little machine-readable text",
                evidence={"text_chars": text_chars, "threshold": THRESHOLDS["low_text_chars"]},
            )
        )
    if (
        len(visual_blocks) >= int(THRESHOLDS["scan_like_visual_blocks"])
        and text_chars < int(THRESHOLDS["scan_like_text_chars"])
    ):
        review.append(
            _finding(
                "scan_like_page",
                "needs_review",
                "visual blocks dominate while machine-readable text is sparse",
                evidence={
                    "visual_blocks": len(visual_blocks),
                    "text_chars": text_chars,
                    "text_threshold": THRESHOLDS["scan_like_text_chars"],
                },
            )
        )

    inversions, comparisons = _reading_order_inversions(blocks)
    inversion_ratio = inversions / comparisons if comparisons else 0.0
    if comparisons and inversion_ratio >= float(THRESHOLDS["reading_order_inversion_ratio"]):
        review.append(
            _finding(
                "reading_order_suspect",
                "needs_review",
                "block order has repeated vertical inversions not explained by a clear column shift",
                evidence={
                    "inversions": inversions,
                    "comparisons": comparisons,
                    "ratio": round(inversion_ratio, 4),
                    "threshold": THRESHOLDS["reading_order_inversion_ratio"],
                },
            )
        )

    title_levels = [level for b in main if (level := _title_level(b)) is not None]
    last_title_level = previous_title_level
    for level in title_levels:
        if last_title_level is not None and level - last_title_level > 1:
            review.append(
                _finding(
                    "heading_level_jump",
                    "needs_review",
                    "heading level jumps by more than one level",
                    evidence={"from_level": last_title_level, "to_level": level},
                )
            )
            break
        last_title_level = level

    for index, table in enumerate(tables):
        html = _table_html(table)
        if not html:
            confirmed.append(
                _finding(
                    "table_machine_structure_missing",
                    "confirmed_anomaly",
                    "MinerU emitted a table block without machine-readable HTML",
                    evidence={"table_index": index},
                )
            )
            continue
        widths = _table_row_widths(html)
        if len(widths) >= 2 and max(widths) != min(widths):
            review.append(
                _finding(
                    "table_row_width_inconsistent",
                    "needs_review",
                    "table rows expose inconsistent effective column widths",
                    evidence={"table_index": index, "row_widths": widths[:20]},
                )
            )
        table_text = _table_units_text(table)
        if (
            len(NUMBER_RE.findall(table_text)) >= int(THRESHOLDS["numeric_table_min_numbers"])
            and UNIT_RE.search(table_text) is None
        ):
            review.append(
                _finding(
                    "numeric_table_unit_unclear",
                    "needs_review",
                    "numeric-heavy table has no explicit unit token in HTML/caption/footnote",
                    evidence={
                        "table_index": index,
                        "numeric_tokens": len(NUMBER_RE.findall(table_text)),
                        "threshold": THRESHOLDS["numeric_table_min_numbers"],
                    },
                )
            )

    if tables and (previous_has_table or next_has_table):
        review.append(
            _finding(
                "cross_page_table_candidate",
                "needs_review",
                "table blocks occur on adjacent pages and may represent a cross-page continuation",
                evidence={"previous_has_table": previous_has_table, "next_has_table": next_has_table},
            )
        )

    for index, formula in enumerate(formulas):
        content = formula.get("content")
        math = content.get("math_content") if isinstance(content, Mapping) else None
        if not isinstance(math, str) or not math.strip():
            confirmed.append(
                _finding(
                    "formula_machine_content_missing",
                    "confirmed_anomaly",
                    "formula/equation block has no machine-readable math content",
                    evidence={"formula_index": index},
                )
            )

    missing_bbox = sum(1 for b in main if _bbox(b) is None)
    if main and missing_bbox:
        review.append(
            _finding(
                "source_block_bbox_incomplete",
                "needs_review",
                "one or more main blocks cannot be positioned on the source page",
                evidence={"missing_bbox_blocks": missing_bbox, "main_blocks": len(main)},
            )
        )

    unresolved: list[str] = []
    for block in visual_blocks + formulas:
        visual_path = _visual_path(block)
        if visual_path and _resolve_visual_asset(
            visual_path, doc_dir=page_path.parent, source_file=source_file
        ) is None:
            unresolved.append(visual_path)
    if unresolved:
        confirmed.append(
            _finding(
                "unresolved_visual_asset",
                "confirmed_anomaly",
                "structured visual/formula block points to an asset that cannot be resolved",
                evidence={"missing_assets": sorted(set(unresolved))[:20], "count": len(set(unresolved))},
            )
        )

    heading = ""
    for block in reversed(main):
        title = _title_text(block)
        if title:
            heading = title
            break
    score = _max_plus_review_score(confirmed, review)
    page = PageAudit(
        domain=domain,
        doc_id=doc_id,
        page_number=page_number,
        page_path=str(page_path),
        source_file=str(source_file) if source_file else "",
        source_page_index=page_number - 1 if source_file else None,
        nearest_heading=heading,
        text_chars=text_chars,
        numeric_tokens=numeric_tokens,
        block_count=len(main),
        visual_block_count=len(visual_blocks),
        table_count=len(tables),
        formula_count=len(formulas),
        risk_score=score,
        risk_level=_page_risk_level(score),
        confirmed_anomalies=tuple(confirmed),
        review_flags=tuple(review),
    )
    return page, last_title_level


def audit_document(
    doc_dir: Path,
    *,
    domain: str,
    project_root: Path,
) -> DocumentAudit:
    """Audit one adapted document directory without modifying it."""
    doc_dir = Path(doc_dir)
    project_root = Path(project_root)
    doc_id = doc_dir.name
    confirmed: list[Finding] = []
    review: list[Finding] = []

    structure_path = doc_dir / "document_structure.json"
    structure: Mapping[str, Any] | None = None
    if not structure_path.is_file():
        confirmed.append(
            _finding(
                "missing_document_structure",
                "confirmed_anomaly",
                "document directory has no document_structure.json",
            )
        )
    else:
        try:
            raw = _read_json(structure_path)
            if isinstance(raw, Mapping):
                structure = raw
            else:
                raise ValueError("document_structure.json is not an object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            confirmed.append(
                _finding(
                    "invalid_document_structure",
                    "confirmed_anomaly",
                    "document_structure.json cannot be parsed as an object",
                    evidence={"error": type(exc).__name__},
                )
            )

    declared_page_count = 0
    source_file: Path | None = None
    source_pages: list[list[Mapping[str, Any]]] | None = None
    if structure is not None:
        declared_page_count = int(structure.get("page_count", 0) or 0)
        structure_doc_id = str(structure.get("doc_id", "") or "")
        if structure_doc_id != doc_id:
            confirmed.append(
                _finding(
                    "doc_id_mismatch",
                    "confirmed_anomaly",
                    "structure doc_id differs from document directory name",
                    evidence={"structure_doc_id": structure_doc_id, "directory_doc_id": doc_id},
                )
            )
        source_files = structure.get("source_files") or []
        if isinstance(source_files, Sequence) and source_files and isinstance(source_files[0], str):
            source_file = _resolve_project_path(source_files[0], project_root)
            if not source_file.is_file():
                confirmed.append(
                    _finding(
                        "missing_source_file",
                        "confirmed_anomaly",
                        "referenced MinerU source file does not exist",
                        evidence={"source_file": str(source_file)},
                    )
                )
            else:
                try:
                    source_payload = _read_json(source_file)
                    source_pages = _source_pages(source_payload)
                    if source_pages is None:
                        raise ValueError("unsupported content_list_v2 shape")
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    confirmed.append(
                        _finding(
                            "invalid_source_json",
                            "confirmed_anomaly",
                            "referenced MinerU source cannot provide per-page block lineage",
                            evidence={"error": type(exc).__name__, "source_file": str(source_file)},
                        )
                    )
                    source_pages = None
        else:
            confirmed.append(
                _finding(
                    "missing_source_file",
                    "confirmed_anomaly",
                    "document_structure.json does not contain a usable source_files entry",
                )
            )

    page_files = sorted(
        (p for p in doc_dir.iterdir() if p.is_file() and _page_file_number(p) is not None),
        key=lambda p: _page_file_number(p) or 0,
    ) if doc_dir.is_dir() else []
    page_numbers = [_page_file_number(p) for p in page_files]
    actual_page_count = len(page_files)

    if declared_page_count and declared_page_count != actual_page_count:
        confirmed.append(
            _finding(
                "page_count_mismatch",
                "confirmed_anomaly",
                "declared page_count differs from adapted page files",
                evidence={"declared": declared_page_count, "actual": actual_page_count},
            )
        )
    if declared_page_count:
        expected = set(range(1, declared_page_count + 1))
        actual = {int(n) for n in page_numbers if n is not None}
        missing_pages = sorted(expected - actual)
        if missing_pages:
            confirmed.append(
                _finding(
                    "declared_page_missing",
                    "confirmed_anomaly",
                    "one or more declared pages have no page_XXXX.md file",
                    evidence={"missing_pages": missing_pages[:100], "count": len(missing_pages)},
                )
            )
    if source_pages is not None and declared_page_count and len(source_pages) != declared_page_count:
        confirmed.append(
            _finding(
                "source_page_count_mismatch",
                "confirmed_anomaly",
                "MinerU source page count differs from document_structure page_count",
                evidence={"source_pages": len(source_pages), "declared": declared_page_count},
            )
        )

    page_has_table: dict[int, bool] = {}
    if source_pages is not None:
        for idx, blocks in enumerate(source_pages, start=1):
            page_has_table[idx] = any(str(b.get("type", "")) == "table" for b in blocks)

    pages: list[PageAudit] = []
    previous_title_level: int | None = None
    normalized_hash_to_pages: dict[str, list[int]] = defaultdict(list)
    normalized_text_by_page: dict[int, str] = {}
    for page_path in page_files:
        page_number = _page_file_number(page_path)
        if page_number is None:
            continue
        try:
            page_text = page_path.read_text(encoding="utf-8")
        except OSError:
            page_text = ""
        blocks: list[Mapping[str, Any]] = []
        if source_pages is not None and 0 <= page_number - 1 < len(source_pages):
            blocks = source_pages[page_number - 1]
        page, previous_title_level = _page_findings(
            domain=domain,
            doc_id=doc_id,
            page_number=page_number,
            page_path=page_path,
            page_text=page_text,
            blocks=blocks,
            source_file=source_file if source_file and source_file.is_file() else None,
            previous_has_table=page_has_table.get(page_number - 1, False),
            next_has_table=page_has_table.get(page_number + 1, False),
            previous_title_level=previous_title_level,
        )
        pages.append(page)
        normalized = _normalize_page_text(page_text)
        if len(normalized) >= int(THRESHOLDS["duplicate_min_text_chars"]):
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            normalized_hash_to_pages[digest].append(page_number)
            normalized_text_by_page[page_number] = normalized

    duplicate_page_numbers: set[int] = set()
    for digest, nums in normalized_hash_to_pages.items():
        if len(nums) > 1:
            duplicate_page_numbers.update(nums)
            confirmed.append(
                _finding(
                    "duplicate_substantive_page",
                    "confirmed_anomaly",
                    "two or more adapted pages have identical substantive text",
                    evidence={"page_numbers": nums, "sha256": digest},
                )
            )

    # Promote duplicate evidence to the affected page records without mutating
    # frozen dataclasses.
    if duplicate_page_numbers:
        promoted: list[PageAudit] = []
        for page in pages:
            if page.page_number not in duplicate_page_numbers:
                promoted.append(page)
                continue
            duplicate_finding = _finding(
                "duplicate_substantive_page",
                "confirmed_anomaly",
                "page substantive text is duplicated elsewhere in the same document",
                evidence={"page_number": page.page_number},
            )
            new_confirmed = tuple(page.confirmed_anomalies) + (duplicate_finding,)
            new_score = _max_plus_review_score(new_confirmed, page.review_flags)
            promoted.append(
                PageAudit(
                    **{
                        **asdict(page),
                        "risk_score": new_score,
                        "risk_level": _page_risk_level(new_score),
                        "confirmed_anomalies": new_confirmed,
                        "review_flags": page.review_flags,
                    }
                )
            )
        pages = promoted

    page_distribution = Counter(page.risk_level for page in pages)
    high_risk_pages = tuple(
        page
        for page in pages
        if page.risk_score >= int(THRESHOLDS["high_risk_page_score"])
    )

    page_confirmed = [f for page in pages for f in page.confirmed_anomalies]
    page_review = [f for page in pages for f in page.review_flags]
    scan_like_pages = sum(
        1 for p in pages if any(f.rule_id == "scan_like_page" for f in p.review_flags)
    )
    scan_like_ratio = scan_like_pages / len(pages) if pages else 0.0
    if len(pages) >= 3 and scan_like_ratio >= 0.50:
        review.append(
            _finding(
                "scan_dominant_document",
                "needs_review",
                "at least half of pages are visual-dominant with sparse machine-readable text",
                evidence={
                    "scan_like_pages": scan_like_pages,
                    "total_pages": len(pages),
                    "ratio": round(scan_like_ratio, 4),
                    "threshold": 0.50,
                },
            )
        )
    all_confirmed = confirmed + page_confirmed
    all_review = review + page_review
    score = _document_score(all_confirmed, all_review)

    metrics = {
        "total_pages": len(pages),
        "high_risk_pages": len(high_risk_pages),
        "confirmed_page_findings": len(page_confirmed),
        "review_page_findings": len(page_review),
        "scan_like_pages": scan_like_pages,
        "scan_like_ratio": round(scan_like_ratio, 4),
        "table_pages": sum(1 for p in pages if p.table_count > 0),
        "formula_pages": sum(1 for p in pages if p.formula_count > 0),
        "numeric_dense_pages": sum(1 for p in pages if p.numeric_tokens >= 8),
        "duplicate_pages": len(duplicate_page_numbers),
        "source_block_lineage_available": source_pages is not None,
    }

    return DocumentAudit(
        domain=domain,
        doc_id=doc_id,
        doc_dir=str(doc_dir),
        structure_found=structure is not None,
        declared_page_count=declared_page_count,
        actual_page_count=actual_page_count,
        source_file=str(source_file) if source_file else "",
        source_found=bool(source_file and source_file.is_file()),
        risk_score=score,
        risk_level=_risk_level(score),
        confirmed_anomalies=tuple(confirmed),
        review_flags=tuple(review),
        high_risk_pages=high_risk_pages,
        page_risk_distribution=dict(sorted(page_distribution.items())),
        metrics=metrics,
    )


def audit_corpus(corpus_root: Path, *, project_root: Path) -> tuple[DocumentAudit, ...]:
    """Audit every document directory under every domain in ``corpus_root``."""
    corpus_root = Path(corpus_root)
    docs: list[DocumentAudit] = []
    if not corpus_root.is_dir():
        return ()
    for domain_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for doc_dir in sorted(p for p in domain_dir.iterdir() if p.is_dir() and not p.name.startswith("_")):
            try:
                docs.append(
                    audit_document(doc_dir, domain=domain_dir.name, project_root=project_root)
                )
            except Exception as exc:  # noqa: BLE001 - corpus audit must continue per dispatch
                fatal = _finding(
                    "audit_read_failure",
                    "confirmed_anomaly",
                    "quality audit could not inspect this document; scan continued",
                    evidence={"error_type": type(exc).__name__, "error": str(exc)[:500]},
                )
                docs.append(
                    DocumentAudit(
                        domain=domain_dir.name,
                        doc_id=doc_dir.name,
                        doc_dir=str(doc_dir),
                        structure_found=(doc_dir / "document_structure.json").is_file(),
                        declared_page_count=0,
                        actual_page_count=len(list(doc_dir.glob("page_*.md"))),
                        source_file="",
                        source_found=False,
                        risk_score=100,
                        risk_level="critical",
                        confirmed_anomalies=(fatal,),
                        review_flags=(),
                        high_risk_pages=(),
                        page_risk_distribution={},
                        metrics={"audit_read_failure": True},
                    )
                )
    return tuple(docs)


def corpus_integrity_snapshot(
    corpus_root: Path,
    *,
    project_root: Path,
) -> dict[str, Any]:
    """Hash bytes and mtimes for all text/structure/source files touched by audit.

    This proves the audit is read-only without hashing unrelated logs or raw
    PDFs.  The snapshot covers every adapted page, every document structure,
    and every referenced MinerU source JSON used by the audit.
    """
    corpus_root = Path(corpus_root)
    files: set[Path] = set()
    for path in corpus_root.rglob("page_*.md"):
        if path.is_file():
            files.add(path.resolve())
    for path in corpus_root.rglob("document_structure.json"):
        if not path.is_file():
            continue
        files.add(path.resolve())
        try:
            structure = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(structure, Mapping):
            continue
        for raw in structure.get("source_files") or []:
            if isinstance(raw, str):
                source = _resolve_project_path(raw, project_root)
                if source.is_file():
                    files.add(source.resolve())

    byte_hasher = hashlib.sha256()
    mtime_hasher = hashlib.sha256()
    total_bytes = 0
    for path in sorted(files, key=lambda p: str(p).lower()):
        rel = str(path)
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        total_bytes += stat.st_size
        byte_hasher.update(f"{rel}\0{stat.st_size}\0{digest}\n".encode("utf-8"))
        mtime_hasher.update(f"{rel}\0{stat.st_mtime_ns}\n".encode("utf-8"))
    return {
        "scope": "adapted page markdown + document_structure.json + referenced MinerU source JSON",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "content_sha256": byte_hasher.hexdigest(),
        "mtime_sha256": mtime_hasher.hexdigest(),
    }


def audit_to_dict(value: Any) -> Any:
    """Convert dataclass-heavy audit values into JSON-serialisable values."""
    if hasattr(value, "__dataclass_fields__"):
        return {key: audit_to_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): audit_to_dict(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [audit_to_dict(v) for v in value]
    return value


def summarise_documents(documents: Sequence[DocumentAudit]) -> dict[str, Any]:
    by_domain: dict[str, dict[str, Any]] = {}
    for domain in sorted({d.domain for d in documents}):
        docs = [d for d in documents if d.domain == domain]
        by_domain[domain] = {
            "documents": len(docs),
            "pages": sum(d.actual_page_count for d in docs),
            "structured_documents": sum(d.structure_found for d in docs),
            "high_or_critical_documents": sum(d.risk_level in {"high", "critical"} for d in docs),
            "documents_with_high_risk_pages": sum(bool(d.high_risk_pages) for d in docs),
            "risk_surface_documents": sum(
                bool(d.high_risk_pages) or d.risk_level in {"high", "critical"} for d in docs
            ),
            "confirmed_anomaly_documents": sum(bool(d.confirmed_anomalies) or int(d.metrics.get("confirmed_page_findings", 0)) > 0 for d in docs),
            "review_flag_documents": sum(bool(d.review_flags) or int(d.metrics.get("review_page_findings", 0)) > 0 for d in docs),
        }
    confirmed_rules = Counter(
        f.rule_id
        for d in documents
        for f in (*d.confirmed_anomalies, *(f for p in d.high_risk_pages for f in p.confirmed_anomalies))
    )
    review_rules = Counter(
        f.rule_id
        for d in documents
        for f in (*d.review_flags, *(f for p in d.high_risk_pages for f in p.review_flags))
    )
    return {
        "document_directories_scanned": len(documents),
        "structured_documents": sum(d.structure_found for d in documents),
        "pages_scanned": sum(d.actual_page_count for d in documents),
        "domains": by_domain,
        "risk_level_distribution": dict(sorted(Counter(d.risk_level for d in documents).items())),
        "high_or_critical_documents": sum(d.risk_level in {"high", "critical"} for d in documents),
        "documents_with_high_risk_pages": sum(bool(d.high_risk_pages) for d in documents),
        "risk_surface_documents": sum(
            bool(d.high_risk_pages) or d.risk_level in {"high", "critical"}
            for d in documents
        ),
        "high_risk_pages": sum(len(d.high_risk_pages) for d in documents),
        "confirmed_rule_counts_high_risk_surface": dict(sorted(confirmed_rules.items())),
        "review_rule_counts_high_risk_surface": dict(sorted(review_rules.items())),
    }
