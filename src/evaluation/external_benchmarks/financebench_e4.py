"""FinanceBench-specific E4 evaluation wiring.

This module keeps FinanceBench benchmark document binding on the runtime
``Question`` while keeping Gold truth on the evaluator side.  It reuses the
existing AnswerAB contracts without changing the generic local-dataset loader.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agent.factory import PipelineFactory
from contracts import Question
from evaluation.answer_ab import AnswerABCase
from evaluation.external_benchmarks.financebench_adapter import (
    FINANCEBENCH_LICENSE_ID,
    RESEARCH_ONLY_USE_SCOPE,
    FORBIDDEN_RUNTIME_KEYS,
    FinanceBenchCase,
    load_financebench_cases,
)
from question.adapter import CanonicalQuestionAdapter


FROZEN_CASE_IDS = (
    "financebench_id_03029",
    "financebench_id_04672",
    "financebench_id_00499",
    "financebench_id_01226",
    "financebench_id_01865",
    "financebench_id_00807",
    "financebench_id_00941",
    "financebench_id_01858",
)
FROZEN_DOC_IDS = frozenset({"3M_2018_10K", "3M_2022_10K", "3M_2023Q2_10Q"})


@dataclass(frozen=True)
class FinanceBenchE4InventoryItem:
    case_id: str
    doc_name: str
    question_type: str
    use_scope: str
    license_id: str


@dataclass(frozen=True)
class FinanceBenchFactoryRetrievalResult:
    case_id: str
    doc_name: str
    retrieved_doc_ids: tuple[str, ...]
    retrieved_page_numbers: tuple[int, ...]
    request_source: str
    scope_provider_calls: int


def _recursive_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_recursive_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            keys.update(_recursive_keys(child))
    return keys


def runtime_gold_key_hits(question: Question) -> tuple[str, ...]:
    """Return forbidden FinanceBench Gold keys found in runtime Question.raw."""
    return tuple(sorted(FORBIDDEN_RUNTIME_KEYS.intersection(_recursive_keys(question.raw))))


def select_frozen_financebench_cases(
    source_path: Path,
    *,
    case_ids: Sequence[str] = FROZEN_CASE_IDS,
) -> tuple[FinanceBenchCase, ...]:
    """Load exactly the frozen external slice in the requested deterministic order."""
    loaded = load_financebench_cases(Path(source_path), use_scope=RESEARCH_ONLY_USE_SCOPE)
    by_id = {case.case_id: case for case in loaded}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"FinanceBench frozen cases missing: {missing}")
    selected = tuple(by_id[case_id] for case_id in case_ids)
    if len(selected) != len(case_ids) or len({case.case_id for case in selected}) != len(case_ids):
        raise ValueError("FinanceBench frozen slice must have unique case IDs")
    docs = {case.document.doc_name for case in selected}
    if not docs.issubset(FROZEN_DOC_IDS):
        raise ValueError(f"FinanceBench frozen slice escaped allowed documents: {sorted(docs)}")
    return selected


def build_financebench_e4_cases(
    cases: Iterable[FinanceBenchCase],
) -> tuple[AnswerABCase, ...]:
    """Convert isolated FinanceBench cases directly into AnswerAB cases.

    The runtime Question keeps benchmark ``candidate_doc_ids`` and never receives
    Gold answer/evidence fields.  Gold answer stays only on AnswerABCase.
    """
    adapter = CanonicalQuestionAdapter()
    result: list[AnswerABCase] = []
    for case in cases:
        question = adapter.adapt(case.runtime_question_payload)
        expected_scope = (case.document.doc_name,)
        if tuple(question.doc_ids) != ():
            raise ValueError(f"FinanceBench E4 runtime doc_ids must stay empty: {case.case_id}")
        if tuple(question.candidate_doc_ids) != expected_scope:
            raise ValueError(
                f"FinanceBench candidate document binding mismatch for {case.case_id}: "
                f"expected={expected_scope} actual={tuple(question.candidate_doc_ids)}"
            )
        leaked = runtime_gold_key_hits(question)
        if leaked:
            raise ValueError(f"FinanceBench runtime Gold leakage for {case.case_id}: {leaked}")
        result.append(
            AnswerABCase(
                case_id=case.case_id,
                question=question,
                gold_answers=(case.gold_label.answer,),
            )
        )
    return tuple(result)


def financebench_e4_inventory(
    cases: Iterable[FinanceBenchCase],
) -> tuple[FinanceBenchE4InventoryItem, ...]:
    return tuple(
        FinanceBenchE4InventoryItem(
            case_id=case.case_id,
            doc_name=case.document.doc_name,
            question_type=case.question_type,
            use_scope=case.use_scope,
            license_id=case.license_id,
        )
        for case in cases
    )


def build_financebench_preflight_config(
    base_config: Mapping[str, Any],
    *,
    processed_docs: str | Path,
) -> dict[str, Any]:
    """Build an in-memory config copy for the frozen FinanceBench E4 slice."""
    config = deepcopy(dict(base_config))
    pipeline = config.setdefault("pipeline", {})
    paths = config.setdefault("paths", {})
    retrieval = config.setdefault("retrieval", {})
    if not isinstance(pipeline, dict) or not isinstance(paths, dict) or not isinstance(retrieval, dict):
        raise ValueError("pipeline/paths/retrieval config sections must be mappings")
    pipeline["retriever"] = "canonical_lexical"
    paths["processed_docs"] = str(processed_docs)
    retrieval["canonical_top_k_per_doc"] = 5
    retrieval["canonical_window_chars"] = 1800
    retrieval["canonical_context_flank_chars"] = 600
    return config


def run_factory_retrieval_preflight(
    e4_cases: Sequence[AnswerABCase],
    *,
    config: Mapping[str, Any],
    project_root: Path,
) -> tuple[FinanceBenchFactoryRetrievalResult, ...]:
    """Run only preparation/classifier/retriever factory components.

    No workflow, solver, verifier, provider, or model client is constructed.
    """
    factory = PipelineFactory(config=dict(config), project_root=Path(project_root))
    preparation = factory.build_question_preparation_pipeline()
    classifier = factory.build_classifier()
    retriever = factory.build_retriever()
    rows: list[FinanceBenchFactoryRetrievalResult] = []
    for case in e4_cases:
        # Re-run C0/C1 from the already Gold-free runtime payload so the preflight
        # exercises the same factory preparation surface intended for real E4.
        prepared = preparation.prepare(case.question.raw)
        question = prepared.question
        if tuple(question.candidate_doc_ids) != tuple(case.question.candidate_doc_ids):
            raise AssertionError(f"candidate document binding changed during preparation: {case.case_id}")
        if runtime_gold_key_hits(question):
            raise AssertionError(f"Gold leaked during factory preparation: {case.case_id}")
        classification = classifier.classify(question)
        candidates = retriever.retrieve(question, classification)
        audit = dict(getattr(candidates, "audit_metadata", {}) or {})
        retrieved_doc_ids = tuple(dict.fromkeys(str(item.doc_id) for item in candidates))
        bound = tuple(question.candidate_doc_ids)
        if any(doc_id not in bound for doc_id in retrieved_doc_ids):
            raise AssertionError(
                f"factory retriever escaped FinanceBench candidate scope for {case.case_id}: "
                f"bound={bound} retrieved={retrieved_doc_ids}"
            )
        rows.append(
            FinanceBenchFactoryRetrievalResult(
                case_id=case.case_id,
                doc_name=bound[0],
                retrieved_doc_ids=retrieved_doc_ids,
                retrieved_page_numbers=tuple(
                    int(item.metadata.get("page_number") or 0) for item in candidates[:5]
                ),
                request_source=str(audit.get("retriever_scope_request_source") or ""),
                scope_provider_calls=int(audit.get("retriever_scope_provider_calls") or 0),
            )
        )
    return tuple(rows)


def validate_frozen_inventory(cases: Sequence[FinanceBenchCase]) -> None:
    if len(cases) != 8 or len({case.case_id for case in cases}) != 8:
        raise AssertionError("FinanceBench E4 preflight requires exactly 8 unique cases")
    docs = {case.document.doc_name for case in cases}
    if docs != FROZEN_DOC_IDS:
        raise AssertionError(f"FinanceBench E4 preflight requires exactly the frozen 3 docs: {docs}")
    for case in cases:
        if case.use_scope != RESEARCH_ONLY_USE_SCOPE:
            raise AssertionError(f"unexpected use scope for {case.case_id}: {case.use_scope}")
        if case.license_id != FINANCEBENCH_LICENSE_ID:
            raise AssertionError(f"unexpected license for {case.case_id}: {case.license_id}")
        if not case.gold_label.answer.strip():
            raise AssertionError(f"FinanceBench case missing Gold answer: {case.case_id}")
