from __future__ import annotations

import csv
from pathlib import Path

import pytest

from contracts import ClassificationResult, PipelineResult, SolverResult
from evaluation.formal_submission import (
    FORMAL_SUBMISSION_HEADER,
    EmptyVisibleOutputError,
    FormalSubmissionError,
    build_formal_output_instruction,
    parse_formal_model_output,
    validate_reasoning_contract,
    validate_reasoning_self_contained,
)
from evaluation.token_accounting import TokenAccountingError, read_multi_slot_submission
from evaluation.writer import CsvSubmissionWriter


GOOD_REASONING = "依据题目给出的关键事实逐项核验，相关条件均能直接支持该结果，因此按要求输出最终答案。"


def _write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _result(*, reasoning: str = GOOD_REASONING) -> PipelineResult:
    return PipelineResult(
        qid="q1",
        answer="42",
        classification=ClassificationResult(labels=[]),
        solver_result=SolverResult(qid="q1", answer="42", solver="fixture"),
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        reasoning=reasoning,
        metadata={"answer_format": "freeform", "final_state": "accepted"},
        submission_answers=("42",),
    )


def test_reasoning_contract_requires_visible_substantive_summary() -> None:
    assert validate_reasoning_contract(GOOD_REASONING, answers=("42",)).valid is True
    assert validate_reasoning_contract("", answers=("42",)).reason == "reasoning_missing"
    assert validate_reasoning_contract("答案为42", answers=("42",)).valid is False
    assert validate_reasoning_contract("证据不足，无法确定最终结果，请人工复核。", answers=("42",)).valid is False


def test_reasoning_contract_rejects_explicit_mcq_contradiction() -> None:
    check = validate_reasoning_contract(
        "法规条文支持选项A，并排除其他选项；但结论为B，因此这里出现了直接矛盾。",
        answers=("A",),
    )
    assert check.valid is False
    assert "reasoning_explicit_answer_contradiction" in check.violations


def test_r2_reasoning_self_contained_rejects_doc_only_lineage() -> None:
    check = validate_reasoning_self_contained(
        "根据 DOC:1 和 DOC:2 的证据可以判断，因此最终答案为A。",
        answers=("A",),
        question_type="多选题",
        expected_slots=1,
    )
    assert check.valid is False
    assert "reasoning_lineage_only_or_fact_too_thin" in check.violations or "reasoning_missing_concrete_fact" in check.violations


def test_r2_reasoning_self_contained_accepts_selection_fact_logic_conclusion() -> None:
    check = validate_reasoning_self_contained(
        "DOC:1 的监管规定明确要求机构必须在交易前完成客户身份核验；A符合该规则，B和C缺少该前置条件，因此最终答案为A。",
        answers=("A",),
        question_type="单选题",
        expected_slots=1,
    )
    assert check.valid is True


def test_r2_reasoning_self_contained_accepts_calculation_inputs_formula_result() -> None:
    check = validate_reasoning_self_contained(
        "DOC:1 给出基数100和增量25，按增长率=25/100×100%计算得到25%，因此最终结果为25%。",
        answers=("25%",),
        question_type="计算题",
        expected_slots=1,
    )
    assert check.valid is True


def test_r2_calculation_formula_ending_in_answer_is_explicit_conclusion_without_template_word() -> None:
    check = validate_reasoning_self_contained(
        "DOC:2 给出89万元，DOC:3给出88.2万元，另外两份合同金额为70万元和86万元，四份合计可退还金额=89+88.2+70+86=333.2万元。",
        answers=("333.20",),
        question_type="计算题",
        expected_slots=1,
    )
    assert check.valid is True


def test_r2_reasoning_self_contained_requires_all_multi_slot_results() -> None:
    check = validate_reasoning_self_contained(
        "报告给出收入100和成本40，按收入减成本计算利润为60，因此第一个结果为100。",
        answers=("100", "75"),
        question_type="计算题",
        expected_slots=2,
    )
    assert check.valid is False
    assert "reasoning_multi_slot_result_not_fully_covered" in check.violations


def test_formal_visible_output_requires_exact_answers_and_reasoning_json() -> None:
    parsed = parse_formal_model_output(
        '{"answers":["AC"],"reasoning":"条文明确支持A和C两项，同时排除了B和D，因此最终选择AC作为答案。"}',
        expected_slots=1,
    )
    assert parsed.answers == ("AC",)
    assert len(parsed.reasoning) >= 20

    with pytest.raises(FormalSubmissionError, match="exactly answers and reasoning"):
        parse_formal_model_output(
            '{"answers":["AC"],"reasoning":"条文明确支持A和C两项，同时排除了B和D，因此最终选择AC作为答案。","extra":1}',
            expected_slots=1,
        )


def test_empty_visible_output_has_independent_failure_type() -> None:
    with pytest.raises(EmptyVisibleOutputError, match="EMPTY_VISIBLE_OUTPUT") as caught:
        parse_formal_model_output("", expected_slots=1)
    assert caught.value.failure_class == "EMPTY_VISIBLE_OUTPUT"


def test_formal_prompt_is_question_type_specific_self_contained_and_has_no_answer_hint() -> None:
    calculation = build_formal_output_instruction(question_type="计算题", expected_slots=3)
    selection = build_formal_output_instruction(question_type="多选题", expected_slots=1)
    assert "公式/关系" in calculation
    assert "支持或排除" in selection
    assert "独立 Judge" in calculation and "不会看到题目" in calculation
    assert "关键事实/数值/条款" in calculation
    assert "80~320" in calculation
    assert "DOC:n" in selection and "不能替代关键事实" in selection
    assert "历史答案" in calculation and "历史答案" in selection
    assert calculation != selection


def test_formal_prompt_makes_answer_slot_cardinality_unambiguous() -> None:
    multi_slot = build_formal_output_instruction(question_type="计算题", expected_slots=2)
    multi_choice = build_formal_output_instruction(question_type="多选题", expected_slots=1)

    assert "answers 数组必须恰好有 2 项" in multi_slot
    assert "一槽一项" in multi_slot
    assert "逐槽覆盖" in multi_slot
    assert "禁止把多个槽位" in multi_slot

    assert "answers 数组必须恰好有 1 项" in multi_choice
    assert '["ACD"]' in multi_choice
    assert '["A","C","D"]' in multi_choice


def test_formal_reader_rejects_legacy_eight_column_header(tmp_path: Path) -> None:
    path = tmp_path / "legacy.csv"
    _write_rows(path, [
        ["qid", "answer_1", "answer_2", "answer_3", "answer_4", "prompt_tokens", "completion_tokens", "total_tokens"],
        ["summary", "", "", "", "", 10, 5, 15],
        ["q1", "42", "", "", "", 10, 5, 15],
    ])
    with pytest.raises(TokenAccountingError, match="invalid multi-slot CSV header"):
        read_multi_slot_submission(path)


def test_formal_reader_accepts_exact_r2_underscore_header_with_reasoning(tmp_path: Path) -> None:
    path = tmp_path / "r2.csv"
    _write_rows(path, [
        list(FORMAL_SUBMISSION_HEADER),
        ["summary", "", "", "", "", 10, 5, 15, ""],
        ["q1", "42", "", "", "", 10, 5, 15, GOOD_REASONING],
    ])
    payload = read_multi_slot_submission(path)
    assert payload["header"] == list(FORMAL_SUBMISSION_HEADER)
    assert payload["summary"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_formal_reader_rejects_old_no_underscore_scoring_header(tmp_path: Path) -> None:
    path = tmp_path / "old_scoring.csv"
    _write_rows(path, [
        ["qid", "answer1", "answer2", "answer3", "answer4", "prompt_tokens", "completion_tokens", "total_tokens", "reasoning"],
        ["summary", "", "", "", "", 10, 5, 15, ""],
        ["q1", "42", "", "", "", 10, 5, 15, GOOD_REASONING],
    ])
    with pytest.raises(TokenAccountingError, match="invalid multi-slot CSV header"):
        read_multi_slot_submission(path)


def test_writer_uses_legacy_template_only_for_qid_and_slot_shape(tmp_path: Path) -> None:
    template = tmp_path / "template.csv"
    _write_rows(template, [
        ["qid", "answer_1", "answer_2", "answer_3", "answer_4", "prompt_tokens", "completion_tokens", "total_tokens"],
        ["summary", "", "", "", "", 0, 0, 0],
        ["q1", "sample", "", "", "", 0, 0, 0],
    ])
    out = tmp_path / "out"
    CsvSubmissionWriter(
        out,
        submission_mode="multi_slot",
        submission_template_path=template,
    ).write([_result()])

    with (out / "submission.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == FORMAL_SUBMISSION_HEADER
    assert rows[0][1:5] == ["answer_1", "answer_2", "answer_3", "answer_4"]
    assert rows[1] == ["summary", "", "", "", "", "10", "5", "15", ""]
    assert rows[2][0] == "q1"
    assert rows[2][8] == GOOD_REASONING
    read_multi_slot_submission(out / "submission.csv")


def test_writer_rejects_missing_or_short_reasoning(tmp_path: Path) -> None:
    template = tmp_path / "template.csv"
    _write_rows(template, [
        ["qid", "answer_1", "answer_2", "answer_3", "answer_4", "prompt_tokens", "completion_tokens", "total_tokens"],
        ["summary", "", "", "", "", 0, 0, 0],
        ["q1", "sample", "", "", "", 0, 0, 0],
    ])
    writer = CsvSubmissionWriter(tmp_path / "out", submission_mode="multi_slot", submission_template_path=template)
    with pytest.raises(ValueError, match="reasoning"):
        writer.write([_result(reasoning="只有答案42")])
