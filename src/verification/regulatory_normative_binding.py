"""Generic regulatory normative proposition extraction and relation binding.

The binder is intentionally QID-independent. It models the parts of a legal or
regulatory proposition that are easy to lose in keyword retrieval: actor,
object, operation, normative modality, condition, exception, scope and temporal
relation.  It never treats silence as permission and never infers a converse or
contrapositive from a one-way conditional rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence


UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"

_MODAL_ORDER = {
    "MUST_NOT": -3,
    "NEED_NOT": -2,
    "MAY": 1,
    "ENCOURAGED": 2,
    "MUST": 3,
}

_MODAL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("MUST_NOT", ("不得", "禁止", "严禁", "不可以", "不能")),
    ("NEED_NOT", ("无需", "不需要", "无须")),
    ("MUST", ("应当", "必须", "须", "应于", "应在", "应自")),
    ("MAY", ("可以", "可由", "有权", "可视情节")),
    ("ENCOURAGED", ("鼓励", "提倡")),
)

_ACTOR_TERMS = (
    "中国人民银行及其分支机构", "中国人民银行", "国家金融监督管理总局", "中国证监会",
    "证券交易所", "上市公司董事会", "上市公司", "证券公司", "银行卡清算机构",
    "非银行支付机构", "重要数据的处理者", "数据处理者", "会计师事务所", "签字注册会计师",
    "保险公司", "金融机构", "股东会", "董事会", "客户",
)

_OBJECT_TERMS = (
    "客户身份资料", "交易记录", "可疑交易报告", "大额交易报告", "风险评估报告", "核心数据",
    "重要数据", "保单贷款", "申请人身份", "分支机构", "半年度报告", "年度报告", "定期报告",
    "董事候选人", "现金分红", "具体原因", "业务收入", "分类评价得分", "分类评价结果",
    "关联交易事项", "重大资产重组文件", "行政处罚", "受益所有人", "身份信息", "照片",
)

_OPERATION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("report_suspicious_transaction", ("提交可疑交易报告", "报告可疑交易")),
    ("report_large_transaction", ("提交大额交易报告", "报告大额交易")),
    ("terminate_relationship", ("终止已建立的业务关系", "终止业务关系")),
    ("establish_relationship", ("建立业务关系",)),
    ("verify_identity", ("核实申请人身份", "核实身份", "识别并核实")),
    ("submit_risk_assessment", ("报送上一年度风险评估报告", "报送风险评估报告")),
    ("conduct_risk_assessment", ("开展风险评估", "进行风险评估")),
    ("report_branch_change", ("撤并分支机构", "报告撤并")),
    ("approve_report", ("审议通过", "审议批准")),
    # Negative/special lexical forms must precede their positive subsets.
    ("omit_disclosure", ("不披露", "无需披露")),
    ("disclose", ("披露", "信息披露")),
    ("delete", ("立即删除", "删除")),
    ("retain", ("保存", "留存")),
    ("pay_dividend", ("完成支付", "支付现金分红", "现金分红支付")),
    ("no_score_effect", ("不会受到影响", "不受影响")),
    ("deduct_score", ("扣分", "相应扣分", "下调公司分类评价结果级别", "受到影响")),
    ("repeal", ("停止施行", "废止")),
    ("effective", ("施行", "生效")),
    ("impose_penalty", ("作出处罚决定", "行政处罚", "予以处罚")),
    ("disclose_related_party", ("披露关联交易事项",)),
    ("standardize_storage", ("统一规范管理",)),
    ("store_terminal", ("终端设备中存储", "终端设备和移动介质中存储")),
)

_TIME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"至少提前\s*(\d+)\s*日", "AT_LEAST_BEFORE_DAYS"),
    (r"提前\s*(\d+)\s*日", "BEFORE_DAYS"),
    (r"(\d+)\s*个工作日内", "WITHIN_WORKING_DAYS"),
    (r"(\d+)\s*个?自然日内", "WITHIN_CALENDAR_DAYS"),
    (r"(\d+)\s*日内", "WITHIN_DAYS"),
    (r"(\d+)\s*个月内", "WITHIN_MONTHS"),
    (r"(\d+)\s*年内", "WITHIN_YEARS"),
    (r"至少保存\s*(\d+|十|五)\s*年", "AT_LEAST_YEARS"),
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%")


def _first_present(text: str, terms: Sequence[str]) -> str:
    compact = _compact(text)
    return next((term for term in terms if _compact(term) in compact), UNKNOWN)


def _modal(text: str) -> str:
    compact = _compact(text)
    for modal, patterns in _MODAL_PATTERNS:
        if any(_compact(pattern) in compact for pattern in patterns):
            return modal
    return UNKNOWN


def _operation(text: str) -> str:
    compact = _compact(text)
    # Negative/special operations must be checked before their positive lexical subset.
    for operation, patterns in _OPERATION_PATTERNS:
        if any(_compact(pattern) in compact for pattern in patterns):
            return operation
    return UNKNOWN


def _polarity(text: str, modal: str, operation: str) -> str:
    compact = _compact(text)
    if modal in {"MUST_NOT", "NEED_NOT"} or operation in {"omit_disclosure", "delete", "no_score_effect", "repeal"}:
        return "NEGATIVE"
    if any(token in compact for token in ("不", "未", "无", "否")):
        return "NEGATIVE_OR_CONDITIONAL"
    return "AFFIRMATIVE"


def _condition(text: str) -> str:
    compact = _compact(text)
    patterns = (
        (r"无法(?:按[^，。；]{0,24})?开展客户尽职调查", "customer_due_diligence_unavailable"),
        (r"已经建立业务关系", "existing_relationship"),
        (r"不具备(?:利润分配|现金分红)?条件而不进行现金分红", "not_eligible_and_no_cash_dividend"),
        (r"(?<!不)具备(?:利润分配|现金分红)?条件而不进行现金分红", "eligible_but_no_cash_dividend"),
        (r"发生业务数据安全事件造成危害后果", "data_incident_with_harm"),
        (r"已按照规定采取[^，。；]{0,24}措施.*(?:立即|及时)采取补救措施", "safeguards_and_remediation"),
        (r"办理保单贷款", "policy_loan_request"),
        (r"撤并分支机构", "branch_closure_or_merger"),
        (r"未经董事会审议通过", "without_board_approval"),
        (r"因违法违规行为被[^，。；]{0,24}行政处罚", "administrative_penalty_for_violation"),
        (r"评价期内[^，。；]{0,40}行政处罚", "penalty_during_evaluation_period"),
        (r"同一事项被实施行政处罚", "same_matter_penalty"),
    )
    for pattern, name in patterns:
        if re.search(pattern, compact):
            return name
    if any(token in compact for token in ("如果", "若", "如", "当", "在", "对于", "除非")):
        # Preserve a bounded lexical condition rather than pretending it is absent.
        match = re.search(r"(?:如果|若|如|当|在|对于)([^，。；]{2,60})", compact)
        return f"lexical:{match.group(1)}" if match else "present_unspecified"
    return NOT_APPLICABLE


def _exception(text: str) -> str:
    compact = _compact(text)
    if "除履行法定职责或者法定义务外" in compact:
        return "except_legal_duty"
    if "除法律另有规定外" in compact or "非依法律规定" in compact:
        return "except_as_provided_by_law"
    if "相关风险事件或者违法违规行为的重大影响尚未消除的除外" in compact:
        return "except_major_impact_not_eliminated"
    if "除非" in compact:
        match = re.search(r"除非([^，。；]{2,60})", compact)
        return f"unless:{match.group(1)}" if match else "unless_unspecified"
    return NOT_APPLICABLE


def _scope(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("所有", "全部", "任何", "一律", "均应")):
        return "UNIVERSAL"
    if any(token in compact for token in ("仅需", "才需要", "仅", "只有")):
        return "EXCLUSIVE_OR_ONLY"
    if any(token in compact for token in ("原则上", "一般")):
        return "GENERAL_WITH_POSSIBLE_EXCEPTION"
    return "SPECIFIC"


def _time_limit(text: str) -> str:
    compact = _compact(text)
    chinese = {"十": "10", "五": "5"}
    for pattern, relation in _TIME_PATTERNS:
        match = re.search(pattern, compact)
        if match:
            raw = match.group(1)
            return f"{relation}:{chinese.get(raw, raw)}"
    date = re.search(r"((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日", compact)
    if date:
        return f"ON_DATE:{int(date.group(1)):04d}-{int(date.group(2)):02d}-{int(date.group(3)):02d}"
    if "年度审计报告出具前" in compact:
        return "BEFORE:annual_audit_report_issued"
    if "年度审计报告出具后" in compact:
        return "AFTER:annual_audit_report_issued"
    if "股东会召开前" in compact:
        return "BEFORE:shareholder_meeting"
    if "变更完成后" in compact:
        return "AFTER:change_completed"
    if "业务关系结束后" in compact:
        return "AFTER:business_relationship_end"
    return NOT_APPLICABLE


def _effective_state(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("停止施行", "废止")):
        return "CEASED"
    if any(token in compact for token in ("施行", "生效")):
        return "EFFECTIVE"
    return NOT_APPLICABLE


def _continuation_state(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("持续", "继续", "仍未结束", "保存至")):
        return "CONTINUING"
    if any(token in compact for token in ("终止", "结束", "停止")):
        return "TERMINATED"
    return NOT_APPLICABLE


@dataclass(frozen=True)
class NormativeProposition:
    actor: str
    object: str
    operation: str
    modal: str
    polarity: str
    condition: str
    exception: str
    scope: str
    time_limit: str
    effective_state: str
    continuation_state: str
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormativeRelation:
    status: str
    actor_relation: str
    object_relation: str
    operation_relation: str
    modal_relation: str
    condition_relation: str
    exception_relation: str
    scope_relation: str
    time_relation: str
    effective_state_relation: str
    continuation_state_relation: str
    invalid_converse_blocked: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_normative_proposition(text: str) -> NormativeProposition:
    modal = _modal(text)
    operation = _operation(text)
    return NormativeProposition(
        actor=_first_present(text, _ACTOR_TERMS),
        object=_first_present(text, _OBJECT_TERMS),
        operation=operation,
        modal=modal,
        polarity=_polarity(text, modal, operation),
        condition=_condition(text),
        exception=_exception(text),
        scope=_scope(text),
        time_limit=_time_limit(text),
        effective_state=_effective_state(text),
        continuation_state=_continuation_state(text),
        raw_text=str(text or ""),
    )


def _entity_relation(claim: str, source: str) -> str:
    if claim == UNKNOWN:
        return "NOT_REQUIRED"
    if source == UNKNOWN:
        return "UNRESOLVED"
    if _compact(claim) == _compact(source) or _compact(claim) in _compact(source) or _compact(source) in _compact(claim):
        return "MATCH"
    aliases = (
        {"上市公司董事会", "董事会"},
        {"重要数据的处理者", "数据处理者"},
    )
    if any(claim in group and source in group for group in aliases):
        return "COMPATIBLE"
    return "CONFLICT"


def _operation_relation(claim: str, source: str) -> str:
    if claim == UNKNOWN:
        return "NOT_REQUIRED"
    if source == UNKNOWN:
        return "UNRESOLVED"
    if claim == source:
        return "MATCH"
    opposites = {
        ("delete", "retain"), ("retain", "delete"),
        ("omit_disclosure", "disclose"), ("disclose", "omit_disclosure"),
        ("no_score_effect", "deduct_score"), ("deduct_score", "no_score_effect"),
        ("report_large_transaction", "report_suspicious_transaction"),
        ("report_suspicious_transaction", "report_large_transaction"),
        ("effective", "repeal"), ("repeal", "effective"),
    }
    return "CONFLICT" if (claim, source) in opposites else "UNRESOLVED"


def _modal_relation(claim: str, source: str) -> str:
    if claim == UNKNOWN:
        return "NOT_REQUIRED"
    if source == UNKNOWN:
        return "UNRESOLVED"
    if claim == source:
        return "MATCH"
    # Explicit dispatch policy: permission cannot prove obligation, prohibition
    # defeats permission, and silence is never promoted to permission.
    if claim == "MUST" and source in {"MAY", "ENCOURAGED", "NEED_NOT"}:
        return "CONFLICT"
    if claim == "MAY" and source == "MUST_NOT":
        return "CONFLICT"
    if claim == "NEED_NOT" and source == "MUST":
        return "CONFLICT"
    if claim == "MUST_NOT" and source in {"MUST", "MAY"}:
        return "CONFLICT"
    if claim == "ENCOURAGED" and source == "MUST":
        return "COMPATIBLE_WEAKER_CLAIM"
    if claim == "MAY" and source == "MUST":
        return "COMPATIBLE_WEAKER_CLAIM"
    return "UNRESOLVED"


def _condition_relation(claim: str, source: str) -> tuple[str, bool]:
    if claim == NOT_APPLICABLE:
        return "NOT_REQUIRED", False
    if source == NOT_APPLICABLE:
        return "UNRESOLVED", False
    if claim == source:
        return "MATCH", False
    converse_pairs = {
        ("not_eligible_and_no_cash_dividend", "eligible_but_no_cash_dividend"),
    }
    if (claim, source) in converse_pairs:
        return "CONVERSE_NOT_INFERRED", True
    # Existing relationship is a narrower branch of inability to conduct CDD.
    if claim == "existing_relationship" and source == "customer_due_diligence_unavailable":
        return "COMPATIBLE_NESTED", False
    return "UNRESOLVED", False


def _simple_relation(claim: str, source: str, *, not_applicable: str = NOT_APPLICABLE) -> str:
    if claim == not_applicable:
        return "NOT_REQUIRED"
    if source == not_applicable:
        return "UNRESOLVED"
    return "MATCH" if claim == source else "UNRESOLVED"


def _time_relation(claim: str, source: str) -> str:
    if claim == NOT_APPLICABLE:
        return "NOT_REQUIRED"
    if source == NOT_APPLICABLE:
        return "UNRESOLVED"
    if claim == source:
        return "MATCH"
    if claim.startswith("BEFORE") and source.startswith("AFTER"):
        return "CONFLICT"
    if claim.startswith("AFTER") and source.startswith("BEFORE"):
        return "CONFLICT"
    def numeric(value: str) -> tuple[str, float] | None:
        if ":" not in value:
            return None
        kind, raw = value.rsplit(":", 1)
        try:
            return kind, float(raw)
        except ValueError:
            return None
    cnum, snum = numeric(claim), numeric(source)
    if cnum and snum:
        ckind, cvalue = cnum
        skind, svalue = snum
        same_family = (
            ("DAYS" in ckind and "DAYS" in skind)
            or ("YEARS" in ckind and "YEARS" in skind)
            or ("MONTHS" in ckind and "MONTHS" in skind)
        )
        if same_family and cvalue != svalue:
            return "CONFLICT"
    return "UNRESOLVED"


def compare_normative_propositions(claim: NormativeProposition, source: NormativeProposition) -> NormativeRelation:
    actor = _entity_relation(claim.actor, source.actor)
    obj = _entity_relation(claim.object, source.object)
    operation = _operation_relation(claim.operation, source.operation)
    modal = _modal_relation(claim.modal, source.modal)
    condition, invalid_converse = _condition_relation(claim.condition, source.condition)
    exception = _simple_relation(claim.exception, source.exception)
    scope = "MATCH" if claim.scope == source.scope else (
        "NOT_REQUIRED" if claim.scope == "SPECIFIC" else "UNRESOLVED"
    )
    time = _time_relation(claim.time_limit, source.time_limit)
    effective = _simple_relation(claim.effective_state, source.effective_state)
    continuation = _simple_relation(claim.continuation_state, source.continuation_state)

    relations = {
        "actor": actor, "object": obj, "operation": operation, "modal": modal,
        "condition": condition, "exception": exception, "scope": scope, "time": time,
        "effective": effective, "continuation": continuation,
    }
    if invalid_converse:
        return NormativeRelation(
            status="UNRESOLVED", actor_relation=actor, object_relation=obj,
            operation_relation=operation, modal_relation=modal, condition_relation=condition,
            exception_relation=exception, scope_relation=scope, time_relation=time,
            effective_state_relation=effective, continuation_state_relation=continuation,
            invalid_converse_blocked=True,
            reason="source states a one-way condition; converse or negated-condition conclusion is not inferred",
        )
    if any(value == "CONFLICT" for value in relations.values()):
        return NormativeRelation(
            status="CONTRADICTED", actor_relation=actor, object_relation=obj,
            operation_relation=operation, modal_relation=modal, condition_relation=condition,
            exception_relation=exception, scope_relation=scope, time_relation=time,
            effective_state_relation=effective, continuation_state_relation=continuation,
            invalid_converse_blocked=False,
            reason="one or more material normative dimensions directly conflict",
        )
    material = (actor, obj, operation, modal, condition, time)
    if all(value in {"MATCH", "COMPATIBLE", "COMPATIBLE_WEAKER_CLAIM", "COMPATIBLE_NESTED", "NOT_REQUIRED"} for value in material):
        return NormativeRelation(
            status="SUPPORTED", actor_relation=actor, object_relation=obj,
            operation_relation=operation, modal_relation=modal, condition_relation=condition,
            exception_relation=exception, scope_relation=scope, time_relation=time,
            effective_state_relation=effective, continuation_state_relation=continuation,
            invalid_converse_blocked=False,
            reason="material normative dimensions are compatible",
        )
    return NormativeRelation(
        status="UNRESOLVED", actor_relation=actor, object_relation=obj,
        operation_relation=operation, modal_relation=modal, condition_relation=condition,
        exception_relation=exception, scope_relation=scope, time_relation=time,
        effective_state_relation=effective, continuation_state_relation=continuation,
        invalid_converse_blocked=False,
        reason="no direct conflict, but one or more material normative dimensions remain unresolved",
    )


def best_normative_relation(claim_text: str, source_windows: Sequence[str]) -> dict[str, Any]:
    claim = extract_normative_proposition(claim_text)
    ranked: list[tuple[int, NormativeRelation, NormativeProposition]] = []
    weights = {"SUPPORTED": 300, "CONTRADICTED": 250, "UNRESOLVED": 0}
    for source_text in source_windows:
        source = extract_normative_proposition(source_text)
        relation = compare_normative_propositions(claim, source)
        score = weights[relation.status]
        score += sum(
            value in {"MATCH", "COMPATIBLE", "COMPATIBLE_WEAKER_CLAIM", "COMPATIBLE_NESTED"}
            for value in (
                relation.actor_relation, relation.object_relation, relation.operation_relation,
                relation.modal_relation, relation.condition_relation, relation.time_relation,
            )
        ) * 10
        ranked.append((score, relation, source))
    if not ranked:
        return {
            "claim": claim.to_dict(), "best_source": None,
            "relation": NormativeRelation(
                status="UNRESOLVED", actor_relation="UNRESOLVED", object_relation="UNRESOLVED",
                operation_relation="UNRESOLVED", modal_relation="UNRESOLVED", condition_relation="UNRESOLVED",
                exception_relation="UNRESOLVED", scope_relation="UNRESOLVED", time_relation="UNRESOLVED",
                effective_state_relation="UNRESOLVED", continuation_state_relation="UNRESOLVED",
                invalid_converse_blocked=False, reason="no source window supplied",
            ).to_dict(),
        }
    ranked.sort(key=lambda row: row[0], reverse=True)
    _, relation, source = ranked[0]
    return {"claim": claim.to_dict(), "best_source": source.to_dict(), "relation": relation.to_dict()}


def closure_distance_rank(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rank blockers by question closure distance before secondary evidence value."""
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        unresolved = list(row.get("unresolved_options_after") or row.get("unresolved_options") or [])
        count = len(unresolved)
        tier = 0 if count <= 1 else 1 if count == 2 else 2 if count == 3 else 3
        verified = int(row.get("verified_source_option_count") or 0)
        missing_lte2 = int(row.get("missing_semantic_lte2_option_count") or 0)
        potential_delta = bool(row.get("potential_delta") or row.get("candidate_delta_after"))
        same_mechanism = int(row.get("same_mechanism_cluster_value") or 0)
        no_forbidden = bool(row.get("no_historical_forbidden_conflict", True))
        scenario_missing = bool(row.get("scenario_core_prerequisite_missing", False))
        structural = bool(row.get("structural_ambiguity", False))
        secondary = (
            verified * 4 + missing_lte2 * 2 + (4 if potential_delta else 0)
            + min(same_mechanism, 3) + (2 if no_forbidden else -5)
            - (5 if scenario_missing else 0) - (3 if structural else 0)
        )
        row.update({
            "unresolved_option_count": count,
            "closure_distance_tier": tier,
            "secondary_score": secondary,
            "primary_sort_key": [tier, count],
        })
        result.append(row)
    result.sort(key=lambda row: (
        int(row["closure_distance_tier"]), int(row["unresolved_option_count"]),
        -int(row["secondary_score"]), str(row.get("qid") or ""),
    ))
    return result


def regulatory_complete_scope_absence_shadow(
    *,
    option_text: str,
    declared_documents_complete: bool,
    option_required_docs_complete: bool,
    relevant_section_boundaries_identified: bool,
    target_relation_defined: bool,
    alias_family_scanned: bool,
    full_relevant_scope_scanned: bool,
    supporting_or_contradicting_clause_found: bool,
) -> dict[str, Any]:
    checks = {
        "declared_documents_complete": bool(declared_documents_complete),
        "option_required_docs_complete": bool(option_required_docs_complete),
        "relevant_section_boundaries_identified": bool(relevant_section_boundaries_identified),
        "target_relation_defined": bool(target_relation_defined),
        "alias_family_scanned": bool(alias_family_scanned),
        "full_relevant_scope_scanned": bool(full_relevant_scope_scanned),
        "no_supporting_or_contradicting_clause_found": not bool(supporting_or_contradicting_clause_found),
    }
    qualifies = all(checks.values())
    return {
        "option_text": option_text,
        "checks": checks,
        "status": "SHADOW_ABSENCE_EVIDENCE" if qualifies else "FAIL_CLOSED_NOT_SHADOW_ABSENCE",
        "tier_a_allowed": False,
        "candidate_gate_only": True,
    }
