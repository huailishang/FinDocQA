"""Semantic-equivalence provenance gate for fact promotion.

Normalization rules are implementation conveniences, not evidence. This module
separates a compiler's internal aliasing from independently auditable proof that
two legal/regulatory terms are equivalent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

EXACT_ATOM_BINDING = "EXACT_ATOM_BINDING"
NO_EQUIVALENCE_REQUIRED_TYPED_BINDING = "NO_EQUIVALENCE_REQUIRED_TYPED_BINDING"
INSUFFICIENT_PROVENANCE = "INSUFFICIENT_PROVENANCE"
# Backward-compatible symbol only; classification no longer defaults to it.
EXACT_TEXT = EXACT_ATOM_BINDING
AUTHORITATIVE_DEFINITION = "AUTHORITATIVE_DEFINITION"
EVALUATOR_CALIBRATED_EQUIVALENCE = "EVALUATOR_CALIBRATED_EQUIVALENCE"
COMPILER_INTERNAL_ALIAS_ONLY = "COMPILER_INTERNAL_ALIAS_ONLY"
MODEL_PARAPHRASE_ONLY = "MODEL_PARAPHRASE_ONLY"

PROMOTION_ALLOWED = {
    EXACT_ATOM_BINDING: True,
    NO_EQUIVALENCE_REQUIRED_TYPED_BINDING: True,
    INSUFFICIENT_PROVENANCE: False,
    AUTHORITATIVE_DEFINITION: True,
    EVALUATOR_CALIBRATED_EQUIVALENCE: True,
    COMPILER_INTERNAL_ALIAS_ONLY: False,
    MODEL_PARAPHRASE_ONLY: False,
}

# Formatting-only replacements do not assert semantic equivalence.
_FORM_ONLY_BEFORE = {
    "\u3000", "％", "，", "。", "；", "：", "（", "）",
}

# Ordinary numeral rendering is treated as form normalization rather than a
# synonym claim. It is still audited separately by numeric comparators.
_CHINESE_NUMBER_TERMS = {
    "十年", "五年", "两年", "二年", "六个月", "七日", "十日", "三十日", "三十个工作日",
}


@dataclass(frozen=True)
class AliasRule:
    before: str
    after: str
    source: str
    semantic: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EquivalenceDependency:
    raw_option_term: str
    raw_source_term: str
    normalized_option_term: str
    normalized_source_term: str
    normalization_rule: str
    provenance_class: str
    promotion_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _basic(value: Any) -> str:
    text = str(value or "").lower()
    for before, after in (("％", "%"), ("，", ","), ("。", "."), ("；", ";"), ("：", ":"), ("（", "("), ("）", ")")):
        text = text.replace(before, after)
    return re.sub(r"\s+", "", text)


def extract_compact_alias_rules(source_path: str | Path) -> list[AliasRule]:
    """Parse `_compact().replacements` without importing or executing it."""
    path = Path(source_path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: list[AliasRule] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "_compact":
            continue
        for child in node.body:
            if not isinstance(child, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "replacements" for target in child.targets):
                continue
            values = ast.literal_eval(child.value)
            for before, after in values:
                semantic = before not in _FORM_ONLY_BEFORE and before not in _CHINESE_NUMBER_TERMS
                result.append(AliasRule(str(before), str(after), str(path), semantic))
    return result


def _matches(text: str, rule: AliasRule) -> bool:
    return _basic(rule.before) in _basic(text)


def alias_dependencies(
    option_text: str,
    source_texts: Sequence[str],
    rules: Sequence[AliasRule],
) -> list[EquivalenceDependency]:
    """Find cross-side terms that become equal only through internal aliases."""
    semantic_rules = [rule for rule in rules if rule.semantic]
    option_hits = [rule for rule in semantic_rules if _matches(option_text, rule)]
    source_hits: list[AliasRule] = []
    source_joined = "\n".join(str(value or "") for value in source_texts)
    for rule in semantic_rules:
        if _matches(source_joined, rule):
            source_hits.append(rule)

    dependencies: list[EquivalenceDependency] = []
    seen: set[tuple[str, str, str]] = set()

    def add(option_term: str, source_term: str, normalized: str, rule_text: str) -> None:
        key = (option_term, source_term, normalized)
        if key in seen:
            return
        seen.add(key)
        dependencies.append(EquivalenceDependency(
            raw_option_term=option_term,
            raw_source_term=source_term,
            normalized_option_term=normalized,
            normalized_source_term=normalized,
            normalization_rule=rule_text,
            provenance_class=COMPILER_INTERNAL_ALIAS_ONLY,
            promotion_allowed=False,
        ))

    # One side may use the alias term while the other already uses the compiler's
    # normalized term verbatim (for example 扣减 -> 扣分).
    for option_rule in option_hits:
        if _basic(option_rule.after) in _basic(source_joined) and _basic(option_rule.before) != _basic(option_rule.after):
            add(option_rule.before, option_rule.after, option_rule.after, f"{option_rule.before}->{option_rule.after}")
    for source_rule in source_hits:
        if _basic(source_rule.after) in _basic(option_text) and _basic(source_rule.before) != _basic(source_rule.after):
            add(source_rule.after, source_rule.before, source_rule.after, f"{source_rule.before}->{source_rule.after}")

    # Or both sides may use different aliases that collapse to the same internal
    # normalized token (the dual-alias self-proof failure mode).
    for option_rule in option_hits:
        for source_rule in source_hits:
            if _basic(option_rule.after) != _basic(source_rule.after):
                continue
            if _basic(option_rule.before) == _basic(source_rule.before):
                continue
            add(
                option_rule.before, source_rule.before, option_rule.after,
                f"{option_rule.before}->{option_rule.after}; {source_rule.before}->{source_rule.after}",
            )
    return dependencies


def direct_same_term_evidence(option_text: str, source_texts: Sequence[str]) -> list[str]:
    """Return material raw terms shared verbatim by claim and source.

    This is deliberately lexical. It does not use the compiler alias table.
    """
    option = _basic(option_text)
    source = _basic("\n".join(source_texts))
    tokens = re.findall(r"[\u4e00-\u9fff]{4,18}|[a-z0-9%]{3,}", option)
    shared: list[str] = []
    for token in tokens:
        if token in source and token not in shared:
            shared.append(token)
    return shared


def classify_semantic_equivalence(
    *,
    option_text: str,
    source_texts: Sequence[str],
    rules: Sequence[AliasRule],
    compiler_caveats: Sequence[str] = (),
    authoritative_definition_sources: Sequence[Mapping[str, Any]] = (),
    evaluator_calibrated_pairs: Iterable[tuple[str, str]] = (),
    model_paraphrase_only: bool = False,
    atom_coverage_audit: Mapping[str, Any] | None = None,
    typed_relation_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dependencies = alias_dependencies(option_text, source_texts, rules)
    shared_terms = direct_same_term_evidence(option_text, source_texts)

    authoritative = [dict(row) for row in authoritative_definition_sources if row]
    calibrated_pairs = {(str(a), str(b)) for a, b in evaluator_calibrated_pairs}
    calibrated_hit = any(
        (dep.raw_option_term, dep.raw_source_term) in calibrated_pairs
        or (dep.raw_source_term, dep.raw_option_term) in calibrated_pairs
        for dep in dependencies
    )

    if authoritative:
        provenance_class = AUTHORITATIVE_DEFINITION
        reason = "independent authoritative definition source proves the equivalence"
    elif calibrated_hit:
        provenance_class = EVALUATOR_CALIBRATED_EQUIVALENCE
        reason = "equivalence was independently calibrated by Evaluator/leaderboard evidence"
    elif dependencies or compiler_caveats:
        provenance_class = COMPILER_INTERNAL_ALIAS_ONLY
        reason = "equivalence depends on compiler alias/normalization and has no independent proof"
    elif model_paraphrase_only:
        provenance_class = MODEL_PARAPHRASE_ONLY
        reason = "semantic relation is available only as model paraphrase"
    elif atom_coverage_audit and bool(atom_coverage_audit.get("exact_atom_binding_pass")):
        provenance_class = EXACT_ATOM_BINDING
        reason = "all decisive raw claim atoms are directly covered by audited raw source spans"
    elif typed_relation_audit and bool(typed_relation_audit.get("pass")):
        provenance_class = NO_EQUIVALENCE_REQUIRED_TYPED_BINDING
        reason = "typed relation closes the fact without semantic synonym equivalence"
    else:
        provenance_class = INSUFFICIENT_PROVENANCE
        reason = "no alias dependency was found, but decisive raw atom coverage or typed relation proof is insufficient"

    return {
        "provenance_class": provenance_class,
        "promotion_allowed": PROMOTION_ALLOWED[provenance_class],
        "reason": reason,
        "shared_raw_terms": shared_terms,
        "alias_dependencies": [row.to_dict() for row in dependencies],
        "compiler_caveats": list(compiler_caveats),
        "authoritative_definition_sources": authoritative,
        "evaluator_calibrated_pair_hit": calibrated_hit,
        "atom_coverage_audit": dict(atom_coverage_audit or {}),
        "typed_relation_audit": dict(typed_relation_audit or {}),
    }


def promotion_decision(classification: Mapping[str, Any]) -> str:
    provenance_class = str(classification.get("provenance_class") or INSUFFICIENT_PROVENANCE)
    if PROMOTION_ALLOWED.get(provenance_class, False):
        return "KEEP_STRONG"
    if provenance_class == MODEL_PARAPHRASE_ONLY:
        return "KEEP_TIER_B_SHADOW"
    return "DOWNGRADE_UNRESOLVED"
