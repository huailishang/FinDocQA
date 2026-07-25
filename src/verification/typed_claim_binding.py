"""Typed option-claim certification against one source-local evidence window.

Source readability and semantic certification are separate. A readable passage is
only a candidate. Support or contradiction requires typed binding of document
identity, named entity/role, date or period, metric object, metric value/unit,
rank, scenario, comparator and polarity in one local window.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

REL_SUPPORTED = "supported_by_exact_passage"
REL_CONTRADICTED = "contradicted_by_exact_passage"
REL_AMBIGUOUS = "ambiguous_or_insufficient"
REL_MISSING = "missing_evidence_ref"

_SPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；;])\s*|\n{2,}")
_DOC_TEXT_RE = re.compile(r"(?:fc_)?text[_-]?0*(\d+)", re.IGNORECASE)
_DATE_YMD_RE = re.compile(r"(?<!\d)(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*(\d{1,2})\s*日?(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?(?!\d)")
_DURATION_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(个月|个工作日|工作日|年|天)(?!\d)")
_METRIC_VALUE_RE = re.compile(
    r"(?<![\d.])(?P<number>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|％|亿元|万元|元|倍|个百分点|GWh|GW|万户|亿户|港元/股|元/股|港元)(?![\d.])",
    re.IGNORECASE,
)

_CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_GENERIC_ENTITIES = ("金融机构", "发行人", "客户", "受益所有人", "空壳银行", "标的公司")
_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "中国移动": ("中国移动", "chinamobile", "annual_chinamobile"),
    "宁德时代": ("宁德时代", "catl", "annual_catl"),
    "比亚迪": ("比亚迪", "byd", "annual_byd"),
    "芯原股份": ("芯原股份", "芯原半导体", "芯原"),
    "天阳科技": ("天阳科技", "天阳"),
    "神州信息": ("神州信息",),
    "宇信科技": ("宇信科技", "宇信"),
    "长亮科技": ("长亮科技", "长亮"),
    "力诺投资": ("力诺投资", "力诺"),
    "欧盟银保渠道": ("欧盟银保渠道", "银保渠道"),
}

_METRIC_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stock_short_name", ("股票简称", "证券简称")),
    ("stock_code", ("股票代码", "证券代码")),
    ("initial_conversion_price_floor", ("初始转股价格不低于", "初始转股价不低于", "转股价格不低于")),
    ("initial_conversion_price", ("初始转股价格", "初始转股价")),
    ("subject_credit_rating", ("主体信用评级", "主体评级", "主体信用等级")),
    ("bond_credit_rating", ("债项信用评级", "债券信用评级", "可转债信用等级", "债券信用等级")),
    ("issuer_category", ("发行人属于证券公司", "发行人为证券公司", "证券公司类别")),
    ("issue_scale_cap", ("发行金额上限", "发行规模设定了上限", "发行规模上限", "本期发行规模")),
    ("conditional_redemption_clause", ("有条件赎回条款", "有条件赎回", "赎回条款")),
    ("default_or_compensation_clause", ("违约或补偿", "违约条款", "补偿条款", "违约情形")),
    ("downward_revision_clause", ("转股价格向下修正", "向下修正条款")),
    ("specific_numeric_disclosure", ("具体资产负债率预测值", "具体预测值", "具体数值")),
    ("overdue_interest_base", ("违约利息计算基数", "逾期利息计算基数", "逾期利息具体计算方式", "违约利息")),
    ("r_and_d_revenue_ratio", ("研发费用占营业收入比重", "研发投入总额占营业收入的比重", "研发投入占营业收入", "研发费用占比")),
    ("r_and_d_growth", ("研发费用同比增长", "研发费用比上年增长", "研发投入同比增长")),
    ("net_profit_growth", ("净利润同比增长", "净利润比上年增长", "净利润增长")),
    ("net_profit_amount", ("净利润盈利", "净利润亏损", "归母净利润达", "归母净利润为", "净利润达", "净利润为", "净利润")),
    ("revenue_growth", ("营收同比增长", "营业收入同比增长", "收入同比增长", "营收增长")),
    ("overseas_income_ratio", ("境外收入占比", "境外营业收入占比", "境外收入比例")),
    ("existing_customer_deadline", ("全部存量客户", "存量全部客户", "存量客户的受益所有人识别核实")),
    ("non_major_difference_report", ("非重大差异",)),
    ("identity_record_retention", ("客户身份资料", "交易记录保存")),
    ("shell_bank_relationship", ("空壳银行", "代理行关系")),
    ("investigation_retention", ("反洗钱调查", "最低保存期限届满")),
    ("ip_licensing_market_share", ("IP授权业务市场份额", "IP 授权业务市场份额", "IP授权业务市场占有率", "IP 授权业务市场占有率")),
    ("original_premium_market_share", ("原保费全球市场份额", "原保费市场份额")),
    ("market_share", ("市占率", "市场份额", "市场占有率")),
    ("financial_xinchuang_market_size", ("金融信创市场规模", "信创市场规模")),
    ("debt_asset_ratio", ("资产负债率",)),
    ("net_profit_margin", ("净利润率",)),
    ("dividend_profit_ratio", ("现金分红占净利润比例", "分红占净利润比例", "派息率")),
)

_SCENARIO_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("post_full_conversion", ("全部转股后", "可转债全部转股后", "全部可转债转股后")),
    ("post_conversion", ("转股后",)),
    ("pre_conversion", ("转股前", "未转股前")),
    ("post_reorganization", ("本次重组完成后", "重组完成后")),
    ("post_issuance", ("本次发行完成后", "发行完成后")),
    ("report_period_end", ("报告期末", "报告期各期末")),
    ("implementation_before", ("实施前",)),
    ("implementation_after", ("实施后",)),
)

_RANK_SCOPE_ALIASES = {
    "中国大陆": "china_mainland",
    "大陆": "china_mainland",
    "国内": "domestic",
    "全球": "global",
    "海外": "overseas",
}


@dataclass(frozen=True)
class TypedClaimAtoms:
    document_identity: list[str]
    structural_numbers: list[str]
    entity_or_subject: list[str]
    entity_roles: list[dict[str, str]]
    date_or_period: list[str]
    duration: list[dict[str, Any]]
    metric_object: list[str]
    metric_value: list[dict[str, Any]]
    rank_scope: list[str]
    rank_value: list[int]
    scenario_or_condition: list[str]
    trajectory: list[dict[str, Any]]
    comparator: str
    polarity_or_scope: list[str]

    @property
    def document_or_source_identity(self) -> list[str]:
        return self.document_identity

    @property
    def time_or_period(self) -> list[str]:
        return self.date_or_period

    @property
    def metric_or_relation(self) -> list[str]:
        return self.metric_object

    @property
    def value(self) -> list[dict[str, Any]]:
        return self.metric_value

    @property
    def unit(self) -> list[str]:
        return _dedupe([str(item.get("unit") or "") for item in self.metric_value if item.get("unit")])

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "document_or_source_identity": self.document_or_source_identity,
                "time_or_period": self.time_or_period,
                "metric_or_relation": self.metric_or_relation,
                "value": self.value,
                "unit": self.unit,
            }
        )
        return payload


def _compact(text: Any) -> str:
    return _SPACE_RE.sub("", str(text or "")).replace("％", "%").lower()


def _dedupe(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _chinese_ordinal(raw: str) -> int | None:
    text = raw.strip().replace("第", "")
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1 if not left else None)
        ones = _CHINESE_DIGITS.get(right, 0 if not right else None)
        if tens is not None and ones is not None:
            return tens * 10 + ones
    if len(text) == 1 and text in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[text]
    return None


def _number_token(number: str, unit: str | None) -> dict[str, Any]:
    return {
        "raw": f"{number}{unit or ''}",
        "number": float(number.replace(",", "")),
        "unit": (unit or "").replace("％", "%"),
    }


def _mask_structural_numbers(text: str) -> tuple[str, list[str]]:
    masked = text
    structural: list[str] = [match.group(0) for match in _DOC_TEXT_RE.finditer(masked)]
    masked = _DOC_TEXT_RE.sub(" DOC_ID ", masked)
    for match in _DATE_YMD_RE.finditer(masked):
        structural.extend([match.group(1), match.group(2), match.group(3)])
    masked = _DATE_YMD_RE.sub(" DATE_YMD ", masked)
    for match in _YEAR_RE.finditer(masked):
        structural.append(match.group(1))
    masked = _YEAR_RE.sub(" YEAR ", masked)
    return masked, _dedupe(structural)


def _extract_document_identities(text: str, question_doc_ids: Sequence[str] | None = None) -> list[str]:
    identities = [f"text{int(match.group(1)):02d}" for match in _DOC_TEXT_RE.finditer(text)]
    docs = [str(item) for item in (question_doc_ids or [])]
    if "第一份文档" in text and docs:
        identities.append(docs[0])
    if "第二份文档" in text and len(docs) > 1:
        identities.append(docs[1])
    return _dedupe(identities)


def _extract_dates(text: str) -> list[str]:
    dates: list[str] = []
    covered: list[tuple[int, int]] = []
    for match in _DATE_YMD_RE.finditer(text):
        dates.append(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")
        covered.append(match.span())
    for match in _YEAR_RE.finditer(text):
        if any(start <= match.start() < end for start, end in covered):
            continue
        dates.append(match.group(1))
    for match in re.finditer(r"(20\d{2})\s*年末", text):
        dates.append(f"{match.group(1)}-year-end")
    return _dedupe(dates)


def _extract_durations(text: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for match in _DURATION_RE.finditer(text):
        number = match.group(1)
        if re.fullmatch(r"(?:19|20)\d{2}", number) and match.group(2) == "年":
            continue
        values.append(_number_token(number, match.group(2)))
    for raw, value in {"十年": 10.0, "六个月": 6.0, "两年": 2.0, "一年": 1.0}.items():
        if raw in text:
            values.append({"raw": raw, "number": value, "unit": "个月" if "个月" in raw else "年"})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for item in values:
        key = (float(item["number"]), str(item["unit"]))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _extract_metric_values(text: str) -> list[dict[str, Any]]:
    masked, _ = _mask_structural_numbers(text)
    masked = _DURATION_RE.sub(" DURATION ", masked)
    values = [_number_token(match.group("number"), match.group("unit")) for match in _METRIC_VALUE_RE.finditer(masked)]
    unique: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for item in values:
        key = (float(item["number"]), str(item["unit"]))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _entity_aliases(entity: str) -> tuple[str, ...]:
    if entity in _ENTITY_ALIASES:
        return _ENTITY_ALIASES[entity]
    aliases = [entity]
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份", "科技", "信息", "投资", "集团", "半导体"):
        if entity.endswith(suffix) and len(entity) - len(suffix) >= 2:
            aliases.append(entity[: -len(suffix)])
    return tuple(_dedupe(aliases))


def _extract_entities(text: str) -> tuple[list[str], list[dict[str, str]]]:
    entities: list[str] = [entity for entity in _GENERIC_ENTITIES if entity in text]
    roles: list[dict[str, str]] = []
    for match in re.finditer(r"(标的公司(?:间接)?控股股东)([\u4e00-\u9fffA-Za-z0-9·]{2,16}?)(?=在|的|，|。|\s|$)", text):
        role, entity = match.group(1), match.group(2)
        entities.append(entity)
        roles.append({"role": role, "entity": entity})
    patterns = (
        r"([\u4e00-\u9fffA-Za-z0-9·]{2,18}(?:科技|信息|投资|移动|半导体|股份|集团|银行|保险|证券))(?=\s*(?:19|20)\d{2}|截至|在|的|，|。|\s)",
        r"([\u4e00-\u9fff]{2,16}(?:银保渠道|销售渠道))",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group(1).strip()
            if not candidate.startswith(("文档", "其中一份文档")) and not any(token in candidate for token in ("披露的", "控股股东", "间接控股股东")):
                entities.append(candidate)
    for known in _ENTITY_ALIASES:
        if known in text:
            entities.append(known)
    role_unique: list[dict[str, str]] = []
    seen_roles: set[tuple[str, str]] = set()
    for item in roles:
        key = (item["role"], item["entity"])
        if key not in seen_roles:
            seen_roles.add(key)
            role_unique.append(item)
    return _dedupe(entities), role_unique


def _detect_metric_objects(text: str) -> list[str]:
    compact = _compact(text)
    metrics: list[str] = []
    for name, patterns in _METRIC_PATTERNS:
        if any(_compact(pattern) in compact for pattern in patterns):
            metrics.append(name)
    if "研发费用" in compact and any(token in compact for token in ("占营业收入比重", "占营业收入的比重")):
        metrics.append("r_and_d_revenue_ratio")
    if "net_profit_growth" in metrics and "revenue_growth" in metrics:
        metrics.remove("revenue_growth")
    if "net_profit_growth" in metrics and "net_profit_amount" in metrics:
        metrics.remove("net_profit_amount")
    if "r_and_d_revenue_ratio" in metrics and "r_and_d_growth" in metrics:
        metrics.remove("r_and_d_growth")
    return _dedupe(metrics)


def _detect_comparator(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("不少于", "至少", "不低于")):
        return "ge"
    if any(token in compact for token in ("不超过", "至多", "不高于")):
        return "le"
    if any(token in compact for token in ("超过", "高于", "大于")):
        return "gt"
    if any(token in compact for token in ("低于", "少于", "小于")):
        return "lt"
    if "下降至" in compact or "降低至" in compact:
        return "decrease_to"
    if "提升至" in compact or "增长至" in compact:
        return "increase_to"
    if "约为" in compact or "约" in compact or "接近" in compact:
        return "approx"
    if "包含" in compact:
        return "contains"
    if "立即" in compact:
        return "immediate"
    if "只要" in compact and "即可" in compact:
        return "conditional_permission"
    return "eq" if _extract_metric_values(text) or _extract_durations(text) else "statement"


def _detect_polarity(text: str) -> list[str]:
    compact = _compact(text)
    return _dedupe([token for token in ("不得", "无需", "不能", "禁止", "未", "无", "不", "立即删除", "全部", "仅", "只要", "即可") if token in compact])


def _extract_rank(text: str) -> tuple[list[str], list[int]]:
    pairs: list[tuple[str, int]] = []
    pattern = re.compile(r"(中国大陆|大陆|国内|全球|海外)\s*(?:位列|排名)?\s*第?([一二三四五六七八九十\d]+)")
    for match in pattern.finditer(text):
        value = _chinese_ordinal(match.group(2))
        if value is not None:
            pairs.append((_RANK_SCOPE_ALIASES[match.group(1)], value))
    return _dedupe([scope for scope, _ in pairs]), list(dict.fromkeys(value for _, value in pairs))


def _extract_rank_pairs(text: str) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    pattern = re.compile(r"(中国大陆|大陆|国内|全球|海外)\s*(?:位列|排名)?\s*第?([一二三四五六七八九十\d]+)")
    for match in pattern.finditer(text):
        value = _chinese_ordinal(match.group(2))
        if value is not None:
            pairs.append((_RANK_SCOPE_ALIASES[match.group(1)], value))
    return list(dict.fromkeys(pairs))


def _extract_scenarios(text: str) -> list[str]:
    compact = _compact(text)
    scenarios: list[str] = []
    for name, aliases in _SCENARIO_PATTERNS:
        if any(_compact(alias) in compact for alias in aliases):
            scenarios.append(name)
    for match in re.finditer(r"(20\d{2})\s*年末", text):
        scenarios.append(f"year_end:{match.group(1)}")
    return _dedupe(scenarios)


def _extract_trajectory(text: str) -> list[dict[str, Any]]:
    normalized = text.replace("％", "%")
    trajectories: list[dict[str, Any]] = []
    range_pattern = re.compile(
        r"(?P<start_year>(?:19|20)\d{2})\s*年至(?P<end_year>(?:19|20)\d{2})\s*年[^。；]{0,100}?"
        r"从\s*(?P<start>\d+(?:\.\d+)?)\s*%\s*(?P<direction>快速提升|提升|增长|上升|下降|降低)至\s*(?P<end>\d+(?:\.\d+)?)\s*%"
    )
    for match in range_pattern.finditer(normalized):
        trajectories.append(
            {
                "start_period": match.group("start_year"),
                "end_period": match.group("end_year"),
                "start_value": float(match.group("start")),
                "end_value": float(match.group("end")),
                "unit": "%",
                "direction": "up" if match.group("direction") in {"快速提升", "提升", "增长", "上升"} else "down",
            }
        )
    simple_pattern = re.compile(
        r"(?:(?P<start_year>(?:19|20)\d{2})\s*年(?:的)?\s*)?"
        r"(?P<start>\d+(?:\.\d+)?)\s*%\s*"
        r"(?P<direction>快速提升|提升|增长|上升|下降|降低)"
        r"(?:至|到)?\s*(?P<end>\d+(?:\.\d+)?)?\s*%?"
    )
    for match in simple_pattern.finditer(normalized):
        item: dict[str, Any] = {
            "start_period": match.group("start_year") or "",
            "start_value": float(match.group("start")),
            "unit": "%",
            "direction": "up" if match.group("direction") in {"快速提升", "提升", "增长", "上升"} else "down",
        }
        if match.group("end"):
            item["end_value"] = float(match.group("end"))
        trajectories.append(item)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in trajectories:
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def extract_typed_claim_atoms(option_text: str, *, question_doc_ids: Sequence[str] | None = None) -> TypedClaimAtoms:
    entities, roles = _extract_entities(option_text)
    masked, structural = _mask_structural_numbers(option_text)
    rank_scope, rank_value = _extract_rank(option_text)
    return TypedClaimAtoms(
        document_identity=_extract_document_identities(option_text, question_doc_ids),
        structural_numbers=structural,
        entity_or_subject=entities,
        entity_roles=roles,
        date_or_period=_extract_dates(option_text),
        duration=_extract_durations(option_text),
        metric_object=_detect_metric_objects(option_text),
        metric_value=_extract_metric_values(masked),
        rank_scope=rank_scope,
        rank_value=rank_value,
        scenario_or_condition=_extract_scenarios(option_text),
        trajectory=_extract_trajectory(option_text),
        comparator=_detect_comparator(option_text),
        polarity_or_scope=_detect_polarity(option_text),
    )


def _paragraph_windows(text: str) -> list[str]:
    raw_parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(str(text or "")) if part.strip()]
    atomic: list[str] = []
    for part in raw_parts:
        if len(part) <= 1400:
            atomic.append(part)
            continue
        sentences = [item.strip() for item in re.split(r"(?<=[。！？；;])", part) if item.strip()]
        for index, sentence in enumerate(sentences):
            combined = sentence
            if index + 1 < len(sentences) and len(combined) < 700:
                combined += sentences[index + 1]
            atomic.append(combined[:1400])

    # Keep the atomic sentence plus bounded backward windows.  Financial prose
    # often introduces a company in a short heading and states the metric a few
    # sentences later.  We never look forward from the fact sentence, and the
    # attribution certifier additionally requires the named subject to precede
    # the exact metric value.
    windows: list[str] = list(atomic)
    for index, _part in enumerate(atomic):
        for width in range(2, 7):
            start = index - width + 1
            if start < 0:
                break
            backward = " ".join(atomic[start : index + 1]).strip()
            if len(backward) <= 1400:
                windows.append(backward)
    return _dedupe(windows)


def source_windows(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    windows: list[dict[str, str]] = []
    source_resolution = payload.get("source_resolution")
    if isinstance(source_resolution, Sequence) and not isinstance(source_resolution, (str, bytes, bytearray)):
        for item in source_resolution:
            if not isinstance(item, Mapping) or item.get("read_status") != "read":
                continue
            source = str(item.get("canonical_ref") or item.get("page_or_lineage") or item.get("resolved_path") or "")
            for local in _paragraph_windows(str(item.get("bounded_context") or "")):
                windows.append({"canonical_source": source, "local_window": local})
    if windows:
        return windows
    passages = payload.get("full_passage_or_bounded_context")
    refs = payload.get("resolved_evidence_refs") or payload.get("evidence_refs") or []
    passage_list = [passages] if isinstance(passages, str) else list(passages or [])
    ref_list = [refs] if isinstance(refs, str) else list(refs or [])
    for index, passage in enumerate(passage_list):
        source = str(ref_list[index] if index < len(ref_list) else "")
        for local in _paragraph_windows(str(passage or "")):
            windows.append({"canonical_source": source, "local_window": local})
    return windows


def _source_identity_match(identities: Sequence[str], source: str) -> bool:
    if not identities:
        return True
    compact_source = _compact(source)
    for identity in identities:
        aliases = {_compact(identity)}
        match = _DOC_TEXT_RE.search(identity)
        if match:
            aliases.add(f"text{int(match.group(1)):02d}")
            aliases.add(f"text{int(match.group(1))}")
        if any(alias and alias in compact_source for alias in aliases):
            return True
    return False


def _entity_match(atoms: TypedClaimAtoms, source: str, window: str) -> tuple[bool, list[str], list[str]]:
    named = [entity for entity in atoms.entity_or_subject if entity not in _GENERIC_ENTITIES]
    if not named:
        return True, [], []
    compact = _compact(source + " " + window)
    matched: list[str] = []
    missing: list[str] = []
    for entity in named:
        if any(_compact(alias) in compact for alias in _entity_aliases(entity)):
            matched.append(entity)
        else:
            missing.append(entity)
    return not missing, matched, missing


def _role_match(atoms: TypedClaimAtoms, window: str) -> tuple[bool, list[str]]:
    if not atoms.entity_roles:
        return True, []
    compact = _compact(window)
    missing: list[str] = []
    for item in atoms.entity_roles:
        role = str(item.get("role") or "")
        entity = str(item.get("entity") or "")
        if _compact(role + entity) not in compact:
            missing.append(f"{role}:{entity}")
    return not missing, missing


def _date_match(periods: Sequence[str], source: str, window: str) -> tuple[bool, list[str]]:
    if not periods:
        return True, []
    local_periods = set(_extract_dates(window))
    source_compact = _compact(source)
    missing: list[str] = []
    for period in periods:
        if period in local_periods:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", period) and period in source_compact:
            continue
        if period.endswith("-year-end") and period.split("-")[0] in local_periods and "年末" in window:
            continue
        missing.append(period)
    return not missing, missing


def _scenario_match(required: Sequence[str], window: str) -> tuple[bool, list[str]]:
    if not required:
        return True, []
    actual = set(_extract_scenarios(window))
    missing = [item for item in required if item not in actual]
    return not missing, missing


def _metric_match(option_metrics: Sequence[str], window: str) -> tuple[bool, list[str], list[str]]:
    if not option_metrics:
        return True, [], []
    actual = set(_detect_metric_objects(window))
    matched: list[str] = []
    missing: list[str] = []
    for metric in option_metrics:
        if metric in actual:
            matched.append(metric)
            continue
        if metric == "specific_numeric_disclosure" and _extract_metric_values(window):
            matched.append(metric)
            continue
        if metric == "ip_licensing_market_share" and "market_share" in actual and "ip授权业务" in _compact(window):
            matched.append(metric)
            continue
        if metric == "original_premium_market_share" and "market_share" in actual and "原保费" in _compact(window) and "银保渠道" in _compact(window):
            matched.append(metric)
            continue
        missing.append(metric)
    return not missing, matched, missing


def _numbers_equal(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _compare_number(expected: float, actual: float, comparator: str) -> bool:
    if comparator in {"eq", "approx", "statement", "contains", "immediate", "conditional_permission", "increase_to", "decrease_to"}:
        return _numbers_equal(expected, actual)
    if comparator == "gt":
        return actual > expected
    if comparator == "ge":
        return actual >= expected
    if comparator == "lt":
        return actual < expected
    if comparator == "le":
        return actual <= expected
    return False


def _metric_specific_values(metric: str, window: str, atoms: TypedClaimAtoms) -> list[dict[str, Any]]:
    if metric == "financial_xinchuang_market_size":
        values: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?:金融)?信创市场规模[^。；]{0,40}?"
            r"(?:预计|预测)?(?:接近|约为|约|达到|为)?\s*(\d+(?:\.\d+)?)\s*(亿元|万元|元)"
        )
        for match in pattern.finditer(window):
            values.append(_number_token(match.group(1), match.group(2)))
        return values

    if metric == "net_profit_amount":
        values: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?:归属于上市公司股东的净利润|归母净利润|净利润)[^。；]{0,30}?"
            r"(?:为|达|达到|盈利|亏损)?\s*(\d+(?:\.\d+)?)\s*(亿元|万元|元)"
        )
        for match in pattern.finditer(window):
            values.append(_number_token(match.group(1), match.group(2)))
        return values

    patterns: dict[str, tuple[re.Pattern[str], ...]] = {
        "r_and_d_revenue_ratio": (re.compile(r"占营业收入(?:的)?比重(?:为|是|约为|达到)?\s*(\d+(?:\.\d+)?)\s*[%％]"),),
        "overseas_income_ratio": (re.compile(r"境外(?:地区)?(?:营业)?收入[^。；]{0,90}?(?:占比|比例|占营业收入(?:的)?比重)[^\d]{0,12}(\d+(?:\.\d+)?)\s*[%％]"),),
        "existing_customer_deadline": (
            re.compile(r"(\d+(?:\.\d+)?)\s*年内完成全部存量客户"),
            re.compile(r"全部存量客户[^。；]{0,50}?(\d+(?:\.\d+)?)\s*年内完成"),
        ),
        "identity_record_retention": (re.compile(r"(?:业务关系结束后|金融服务结束后)[^。；]{0,70}?至少保存\s*(\d+(?:\.\d+)?)\s*年"),),
        "net_profit_margin": (re.compile(r"净利润率(?:为|是|约为|达到)?\s*(\d+(?:\.\d+)?)\s*[%％]"),),
        "dividend_profit_ratio": (re.compile(r"(?:现金分红|分红|派息率)[^。；]{0,90}?(\d+(?:\.\d+)?)\s*[%％]"),),
        "market_share": (re.compile(r"(?:市占率|市场份额|市场占有率)(?:为|是|约为|达到)?\s*(\d+(?:\.\d+)?)\s*[%％]"),),
        "ip_licensing_market_share": (re.compile(r"(?:IP\s*授权业务)[^。；]{0,80}?(?:市占率|市场份额|市场占有率)(?:为|是|约为|达到)?\s*(\d+(?:\.\d+)?)\s*[%％]"),),
        "original_premium_market_share": (re.compile(r"原保费[^。；]{0,80}?(?:市场份额|市场占有率)(?:为|是|约为|达到)?\s*(\d+(?:\.\d+)?)\s*[%％]"),),
    }
    unit = "年" if metric in {"existing_customer_deadline", "identity_record_retention"} else "%"
    values: list[dict[str, Any]] = []
    for pattern in patterns.get(metric, ()):
        for match in pattern.finditer(window):
            values.append(_number_token(match.group(1), unit))
    if metric == "identity_record_retention" and "至少保存十年" in _compact(window):
        values.append({"raw": "十年", "number": 10.0, "unit": "年"})
    if metric == "debt_asset_ratio":
        for match in re.finditer(r"([^，。；]{0,90}?)(?:的)?资产负债率(?:将)?(?:为|是|约为|达到|下降至|降低至|上升至)?\s*(\d+(?:\.\d+)?)\s*[%％]", window):
            prefix = match.group(1)
            if atoms.entity_roles and not any(_compact(item["role"] + item["entity"]) in _compact(prefix) for item in atoms.entity_roles):
                continue
            context = window[max(0, match.start() - 100) : match.end() + 40]
            if atoms.scenario_or_condition and not _scenario_match(atoms.scenario_or_condition, context)[0]:
                continue
            values.append(_number_token(match.group(2), "%"))
    if metric in {"net_profit_growth", "revenue_growth", "r_and_d_growth"}:
        objects = {
            "net_profit_growth": ("净利润", "归属于上市公司股东的净利润", "归母净利润"),
            "revenue_growth": ("营收", "营业收入", "收入"),
            "r_and_d_growth": ("研发费用", "研发投入"),
        }[metric]
        for obj in objects:
            pattern = re.compile(re.escape(obj) + r"[^。；]{0,80}?(?:同比|比上年)\s*(?:增长|下降)\s*(\d+(?:\.\d+)?)\s*[%％]")
            for match in pattern.finditer(window):
                values.append(_number_token(match.group(1), "%"))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for item in values:
        key = (float(item["number"]), str(item["unit"]))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _value_relation(atoms: TypedClaimAtoms, window: str, matched_metrics: Sequence[str]) -> tuple[str, list[str], list[str]]:
    if atoms.trajectory:
        return "not_required", [], []
    if not atoms.metric_value and not atoms.duration:
        return "not_required", [], []
    actual: list[dict[str, Any]] = []
    for metric in matched_metrics:
        actual.extend(_metric_specific_values(metric, window, atoms))
    expected = list(atoms.metric_value) + list(atoms.duration)
    if not actual:
        return "missing", [str(item.get("raw") or "") for item in expected], []
    conflicts: list[str] = []
    matched_values: list[str] = []
    for item in expected:
        unit = str(item.get("unit") or "")
        compatible = [fact for fact in actual if not unit or str(fact.get("unit") or "") == unit]
        if not compatible:
            conflicts.append(f"unit:{unit}")
            continue
        expected_number = float(item.get("number") or 0.0)
        if any(_compare_number(expected_number, float(fact.get("number") or 0.0), atoms.comparator) for fact in compatible):
            matched_values.append(str(item.get("raw") or expected_number))
        else:
            conflicts.append(f"expected:{item.get('raw')} actual:{','.join(str(fact.get('raw')) for fact in compatible)}")
    return ("matched", matched_values, []) if not conflicts else ("conflict", matched_values, conflicts)


def _profit_polarity(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("亏损", "为负", "负值")):
        return "loss"
    if any(token in compact for token in ("盈利", "实现盈利", "扭亏为盈")):
        return "profit"
    return "unknown"


def _profit_polarity_relation(
    atoms: TypedClaimAtoms,
    option_text: str,
    window: str,
    matched_metrics: Sequence[str],
) -> tuple[str, list[str]]:
    if "net_profit_amount" not in matched_metrics:
        return "not_required", []
    expected = _profit_polarity(option_text)
    actual = _profit_polarity(window)
    if expected == "unknown" or actual == "unknown":
        return "missing", ["net_profit_polarity"]
    if expected == actual:
        return "matched", []
    return "conflict", [f"net_profit_polarity:expected={expected} actual={actual}"]


def _entity_attribution_relation(
    atoms: TypedClaimAtoms,
    window: str,
    *,
    entity_ok: bool,
    date_ok: bool,
    scenario_ok: bool,
    metric_ok: bool,
    value_relation: str,
) -> tuple[str, list[str], list[str]]:
    expected = [entity for entity in atoms.entity_or_subject if entity not in _GENERIC_ENTITIES]
    if entity_ok or not expected:
        return "not_required", [], []
    if not (date_ok and scenario_ok and metric_ok and value_relation == "matched"):
        return "not_bound", [], []

    value_anchors = [
        str(item.get("number") or "")
        for item in atoms.metric_value
        if item.get("number") is not None
    ]
    anchor_positions = [window.find(anchor) for anchor in value_anchors if anchor and window.find(anchor) >= 0]
    if not anchor_positions:
        return "not_bound", [], []
    fact_start = min(anchor_positions)
    attribution_prefix = window[:fact_start]
    local_entities, _ = _extract_entities(attribution_prefix)
    actual = [entity for entity in local_entities if entity not in _GENERIC_ENTITIES]
    expected_aliases = {_compact(alias) for entity in expected for alias in _entity_aliases(entity)}
    actual_distinct = [
        entity for entity in actual
        if not any(alias and alias in _compact(entity) for alias in expected_aliases)
    ]
    actual_distinct = _dedupe(actual_distinct)
    if len(actual_distinct) != 1:
        return "ambiguous", [], []
    actual_entity = actual_distinct[0]
    return (
        "conflict",
        ["entity_attribution", f"actual_entity:{actual_entity}"],
        [f"entity_attribution:expected={expected[0]} actual={actual_entity}"],
    )


def _rank_relation(atoms: TypedClaimAtoms, window: str) -> tuple[str, list[str]]:
    if not atoms.rank_scope and not atoms.rank_value:
        return "not_required", []
    facts = _extract_rank_pairs(window)
    if not facts:
        return "missing", ["rank_fact"]
    expected_pairs = list(zip(atoms.rank_scope, atoms.rank_value))
    for expected_scope, expected_value in expected_pairs:
        same_scope = [value for scope, value in facts if scope == expected_scope]
        if expected_value in same_scope:
            continue
        if same_scope:
            return "conflict", [f"rank:{expected_scope}:{expected_value} actual:{same_scope}"]
        return "missing", [f"rank_scope:{expected_scope}"]
    return "matched", []


def _trajectory_relation(atoms: TypedClaimAtoms, window: str) -> tuple[str, list[str]]:
    if not atoms.trajectory:
        return "not_required", []
    actual = _extract_trajectory(window)
    if not actual:
        return "missing", ["trajectory"]
    for expected in atoms.trajectory:
        for fact in actual:
            checks = [
                not expected.get("start_period") or expected.get("start_period") == fact.get("start_period"),
                _numbers_equal(expected.get("start_value", 0), fact.get("start_value", -1)),
                expected.get("direction") == fact.get("direction"),
                "end_value" not in expected or _numbers_equal(expected["end_value"], fact.get("end_value", -1)),
            ]
            if all(checks):
                return "matched", []
    return "conflict", [f"trajectory_expected:{atoms.trajectory} actual:{actual}"]


def _formula_relation(atoms: TypedClaimAtoms, option_text: str, window: str) -> tuple[str, list[str]]:
    if "overdue_interest_base" not in atoms.metric_object:
        return "not_required", []
    matches = list(re.finditer(r"(?:逾期利息|违约利息)[^。；]{0,180}?计算方式为\s*([^。；\n]+)", window))
    if not matches:
        return "missing", ["overdue_interest_formula"]
    formulas = [match.group(1) for match in matches]
    if "本金和利息" in _compact(option_text):
        if any("本金和利息" in _compact(formula) for formula in formulas):
            return "matched", []
        return "conflict", [f"option_base=本金和利息 actual_formula={formulas[0]}"]
    return "matched", []



def _field_value(text: str, fact_type: str) -> str:
    patterns: dict[str, tuple[re.Pattern[str], ...]] = {
        "stock_short_name": (
            re.compile(r"(?:股票|证券)简称(?:是|为|：|:)?\s*([A-Za-z\u4e00-\u9fff]{2,20})"),
        ),
        "stock_code": (
            re.compile(r"(?:股票|证券)代码(?:是|为|：|:)?\s*(\d{6})"),
        ),
        "initial_conversion_price": (
            re.compile(r"初始转股(?:价格|价)(?:为|是|：|:)?\s*(\d+(?:\.\d+)?)\s*元(?:/股)?"),
        ),
        "subject_credit_rating": (
            re.compile(r"(?:主体信用评级|主体评级|主体信用等级)(?:为|是|达到|：|:)?\s*(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB)", re.IGNORECASE),
        ),
        "bond_credit_rating": (
            re.compile(r"(?:债项信用评级|债券信用评级|可转债信用等级|债券信用等级)(?:为|是|达到|：|:)?\s*(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|负值)", re.IGNORECASE),
        ),
    }
    for pattern in patterns.get(fact_type, ()):
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


def _attribute_relation(atoms: TypedClaimAtoms, option_text: str, window: str) -> tuple[str, str, list[str], list[str]]:
    metrics = set(atoms.metric_object)
    option = _compact(option_text)
    local = _compact(window)
    matched: list[str] = []
    conflicts: list[str] = []

    for fact_type in ("stock_short_name", "stock_code", "initial_conversion_price", "subject_credit_rating", "bond_credit_rating"):
        if fact_type not in metrics:
            continue
        expected = _field_value(option_text, fact_type)
        actual = _field_value(window, fact_type)
        if not expected or not actual:
            continue
        if fact_type == "initial_conversion_price":
            equal = _numbers_equal(float(expected), float(actual))
        else:
            equal = _compact(expected) == _compact(actual)
        if equal:
            matched.extend([fact_type, f"attribute_value:{expected}"])
            return "supported", f"same-document {fact_type} field matches option value", matched, conflicts
        conflicts.append(f"{fact_type}:expected={expected} actual={actual}")
        return "contradicted", f"same-document {fact_type} field has an incompatible value", matched, conflicts

    if "initial_conversion_price_floor" in metrics:
        option_floor = "不低于" in option and "初始转股" in option
        local_floor = "不低于" in local and "初始转股" in local and "交易均价" in local
        if option_floor and local_floor:
            matched.extend(["initial_conversion_price_floor", "comparator:not_lower_than", "market_price_basis"])
            return "supported", "same-document initial conversion price floor is tied to the announcement-date trading-average basis", matched, conflicts

    if "issuer_category" in metrics:
        expects_securities = "证券公司" in option
        actual_securities = "证券公司" in local or "证券股份有限公司" in local
        if expects_securities and actual_securities:
            return "supported", "same-document issuer identity is explicitly a securities company", ["issuer_category"], []

    clause_requirements: dict[str, tuple[str, ...]] = {
        "conditional_redemption_clause": ("赎回", "条款"),
        "default_or_compensation_clause": ("违约",),
        "downward_revision_clause": ("转股价格", "向下修正"),
        "issue_scale_cap": ("发行", "规模"),
    }
    for fact_type, required in clause_requirements.items():
        if fact_type not in metrics:
            continue
        if all(token in local for token in required):
            if fact_type == "issue_scale_cap" and not any(token in local for token in ("不超过", "上限", "最高", "不多于")):
                continue
            return "supported", f"same-document local clause explicitly binds {fact_type}", [fact_type], []
    return "none", "", matched, conflicts


def _legal_relation(atoms: TypedClaimAtoms, option_text: str, window: str) -> tuple[str, str]:
    option = _compact(option_text)
    local = _compact(window)
    metrics = set(atoms.metric_object)
    if "non_major_difference_report" in metrics and "非重大差异" in local:
        if "提交差异报告" in option and "无需提交差异报告" in local:
            return "contradicted", "same-window rule says non-major differences do not require a report"
        if ("无需提交差异报告" in option or "不提交差异报告" in option) and "无需提交差异报告" in local:
            return "supported", "same-window non-major difference no-report clause"
    if "shell_bank_relationship" in metrics and "空壳银行" in local and "代理行" in local and "不得与空壳银行建立" in local:
        if any(token in option for token in ("可以", "只要", "即可", "允许")):
            return "contradicted", "same-window prohibition conflicts with option permission"
        if "不得" in option or "不能" in option:
            return "supported", "same-window shell-bank prohibition"
    if "investigation_retention" in metrics:
        required = ("反洗钱调查", "最低保存期限届满", "仍未结束", "保存至", "调查工作结束")
        if all(token in local for token in required):
            return "supported", "same-window investigation retention clause"
    if "identity_record_retention" in metrics and "立即删除" in option and "至少保存" in local and "业务关系结束" in local:
        return "contradicted", "same-window minimum retention conflicts with immediate deletion"
    return "none", ""


def _strict_calculation_certification(payload: Mapping[str, Any], option_text: str) -> dict[str, Any] | None:
    route = str(payload.get("claim_route") or payload.get("route") or "").lower()
    if route not in {"calculation", "deterministic_calculation", "formula_calculation"}:
        return None
    refs = payload.get("calculation_refs") or []
    metadata = payload.get("calculation_metadata") if isinstance(payload.get("calculation_metadata"), Mapping) else payload
    required = {
        "formula": metadata.get("formula") or metadata.get("extracted_formula") or metadata.get("extracted_formulas"),
        "variables": metadata.get("variables") or metadata.get("extracted_values") or metadata.get("variable_alias_map"),
        "computed_result": metadata.get("computed_result") or metadata.get("computed_values"),
        "option_mapping": metadata.get("option_mapping") or metadata.get("option_match") or metadata.get("match_raw"),
    }
    complete_flag = metadata.get("calculation_complete") is True or metadata.get("computation_complete") is True
    missing = [name for name, value in required.items() if value in (None, "", [], {})]
    atoms = extract_typed_claim_atoms(option_text)
    if not refs or not complete_flag or missing:
        return _result(
            source_status="not_required_for_calculation",
            status="ambiguous",
            basis="calculation lineage incomplete",
            source="",
            window="",
            atoms=atoms,
            matched=[],
            missing=["calculation_complete"] + missing,
            conflicts=[],
            refs=[str(item) for item in refs],
            audit_source="calculation",
        )
    return _result(
        source_status="not_required_for_calculation",
        status="supported",
        basis="complete deterministic calculation binds formula, variables, result and option mapping",
        source="",
        window="",
        atoms=atoms,
        matched=["calculation_formula", "calculation_variables", "computed_result", "option_mapping"],
        missing=[],
        conflicts=[],
        refs=[str(item) for item in refs],
        audit_source="calculation",
    )


def _result(
    *,
    source_status: str,
    status: str,
    basis: str,
    source: str,
    window: str,
    atoms: TypedClaimAtoms,
    matched: Sequence[str],
    missing: Sequence[str],
    conflicts: Sequence[str],
    refs: Sequence[str],
    audit_source: str = "typed_claim_local_window",
    score: int | None = None,
) -> dict[str, Any]:
    relation = REL_SUPPORTED if status == "supported" else REL_CONTRADICTED if status == "contradicted" else REL_AMBIGUOUS
    row = {
        "source_resolution_status": source_status,
        "claim_certification_status": status,
        "relation_after_audit": relation,
        "certification_basis": basis,
        "canonical_source": source,
        "local_window": window,
        "claim_atoms": atoms.to_dict(),
        "matched_atoms": _dedupe(list(matched)),
        "missing_atoms": _dedupe(list(missing)),
        "conflicting_atoms": _dedupe(list(conflicts)),
        "evidence_refs_considered": list(refs),
        "short_evidence_excerpt_or_reason": window[:500] if window else basis,
        "audited_terms_found": _dedupe(list(matched)),
        "audited_terms_missing": _dedupe(list(missing)),
        "audit_text_source": audit_source,
    }
    if score is not None:
        row["_score"] = score
    return row


def certify_typed_option_claim(payload: Mapping[str, Any] | None, *, replacement_effect: str = "no_change") -> dict[str, Any]:
    raw = dict(payload or {})
    option_text = str(raw.get("option_text") or "").strip()
    if _compact(option_text) in {"正确", "错误", "对", "错", "是", "否"}:
        atoms = extract_typed_claim_atoms(option_text)
        return _result(
            source_status="missing_ref" if not (raw.get("resolved_evidence_refs") or raw.get("evidence_refs")) else "resolved_but_semantically_opaque",
            status="ambiguous",
            basis="truth-label option requires the question proposition and cannot be certified from a matching word",
            source="",
            window="",
            atoms=atoms,
            matched=[],
            missing=["question_proposition_binding"],
            conflicts=[],
            refs=[],
        )
    calc = _strict_calculation_certification(raw, option_text)
    if calc is not None:
        return calc
    question_doc_ids = raw.get("question_doc_ids") if isinstance(raw.get("question_doc_ids"), Sequence) and not isinstance(raw.get("question_doc_ids"), (str, bytes, bytearray)) else None
    atoms = extract_typed_claim_atoms(option_text, question_doc_ids=question_doc_ids)
    windows = source_windows(raw)
    refs = raw.get("resolved_evidence_refs") or raw.get("evidence_refs") or []
    ref_list = [str(refs)] if isinstance(refs, str) else [str(item) for item in (refs or [])]
    source_status = "resolved" if windows else "missing_ref" if not ref_list else "resolved_but_no_local_window"
    if not option_text:
        return _result(source_status=source_status, status="ambiguous", basis="option text missing", source="", window="", atoms=atoms, matched=[], missing=["option_text"], conflicts=[], refs=ref_list)
    if not windows:
        result = _result(source_status=source_status, status="ambiguous", basis="no readable local evidence window", source="", window="", atoms=atoms, matched=[], missing=["local_window"], conflicts=[], refs=ref_list)
        if not ref_list:
            result["relation_after_audit"] = REL_MISSING
        return result

    best: dict[str, Any] | None = None
    for candidate in windows:
        source = candidate["canonical_source"]
        window = candidate["local_window"]
        doc_ok = _source_identity_match(atoms.document_identity, source)
        entity_ok, matched_entities, missing_entities = _entity_match(atoms, source, window)
        role_ok, missing_roles = _role_match(atoms, window)
        date_ok, missing_dates = _date_match(atoms.date_or_period, source, window)
        scenario_ok, missing_scenarios = _scenario_match(atoms.scenario_or_condition, window)
        metric_ok, matched_metrics, missing_metrics = _metric_match(atoms.metric_object, window)
        value_relation, matched_values, value_conflicts = _value_relation(atoms, window, matched_metrics)
        rank_relation, rank_issues = _rank_relation(atoms, window)
        trajectory_relation, trajectory_issues = _trajectory_relation(atoms, window)
        formula_relation, formula_issues = _formula_relation(atoms, option_text, window)
        profit_polarity_relation, profit_polarity_issues = _profit_polarity_relation(
            atoms, option_text, window, matched_metrics
        )
        entity_attribution_relation, entity_attribution_matched, entity_attribution_issues = _entity_attribution_relation(
            atoms,
            window,
            entity_ok=entity_ok,
            date_ok=date_ok,
            scenario_ok=scenario_ok,
            metric_ok=metric_ok,
            value_relation=value_relation,
        )
        legal_relation, legal_basis = _legal_relation(atoms, option_text, window)
        attribute_relation, attribute_basis, attribute_matched, attribute_conflicts = _attribute_relation(atoms, option_text, window)

        matched: list[str] = []
        missing: list[str] = []
        conflicts: list[str] = []
        for name, ok, required in (
            ("document_identity", doc_ok, bool(atoms.document_identity)),
            ("entity_or_subject", entity_ok, bool([e for e in atoms.entity_or_subject if e not in _GENERIC_ENTITIES])),
            ("entity_role", role_ok, bool(atoms.entity_roles)),
            ("date_or_period", date_ok, bool(atoms.date_or_period)),
            ("scenario_or_condition", scenario_ok, bool(atoms.scenario_or_condition)),
            ("metric_object", metric_ok, bool(atoms.metric_object)),
        ):
            if ok:
                matched.append(name)
            elif required:
                missing.append(name)
        matched.extend(f"entity:{item}" for item in matched_entities)
        missing.extend(f"entity:{item}" for item in missing_entities)
        missing.extend(f"role:{item}" for item in missing_roles)
        missing.extend(f"date:{item}" for item in missing_dates)
        missing.extend(f"scenario:{item}" for item in missing_scenarios)
        missing.extend(f"metric:{item}" for item in missing_metrics)
        if value_relation == "matched":
            matched.extend(["metric_value", "unit", "comparator"] + [f"value:{item}" for item in matched_values])
        elif value_relation == "missing":
            missing.extend(["metric_value", "unit", "comparator"])
        elif value_relation == "conflict":
            conflicts.extend(value_conflicts)
        if rank_relation == "matched":
            matched.extend(["rank_scope", "rank_value"])
        elif rank_relation == "missing":
            missing.extend(rank_issues)
        elif rank_relation == "conflict":
            conflicts.extend(rank_issues)
        if trajectory_relation == "matched":
            matched.append("trajectory")
        elif trajectory_relation == "missing":
            missing.extend(trajectory_issues)
        elif trajectory_relation == "conflict":
            conflicts.extend(trajectory_issues)
        matched.extend(attribute_matched)
        conflicts.extend(attribute_conflicts)
        matched.extend(entity_attribution_matched)
        conflicts.extend(entity_attribution_issues)
        if profit_polarity_relation == "matched":
            matched.append("net_profit_polarity")
        elif profit_polarity_relation == "missing":
            missing.extend(profit_polarity_issues)
        elif profit_polarity_relation == "conflict":
            conflicts.extend(profit_polarity_issues)
        if formula_relation == "matched":
            matched.append("formula_relation")
        elif formula_relation == "missing":
            missing.extend(formula_issues)
        elif formula_relation == "conflict":
            conflicts.extend(formula_issues)

        prerequisites = doc_ok and entity_ok and role_ok and metric_ok and date_ok and scenario_ok
        status = "ambiguous"
        basis = "typed claim atoms are not fully bound in one local window"
        if attribute_relation == "supported" and doc_ok and entity_ok and date_ok and scenario_ok:
            status = "supported"
            basis = attribute_basis
        elif attribute_relation == "contradicted" and doc_ok and entity_ok and date_ok and scenario_ok:
            status = "contradicted"
            basis = attribute_basis
        elif legal_relation == "supported" and doc_ok and entity_ok and metric_ok:
            status = "supported"
            basis = legal_basis
        elif legal_relation == "contradicted" and doc_ok and entity_ok and metric_ok:
            status = "contradicted"
            basis = legal_basis
        elif entity_attribution_relation == "conflict" and doc_ok:
            status = "contradicted"
            basis = "same-time, same-metric, exact-value fact is explicitly attributed to a different named entity"
        elif prerequisites and profit_polarity_relation == "conflict":
            status = "contradicted"
            basis = "same-entity, same-time net-profit polarity conflicts with option"
        elif prerequisites and rank_relation == "conflict":
            status = "contradicted"
            basis = "same-scope rank fact conflicts with option ordinal"
        elif prerequisites and trajectory_relation == "conflict":
            status = "contradicted"
            basis = "same-entity metric trajectory conflicts with option trajectory"
        elif prerequisites and formula_relation == "conflict":
            status = "contradicted"
            basis = "same-document formula conflicts with option calculation base"
        elif prerequisites and value_relation == "conflict":
            status = "contradicted"
            basis = "same-entity, same-metric, same-time/scenario value conflicts with option"
        else:
            value_ok = value_relation in {"matched", "not_required"}
            rank_ok = rank_relation in {"matched", "not_required"}
            trajectory_ok = trajectory_relation in {"matched", "not_required"}
            formula_ok = formula_relation in {"matched", "not_required"}
            exact_option = _compact(option_text) and _compact(option_text) in _compact(window)
            polarity_ok = atoms.comparator not in {"immediate", "conditional_permission"}
            if prerequisites and value_ok and rank_ok and trajectory_ok and formula_ok and polarity_ok and (atoms.metric_object or exact_option):
                status = "supported"
                basis = "single-source typed local window binds entity, metric, period/scenario and relation"

        if not entity_ok or not metric_ok or not scenario_ok or not role_ok or not doc_ok:
            if status == "contradicted" and entity_attribution_relation != "conflict":
                status = "ambiguous"
                basis = "different entity/document/metric/scenario cannot directly contradict option"

        score = len(matched) * 3 - len(missing) * 2 - len(conflicts)
        if status == "supported":
            score += 200
        elif status == "contradicted":
            score += 180
        score += 20 if scenario_ok and atoms.scenario_or_condition else 0
        score += 20 if role_ok and atoms.entity_roles else 0
        score += 20 if value_relation == "matched" else 0
        score += 20 if rank_relation == "matched" else 0
        score += 20 if trajectory_relation == "matched" else 0

        row = _result(
            source_status="resolved",
            status=status,
            basis=basis,
            source=source,
            window=window,
            atoms=atoms,
            matched=matched,
            missing=missing,
            conflicts=conflicts,
            refs=[source] if source else ref_list[:5],
            score=score,
        )
        if best is None or int(row["_score"]) > int(best["_score"]):
            best = row

    assert best is not None
    best.pop("_score", None)
    return best


ClaimAtoms = TypedClaimAtoms
extract_claim_atoms = extract_typed_claim_atoms
certify_option_claim = certify_typed_option_claim
