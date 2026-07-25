"""Auditable shadow query planning for BB-P0-04G.

The production document-scope resolver and lexical hybrid retriever deliberately
remain unchanged.  This module turns only multi-slot-visible question fields into a
structured query plan that can be compared offline with the current production
signals.  It never consumes answer truth or qid->document mappings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Mapping, Sequence

from contracts import ClassificationResult, Question, QuestionLabel
from retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry
from retrieval.document_scope import DocumentScopeResult


POLICY_VERSION = "bb_p0_04g_shadow_query_plan_v1"

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_NUMERIC_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:%|％|亿元|万元|万|亿|元|倍|个百分点|年|月|日)?"
)
_BRACKET_TITLE_RE = re.compile(r"《([^》]{3,80})》")
_ORG_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{2,30}(?:股份有限公司|有限责任公司|有限公司|集团|银行|证券|保险|基金|委员会|管理局|监督管理总局|交易所|研究院|研究所)"
)
_SPLIT_RE = re.compile(r"[\s，。；：、！？,.!?;:（）()【】\[\]<>《》“”\"'—]+")
_RELATION_SPLIT_RE = re.compile(
    r"(?:关于|根据|结合|依据|下列|以下|其中|以及|并且|同时|分别|及|与|和|的|为|达到|达到了|预计|同比|环比|较|是否|描述|说法|陈述)"
)
_COMPACT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

_GENERIC_TERMS = {
    "根据", "结合", "依据", "关于", "以下", "下列", "哪些", "哪个", "判断", "描述",
    "说法", "陈述", "正确", "错误", "准确", "符合", "事实", "报告", "年度报告", "数据",
    "公司", "产品", "规定", "条款", "情况", "进行", "是否", "其中", "分别", "给出",
    "多选题", "单选题", "判断题", "计算题", "抽取题", "财务指标对比分析", "财务数据一致性校验",
    "金额", "问题", "选项", "结果", "内容",
}

_METRIC_TERMS: Mapping[str, tuple[str, ...]] = {
    "financial_reports": (
        "营业收入", "营业总收入", "净利润", "归母净利润", "扣非净利润", "毛利率", "净利率",
        "研发投入", "研发费用", "经营活动产生的现金流量净额", "现金流量净额", "现金分红",
        "股份回购", "资产负债率", "总资产", "净资产", "每股收益", "境外收入", "境内收入",
    ),
    "financial_contracts": (
        "募集资金", "票面利率", "债券余额", "发行规模", "发行期限", "回售", "赎回", "担保",
        "承销", "偿债", "发行人", "募集说明书",
    ),
    "insurance": (
        "保险责任", "责任免除", "等待期", "犹豫期", "现金价值", "赔付", "给付", "免赔额",
        "保险费", "保额", "退保", "受益人", "被保险人", "投保人",
    ),
    "regulatory": (
        "注册资本", "风险权重", "资本充足率", "杠杆率", "流动性", "期限", "比例", "限额",
        "罚款", "信息披露", "监督管理", "报告", "备案", "许可",
    ),
    "research": (
        "市场规模", "增速", "复合增长率", "渗透率", "市场份额", "预测", "预计", "空间",
        "需求", "供给", "销量", "价格", "产能", "装机量", "竞争格局",
    ),
}

_RELATION_TERMS = (
    "同比", "环比", "较", "增长", "下降", "增加", "减少", "高于", "低于", "超过", "不足",
    "等于", "差额", "合计", "占比", "比例", "排序", "最高", "最低", "分别", "之间", "比较",
)

_NEGATIVE_TERMS = (
    "不正确", "错误", "不准确", "不符合", "不包括", "不属于", "除外", "未", "不得", "没有",
    "无需", "不能", "不应", "不承担", "不适用", "否",
)

_SECTION_HINTS: Mapping[str, tuple[str, ...]] = {
    "financial_reports": (
        "主要会计数据", "财务报表", "管理层讨论", "经营情况讨论与分析", "研发投入", "分红",
        "主营业务", "收入构成", "现金流量表", "利润表", "资产负债表",
    ),
    "financial_contracts": (
        "募集资金运用", "发行条款", "偿债保障", "担保", "回售", "赎回", "风险因素", "释义",
    ),
    "insurance": (
        "保险责任", "责任免除", "等待期", "犹豫期", "现金价值", "合同解除", "释义", "给付",
    ),
    "regulatory": (
        "总则", "监督管理", "法律责任", "附则", "信息披露", "风险管理", "资本管理", "适用范围",
    ),
    "research": (
        "市场规模", "行业空间", "竞争格局", "产业链", "投资建议", "风险提示", "需求", "供给",
    ),
}

_CLASSIFICATION_TERMS: Mapping[QuestionLabel, tuple[str, ...]] = {
    QuestionLabel.CALCULATION: ("计算", "金额", "比例"),
    QuestionLabel.CLAUSE_LOOKUP: ("责任免除", "等待期", "除外", "应当", "不得"),
    QuestionLabel.FACT_LOOKUP: ("营业收入", "净利润", "金额", "数据"),
    QuestionLabel.CROSS_DOC: ("比较", "分别", "合计"),
    QuestionLabel.MULTI_OPTION: (),
    QuestionLabel.DEFAULT: (),
}


@dataclass(frozen=True)
class QueryTermProvenance:
    term: str
    category: str
    source: str
    rule: str
    retrieval_use: str

    def to_dict(self) -> dict[str, str]:
        return {
            "term": self.term,
            "category": self.category,
            "source": self.source,
            "rule": self.rule,
            "retrieval_use": self.retrieval_use,
        }


@dataclass(frozen=True)
class QueryPlan:
    qid: str
    domain: str
    question_terms: tuple[str, ...]
    option_terms_by_label: Mapping[str, tuple[str, ...]]
    entity_terms: tuple[str, ...]
    product_or_document_identity_terms: tuple[str, ...]
    year_terms: tuple[str, ...]
    numeric_terms: tuple[str, ...]
    metric_terms: tuple[str, ...]
    relation_terms: tuple[str, ...]
    negative_exception_terms: tuple[str, ...]
    section_or_title_hints: tuple[str, ...]
    generic_terms_dropped: tuple[str, ...]
    query_confidence: str
    confidence_reasons: tuple[str, ...]
    document_scope_terms: tuple[str, ...]
    window_retrieval_terms: tuple[str, ...]
    reclassification_recommendation: str
    term_provenance: tuple[QueryTermProvenance, ...]
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "qid": self.qid,
            "domain": self.domain,
            "question_terms": list(self.question_terms),
            "option_terms_by_label": {
                label: list(terms) for label, terms in self.option_terms_by_label.items()
            },
            "entity_terms": list(self.entity_terms),
            "product_or_document_identity_terms": list(self.product_or_document_identity_terms),
            "year_terms": list(self.year_terms),
            "numeric_terms": list(self.numeric_terms),
            "metric_terms": list(self.metric_terms),
            "relation_terms": list(self.relation_terms),
            "negative_exception_terms": list(self.negative_exception_terms),
            "section_or_title_hints": list(self.section_or_title_hints),
            "generic_terms_dropped": list(self.generic_terms_dropped),
            "query_confidence": self.query_confidence,
            "confidence_reasons": list(self.confidence_reasons),
            "document_scope_terms": list(self.document_scope_terms),
            "window_retrieval_terms": list(self.window_retrieval_terms),
            "reclassification_recommendation": self.reclassification_recommendation,
            "term_provenance": [item.to_dict() for item in self.term_provenance],
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class ShadowDocumentCandidate:
    doc_id: str
    score: float
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "score": round(self.score, 6),
            "matched_terms": list(self.matched_terms),
        }


class QueryPlanBuilder:
    """Create a deterministic query plan from visible question fields only."""

    def __init__(self, catalog: DocumentCatalog | None = None) -> None:
        self.catalog = catalog

    def build(
        self,
        question: Question,
        classification: ClassificationResult,
        baseline_scope: DocumentScopeResult | None = None,
    ) -> QueryPlan:
        raw_type = str(question.raw.get("type") or question.raw.get("_raw_type") or "").strip()
        option_text = {str(label): str(value) for label, value in question.options.items()}
        visible_texts = [question.text, *option_text.values(), raw_type]
        joined = "\n".join(value for value in visible_texts if value)

        provenance: list[QueryTermProvenance] = []
        generic_dropped: list[str] = []

        question_terms, dropped = _extract_text_terms(question.text)
        generic_dropped.extend(dropped)
        for term in question_terms:
            provenance.append(_prov(term, "question", "question", "visible_text_phrase", "both"))

        option_terms: dict[str, tuple[str, ...]] = {}
        for label, text in option_text.items():
            terms, dropped = _extract_text_terms(text)
            option_terms[label] = terms
            generic_dropped.extend(dropped)
            for term in terms:
                provenance.append(
                    _prov(term, "option", f"option {label}", "visible_option_phrase", "both")
                )

        years = _dedup(_YEAR_RE.findall(joined))
        for term in years:
            provenance.append(_prov(term, "year", _source_for_term(term, question, option_text), "year_regex", "both"))

        numerics = _extract_numeric_terms(joined, years)
        for term in numerics:
            provenance.append(_prov(term, "numeric", _source_for_term(term, question, option_text), "numeric_regex", "window"))

        metrics = tuple(term for term in _METRIC_TERMS.get(question.domain, ()) if term in joined)
        for term in metrics:
            provenance.append(_prov(term, "metric", _source_for_term(term, question, option_text), "deterministic_domain_rule", "both"))

        relations = tuple(term for term in _RELATION_TERMS if term in joined)
        for term in relations:
            provenance.append(_prov(term, "relation", _source_for_term(term, question, option_text), "relation_lexicon", "both"))

        negatives = tuple(term for term in _NEGATIVE_TERMS if term in joined)
        for term in negatives:
            provenance.append(_prov(term, "negative_exception", _source_for_term(term, question, option_text), "negative_exception_lexicon", "window"))

        sections = tuple(term for term in _SECTION_HINTS.get(question.domain, ()) if term in joined)
        for term in sections:
            provenance.append(_prov(term, "section_or_title_hint", _source_for_term(term, question, option_text), "deterministic_domain_rule", "window"))

        entities = _dedup([*_ORG_RE.findall(joined), *_BRACKET_TITLE_RE.findall(joined)])
        for term in entities:
            provenance.append(_prov(term, "entity", _source_for_term(term, question, option_text), "visible_entity_pattern", "document"))

        identities = self._identity_terms(question.domain, joined)
        for term in identities:
            provenance.append(_prov(term, "identity", _source_for_term(term, question, option_text), "catalog_title_alias_match", "document"))

        for label in classification.labels:
            for term in _CLASSIFICATION_TERMS.get(label, ()):
                provenance.append(
                    _prov(term, "classification_hint", f"classification:{label.value}", "classification_rule", "window")
                )

        document_scope_terms = _dedup(
            [
                *identities,
                *entities,
                *years,
                *metrics,
                *relations,
                *question_terms,
                *(term for terms in option_terms.values() for term in terms),
            ]
        )[:80]
        window_retrieval_terms = _dedup(
            [
                *metrics,
                *numerics,
                *years,
                *sections,
                *negatives,
                *relations,
                *question_terms,
                *(term for terms in option_terms.values() for term in terms),
                *(item.term for item in provenance if item.category == "classification_hint"),
            ]
        )[:100]

        confidence, reasons, recommendation = _confidence(
            question_terms=question_terms,
            option_terms=option_terms,
            entities=entities,
            identities=identities,
            years=years,
            numerics=numerics,
            metrics=metrics,
            sections=sections,
            baseline_scope=baseline_scope,
        )

        return QueryPlan(
            qid=question.qid,
            domain=question.domain,
            question_terms=question_terms,
            option_terms_by_label=option_terms,
            entity_terms=entities,
            product_or_document_identity_terms=identities,
            year_terms=years,
            numeric_terms=numerics,
            metric_terms=metrics,
            relation_terms=relations,
            negative_exception_terms=negatives,
            section_or_title_hints=sections,
            generic_terms_dropped=_dedup(generic_dropped),
            query_confidence=confidence,
            confidence_reasons=reasons,
            document_scope_terms=document_scope_terms,
            window_retrieval_terms=window_retrieval_terms,
            reclassification_recommendation=recommendation,
            term_provenance=_dedup_provenance(provenance),
        )

    def _identity_terms(self, domain: str, joined: str) -> tuple[str, ...]:
        if self.catalog is None:
            return ()
        compact_joined = _compact(joined)
        matches: list[tuple[int, str]] = []
        for entry in self.catalog.entries_for_domain(domain):
            for alias in entry.title_aliases:
                compact_alias = _compact(alias)
                if len(compact_alias) < 3 or compact_alias in _GENERIC_TERMS:
                    continue
                if compact_alias in compact_joined:
                    matches.append((len(compact_alias), alias.strip()))
        matches.sort(key=lambda item: (-item[0], item[1]))
        return _dedup(value for _, value in matches)[:20]


def build_shadow_document_index(
    entries: Sequence[DocumentCatalogEntry],
) -> dict[str, tuple[str, str]]:
    """Precompute compact catalog text for repeated offline shadow ranking."""
    return {
        entry.doc_id: (
            _compact("\n".join([entry.doc_id, entry.title, *entry.title_aliases])),
            _compact(f"{entry.identity_text}\n{entry.lexical_profile}"),
        )
        for entry in entries
    }


def rank_shadow_documents(
    plan: QueryPlan,
    entries: Sequence[DocumentCatalogEntry],
    *,
    limit: int = 10,
    compact_index: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[ShadowDocumentCandidate, ...]:
    """Rank documents with QueryPlan terms for offline comparison only."""
    provenance_by_term: dict[str, set[str]] = {}
    for item in plan.term_provenance:
        provenance_by_term.setdefault(item.term, set()).add(item.category)

    ranked: list[ShadowDocumentCandidate] = []
    for entry in entries:
        if compact_index is not None and entry.doc_id in compact_index:
            title_text, profile_text = compact_index[entry.doc_id]
        else:
            title_text = _compact("\n".join([entry.doc_id, entry.title, *entry.title_aliases]))
            profile_text = _compact(f"{entry.identity_text}\n{entry.lexical_profile}")
        score = 0.0
        matched: list[str] = []
        for term in plan.document_scope_terms:
            compact_term = _compact(term)
            if len(compact_term) < 2:
                continue
            categories = provenance_by_term.get(term, set())
            title_hit = compact_term in title_text
            body_hit = compact_term in profile_text
            if not title_hit and not body_hit:
                continue
            if "identity" in categories:
                weight = 18.0 if title_hit else 8.0
            elif "entity" in categories:
                weight = 12.0 if title_hit else 5.0
            elif "year" in categories:
                weight = 8.0 if title_hit else 3.0
            elif "metric" in categories:
                weight = 6.0 if title_hit else 3.0
            elif "relation" in categories:
                weight = 2.0 if title_hit else 1.0
            else:
                weight = 4.0 if title_hit else (2.0 if len(compact_term) >= 4 else 1.0)
            score += weight
            matched.append(term)
        if score > 0:
            ranked.append(
                ShadowDocumentCandidate(
                    doc_id=entry.doc_id,
                    score=score,
                    matched_terms=_dedup(matched)[:20],
                )
            )
    ranked.sort(key=lambda item: (-item.score, item.doc_id))
    return tuple(ranked[: max(1, int(limit))])


def baseline_preserving_shadow_scope(
    baseline_doc_ids: Sequence[str],
    plan_ranked_doc_ids: Sequence[str],
    *,
    recommendation: str,
    max_docs: int = 10,
) -> tuple[str, ...]:
    """Keep production ordering and only append shadow candidates when signaled.

    This is intentionally conservative: the BB-P0-04G package is not authorized
    to replace production ranking.  A LOW/MEDIUM-confidence plan may propose a
    broader recall scope, but it cannot displace the current top-5 during audit.
    """
    baseline = _dedup(str(value) for value in baseline_doc_ids if str(value))
    if recommendation == "KEEP_CURRENT_STRATEGY":
        return baseline[:max_docs]
    return _dedup([*baseline, *(str(value) for value in plan_ranked_doc_ids if str(value))])[:max_docs]


def provenance_complete(plan: QueryPlan) -> bool:
    """Return whether every emitted retrieval term has an auditable source."""
    indexed = {item.term for item in plan.term_provenance}
    emitted = set(plan.question_terms)
    emitted.update(term for terms in plan.option_terms_by_label.values() for term in terms)
    emitted.update(plan.entity_terms)
    emitted.update(plan.product_or_document_identity_terms)
    emitted.update(plan.year_terms)
    emitted.update(plan.numeric_terms)
    emitted.update(plan.metric_terms)
    emitted.update(plan.relation_terms)
    emitted.update(plan.negative_exception_terms)
    emitted.update(plan.section_or_title_hints)
    emitted.update(plan.document_scope_terms)
    emitted.update(plan.window_retrieval_terms)
    return emitted <= indexed


def _confidence(
    *,
    question_terms: Sequence[str],
    option_terms: Mapping[str, Sequence[str]],
    entities: Sequence[str],
    identities: Sequence[str],
    years: Sequence[str],
    numerics: Sequence[str],
    metrics: Sequence[str],
    sections: Sequence[str],
    baseline_scope: DocumentScopeResult | None,
) -> tuple[str, tuple[str, ...], str]:
    reasons: list[str] = []
    score = 0
    if identities:
        score += 4
        reasons.append("visible_document_or_product_identity")
    if entities:
        score += 2
        reasons.append("visible_entity_signal")
    if years:
        score += 1
        reasons.append("year_signal")
    if metrics:
        score += 1
        reasons.append("domain_metric_signal")
    if numerics:
        score += 1
        reasons.append("numeric_signal")
    if sections:
        score += 1
        reasons.append("section_or_title_signal")
    if len(question_terms) >= 2:
        score += 1
        reasons.append("non_generic_question_terms")

    severe_dispersion = _option_topic_dispersion(option_terms)
    if severe_dispersion:
        score -= 2
        reasons.append("option_topics_severely_dispersed")

    weak_margin = False
    identity_conflict = False
    if baseline_scope is not None:
        candidates = list(baseline_scope.candidates)
        if len(candidates) >= 2:
            margin = float(candidates[0].score) - float(candidates[1].score)
            weak_margin = margin < 2.0
        elif not candidates:
            weak_margin = True
        if weak_margin:
            score -= 1
            reasons.append("document_top1_top2_margin_weak")

        baseline_identity = {_compact(term) for term in baseline_scope.matched_identity_terms if _compact(term)}
        plan_identity = {_compact(term) for term in identities if _compact(term)}
        if baseline_identity and plan_identity:
            identity_conflict = not any(
                left in right or right in left
                for left in baseline_identity
                for right in plan_identity
            )
        if identity_conflict:
            score -= 4
            reasons.append("query_plan_conflicts_with_document_scope_identity")

    only_generic_or_weak = not any((identities, entities, years, metrics, sections)) and len(question_terms) < 2
    if only_generic_or_weak:
        reasons.append("no_stable_identity_or_topic_signal")

    if identity_conflict or only_generic_or_weak or score <= 1:
        confidence = "LOW"
    elif score >= 6 and not severe_dispersion and not weak_margin:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    if confidence == "HIGH":
        recommendation = "KEEP_CURRENT_STRATEGY"
    elif confidence == "LOW" and (identity_conflict or severe_dispersion):
        recommendation = "NEED_RECLASSIFICATION_REVIEW"
    else:
        recommendation = "EXPAND_DOCUMENT_RECALL"

    if not reasons:
        reasons.append("insufficient_visible_query_signal")
    return confidence, tuple(reasons), recommendation


def _option_topic_dispersion(option_terms: Mapping[str, Sequence[str]]) -> bool:
    groups = [set(terms) for terms in option_terms.values() if terms]
    if len(groups) < 3:
        return False
    similarities: list[float] = []
    for left, right in combinations(groups, 2):
        union = left | right
        similarities.append(len(left & right) / len(union) if union else 1.0)
    return bool(similarities) and max(similarities) < 0.12


def _extract_text_terms(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    terms: list[str] = []
    dropped: list[str] = []
    for raw_chunk in _SPLIT_RE.split(str(text or "")):
        chunk = raw_chunk.strip()
        compact = _compact(chunk)
        if not compact:
            continue
        if compact in _GENERIC_TERMS:
            dropped.append(chunk)
            continue
        if 2 <= len(compact) <= 36:
            terms.append(chunk)
        for fragment in _RELATION_SPLIT_RE.split(chunk):
            fragment = fragment.strip()
            fragment_compact = _compact(fragment)
            if not fragment_compact:
                continue
            if fragment_compact in _GENERIC_TERMS:
                dropped.append(fragment)
                continue
            if 3 <= len(fragment_compact) <= 24:
                terms.append(fragment)
    return _dedup(terms)[:40], _dedup(dropped)


def _extract_numeric_terms(joined: str, years: Sequence[str]) -> tuple[str, ...]:
    year_set = set(years)
    values: list[str] = []
    for match in _NUMERIC_RE.finditer(joined):
        value = match.group(0).strip()
        compact = value.replace(" ", "")
        if compact in year_set or compact.rstrip("年") in year_set:
            continue
        if compact:
            values.append(compact)
    return _dedup(values)[:30]


def _source_for_term(term: str, question: Question, options: Mapping[str, str]) -> str:
    if term and term in question.text:
        return "question"
    for label, value in options.items():
        if term and term in value:
            return f"option {label}"
    raw_type = str(question.raw.get("type") or question.raw.get("_raw_type") or "")
    if term and term in raw_type:
        return "raw type"
    return "deterministic domain rule"


def _prov(term: str, category: str, source: str, rule: str, retrieval_use: str) -> QueryTermProvenance:
    return QueryTermProvenance(
        term=str(term).strip(),
        category=category,
        source=source,
        rule=rule,
        retrieval_use=retrieval_use,
    )


def _dedup(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _dedup_provenance(values: Iterable[QueryTermProvenance]) -> tuple[QueryTermProvenance, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[QueryTermProvenance] = []
    for item in values:
        if not item.term:
            continue
        key = (item.term, item.category, item.source, item.rule, item.retrieval_use)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _compact(value: str) -> str:
    return _COMPACT_RE.sub("", str(value or "").lower())
