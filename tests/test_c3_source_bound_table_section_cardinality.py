from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import fields, replace
import json
from pathlib import Path

import pytest

from calculation import (
    ExecutionGateFact,
    FormulaSourceRef,
    SourceBoundTableMember,
    SourceBoundTableMemberCollection,
    SourceBoundTableSectionCardinalityCounter,
    SourceBoundTableSectionCardinalityRequest,
    SourceSeriesBindingStatus,
    TableSectionAxisType,
)
from calculation.section_cardinality import (
    AMBIGUOUS_COLLECTION_RANGE,
    CROSS_SOURCE_COLLECTION,
    DUPLICATE_COORDINATE,
    EMPTY_COLLECTION,
    INVALID_COLLECTION,
    INVALID_COLLECTION_MEMBER,
    MISSING_LINEAGE,
    QUESTION_CARDINALITY_MISMATCH,
    UNSUPPORTED_AXIS_TYPE,
)
from evaluation.external_benchmarks.c3_oracle_baseline import (
    execute_c3_runtime,
    run_cases,
)
from evaluation.external_benchmarks.contracts import TerminalClassification
from evaluation.external_benchmarks.tatqa_adapter import (
    TATQASectionCardinalityOracleRuntime,
    _section_cardinality_runtime_from_proof,
    load_tatqa_cases,
)

SOURCE = "document://demo/table/1"
TATQA_SOURCE = Path(
    "evaluation_artifacts/external_benchmarks/tatqa/dataset_raw/"
    "tatqa_dataset_dev.json"
)
TAXONOMY = Path(
    "evaluation_artifacts/c3_unsupported_operator_triage_v1/"
    "per_case_taxonomy.jsonl"
)
DEFERRED_ASSETS_CASE = "520740d2-1345-496c-9013-1bbe687913aa"
ALTERNATIVE_INVESTMENTS_CASE = "c5a8edfd-93eb-4e95-a787-1bebe231c7c7"
EXECUTIVE_OFFICERS_CASE = "e2665282-60dd-4ef1-b29b-fde6a2628d9d"
ACCEPTED_CASES = {
    DEFERRED_ASSETS_CASE,
    ALTERNATIVE_INVESTMENTS_CASE,
    EXECUTIVE_OFFICERS_CASE,
}


def _member(
    position: int,
    *,
    label: object | None = None,
    coordinate: object | None = None,
    source_object_id: object = SOURCE,
    source_ref: object = ...,
) -> SourceBoundTableMember:
    member_label = f"member-{position + 1}" if label is None else label
    source_coordinate = f"{SOURCE}/r{position + 1}c0" if coordinate is None else coordinate
    if source_ref is ...:
        source_ref = FormulaSourceRef(
            doc_id="document-demo",
            page_number=1,
            source=source_object_id,  # type: ignore[arg-type]
            block_id="table-1",
            excerpt=str(member_label),
        )
    return SourceBoundTableMember(
        position=position,
        member_label=member_label,  # type: ignore[arg-type]
        source_ref=source_ref,  # type: ignore[arg-type]
        source_coordinate=source_coordinate,  # type: ignore[arg-type]
        source_object_id=source_object_id,  # type: ignore[arg-type]
    )


def _collection(
    member_count: int = 4,
    **changes: object,
) -> SourceBoundTableMemberCollection:
    members = changes.pop(
        "members",
        tuple(_member(position) for position in range(member_count)),
    )
    return SourceBoundTableMemberCollection(
        collection_id=changes.pop("collection_id", "collection-demo"),  # type: ignore[arg-type]
        members=members,  # type: ignore[arg-type]
        source_object_id=changes.pop("source_object_id", SOURCE),  # type: ignore[arg-type]
        axis_type=changes.pop(
            "axis_type", TableSectionAxisType.ROWS_IN_BOUND_SECTION
        ),  # type: ignore[arg-type]
        binding_status=changes.pop(
            "binding_status", SourceSeriesBindingStatus.EXACT
        ),  # type: ignore[arg-type]
        range_explicit=changes.pop("range_explicit", True),  # type: ignore[arg-type]
        boundary_rows_excluded=changes.pop(
            "boundary_rows_excluded", True
        ),  # type: ignore[arg-type]
    )


def _request(
    *,
    collection: object | None = None,
    question_match: object = True,
) -> SourceBoundTableSectionCardinalityRequest:
    fact = (
        question_match
        if isinstance(question_match, ExecutionGateFact)
        else ExecutionGateFact(question_match)  # type: ignore[arg-type]
    )
    return SourceBoundTableSectionCardinalityRequest(
        collection=collection if collection is not None else _collection(),  # type: ignore[arg-type]
        question_cardinality_match=fact,  # type: ignore[arg-type]
    )


def _blocked(request: object, reason: str) -> None:
    result = SourceBoundTableSectionCardinalityCounter().execute(request)  # type: ignore[arg-type]
    assert result.ok is False
    assert result.value is None
    assert result.formula_program is None
    assert result.gate_status == "NOT_READY"
    assert result.audit_reasons
    assert reason in result.audit_reasons


@pytest.mark.parametrize("member_count", [1, 4, 7])
def test_valid_collections_return_stable_non_negative_integer(member_count: int) -> None:
    request = _request(collection=_collection(member_count))
    first = SourceBoundTableSectionCardinalityCounter().execute(request)
    second = SourceBoundTableSectionCardinalityCounter().execute(request)

    assert first.ok is True
    assert type(first.value) is int
    assert first.value == member_count
    assert first.value >= 0
    assert first.display_value == str(member_count)
    assert first.gate_status == "PASS"
    assert first.formula_program is None
    assert first.to_dict() == second.to_dict()
    assert len(first.source_refs) == member_count
    assert [row["position"] for row in first.trace[:-1]] == list(
        range(member_count)
    )
    assert [row["member_label"] for row in first.trace[:-1]] == [
        member.member_label for member in request.collection.members
    ]
    assert first.trace[-1] == {
        "trace_type": "section_cardinality_summary",
        "collection_id": request.collection.collection_id,
        "axis_type": request.collection.axis_type.value,
        "member_count": member_count,
    }


@pytest.mark.parametrize(
    ("cardinality_request", "reason"),
    [
        (object(), INVALID_COLLECTION),
        (_request(collection=object()), INVALID_COLLECTION),
        (_request(collection=_collection(members=())), EMPTY_COLLECTION),
        (_request(collection=_collection(members=object())), INVALID_COLLECTION_MEMBER),
        (_request(collection=_collection(members=(object(),))), INVALID_COLLECTION_MEMBER),
        (
            _request(
                collection=_collection(
                    members=(replace(_member(0), position=True),)
                )
            ),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(
                collection=_collection(
                    members=(_member(0), replace(_member(1), position=3))
                )
            ),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(collection=_collection(members=(_member(0, label=" "),))),
            INVALID_COLLECTION_MEMBER,
        ),
        (
            _request(collection=_collection(members=(_member(0, label=object()),))),
            INVALID_COLLECTION_MEMBER,
        ),
        (
            _request(
                collection=_collection(members=(_member(0, source_ref=None),))
            ),
            MISSING_LINEAGE,
        ),
        (
            _request(
                collection=_collection(members=(_member(0, source_ref=object()),))
            ),
            MISSING_LINEAGE,
        ),
        (
            _request(
                collection=_collection(
                    members=(
                        _member(
                            0,
                            source_ref=FormulaSourceRef(
                                doc_id="",
                                page_number=1,
                                source=SOURCE,
                            ),
                        ),
                    )
                )
            ),
            MISSING_LINEAGE,
        ),
        (
            _request(collection=_collection(members=(_member(0, coordinate=" "),))),
            MISSING_LINEAGE,
        ),
        (
            _request(
                collection=_collection(
                    members=(
                        _member(0, coordinate="same"),
                        _member(1, coordinate="same"),
                    )
                )
            ),
            DUPLICATE_COORDINATE,
        ),
        (
            _request(
                collection=_collection(
                    members=(
                        _member(0),
                        _member(1, source_object_id="document://other/table/2"),
                    )
                )
            ),
            CROSS_SOURCE_COLLECTION,
        ),
        (
            _request(
                collection=_collection(
                    members=(
                        _member(
                            0,
                            source_ref=FormulaSourceRef(
                                doc_id="document-demo",
                                page_number=1,
                                source="document://other/table/2",
                            ),
                        ),
                    )
                )
            ),
            CROSS_SOURCE_COLLECTION,
        ),
        (
            _request(collection=replace(_collection(), collection_id=" ")),
            MISSING_LINEAGE,
        ),
        (
            _request(collection=replace(_collection(), source_object_id=None)),
            MISSING_LINEAGE,
        ),
        (
            _request(collection=replace(_collection(), axis_type="ROWS_IN_BOUND_SECTION")),
            UNSUPPORTED_AXIS_TYPE,
        ),
        (
            _request(collection=replace(_collection(), axis_type=None)),
            UNSUPPORTED_AXIS_TYPE,
        ),
        (
            _request(collection=replace(_collection(), binding_status="EXACT")),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(
                collection=replace(
                    _collection(), binding_status=SourceSeriesBindingStatus.AMBIGUOUS
                )
            ),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(collection=replace(_collection(), range_explicit="true")),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(collection=replace(_collection(), range_explicit=False)),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(collection=replace(_collection(), boundary_rows_excluded=1)),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (
            _request(collection=replace(_collection(), boundary_rows_excluded=False)),
            AMBIGUOUS_COLLECTION_RANGE,
        ),
        (_request(question_match=False), QUESTION_CARDINALITY_MISMATCH),
        (_request(question_match=None), QUESTION_CARDINALITY_MISMATCH),
        (
            replace(_request(), question_cardinality_match=object()),
            QUESTION_CARDINALITY_MISMATCH,
        ),
    ],
)
def test_invalid_contracts_fail_closed_without_exception_escape(
    cardinality_request: object,
    reason: str,
) -> None:
    _blocked(cardinality_request, reason)


def _section_inputs() -> tuple[
    dict[str, tuple[dict[str, object], dict[str, object]]],
    dict[str, dict[str, object]],
]:
    payload = json.loads(TATQA_SOURCE.read_text(encoding="utf-8"))
    index = {
        question["uid"]: (document["table"], question)
        for document in payload
        for question in document["questions"]
    }
    rows = [
        json.loads(line)
        for line in TAXONOMY.read_text(encoding="utf-8").splitlines()
        if line
    ]
    proofs = {
        row["case_id"]: row["oracle_proof"]
        for row in rows
        if row.get("candidate_capability")
        == "SOURCE_BOUND_TABLE_SECTION_CARDINALITY"
        and row.get("candidate_type") == "PRODUCT_CAPABILITY"
        and row.get("selection_eligibility") is True
        and row.get("binding_uniqueness_status") == "UNIQUE"
        and (row.get("oracle_proof") or {}).get("proof_status") == "COMPLETE"
        and (row.get("oracle_proof") or {}).get("binding_uniqueness_status")
        == "UNIQUE"
    }
    return index, proofs


def _build_runtime_from_copies(
    case_id: str,
    *,
    axis_changes: dict[str, object] | None = None,
    member_mutator=None,
    table_mutator=None,
    proof_mutator=None,
) -> TATQASectionCardinalityOracleRuntime:
    index, proofs = _section_inputs()
    table_payload, question = deepcopy(index[case_id])
    proof = deepcopy(proofs[case_id])
    if axis_changes:
        proof["bound_axis_or_section"].update(axis_changes)
    if member_mutator is not None:
        member_mutator(proof["bound_member_or_value_coordinates"])
    if table_mutator is not None:
        table_mutator(table_payload["table"])
    if proof_mutator is not None:
        proof_mutator(proof)
    return _section_cardinality_runtime_from_proof(
        table_payload=table_payload,
        question_row=question,
        proof=proof,
    )


def test_selected_section_subset_is_exact_source_bound_and_correct() -> None:
    selected = [
        case
        for case in load_tatqa_cases(TATQA_SOURCE)
        if isinstance(case.runtime, TATQASectionCardinalityOracleRuntime)
    ]
    assert len(selected) == 3
    assert {case.case_id for case in selected} == ACCEPTED_CASES
    assert Counter(case.runtime.oracle_axis for case in selected) == {
        "ROWS_IN_BOUND_SECTION": 2,
        "WHOLE_TABLE_ENTITY_ROWS": 1,
    }
    assert Counter(
        len(case.runtime.section_request.collection.members) for case in selected
    ) == {4: 2, 7: 1}
    assert sum(
        len(case.runtime.section_request.collection.members) for case in selected
    ) == 15

    records = run_cases(selected)
    assert all(
        record.terminal_classification is TerminalClassification.EXECUTED_CORRECT
        for record in records
    )
    for case in selected:
        runtime = case.runtime
        assert isinstance(runtime, TATQASectionCardinalityOracleRuntime)
        request = runtime.section_request
        assert request is not None
        assert request.question_cardinality_match.passed is True
        assert request.collection.binding_status is SourceSeriesBindingStatus.EXACT
        assert request.collection.range_explicit is True
        assert request.collection.boundary_rows_excluded is True
        observation = execute_c3_runtime(runtime)
        assert observation.ok is True
        assert type(int(observation.answer)) is int
        assert len(observation.source_lineage) == len(request.collection.members)
        assert len(observation.trace) == len(request.collection.members) + 1
        assert observation.trace[-1]["member_count"] == len(
            request.collection.members
        )


@pytest.mark.parametrize(
    "axis_changes",
    [
        {"section_phrase": "not the real section"},
        {"section_phrase": None},
        {"start_row": 5},
        {"end_row_exclusive": 7},
        {"start_row": True},
        {"end_row_exclusive": True},
    ],
)
def test_bound_section_axis_tampering_fails_closed(
    axis_changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            axis_changes=axis_changes,
        )


def test_bound_section_heading_missing_or_duplicate_fails_closed() -> None:
    def remove_heading(table: list[list[object]]) -> None:
        table[3][0] = "not the section"

    def duplicate_heading(table: list[list[object]]) -> None:
        table[2][0] = "Deferred tax assets"

    with pytest.raises(ValueError, match="heading mismatch"):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            table_mutator=remove_heading,
        )
    with pytest.raises(ValueError, match="heading is ambiguous"):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            table_mutator=duplicate_heading,
        )


def test_bound_section_coordinated_truncation_and_middle_omission_fail_closed() -> None:
    def truncate(proof: dict[str, object]) -> None:
        proof["bound_axis_or_section"]["end_row_exclusive"] = 7
        proof["bound_member_or_value_coordinates"] = proof[
            "bound_member_or_value_coordinates"
        ][:-1]
        proof["independently_derived_expected_count"] = 3

    def omit_middle(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        proof["bound_member_or_value_coordinates"] = [
            members[0],
            members[1],
            members[3],
        ]
        proof["independently_derived_expected_count"] = 3

    with pytest.raises(ValueError, match="official complete range"):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            proof_mutator=truncate,
        )
    with pytest.raises(ValueError):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            proof_mutator=omit_middle,
        )


def test_bound_section_summary_boundary_is_excluded_and_independently_required() -> None:
    runtime = _build_runtime_from_copies(DEFERRED_ASSETS_CASE)
    assert [member.member_label for member in runtime.section_request.collection.members] == [
        "Post-retirement and pension benefit costs",
        "Net operating loss carryforwards",
        "Other employee benefits",
        "Other",
    ]

    def remove_boundary(table: list[list[object]]) -> None:
        table[8][0] = "Subtotal deferred tax assets"

    def add_unlisted_detail(table: list[list[object]]) -> None:
        table.insert(8, ["New independently present detail", "1", "1"])

    def include_boundary(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        source = str(members[-1]["coordinate"]).split("/r", 1)[0]
        members.append(
            {
                "row_index": 8,
                "column_index": 0,
                "coordinate": f"{source}/r8c0",
                "member_label": "Gross deferred tax assets",
            }
        )
        proof["bound_axis_or_section"]["end_row_exclusive"] = 9
        proof["independently_derived_expected_count"] = 5

    with pytest.raises(ValueError, match="boundary missing"):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            table_mutator=remove_boundary,
        )
    with pytest.raises(ValueError, match="official complete range"):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            table_mutator=add_unlisted_detail,
        )
    with pytest.raises(ValueError, match="official complete range"):
        _build_runtime_from_copies(
            DEFERRED_ASSETS_CASE,
            proof_mutator=include_boundary,
        )


def test_total_boundary_section_is_supported_but_not_counted() -> None:
    runtime = _build_runtime_from_copies(ALTERNATIVE_INVESTMENTS_CASE)
    assert runtime.section_request is not None
    assert [member.member_label for member in runtime.section_request.collection.members] == [
        "Private equities",
        "Hedge funds",
        "Real estate",
        "Other",
    ]
    result = SourceBoundTableSectionCardinalityCounter().execute(
        runtime.section_request
    )
    assert result.value == 4


@pytest.mark.parametrize(
    "axis_changes",
    [
        {"header_row": 1},
        {"header_row": True},
        {"start_row": 2},
        {"end_row_exclusive": 7},
        {"end_row_exclusive": True},
    ],
)
def test_whole_table_axis_tampering_fails_closed(
    axis_changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _build_runtime_from_copies(
            EXECUTIVE_OFFICERS_CASE,
            axis_changes=axis_changes,
        )


def test_whole_table_coordinated_truncation_omission_and_duplicates_fail_closed() -> None:
    def truncate(proof: dict[str, object]) -> None:
        proof["bound_axis_or_section"]["end_row_exclusive"] = 7
        proof["bound_member_or_value_coordinates"] = proof[
            "bound_member_or_value_coordinates"
        ][:-1]
        proof["independently_derived_expected_count"] = 6

    def omit_middle(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        proof["bound_member_or_value_coordinates"] = members[:3] + members[4:]
        proof["independently_derived_expected_count"] = 6

    def duplicate_member(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        members[1] = deepcopy(members[0])

    for mutator in (truncate, omit_middle, duplicate_member):
        with pytest.raises(ValueError):
            _build_runtime_from_copies(
                EXECUTIVE_OFFICERS_CASE,
                proof_mutator=mutator,
            )


def test_whole_table_header_inclusion_and_new_unlisted_entity_fail_closed() -> None:
    def include_header(proof: dict[str, object]) -> None:
        members = proof["bound_member_or_value_coordinates"]
        source = str(members[0]["coordinate"]).split("/r", 1)[0]
        proof["bound_member_or_value_coordinates"] = [
            {
                "row_index": 0,
                "column_index": 0,
                "coordinate": f"{source}/r0c0",
                "member_label": "Name",
            },
            *members,
        ]
        proof["bound_axis_or_section"]["start_row"] = 0
        proof["independently_derived_expected_count"] = 8

    def append_entity(table: list[list[object]]) -> None:
        table.append(["New Officer", "40", "Vice President"])

    with pytest.raises(ValueError, match="official complete range"):
        _build_runtime_from_copies(
            EXECUTIVE_OFFICERS_CASE,
            proof_mutator=include_header,
        )
    with pytest.raises(ValueError, match="official complete range"):
        _build_runtime_from_copies(
            EXECUTIVE_OFFICERS_CASE,
            table_mutator=append_entity,
        )


def test_product_contract_and_source_have_no_dataset_case_or_answer_dispatch() -> None:
    names = {
        item.name.lower()
        for contract in (
            SourceBoundTableMember,
            SourceBoundTableMemberCollection,
            SourceBoundTableSectionCardinalityRequest,
        )
        for item in fields(contract)
    }
    forbidden = {
        "dataset",
        "benchmark",
        "case_id",
        "qid",
        "gold",
        "answer",
        "expected_count",
    }
    assert not names.intersection(forbidden)

    product_source = Path("src/calculation/section_cardinality.py").read_text(
        encoding="utf-8"
    ).lower()
    adapter_source = Path(
        "src/evaluation/external_benchmarks/tatqa_adapter.py"
    ).read_text(encoding="utf-8")
    assert "tatqa" not in product_source
    assert "finqa" not in product_source
    assert "case_id" not in product_source
    assert "answer" not in product_source
    assert "expected_count" not in product_source
    assert "except exception" not in product_source
    for case_id in ACCEPTED_CASES:
        assert case_id not in adapter_source
