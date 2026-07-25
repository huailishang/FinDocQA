"""Deterministic clause-local claim atomization for BB-P0-13.

P13 narrows P11's atomizer semantics: condition, exception and time scope are
local to one top-level clause by default.  Semicolons/full stops cut scope;
parallel predicates inside the same clause may inherit an explicit leading
scope.  The implementation is model-free, qid-agnostic and answer-agnostic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Sequence


_METRICS = (
    "归属于母公司股东的净利润",
    "归属于上市公司股东的净利润",
    "经营活动产生的现金流量净额",
    "加权平均净资产收益率",
    "扣除非经常性损益后的基本每股收益",
    "基本每股收益",
    "营业收入",
    "营业总收入",
    "净利润",
    "利润总额",
    "市场占有率",
    "EBITDA 率",
    "EBITDA率",
    "增长率",
    "同比增速",
    "同比增长率",
    "环比增速",
    "现金价值",
    "保险金额",
    "保费",
    "赔付金额",
    "免赔额",
    "利率",
    "费率",
    "收费标准",
    "锁定期",
    "持有期限",
    "金额",
    "收入",
    "利润",
    "销量",
    "人数",
    "期限",
    "等待期",
    "保存期限",
    "占比",
    "比例",
)

_TIME_RE = re.compile(
    r"(?:(?:19|20)\d{2}\s*年(?:\d{1,2}\s*月(?:\d{1,2}\s*日)?)?|"
    r"第?\d+(?:\.\d+)?\s*(?:个保单年度|保单年度|年度|年|个月|月|个工作日|工作日|天|日)(?:内|前|后)?|"
    r"截至[^，；。]{0,24}|报告期(?:内|末)?)"
)
_VALUE_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>个百分点|万亿元|亿元|万元|千元|元|%|％|倍|个保单年度|保单年度|年|个月|月|个工作日|工作日|天|日|人|辆|笔)?"
)
_NEGATION_RE = re.compile(
    r"(?:不得|不能|不应|不可以|禁止|无需|无须|不包括|不属于|不是|未予|未能|未发生|未披露|无权|无效)"
)
_PROHIBITION_RE = re.compile(r"(?:不得|不能|不应|不可以|禁止)")
_CONDITION_PATTERNS = (
    re.compile(r"^\s*在[^，；。]{1,80}?情况下[，,]?"),
    re.compile(r"^\s*仅当[^，；。]{1,80}[，,]?"),
    re.compile(r"^\s*只有[^，；。]{1,80}?才[，,]?"),
    re.compile(r"^\s*如果[^，；。]{1,80}?(?:则|时)?[，,]"),
    re.compile(r"^\s*若[^，；。]{1,80}?(?:则|时)?[，,]"),
)
# "除非" is a logical marker only when it is not the tail of the financial
# term "扣除非经常性损益".  Parenthetical exceptions are handled separately.
_EXCEPTION_PATTERNS = (
    re.compile(r"^\s*(?<!扣)除非[^，；。]{1,80}[，,]?"),
    re.compile(r"^\s*(?<!扣)除[^，；。]{1,80}?外(?:的)?[，,]?"),
    re.compile(r"^\s*例外(?:是|为)?[^，；。]{0,60}[，,]?"),
)
_PAREN_RE = re.compile(r"[（(]([^（）()]{1,240})[）)]")
_PAREN_EXCEPTION_RE = re.compile(r"(?:除外|例外|除非)")
_QUANTIFIER_PATTERNS: Sequence[tuple[str, str]] = (
    ("at_least", "至少"),
    ("at_least", "不少于"),
    ("at_least", "不低于"),
    ("at_most", "至多"),
    ("at_most", "不超过"),
    ("at_most", "不高于"),
    ("greater_than", "高于"),
    ("greater_than", "大于"),
    ("greater_than", "超过"),
    ("less_than", "低于"),
    ("less_than", "小于"),
    ("less_than", "少于"),
    ("all", "全部"),
    ("all", "所有"),
    ("all", "均"),
    ("only", "仅"),
    ("any", "任一"),
    ("any", "任何"),
)
_RELATION_BY_QUANTIFIER = {
    "at_least": ">=",
    "at_most": "<=",
    "greater_than": ">",
    "less_than": "<",
    "all": "all",
    "only": "only",
    "any": "any",
}
_EQUALS_RE = re.compile(r"(?:等于|为|达到|达|是)")
_TOP_LEVEL_SPLIT_CHARS = frozenset("；;。")
_FACT_SPLIT_RE = re.compile(r"(?:，\s*而|,\s*而|并且|以及|同时|且)")


@dataclass(frozen=True)
class ClaimAtom:
    atom_id: str
    subject: str
    object_or_metric: str
    time_scope: str
    value: str
    unit: str
    relation: str
    polarity: str
    condition: str
    exception: str
    quantifier: str
    source_text: str
    atom_text: str
    clause_id: str = ""
    scope_confidence: str = "HIGH"
    scope_reason: str = "clause_local_default"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimAtomizationResult:
    source_text: str
    atoms: tuple[ClaimAtom, ...]
    deterministic: bool = True
    provider_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "atoms": [atom.to_dict() for atom in self.atoms],
            "deterministic": self.deterministic,
            "provider_calls": self.provider_calls,
        }


def atomize_claim(text: str, *, subject_hint: str = "") -> ClaimAtomizationResult:
    """Split a claim into clause-local semantic atoms.

    Scope never crosses a top-level semicolon/full stop.  A leading condition
    or exception is shared only by predicates inside the same clause.  A
    parenthetical exception stays attached to its host clause and is removed
    from the semantic core as one balanced unit, preserving the outer sentence.
    """
    source = str(text or "").strip()
    if not source:
        return ClaimAtomizationResult(source_text=source, atoms=())

    atoms: list[ClaimAtom] = []
    for clause_index, raw_clause in enumerate(_split_top_level_clauses(source), start=1):
        clause = raw_clause.strip(" ，,；;。")
        if not clause:
            continue
        clause_id = f"clause_{clause_index:02d}"
        scope = _clause_scope(clause)
        core = scope["core"]
        fact_parts = _split_fact_clauses(core)
        if not fact_parts:
            fact_parts = (core.strip(" ，,；;。"),)

        # Subject inheritance is allowed only inside this clause.  Semicolon /
        # full stop starts a new local subject chain.
        carry_subject = str(subject_hint or "").strip()
        for part in fact_parts:
            atom_core = part.strip(" ，,；;。")
            if not atom_core:
                continue
            metric = _extract_metric(atom_core)
            quantifier, quantifier_surface = _extract_quantifier(atom_core)
            value, unit = _extract_value_unit(atom_core, quantifier_surface, metric)
            polarity = "negative" if _NEGATION_RE.search(atom_core) else "positive"
            relation = _relation(atom_core, quantifier, polarity)
            subject = _extract_subject(atom_core, metric, carry_subject)
            if subject:
                carry_subject = subject
            object_or_metric = metric or _extract_action_or_object(atom_core, subject)

            atom_time = _time_scope_for_atom(atom_core, clause, scope["leading_time"])
            atom_exception = scope["exception"]
            atoms.append(
                ClaimAtom(
                    atom_id=f"atom_{len(atoms) + 1:02d}",
                    subject=subject,
                    object_or_metric=object_or_metric,
                    time_scope=atom_time,
                    value=value,
                    unit=unit,
                    relation=relation,
                    polarity=polarity,
                    condition=scope["condition"],
                    exception=atom_exception,
                    quantifier=quantifier_surface or quantifier,
                    source_text=source,
                    atom_text=part.strip(" ，,；;。"),
                    clause_id=clause_id,
                    scope_confidence=scope["confidence"],
                    scope_reason=scope["reason"],
                )
            )

    return ClaimAtomizationResult(source_text=source, atoms=tuple(atoms))


def _split_top_level_clauses(text: str) -> tuple[str, ...]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in str(text or ""):
        if char in "（(":
            depth += 1
        elif char in "）)" and depth > 0:
            depth -= 1
        if char in _TOP_LEVEL_SPLIT_CHARS and depth == 0:
            value = "".join(buf).strip()
            if value:
                parts.append(value)
            buf = []
            continue
        buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return tuple(parts)


def _clause_scope(clause: str) -> dict[str, str]:
    confidence = "HIGH"
    reasons: list[str] = ["clause_local_default"]
    if clause.count("（") != clause.count("）") or clause.count("(") != clause.count(")"):
        confidence = "LOW"
        reasons.append("unbalanced_parentheses")

    parenthetical_exceptions: list[str] = []
    core = clause
    for match in tuple(_PAREN_RE.finditer(clause)):
        inner = match.group(1).strip()
        if _PAREN_EXCEPTION_RE.search(inner):
            parenthetical_exceptions.append(inner)
            core = core.replace(match.group(0), "")
            reasons.append("host_parenthetical_exception")

    condition, core = _extract_condition_scope(core)
    exception, core = _extract_leading_scope(core, _EXCEPTION_PATTERNS)
    if condition:
        reasons.append("leading_condition_shared_within_clause")
    if exception:
        reasons.append("leading_exception_shared_within_clause")
    all_exceptions = tuple(dict.fromkeys([exception, *parenthetical_exceptions]))
    all_exceptions = tuple(value for value in all_exceptions if value)

    # Remaining logical markers in the middle of a clause are potentially
    # ambiguous because deterministic attachment is not guaranteed.
    residual = core
    if re.search(r"(?<!扣)除非|仅当|只有.*才|如果|若", residual):
        confidence = "LOW"
        reasons.append("ambiguous_internal_scope_marker")

    leading_time = _join_unique(_TIME_RE.findall(condition or ""))
    return {
        "core": re.sub(r"\s+", " ", core).strip(" ，,"),
        "condition": condition.strip(" ，,") if condition else "",
        "exception": _join_unique(all_exceptions),
        "leading_time": leading_time,
        "confidence": confidence,
        "reason": ";".join(reasons),
    }


def _extract_leading_scope(text: str, patterns: Sequence[re.Pattern[str]]) -> tuple[str, str]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = match.group(0).strip(" ，,")
            remainder = text[: match.start()] + " " + text[match.end() :]
            return value, re.sub(r"\s+", " ", remainder).strip()
    return "", text


def _extract_condition_scope(text: str) -> tuple[str, str]:
    condition, remainder = _extract_leading_scope(text, _CONDITION_PATTERNS)
    if condition:
        return condition, remainder
    # Allow one explicit subject/scope prefix before a comma, e.g.
    # "其他交易对方，若持有权益不足12个月，锁定期为12个月".  The prefix
    # remains in the semantic core so the following predicate can inherit it.
    match = re.match(r"^(?P<prefix>[^，,]{1,80})[，,]\s*(?P<tail>若|如果)(?P<body>[^，,；。]{1,80})[，,](?P<rest>.*)$", text)
    if not match:
        return "", text
    condition = f"{match.group('tail')}{match.group('body')}".strip(" ，,")
    remainder = f"{match.group('prefix')}，{match.group('rest')}"
    return condition, re.sub(r"\s+", " ", remainder).strip()


def _split_fact_clauses(text: str) -> tuple[str, ...]:
    expanded = re.sub(r"[，,]\s*而\s*", "；", text)
    primary = [
        part.strip()
        for part in re.split(r"；|并且|以及|同时|且", expanded)
        if part and part.strip(" ，,；;。")
    ]
    output: list[str] = []
    for part in primary:
        pieces = re.split(r"[，,]", part)
        if len(pieces) == 2:
            left, right = pieces[0].strip(), pieces[1].strip()
            left_has_metric = any(metric in left for metric in _METRICS)
            right_starts_metric = any(right.startswith(metric) for metric in _METRICS)
            if left_has_metric and right_starts_metric:
                output.extend((left, right))
                continue
        output.append(part.strip())
    return tuple(value for value in output if value)


def _extract_metric(text: str) -> str:
    positions = [
        (text.find(metric), -len(metric), metric)
        for metric in _METRICS
        if text.find(metric) >= 0
    ]
    if not positions:
        return ""
    positions.sort()
    return positions[0][2]


def _extract_quantifier(text: str) -> tuple[str, str]:
    positioned: list[tuple[int, str, str]] = []
    for canonical, surface in _QUANTIFIER_PATTERNS:
        position = text.find(surface)
        if position >= 0:
            positioned.append((position, canonical, surface))
    if not positioned:
        return "", ""
    positioned.sort(key=lambda item: (item[0], -len(item[2])))
    _, canonical, surface = positioned[0]
    return canonical, surface


def _extract_value_unit(text: str, quantifier_surface: str, metric: str) -> tuple[str, str]:
    starts: list[int] = []
    if quantifier_surface and quantifier_surface in text:
        starts.append(text.find(quantifier_surface) + len(quantifier_surface))
    if metric and metric in text:
        starts.append(text.find(metric) + len(metric))
    search_from = max(starts) if starts else 0
    matches = list(_VALUE_UNIT_RE.finditer(text, search_from)) + list(_VALUE_UNIT_RE.finditer(text, 0, search_from))
    for match in matches:
        value = match.group("value").replace(",", "")
        unit = str(match.group("unit") or "").replace("％", "%")
        # Four-digit calendar years are scope anchors, not claim values.
        if unit in {"年", "年度"} and len(value.lstrip("+-")) == 4 and value[:2] in {"19", "20"}:
            continue
        return value, unit
    return "", ""


def _relation(text: str, quantifier: str, polarity: str) -> str:
    if quantifier in _RELATION_BY_QUANTIFIER:
        return _RELATION_BY_QUANTIFIER[quantifier]
    if _PROHIBITION_RE.search(text):
        return "prohibited"
    if _EQUALS_RE.search(text):
        return "="
    if polarity == "negative":
        return "negated"
    return "asserted"


def _extract_subject(text: str, metric: str, fallback: str) -> str:
    cut_positions: list[int] = []
    if metric and metric in text:
        cut_positions.append(text.find(metric))
    negation = _NEGATION_RE.search(text)
    if negation:
        cut_positions.append(negation.start())
    quantifier = _extract_quantifier(text)[1]
    if quantifier and quantifier in text:
        cut_positions.append(text.find(quantifier))
    equals = _EQUALS_RE.search(text)
    if equals:
        cut_positions.append(equals.start())
    prefix = text[: min(cut_positions)] if cut_positions else ""
    prefix = _TIME_RE.sub("", prefix)
    prefix = re.sub(r"^(?:根据|按照|关于|其中|且|并|同时|而)\s*", "", prefix)
    prefix = prefix.strip(" ，,：:的\"“”")
    if 1 <= len(prefix) <= 60:
        return prefix
    return str(fallback or "").strip()


def _extract_action_or_object(text: str, subject: str) -> str:
    value = text
    if subject and value.startswith(subject):
        value = value[len(subject) :]
    value = _TIME_RE.sub("", value)
    value = _NEGATION_RE.sub("", value)
    for _, surface in _QUANTIFIER_PATTERNS:
        value = value.replace(surface, "")
    value = _VALUE_UNIT_RE.sub("", value)
    value = _EQUALS_RE.sub("", value)
    value = value.strip(" ，,：:的\"“”")
    value = re.sub(r"^在(?=\S)", "", value)
    return value[:100]


def _time_scope_for_atom(atom_text: str, clause: str, leading_time: str) -> str:
    local = _join_unique(_TIME_RE.findall(atom_text))
    if local:
        return _join_unique((leading_time, local))
    # A clause-level year before the first predicate applies to parallel facts
    # in that clause, but never crosses a top-level clause boundary.
    clause_times = _TIME_RE.findall(clause)
    calendar = [value for value in clause_times if re.search(r"(?:19|20)\d{2}\s*年", value)]
    return _join_unique((leading_time, *calendar))


def _join_unique(values: Sequence[str]) -> str:
    return "；".join(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
