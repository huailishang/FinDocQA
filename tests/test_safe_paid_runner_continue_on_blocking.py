from __future__ import annotations

import importlib.util
from pathlib import Path

from runtime_safety import CircuitBreaker


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "safe_paid_runner.py"


def _module():
    spec = importlib.util.spec_from_file_location("safe_paid_runner_module", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _business_block_record():
    return {
        "qid": "case_003",
        "error": "production_integrity:option evidence unresolved",
        "metadata": {
            "final_state": "BLOCKED",
            "answer_validation": "blocking_invalid",
            "blocking_reason": "production_integrity:option evidence unresolved",
            "blocking_reasons": ["option_evidence_unresolved"],
        },
    }


def test_authorized_business_blocks_do_not_trigger_accepted_rate_breaker():
    module = _module()
    breaker = CircuitBreaker("stable")
    for _ in range(12):
        stop = module._observe_with_continue_on_blocking_policy(
            breaker,
            _business_block_record(),
            continue_on_blocking=True,
            token_usage_known=True,
            per_question_budget_ok=True,
        )
        assert stop is None
    assert breaker.processed == 0


def test_business_block_still_requires_token_usage_and_budget_compliance():
    module = _module()
    breaker = CircuitBreaker("stable")
    assert module._observe_with_continue_on_blocking_policy(
        breaker,
        _business_block_record(),
        continue_on_blocking=True,
        token_usage_known=False,
        per_question_budget_ok=True,
    ) == "missing_token_usage"
    assert module._observe_with_continue_on_blocking_policy(
        breaker,
        _business_block_record(),
        continue_on_blocking=True,
        token_usage_known=True,
        per_question_budget_ok=False,
    ) == "per_question_token_budget_exceeded"


def test_without_authorization_legacy_stable_breaker_still_applies():
    module = _module()
    breaker = CircuitBreaker("stable")
    stop = None
    for _ in range(10):
        stop = module._observe_with_continue_on_blocking_policy(
            breaker,
            _business_block_record(),
            continue_on_blocking=False,
            token_usage_known=True,
            per_question_budget_ok=True,
        )
        if stop:
            break
    assert stop in {"four_of_first_five_same_reason", "five_consecutive_same_reason", "zero_accepted_in_first_ten"}


def test_canary_business_block_ignores_continue_authorization():
    module = _module()
    breaker = CircuitBreaker("canary")
    stop = module._observe_with_continue_on_blocking_policy(
        breaker,
        _business_block_record(),
        continue_on_blocking=False,
        token_usage_known=True,
        per_question_budget_ok=True,
    )
    assert stop == "first_unexpected_block"
    assert breaker.processed == 1
