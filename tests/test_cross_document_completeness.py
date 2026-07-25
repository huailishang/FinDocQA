from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path("tests/fixtures/cross_document_completeness_cases.json")


def _cases():
    payload=json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["auto_apply_submission"] is False
    return payload["cases"]


def _derived_complete(case):
    required=set(case["required_docs"])
    retrieved=set(case["retrieved_docs"])
    used=set(case["used_docs"])
    fields=case["fields_by_doc"]
    return required == retrieved == used and all(doc in fields and fields[doc] is not None for doc in required)


def test_complete_cases_use_every_required_document():
    for case in _cases():
        assert _derived_complete(case) is case["complete"]
        if case["complete"]:
            assert set(case["required_docs"]) == set(case["retrieved_docs"]) == set(case["used_docs"])


def test_missing_document_or_field_is_explicitly_incomplete():
    for case in _cases():
        if case["complete"]:
            continue
        assert case["incomplete_reason"] in {"missing_required_document", "missing_required_field"}
        assert not _derived_complete(case)


def test_extracted_values_enter_comparison_or_calculation_logic():
    ins=next(c for c in _cases() if c["qid"]=="case_013")
    assert sorted(ins["fields_by_doc"].values(), reverse=True) == [1440000,900000,850000,800000]
    res=next(c for c in _cases() if c["qid"]=="case_029")
    assert set(res["fields_by_doc"]["pack2_text04"] + res["fields_by_doc"]["pack2_text09"]) == set("ABCD")
