from __future__ import annotations

import pytest

from solvers.base import normalize_answer


@pytest.mark.parametrize(
    ("raw", "answer_format", "expected"),
    [
        ("mcq\n\n最终答案：A", "mcq", "A"),
        ("mcq\n\nD", "mcq", "D"),
        ("根据计算，A不成立，最终答案：B", "mcq", "B"),
        ("解释文本提到 A 和 C。\nB", "mcq", "B"),
        ("FINAL ANSWER: D", "mcq", "D"),
        ("最终答案：B", "tf", "B"),
        ("最终答案：DBA", "multi", "ABD"),
        ("mcq\n\n最终答案：A", "multi", "A"),
    ],
)
def test_normalize_answer_avoids_prefix_and_prose_contamination(raw, answer_format, expected):
    assert normalize_answer(raw, answer_format) == expected


def test_case_013_truncated_explanation_prefers_last_standalone_option():
    raw = "根据计算结果：144万 > 90万 > 85万 > 80万。\n正确排序对应选项 B"
    assert normalize_answer(raw, "mcq") == "B"


def test_legacy_compact_output_remains_supported():
    assert normalize_answer("A", "mcq") == "A"
    assert normalize_answer("AB", "multi") == "AB"


def test_ambiguous_prose_does_not_select_an_incidental_letter():
    assert normalize_answer("可能是 B，也可能是 C", "mcq") == "A"
