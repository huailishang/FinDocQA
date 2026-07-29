"""Built-in C3 formula registry for common financial calculations."""
from __future__ import annotations

import re
from typing import Sequence

from calculation.contracts import FormulaProgram, FormulaStep


class BuiltinFormulaRegistry:
    """Small deterministic registry; detection is intentionally conservative."""

    def detect(self, question_text: str) -> str | None:
        text = re.sub(r"\s+", "", str(question_text or ""))
        if not text:
            return None
        if any(marker in text for marker in ("百分点", "percentagepoint", "pctpoint")):
            return "percentage_point_change"
        if any(marker in text for marker in ("从高到低", "降序", "由高到低")):
            return "ranking_desc"
        if any(marker in text for marker in ("从低到高", "升序", "由低到高")):
            return "ranking_asc"
        # A bare “排序/排名/哪家更高” does not specify a stable output contract.
        # Fail closed instead of silently choosing descending order.
        if any(marker in text for marker in ("排序", "排名", "哪家更高", "哪家更低", "哪个最高", "哪个最低")):
            return None
        if any(marker in text for marker in ("同比", "环比", "增长率", "增幅", "下降率", "减少率")):
            return "growth_rate"
        if any(marker in text for marker in ("占比", "比例", "比重", "占营业收入", "占总额")):
            return "ratio"
        if any(marker in text for marker in ("差额", "相差", "多多少", "少多少", "差多少")):
            return "difference"
        return None

    def build(self, formula_id: str, *, variable_names: Sequence[str] = ()) -> FormulaProgram:
        formula_id = str(formula_id or "").strip()
        if formula_id == "growth_rate":
            return FormulaProgram(
                formula_id=formula_id,
                steps=(
                    FormulaStep("#1", "subtract", ("current", "previous")),
                    FormulaStep("#2", "divide", ("#1", "previous")),
                ),
                output_ref="#2",
                output_semantics="ratio",
            )
        if formula_id == "difference":
            return FormulaProgram(
                formula_id=formula_id,
                steps=(FormulaStep("#1", "subtract", ("left", "right")),),
                output_ref="#1",
                output_semantics="number",
            )
        if formula_id == "ratio":
            return FormulaProgram(
                formula_id=formula_id,
                steps=(FormulaStep("#1", "divide", ("part", "whole")),),
                output_ref="#1",
                output_semantics="ratio",
            )
        if formula_id == "percentage_point_change":
            return FormulaProgram(
                formula_id=formula_id,
                steps=(FormulaStep("#1", "subtract", ("current_rate", "previous_rate")),),
                output_ref="#1",
                output_semantics="percentage_point",
            )
        if formula_id in {"ranking_asc", "ranking_desc"}:
            names = tuple(str(name) for name in variable_names if str(name))
            if not names:
                raise ValueError("ranking_requires_variable_names")
            direction = "asc" if formula_id.endswith("_asc") else "desc"
            return FormulaProgram(
                formula_id=formula_id,
                steps=(FormulaStep("#1", f"sort_{direction}", names),),
                output_ref="#1",
                output_semantics=f"ranking_{direction}",
                metadata={"ranking_direction": direction},
            )
        if formula_id == "ranking":
            raise ValueError("ranking_direction_required")
        raise ValueError(f"unknown_builtin_formula:{formula_id}")
