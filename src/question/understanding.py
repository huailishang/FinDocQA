"""C1: deterministic query understanding for structured and natural questions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
from typing import Any, Mapping, Sequence

from answer_contract import build_question_answer_contract, contract_to_dict
from contracts import Question


_KNOWN_DOMAINS = {
    "financial_contracts",
    "financial_reports",
    "insurance",
    "regulatory",
    "research",
}

_OFFICIAL_TYPE_TO_BASE = {
    "单选题": "single_choice",
    "多选题": "multi_choice",
    "判断题": "judgement",
    "计算题": "calculation",
    "抽取题": "extraction",
}

_DOMAIN_TERMS: Mapping[str, tuple[str, ...]] = {
    "insurance": (
        "保险", "保单", "投保", "被保险人", "保险人", "受益人", "理赔", "赔付", "给付",
        "免赔", "等待期", "现金价值", "退保", "保险责任", "责任免除",
    ),
    "financial_contracts": (
        "募集说明书", "债券", "发行人", "承销", "票面利率", "兑付", "募集资金", "契约",
        "债务融资", "信贷协议", "授信协议", "借款合同",
    ),
    "regulatory": (
        "监管", "监管要求", "法规", "条例", "办法", "规定", "准则", "证监会", "银保监",
        "金融监管总局", "人民银行", "应当", "不得", "合规",
    ),
    "financial_reports": (
        "年报", "年度报告", "财报", "财务报告", "资产负债表", "利润表", "现金流量表",
        "营业收入", "营收", "收入", "营业利润", "净利润", "利润", "盈利", "赚了", "总资产",
        "资产", "负债", "净资产", "分红", "每股收益", "不良贷款率", "资本充足率",
        "拨备覆盖率", "研发费用", "毛利率", "净利率", "ROE", "净资产收益率",
    ),
    "research": (
        "研报", "研究报告", "行业报告", "市场规模", "市场份额", "渗透率", "行业", "预测",
        "CAGR", "复合增长率", "产业链", "景气度",
    ),
}

_CALCULATION_TERMS = (
    "计算", "算出", "求出", "合计", "总计", "增长率", "同比增长", "环比增长", "占比",
    "比例", "百分点", "平均", "差额", "相差", "增长多少", "减少多少", "增加多少",
)
_COMPARISON_TERMS = (
    "比较", "对比", "相比", "高于", "低于", "大于", "小于", "更高", "更低", "超过",
    "哪家更", "哪个更", "是否上升", "是否下降", "上升了吗", "下降了吗",
)
_RANKING_TERMS = (
    "排序", "排名", "从高到低", "从低到高", "由高到低", "由低到高", "依次排列",
)
_NEGATION_TERMS = (
    "不正确", "错误的是", "不符合", "不包括", "不属于", "不是", "不得", "不能", "禁止",
    "无需", "无须", "未",
)
_CONDITION_TERMS = (
    "除外", "除非", "例外", "仅当", "只有", "前提", "条件", "情况下", "如果", "若",
)
_ANALYSIS_TERMS = (
    "为什么", "原因", "影响", "怎么看", "如何理解", "分析", "趋势", "驱动因素", "主要因素",
)
_STRONG_CROSS_DOC_TERMS = (
    "两份文档", "多个文档", "多份文档", "跨文档", "分别根据", "分别结合", "结合两份",
    "两份报告", "两份年报", "两个报告", "两家公司", "多家公司",
)
_BOOLEAN_PATTERNS = (
    "是否", "能否", "可否", "有没有", "是不是", "对不对", "正确吗", "符合吗", "上升了吗",
    "下降了吗", "增加了吗", "减少了吗",
)
_NUMBER_PATTERNS = (
    "多少", "金额", "比例", "比率", "率是多少", "数值", "百分点", "几倍", "几元", "几万元",
    "几亿元",
)
_TEMPORAL_TERMS = (
    "截至", "期间", "年度", "年末", "季度", "月末", "同比", "环比", "报告期", "去年", "前年",
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年?")
_DATE_RE = re.compile(r"(?:19|20)\d{2}[年./-]\d{1,2}(?:[月./-]\d{1,2}日?)?")


@dataclass(frozen=True)
class QueryUnderstandingResult:
    domain: str
    base_type: str
    answer_shape: str
    traits: tuple[str, ...]
    confidence: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["traits"] = list(self.traits)
        return payload


class RuleBasedQueryUnderstanding:
    """Infer missing routing metadata from visible question text only.

    Explicit dataset metadata remains authoritative. Inference is deliberately
    conservative so a real user query can enter the same pipeline without
    pretending to know a competition-only field such as A/B/C/D format.
    """

    def understand(self, question: Question) -> QueryUnderstandingResult:
        text = self._joined_text(question)
        raw_type = str(question.raw.get("type") or question.raw.get("question_type") or "").strip()
        reasons: list[str] = []

        domain, domain_confidence, domain_reason = self._infer_domain(question.domain, text)
        reasons.append(domain_reason)

        base_type, type_confidence, type_reason = self._infer_base_type(
            raw_type=raw_type,
            answer_format=str(question.answer_format or "").strip().lower(),
            text=text,
            has_options=bool(question.options),
        )
        reasons.append(type_reason)

        traits: list[str] = []
        self._add_trait(traits, reasons, "calculation", base_type == "calculation", "base_type")
        self._add_trait(traits, reasons, "comparison", self._has_any(text, _COMPARISON_TERMS), "text")
        self._add_trait(traits, reasons, "ranking", base_type == "ranking" or self._has_any(text, _RANKING_TERMS), "text")
        self._add_trait(traits, reasons, "negation", self._has_any(text, _NEGATION_TERMS), "text")
        self._add_trait(traits, reasons, "exception_or_condition", self._has_any(text, _CONDITION_TERMS), "text")
        self._add_trait(
            traits,
            reasons,
            "temporal_scope",
            self._has_any(text, _TEMPORAL_TERMS) or bool(_YEAR_RE.search(text)) or bool(_DATE_RE.search(text)),
            "text",
        )
        cross_document = len(tuple(question.doc_ids or ())) >= 2 or self._has_any(text, _STRONG_CROSS_DOC_TERMS)
        self._add_trait(traits, reasons, "cross_document", cross_document, "explicit_docs_or_strong_text")

        answer_shape = self._infer_answer_shape(base_type, text, bool(question.options))
        reasons.append(f"answer_shape:{answer_shape}")

        confidence = round(max(0.0, min(1.0, (domain_confidence + type_confidence) / 2.0)), 4)
        return QueryUnderstandingResult(
            domain=domain,
            base_type=base_type,
            answer_shape=answer_shape,
            traits=tuple(traits),
            confidence=confidence,
            reasons=tuple(reasons),
        )

    def materialize(self, question: Question, result: QueryUnderstandingResult | None = None) -> Question:
        """Attach C1 output and fill only metadata that was absent at input time."""

        result = result or self.understand(question)
        raw = dict(question.raw or {})
        raw["_query_understanding"] = result.to_dict()

        domain = question.domain
        if not domain or domain == "unknown":
            domain = result.domain

        answer_format = question.answer_format
        if (not answer_format or answer_format == "unknown") and not question.options:
            answer_format = "freeform"

        contract = question.answer_contract
        if contract is None or contract.answer_format != answer_format:
            contract = build_question_answer_contract(
                qid=question.qid,
                raw_type=raw.get("type") or raw.get("question_type"),
                raw_answer_format=answer_format,
                options=question.options,
            )
            raw["_answer_contract"] = contract_to_dict(contract)

        return replace(
            question,
            domain=domain,
            answer_format=answer_format,
            raw=raw,
            answer_contract=contract,
        )

    @staticmethod
    def _infer_domain(explicit_domain: str, text: str) -> tuple[str, float, str]:
        explicit = str(explicit_domain or "").strip()
        if explicit in _KNOWN_DOMAINS:
            return explicit, 1.0, f"domain:explicit={explicit}"

        scores = {
            domain: sum(1 for term in terms if term and term.lower() in text.lower())
            for domain, terms in _DOMAIN_TERMS.items()
        }
        best_domain, best_score = max(scores.items(), key=lambda item: (item[1], item[0]))
        if best_score <= 0:
            return "unknown", 0.4, "domain:unknown"
        tied = [domain for domain, score in scores.items() if score == best_score]
        if len(tied) > 1:
            return "unknown", 0.5, "domain:ambiguous=" + ",".join(sorted(tied))
        confidence = 0.82 if best_score >= 2 else 0.72
        return best_domain, confidence, f"domain:inferred={best_domain};hits={best_score}"

    @staticmethod
    def _infer_base_type(
        *,
        raw_type: str,
        answer_format: str,
        text: str,
        has_options: bool,
    ) -> tuple[str, float, str]:
        if raw_type in _OFFICIAL_TYPE_TO_BASE:
            return _OFFICIAL_TYPE_TO_BASE[raw_type], 1.0, f"type:explicit={raw_type}"
        if answer_format in {"mcq", "single", "single_choice"}:
            return "single_choice", 0.95, f"type:answer_format={answer_format}"
        if answer_format == "multi":
            return "multi_choice", 0.95, "type:answer_format=multi"
        if answer_format == "tf" and has_options:
            return "judgement", 0.95, "type:answer_format=tf"

        if RuleBasedQueryUnderstanding._has_any(text, _RANKING_TERMS):
            return "ranking", 0.86, "type:inferred=ranking"
        if RuleBasedQueryUnderstanding._has_any(text, _CALCULATION_TERMS):
            return "calculation", 0.86, "type:inferred=calculation"
        if RuleBasedQueryUnderstanding._has_any(text, _BOOLEAN_PATTERNS):
            return "judgement", 0.84, "type:inferred=judgement"
        if RuleBasedQueryUnderstanding._has_any(text, _COMPARISON_TERMS):
            return "comparison", 0.80, "type:inferred=comparison"
        if RuleBasedQueryUnderstanding._has_any(text, _ANALYSIS_TERMS):
            return "analysis", 0.76, "type:inferred=analysis"
        if has_options:
            return "choice", 0.55, "type:options_present_but_cardinality_unknown"
        return "extraction", 0.72, "type:inferred=extraction"

    @staticmethod
    def _infer_answer_shape(base_type: str, text: str, has_options: bool) -> str:
        if base_type == "multi_choice":
            return "choice_set"
        if base_type in {"single_choice"} or (base_type == "choice" and has_options):
            return "choice"
        if base_type == "ranking":
            return "ordered_list"
        if base_type == "judgement":
            return "boolean" if not has_options else "choice"
        if base_type == "calculation" or RuleBasedQueryUnderstanding._has_any(text, _NUMBER_PATTERNS):
            return "number"
        if base_type == "comparison" and RuleBasedQueryUnderstanding._has_any(text, _BOOLEAN_PATTERNS):
            return "boolean"
        if base_type == "analysis":
            return "long_text"
        return "text"

    @staticmethod
    def _joined_text(question: Question) -> str:
        return "\n".join([question.text, *[str(value) for value in question.options.values()]])

    @staticmethod
    def _has_any(text: str, terms: Sequence[str]) -> bool:
        lowered = text.lower()
        return any(term and term.lower() in lowered for term in terms)

    @staticmethod
    def _add_trait(
        traits: list[str],
        reasons: list[str],
        trait: str,
        matched: bool,
        detail: str,
    ) -> None:
        if matched and trait not in traits:
            traits.append(trait)
            reasons.append(f"trait:{trait}:{detail}")
