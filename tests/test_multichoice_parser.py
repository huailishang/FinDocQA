"""MultiChoice repeated-tag parser hardening (P6e-4).

Covers task card §2.4: when the model emits multiple explicit judgment tags
for one option (self-correction), the LAST explicit tag is used.
"""

from __future__ import annotations

from solvers.multi_choice import MultiChoiceSolver

OPTS = {"A": "选项甲", "B": "选项乙", "C": "选项丙", "D": "选项丁"}


def test_parse_single_tag_per_option():
    text = "A: 【支持】理由\nB: 【反驳】理由\nC: 【不确定】理由"
    j = MultiChoiceSolver._parse_judgments(text, OPTS)
    assert j == {"A": "支持", "B": "反驳", "C": "不确定", "D": "不确定"}


def test_repeated_explicit_judgments_uses_last_tag():
    """P6e-4 core: a self-correction (A first 支持 then 反驳) honors the last tag."""
    text = "A: 【支持】起初理由\nA: 【反驳】更正后的理由"
    j = MultiChoiceSolver._parse_judgments(text, OPTS)
    assert j["A"] == "反驳"


def test_repeated_then_reverted_uses_last():
    text = "B: 【反驳】第一\nB: 【支持】第二\nB: 【不确定】第三"
    j = MultiChoiceSolver._parse_judgments(text, OPTS)
    assert j["B"] == "不确定"


def test_no_tag_defaults_to_uncertain():
    j = MultiChoiceSolver._parse_judgments("无关文本，没有任何标签", OPTS)
    assert all(v == "不确定" for v in j.values())


def test_has_explicit_tag_detection():
    text = "A: 【支持】理由\nB: 没有标签"
    assert MultiChoiceSolver._has_explicit_tag(text, "A") is True
    assert MultiChoiceSolver._has_explicit_tag(text, "B") is False


def test_fullwidth_and_halfwidth_colon_both_supported():
    j1 = MultiChoiceSolver._parse_judgments("A:【支持】", OPTS)
    j2 = MultiChoiceSolver._parse_judgments("A：【支持】", OPTS)
    assert j1["A"] == "支持"
    assert j2["A"] == "支持"


def test_untagged_final_self_correction_uses_last_judgment():
    text = chr(10).join([
        "A: 【支持】初步判断",
        "B: 【支持】初步判断",
        "最终检查：",
        "A: 支持。最终确认",
        "B: 反驳。重新计算后否定",
    ])
    j = MultiChoiceSolver._parse_judgments(text, OPTS)
    assert j["A"] == "支持"
    assert j["B"] == "反驳"


def test_plain_prose_does_not_override_line_judgment():
    text = chr(10).join([
        "A: 【支持】明确判断",
        "后文讨论 A 可能反驳，但没有判断格式",
    ])
    j = MultiChoiceSolver._parse_judgments(text, OPTS)
    assert j["A"] == "支持"
