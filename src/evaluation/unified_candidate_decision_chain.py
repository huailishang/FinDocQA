"""Unified, baseline-agnostic candidate-side decision chain.

This module composes already-existing project capabilities into a single auditable
sequence.  It intentionally does not know leaderboard scores, baseline answers,
or QID-specific expected labels.  Callers may compare its final answer with a
baseline only after the evidence, completeness, predicate-alignment and answer-
contract gates have closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from contracts import Question
from verification.compound_claims import route_option_claim


FACT_STATUSES = {"SUPPORTED", "CONTRADICTED", "PARTIAL_UNRESOLVED", "UNRESOLVED"}
EVIDENCE_GRADES = {
    "DIRECT_CLAUSE_SUPPORT",
    "DIRECT_CLAUSE_CONTRADICTION",
    "DERIVED_TOOL_RESULT",
    "COMPLETE_SCOPE_ABSENCE",
    "PARTIAL_RETRIEVAL_ABSENCE",
    "SCENARIO_PREREQUISITE_COMPLETE",
    "SCENARIO_PREREQUISITE_MISSING",
    "MODEL_ADVISORY_ONLY",
    "BASELINE_PARITY_ONLY",
}

CALCULATION_SEMANTICS = {
    "DIRECT_STATEMENT",
    "DIRECT_RELATION_STATEMENT",
    "DERIVED_CALCULATION_REQUIRED",
    "NON_CALCULATION",
}


def normalize_calculation_semantics(value: Any) -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "DIRECT_GROWTH_STATEMENT": "DIRECT_STATEMENT",
        "QUANTITATIVE_TEMPORAL_DIRECT_BINDING": "DIRECT_STATEMENT",
        "DERIVED_GROWTH_REQUIRED": "DERIVED_CALCULATION_REQUIRED",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in CALCULATION_SEMANTICS else ""

_CONNECTOR_RE = re.compile(r"(并且|以及|同时|且|而且|或者|或|并|但|但是|除非|若|如果|当|在.+?情况下)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?")
_VALUE_RE = re.compile(r"-?\d+(?:\.\d+)?\s*(?:%|％|亿元|万元|元|倍|天|日|个月|年|美元|亿|万|条|项|家|股)?")
_NEGATIVE_TOKENS = ("不", "未", "无", "没有", "不得", "并非", "低于", "少于", "下降", "减少")


def _has_negative_semantics(text: str) -> bool:
    """Detect semantic negation without treating lexical compounds like '不同' as negation."""
    normalized = str(text or "").replace("不同", "")
    return any(token in normalized for token in _NEGATIVE_TOKENS)
_QUESTION_SCOPE_PATTERNS = (
    re.compile(r"关于(.+?)的(?:细节|描述|信息|判断|说法|结论|情况)[，,？?]?$"),
    re.compile(r"其中关于(.+?)的(?:细节|描述|信息|判断|说法|结论|情况)"),
    re.compile(r"围绕(.+?)(?:进行|作出|判断|比较)"),
)

# Small lexical equivalence table for explicit stem predicates.  These are
# relation/metric aliases, not answer labels and not QID-specific rules.
_PREDICATE_ALIASES: Mapping[str, tuple[str, ...]] = {
    "发行规模": ("发行规模", "发行金额", "发行总额", "募集规模"),
    "违约赔偿": ("违约赔偿", "违约金", "违约责任", "赔偿计算", "惩罚系数"),
    "发行人身份": ("发行人", "发行主体", "公司名称", "主体身份"),
    "信用评级": ("信用评级", "主体评级", "主体信用等级", "债项评级"),
    "利润分配": ("利润分配", "现金分红", "分红", "派息"),
    "营业收入": ("营业收入", "营业总收入", "营收"),
    "净利润": ("净利润", "归母净利润", "归属于母公司股东的净利润"),
    "现金流": ("经营活动产生的现金流量净额", "经营现金流", "现金流量净额"),
}


@dataclass(frozen=True)
class QuestionPredicate:
    objects: tuple[str, ...]
    relations: tuple[str, ...]
    metrics: tuple[str, ...]
    actions: tuple[str, ...]
    conditions: tuple[str, ...]
    scopes: tuple[str, ...]
    periods: tuple[str, ...]
    polarity: str
    restricted_terms: tuple[str, ...]
    extraction_mode: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AtomicClaim:
    atom_id: str
    text: str
    connector_before: str
    polarity: str
    claim_type: str
    entities: tuple[str, ...]
    periods: tuple[str, ...]
    metric: str
    comparator: str
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequiredEvidenceSpec:
    required_doc_ids: tuple[str, ...]
    required_subject: tuple[str, ...]
    required_period: tuple[str, ...]
    required_metric_or_clause: tuple[str, ...]
    required_value_or_formula: tuple[str, ...]
    required_unit: tuple[str, ...]
    required_condition: tuple[str, ...]
    required_exception: tuple[str, ...]
    scenario_prerequisites_required: bool
    calculation_chain_required: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PredicateAlignment:
    status: str
    matched_terms: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScenarioGate:
    applicable: bool
    complete: bool
    missing_prerequisites: tuple[str, ...]
    matched_prerequisites: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalculationGate:
    applicable: bool
    complete: bool
    required_parts: tuple[str, ...]
    missing_parts: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(value: Any) -> str:
    return re.sub(r"[\s，。；：、（）()《》\[\]【】‘’“”\"'<>]+", "", str(value or "")).replace("％", "%").lower()


def _uniq(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _canonical_predicate_term(term: str) -> str:
    compact = _compact(term)
    for canonical, aliases in _PREDICATE_ALIASES.items():
        if compact == _compact(canonical) or any(_compact(alias) == compact for alias in aliases):
            return canonical
    return str(term).strip()


def extract_question_predicate(question_text: str) -> QuestionPredicate:
    text = str(question_text or "")
    compact = _compact(text)
    periods = _uniq(_YEAR_RE.findall(text))
    restricted: list[str] = []
    extraction_mode = "broad_question"
    for pattern in _QUESTION_SCOPE_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(1)
            # Keep only explicit stem scope items; do not infer related concepts.
            parts = re.split(r"或|或者|和|与|及|、|/", raw)
            restricted = [_canonical_predicate_term(part) for part in parts if part.strip()]
            extraction_mode = "explicit_restricted_scope"
            break

    metrics: list[str] = []
    for canonical, aliases in _PREDICATE_ALIASES.items():
        if any(_compact(alias) in compact for alias in aliases):
            metrics.append(canonical)
    actions = [token for token in ("发行", "赔偿", "支付", "提交", "报告", "识别", "比较", "增长", "下降", "赔付", "给付") if token in text]
    relations = [token for token in ("高于", "低于", "超过", "少于", "等于", "包含", "属于", "晚于", "早于", "均", "唯一", "单一") if token in text]
    conditions = [part for part in re.findall(r"(?:若|如果|当|在)([^，。；]{2,40})", text)]
    scopes = [token for token in ("两份文档", "全部", "均", "仅", "其中", "本期", "当年", "同期") if token in text]
    polarity = "negative" if _has_negative_semantics(text) else "affirmative"
    objects = list(restricted) if restricted else list(metrics)
    return QuestionPredicate(
        objects=_uniq(objects), relations=_uniq(relations), metrics=_uniq(metrics), actions=_uniq(actions),
        conditions=_uniq(conditions), scopes=_uniq(scopes), periods=periods, polarity=polarity,
        restricted_terms=_uniq(restricted), extraction_mode=extraction_mode,
    )


def predicate_alignment(predicate: QuestionPredicate, option_text: str) -> PredicateAlignment:
    if not predicate.restricted_terms:
        return PredicateAlignment("PASS_NOT_RESTRICTED", (), "question stem has no explicit restricted target dimension")
    option = _compact(option_text)
    matched: list[str] = []
    for term in predicate.restricted_terms:
        aliases = _PREDICATE_ALIASES.get(term, (term,))
        if any(_compact(alias) in option for alias in aliases):
            matched.append(term)
    if matched:
        return PredicateAlignment("PASS", _uniq(matched), "option directly addresses an explicit question-stem target")
    return PredicateAlignment("FAIL", (), "option does not address any explicit restricted question-stem target")


def decompose_option(option_text: str, question_doc_ids: Sequence[str] = ()) -> tuple[AtomicClaim, ...]:
    text = str(option_text or "").strip()
    route = route_option_claim(text, question_doc_ids)
    parts: list[tuple[str, str]] = []
    cursor = 0
    connector = "ROOT"
    for match in _CONNECTOR_RE.finditer(text):
        segment = text[cursor:match.start()].strip(" ，,；;")
        if segment:
            parts.append((connector, segment))
        connector = match.group(1)
        cursor = match.end()
    tail = text[cursor:].strip(" ，,；;")
    if tail:
        parts.append((connector, tail))
    if not parts:
        parts = [("ROOT", text)]

    claims: list[AtomicClaim] = []
    for index, (link, part) in enumerate(parts, 1):
        local = route_option_claim(part, question_doc_ids)
        polarity = "negative" if _has_negative_semantics(part) else "affirmative"
        claims.append(AtomicClaim(
            atom_id=f"atom_{index}", text=part, connector_before=link, polarity=polarity,
            claim_type=local.claim_type or route.claim_type, entities=tuple(local.entities),
            periods=tuple(local.periods), metric=local.metric, comparator=local.comparator,
            values=_uniq(_VALUE_RE.findall(part)),
        ))
    return tuple(claims)


def required_evidence_spec(
    question: Question, option_text: str, atoms: Sequence[AtomicClaim], *, calculation_semantics: str = ""
) -> RequiredEvidenceSpec:
    route = route_option_claim(option_text, question.doc_ids)
    subjects = _uniq(entity for atom in atoms for entity in atom.entities)
    periods = _uniq(period for atom in atoms for period in atom.periods)
    metrics = _uniq([atom.metric for atom in atoms if atom.metric])
    values = _uniq(value for atom in atoms for value in atom.values)
    units = _uniq(re.findall(r"(?:%|％|亿元|万元|元|倍|天|日|个月|年|美元|亿|万|条|项|家|股)", option_text))
    conditions = _uniq(re.findall(r"(?:若|如果|当|在)([^，。；]{2,50})", option_text))
    exceptions = _uniq(re.findall(r"(?:除外|除非|但|但是|不包括|例外)([^，。；]{0,50})", option_text))
    scenario = question.domain == "insurance" and bool(re.search(r"(?:某|先生|女士|因|发生|遭受|导致|事故|住院|治疗)", question.text))
    semantics = normalize_calculation_semantics(calculation_semantics)
    if semantics in {"DIRECT_STATEMENT", "DIRECT_RELATION_STATEMENT", "NON_CALCULATION"}:
        calculation = False
    elif semantics == "DERIVED_CALCULATION_REQUIRED":
        calculation = True
    else:
        # Do not infer derivation merely from a yoy-growth route.  The upstream
        # fact binder must explicitly mark DERIVED_CALCULATION_REQUIRED when the
        # source provides only operands and the answer depends on a calculation.
        calculation = bool(
            route.claim_type in {"numeric_sum_comparison", "cross_entity_comparison"}
            or re.search(r"(?:计算|公式|赔付金额|应赔|需要计算|合计|求和|换算)", option_text)
        )
    formula_atoms = ("formula", "operands", "unit", "condition_order") if calculation else ()
    return RequiredEvidenceSpec(
        required_doc_ids=tuple(map(str, question.doc_ids)), required_subject=subjects,
        required_period=periods, required_metric_or_clause=metrics or _uniq([route.claim_type]),
        required_value_or_formula=_uniq((*values, *formula_atoms)), required_unit=units,
        required_condition=conditions, required_exception=exceptions,
        scenario_prerequisites_required=scenario, calculation_chain_required=calculation,
    )


def normalize_fact_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value in {"supported", "support", "true", "correct"}:
        return "SUPPORTED"
    if value in {"contradicted", "refuted", "false", "incorrect"}:
        return "CONTRADICTED"
    if value in {"partial_unresolved", "partially_supported", "partial"}:
        return "PARTIAL_UNRESOLVED"
    return "UNRESOLVED"


def assign_evidence_grade(
    *, status: str, sources: Sequence[Mapping[str, Any]] = (), formula: str = "",
    variables: Mapping[str, Any] | None = None, scenario_gate: ScenarioGate | None = None,
) -> str:
    normalized = normalize_fact_status(status)
    roles = {_compact(source.get("source_role") or source.get("role") or "") for source in sources}
    if scenario_gate and scenario_gate.applicable and not scenario_gate.complete:
        return "SCENARIO_PREREQUISITE_MISSING"
    if formula and variables and normalized in {"SUPPORTED", "CONTRADICTED"}:
        return "DERIVED_TOOL_RESULT"
    if any("complete" in role and "absence" in role for role in roles):
        return "COMPLETE_SCOPE_ABSENCE"
    if normalized == "SUPPORTED" and sources:
        return "DIRECT_CLAUSE_SUPPORT"
    if normalized == "CONTRADICTED" and sources:
        return "DIRECT_CLAUSE_CONTRADICTION"
    if normalized in {"PARTIAL_UNRESOLVED", "UNRESOLVED"}:
        return "PARTIAL_RETRIEVAL_ABSENCE"
    return "MODEL_ADVISORY_ONLY"


def assess_scenario_prerequisites(
    question: Question,
    option_text: str,
    sources: Sequence[Mapping[str, Any]],
) -> ScenarioGate:
    applicable = question.domain == "insurance" and bool(re.search(r"(?:某|先生|女士|因|发生|遭受|导致|事故|住院|治疗)", question.text))
    if not applicable:
        return ScenarioGate(False, True, (), (), "not a scenario-dependent insurance option")
    stem = _compact(question.text)
    option = _compact(option_text)
    source_text = _compact("\n".join(str(source.get("span") or source.get("source_span") or "") for source in sources))
    missing: list[str] = []
    matched: list[str] = []

    checks = [
        ("transport_context", ("营运交通工具", "乘坐合法从事客运"), ("交通工具", "乘坐", "客运", "车", "船", "飞机")),
        ("hospital_or_treatment", ("住院医疗", "住院", "治疗费用"), ("住院", "治疗", "医疗费用", "费用")),
        ("vaccination_causality", ("预防接种",), ("预防接种", "疫苗", "接种")),
        ("disability_grade", ("伤残等级", "伤残评定"), ("伤残", "伤残等级", "评定")),
        ("hearing_threshold", ("91分贝", "永久不可逆", "听力损失"), ("91分贝", "永久不可逆", "听力测试", "听力损失")),
    ]
    for name, source_markers, stem_markers in checks:
        if any(_compact(marker) in source_text for marker in source_markers) or any(_compact(marker) in option for marker in source_markers):
            if any(_compact(marker) in stem for marker in stem_markers):
                matched.append(name)
            else:
                missing.append(name)

    # A payout question requires the scenario to satisfy the product definition,
    # not merely prove that the product contains a named disease/benefit clause.
    payout_question = any(token in stem for token in (_compact("获得赔付"), _compact("可以赔付"), _compact("可获赔"), _compact("赔付")))
    disease_definition = any(token in option or token in source_text for token in (_compact("重大疾病"), _compact("双耳失聪"), _compact("疾病定义")))
    if payout_question and disease_definition:
        definition_markers = (_compact("确诊"), _compact("符合定义"), _compact("达到"), _compact("永久不可逆"), _compact("91分贝"), _compact("听力测试"))
        if any(marker in stem for marker in definition_markers):
            matched.append("diagnosis_definition_satisfaction")
        else:
            missing.append("diagnosis_definition_satisfaction")
    complete = not missing
    return ScenarioGate(True, complete, _uniq(missing), _uniq(matched), "all source-implied scenario prerequisites are present" if complete else "question scenario omits prerequisites required by the cited coverage clause")


def assess_calculation_completeness(
    question: Question,
    option_text: str,
    *, formula: str = "", variables: Mapping[str, Any] | None = None,
    sources: Sequence[Mapping[str, Any]] = (), calculation_semantics: str = "",
) -> CalculationGate:
    route = route_option_claim(option_text, question.doc_ids)
    semantics = normalize_calculation_semantics(calculation_semantics)
    if semantics in {"DIRECT_STATEMENT", "DIRECT_RELATION_STATEMENT", "NON_CALCULATION"}:
        return CalculationGate(False, True, (), (), f"{semantics.lower()} does not require deterministic derivation")
    if semantics == "DERIVED_CALCULATION_REQUIRED":
        applicable = True
    else:
        applicable = bool(
            route.claim_type in {"numeric_sum_comparison", "cross_entity_comparison"}
            or re.search(r"(?:计算|公式|赔付金额|应赔|需要计算|合计|求和|换算)", option_text)
        )
    if not applicable:
        return CalculationGate(False, True, (), (), "not a deterministic calculation-dependent option")
    required = ["source", "formula_or_rule", "operands", "unit_or_dimension"]
    missing: list[str] = []
    if not sources:
        missing.append("source")
    if not formula:
        missing.append("formula_or_rule")
    if not variables:
        missing.append("operands")
    unit_present = bool(re.search(r"(?:%|％|亿元|万元|元|倍|天|日|个月|美元|亿|万)", option_text)) or any(
        source.get("normalized_units") or source.get("unit") for source in sources
    )
    if not unit_present:
        missing.append("unit_or_dimension")
    return CalculationGate(True, not missing, tuple(required), _uniq(missing), "calculation chain complete" if not missing else "calculation chain missing deterministic inputs")


def answer_contract_closed(question: Question, selected_labels: Sequence[str]) -> bool:
    selected = _uniq(selected_labels)
    contract = question.answer_contract
    if contract is not None:
        return bool(
            set(selected) <= set(map(str, contract.allowed_labels))
            and int(contract.min_selected) <= len(selected) <= int(contract.max_selected)
        )
    fmt = str(question.answer_format or "multi").lower()
    if fmt in {"mcq", "single", "single_choice", "tf", "boolean", "judge"}:
        return len(selected) == 1
    return bool(selected)


def decide_question(
    question: Question,
    option_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    predicate = extract_question_predicate(question.text)
    option_rows: dict[str, dict[str, Any]] = {}
    selected: list[str] = []
    for label, option_text in question.options.items():
        raw = dict(option_results.get(label) or {})
        status = normalize_fact_status(raw.get("status") or raw.get("fact_status"))
        sources = list(raw.get("sources") or [])
        atoms = decompose_option(option_text, question.doc_ids)
        calculation_semantics = normalize_calculation_semantics(raw.get("calculation_semantics"))
        spec = required_evidence_spec(question, option_text, atoms, calculation_semantics=calculation_semantics)
        alignment = predicate_alignment(predicate, option_text)
        scenario = assess_scenario_prerequisites(question, option_text, sources)
        calc = assess_calculation_completeness(
            question, option_text, formula=str(raw.get("formula") or ""),
            variables=raw.get("variables") if isinstance(raw.get("variables"), Mapping) else None,
            sources=sources, calculation_semantics=calculation_semantics,
        )
        grade = assign_evidence_grade(status=status, sources=sources, formula=str(raw.get("formula") or ""), variables=raw.get("variables") if isinstance(raw.get("variables"), Mapping) else None, scenario_gate=scenario)
        predicate_ok = alignment.status in {"PASS", "PASS_NOT_RESTRICTED"}
        scenario_ok = (not scenario.applicable) or scenario.complete
        calc_ok = (not calc.applicable) or calc.complete
        eligible = status == "SUPPORTED" and predicate_ok and scenario_ok and calc_ok
        if eligible:
            selected.append(label)
        option_rows[label] = {
            "option_text": option_text,
            "atoms": [atom.to_dict() for atom in atoms],
            "required_evidence_spec": spec.to_dict(),
            "fact_status": status,
            "evidence_grade": grade,
            "predicate_alignment": alignment.to_dict(),
            "scenario_prerequisite_gate": scenario.to_dict(),
            "calculation_semantics": calculation_semantics or "UNSPECIFIED",
            "calculation_completeness_gate": calc.to_dict(),
            "answer_eligibility": "YES" if eligible else "NO",
            "sources": sources,
            "formula": raw.get("formula") or "",
            "variables": raw.get("variables") or {},
            "blockers": list(raw.get("blockers") or []),
        }
    def decision_dimension_closed(row: Mapping[str, Any]) -> bool:
        if row["predicate_alignment"]["status"] == "FAIL":
            return True
        if row["fact_status"] == "CONTRADICTED":
            return True
        if row["fact_status"] != "SUPPORTED":
            return False
        scenario = row["scenario_prerequisite_gate"]
        calculation = row["calculation_completeness_gate"]
        scenario_ok = (not scenario["applicable"]) or scenario["complete"]
        calculation_ok = (not calculation["applicable"]) or calculation["complete"]
        return bool(scenario_ok and calculation_ok)

    all_closed = all(decision_dimension_closed(row) for row in option_rows.values())
    contract_ok = all_closed and answer_contract_closed(question, selected)
    answer = "".join(label for label in question.options if label in selected) if contract_ok else ""
    blockers: list[str] = []
    if not all_closed:
        blockers.append("full_option_closure_not_reached")
    if not contract_ok:
        blockers.append("answer_contract_not_closed")
    return {
        "qid": question.qid,
        "domain": question.domain,
        "question_predicate": predicate.to_dict(),
        "options": option_rows,
        "all_options_closed": all_closed,
        "answer_contract_closed": contract_ok,
        "recomputed_answer": answer,
        "blockers": blockers,
    }


class LocalCorrectiveRetriever:
    """Rules-only corrective retrieval over declared local documents.

    Returned spans are discovery candidates.  They become canonical facts only if
    a strict downstream evaluator re-binds them; this class never assigns an
    answer label or fact truth by itself.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def _doc_paths(self, domain: str, doc_id: str) -> list[Path]:
        roots = [
            self.repo_root / "data/processed_mineru" / domain / doc_id,
            self.repo_root / "data/processed_mineru_retrieval" / domain / doc_id,
        ]
        paths: list[Path] = []
        for root in roots:
            if root.is_file():
                paths.append(root)
            elif root.is_dir():
                paths.extend(sorted(root.rglob("*.md")))
                paths.extend(sorted(root.rglob("*.txt")))
                paths.extend(sorted(root.rglob("*.html")))
        return list(dict.fromkeys(path.resolve() for path in paths if path.is_file()))

    @staticmethod
    def _query_terms(option_text: str) -> tuple[str, ...]:
        values = [value.strip() for value in _VALUE_RE.findall(option_text)]
        years = list(_YEAR_RE.findall(option_text))
        words = re.findall(r"[\u4e00-\u9fff]{2,12}", option_text)
        stop = {"以下", "关于", "文档", "描述", "正确", "选项", "其中", "公司", "年度", "信息"}
        words = [word for word in words if word not in stop]
        ranked = values + years + sorted(words, key=len, reverse=True)
        return _uniq(ranked[:8])

    def search(self, question: Question, option_label: str, option_text: str, *, max_hits: int = 6) -> dict[str, Any]:
        terms = self._query_terms(option_text)
        actions: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = []
        levels = [
            (1, terms[:3], "exact_keywords_numbers_years"),
            (2, terms[:5], "expanded_terms_parent_context"),
            (3, terms, "required_doc_targeted_scan"),
            (4, terms, "declared_document_full_rule_scan"),
        ]
        for level, active_terms, action in levels:
            before = len(found)
            for doc_id in question.doc_ids:
                for path in self._doc_paths(question.domain, str(doc_id)):
                    try:
                        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                    except Exception:
                        continue
                    for index, line in enumerate(lines):
                        compact = _compact(line)
                        matched = [term for term in active_terms if _compact(term) and _compact(term) in compact]
                        threshold = 1 if level >= 3 else min(2, max(1, len(active_terms)))
                        if len(matched) < threshold:
                            continue
                        start, end = max(0, index - 2), min(len(lines), index + 3)
                        span = "\n".join(lines[start:end])
                        found.append({
                            "doc_id": str(doc_id), "path": str(path), "line_start": start + 1, "line_end": end,
                            "span": span, "span_sha256": hashlib.sha256(span.encode("utf-8")).hexdigest(),
                            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "matched_terms": matched,
                            "retrieval_level": level,
                        })
                        if len(found) >= max_hits:
                            break
                    if len(found) >= max_hits:
                        break
                if len(found) >= max_hits:
                    break
            actions.append({
                "level": level, "retrieval_action": action, "gap_before": "missing_canonical_fact",
                "query_terms": list(active_terms), "new_source_count": len(found) - before,
                "gap_after": "candidate_source_found_requires_strict_rebinding" if len(found) > before else "still_unresolved",
            })
            if found:
                break
        # Deterministic dedupe by source hash.
        unique = {row["span_sha256"]: row for row in found}
        return {
            "qid": question.qid, "option": option_label, "gap_before": "missing_canonical_fact",
            "actions": actions, "sources": list(unique.values()),
            "new_source": bool(unique), "new_canonical_fact": False,
            "gap_after": "requires_strict_rebinding" if unique else "unresolved",
        }
