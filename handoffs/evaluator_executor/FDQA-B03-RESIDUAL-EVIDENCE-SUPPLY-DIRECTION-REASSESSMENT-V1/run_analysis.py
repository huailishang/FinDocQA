"""Deterministic zero-API H-61 audit generator.

Reads only frozen local evidence/code metadata and writes the four machine
artifacts declared by CONTRACT_A1.md. It does not generate answers, call
models/providers, alter rankings, or rebuild the input/mechanism freezes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[2]
BASE = TASK.parent
H60 = BASE / "FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1"
H50 = BASE / "FDQA-B03-CROSS-COHORT-LANE-ARBITRATION-DIAGNOSTIC-V1"

H60_SUMMARY = H60 / "BOUNDARY_CAPABILITY_SUMMARY.json"
H60_BASELINE = H60 / "BASELINE_RRF60_RESULTS.jsonl"
H60_CANDIDATE = H60 / "CANDIDATE_WORKSPACE_RESULTS.jsonl"
H60_RUNTIME = H60 / "H60_RUNTIME_INPUTS.jsonl"
H50_MATRIX = H50 / "LANE_OUTCOME_MATRIX.jsonl"
H50_TRACE = H50 / "LANE_SIGNAL_TRACE.jsonl"

OUTPUTS = (
    "CASE_FUNNEL.jsonl",
    "RESIDUAL_CASES.jsonl",
    "DOWNSTREAM_AUDIT.json",
    "DECISION.json",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def line_for(path: Path, needle: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return number
    raise ValueError(f"needle not found in {path}: {needle}")


def ref_line(path: Path, needle: str) -> str:
    return f"{rel(path)}:{line_for(path, needle)}"


def all_gold_reached(gold: list[int], pages: list[int]) -> bool:
    return set(gold).issubset(set(pages))


def case_funnel() -> list[dict[str, Any]]:
    summary = read_json(H60_SUMMARY)
    baseline = {row["qid"]: row for row in read_jsonl(H60_BASELINE)}
    candidate = {row["qid"]: row for row in read_jsonl(H60_CANDIDATE)}
    page_text = {
        (row["document_family"], row["page"]): row["text"]
        for row in read_jsonl(H60_RUNTIME)
        if row.get("kind") == "page"
    }
    rows: list[dict[str, Any]] = []
    for source in summary["cases"]:
        qid = source["qid"]
        family = source["document_family"]
        gold = source["canonical_gold_pages"]
        base_pages = list(baseline[qid]["verification_pages"])
        cand_pages = list(candidate[qid]["verification_workspace_pages"])
        rows.append(
            {
                "qid": qid,
                "document_family": family,
                "lexical_gold_reach": all_gold_reached(gold, source["lexical_top5"]),
                "semantic_gold_reach": all_gold_reached(gold, source["semantic_top5"]),
                "baseline_gold_reach": all_gold_reached(gold, base_pages),
                "candidate_gold_reach": all_gold_reached(gold, cand_pages),
                "baseline_pages": base_pages,
                "candidate_pages": cand_pages,
                "baseline_text_chars": sum(
                    len(page_text[(family, page)]) for page in base_pages
                ),
                "candidate_text_chars": sum(
                    len(page_text[(family, page)]) for page in cand_pages
                ),
                "candidate_claim_available": False,
                "verifier_executed": False,
                "fact_sufficiency": "NOT_MEASURED",
                "answer_correctness": "NOT_MEASURED",
            }
        )
    return rows


def residual_cases() -> list[dict[str, Any]]:
    h60_summary = read_json(H60_SUMMARY)
    h50_rows = read_jsonl(H50_MATRIX)
    h50_trace = {row["question_id"]: row for row in read_jsonl(H50_TRACE)}
    result: list[dict[str, Any]] = []

    for row in h60_summary["cases"]:
        if row["candidate_gold_reach"]:
            continue
        qid = row["qid"]
        result.append(
            {
                "cohort": "H60",
                "qid": qid,
                "document_family": row["document_family"],
                "source_ref": rel(H60_SUMMARY),
                "diagnostic_label": "BOUNDED_WORKSPACE_MISS",
                "evidence_refs": [
                    ref_line(H60_SUMMARY, f'"qid": "{qid}"'),
                    f"{rel(H60_BASELINE)}:{line_for(H60_BASELINE, qid)}",
                    f"{rel(H60_CANDIDATE)}:{line_for(H60_CANDIDATE, qid)}",
                ],
            }
        )

    for row in h50_rows:
        if row["lane_outcome_class"] != "BOTH_MISS":
            continue
        qid = row["question_id"]
        trace = h50_trace[qid]
        overlap = int(trace["lexical_semantic_top5_overlap_count"])
        label = (
            "BOTH_MISS_LANES_DISAGREE"
            if overlap <= 1
            else "BOTH_MISS_SHARED_DISTRACTORS"
        )
        result.append(
            {
                "cohort": "H50",
                "qid": qid,
                "document_family": row["doc_name"],
                "source_ref": rel(H50_MATRIX),
                "diagnostic_label": label,
                "evidence_refs": [
                    f"{rel(H50_MATRIX)}:{line_for(H50_MATRIX, qid)}",
                    f"{rel(H50_TRACE)}:{line_for(H50_TRACE, qid)}",
                ],
            }
        )

    return result


def downstream_audit() -> dict[str, Any]:
    h60_script = rel(H60 / "run_h60_cloudflare_workspace.py")
    return {
        "h60_status_origin": "STATIC_INPUT_APPLICABILITY",
        "observed_verifier_executions": 0,
        "observed_answer_evaluations": 0,
        "runtime_claim_source": None,
        "edges": [
            {
                "producer": "H-60 retrieval/workspace runner",
                "consumer": "downstream_support_measurement",
                "required_fields": [
                    "qid",
                    "document_family",
                    "verification_pages",
                    "verification_workspace_pages",
                ],
                "evidence_refs": [
                    f"{h60_script}:964",
                    f"{h60_script}:968",
                    f"{h60_script}:975",
                ],
                "finding": (
                    "The H-60 function writes VERIFIER_UNSUPPORTED from a static "
                    "input-applicability branch; it does not call a verifier per case."
                ),
            },
            {
                "producer": "candidate answer assertion / option text",
                "consumer": "parse_financial_claim",
                "required_fields": [
                    "question",
                    "candidate assertion text",
                    "entity",
                    "metric",
                    "relation",
                    "period/value when required",
                ],
                "evidence_refs": [
                    "src/verification/financial_claim_ast.py:261",
                    "src/verification/financial_claim_ast.py:300",
                ],
                "finding": (
                    "Existing financial claim parsing is assertion-oriented. "
                    "H-60 supplies a freeform question but no non-Gold candidate assertion."
                ),
            },
            {
                "producer": "FinancialClaimSpec + source facts",
                "consumer": "ClaimFactBinding",
                "required_fields": [
                    "entity",
                    "metric",
                    "period/comparison_period",
                    "unit",
                    "document binding",
                    "canonical_source",
                    "local_window",
                ],
                "evidence_refs": [
                    "src/verification/claim_fact_binding.py:346",
                    "src/verification/claim_fact_binding.py:367",
                    "src/verification/claim_fact_binding.py:415",
                ],
                "finding": (
                    "Binding is fail-closed on semantic slots, document identity, unit, "
                    "and lineage; page reach alone is not enough."
                ),
            },
            {
                "producer": "ClaimFactBinding + facts + formula/claim payload",
                "consumer": "FinancialEvidenceSufficiency",
                "required_fields": [
                    "claim spec",
                    "bound facts",
                    "formula/status",
                    "declared_doc_ids",
                    "canonical_source/local_window",
                ],
                "evidence_refs": [
                    "src/verification/evidence_sufficiency.py:80",
                    "src/verification/evidence_sufficiency.py:129",
                    "src/verification/evidence_sufficiency.py:155",
                ],
                "finding": (
                    "Sufficiency depends on resolved atoms, safe binding, lineage, "
                    "document boundary and a complete formula; none was observed for H-60."
                ),
            },
            {
                "producer": "EvidenceBundle.metadata.evidence_workspace_scope",
                "consumer": "production typed/derived financial evidence routes",
                "required_fields": [
                    "allowed canonical pages",
                    "doc_id",
                    "canonical page mapping",
                    "fail-closed outside/unknown page semantics",
                ],
                "evidence_refs": [
                    "src/verification/production_typed_evidence.py:700",
                    "src/verification/production_typed_evidence.py:711",
                    "src/verification/evidence_workspace.py:54",
                    "src/verification/evidence_workspace.py:142",
                    "src/verification/derived_claim_router.py:594",
                    "src/verification/financial_report_claims.py:1151",
                ],
                "finding": (
                    "Workspace scope and lineage interfaces exist and are propagated by "
                    "the repaired product route; H-61 found no evidence that scope wiring "
                    "is the missing H-60 consumer input."
                ),
            },
        ],
        "missing_contracts": [
            {
                "id": "MC-01",
                "boundary": "freeform question/workspace -> candidate assertion",
                "status": "MISSING_FOR_H60_OBSERVATION",
                "required_minimum": [
                    "non-Gold candidate assertion text or typed claim",
                    "provenance to the producing answer/solver artifact",
                    "question/document identity",
                    "workspace scope identity",
                ],
                "evidence_refs": [
                    f"{h60_script}:975",
                    f"{h60_script}:977",
                    f"{h60_script}:978",
                    f"{h60_script}:981",
                ],
                "interpretation": (
                    "This is an input/adapter gap in the H-60 measurement boundary, "
                    "not evidence that the verifier implementation failed."
                ),
            },
            {
                "id": "MC-02",
                "boundary": "candidate assertion -> deterministic verification result",
                "status": "NOT_OBSERVED_ON_H60",
                "required_minimum": [
                    "parsed claim spec",
                    "bound source facts",
                    "lineage/scope audit",
                    "sufficiency outcome",
                ],
                "evidence_refs": [
                    "src/verification/financial_claim_ast.py:261",
                    "src/verification/claim_fact_binding.py:346",
                    "src/verification/evidence_sufficiency.py:80",
                ],
                "interpretation": (
                    "The consumer APIs exist, but H-60 did not provide the assertion "
                    "needed to exercise them on these 12 freeform cases."
                ),
            },
            {
                "id": "MC-03",
                "boundary": "verified assertion -> final freeform answer evaluation",
                "status": "NOT_MEASURED",
                "required_minimum": [
                    "answer artifact",
                    "answer-to-claim linkage",
                    "Gold-independent verification observation",
                    "separate final-answer scoring authority",
                ],
                "evidence_refs": [
                    "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:295",
                    "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:302",
                ],
                "interpretation": (
                    "H-61 cannot infer end-to-end answer correctness from page reach "
                    "or static verifier applicability."
                ),
            },
        ],
        "scope_and_lineage_findings": [
            {
                "finding": "Workspace scope is an immutable allow-list and fails closed outside/unknown pages.",
                "evidence_refs": [
                    "src/verification/evidence_workspace.py:54",
                    "src/verification/evidence_workspace.py:142",
                    "src/verification/evidence_workspace.py:149",
                ],
            },
            {
                "finding": (
                    "H-56R1/H-60 historical evidence already closed product-route scope "
                    "reachability as the active blocker; H-61 does not reopen it."
                ),
                "evidence_refs": [
                    "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:332"
                ],
            },
            {
                "finding": (
                    "Page reach, fact sufficiency, verifier execution and answer correctness "
                    "are distinct measurement stages and remain separated."
                ),
                "evidence_refs": [
                    "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:302"
                ],
            },
        ],
    }


def decision(residual: list[dict[str, Any]]) -> dict[str, Any]:
    h60_count = sum(row["cohort"] == "H60" for row in residual)
    h50_count = sum(row["cohort"] == "H50" for row in residual)
    return {
        "retrieval_direction": "NO_SINGLE_DIRECTION",
        "supporting_cases": [],
        "next_action": "DOWNSTREAM_CONTRACT_FIRST",
        "reason": (
            f"Residual evidence supply remains real ({h60_count} H-60 misses and "
            f"{h50_count} H-50 BOTH_MISS observations), but no newly admitted "
            "Gold-free mechanism survives the historical fresh counterevidence. "
            "At the same time H-60's 12 VERIFIER_UNSUPPORTED rows are static "
            "input-applicability declarations: no candidate assertion was produced "
            "and no verifier or answer evaluation ran. The next bounded information "
            "gain is therefore to freeze and measure the missing freeform "
            "question/workspace -> candidate assertion -> verifier consumption "
            "contract before another retrieval or judge expansion."
        ),
        "evidence_refs": [
            "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:292",
            "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:295",
            "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:300",
            "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:327",
            "docs/evaluation/PROJECT_BOTTLENECK_MAP.md:328",
            "handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/run_h60_cloudflare_workspace.py:964",
            "handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/run_h60_cloudflare_workspace.py:975",
        ],
        "alternatives": {
            "B-03": {
                "status": "ACTIVE_MEASURED_BUT_NO_NEW_QUALIFIED_MECHANISM",
                "evidence_strength": "high for residual loss; insufficient for another mechanism",
                "scope": "H-50 18/46 BOTH_MISS; H-60 3/12 residual",
                "cost_dependency": (
                    "Another retrieval experiment needs a new Gold-free mechanism and "
                    "fresh validation; fixed-neighbor/gap-fill/unchanged target planning are blocked by counterevidence."
                ),
                "priority_implication": "Do not spend the next bounded task on retrieval tuning.",
            },
            "B-05": {
                "status": "WATCH",
                "evidence_strength": "medium phenomenon / low question-level impact",
                "scope": "2124 complex/image/span tables observed, question-level impact unknown",
                "cost_dependency": "Needs question-level attribution before parser work is justified.",
                "priority_implication": "Not promoted by H-61.",
            },
            "B-06": {
                "status": "SECONDARY_BLOCKED_BY_MEASUREMENT_BOUNDARY",
                "evidence_strength": "known-wrong judge evidence exists; known-correct/general freeform authority remains incomplete",
                "scope": "end-to-end freeform answer scoring",
                "cost_dependency": (
                    "H-61 has zero observed answer evaluations; expanding the judge now "
                    "would skip the missing candidate-assertion/verifier-consumption boundary."
                ),
                "priority_implication": "Do not jump directly to judge expansion.",
            },
            "B-07": {
                "status": "SECONDARY_NO_SINGLE_COMMON_FAMILY",
                "evidence_strength": "high on historical AMD cohort / medium global",
                "scope": "provider/output-gate reliability",
                "cost_dependency": "No new common provider/output failure family was observed in H-61.",
                "priority_implication": "No new reason to promote.",
            },
        },
        "historical_counterevidence": [
            "H-43 fresh target-guided planner: 0/7 recovery, NO_MEASURABLE_GAIN.",
            "H-53 fixed ±2 neighbor fresh12: 2/12 recovery, NOT_QUALIFIED, high added-page cost.",
            "H-60 local gap-fill signal replayed on H-50 18 BOTH_MISS: 0/18 for max_gap 2/3/4.",
        ],
        "fresh_failure_condition": (
            "Any future retrieval probe must predeclare a materially new Gold-free trigger "
            "and fail if it does not recover >=3 independent qids across >=2 document families "
            "under its frozen fresh measurement boundary."
        ),
        "historical_verdicts_unchanged": True,
        "default_enable_authorized": False,
        "product_readiness_proven": False,
        "end_to_end_improvement": "NOT_MEASURED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    if not output.is_absolute():
        output = (ROOT / output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    funnel = case_funnel()
    residual = residual_cases()
    audit = downstream_audit()
    verdict = decision(residual)

    write_jsonl(output / "CASE_FUNNEL.jsonl", funnel)
    write_jsonl(output / "RESIDUAL_CASES.jsonl", residual)
    write_json(output / "DOWNSTREAM_AUDIT.json", audit)
    write_json(output / "DECISION.json", verdict)

    print(
        json.dumps(
            {
                "case_funnel_rows": len(funnel),
                "residual_rows": len(residual),
                "h60_residual": sum(r["cohort"] == "H60" for r in residual),
                "h50_both_miss": sum(r["cohort"] == "H50" for r in residual),
                "retrieval_direction": verdict["retrieval_direction"],
                "next_action": verdict["next_action"],
                "external_api_calls": 0,
                "model_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
