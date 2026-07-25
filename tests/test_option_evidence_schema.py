from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verification.option_evidence_schema import (
    OPTION_LABELS,
    normalize_option_evidence_slots,
    replacement_decision_from_slots,
)

FIXTURE = ROOT / "tests" / "fixtures" / "option_evidence_schema_cases.json"
ANSWER_FORMATS = {"case_002": "multi", "case_023": "multi", "case_004": "tf"}


def _cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_normalizer_outputs_complete_abcd_slots_for_all_fixtures():
    for case in _cases():
        slots = normalize_option_evidence_slots(
            baseline_answer=case["baseline_answer"],
            proposed_answer=case["proposed_answer"],
            option_payloads=case.get("option_payloads", {}),
        )
        labels = [slot["option_label"] for slot in slots]
        assert labels == list(OPTION_LABELS)
        for slot in slots:
            assert set(slot) == {
                "option_label",
                "option_text",
                "status",
                "claim_route",
                "evidence_refs",
                "term_equivalence",
                "factual_statement_true",
                "question_scope_binding",
                "calculation_refs",
                "unresolved_reason",
                "replacement_effect",
            }


def test_missing_option_slot_becomes_unresolved_not_silent_accept():
    slots = normalize_option_evidence_slots(
        baseline_answer="A",
        proposed_answer="AB",
        option_payloads={"A": {"status": "supported", "claim_route": "direct_evidence"}},
    )
    slot_b = next(slot for slot in slots if slot["option_label"] == "B")
    assert slot_b["status"] == "unresolved"
    assert slot_b["unresolved_reason"] == "missing option evidence slot"
    decision = replacement_decision_from_slots(qid="missing_slot", baseline_answer="A", proposed_answer="AB", slots=slots, answer_format="multi")
    assert decision["accepted_for_replacement"] is False
    assert "option_slot_B_unresolved" in decision["block_reason"]


def test_replacement_policy_blocks_bad_delta_and_allows_clean_calculation_fixture():
    for case in _cases():
        slots = normalize_option_evidence_slots(
            baseline_answer=case["baseline_answer"],
            proposed_answer=case["proposed_answer"],
            option_payloads=case.get("option_payloads", {}),
        )
        decision = replacement_decision_from_slots(
            qid=case["qid"],
            baseline_answer=case["baseline_answer"],
            proposed_answer=case["proposed_answer"],
            slots=slots,
            calculation_grounding=case.get("calculation_grounding"),
            answer_format=ANSWER_FORMATS[case["qid"]],
        )
        assert decision["accepted_for_replacement"] is case["expected_accepted_for_replacement"]
        expected_reason = case.get("expected_block_reason_contains") or ""
        if expected_reason:
            assert expected_reason in decision["block_reason"]
        else:
            assert decision["block_reason"] == ""


def test_calculation_block_flags_prevent_replacement_even_with_option_support():
    case = next(item for item in _cases() if item["qid"] == "case_004")
    slots = normalize_option_evidence_slots(
        baseline_answer=case["baseline_answer"],
        proposed_answer=case["proposed_answer"],
        option_payloads=case.get("option_payloads", {}),
    )
    decision = replacement_decision_from_slots(
        qid="case_004_zero_match_guard",
        baseline_answer=case["baseline_answer"],
        proposed_answer=case["proposed_answer"],
        slots=slots,
        calculation_grounding={"zero_match": True},
        answer_format="tf",
    )
    assert decision["accepted_for_replacement"] is False
    assert "calculation_zero_match" in decision["block_reason"]


if __name__ == "__main__":
    test_normalizer_outputs_complete_abcd_slots_for_all_fixtures()
    test_missing_option_slot_becomes_unresolved_not_silent_accept()
    test_replacement_policy_blocks_bad_delta_and_allows_clean_calculation_fixture()
    test_calculation_block_flags_prevent_replacement_even_with_option_support()
    print("option evidence schema fixture tests: PASS")
