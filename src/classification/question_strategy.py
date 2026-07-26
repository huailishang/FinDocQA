"""Deterministic compound question strategy routing for BB-P0-11.

The module is shadow-only.  It does not wire itself into the production
classifier/workflow and never calls a provider.  The policy is versioned in
``config/question_strategy_matrix.json`` so tag composition and strategy
changes remain auditable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import Question, question_answer_slot_count


_OFFICIAL_TYPE_TO_BASE = {
    "单选题": "single_choice",
    "多选题": "multi_choice",
    "判断题": "judgement",
    "计算题": "calculation",
    "抽取题": "extraction",
}
_ANSWER_FORMAT_TO_BASE = {
    "mcq": "single_choice",
    "single": "single_choice",
    "single_choice": "single_choice",
    "multi": "multi_choice",
    "tf": "judgement",
}
_KNOWN_DOMAINS = {
    "financial_contracts",
    "financial_reports",
    "insurance",
    "regulatory",
    "research",
}

_CROSS_DOCUMENT_TERMS = (
    "两份", "多个文件", "多个文档", "跨文档", "分别根据", "分别结合", "结合两",
    "对比两", "比较两", "连续两年", "各自", "三者", "四者", "分别",
)
_CALCULATION_TERMS = (
    "计算", "算出", "求出", "合计", "总计", "增长率", "同比", "环比", "占比",
    "比例", "百分点", "平均", "差额", "金额", "现金价值", "赔付", "给付", "退保", "利率",
)
_COMPARISON_TERMS = (
    "比较", "对比", "相比", "高于", "低于", "大于", "小于", "差异", "更高", "更低",
    "超过", "少于", "不低于", "不高于",
)
_RANKING_TERMS = (
    "排序", "从高到低", "从低到高", "由高到低", "由低到高", "依次", "排名", "顺序",
)
_NEGATION_TERMS = (
    "不正确", "错误的是", "不符合", "不包括", "不属于", "不是", "不得", "不能", "禁止",
    "无需", "无须", "未", "无",
)
_EXCEPTION_CONDITION_TERMS = (
    "除外", "除非", "例外", "仅当", "只有", "前提", "条件", "情况下", "如果", "若", "但",
)
_TEMPORAL_TERMS = (
    "截至", "期间", "年度", "年末", "季度", "月末", "同比", "环比", "连续两年", "报告期",
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年?")
_DATE_RE = re.compile(r"(?:19|20)\d{2}[年./-]\d{1,2}(?:[月./-]\d{1,2}日?)?")


@dataclass(frozen=True)
class QuestionStrategyTags:
    domain: str
    base_type: str
    traits: tuple[str, ...]
    confidence: float
    low_confidence: bool
    reasons: tuple[str, ...]

    @property
    def question_tags(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.base_type, *self.traits)))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["question_tags"] = list(self.question_tags)
        return payload


@dataclass(frozen=True)
class StrategyConflict:
    field: str
    previous_value: Any
    incoming_value: Any
    previous_rule: str
    incoming_rule: str
    resolution: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuestionStrategy:
    """Stable BB-P0-11 shadow strategy contract."""

    qid: str
    domain: str
    question_tags: tuple[str, ...]
    doc_top_k_hint: int
    window_top_k_hint: int
    evidence_budget_hint: Mapping[str, int]
    solver_hint: str
    verification_requirements: tuple[str, ...]
    low_confidence: bool
    strategy_reason: tuple[str, ...]
    retrieval_depth_hint: str
    policy_version: str
    applied_rules: tuple[str, ...]
    conflicts: tuple[StrategyConflict, ...]
    production_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["question_tags"] = list(self.question_tags)
        payload["verification_requirements"] = list(self.verification_requirements)
        payload["strategy_reason"] = list(self.strategy_reason)
        payload["applied_rules"] = list(self.applied_rules)
        payload["conflicts"] = [item.to_dict() for item in self.conflicts]
        payload["evidence_budget_hint"] = dict(self.evidence_budget_hint)
        return payload


class QuestionStrategyMatrix:
    """Apply a versioned multi-label strategy matrix without production wiring."""

    def __init__(self, policy: Mapping[str, Any]) -> None:
        self.policy = dict(policy)
        self._validate_policy()
        self.schema_version = str(self.policy["schema_version"])
        self.policy_version = str(self.policy["policy_version"])
        self.production_enabled = bool(self.policy.get("production_enabled", False))
        self.low_confidence_threshold = float(self.policy.get("low_confidence_threshold", 0.75))
        self.recognized_domains = {
            str(value)
            for value in self.policy.get("recognized_domains", sorted(_KNOWN_DOMAINS))
        }

    @classmethod
    def from_file(cls, path: str | Path) -> "QuestionStrategyMatrix":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("question strategy matrix must be a JSON object")
        return cls(payload)

    def classify_tags(self, question: Question) -> QuestionStrategyTags:
        raw_type = str(question.raw.get("type") or "").strip()
        answer_format = str(question.answer_format or "").strip().lower()
        base_type, confidence, base_reason = self._base_type(raw_type, answer_format)
        reasons = [base_reason]
        traits: list[str] = []
        text = self._joined_text(question)

        def add(trait: str, detail: str) -> None:
            if trait not in traits:
                traits.append(trait)
            reasons.append(f"{trait}:{detail}")

        cross_hits = self._hits(text, _CROSS_DOCUMENT_TERMS)
        if len(tuple(question.doc_ids or ())) >= 2 or cross_hits:
            detail = f"declared_doc_count={len(tuple(question.doc_ids or ()))}" if len(tuple(question.doc_ids or ())) >= 2 else "terms=" + ",".join(cross_hits[:4])
            add("cross_document", detail)

        calculation_hits = self._hits(text, _CALCULATION_TERMS)
        numeric_signal = any(char.isdigit() for char in text) or "%" in text or "％" in text
        if base_type == "calculation" or (calculation_hits and numeric_signal):
            add("calculation", "base_type" if base_type == "calculation" else "terms=" + ",".join(calculation_hits[:5]))

        comparison_hits = self._hits(text, _COMPARISON_TERMS)
        if comparison_hits:
            add("comparison", "terms=" + ",".join(comparison_hits[:4]))

        ranking_hits = self._hits(text, _RANKING_TERMS)
        if ranking_hits:
            add("ranking", "terms=" + ",".join(ranking_hits[:4]))

        negation_hits = self._hits(text, _NEGATION_TERMS)
        if negation_hits:
            add("negation", "terms=" + ",".join(negation_hits[:4]))

        condition_hits = self._hits(text, _EXCEPTION_CONDITION_TERMS)
        if condition_hits:
            add("exception_or_condition", "terms=" + ",".join(condition_hits[:4]))

        answer_slot_count = question_answer_slot_count(question)
        if answer_slot_count > 1:
            add("multi_slot", f"answer_slot_count={answer_slot_count}")

        temporal_hits = self._hits(text, _TEMPORAL_TERMS)
        if temporal_hits or _YEAR_RE.search(text) or _DATE_RE.search(text):
            add("temporal_scope", "terms=" + ",".join(temporal_hits[:4]) if temporal_hits else "explicit_year_or_date")

        confidence, consistency_reasons = self._adjust_confidence(question, base_type, confidence)
        reasons.extend(consistency_reasons)
        low_confidence = confidence < self.low_confidence_threshold
        if low_confidence:
            add("low_confidence", f"{confidence:.2f}<{self.low_confidence_threshold:.2f}")

        return QuestionStrategyTags(
            domain=question.domain,
            base_type=base_type,
            traits=tuple(traits),
            confidence=round(max(0.0, min(confidence, 1.0)), 4),
            low_confidence=low_confidence,
            reasons=tuple(reasons),
        )

    def recommend(self, question: Question) -> QuestionStrategy:
        tags = self.classify_tags(question)
        state = self._defaults()
        sources = {field: ("default", 0) for field in state}
        conflicts: list[StrategyConflict] = []
        reasons = list(tags.reasons)
        applied = ["default"]

        base_rule = dict((self.policy.get("base_policies") or {}).get(tags.base_type) or (self.policy.get("base_policies") or {}).get("unknown") or {})
        if base_rule:
            self._apply_rule(state, sources, conflicts, f"base:{tags.base_type}", base_rule)
            applied.append(f"base:{tags.base_type}")
            reasons.append(f"policy:base:{tags.base_type}")

        trait_policies = self.policy.get("trait_policies") or {}
        for trait in sorted(tags.traits, key=lambda value: (int((trait_policies.get(value) or {}).get("priority", 0)), value)):
            rule = dict(trait_policies.get(trait) or {})
            if not rule:
                continue
            rule_id = f"trait:{trait}"
            self._apply_rule(state, sources, conflicts, rule_id, rule)
            applied.append(rule_id)
            reasons.append(f"policy:{rule_id}")

        match_labels = set(tags.traits) | {f"base:{tags.base_type}"}
        combinations = sorted(
            list(self.policy.get("combination_policies") or []),
            key=lambda item: (int(item.get("priority", 0)), str(item.get("id") or "")),
        )
        for rule in combinations:
            when_all = {str(value) for value in rule.get("when_all") or ()}
            if when_all and not when_all.issubset(match_labels):
                continue
            rule_id = f"combo:{rule.get('id') or 'unnamed'}"
            self._apply_rule(state, sources, conflicts, rule_id, rule)
            applied.append(rule_id)
            reasons.append(f"policy:{rule_id}")

        for conflict in conflicts:
            reasons.append(
                f"conflict:{conflict.field}:{conflict.previous_rule}->{conflict.incoming_rule}:{conflict.resolution}"
            )

        return QuestionStrategy(
            qid=question.qid,
            domain=question.domain,
            question_tags=tags.question_tags,
            doc_top_k_hint=int(state["doc_top_k_hint"]),
            window_top_k_hint=int(state["window_top_k_hint"]),
            evidence_budget_hint={
                "prompt_chars": int(state["prompt_chars"]),
                "completion_tokens": int(state["completion_tokens"]),
            },
            solver_hint=str(state["solver_hint"]),
            verification_requirements=tuple(str(value) for value in state["verification_requirements"]),
            low_confidence=tags.low_confidence,
            strategy_reason=tuple(reasons),
            retrieval_depth_hint=str(state["retrieval_depth_hint"]),
            policy_version=self.policy_version,
            applied_rules=tuple(applied),
            conflicts=tuple(conflicts),
            production_enabled=False,
        )

    def _base_type(self, raw_type: str, answer_format: str) -> tuple[str, float, str]:
        if raw_type in _OFFICIAL_TYPE_TO_BASE:
            return _OFFICIAL_TYPE_TO_BASE[raw_type], 1.0, f"base_type:official_type={raw_type}"
        mapped = _ANSWER_FORMAT_TO_BASE.get(answer_format)
        if mapped:
            return mapped, 0.9, f"base_type:answer_format={answer_format}"
        if answer_format == "freeform":
            if "计算" in raw_type or "calc" in raw_type.lower():
                return "calculation", 0.9, f"base_type:freeform_type_hint={raw_type}"
            if "抽取" in raw_type or "extract" in raw_type.lower():
                return "extraction", 0.9, f"base_type:freeform_type_hint={raw_type}"
            return "extraction", 0.65, "base_type:ambiguous_freeform_conservative_extraction"
        return "unknown", 0.4, f"base_type:unrecognized_type={raw_type or '<empty>'}"

    def _adjust_confidence(self, question: Question, base_type: str, confidence: float) -> tuple[float, list[str]]:
        reasons: list[str] = []
        adjusted = confidence
        has_options = bool(question.options)
        if base_type in {"single_choice", "multi_choice", "judgement"} and not has_options:
            adjusted -= 0.25
            reasons.append("confidence_penalty:option_type_without_options")
        if base_type in {"calculation", "extraction"} and has_options:
            adjusted -= 0.20
            reasons.append("confidence_penalty:freeform_type_with_options")
        if question.domain not in self.recognized_domains:
            adjusted -= 0.15
            reasons.append(f"confidence_penalty:unknown_domain={question.domain}")
        warnings = tuple(getattr(question.answer_contract, "consistency_warnings", ()) or ())
        if warnings:
            adjusted -= min(0.20, 0.05 * len(warnings))
            reasons.append("confidence_penalty:answer_contract=" + ",".join(warnings[:4]))
        return adjusted, reasons

    def _defaults(self) -> dict[str, Any]:
        defaults = dict(self.policy.get("defaults") or {})
        required = {
            "doc_top_k_hint", "window_top_k_hint", "prompt_chars", "completion_tokens",
            "solver_hint", "verification_requirements", "retrieval_depth_hint",
        }
        missing = sorted(required - set(defaults))
        if missing:
            raise ValueError(f"question strategy defaults missing fields: {missing}")
        defaults["verification_requirements"] = list(defaults["verification_requirements"])
        return defaults

    @staticmethod
    def _apply_rule(
        state: dict[str, Any],
        sources: dict[str, tuple[str, int]],
        conflicts: list[StrategyConflict],
        rule_id: str,
        rule: Mapping[str, Any],
    ) -> None:
        priority = int(rule.get("priority", 0))
        patch = dict(rule.get("patch") or {})
        numeric_fields = {"doc_top_k_hint", "window_top_k_hint", "prompt_chars", "completion_tokens"}
        for field, incoming in patch.items():
            if field not in state:
                raise ValueError(f"unsupported strategy patch field: {field}")
            previous = state[field]
            previous_rule, previous_priority = sources.get(field, ("default", 0))
            if field in numeric_fields:
                if int(incoming) > int(previous):
                    state[field] = int(incoming)
                    sources[field] = (rule_id, priority)
                continue
            if field == "verification_requirements":
                merged = list(previous)
                for value in incoming or ():
                    if value not in merged:
                        merged.append(value)
                state[field] = merged
                if merged != list(previous):
                    sources[field] = (rule_id, priority)
                continue
            if incoming == previous:
                continue
            resolution = "incoming_higher_or_equal_priority" if priority >= previous_priority else "kept_higher_priority_previous"
            if previous_rule != "default":
                conflicts.append(
                    StrategyConflict(
                        field=field,
                        previous_value=previous,
                        incoming_value=incoming,
                        previous_rule=previous_rule,
                        incoming_rule=rule_id,
                        resolution=resolution,
                    )
                )
            if priority >= previous_priority:
                state[field] = incoming
                sources[field] = (rule_id, priority)

    def _validate_policy(self) -> None:
        required = {"schema_version", "policy_version", "defaults", "base_policies", "trait_policies"}
        missing = sorted(required - set(self.policy))
        if missing:
            raise ValueError(f"question strategy policy missing keys: {missing}")
        if bool(self.policy.get("production_enabled", False)):
            raise ValueError("BB-P0-11 strategy matrix must remain production_enabled=false")

    @staticmethod
    def _joined_text(question: Question) -> str:
        return "\n".join([question.text, *[str(value) for value in question.options.values()]])

    @staticmethod
    def _hits(text: str, terms: Sequence[str]) -> list[str]:
        return [term for term in terms if term and term in text]
