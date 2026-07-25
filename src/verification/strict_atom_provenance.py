"""Strict raw-atom provenance audit shared across financial QA domains.

The module is intentionally baseline/QID agnostic.  It distinguishes two strong
lanes:

* EXACT_ATOM_BINDING: every decisive claim atom is directly auditable in raw
  source text;
* NO_EQUIVALENCE_REQUIRED_TYPED_BINDING: the claim is closed by an explicit
  typed relation (for example 7 days versus 30 days, MUST versus MUST_NOT,
  BEFORE versus AFTER) without relying on a semantic synonym/alias.

If neither lane is demonstrated, callers must fail closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

EXACT_ATOM_BINDING = "EXACT_ATOM_BINDING"
NO_EQUIVALENCE_REQUIRED_TYPED_BINDING = "NO_EQUIVALENCE_REQUIRED_TYPED_BINDING"
INSUFFICIENT_PROVENANCE = "INSUFFICIENT_PROVENANCE"

_FIELD_ORDER = (
    "actor_entity",
    "object_metric_clause",
    "operation_relation",
    "modal_polarity",
    "condition_exception",
    "value_unit_comparator",
    "period_time_relation",
)

_VALUE_RE = re.compile(
    r"-?\d+(?:\.\d+)?\s*(?:%|％|亿元|万元|元|倍|天|日|个月|年|美元|亿|万|条|项|家|股|分贝)?"
)
_DATE_RE = re.compile(r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?")
_CONDITION_RE = re.compile(r"(?:若|如果|当|在|除非|但|但是)([^，。；]{2,60})")

# These vocabularies are relation/role primitives, not answer rules.  They are
# shared across domains and only expose raw atoms for audit.
_ACTOR_TERMS = (
    "金融机构", "上市公司", "证券公司", "保险公司", "会计师事务所", "银行卡清算机构",
    "数据处理者", "重要数据的处理者", "发行人", "董事会", "股东会", "审计委员会",
    "中国证监会", "证监会", "中国人民银行", "人民银行分支机构", "监管部门", "客户",
    "受益所有人", "董事", "监事", "高级管理人员", "签字注册会计师", "投保人", "被保险人",
)
_OBJECT_TERMS = (
    "客户尽职调查", "业务关系", "可疑交易报告", "大额交易报告", "受益所有人", "身份资料",
    "身份信息", "身份照片", "敏感数据项", "高敏感性数据项", "定期报告", "半年度报告",
    "年度报告", "风险评估报告", "核心数据", "重要数据", "业务数据", "数据安全事件",
    "行政处罚", "分类评价得分", "关联交易", "重大资产重组", "业务收入", "审计费",
    "发行规模", "发行金额", "主体信用评级", "信用评级", "违约金", "赔偿", "募集资金",
    "现金分红", "净利润", "营业收入", "现金流", "保险责任", "赔付", "伤残", "住院",
    "治疗费用", "审计报告", "处罚时效", "支付期限", "保存期限", "分支机构",
)
_OPERATION_TERMS = (
    "报告", "报送", "披露", "保存", "删除", "终止", "建立", "识别", "核实", "提供",
    "支付", "完成支付", "撤并", "审议通过", "批准", "处罚", "扣分", "没收", "罚款",
    "开展", "评估", "采取", "补救", "从轻", "减轻", "发行", "赔付", "给付", "计算",
    "增加", "减少", "增长", "下降", "高于", "低于", "超过", "少于", "等于", "包含",
    "属于", "早于", "晚于", "实施", "施行", "废止", "停止施行",
)
_COMPARATOR_TERMS = ("至少", "至多", "不超过", "超过", "高于", "低于", "大于", "小于", "等于", "不少于", "不低于", "不高于")
_TIME_TERMS = ("提前", "之后", "以后", "以前", "之前", "之日起", "当日", "立即", "每年", "每月", "每季度", "届满", "持续", "连续")

_MODAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("不得", "MUST_NOT"),
    ("无需", "NEED_NOT"),
    ("不需要", "NEED_NOT"),
    ("应当", "MUST"),
    ("必须", "MUST"),
    ("可以", "MAY"),
    ("可能", "MAY"),
    ("可", "MAY"),
)


@dataclass(frozen=True)
class AtomCoverageRow:
    field: str
    required: bool
    raw_option_atom: tuple[str, ...]
    raw_source_match: tuple[str, ...]
    match_type: str
    match_pass: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(value: Any) -> str:
    return re.sub(r"[\s，。；：、（）()《》\[\]【】‘’“”\"'<>]+", "", str(value or "")).replace("％", "%").lower()


def _uniq(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _present_terms(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    compact = _compact(text)
    return _uniq([term for term in terms if _compact(term) in compact])


def _modal_atoms(text: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw, canonical in _MODAL_PATTERNS:
        if raw == "可":
            # Single-character 可 is highly ambiguous inside lexical words such
            # as 可支配收入/可能/可靠.  Treat it as MAY only when it directly
            # governs a recognizable normative/action verb.
            if not re.search(r"可(?:报告|报送|披露|保存|删除|终止|建立|识别|核实|提供|支付|撤并|批准|处罚|扣分|发行|赔付|给付|采取|申请|要求|免除|不予|不|予以)", text):
                continue
        if raw in text and canonical not in result:
            result.append(canonical)
    if any(token in text for token in ("不", "未", "无", "没有", "并非", "不会")):
        result.append("NEGATIVE")
    else:
        result.append("AFFIRMATIVE")
    return _uniq(result)


def _typed_values(text: str) -> tuple[str, ...]:
    return _uniq([re.sub(r"\s+", "", item).replace("％", "%") for item in _VALUE_RE.findall(text)])


def _period_atoms(text: str) -> tuple[str, ...]:
    dates = [re.sub(r"\s+", "", item) for item in _DATE_RE.findall(text)]
    relations = list(_present_terms(text, _TIME_TERMS))
    return _uniq([*dates, *relations])


def extract_raw_claim_atoms(text: str, structured_hint: Mapping[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    """Extract auditable claim atoms without semantic synonym expansion."""
    structured_hint = structured_hint or {}
    actor = list(_present_terms(text, _ACTOR_TERMS))
    obj = list(_present_terms(text, _OBJECT_TERMS))
    op = list(_present_terms(text, _OPERATION_TERMS))

    # Reuse already-extracted raw structured hints, but never convert aliases.
    for atom in structured_hint.get("atoms") or []:
        actor.extend(str(value) for value in atom.get("entities") or [] if value)
        if atom.get("metric"):
            obj.append(str(atom["metric"]))
        if atom.get("comparator"):
            op.append(str(atom["comparator"]))
    spec = structured_hint.get("required_evidence_spec") or {}
    actor.extend(str(value) for value in spec.get("required_subject") or [] if value)
    obj.extend(str(value) for value in spec.get("required_metric_or_clause") or [] if value and value not in {"direct_fact"})

    conditions = [match.group(0) for match in _CONDITION_RE.finditer(text)]
    values = list(_typed_values(text))
    values.extend(_present_terms(text, _COMPARATOR_TERMS))
    units = re.findall(r"(?:%|％|亿元|万元|元|倍|天|日|个月|年|美元|亿|万|条|项|家|股|分贝)", text)
    values.extend(units)

    return {
        "actor_entity": _uniq(actor),
        "object_metric_clause": _uniq(obj),
        "operation_relation": _uniq(op),
        "modal_polarity": _modal_atoms(text),
        "condition_exception": _uniq(conditions),
        "value_unit_comparator": _uniq(values),
        "period_time_relation": _period_atoms(text),
    }


def _source_exact_matches(atoms: Sequence[str], source_text: str) -> tuple[str, ...]:
    source = _compact(source_text)
    return _uniq([atom for atom in atoms if _compact(atom) and _compact(atom) in source])


def _decisive_typed(items: Sequence[str]) -> tuple[str, ...]:
    generic_units = {"%", "日", "天", "年", "月", "个月", "元", "万元", "亿元", "万", "亿", "条", "项", "家", "股"}
    return _uniq([item for item in items if item != "AFFIRMATIVE" and item not in generic_units])


def _typed_relation_audit(claim_atoms: Mapping[str, Sequence[str]], source_atoms: Mapping[str, Sequence[str]], fact_status: str) -> dict[str, Any]:
    status = str(fact_status or "").upper()
    typed_fields = ("modal_polarity", "value_unit_comparator", "period_time_relation")
    decisive_overlap = bool(
        set(claim_atoms.get("object_metric_clause") or ()) & set(source_atoms.get("object_metric_clause") or ())
        or set(claim_atoms.get("operation_relation") or ()) & set(source_atoms.get("operation_relation") or ())
        or set(claim_atoms.get("actor_entity") or ()) & set(source_atoms.get("actor_entity") or ())
    )
    field_rows: list[dict[str, Any]] = []
    mismatch_count = 0
    support_field_passes: list[bool] = []
    for field in typed_fields:
        claim = _decisive_typed(tuple(claim_atoms.get(field) or ()))
        source = _decisive_typed(tuple(source_atoms.get(field) or ()))
        if not claim:
            continue
        exact = tuple(item for item in claim if item in source)
        claim_missing = tuple(item for item in claim if item not in source)
        source_extra = tuple(item for item in source if item not in claim)
        mismatch = bool(source and claim_missing and source_extra)
        support_field_pass = not claim_missing
        if mismatch:
            mismatch_count += 1
        support_field_passes.append(support_field_pass)
        field_rows.append({
            "field": field,
            "claim": claim,
            "source": source,
            "exact_overlap": exact,
            "claim_missing": claim_missing,
            "source_extra": source_extra,
            "typed_mismatch": mismatch,
            "support_field_pass": support_field_pass,
        })

    contradiction_pass = status == "CONTRADICTED" and decisive_overlap and mismatch_count > 0
    support_pass = status == "SUPPORTED" and decisive_overlap and bool(field_rows) and all(support_field_passes)
    return {
        "applicable": bool(field_rows),
        "decisive_non_typed_overlap": decisive_overlap,
        "fact_status": status,
        "field_rows": field_rows,
        "contradiction_relation_pass": contradiction_pass,
        "support_relation_pass": support_pass,
        "pass": contradiction_pass or support_pass,
    }


def audit_strict_atom_provenance(
    *,
    option_text: str,
    source_texts: Sequence[str],
    fact_status: str,
    structured_hint: Mapping[str, Any] | None = None,
    semantic_alias_dependencies: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return strict atom coverage plus typed-relation audit.

    Semantic alias dependencies always take precedence over these strong lanes;
    the caller decides the final provenance class.
    """
    source_text = "\n".join(str(value or "") for value in source_texts)
    claim_atoms = extract_raw_claim_atoms(option_text, structured_hint)
    source_atoms = extract_raw_claim_atoms(source_text)
    rows: list[AtomCoverageRow] = []
    required_count = 0
    passed_count = 0

    for field in _FIELD_ORDER:
        atoms = tuple(claim_atoms.get(field) or ())
        # AFFIRMATIVE is a default polarity marker, not a decisive raw token.
        decisive_atoms = tuple(atom for atom in atoms if atom != "AFFIRMATIVE")
        required = bool(decisive_atoms)
        matches = _source_exact_matches(decisive_atoms, source_text) if required else ()
        passed = (not required) or len(matches) == len(decisive_atoms)
        if required:
            required_count += 1
            passed_count += int(passed)
        rows.append(AtomCoverageRow(
            field=field,
            required=required,
            raw_option_atom=decisive_atoms,
            raw_source_match=matches,
            match_type="EXACT_RAW" if passed and required else ("NOT_REQUIRED" if not required else "MISSING_RAW_ATOM"),
            match_pass=passed,
        ))

    exact_pass = required_count > 0 and required_count == passed_count
    typed = _typed_relation_audit(claim_atoms, source_atoms, fact_status)
    alias_free = not semantic_alias_dependencies
    if alias_free and exact_pass:
        provenance_class = EXACT_ATOM_BINDING
        promotion_allowed = True
    elif alias_free and typed["pass"]:
        provenance_class = NO_EQUIVALENCE_REQUIRED_TYPED_BINDING
        promotion_allowed = True
    else:
        provenance_class = INSUFFICIENT_PROVENANCE
        promotion_allowed = False

    return {
        "claim_atoms": {key: list(value) for key, value in claim_atoms.items()},
        "source_atoms": {key: list(value) for key, value in source_atoms.items()},
        "required_atoms": [row.field for row in rows if row.required],
        "raw_source_spans": list(source_texts),
        "atom_coverage": [row.to_dict() for row in rows],
        "required_field_count": required_count,
        "passed_required_field_count": passed_count,
        "exact_atom_binding_pass": exact_pass,
        "typed_relation_audit": typed,
        "semantic_alias_dependencies": [dict(row) for row in semantic_alias_dependencies],
        "provenance_class": provenance_class,
        "promotion_allowed": promotion_allowed,
    }
