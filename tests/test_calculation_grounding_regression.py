from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path("tests/fixtures/calculation_grounding_cases.json")


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _case(qid: str):
    return next(case for case in _payload()["cases"] if case["qid"] == qid)


def _values(case):
    return {name: item["normalized_value"] for name, item in case["variables"].items()}


def _calculate(case):
    values = _values(case)
    pattern = case["pattern"]
    if pattern == "amount_ranking":
        amounts = {
            "平安智盈金生": values["zhiying_account_value"],
            "国寿增益宝": max(values["zengyibao_basic_amount"] * values["age_ratio"], values["zengyibao_account_value"]),
            "国寿鑫享添盈": max(values["xinxiang_premium"] - values["xinxiang_annuity_paid"], values["xinxiang_cash_value"]),
            "平安富鸿金生": max(values["fuhong_premium"] - values["fuhong_annuity_paid"], values["fuhong_cash_value"]),
        }
        return [[name, amount] for name, amount in sorted(amounts.items(), key=lambda item: item[1], reverse=True)]
    if pattern == "multi_policy_sum":
        payments = {
            "众安": max(values["self_paid"] - values["zhongan_deductible"], 0),
            "平安e生保": max(values["self_paid"] - values["pingan_deductible"], 0),
            "太保": max(values["self_paid"] - values["cpic_deductible"], 0),
        }
        payments["合计"] = sum(payments.values())
        return payments
    if pattern == "deductible_threshold":
        return {
            "家财险": max(values["property_loss"] - values["property_deductible"], 0),
            "平安e生保": 0,
            "太保": 0,
        }
    if pattern == "undefined_variable":
        required = {"a", "b", "x"}
        if not required <= values.keys():
            return None
        return values["a"] + values["b"] + values["x"]
    raise AssertionError(f"unknown pattern: {pattern}")


def test_fixture_is_auditable_and_submission_auto_apply_is_disabled():
    payload = _payload()
    assert payload["auto_apply_submission"] is False
    for case in payload["cases"]:
        assert case["formula"].strip()
        for variable in case["variables"].values():
            assert variable["value"] == variable["normalized_value"]
            for field in ("doc_id", "source_path", "location", "evidence_text"):
                assert variable[field]
        for rule in case["rules"]:
            for field in ("doc_id", "source_path", "location", "evidence_text"):
                assert rule[field]


def test_formulas_are_reexecuted_from_normalized_variables():
    for case in _payload()["cases"]:
        assert _calculate(case) == case["expected_computed"]


def test_exact_option_text_mapping_and_duplicates_are_explicit():
    for case in _payload()["cases"]:
        answer = case["expected_answer"]
        if answer is None:
            assert case["mapping_complete"] is False
            assert case["answer_letter_resolvable"] is False
            if case["duplicate_options"]:
                assert case["selected_option_text"] is not None
                matching = [letter for letter, text in case["options"].items() if text == case["selected_option_text"]]
                assert sorted(matching) == sorted(case["mapping_candidates"])
            else:
                assert case["selected_option_text"] is None
            continue
        assert case["answer_letter_resolvable"] is True
        assert case["options"][answer] == case["selected_option_text"]
        duplicates = case["duplicate_options"]
        if duplicates:
            assert case["mapping_complete"] is False
            candidate_group = next(group for group in duplicates if answer in group)
            texts = {case["options"][letter] for letter in candidate_group}
            assert len(texts) == 1
            assert sorted(candidate_group) == sorted(case["mapping_candidates"])
        else:
            assert case["mapping_complete"] is True
            assert list(case["options"].values()).count(case["selected_option_text"]) == 1


def test_case_013_recalculates_exact_ranking_and_maps_to_b():
    case = _case("case_013")
    assert _calculate(case) == [
        ["国寿增益宝", 1440000],
        ["平安智盈金生", 900000],
        ["平安富鸿金生", 850000],
        ["国寿鑫享添盈", 800000],
    ]
    assert case["expected_answer"] == "B"


def test_case_015_recalculates_but_flags_duplicate_option_letters():
    case = _case("case_015")
    assert _calculate(case) == {"众安": 50000, "平安e生保": 45000, "太保": 40000, "合计": 135000}
    assert case["duplicate_options"] == [["A", "B", "C"]]
    assert case["mapping_complete"] is False
    assert case["expected_answer"] is None
    assert case["answer_letter_resolvable"] is False
    assert case["mapping_candidates"] == ["A", "B", "C"]


def test_case_019_applies_actual_coverage_rules_and_maps_to_d():
    case = _case("case_019")
    assert _calculate(case) == {"家财险": 8000, "平安e生保": 0, "太保": 0}
    assert case["expected_answer"] == "D"


def test_undefined_variable_blocks_grounded_answer():
    case = _case("incomplete_formula_guard")
    assert _calculate(case) is None
    assert case["computation_complete"] is False
    assert case["expected_answer"] is None
    assert case["mapping_complete"] is False
