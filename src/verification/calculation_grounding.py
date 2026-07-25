"""Shared calculation-grounding payload utilities.

This module is intentionally qid-agnostic.  It describes the outcome of a
calculation task in a common schema that can be consumed by:

* Package B replacement policy / production gates;
* Package C option-evidence slots;
* Package H offline cluster smoke.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

CALCULATION_GROUNDING_VERSION = "calculation_grounding_v1"

_TRUE = {"true", "supported", "match", "matched", "yes"}
_FALSE = {"false", "contradicted", "mismatch", "no"}
_UNRESOLVED = {"unresolved", "missing", "unknown", "scope_excluded"}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item)]


def normalize_option_evaluations(
    option_evaluations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize option-level calculation verdicts into Package C-compatible slots."""
    if option_evaluations is None:
        return []
    raw_items: list[tuple[str, Any]] = []
    if isinstance(option_evaluations, Mapping):
        raw_items = [(str(option), payload) for option, payload in option_evaluations.items()]
    else:
        for payload in option_evaluations:
            if isinstance(payload, Mapping):
                raw_items.append((str(payload.get("option") or ""), payload))
    normalized: list[dict[str, Any]] = []
    for option, payload in raw_items:
        item = dict(payload) if isinstance(payload, Mapping) else {"evaluated_value": payload}
        verdict = str(item.get("verdict") or item.get("status") or "").lower()
        if verdict in _TRUE:
            verdict = "true"
        elif verdict in _FALSE:
            verdict = "false"
        elif verdict in _UNRESOLVED:
            verdict = "unresolved"
        else:
            verdict = "unresolved"
        normalized.append({
            "option": str(item.get("option") or option).upper(),
            "evaluated_value": item.get("evaluated_value"),
            "expected_condition": item.get("expected_condition", ""),
            "verdict": verdict,
            "evidence_refs": _string_list(item.get("evidence_refs")),
            "calculation_refs": _string_list(item.get("calculation_refs")),
            "unresolved_reason": str(item.get("unresolved_reason") or ""),
            "comparison_audit": item.get("comparison_audit"),
            "numeric_claims": item.get("numeric_claims") or ((item.get("comparison_audit") or {}).get("numeric_claims") if isinstance(item.get("comparison_audit"), Mapping) else None),
            "claim_match_audit": item.get("claim_match_audit") or ((item.get("comparison_audit") or {}).get("claim_match_audit") if isinstance(item.get("comparison_audit"), Mapping) else None),
            "matched_computed_keys": item.get("matched_computed_keys") or ((item.get("comparison_audit") or {}).get("matched_computed_keys") if isinstance(item.get("comparison_audit"), Mapping) else None),
            "contradicted_claims": item.get("contradicted_claims") or ((item.get("comparison_audit") or {}).get("contradicted_claims") if isinstance(item.get("comparison_audit"), Mapping) else None),
            "unresolved_claims": item.get("unresolved_claims") or ((item.get("comparison_audit") or {}).get("unresolved_claims") if isinstance(item.get("comparison_audit"), Mapping) else None),
            "comparison_tolerance": item.get("comparison_tolerance") or ((item.get("comparison_audit") or {}).get("comparison_tolerance") if isinstance(item.get("comparison_audit"), Mapping) else None),
        })
    return sorted(normalized, key=lambda item: item["option"])



def _normalize_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return str(int(value))
        return ("%.10f" % float(value)).rstrip("0").rstrip(".")
    return str(value).strip().lower()


def _result_values(deterministic_result: Any) -> list[str]:
    values: list[str] = []
    if isinstance(deterministic_result, Mapping):
        for key, value in deterministic_result.items():
            values.append(_normalize_scalar(value))
            values.append(f"{str(key).strip().lower()}={_normalize_scalar(value)}")
    elif isinstance(deterministic_result, (list, tuple, set)):
        for item in deterministic_result:
            values.extend(_result_values(item))
    else:
        values.append(_normalize_scalar(deterministic_result))
    return [value for value in values if value]



def _comparison_from_mapping(deterministic_result: Mapping[str, Any], condition: str) -> dict[str, Any] | None:
    lowered = condition.lower().replace(" ", "")
    # Support small, auditable condition expressions such as:
    #   profit_2025 < profit_2024
    #   dividend_ratio_2025 > dividend_ratio_2024
    m = re.match(r"^([a-zA-Z0-9_]+)(<=|>=|<|>|==)([a-zA-Z0-9_.%+-]+)$", lowered)
    if not m:
        return None
    left, op, right = m.groups()
    keys = {str(k).strip().lower(): v for k, v in deterministic_result.items()}
    if left not in keys:
        return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": f"left_operand_missing:{left}"}
    left_value = keys[left]
    right_value = keys.get(right, right)
    try:
        lv = float(str(left_value).rstrip("%"))
        rv = float(str(right_value).rstrip("%"))
    except ValueError:
        return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": "non_numeric_comparison_operand"}
    verdict = {
        "<": lv < rv,
        ">": lv > rv,
        "<=": lv <= rv,
        ">=": lv >= rv,
        "==": lv == rv,
    }[op]
    return {"verdict": "true" if verdict else "false", "evaluated_value": {left: lv, right: rv}, "unresolved_reason": ""}


def _parse_boolean_clause(deterministic_result: Mapping[str, Any], condition: str) -> dict[str, Any] | None:
    # Boolean flag support: flag:true / flag=false / flag is true.
    lowered = condition.lower().replace(" ", "")
    m = re.match(r"^([a-zA-Z0-9_]+)(?:is|:|=)(true|false)$", lowered)
    if not m:
        return None
    key, expected = m.groups()
    keys = {str(k).strip().lower(): v for k, v in deterministic_result.items()}
    if key not in keys:
        return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": f"boolean_key_missing:{key}"}
    actual = bool(keys[key])
    expected_bool = expected == "true"
    return {"verdict": "true" if actual is expected_bool else "false", "evaluated_value": actual, "unresolved_reason": ""}


def _parse_threshold_clause(deterministic_result: Mapping[str, Any], condition: str) -> dict[str, Any] | None:
    # Ratio/threshold support: rd_ratio > 5% / dividend_ratio<=20.
    lowered = condition.lower().replace(" ", "")
    m = re.match(r"^([a-zA-Z0-9_]*ratio[a-zA-Z0-9_]*|[a-zA-Z0-9_]*rate[a-zA-Z0-9_]*|[a-zA-Z0-9_]*percent[a-zA-Z0-9_]*)(<=|>=|<|>|==)([0-9.]+)%?$", lowered)
    if not m:
        return None
    key, op, threshold = m.groups()
    keys = {str(k).strip().lower(): v for k, v in deterministic_result.items()}
    if key not in keys:
        return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": f"threshold_key_missing:{key}"}
    try:
        lv = float(str(keys[key]).rstrip("%"))
        rv = float(threshold)
    except ValueError:
        return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": "non_numeric_threshold_operand"}
    verdict = {
        "<": lv < rv,
        ">": lv > rv,
        "<=": lv <= rv,
        ">=": lv >= rv,
        "==": lv == rv,
    }[op]
    return {"verdict": "true" if verdict else "false", "evaluated_value": {key: lv, "threshold": rv}, "unresolved_reason": ""}




def _numeric_float(value: Any) -> float | None:
    try:
        text = str(value).strip().replace(",", "").rstrip("元")
        if text.endswith("%"):
            return float(text[:-1]) / 100.0
        return float(text)
    except (TypeError, ValueError):
        return None


def _computed_numeric_map(deterministic_result: Any) -> dict[str, float]:
    if not isinstance(deterministic_result, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, value in deterministic_result.items():
        numeric = _numeric_float(value)
        if numeric is not None:
            result[str(key)] = numeric
    return result


def _subject_aliases(subject: str) -> list[str]:
    raw = str(subject or "").strip()
    compact = re.sub(r"\s+", "", raw)
    cleaned = re.sub(r"^(医疗险|医疗|商业医疗险|合计医疗|共赔|共计|总计)", "", compact)
    aliases = [compact, cleaned]
    if compact in {"共", "共赔", "共计", "总计", "计", "合计医疗", "医疗合计", "医疗险合计"}:
        aliases.extend(["合计", "医疗合计"])
    if "合计" in compact:
        aliases.append("合计")
    # Keep product labels visible even when prefixed by context words.
    for product in ("家财险", "e生保", "益生保", "太保"):
        if product in compact:
            aliases.append(product)
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _match_numeric_claim_key(subject: str, computed: Mapping[str, float]) -> str | None:
    key_norm = {re.sub(r"\s+", "", str(key)): str(key) for key in computed}
    for alias in _subject_aliases(subject):
        if alias in key_norm:
            return key_norm[alias]
    for alias in _subject_aliases(subject):
        for norm, original in key_norm.items():
            if alias and (alias in norm or norm in alias):
                return original
    return None


def extract_numeric_claims(condition: str) -> list[dict[str, Any]]:
    """Extract qid-agnostic option-level numeric claims from Chinese option text.

    The extractor is intentionally conservative: it returns explicit amount
    claims (subject + amount) and zero-payment claims (subject + 均不赔付/不赔付).
    It does not infer final answers and it does not depend on qid names.
    """
    text = str(condition or "").strip()
    if not text:
        return []
    claims: list[dict[str, Any]] = []
    # Product zero claims, e.g. "e生保和太保均不赔付".
    zero_pattern = re.compile(r"([A-Za-z0-9_\u4e00-\u9fff、和及与]+?)(?:均|都|皆)?不(?:予)?赔(?:付)?")
    for m in zero_pattern.finditer(text):
        subject_text = m.group(1)
        subject_text = re.sub(r".*[：:；;，,。]\s*", "", subject_text)
        for part in re.split(r"[、和及与]", subject_text):
            part = part.strip()
            if part:
                claims.append({"subject": part, "expected": 0.0, "raw": m.group(0), "claim_type": "zero_payment"})
    # Positive/explicit amount claims.
    amount_pattern = re.compile(
        r"([A-Za-z0-9_\u4e00-\u9fff]+?)"
        r"(?:赔付|赔|给付|支付|为|=|：|:)?"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:元|万元|万|亿元|亿)?"
    )
    stop_subjects = {"年", "月", "日"}
    for m in amount_pattern.finditer(text):
        subject = m.group(1).strip()
        amount_text = m.group(2)
        # Avoid pulling year-like fragments where no payment/amount word exists.
        raw = m.group(0)
        if subject in stop_subjects:
            continue
        if not any(word in raw for word in ("赔", "付", "合计", "共", "计", "为", "=")) and not any(product in subject for product in ("家财险", "e生保", "益生保", "太保")):
            continue
        multiplier = 1.0
        if "亿元" in raw or raw.endswith("亿"):
            multiplier = 100000000.0
        elif "万元" in raw or raw.endswith("万"):
            multiplier = 10000.0
        expected = float(amount_text) * multiplier
        claims.append({"subject": subject, "expected": expected, "raw": raw, "claim_type": "amount"})
    # Deduplicate exact claims while preserving order.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()
    for claim in claims:
        key = (str(claim.get("subject")), float(claim.get("expected", 0.0)), str(claim.get("raw")))
        if key not in seen:
            seen.add(key)
            deduped.append(claim)
    return deduped


def _evaluate_numeric_claims(
    deterministic_result: Any,
    condition: str,
    *,
    tolerance: float = 1e-6,
) -> dict[str, Any] | None:
    computed = _computed_numeric_map(deterministic_result)
    if not computed:
        return None
    claims = extract_numeric_claims(condition)
    if not claims:
        return None
    audit: list[dict[str, Any]] = []
    false_claims: list[dict[str, Any]] = []
    unresolved_claims: list[dict[str, Any]] = []
    matched_keys: list[str] = []
    for claim in claims:
        subject = str(claim.get("subject") or "")
        matched_key = _match_numeric_claim_key(subject, computed)
        item = dict(claim)
        item["matched_key"] = matched_key
        item["comparison_tolerance"] = tolerance
        if matched_key is None:
            item["verdict"] = "unresolved"
            unresolved_claims.append(item)
            audit.append(item)
            continue
        actual = computed[matched_key]
        expected = float(claim.get("expected") or 0.0)
        matched = abs(actual - expected) <= tolerance
        item.update({"actual": actual, "expected": expected, "verdict": "true" if matched else "false"})
        matched_keys.append(matched_key)
        if not matched:
            false_claims.append(item)
        audit.append(item)
    if false_claims:
        verdict = "false"
        reason = "numeric_claim_contradiction"
    elif unresolved_claims:
        verdict = "unresolved"
        reason = "numeric_claim_unmapped"
    else:
        verdict = "true"
        reason = ""
    return {
        "verdict": verdict,
        "evaluated_value": deterministic_result,
        "unresolved_reason": reason,
        "comparison_audit": {
            "numeric_claims": claims,
            "claim_match_audit": audit,
            "matched_computed_keys": list(dict.fromkeys(matched_keys)),
            "contradicted_claims": false_claims,
            "unresolved_claims": unresolved_claims,
            "comparison_tolerance": tolerance,
        },
    }


def _parse_tf_claim_clause(deterministic_result: Any, condition: str) -> dict[str, Any] | None:
    """Evaluate true/false option text only when a parseable claim exists.

    Naked labels such as 正确/错误 remain unresolved.  Forms like
    "正确: dividend_ratio_2025 > dividend_ratio_2024" evaluate the claim and
    then compare it with the option's truth label.  This keeps TF options
    auditable without letting a bare truth label become a replacement candidate.
    """
    text = str(condition or "").strip()
    compact = text.replace(" ", "")
    label: bool | None = None
    claim = ""
    for prefix, expected in (("正确", True), ("错误", False), ("对", True), ("错", False), ("true", True), ("false", False)):
        if compact.lower() == prefix.lower():
            return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": "naked_tf_label_without_claim"}
        for sep in (":", "：", "=", "-"):
            raw_prefix = prefix + sep
            if text.lower().startswith(raw_prefix.lower()):
                label = expected
                claim = text[len(raw_prefix):].strip()
                break
        if label is not None:
            break
    if label is None:
        m = re.match(r"^(正确|错误|对|错|true|false)[，,。；;\s]+(.+)$", text, flags=re.IGNORECASE)
        if m:
            label_text, claim = m.groups()
            label = label_text.lower() in {"正确", "对", "true"}
    if label is None or not claim:
        return None
    evaluated = evaluate_option_condition(deterministic_result=deterministic_result, expected_condition=claim)
    if evaluated.get("verdict") not in {"true", "false"}:
        return {
            "verdict": "unresolved",
            "evaluated_value": evaluated.get("evaluated_value"),
            "unresolved_reason": "tf_claim_not_deterministically_parseable:" + str(evaluated.get("unresolved_reason") or ""),
            "comparison_audit": {"tf_label": label, "claim": claim, "claim_result": evaluated},
        }
    claim_truth = evaluated.get("verdict") == "true"
    option_truth = claim_truth is label
    return {
        "verdict": "true" if option_truth else "false",
        "evaluated_value": evaluated.get("evaluated_value"),
        "unresolved_reason": "",
        "comparison_audit": {"tf_label": label, "claim": claim, "claim_result": evaluated},
    }

def evaluate_option_condition(
    *,
    deterministic_result: Any,
    expected_condition: str,
) -> dict[str, Any]:
    """Evaluate one option condition without trusting an LLM-selected letter.

    This conservative evaluator supports explicit fixture-style conditions and
    exact deterministic-result containment.  If it cannot prove true/false, it
    returns unresolved so Package B/C gates can block instead of accepting an
    unsupported match.
    """
    condition = str(expected_condition or "").strip()
    lowered = condition.lower()
    if not condition:
        return {"verdict": "unresolved", "evaluated_value": None, "unresolved_reason": "empty_expected_condition"}
    if condition.replace(" ", "") in {"正确", "错误", "对", "错"}:
        return {"verdict": "unresolved", "evaluated_value": deterministic_result, "unresolved_reason": "naked_tf_label_without_claim"}
    tf_parsed = _parse_tf_claim_clause(deterministic_result, condition)
    if tf_parsed is not None:
        return tf_parsed
    numeric_claims = _evaluate_numeric_claims(deterministic_result, condition)
    if numeric_claims is not None:
        return numeric_claims
    if lowered in {"true", "statement true", "expected true", "match", "matched"}:
        return {"verdict": "true", "evaluated_value": True, "unresolved_reason": ""}
    if lowered in {"false", "statement false", "expected false", "mismatch", "not matched"}:
        return {"verdict": "false", "evaluated_value": False, "unresolved_reason": ""}
    if isinstance(deterministic_result, Mapping):
        for parser in (_parse_boolean_clause, _parse_threshold_clause, _comparison_from_mapping):
            parsed = parser(deterministic_result, condition)
            if parsed is not None:
                return parsed
    if "=" in lowered and isinstance(deterministic_result, Mapping):
        left, right = lowered.split("=", 1)
        key = left.strip()
        expected_norm = _normalize_scalar(right.strip())
        if key in {str(k).strip().lower() for k in deterministic_result}:
            actual = next(value for k, value in deterministic_result.items() if str(k).strip().lower() == key)
            verdict = _normalize_scalar(actual) == expected_norm
            return {"verdict": "true" if verdict else "false", "evaluated_value": deterministic_result, "unresolved_reason": ""}
    if lowered.startswith("result=") or lowered.startswith("equals:"):
        expected = condition.split("=", 1)[1] if "=" in condition else condition.split(":", 1)[1]
        expected_norm = _normalize_scalar(expected)
        verdict = expected_norm in set(_result_values(deterministic_result))
        return {"verdict": "true" if verdict else "false", "evaluated_value": deterministic_result, "unresolved_reason": ""}
    # Exact normalized containment for simple scalar/list/dict results.
    result_values = set(_result_values(deterministic_result))
    normalized_condition = lowered.replace(" ", "")
    for value in result_values:
        if value and value.replace(" ", "") in normalized_condition:
            return {"verdict": "true", "evaluated_value": deterministic_result, "unresolved_reason": ""}
    return {
        "verdict": "unresolved",
        "evaluated_value": deterministic_result,
        "unresolved_reason": "condition_not_deterministically_parseable",
    }


def build_option_evaluations_from_conditions(
    *,
    deterministic_result: Any,
    option_conditions: Mapping[str, Any],
    evidence_refs: Sequence[str] | None = None,
    calculation_refs: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Build qid-agnostic option evaluations from deterministic results.

    The caller supplies option text or explicit option conditions.  This helper
    never consumes an LLM answer letter; unparseable conditions are unresolved.
    """
    evaluations: list[dict[str, Any]] = []
    for option, condition in option_conditions.items():
        evaluated = evaluate_option_condition(
            deterministic_result=deterministic_result,
            expected_condition=str(condition or ""),
        )
        evaluations.append({
            "option": str(option).upper(),
            "evaluated_value": evaluated.get("evaluated_value"),
            "expected_condition": str(condition or ""),
            "verdict": evaluated.get("verdict", "unresolved"),
            "evidence_refs": _string_list(evidence_refs),
            "calculation_refs": _string_list(calculation_refs),
            "unresolved_reason": str(evaluated.get("unresolved_reason") or ""),
            "comparison_audit": evaluated.get("comparison_audit"),
            "numeric_claims": (evaluated.get("comparison_audit") or {}).get("numeric_claims") if isinstance(evaluated.get("comparison_audit"), Mapping) else None,
            "claim_match_audit": (evaluated.get("comparison_audit") or {}).get("claim_match_audit") if isinstance(evaluated.get("comparison_audit"), Mapping) else None,
            "matched_computed_keys": (evaluated.get("comparison_audit") or {}).get("matched_computed_keys") if isinstance(evaluated.get("comparison_audit"), Mapping) else None,
            "contradicted_claims": (evaluated.get("comparison_audit") or {}).get("contradicted_claims") if isinstance(evaluated.get("comparison_audit"), Mapping) else None,
            "unresolved_claims": (evaluated.get("comparison_audit") or {}).get("unresolved_claims") if isinstance(evaluated.get("comparison_audit"), Mapping) else None,
            "comparison_tolerance": (evaluated.get("comparison_audit") or {}).get("comparison_tolerance") if isinstance(evaluated.get("comparison_audit"), Mapping) else None,
        })
    return sorted(evaluations, key=lambda item: item["option"])

def build_calculation_grounding(
    *,
    formula_text: str = "",
    formula_source_refs: Sequence[str] | None = None,
    variables: Mapping[str, Any] | None = None,
    variable_source_refs: Mapping[str, Any] | None = None,
    unit_normalization: str | Mapping[str, Any] | None = None,
    deterministic_result: Any = None,
    option_evaluations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    unresolved_variables: Sequence[str] | None = None,
    used_material_variables: Sequence[str] | None = None,
    unused_material_variables: Sequence[str] | None = None,
    coverage_gap: bool = False,
    computation_complete: bool | None = None,
) -> dict[str, Any]:
    """Create a qid-agnostic calculation grounding payload.

    A candidate is considered calculation-complete only when there is exactly one
    true option and no unresolved variables, unused material variables, or
    coverage gap.  Zero-match and multi-match are represented explicitly so the
    gate can block them without guessing.
    """
    slots = normalize_option_evaluations(option_evaluations)
    true_options = [slot["option"] for slot in slots if slot.get("verdict") == "true"]
    raw_unique_option_match = len(true_options) == 1
    # D-R3 replacement guard: a partial deterministic computation must never
    # become replacement-eligible only because the option text happens to match
    # a partial scalar. The caller can explicitly pass computation_complete=False
    # to force no unique match and preserve blocking semantics.
    forced_incomplete = computation_complete is False
    if forced_incomplete:
        true_options = []
    unresolved = _string_list(unresolved_variables)
    used = _string_list(used_material_variables)
    unused = _string_list(unused_material_variables)
    option_requirements_resolved = not unresolved and not unused and not coverage_gap and not forced_incomplete
    if not option_requirements_resolved:
        true_options = []
    zero_match = bool(slots) and len(true_options) == 0
    multi_match = len(true_options) > 1
    option_match_unique = bool(raw_unique_option_match and option_requirements_resolved)
    option_match = true_options[0] if option_match_unique else (true_options if true_options else None)
    block_reasons: list[str] = []
    if zero_match:
        block_reasons.append("zero_option_match")
    if multi_match:
        block_reasons.append("multi_option_match")
    if unresolved:
        block_reasons.append("unresolved_variables")
    if unused:
        block_reasons.append("unused_material_variables")
    if coverage_gap:
        block_reasons.append("coverage_gap")
    if forced_incomplete:
        block_reasons.append("calculation_incomplete")
    if raw_unique_option_match and not option_requirements_resolved:
        block_reasons.append("unique_match_blocked_by_incomplete_calculation")
    if not option_match_unique and slots:
        block_reasons.append("no_unique_option_match")
    calculation_complete = bool(option_match_unique and option_requirements_resolved)
    return {
        "calculation_grounding_version": CALCULATION_GROUNDING_VERSION,
        "formula_text": str(formula_text or ""),
        "formula_source_refs": _string_list(formula_source_refs),
        "variables": dict(variables or {}),
        "variable_source_refs": dict(variable_source_refs or {}),
        "unit_normalization": unit_normalization or "",
        "deterministic_result": deterministic_result,
        "option_evaluations": slots,
        "option_match": option_match,
        "option_match_unique": option_match_unique,
        "zero_match": zero_match,
        "multi_match": multi_match,
        "unresolved_variables": unresolved,
        "used_material_variables": used,
        "unused_material_variables": unused,
        "coverage_gap": bool(coverage_gap),
        "calculation_complete": calculation_complete,
        "candidate_block_reason": ";".join(dict.fromkeys(block_reasons)),
    }


def integrity_blocking_reasons(payload: Mapping[str, Any] | None) -> list[str]:
    """Return production-integrity blocking reasons implied by a payload."""
    if not isinstance(payload, Mapping) or not payload:
        return []
    reasons: list[str] = []
    if payload.get("zero_match"):
        reasons.append("zero_option_match")
    if payload.get("multi_match"):
        reasons.append("multi_option_match")
    if payload.get("option_match_unique") is False:
        reasons.append("no_unique_option_match")
    if payload.get("unresolved_variables"):
        reasons.append("unresolved_variables")
    if payload.get("unused_material_variables"):
        reasons.append("unused_material_variables")
    if payload.get("coverage_gap"):
        reasons.append("coverage_gap")
    if payload.get("calculation_complete") is False:
        reasons.append("calculation_incomplete")
    return list(dict.fromkeys(reasons))
