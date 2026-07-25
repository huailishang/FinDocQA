"""Semantic state audit for financial policies and historical corporate actions.

This module keeps proposal, approval, launch, execution, completion and
historical continuity separate.  It is QID-independent and intentionally does
not inspect answer labels.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


PROPOSED = "proposed"
SUGGESTED = "suggested"
BOARD_APPROVED = "board_approved"
SHAREHOLDER_APPROVED = "shareholder_approved"
PLAN_LAUNCHED = "plan_launched"
EXECUTED = "executed"
COMPLETED = "completed"
HISTORICAL_SERIES = "historical_series"
CONTINUOUS_THROUGH_REPORT_PERIOD = "continuous_through_report_period"
UNRESOLVED = "unresolved"

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_DURATION_RE = re.compile(r"连续\s*([一二三四五六七八九十\d]+)\s*年")
_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True)
class HistoricalActionAudit:
    action_verb: str
    start_year: int | None
    explicit_duration: int | None
    inferred_end_year: int | None
    execution_state: str
    continuity_scope: str
    supporting_source: str
    unresolved_reason: str
    claim_supported: bool
    report_year: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _duration(text: str) -> int | None:
    match = _DURATION_RE.search(text)
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return _CHINESE_NUMBERS.get(token)


def _action_verb(text: str) -> str:
    compact = _compact(text)
    for token in (
        "已实施完毕",
        "实施完毕",
        "已完成",
        "持续推出实施",
        "连续实施",
        "已实施",
        "实施",
        "推出回购计划",
        "推出",
        "拟推出",
        "建议",
    ):
        if token in compact:
            return token
    return ""


def _source_state(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("已实施完毕", "实施完毕", "已完成", "执行完毕")):
        return COMPLETED
    if any(token in compact for token in ("股东大会审议通过", "股东会审议通过")):
        return SHAREHOLDER_APPROVED
    if any(token in compact for token in ("董事会审议通过", "董事会通过")):
        return BOARD_APPROVED
    if "连续" in compact and any(token in compact for token in ("推出回购计划", "回购方案", "回购计划")):
        return HISTORICAL_SERIES
    if any(token in compact for token in ("持续推出实施", "已实施", "实际实施", "实施了一系列")):
        return EXECUTED
    if any(token in compact for token in ("推出计划", "推出回购计划", "启动计划", "启动回购")):
        return PLAN_LAUNCHED
    if any(token in compact for token in ("拟", "预案", "计划")):
        return PROPOSED
    if "建议" in compact:
        return SUGGESTED
    return UNRESOLVED


def audit_historical_action(
    *,
    claim_text: str,
    source_text: str,
    supporting_source: str,
    report_year: int | None = None,
) -> HistoricalActionAudit:
    """Audit one historical action claim against one source-local narrative.

    A finite historical series never proves completion or continuity through the
    report period unless the source says so explicitly.
    """
    claim = _compact(claim_text)
    source = _compact(source_text)
    source_years = [int(value) for value in _YEAR_RE.findall(source)]
    explicit_start = re.search(r"自((?:19|20)\d{2})年起", source)
    start_year = (
        int(explicit_start.group(1))
        if explicit_start
        else source_years[0]
        if source_years
        else None
    )
    duration = _duration(source)
    inferred_end = start_year + duration - 1 if start_year and duration else None
    state = _source_state(source)

    explicit_through_report = bool(
        report_year
        and any(
            token in source
            for token in (
                f"持续至{report_year}年",
                f"截至{report_year}年连续",
                f"{start_year}年至{report_year}年连续" if start_year else "",
            )
            if token
        )
    )
    continuity_scope = (
        CONTINUOUS_THROUGH_REPORT_PERIOD
        if explicit_through_report
        else HISTORICAL_SERIES
        if duration is not None and start_year is not None
        else UNRESOLVED
    )

    claim_requires_completion = any(
        token in claim for token in ("完成回购", "实施完毕", "已完成", "每年均已完成")
    )
    claim_requires_through_report = any(
        token in claim
        for token in (
            "持续至报告期",
            "持续到报告期",
            "截至报告期持续",
            f"持续至{report_year}年" if report_year else "",
            f"截至{report_year}年" if report_year else "",
        )
        if token
    )
    claim_historical_series = bool(
        "连续" in claim
        and any(token in claim for token in ("回购方案", "回购计划", "股份回购"))
    )
    source_has_implementation = any(
        token in source for token in ("持续推出实施", "实施了一系列", "已实施")
    )
    source_has_repurchase_series = bool(
        duration is not None
        and start_year is not None
        and "回购" in source
        and any(token in source for token in ("连续", "持续推出实施"))
    )

    unresolved_reason = ""
    supported = False
    if claim_requires_completion and state != COMPLETED:
        unresolved_reason = "source_does_not_prove_completed_state"
    elif claim_requires_through_report and not explicit_through_report:
        unresolved_reason = "finite_historical_series_does_not_prove_continuity_through_report_period"
    elif claim_historical_series and source_has_repurchase_series and source_has_implementation:
        supported = True
    elif claim_historical_series and source_has_repurchase_series:
        unresolved_reason = "source_proves_plan_series_but_not_implementation_semantics"
    else:
        unresolved_reason = "claim_and_source_action_semantics_do_not_fully_align"

    return HistoricalActionAudit(
        action_verb=_action_verb(source),
        start_year=start_year,
        explicit_duration=duration,
        inferred_end_year=inferred_end,
        execution_state=state,
        continuity_scope=continuity_scope,
        supporting_source=supporting_source,
        unresolved_reason=unresolved_reason,
        claim_supported=supported,
        report_year=report_year,
    )
