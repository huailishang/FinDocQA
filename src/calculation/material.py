"""Material-formula extraction, local binding and completeness gate for C3-L2."""
from __future__ import annotations

import ast
import re
from decimal import Decimal, InvalidOperation
from typing import Mapping

from calculation.compiler import SafeFormulaCompiler, normalize_expression
from calculation.contracts import (
    BoundVariable,
    FormulaEvidence,
    FormulaGateResult,
    FormulaGateStatus,
    FormulaSourceRef,
)
from contracts import EvidenceCandidate


_AMOUNT_MULTIPLIERS = {
    "": Decimal("1"),
    "元": Decimal("1"),
    "万": Decimal("10000"),
    "万元": Decimal("10000"),
    "亿": Decimal("100000000"),
    "亿元": Decimal("100000000"),
    "ratio": Decimal("1"),
}
_PERCENT_UNITS = {"%", "％"}
_MIN_CONSTRAINT_MARKERS = ("不超过", "不得超过", "最高限额", "最高为", "上限", "至多", "较小值", "取小")
_MAX_CONSTRAINT_MARKERS = ("不低于", "不得低于", "最低限额", "最低为", "至少", "下限", "较大值", "取大")
_TABLE_REFERENCE_MARKERS = ("按下表", "见下表", "如下表", "详见表", "参见表", "见表")
_CROSS_PAGE_MARKERS = ("见下页", "下一页", "下页续", "续表")
_LOCAL_VALUE_PATTERN = r"(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|亿|万|元|[%％])?"
_MIN_TARGET_NAME_HINTS = ("limit", "cap", "ceiling", "upper", "maximum", "max_limit", "上限", "限额")
_MAX_TARGET_NAME_HINTS = ("floor", "lower", "minimum", "min_limit", "下限", "最低")


def normalize_value(value: str | int | float | Decimal, unit: str = "") -> Decimal:
    raw = str(value).strip().replace(",", "")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid_numeric_value:{value}") from exc
    normalized_unit = str(unit or "").strip()
    if normalized_unit in _PERCENT_UNITS:
        return number / Decimal("100")
    multiplier = _AMOUNT_MULTIPLIERS.get(normalized_unit)
    if multiplier is None:
        raise ValueError(f"unsupported_unit:{normalized_unit}")
    return number * multiplier


def _page_number(candidate: EvidenceCandidate) -> int | None:
    for key in ("page_number", "page", "page_index"):
        value = candidate.metadata.get(key)
        if isinstance(value, int):
            return value + 1 if key == "page_index" else value
        if str(value or "").isdigit():
            number = int(str(value))
            return number + 1 if key == "page_index" else number
    match = re.search(r"page[_-]?(\d+)", str(candidate.source or ""), re.I)
    return int(match.group(1)) if match else None


def _sentence_fragments(text: str) -> tuple[str, ...]:
    return tuple(fragment.strip() for fragment in re.split(r"[。；;\n]", text) if fragment.strip())


def _unit_category(unit: str) -> str:
    value = str(unit or "").strip()
    if value in _PERCENT_UNITS:
        return "ratio"
    if value in {"元", "万", "万元", "亿", "亿元"}:
        return "CNY"
    return "scalar"


def _local_symbol_values(context: str, name: str) -> tuple[tuple[Decimal, str, str], ...]:
    pattern = re.compile(rf"(?<![\w]){re.escape(name)}\s*[:=：]\s*{_LOCAL_VALUE_PATTERN}")
    rows: list[tuple[Decimal, str, str]] = []
    for match in pattern.finditer(str(context or "")):
        raw_value, unit = match.groups()
        normalized_unit = unit or ""
        try:
            value = normalize_value(raw_value, normalized_unit)
        except ValueError:
            continue
        rows.append((value, normalized_unit, _unit_category(normalized_unit)))
    return tuple(rows)


def _assignment_names(context: str) -> tuple[str, ...]:
    pattern = re.compile(
        rf"(?<![\w])([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)\s*[:=：]\s*{_LOCAL_VALUE_PATTERN}"
    )
    return tuple(dict.fromkeys(match.group(1) for match in pattern.finditer(str(context or ""))))


def _source_ref_valid(source_ref: FormulaSourceRef | None) -> bool:
    return bool(
        source_ref is not None
        and str(source_ref.doc_id or "").strip()
        and str(source_ref.source or "").strip()
    )


def _name_in_text(name: str, text: str) -> bool:
    if not name:
        return False
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None
    return name in text


def _governing_direct_name_operands(expression: str, function_name: str) -> set[str]:
    """Return direct variable operands only when the expression root governs output.

    For a textual upper/lower constraint, merely mentioning the target somewhere
    inside a nested/arithmetic min/max argument is not sufficient proof.  The
    conservative V1 rule requires the whole expression root to be the governing
    min/max call and the constraint target to be one of its direct ``ast.Name``
    arguments.
    """
    try:
        tree = ast.parse(normalize_expression(expression), mode="eval")
    except SyntaxError:
        return set()
    root = tree.body
    if not isinstance(root, ast.Call):
        return set()
    if not isinstance(root.func, ast.Name) or root.func.id != function_name:
        return set()
    return {arg.id for arg in root.args if isinstance(arg, ast.Name)}


def _constraint_fragments(evidence: FormulaEvidence, markers: tuple[str, ...]) -> tuple[str, ...]:
    explicit = tuple(str(item).strip() for item in evidence.conditions if str(item).strip())
    contextual = _sentence_fragments(evidence.context_text)
    source = tuple(dict.fromkeys((*explicit, *contextual)))
    return tuple(fragment for fragment in source if any(marker in fragment for marker in markers))


def _constraint_targets(
    fragments: tuple[str, ...],
    *,
    context: str,
    bindings: Mapping[str, BoundVariable],
    hints: tuple[str, ...],
) -> tuple[tuple[str, ...], bool]:
    names = set(bindings) | set(_assignment_names(context))
    explicit = {
        name
        for name in names
        if any(_name_in_text(name, fragment) for fragment in fragments)
    }
    if explicit:
        return tuple(sorted(explicit)), True
    inferred = {
        name
        for name in names
        if any(hint in name.lower() for hint in hints)
    }
    return tuple(sorted(inferred)), False


class MaterialFormulaExtractor:
    """Extract explicit local formulas while preserving surrounding evidence.

    This first version intentionally does not reconstruct a formula across pages.
    Cross-page/table references remain visible to the gate and therefore block
    deterministic execution until the missing evidence is supplied.
    """

    _FORMULA_PREFIX_RE = re.compile(r"^(?:公式\s*[:：]\s*)?(.+?)\s*=\s*(.+)$")

    @staticmethod
    def _is_formula_rhs(rhs: str, full_line: str) -> bool:
        return bool(
            "公式" in full_line
            or re.search(r"[+\-*/×÷()]", rhs)
            or re.search(r"\b(?:min|max|abs|MIN|MAX|ABS)\s*\(", rhs)
        )

    @staticmethod
    def _linked_table_refs(candidate: EvidenceCandidate) -> tuple[str, ...]:
        values: list[str] = []
        for key in ("table_id", "table_ids", "linked_table_refs"):
            raw = candidate.metadata.get(key)
            if isinstance(raw, (list, tuple, set)):
                values.extend(str(item) for item in raw if str(item))
            elif raw:
                values.append(str(raw))
        return tuple(dict.fromkeys(values))

    def extract_from_candidate(self, candidate: EvidenceCandidate) -> tuple[FormulaEvidence, ...]:
        context = "\n".join(
            part.strip()
            for part in (candidate.before_text, candidate.text, candidate.after_text)
            if str(part or "").strip()
        )
        conditions = tuple(
            fragment
            for fragment in _sentence_fragments(context)
            if any(marker in fragment for marker in (*_MIN_CONSTRAINT_MARKERS, *_MAX_CONSTRAINT_MARKERS))
        )
        source_ref = FormulaSourceRef(
            doc_id=candidate.doc_id,
            page_number=_page_number(candidate),
            source=candidate.source,
            block_id=str(candidate.metadata.get("block_id") or ""),
            excerpt=str(candidate.text or "")[:500],
        )
        results: list[FormulaEvidence] = []
        for raw_line in str(candidate.text or "").splitlines():
            line = raw_line.strip().strip("`$")
            if not line:
                continue
            match = self._FORMULA_PREFIX_RE.match(line)
            if match is None:
                continue
            lhs, rhs = match.groups()
            if not self._is_formula_rhs(rhs, line):
                continue
            expression = normalize_expression(rhs)
            if not expression:
                continue
            results.append(
                FormulaEvidence(
                    raw_formula=line,
                    normalized_expression=expression,
                    context_text=context,
                    conditions=conditions,
                    source_refs=(source_ref,),
                    linked_table_refs=self._linked_table_refs(candidate),
                    metadata={
                        "formula_label": lhs.strip(),
                        "formula_id": str(candidate.metadata.get("formula_id") or "material_formula"),
                        "domain": candidate.domain,
                    },
                )
            )
        return tuple(results)


class LocalContextVariableBinder:
    """Bind only explicit local ``name = numeric unit`` values.

    It is intentionally narrow. Values requiring table lookup, cross-page lookup,
    metric/entity disambiguation or natural-language inference stay unresolved.
    """

    def bind(self, evidence: FormulaEvidence) -> dict[str, BoundVariable]:
        try:
            symbols = SafeFormulaCompiler.referenced_symbols(evidence.normalized_expression)
        except ValueError:
            return {}
        source_ref = evidence.source_refs[0] if evidence.source_refs else None
        bindings: dict[str, BoundVariable] = {}
        for name in symbols:
            matches = _local_symbol_values(evidence.context_text, name)
            if not matches:
                continue
            distinct = {(value, category) for value, _unit, category in matches}
            if len(distinct) != 1:
                # Do not choose an arbitrary occurrence. The gate independently
                # reports ``ambiguous_variable_binding:<name>`` from the context.
                continue
            value, normalized_unit, _category = matches[0]
            bindings[name] = BoundVariable(
                name=name,
                value=value,
                unit=("ratio" if normalized_unit in _PERCENT_UNITS else normalized_unit),
                source_ref=source_ref,
                definition=str(evidence.variable_definitions.get(name) or ""),
                confidence="local_explicit",
            )
        return bindings


class FormulaEvidenceGate:
    """Fail closed before compilation/execution when L2 evidence is incomplete."""

    @staticmethod
    def _balanced(expression: str) -> bool:
        pairs = {')': '(', ']': '[', '}': '{'}
        stack: list[str] = []
        for char in expression:
            if char in "([{":
                stack.append(char)
            elif char in pairs:
                if not stack or stack.pop() != pairs[char]:
                    return False
        return not stack

    def evaluate(
        self,
        evidence: FormulaEvidence,
        bindings: Mapping[str, BoundVariable],
    ) -> FormulaGateResult:
        fatal: list[str] = []
        review: list[str] = []
        expression = normalize_expression(evidence.normalized_expression)
        if not evidence.raw_formula.strip() or not expression:
            fatal.append("formula_missing")
        if expression and not self._balanced(expression):
            fatal.append("unbalanced_delimiters")
        if expression and (re.search(r"[+\-*/=]\s*$", expression) or "..." in expression or "……" in expression):
            fatal.append("formula_truncated")

        referenced: tuple[str, ...] = ()
        if expression and not fatal:
            try:
                referenced = SafeFormulaCompiler.referenced_symbols(expression)
            except ValueError:
                fatal.append("invalid_formula_syntax")

        if not evidence.source_refs:
            review.append("source_lineage_missing")
        elif not all(_source_ref_valid(source_ref) for source_ref in evidence.source_refs):
            review.append("source_lineage_invalid")

        ambiguous_symbols: set[str] = set()
        for symbol in referenced:
            matches = _local_symbol_values(evidence.context_text, symbol)
            distinct = {(value, category) for value, _unit, category in matches}
            if len(distinct) > 1:
                ambiguous_symbols.add(symbol)
                review.append(f"ambiguous_variable_binding:{symbol}")
            if symbol not in bindings:
                if symbol not in ambiguous_symbols:
                    review.append(f"missing_variable_binding:{symbol}")
                continue
            variable_ref = bindings[symbol].source_ref
            if variable_ref is None:
                review.append(f"variable_lineage_missing:{symbol}")
            elif not _source_ref_valid(variable_ref):
                review.append(f"variable_lineage_invalid:{symbol}")

        context = evidence.context_text or ""
        min_fragments = _constraint_fragments(evidence, _MIN_CONSTRAINT_MARKERS)
        max_fragments = _constraint_fragments(evidence, _MAX_CONSTRAINT_MARKERS)
        normalized_compact = re.sub(r"\s+", "", expression).lower()

        if min_fragments:
            if "min(" not in normalized_compact:
                review.append("constraint_not_compiled:min")
            else:
                targets, explicit = _constraint_targets(
                    min_fragments,
                    context=context,
                    bindings=bindings,
                    hints=_MIN_TARGET_NAME_HINTS,
                )
                if not targets:
                    review.append("constraint_target_unresolved:min")
                elif not explicit and len(targets) > 1:
                    review.append("constraint_target_ambiguous:min")
                else:
                    operands = _governing_direct_name_operands(expression, "min")
                    for target in targets:
                        if target not in operands:
                            review.append(f"constraint_target_not_bound:min:{target}")

        if max_fragments:
            if "max(" not in normalized_compact:
                review.append("constraint_not_compiled:max")
            else:
                targets, explicit = _constraint_targets(
                    max_fragments,
                    context=context,
                    bindings=bindings,
                    hints=_MAX_TARGET_NAME_HINTS,
                )
                if not targets:
                    review.append("constraint_target_unresolved:max")
                elif not explicit and len(targets) > 1:
                    review.append("constraint_target_ambiguous:max")
                else:
                    operands = _governing_direct_name_operands(expression, "max")
                    for target in targets:
                        if target not in operands:
                            review.append(f"constraint_target_not_bound:max:{target}")

        if any(marker in context for marker in _TABLE_REFERENCE_MARKERS) and not evidence.linked_table_refs:
            review.append("linked_table_missing")
        if any(marker in context for marker in _CROSS_PAGE_MARKERS) and len(evidence.source_refs) < 2:
            review.append("cross_page_formula_context_missing")

        reasons = tuple(dict.fromkeys([*fatal, *review]))
        if fatal:
            status = FormulaGateStatus.FAIL
        elif review:
            status = FormulaGateStatus.REVIEW
        else:
            status = FormulaGateStatus.PASS
        return FormulaGateResult(status=status, reasons=reasons, referenced_variables=referenced)
