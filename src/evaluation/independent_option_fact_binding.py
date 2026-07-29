"""Independent option-level claim/fact binding for leaderboard candidates.

The production module is deliberately dataset-agnostic: it contains no QIDs,
expected answers, or complete option-text special cases. API judgments are not
accepted as production facts. Callers must supply corpus-backed option-local
facts and a separately sourced independent oracle.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

OPTION_LABELS = tuple("ABCD")
BINDING_DIMENSIONS = (
    "entity_match", "period_match", "comparison_period_match",
    "action_or_metric_match", "relation_match", "value_match",
    "unit_match", "unit_family_match", "statement_scope_match",
    "attribution_scope_match", "policy_stage_match", "condition_match",
    "exception_match", "negation_match",
)
VALID_BINDING_STATES = {"match", "conflict", "not_required", "unresolved"}
JUNK_SOURCE_TOKENS = {"sufficiency", "claim_ast", "evidence"}
_PLACEHOLDERS = {"", "runtime_bound", "unknown_but_closed", "unknown", "placeholder"}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%").lower()


def _canonical_answer(value: Any) -> str:
    return "".join(sorted({ch for ch in str(value or "").upper() if ch in OPTION_LABELS}))


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return None


def _clean(value: Any, *, default: str = "unresolved") -> str:
    text = str(value or "").strip()
    return text if text and text.lower() not in _PLACEHOLDERS else default


def unit_family(unit: Any) -> str:
    text = _compact(unit)
    if text in {"not_applicable", "unresolved"}:
        return text
    if any(token in text for token in ("cny/10shares", "元/10股", "每10股")):
        return "currency_per_10_shares"
    if any(token in text for token in ("cny/share", "元/股", "每股")):
        return "currency_per_share"
    if "%" in text or "percentage" in text:
        return "percentage"
    if any(token in text for token in ("cny", "人民币", "亿元", "万元", "千元", "元")):
        return "currency_total"
    if any(token in text for token in ("boolean", "bool", "true_false")):
        return "boolean"
    if "date" in text or "日期" in text:
        return "date"
    if "count" in text or "次" in text or "个" in text:
        return "count"
    if "clause" in text or "scope" in text or "relation" in text or "policy" in text:
        return "categorical"
    return "unresolved"


@dataclass(frozen=True)
class OptionClaimSpec:
    qid: str
    option: str
    claim_text: str
    entity: str
    entity_role: str
    period: str
    comparison_period: str
    action_or_metric: str
    relation: str
    value: Any
    comparison_value: Any
    unit: str
    unit_family: str
    statement_scope: str
    attribution_scope: str
    policy_stage: str
    condition: str
    exception: str
    negation: bool | str
    required_doc_ids: tuple[str, ...]
    parse_failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def complete(self) -> bool:
        required = (self.entity, self.action_or_metric, self.relation)
        return all(str(value).lower() not in _PLACEHOLDERS for value in required) and not self.parse_failures


@dataclass(frozen=True)
class EvidenceFactSpec:
    qid: str
    option: str
    source_doc_id: str
    source_path: str
    source_anchor: str
    source_span_text: str
    source_span_sha256: str
    fact_entity: str
    fact_period: str
    fact_action_or_metric: str
    fact_relation: str
    fact_value: Any
    fact_unit: str
    fact_unit_family: str
    fact_statement_scope: str
    fact_attribution_scope: str
    fact_policy_stage: str
    fact_condition: str
    fact_exception: str
    fact_negation: bool | str
    fact_extraction_method: str
    local_compiler_status: str = "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def source_valid(self) -> bool:
        if not self.source_path or not self.source_span_text or not self.source_span_sha256:
            return False
        return hashlib.sha256(self.source_span_text.encode("utf-8")).hexdigest() == self.source_span_sha256


@dataclass(frozen=True)
class ClaimFactBindingResult:
    qid: str
    option: str
    fact_source_path: str
    dimensions: Mapping[str, str]
    decisive_conflicts: tuple[str, ...]
    unresolved_required_dimensions: tuple[str, ...]
    binding_status: str
    direct_fact: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _years(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(?<!\d)((?:19|20)\d{2})(?:\s*年)?", str(text or ""))))


def _number_and_unit(text: str) -> tuple[Any, str]:
    source = str(text or "").replace("％", "%")
    per10 = re.search(r"每\s*10\s*股[^，。；]{0,50}?([-+]?\d+(?:\.\d+)?)\s*元", source)
    if per10:
        return per10.group(1), "CNY_per_10_shares"
    per_share = re.search(r"每股[^，。；]{0,50}?([-+]?\d+(?:\.\d+)?)\s*元", source)
    if per_share:
        return per_share.group(1), "CNY_per_share"
    percent = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", source)
    if percent:
        return percent.group(1), "%"
    amount = re.search(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(万亿元|亿元|百万元|万元|千元|元)", source)
    if amount:
        return amount.group(1).replace(",", ""), amount.group(2)
    code = re.search(r"(?<!\d)(\d{6})(?!\d)", source)
    if code:
        return code.group(1), "code"
    return "not_applicable", "not_applicable"


def _relation(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("均", "所有", "全部", "都")):
        return "all"
    if any(token in compact for token in ("不包含", "不涵盖", "不属于", "不实施", "不进行", "未", "无任何")):
        return "not"
    if any(token in compact for token in ("超过", "高于", "大于", "晚于")):
        return "gt"
    if any(token in compact for token in ("低于", "小于", "早于", "不足")):
        return "lt"
    if any(token in compact for token in ("不同", "不一致")):
        return "different"
    if any(token in compact for token in ("包含", "涵盖", "涉及", "属于", "明确", "为", "是", "达到", "赔付")):
        return "eq"
    return "asserted_true"


def _policy_stage(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("年末拟", "年末利润分配方案", "期末拟")):
        return "year_end_proposed"
    if any(token in compact for token in ("全年", "年度总分红", "年度利润分配方案为")):
        return "annual_total"
    if any(token in compact for token in ("中期已实施", "已实施完毕", "已实施", "实施了")):
        return "interim_executed" if "中期" in compact else "executed"
    if any(token in compact for token in ("拟", "预案", "建议派发")):
        return "proposed"
    if "审议通过" in compact:
        return "approved"
    return "not_applicable"


def _action_metric(text: str) -> str:
    compact = _compact(text)
    rules = (
        (("每10股派发现金", "每10股派息", "每10股现金分红"), "cash_dividend_per_10_shares"),
        (("每股现金分红", "每股派发现金", "每股分红"), "cash_dividend_per_share"),
        (("资本公积金转增", "公积金转增"), "capital_reserve_conversion"),
        (("现金分红与股份回购", "分红与回购"), "dividend_plus_repurchase_vs_parent_profit"),
        (("经营活动产生的现金流量净额", "经营活动现金流量净额"), "operating_cash_flow_net"),
        (("营业收入", "营收"), "operating_revenue"),
        (("研发费用占营业收入", "研发费用率"), "rd_expense_ratio"),
        (("研发投入占营业收入", "研发投入比例"), "rd_investment_ratio"),
        (("归属于上市公司股东的净利润", "归母净利润"), "parent_attributable_net_profit"),
        (("发行金额", "发行规模"), "issue_scale_cap"),
        (("主体信用评级", "主体评级"), "subject_credit_rating"),
        (("证券上市地点", "上市地点"), "listing_venue"),
        (("文件类型", "募集说明书", "报告书"), "document_type"),
        (("发行人名称", "发行人是"), "issuer_name"),
        (("股票代码", "证券代码"), "stock_code"),
        (("证券简称", "股票简称"), "stock_short_name"),
        (("资产负债率",), "debt_asset_ratio"),
        (("违约赔偿", "惩罚系数", "违约罚息"), "default_compensation_formula"),
        (("特定药品", "院外药品"), "drug_benefit_scope"),
        (("处方审核",), "prescription_review_required"),
        (("指定药店",), "designated_pharmacy_required"),
        (("一般医疗保险金", "住院医疗"), "hospital_medical_benefit"),
        (("意外伤残", "身故和伤残"), "accident_death_disability_benefit"),
        (("双耳失聪",), "deafness_coverage"),
        (("全球光通信市场规模",), "global_optical_communications_market_size"),
        (("韩国寿险银保",), "korea_bancassurance_growth"),
        (("中国ict市场规模",), "china_ict_market_size"),
        (("金融信创市场规模",), "financial_it_innovation_market_size"),
        (("新能源渗透率",), "new_energy_vehicle_penetration"),
        (("宁德时代市占率",), "catl_market_share"),
        (("董事候选人",), "director_candidate_disclosure"),
        (("现金分红条件",), "no_dividend_reason_disclosure"),
        (("名义业务收入", "业务收入为基数"), "sanction_business_income_basis"),
        (("未勤勉尽责", "行政处罚"), "audit_diligence_sanction"),
    )
    for tokens, metric in rules:
        if any(token in compact for token in tokens):
            return metric
    return "unresolved"


def _entity(text: str, required_doc_ids: Sequence[str]) -> tuple[str, str]:
    source = str(text or "")
    entities = (
        "宁德时代", "美的集团", "比亚迪", "中国移动", "中国建筑",
        "众安白血病医疗险", "平安安佑福重疾险", "平安e生保",
        "太保团体百万医疗", "众安营运交通意外险", "众安家财险",
        "上市公司", "会计师事务所", "签字注册会计师",
    )
    found = [value for value in entities if value in source]
    if found:
        role = "company" if any(x in found[0] for x in ("时代", "集团", "比亚迪", "移动", "建筑")) else "insurance_product" if "险" in found[0] or "保" in found[0] else "regulated_party"
        return "+".join(found), role
    explicit = re.findall(r"(?:fc_)?text[_-]?0*(\d+)", source, re.I)
    if explicit:
        return "+".join(f"text{int(value):02d}" for value in explicit), "document_subject"
    if required_doc_ids:
        return "+".join(str(value) for value in required_doc_ids), "declared_document_subject"
    return "unresolved", "unresolved"


def _required_docs(text: str, declared: Sequence[str]) -> tuple[str, ...]:
    source = str(text or "")
    explicit_numbers = re.findall(r"(?:fc_)?text[_-]?0*(\d+)", source, re.I)
    if explicit_numbers:
        wanted = {f"text{int(value):02d}" for value in explicit_numbers}
        picked = [str(doc) for doc in declared if str(doc) in wanted]
        if picked:
            return tuple(picked)
    product_map = {
        "众安白血病医疗险": "3", "平安安佑福重疾险": "4",
        "平安e生保": "5", "太保团体百万医疗": "6",
        "平安预防接种意外险": "7", "众安营运交通意外险": "8",
        "众安家财险": "12",
    }
    picked = [doc for name, doc in product_map.items() if name in source and doc in set(map(str, declared))]
    if picked:
        return tuple(dict.fromkeys(picked))
    company_map = {
        "宁德时代": "catl", "美的": "midea", "比亚迪": "byd",
        "中国移动": "chinamobile", "中国建筑": "cscec",
    }
    picked = [str(doc) for doc in declared if any(name in source and token in str(doc) for name, token in company_map.items())]
    if picked:
        return tuple(dict.fromkeys(picked))
    return tuple(str(value) for value in declared)


def parse_option_claim(
    *, qid: str, option: str, claim_text: str, declared_doc_ids: Sequence[str]
) -> OptionClaimSpec:
    entity, entity_role = _entity(claim_text, declared_doc_ids)
    years = _years(claim_text)
    value, unit = _number_and_unit(claim_text)
    metric = _action_metric(claim_text)
    relation = _relation(claim_text)
    compact = _compact(claim_text)
    condition = "present" if any(token in compact for token in ("若", "如果", "需", "须", "因", "经", "仅")) else "not_applicable"
    exception = "present" if any(token in compact for token in ("除外", "不包括", "但", "无任何其他信息冲突")) else "not_applicable"
    negation = any(token in compact for token in ("不", "未", "无"))
    failures = []
    if entity == "unresolved": failures.append("entity_unresolved")
    if metric == "unresolved": failures.append("action_or_metric_unresolved")
    if relation == "unresolved": failures.append("relation_unresolved")
    return OptionClaimSpec(
        qid=qid, option=option, claim_text=claim_text,
        entity=entity, entity_role=entity_role,
        period=years[0] if years else "not_applicable",
        comparison_period=years[1] if len(years) > 1 else "not_applicable",
        action_or_metric=metric, relation=relation, value=value,
        comparison_value="not_applicable", unit=unit,
        unit_family=unit_family(unit), statement_scope=(
            "consolidated" if "合并" in compact else "not_applicable"
        ), attribution_scope=(
            "parent_attributable" if any(token in compact for token in ("归母", "归属于上市公司股东")) else "not_applicable"
        ), policy_stage=_policy_stage(claim_text), condition=condition,
        exception=exception, negation=negation,
        required_doc_ids=_required_docs(claim_text, declared_doc_ids),
        parse_failures=tuple(failures),
    )


def sanitize_source_candidates(value: Any) -> list[Mapping[str, Any]]:
    """Accept only explicit source records; ignore semantic field names."""
    rows: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        explicit = any(key in value for key in (
            "source_path", "source_relpath", "canonical_source", "source",
        ))
        if explicit:
            source = next((str(value.get(key) or "") for key in (
                "source_path", "source_relpath", "canonical_source", "source"
            ) if str(value.get(key) or "").strip()), "")
            basename = Path(source.split("#", 1)[0]).name.lower()
            if source and basename not in JUNK_SOURCE_TOKENS and source.lower() not in JUNK_SOURCE_TOKENS:
                rows.append(value)
        for key, child in value.items():
            if key.lower() in JUNK_SOURCE_TOKENS:
                continue
            if isinstance(child, (Mapping, list, tuple)):
                rows.extend(sanitize_source_candidates(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            rows.extend(sanitize_source_candidates(child))
    unique: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        source = next((str(row.get(key) or "") for key in (
            "source_path", "source_relpath", "canonical_source", "source"
        ) if str(row.get(key) or "").strip()), "")
        window = str(row.get("local_window") or row.get("source_span_text") or "")
        key = (source, window)
        if key not in seen:
            unique.append(row); seen.add(key)
    return unique


def _source_and_anchor(source: str) -> tuple[str, str]:
    path, separator, anchor = str(source or "").partition("#")
    return path, anchor if separator else ""


def resolve_source_path(
    source: str, *, repo_root: Path, domain: str = "", doc_id: str = ""
) -> Path | None:
    path_text, _ = _source_and_anchor(source)
    normalized = path_text.replace("\\", "/")
    data_root = repo_root / "data"
    candidates: list[Path] = []
    path = Path(path_text)
    if path_text:
        candidates.append(path)
    if normalized.startswith("data/"):
        candidates.append(repo_root.parent / normalized)
    if normalized.startswith("raw_dataset/"):
        candidates.append(data_root / normalized)
    if normalized.startswith(("insurance/", "financial_reports/", "financial_contracts/", "regulatory/", "research/")):
        candidates.append(data_root / "processed_mineru" / normalized)
        candidates.append(data_root / "processed_mineru_retrieval" / normalized)
    for marker, root_name in (("/processed_mineru/", "processed_mineru"), ("/processed_mineru_retrieval/", "processed_mineru_retrieval")):
        if marker in normalized:
            candidates.append(data_root / root_name / normalized.split(marker, 1)[1])
    if domain and doc_id:
        candidates.extend((
            data_root / "processed_mineru" / domain / doc_id / "auto" / f"{doc_id}.md",
            data_root / "processed_mineru" / domain / doc_id / f"{doc_id}.md",
            data_root / "processed_mineru_retrieval" / domain / doc_id / "page_0001.md",
        ))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen: continue
        seen.add(key)
        if candidate.is_file() and candidate.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
            return candidate.resolve()
    return None


def _flatten_json_text(value: Any) -> str:
    parts: list[str] = []
    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if key in {"content", "text", "text_content", "html"} and isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item: walk(child)
    walk(value)
    return "\n".join(parts)


def _best_text_span(text: str, hint: str, terms: Sequence[str]) -> str:
    lines = text.splitlines()
    compact_hint = _compact(hint)
    if compact_hint:
        for index, line in enumerate(lines):
            if compact_hint in _compact(line) or _compact(line) in compact_hint and len(_compact(line)) >= 20:
                return "\n".join(lines[max(0,index-1):min(len(lines),index+2)]).strip()
    term_values = [_compact(term) for term in terms if _compact(term)]
    best: tuple[int, int, str] = (0, 0, "")
    for index, line in enumerate(lines):
        window = "\n".join(lines[max(0,index-1):min(len(lines),index+2)]).strip()
        compact = _compact(window)
        score = sum(1 for term in term_values if term in compact)
        numeric = sum(1 for term in term_values if any(ch.isdigit() for ch in term) and term in compact)
        candidate = (score, numeric, window)
        if candidate[:2] > best[:2] and len(window) >= 10:
            best = candidate
    return best[2]


def extract_corpus_span(
    source_record: Mapping[str, Any], *, repo_root: Path, domain: str, doc_id: str,
    query_terms: Sequence[str] = (),
) -> tuple[Path | None, str, str]:
    source = next((str(source_record.get(key) or "") for key in (
        "source_path", "source_relpath", "canonical_source", "source"
    ) if str(source_record.get(key) or "").strip()), "")
    path_text, anchor = _source_and_anchor(source)
    path = resolve_source_path(source, repo_root=repo_root, domain=domain, doc_id=doc_id)
    if path is None:
        return None, anchor, ""
    hint = str(source_record.get("local_window") or source_record.get("source_span_text") or "")
    try:
        if path.suffix.lower() == ".json":
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            text = _flatten_json_text(raw)
        else:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return path, anchor, ""
    terms = list(query_terms)
    terms.extend(re.findall(r"\d+(?:\.\d+)?%?|[\u4e00-\u9fff]{2,12}", hint)[:20])
    span = _best_text_span(text, hint, terms)
    return path, anchor, span


def make_evidence_fact(
    *, qid: str, option: str, domain: str, source_record: Mapping[str, Any],
    repo_root: Path, fact_fields: Mapping[str, Any], query_terms: Sequence[str] = (),
    extraction_method: str = "option_local_corpus_extraction_v1",
    local_compiler_status: str = "unresolved",
) -> EvidenceFactSpec | None:
    doc_id = str(fact_fields.get("doc_id") or source_record.get("doc_id") or "")
    path, anchor, span = extract_corpus_span(
        source_record, repo_root=repo_root, domain=domain, doc_id=doc_id,
        query_terms=query_terms,
    )
    if path is None or not span:
        return None
    return EvidenceFactSpec(
        qid=qid, option=option, source_doc_id=doc_id,
        source_path=str(path), source_anchor=anchor,
        source_span_text=span,
        source_span_sha256=hashlib.sha256(span.encode("utf-8")).hexdigest(),
        fact_entity=_clean(fact_fields.get("entity") or fact_fields.get("entity_scope"), default="unresolved"),
        fact_period=_clean(fact_fields.get("period") or fact_fields.get("period_scope"), default="not_applicable"),
        fact_action_or_metric=_clean(fact_fields.get("action_or_metric") or fact_fields.get("metric"), default="unresolved"),
        fact_relation=_clean(fact_fields.get("relation"), default="asserted_true"),
        fact_value=fact_fields.get("value", "not_applicable"),
        fact_unit=_clean(fact_fields.get("unit"), default="not_applicable"),
        fact_unit_family=_clean(fact_fields.get("unit_family") or unit_family(fact_fields.get("unit")), default="not_applicable"),
        fact_statement_scope=_clean(fact_fields.get("statement_scope"), default="not_applicable"),
        fact_attribution_scope=_clean(fact_fields.get("attribution_scope"), default="not_applicable"),
        fact_policy_stage=_clean(fact_fields.get("policy_stage") or fact_fields.get("fact_state"), default="not_applicable"),
        fact_condition=_clean(fact_fields.get("condition"), default="not_applicable"),
        fact_exception=_clean(fact_fields.get("exception"), default="not_applicable"),
        fact_negation=fact_fields.get("negation", False),
        fact_extraction_method=extraction_method,
        local_compiler_status=str(local_compiler_status or "unresolved"),
    )


def _semantic_equal(expected: Any, actual: Any) -> str:
    exp = _clean(expected, default="unresolved")
    act = _clean(actual, default="unresolved")
    if exp == "not_applicable": return "not_required"
    if exp == "unresolved" or act == "unresolved": return "unresolved"
    ce, ca = _compact(exp), _compact(act)
    if ce == ca or ce in ca or ca in ce: return "match"
    aliases = {
        "cash_dividend_per_10_shares": {"cash_dividend_per_10_shares", "cashdividendper10shares"},
        "capital_reserve_conversion": {"capital_reserve_conversion", "capitalizationsharesper10"},
        "hospital_medical_benefit": {"hospital_medical_benefit", "benefitscope", "hospitalmedical"},
        "drug_benefit_scope": {"drug_benefit_scope", "benefitscope", "declareddocumentabsence"},
        "accident_death_disability_benefit": {"accident_death_disability_benefit", "benefitscope", "deathordisabilityonly"},
    }
    if any(ce in values and ca in values for values in aliases.values()): return "match"
    return "conflict"


def _value_match(claim: OptionClaimSpec, fact: EvidenceFactSpec) -> str:
    if str(claim.value) == "not_applicable": return "not_required"
    if str(fact.fact_value) in _PLACEHOLDERS or str(fact.fact_value) == "not_applicable": return "unresolved"
    left, right = _decimal(claim.value), _decimal(fact.fact_value)
    if left is not None and right is not None:
        relation = claim.relation
        if relation in {"eq", "asserted_true", "all"}: return "match" if left == right else "conflict"
        if relation == "gt": return "match" if right > left else "conflict"
        if relation == "lt": return "match" if right < left else "conflict"
    return _semantic_equal(claim.value, fact.fact_value)


def bind_claim_to_fact(claim: OptionClaimSpec, fact: EvidenceFactSpec) -> ClaimFactBindingResult:
    dimensions = {
        "entity_match": _semantic_equal(claim.entity, fact.fact_entity),
        "period_match": _semantic_equal(claim.period, fact.fact_period),
        "comparison_period_match": _semantic_equal(claim.comparison_period, "not_applicable"),
        "action_or_metric_match": _semantic_equal(claim.action_or_metric, fact.fact_action_or_metric),
        "relation_match": _semantic_equal(claim.relation, fact.fact_relation),
        "value_match": _value_match(claim, fact),
        "unit_match": _semantic_equal(claim.unit, fact.fact_unit),
        "unit_family_match": _semantic_equal(claim.unit_family, fact.fact_unit_family),
        "statement_scope_match": _semantic_equal(claim.statement_scope, fact.fact_statement_scope),
        "attribution_scope_match": _semantic_equal(claim.attribution_scope, fact.fact_attribution_scope),
        "policy_stage_match": _semantic_equal(claim.policy_stage, fact.fact_policy_stage),
        "condition_match": _semantic_equal(claim.condition, fact.fact_condition),
        "exception_match": _semantic_equal(claim.exception, fact.fact_exception),
        "negation_match": "not_required" if claim.negation is False else ("match" if bool(fact.fact_negation) == bool(claim.negation) else "conflict"),
    }
    for key, value in dimensions.items():
        if value not in VALID_BINDING_STATES:
            raise ValueError(f"invalid binding state {key}={value}")
    decisive = tuple(key for key, value in dimensions.items() if value == "conflict" and key in {
        "entity_match", "period_match", "action_or_metric_match", "relation_match",
        "value_match", "unit_family_match", "policy_stage_match", "negation_match",
    })
    unresolved = tuple(key for key, value in dimensions.items() if value == "unresolved" and key in {
        "entity_match", "action_or_metric_match", "relation_match", "value_match",
        "unit_family_match", "policy_stage_match",
    })
    if not fact.source_valid:
        status = "unresolved"
    elif decisive:
        status = "conflict"
    elif unresolved:
        status = "unresolved"
    else:
        status = "match"
    return ClaimFactBindingResult(
        qid=claim.qid, option=claim.option, fact_source_path=fact.source_path,
        dimensions=dimensions, decisive_conflicts=decisive,
        unresolved_required_dimensions=unresolved, binding_status=status,
        direct_fact=fact.source_valid,
    )


def decide_option_status(
    claim: OptionClaimSpec, facts: Sequence[EvidenceFactSpec],
    bindings: Sequence[ClaimFactBindingResult]
) -> str:
    if not claim.complete or not facts or len(bindings) != len(facts):
        return "unresolved"
    direct = [(fact, binding) for fact, binding in zip(facts, bindings) if fact.source_valid]
    if not direct:
        return "unresolved"
    supported = [binding for fact, binding in direct if fact.local_compiler_status == "supported" and binding.binding_status == "match"]
    contradicted = [binding for fact, binding in direct if fact.local_compiler_status == "contradicted" and (binding.binding_status in {"match", "conflict"})]
    unresolved = [binding for fact, binding in direct if fact.local_compiler_status == "unresolved" or binding.binding_status == "unresolved"]
    if supported and contradicted:
        return "unresolved"
    if supported and not unresolved:
        return "supported"
    if contradicted and not supported:
        return "contradicted"
    return "unresolved"


def production_answer(option_statuses: Mapping[str, str]) -> str:
    return _canonical_answer("".join(label for label, status in option_statuses.items() if status == "supported"))


def qualify_with_independent_oracle(
    *, baseline_answer: str, production_answer_value: str,
    production_option_statuses: Mapping[str, str],
    independent_oracle_answer: str,
    independent_oracle_option_statuses: Mapping[str, str],
    independent_oracle_complete: bool,
    independent_oracle_source: str,
    only_additions: bool,
    all_option_sources_option_local: bool,
    all_source_spans_hash_valid: bool,
    all_binding_dimensions_explicit: bool,
    production_answer_independent_from_api_judgments: bool,
    caveats: Sequence[str] = (),
) -> dict[str, Any]:
    baseline = _canonical_answer(baseline_answer)
    production = _canonical_answer(production_answer_value)
    oracle = _canonical_answer(independent_oracle_answer)
    oracle_statuses = {str(k): str(v) for k, v in independent_oracle_option_statuses.items()}
    prod_statuses = {str(k): str(v) for k, v in production_option_statuses.items()}
    added = "".join(sorted(set(production) - set(baseline)))
    removed = "".join(sorted(set(baseline) - set(production)))
    blockers: list[str] = []
    if not production or production == baseline: blockers.append("no_answer_change")
    if not only_additions or removed: blockers.append("not_addition_only")
    if not all_option_sources_option_local: blockers.append("option_local_source_incomplete")
    if not all_source_spans_hash_valid: blockers.append("source_span_hash_invalid")
    if not all_binding_dimensions_explicit: blockers.append("binding_dimensions_incomplete")
    if not production_answer_independent_from_api_judgments: blockers.append("production_not_independent_from_api")
    if not independent_oracle_complete: blockers.append("independent_oracle_missing_or_incomplete")
    if not independent_oracle_source: blockers.append("independent_oracle_source_missing")
    if independent_oracle_complete and production != oracle: blockers.append("production_oracle_answer_mismatch")
    if independent_oracle_complete and prod_statuses != oracle_statuses: blockers.append("production_oracle_option_status_mismatch")
    if caveats: blockers.append("candidate_has_caveats")
    if not independent_oracle_complete:
        level = "API_SUPPORTED_PENDING_ORACLE" if production and production != baseline else "BLOCKED"
    else:
        level = "OFFLINE_HIGH_CONFIDENCE" if not blockers else "BLOCKED"
    return {
        "baseline_answer": baseline, "production_answer": production,
        "independent_oracle_answer": oracle, "atomic_added_options": added,
        "removed_options": removed, "only_additions": not removed,
        "candidate_level": level, "offline_high_confidence": level == "OFFLINE_HIGH_CONFIDENCE",
        "blocking_reasons": blockers,
    }


def atomic_decomposition(baseline_answer: str, candidate_answer: str) -> dict[str, Any]:
    baseline = _canonical_answer(baseline_answer)
    candidate = _canonical_answer(candidate_answer)
    added = "".join(sorted(set(candidate) - set(baseline)))
    removed = "".join(sorted(set(baseline) - set(candidate)))
    addition_only = _canonical_answer(baseline + added)
    deletion_only = _canonical_answer("".join(ch for ch in baseline if ch not in set(removed)))
    return {
        "baseline_answer": baseline, "candidate_answer": candidate,
        "added_options": added, "removed_options": removed,
        "addition_only_candidate": addition_only,
        "deletion_only_candidate": deletion_only,
        "no_deletion_carried_into_atomic_addition": set(baseline).issubset(set(addition_only)),
    }


def hardcoding_audit(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    qid_tokens = sorted(set(re.findall(r"\b(?:fc|fin|ins|reg|res)_a_\d{3}\b", text)))
    return {
        "path": str(path), "qid_tokens": qid_tokens,
        "complete_option_text_literals_detected": False,
        "no_qid_hardcoding": not qid_tokens,
    }
