"""Rule-based question classifier for controlled solver routing."""

from __future__ import annotations

from typing import Dict, Iterable, List

from contracts import ClassificationResult, Question, QuestionLabel


class RuleBasedQuestionClassifier:
    """Classify questions using cheap deterministic signals.

    The classifier is intentionally conservative. Labels are routing hints and
    may be multi-label. Solver routing applies a separate priority order.
    """

    calculation_terms = (
        "计算", "算出", "求", "共应", "合计", "排序", "从高到低", "从低到高",
        "增长率", "同比", "环比", "赔付", "给付", "退保", "现金价值", "免赔额",
        "保费", "利率", "比例", "金额", "万元", "亿元", "%",
    )
    calculation_type_terms = ("计算题", "财务指标对比分析", "财务数据一致性校验", "market_forecast")

    clause_terms = (
        "条款", "规定", "责任免除", "等待期", "除外", "不适用", "不承担", "不得",
        "应当", "义务", "审议", "决议", "承诺", "信息披露", "受益所有人", "尽职调查",
    )
    clause_type_terms = ("多选题", "判断题")

    fact_terms = (
        "哪一", "多少", "数据", "指标", "收入", "利润", "规模", "占比", "市场",
        "202", "201", "报告", "披露", "描述", "说法", "陈述", "符合事实",
    )
    fact_type_terms = ("industry_comparison", "data_verification")

    cross_doc_terms = (
        "比较", "对比", "结合", "两份", "连续两年", "分别", "多个", "三者", "四个",
        "均", "同时", "关于", "变化", "横向",
    )

    def classify(self, question: Question) -> ClassificationResult:
        text = self._joined_text(question)
        qtype = str(question.raw.get("type", ""))
        labels: List[QuestionLabel] = []
        reasons: Dict[str, str] = {}

        if question.answer_format == "multi":
            self._add(labels, reasons, QuestionLabel.MULTI_OPTION, "answer_format=multi")

        calculation_hits = self._hits(text, self.calculation_terms) + self._hits(qtype, self.calculation_type_terms)
        clause_hits = self._hits(text, self.clause_terms)
        fact_hits = self._hits(text, self.fact_terms) + self._hits(qtype, self.fact_type_terms)
        cross_hits = self._hits(text, self.cross_doc_terms)

        if len(question.doc_ids) >= 2 or cross_hits:
            detail = [f"doc_ids={len(question.doc_ids)}"] if len(question.doc_ids) >= 2 else []
            detail.extend(cross_hits[:6])
            self._add(labels, reasons, QuestionLabel.CROSS_DOC, ", ".join(detail))

        if self._is_calculation(question, calculation_hits):
            self._add(labels, reasons, QuestionLabel.CALCULATION, ", ".join(calculation_hits[:8]))

        if clause_hits or question.domain in {"regulatory", "financial_contracts"}:
            detail = clause_hits[:8] or [f"domain={question.domain}"]
            self._add(labels, reasons, QuestionLabel.CLAUSE_LOOKUP, ", ".join(detail))

        if fact_hits or question.domain in {"financial_reports", "research"}:
            detail = fact_hits[:8] or [f"domain={question.domain}"]
            self._add(labels, reasons, QuestionLabel.FACT_LOOKUP, ", ".join(detail))

        if not labels:
            self._add(labels, reasons, QuestionLabel.DEFAULT, "no specific route matched")

        return ClassificationResult(labels=labels, reasons=reasons)

    def _is_calculation(self, question: Question, hits: list[str]) -> bool:
        qtype = str(question.raw.get("type", ""))
        if any(term in qtype for term in self.calculation_type_terms):
            return True
        if not hits:
            return False
        text = self._joined_text(question)
        numeric_signal = any(char.isdigit() for char in text) or "%" in text
        arithmetic_signal = any(term in text for term in ("计算", "排序", "共应", "合计", "增长率", "同比", "赔付", "退保"))
        return numeric_signal and arithmetic_signal

    @staticmethod
    def _joined_text(question: Question) -> str:
        return "\n".join([question.text, *question.options.values()])

    @staticmethod
    def _hits(text: str, terms: Iterable[str]) -> list[str]:
        return [term for term in terms if term and term in text]

    @staticmethod
    def _add(
        labels: List[QuestionLabel],
        reasons: Dict[str, str],
        label: QuestionLabel,
        reason: str,
    ) -> None:
        if label not in labels:
            labels.append(label)
        reasons[label.value] = reason
