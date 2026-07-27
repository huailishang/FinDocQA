"""multi-slot candidate document discovery.

This module resolves a question with no declared ``doc_ids`` into an auditable,
ranked candidate scope.  It is deterministic and zero-API by design.  Candidate
scope is retrieval scope only; it is never promoted to required-document truth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from contracts import ClassificationResult, Question
from retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry


_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_SPLIT_RE = re.compile(r"[\s，。；：、！？,.!?;:（）()【】\[\]<>《》“”\"'—\-]+")
_RELATION_SPLIT_RE = re.compile(
    r"(?:关于|根据|结合|依据|下列|以下|其中|以及|及|与|和|的|为|达到|达到了|预计|同比|较|是否|描述|说法|陈述)"
)
_COMPACT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_FINANCIAL_REPORT_DOC_RE = re.compile(
    r"^annual_(?P<entity>.+?)_(?P<year>(?:19|20)\d{2})_report$",
    re.IGNORECASE,
)
_TOPIC_SPLIT_RE = re.compile(r"(?:以及|并且|同时|分别|及|与|和|、|；|;|，|,)")
_REGULATORY_TITLE_SUFFIXES = (
    "管理办法",
    "实施办法",
    "暂行办法",
    "办法",
    "管理规定",
    "规定",
    "条例",
    "规则",
    "细则",
    "指引",
    "通知",
)
_IDENTITY_ALIAS_NOISE = {
    "年度报告",
    "报告",
    "全文",
    "股份有限公司",
    "有限公司",
    "集团",
    "公司",
    "股票简称",
    "证券简称",
    "公司简称",
    "股票代码",
    "证券代码",
    "募集说明书",
    "向不特定对象发行可转换公司债券",
    "保荐人",
    "主承销商",
}
_IDENTITY_GENERIC_CHARS_RE = re.compile(r"[年度报告全文股份有限公司集团]")
_CONTRACT_IDENTITY_NOISE = (
    "募集说明书",
    "公司债券",
    "可转换公司债券",
    "保荐",
    "主承销",
    "股票简称",
    "证券简称",
    "股票代码",
    "证券代码",
    "注册稿",
    "草案",
)

_GENERIC_TERMS = {
    "根据", "结合", "依据", "关于", "以下", "下列", "哪些", "哪个", "判断", "描述",
    "说法", "陈述", "正确", "错误", "准确", "符合", "事实", "报告", "年度报告", "数据",
    "公司", "产品", "规定", "条款", "情况", "进行", "是否", "其中", "分别", "给出",
    "多选题", "判断题", "财务指标对比分析", "财务数据一致性校验",
}

_DOMAIN_SIGNAL_TERMS: Mapping[str, tuple[str, ...]] = {
    "financial_reports": (
        "营业收入", "营业总收入", "净利润", "归母净利润", "现金流量净额", "研发投入",
        "现金分红", "股份回购", "资产负债率", "毛利率", "年度报告",
    ),
    "financial_contracts": (
        "发行人", "债券", "募集说明书", "募集资金", "承销", "担保", "回售", "赎回",
        "票面利率", "偿债", "科技创新公司债券",
    ),
    "insurance": (
        "保险", "保险责任", "责任免除", "等待期", "犹豫期", "退保", "现金价值", "赔付",
        "给付", "免赔额", "投保人", "被保险人", "受益人",
    ),
    "regulatory": (
        "办法", "规定", "条例", "细则", "指引", "通知", "决定", "准则", "规则", "法律",
        "中国人民银行", "国家金融监督管理总局", "中国证券监督管理委员会", "证监会",
    ),
    "research": (
        "行业", "市场", "规模", "增速", "渗透率", "预测", "预计", "空间", "竞争格局",
        "产业链", "需求", "供给", "市场份额",
    ),
}


@dataclass(frozen=True)
class QuerySignals:
    terms: tuple[str, ...]
    years: tuple[str, ...]
    codes: tuple[str, ...]
    chunks: tuple[str, ...]


@dataclass(frozen=True)
class DocumentCandidate:
    doc_id: str
    domain: str
    score: float
    rank: int
    matched_terms: tuple[str, ...]
    matched_title_terms: tuple[str, ...]
    source_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "domain": self.domain,
            "score": self.score,
            "rank": self.rank,
            "matched_terms": list(self.matched_terms),
            "matched_title_terms": list(self.matched_title_terms),
            "source_paths": list(self.source_paths),
        }


@dataclass(frozen=True)
class DocumentScopeResult:
    qid: str
    domain: str
    candidate_doc_ids: tuple[str, ...]
    candidates: tuple[DocumentCandidate, ...]
    query_terms: tuple[str, ...]
    strategy: str
    provider_calls: int
    warnings: tuple[str, ...]
    effective_top_k: int = 0
    adaptive_scope: bool = False
    confidence: str = "unknown"
    matched_identity_terms: tuple[str, ...] = ()
    coverage_groups: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "qid": self.qid,
            "domain": self.domain,
            "candidate_doc_ids": list(self.candidate_doc_ids),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "query_terms": list(self.query_terms),
            "strategy": self.strategy,
            "provider_calls": self.provider_calls,
            "warnings": list(self.warnings),
            "effective_top_k": self.effective_top_k,
            "adaptive_scope": self.adaptive_scope,
            "confidence": self.confidence,
            "matched_identity_terms": list(self.matched_identity_terms),
            "coverage_groups": [dict(group) for group in self.coverage_groups],
        }


@dataclass(frozen=True)
class _IdentityScopePlan:
    score_bonus_by_doc: Mapping[str, float]
    identity_terms_by_doc: Mapping[str, tuple[str, ...]]
    matched_identity_terms: tuple[str, ...]
    coverage_groups: tuple[Mapping[str, object], ...]
    effective_top_k: int
    adaptive_scope: bool


class DocumentScopeResolver:
    """Resolve empty-doc-id questions into ranked candidate documents."""

    def __init__(
        self,
        catalog: DocumentCatalog,
        *,
        top_k: int = 5,
        max_top_k: int = 10,
        recall_pool_size: int = 10,
        strategy: str = "deterministic_lexical_v1",
        min_score: float = 1.0,
        insurance_product_catalog_path: Path | None = None,
        weak_scope_min_score: float = 18.0,
        weak_scope_min_margin: float = 2.0,
    ) -> None:
        self.catalog = catalog
        self.top_k = max(1, int(top_k))
        self.max_top_k = max(self.top_k, int(max_top_k))
        self.recall_pool_size = max(self.max_top_k, int(recall_pool_size))
        self.strategy = str(strategy or "deterministic_lexical_v1")
        self.min_score = float(min_score)
        self.weak_scope_min_score = float(weak_scope_min_score)
        self.weak_scope_min_margin = float(weak_scope_min_margin)
        self.insurance_product_aliases = _load_insurance_product_aliases(
            insurance_product_catalog_path
        )
        self.financial_report_groups = _build_financial_report_groups(
            catalog.entries_for_domain("financial_reports")
        )

    def resolve(
        self,
        question: Question,
        classification: ClassificationResult,
    ) -> DocumentScopeResult:
        """Return a ranked candidate scope without consulting ``question.doc_ids``.

        The resolver intentionally ignores ``question.doc_ids`` even if a caller
        accidentally supplies them.  Masked legacy evaluation therefore uses
        exactly the same query-visible fields as multi-slot input: question text,
        options, domain, type, and classifier labels.
        """
        signals = extract_query_signals(question, classification)
        entries = self.catalog.entries_for_domain(question.domain)
        warnings: list[str] = []
        if not entries:
            warnings.append("domain_catalog_empty")
            return DocumentScopeResult(
                qid=question.qid,
                domain=question.domain,
                candidate_doc_ids=(),
                candidates=(),
                query_terms=signals.terms,
                strategy=self.strategy,
                provider_calls=0,
                warnings=tuple(warnings),
            )

        identity_plan = self._build_identity_scope_plan(question, signals, entries)
        phrase_doc_freq = _phrase_document_frequency(entries, signals)
        scored: list[tuple[float, DocumentCatalogEntry, tuple[str, ...], tuple[str, ...]]] = []
        for entry in entries:
            score, matched_terms, matched_title_terms = _score_entry(
                entry,
                question,
                signals,
                phrase_doc_freq=phrase_doc_freq,
                domain_doc_count=len(entries),
            )
            identity_bonus = float(identity_plan.score_bonus_by_doc.get(entry.doc_id, 0.0))
            if identity_bonus:
                score += identity_bonus
                matched_title_terms = _dedup(
                    [
                        *matched_title_terms,
                        *identity_plan.identity_terms_by_doc.get(entry.doc_id, ()),
                    ]
                )[:20]
            if score >= self.min_score:
                scored.append((score, entry, matched_terms, matched_title_terms))

        # Stable deterministic tie-break by doc_id.  V2 may expand the output
        # only when visible entity/year/product structure requires broader
        # coverage; candidate scope remains retrieval scope, never required truth.
        scored.sort(key=lambda item: (-item[0], item[1].doc_id))
        scored = scored[: self.recall_pool_size]
        effective_top_k = max(
            self.top_k,
            min(self.max_top_k, int(identity_plan.effective_top_k or self.top_k)),
        )
        top = scored[:effective_top_k]
        if not top:
            warnings.append("no_candidate_above_threshold")

        top1_score = float(top[0][0]) if top else 0.0
        top2_score = float(top[1][0]) if len(top) >= 2 else 0.0
        margin = top1_score - top2_score if top else 0.0
        has_identity = bool(identity_plan.matched_identity_terms)
        weak_scope = bool(
            top
            and not has_identity
            and (
                top1_score < self.weak_scope_min_score
                or margin < self.weak_scope_min_margin
            )
        )
        if weak_scope:
            warnings.append("weak_scope")
        if identity_plan.adaptive_scope:
            warnings.append("adaptive_scope")
        confidence = (
            "none"
            if not top
            else "high"
            if has_identity and margin >= self.weak_scope_min_margin
            else "medium"
            if has_identity or not weak_scope
            else "low"
        )

        candidates = tuple(
            DocumentCandidate(
                doc_id=entry.doc_id,
                domain=entry.domain,
                score=round(score, 6),
                rank=index + 1,
                matched_terms=matched_terms,
                matched_title_terms=matched_title_terms,
                source_paths=entry.source_paths,
            )
            for index, (score, entry, matched_terms, matched_title_terms) in enumerate(top)
        )
        return DocumentScopeResult(
            qid=question.qid,
            domain=question.domain,
            candidate_doc_ids=tuple(candidate.doc_id for candidate in candidates),
            candidates=candidates,
            query_terms=signals.terms,
            strategy=self.strategy,
            provider_calls=0,
            warnings=_dedup(warnings),
            effective_top_k=effective_top_k,
            adaptive_scope=identity_plan.adaptive_scope,
            confidence=confidence,
            matched_identity_terms=identity_plan.matched_identity_terms,
            coverage_groups=identity_plan.coverage_groups,
        )

    def _build_identity_scope_plan(
        self,
        question: Question,
        signals: QuerySignals,
        entries: Sequence[DocumentCatalogEntry],
    ) -> _IdentityScopePlan:
        if question.domain == "financial_reports":
            return _financial_report_identity_plan(
                question,
                signals,
                self.financial_report_groups,
                base_top_k=self.top_k,
                max_top_k=self.max_top_k,
            )
        if question.domain == "financial_contracts":
            return _financial_contract_identity_plan(
                question,
                entries,
                base_top_k=self.top_k,
                max_top_k=self.max_top_k,
            )
        if question.domain == "insurance":
            return _insurance_identity_plan(
                question,
                self.insurance_product_aliases,
                base_top_k=self.top_k,
                max_top_k=self.max_top_k,
            )
        if question.domain == "regulatory":
            return _regulatory_identity_plan(
                question,
                entries,
                base_top_k=self.top_k,
                max_top_k=self.max_top_k,
            )
        if question.domain == "research":
            return _research_topic_plan(
                question,
                entries,
                base_top_k=self.top_k,
                max_top_k=self.max_top_k,
            )
        return _empty_identity_plan(self.top_k)


def extract_query_signals(
    question: Question,
    classification: ClassificationResult,
) -> QuerySignals:
    """Extract weighted document-level query signals from multi-slot-visible fields."""
    raw_type = str(question.raw.get("type") or question.raw.get("_raw_type") or "")
    parts = [question.text, *question.options.values(), raw_type]
    joined = "\n".join(str(part) for part in parts if str(part).strip())

    years = _dedup(_YEAR_RE.findall(joined))
    codes = _dedup(_CODE_RE.findall(joined))

    chunks: list[str] = []
    for part in parts:
        for raw_chunk in _SPLIT_RE.split(str(part)):
            chunk = raw_chunk.strip()
            compact = _compact(chunk)
            if not compact or compact in _GENERIC_TERMS:
                continue
            # Keep bounded whole clauses because options often contain precise
            # product/entity/value wording that is useful for document-level
            # retrieval. Also split common relation words to retain topic phrases
            # such as “银保渠道发展历程” and “银行IT市场规模”.
            if 2 <= len(compact) <= 60:
                chunks.append(chunk)
            for fragment in _RELATION_SPLIT_RE.split(chunk):
                fragment_compact = _compact(fragment)
                if (
                    3 <= len(fragment_compact) <= 28
                    and fragment_compact not in _GENERIC_TERMS
                ):
                    chunks.append(fragment)

    terms: list[str] = []
    terms.extend(years)
    terms.extend(codes)
    for term in _DOMAIN_SIGNAL_TERMS.get(question.domain, ()):
        if term in joined:
            terms.append(term)
    terms.extend(chunks)
    for label in classification.labels:
        terms.append(str(getattr(label, "value", label)))

    return QuerySignals(
        terms=_dedup(terms)[:80],
        years=years,
        codes=codes,
        chunks=_dedup(chunks)[:60],
    )


def _empty_identity_plan(base_top_k: int) -> _IdentityScopePlan:
    return _IdentityScopePlan(
        score_bonus_by_doc={},
        identity_terms_by_doc={},
        matched_identity_terms=(),
        coverage_groups=(),
        effective_top_k=max(1, int(base_top_k)),
        adaptive_scope=False,
    )


def _load_insurance_product_aliases(
    path: Path | None,
) -> dict[str, tuple[str, ...]]:
    if path is None or not Path(path).is_file():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    documents = payload.get("documents")
    if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for row in documents:
        if not isinstance(row, Mapping):
            continue
        doc_id = str(row.get("document_id") or "").strip()
        if not doc_id:
            continue
        values: list[str] = []
        product_name = str(row.get("product_name") or "").strip()
        if product_name:
            values.append(product_name)
        aliases = row.get("aliases")
        if isinstance(aliases, Sequence) and not isinstance(aliases, (str, bytes)):
            values.extend(str(value).strip() for value in aliases if str(value).strip())
        result[doc_id] = _dedup(values)
    return result


def _derive_entity_aliases(entry: DocumentCatalogEntry) -> tuple[str, ...]:
    values: list[str] = list(entry.title_aliases)
    suffixes = (
        "集团股份有限公司",
        "股份有限公司",
        "有限责任公司",
        "集团有限公司",
        "有限公司",
        "股份",
    )
    noise_suffixes = ("新能源科技", "科技创新")
    for raw in list(values):
        compact = _compact(raw)
        compact = re.sub(r"(?:19|20)\d{2}年?年?度报告(?:全文)?.*$", "", compact)
        if not compact:
            continue
        values.append(compact)
        current = compact
        changed = True
        while changed:
            changed = False
            for suffix in suffixes:
                if current.endswith(suffix) and len(current) > len(suffix) + 1:
                    current = current[: -len(suffix)]
                    values.append(current)
                    changed = True
                    break
        if current.endswith("集团") and len(current) > 4:
            # Keep both 美的集团 and 美的; the longer alias is preferred when
            # the question explicitly names it.
            values.append(current[: -len("集团")])
        for suffix in noise_suffixes:
            if current.endswith(suffix) and len(current) > len(suffix) + 1:
                values.append(current[: -len(suffix)])

    aliases: list[str] = []
    for value in values:
        compact = _compact(value)
        identity_core = _IDENTITY_GENERIC_CHARS_RE.sub("", compact)
        if (
            2 <= len(compact) <= 24
            and len(identity_core) >= 2
            and re.search(r"[\u4e00-\u9fff]", compact)
            and not re.search(r"\d", compact)
            and compact not in _IDENTITY_ALIAS_NOISE
            and "年度报告" not in compact
        ):
            aliases.append(compact)
    return _dedup(aliases)


def _build_financial_report_groups(
    entries: Sequence[DocumentCatalogEntry],
) -> dict[str, Mapping[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    for entry in entries:
        match = _FINANCIAL_REPORT_DOC_RE.match(entry.doc_id)
        if match is None:
            continue
        key = match.group("entity").lower()
        group = groups.setdefault(key, {"documents": [], "aliases": []})
        group["documents"].append((entry.doc_id, match.group("year")))
        group["aliases"].extend(_derive_entity_aliases(entry))
    normalized: dict[str, Mapping[str, object]] = {}
    for key, group in groups.items():
        normalized[key] = {
            "documents": tuple(sorted(group["documents"])),
            "aliases": _dedup(group["aliases"]),
        }
    return normalized


def _question_visible_text(question: Question) -> str:
    return "\n".join(
        [
            question.text,
            *[str(value) for value in question.options.values()],
            str(question.raw.get("type") or question.raw.get("_raw_type") or ""),
        ]
    )


def _financial_report_identity_plan(
    question: Question,
    signals: QuerySignals,
    groups: Mapping[str, Mapping[str, object]],
    *,
    base_top_k: int,
    max_top_k: int,
) -> _IdentityScopePlan:
    query = _compact(_question_visible_text(question))
    score_bonus: dict[str, float] = {}
    identity_by_doc: dict[str, tuple[str, ...]] = {}
    matched_terms: list[str] = []
    coverage_groups: list[Mapping[str, object]] = []
    covered_docs: set[str] = set()
    matched_group_count = 0

    for group_key, group in groups.items():
        aliases = tuple(str(value) for value in group.get("aliases", ()))
        matched_aliases = [alias for alias in aliases if alias and alias in query]
        if not matched_aliases:
            continue
        matched_group_count += 1
        identity = max(matched_aliases, key=lambda value: (len(value), value))
        matched_terms.append(identity)
        documents = tuple(group.get("documents", ()))
        selected = [
            (str(doc_id), str(year))
            for doc_id, year in documents
            if not signals.years or str(year) in set(signals.years)
        ]
        if not selected:
            selected = [(str(doc_id), str(year)) for doc_id, year in documents]
        group_doc_ids: list[str] = []
        for doc_id, year in selected:
            bonus = 120.0 + (30.0 if year in signals.years else 0.0)
            score_bonus[doc_id] = max(score_bonus.get(doc_id, 0.0), bonus)
            identity_by_doc[doc_id] = _dedup([*identity_by_doc.get(doc_id, ()), identity, year])
            covered_docs.add(doc_id)
            group_doc_ids.append(doc_id)
        coverage_groups.append(
            {
                "kind": "entity_year",
                "group_key": group_key,
                "identity": identity,
                "years": list(signals.years),
                "doc_ids": group_doc_ids,
            }
        )

    effective = max(base_top_k, min(max_top_k, len(covered_docs)))
    adaptive = matched_group_count >= 2 or len(covered_docs) > base_top_k
    return _IdentityScopePlan(
        score_bonus_by_doc=score_bonus,
        identity_terms_by_doc=identity_by_doc,
        matched_identity_terms=_dedup(matched_terms),
        coverage_groups=tuple(coverage_groups),
        effective_top_k=effective,
        adaptive_scope=adaptive,
    )


def _financial_contract_identity_plan(
    question: Question,
    entries: Sequence[DocumentCatalogEntry],
    *,
    base_top_k: int,
    max_top_k: int,
) -> _IdentityScopePlan:
    """Promote contract documents whose corpus-derived issuer aliases are named.

    Prospectus questions often name several issuers/products in the options. The
    generic lexical score can still crowd one named document out with documents
    sharing boilerplate such as ``募集说明书``. Treat exact issuer/short-name
    aliases as document identity, analogous to insurance product aliases.
    """
    query = _compact(_question_visible_text(question))
    score_bonus: dict[str, float] = {}
    identity_by_doc: dict[str, tuple[str, ...]] = {}
    matched_terms: list[str] = []
    coverage_groups: list[Mapping[str, object]] = []

    for entry in entries:
        identities: list[str] = []
        for alias in _derive_entity_aliases(entry):
            compact_alias = _compact(alias)
            if len(compact_alias) < 3 or compact_alias not in query:
                continue
            if any(noise in compact_alias for noise in _CONTRACT_IDENTITY_NOISE):
                continue
            identities.append(compact_alias)
        if not identities:
            continue
        identity = max(_dedup(identities), key=lambda value: (len(value), value))
        score_bonus[entry.doc_id] = 140.0
        identity_by_doc[entry.doc_id] = (identity,)
        matched_terms.append(identity)
        coverage_groups.append(
            {"kind": "contract_issuer", "identity": identity, "doc_ids": [entry.doc_id]}
        )

    effective = max(base_top_k, min(max_top_k, len(score_bonus)))
    return _IdentityScopePlan(
        score_bonus_by_doc=score_bonus,
        identity_terms_by_doc=identity_by_doc,
        matched_identity_terms=_dedup(matched_terms),
        coverage_groups=tuple(coverage_groups),
        effective_top_k=effective,
        adaptive_scope=len(score_bonus) > 1,
    )


def _insurance_identity_plan(
    question: Question,
    product_aliases: Mapping[str, tuple[str, ...]],
    *,
    base_top_k: int,
    max_top_k: int,
) -> _IdentityScopePlan:
    query = _compact(_question_visible_text(question))
    score_bonus: dict[str, float] = {}
    identity_by_doc: dict[str, tuple[str, ...]] = {}
    matched_terms: list[str] = []
    coverage_groups: list[Mapping[str, object]] = []
    for doc_id, aliases in product_aliases.items():
        normalized = [_compact(alias) for alias in aliases if _compact(alias)]
        matched = [alias for alias in normalized if alias in query]
        if not matched:
            continue
        identity = max(matched, key=lambda value: (len(value), value))
        score_bonus[doc_id] = 140.0
        identity_by_doc[doc_id] = (identity,)
        matched_terms.append(identity)
        coverage_groups.append(
            {"kind": "insurance_product", "identity": identity, "doc_ids": [doc_id]}
        )
    effective = max(base_top_k, min(max_top_k, len(score_bonus)))
    return _IdentityScopePlan(
        score_bonus_by_doc=score_bonus,
        identity_terms_by_doc=identity_by_doc,
        matched_identity_terms=_dedup(matched_terms),
        coverage_groups=tuple(coverage_groups),
        effective_top_k=effective,
        adaptive_scope=len(score_bonus) > base_top_k,
    )


def _regulatory_identity_plan(
    question: Question,
    entries: Sequence[DocumentCatalogEntry],
    *,
    base_top_k: int,
    max_top_k: int,
) -> _IdentityScopePlan:
    query = _compact(_question_visible_text(question))
    score_bonus: dict[str, float] = {}
    identity_by_doc: dict[str, tuple[str, ...]] = {}
    matched_terms: list[str] = []
    coverage_groups: list[Mapping[str, object]] = []
    for entry in entries:
        identities: list[str] = []
        for raw_alias in entry.title_aliases:
            alias = _compact(raw_alias)
            if not alias:
                continue
            for suffix in _REGULATORY_TITLE_SUFFIXES:
                if alias.endswith(suffix) and len(alias) > len(suffix) + 2:
                    stem = alias[: -len(suffix)]
                    if len(stem) >= 4 and stem in query:
                        identities.append(stem)
                    if len(alias) <= 36 and alias in query:
                        identities.append(alias)
        if not identities:
            continue
        identity = max(_dedup(identities), key=lambda value: (len(value), value))
        score_bonus[entry.doc_id] = 160.0
        identity_by_doc[entry.doc_id] = (identity,)
        matched_terms.append(identity)
        coverage_groups.append(
            {"kind": "regulatory_title_or_object", "identity": identity, "doc_ids": [entry.doc_id]}
        )
    return _IdentityScopePlan(
        score_bonus_by_doc=score_bonus,
        identity_terms_by_doc=identity_by_doc,
        matched_identity_terms=_dedup(matched_terms),
        coverage_groups=tuple(coverage_groups),
        effective_top_k=base_top_k,
        adaptive_scope=False,
    )


def _research_topic_plan(
    question: Question,
    entries: Sequence[DocumentCatalogEntry],
    *,
    base_top_k: int,
    max_top_k: int,
) -> _IdentityScopePlan:
    topics = _dedup(
        fragment.strip()
        for fragment in _TOPIC_SPLIT_RE.split(question.text)
        if 5 <= len(_compact(fragment)) <= 36
    )
    score_bonus: dict[str, float] = {}
    identity_by_doc: dict[str, tuple[str, ...]] = {}
    coverage_groups: list[Mapping[str, object]] = []
    for topic in topics[:8]:
        compact_topic = _compact(topic)
        matches: list[str] = []
        for entry in entries:
            haystack = _compact(
                "\n".join(
                    [entry.title, *entry.title_aliases, entry.identity_text, entry.lexical_profile]
                )
            )
            if compact_topic and compact_topic in haystack:
                matches.append(entry.doc_id)
        # A rare exact topic hit is useful for diversification. Keep the bonus
        # deliberately small so masked-A lexical behavior remains stable.
        if 1 <= len(matches) <= 2:
            for doc_id in matches:
                score_bonus[doc_id] = score_bonus.get(doc_id, 0.0) + 8.0
                identity_by_doc[doc_id] = _dedup(
                    [*identity_by_doc.get(doc_id, ()), topic]
                )
            coverage_groups.append(
                {"kind": "research_topic", "identity": topic, "doc_ids": matches}
            )
    return _IdentityScopePlan(
        score_bonus_by_doc=score_bonus,
        identity_terms_by_doc=identity_by_doc,
        # Topic decomposition is not document identity, so it must not upgrade
        # confidence or suppress weak-scope warnings.
        matched_identity_terms=(),
        coverage_groups=tuple(coverage_groups),
        effective_top_k=base_top_k,
        adaptive_scope=False,
    )


def _phrase_document_frequency(
    entries: Sequence[DocumentCatalogEntry],
    signals: QuerySignals,
) -> dict[str, int]:
    """Count phrase presence inside the current domain for IDF-like weighting."""
    phrases = {
        compact
        for compact in (_compact(value) for value in signals.chunks)
        if len(compact) >= 4 and compact not in _GENERIC_TERMS
    }
    frequencies = {phrase: 0 for phrase in phrases}
    if not phrases:
        return frequencies
    for entry in entries:
        haystack = _compact(
            "\n".join(
                [
                    entry.title,
                    *entry.title_aliases,
                    entry.identity_text,
                    entry.lexical_profile,
                ]
            )
        )
        for phrase in phrases:
            if phrase in haystack:
                frequencies[phrase] += 1
    return frequencies


def _score_entry(
    entry: DocumentCatalogEntry,
    question: Question,
    signals: QuerySignals,
    *,
    phrase_doc_freq: Mapping[str, int],
    domain_doc_count: int,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    raw_type = str(question.raw.get("type") or question.raw.get("_raw_type") or "")
    query_text = "\n".join([question.text, *question.options.values(), raw_type])
    query_compact = _compact(query_text)
    title_compact = _compact(" ".join([entry.title, entry.doc_id, *entry.title_aliases]))
    identity_compact = _compact(entry.identity_text)
    lexical_compact = _compact(entry.lexical_profile or entry.identity_text)

    score = 0.0
    matched_terms: list[str] = []
    matched_title_terms: list[str] = []

    # 1) Exact catalog alias match is the strongest signal.  Aliases are derived
    # only from doc id/title/first-page identity, never from answer labels.
    seen_aliases: set[str] = set()
    for alias in entry.title_aliases:
        compact_alias = _compact(alias)
        if len(compact_alias) < 2 or compact_alias in seen_aliases:
            continue
        seen_aliases.add(compact_alias)
        if compact_alias in query_compact:
            alias_len = len(compact_alias)
            if alias_len >= 12:
                bonus = 42.0
            elif alias_len >= 8:
                bonus = 34.0
            elif alias_len >= 5:
                bonus = 26.0
            elif alias_len >= 3:
                bonus = 18.0
            else:
                bonus = 10.0
            score += bonus
            matched_title_terms.append(alias)

    # 2) Year / securities-code matches.  Require presence in title/doc id when
    # possible so generic dates in a first page do not dominate ranking.
    for year in signals.years:
        if year in title_compact:
            score += 15.0
            matched_terms.append(year)
        elif year in identity_compact:
            score += 5.0
            matched_terms.append(year)
    for code in signals.codes:
        if code in title_compact:
            score += 30.0
            matched_terms.append(code)
        elif code in identity_compact:
            score += 16.0
            matched_terms.append(code)

    # 3) Domain-specific and extracted phrase overlap.  Longer phrases carry
    # more identity information; generic option wording is down-weighted.
    for term in signals.terms:
        compact_term = _compact(term)
        if not compact_term or compact_term in _GENERIC_TERMS:
            continue
        if compact_term in title_compact:
            bonus = min(12.0, 2.0 + len(compact_term) * 1.2)
            score += bonus
            matched_title_terms.append(term)
        elif len(compact_term) >= 3 and compact_term in identity_compact:
            bonus = min(6.0, 1.0 + len(compact_term) * 0.45)
            score += bonus
            matched_terms.append(term)

    # Document-wide lexical profile is a lower-priority recall layer.  Exact
    # bounded phrases are useful for generic-cover research reports, while the
    # title/identity layers above remain more heavily weighted.
    for phrase in signals.chunks:
        compact_phrase = _compact(phrase)
        if len(compact_phrase) < 4 or compact_phrase in _GENERIC_TERMS:
            continue
        if compact_phrase in title_compact or compact_phrase in identity_compact:
            continue
        if compact_phrase in lexical_compact:
            doc_freq = phrase_doc_freq.get(compact_phrase, domain_doc_count)
            if doc_freq <= 1:
                rarity_bonus = 10.0
            elif doc_freq <= 2:
                rarity_bonus = 8.0
            elif doc_freq <= max(3, int(domain_doc_count * 0.2)):
                rarity_bonus = 5.0
            elif doc_freq <= max(5, int(domain_doc_count * 0.5)):
                rarity_bonus = 2.0
            else:
                rarity_bonus = 0.5
            bonus = min(22.0, 2.0 + len(compact_phrase) * 0.3 + rarity_bonus)
            score += bonus
            matched_terms.append(phrase)

    # 4) Character n-gram containment gives deterministic fuzzy recall for
    # abbreviations and slight title wording differences without embeddings.
    q_grams = _char_ngrams(query_compact, sizes=(2, 3))
    title_grams = _char_ngrams(title_compact, sizes=(2, 3))
    identity_grams = _char_ngrams(identity_compact[:4000], sizes=(2, 3))
    if q_grams and title_grams:
        overlap = len(q_grams & title_grams)
        containment = overlap / max(1, min(len(q_grams), len(title_grams)))
        score += min(18.0, overlap * 0.18 + containment * 10.0)
    if q_grams and identity_grams:
        overlap = len(q_grams & identity_grams)
        containment = overlap / max(1, len(q_grams))
        score += min(10.0, overlap * 0.05 + containment * 8.0)

    # Small exact doc-id fragment bonus for semantic ids such as annual_byd_2024.
    for token in re.split(r"[_\-]+", entry.doc_id.lower()):
        if len(token) >= 3 and token in query_compact:
            score += min(6.0, 1.5 + len(token) * 0.5)
            matched_title_terms.append(token)

    return (
        score,
        _dedup(matched_terms)[:20],
        _dedup(matched_title_terms)[:20],
    )


def _char_ngrams(text: str, *, sizes: Sequence[int]) -> set[str]:
    result: set[str] = set()
    for size in sizes:
        if len(text) < size:
            continue
        result.update(text[index : index + size] for index in range(len(text) - size + 1))
    return result


def _compact(value: str) -> str:
    return _COMPACT_RE.sub("", str(value or "").lower())


def _dedup(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)
