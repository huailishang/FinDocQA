"""E1: parser/document quality evaluation against page-level gold anchors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from document.contracts import CanonicalDocument


def _ratio(hit: int, total: int) -> float | None:
    return None if total == 0 else hit / total


@dataclass(frozen=True)
class ParserGoldPage:
    page_number: int
    text_anchors: tuple[str, ...] = ()
    table_headers: tuple[tuple[str, ...], ...] = ()
    formula_anchors: tuple[str, ...] = ()
    figure_anchors: tuple[str, ...] = ()
    require_lineage: bool = True


@dataclass(frozen=True)
class ParserQualityResult:
    page_recall: float
    text_anchor_recall: float | None
    table_header_recall: float | None
    formula_anchor_recall: float | None
    figure_anchor_recall: float | None
    lineage_rate: float | None
    matched_pages: int
    gold_pages: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "page_recall": self.page_recall,
            "text_anchor_recall": self.text_anchor_recall,
            "table_header_recall": self.table_header_recall,
            "formula_anchor_recall": self.formula_anchor_recall,
            "figure_anchor_recall": self.figure_anchor_recall,
            "lineage_rate": self.lineage_rate,
            "matched_pages": self.matched_pages,
            "gold_pages": self.gold_pages,
        }


def evaluate_parser(
    document: CanonicalDocument,
    gold_pages: Sequence[ParserGoldPage],
) -> ParserQualityResult:
    gold = tuple(gold_pages)
    matched_pages = 0
    text_hit = text_total = 0
    table_hit = table_total = 0
    formula_hit = formula_total = 0
    figure_hit = figure_total = 0
    lineage_hit = lineage_total = 0

    for expected in gold:
        page = document.page(expected.page_number)
        if page is None:
            text_total += len(expected.text_anchors)
            table_total += len(expected.table_headers)
            formula_total += len(expected.formula_anchors)
            figure_total += len(expected.figure_anchors)
            if expected.require_lineage:
                lineage_total += 1
            continue
        matched_pages += 1

        for anchor in expected.text_anchors:
            text_total += 1
            if anchor and anchor in page.text:
                text_hit += 1

        predicted_headers = {tuple(table.headers) for table in page.tables if table.headers}
        for headers in expected.table_headers:
            table_total += 1
            if tuple(headers) in predicted_headers:
                table_hit += 1

        formula_text = "\n".join(
            f"{formula.expression}\n{formula.latex}" for formula in page.formulas
        )
        for anchor in expected.formula_anchors:
            formula_total += 1
            if anchor and anchor in formula_text:
                formula_hit += 1

        figure_text = "\n".join(
            f"{figure.uri}\n{figure.caption}\n{figure.alt_text}" for figure in page.figures
        )
        for anchor in expected.figure_anchors:
            figure_total += 1
            if anchor and anchor in figure_text:
                figure_hit += 1

        if expected.require_lineage:
            lineage_total += 1
            if page.lineage is not None and page.lineage.source_path:
                lineage_hit += 1

    page_recall = 1.0 if not gold else matched_pages / len(gold)
    return ParserQualityResult(
        page_recall=page_recall,
        text_anchor_recall=_ratio(text_hit, text_total),
        table_header_recall=_ratio(table_hit, table_total),
        formula_anchor_recall=_ratio(formula_hit, formula_total),
        figure_anchor_recall=_ratio(figure_hit, figure_total),
        lineage_rate=_ratio(lineage_hit, lineage_total),
        matched_pages=matched_pages,
        gold_pages=len(gold),
    )
