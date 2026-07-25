"""Offline frozen-input Prompt A/B harness for BB-P0-04C.

This script performs no provider calls.  It renders registered prompt builders
against immutable synthetic fixtures, evaluates fixed model-output fixtures and
writes audit artifacts.  It is explicitly not a model-quality benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from answer_contract import (  # noqa: E402
    build_question_answer_contract,
    contract_from_question,
    normalize_answer_candidate,
    validate_answer_against_contract,
)
from contracts import (  # noqa: E402
    ClassificationResult,
    EvidenceBundle,
    EvidenceCandidate,
    Question,
    QuestionLabel,
)
from evidence.prompt_budget import estimate_prompt_budget  # noqa: E402
from prompts.registry import (  # noqa: E402
    PromptRegistry,
    PromptRegistryEntry,
    PromptRegistryError,
    load_prompt_registry,
    normalize_template_text,
    sha256_text,
)
from solvers.calculation import CalculationSolver  # noqa: E402
from solvers.cross_doc import CrossDocSolver  # noqa: E402
from solvers.direct import DirectSolver  # noqa: E402
from solvers.freeform import parse_freeform_submission_answers  # noqa: E402
from solvers.multi_choice import MultiChoiceSolver  # noqa: E402

FIXTURE_SCHEMA_VERSION = "prompt_ab_fixture_v1"
REPORT_SCHEMA_VERSION = "prompt_ab_report_v1"
FORBIDDEN_ARM_KEYS = {
    "question",
    "classification",
    "evidence_bundle",
    "retrieval",
    "verification",
    "model",
    "model_id",
    "parameters",
    "generation_parameters",
    "input_override",
}
_OPTION_JUDGMENT_RE = re.compile(
    r"(?m)^\s*([A-D])\s*[:：]\s*【\s*(支持|反驳|不确定)\s*】\s*(.*)$"
)
_DOC_TAG_RE = re.compile(r"\[DOC:([^\]\s]+)\]", re.IGNORECASE)
_USED_DOC_LINE_RE = re.compile(r"(?m)^\s*使用文档\s*[:：]\s*(.+?)\s*$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromptRegistryError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _question_from_fixture(raw: Mapping[str, Any]) -> Question:
    question_raw = dict(raw.get("raw") or {})
    answer_format = str(raw.get("answer_format") or question_raw.get("answer_format") or "")
    contract = build_question_answer_contract(
        qid=str(raw.get("qid") or ""),
        raw_type=question_raw.get("type"),
        raw_answer_format=question_raw.get("answer_format", answer_format),
        options=dict(raw.get("options") or {}),
    )
    return Question(
        qid=str(raw.get("qid") or ""),
        domain=str(raw.get("domain") or ""),
        text=str(raw.get("text") or ""),
        options={str(key): str(value) for key, value in dict(raw.get("options") or {}).items()},
        answer_format=answer_format,
        doc_ids=tuple(str(item) for item in raw.get("doc_ids") or ()),
        candidate_doc_ids=tuple(str(item) for item in raw.get("candidate_doc_ids") or ()),
        submission_slot_count=(
            int(raw["submission_slot_count"])
            if raw.get("submission_slot_count") is not None
            else None
        ),
        submission_slot_contracts=tuple(dict(item) for item in raw.get("submission_slot_contracts") or ()),
        raw=question_raw,
        answer_contract=contract,
    )


def _candidate_from_fixture(raw: Mapping[str, Any]) -> EvidenceCandidate:
    return EvidenceCandidate(
        domain=str(raw.get("domain") or ""),
        doc_id=str(raw.get("doc_id") or ""),
        source=str(raw.get("source") or ""),
        text=str(raw.get("text") or ""),
        before_text=str(raw.get("before_text") or ""),
        after_text=str(raw.get("after_text") or ""),
        section_title=(str(raw.get("section_title")) if raw.get("section_title") is not None else None),
        score=float(raw.get("score") or 0.0),
        retriever=str(raw.get("retriever") or "offline_fixture"),
        metadata=dict(raw.get("metadata") or {}),
    )


def build_frozen_bundle(frozen_input: Mapping[str, Any]) -> EvidenceBundle:
    question_raw = frozen_input.get("question")
    classification_raw = frozen_input.get("classification")
    bundle_raw = frozen_input.get("evidence_bundle")
    if not isinstance(question_raw, Mapping) or not isinstance(classification_raw, Mapping) or not isinstance(bundle_raw, Mapping):
        raise PromptRegistryError("frozen_input must contain question, classification and evidence_bundle objects")

    question = _question_from_fixture(question_raw)
    labels: list[QuestionLabel] = []
    for value in classification_raw.get("labels") or []:
        labels.append(QuestionLabel(str(value)))
    classification = ClassificationResult(
        labels=tuple(labels),
        reasons=dict(classification_raw.get("reasons") or {}),
    )
    candidates = tuple(_candidate_from_fixture(item) for item in bundle_raw.get("candidates") or ())
    verification_candidates = tuple(
        _candidate_from_fixture(item) for item in bundle_raw.get("verification_candidates") or ()
    )
    return EvidenceBundle(
        question=question,
        classification=classification,
        candidates=candidates,
        prompt_context=str(bundle_raw.get("prompt_context") or ""),
        estimated_tokens=int(bundle_raw.get("estimated_tokens") or 0),
        metadata=dict(bundle_raw.get("metadata") or {}),
        verification_candidates=verification_candidates,
    )


def _render_source_prompt(entry: PromptRegistryEntry, bundle: EvidenceBundle) -> str:
    symbol = entry.source_symbol
    if symbol == "DirectSolver._build_prompt":
        return DirectSolver()._build_prompt(bundle)
    if symbol == "MultiChoiceSolver._build_prompt":
        return MultiChoiceSolver()._build_prompt(bundle)
    if symbol == "CrossDocSolver._build_prompt":
        return CrossDocSolver()._build_prompt(bundle)
    if symbol == "CalculationSolver._build_freeform_prompt":
        slots = bundle.question.submission_slot_count or 1
        return CalculationSolver()._build_freeform_prompt(bundle, slot_count=slots)
    if symbol == "CalculationSolver._build_one_call_prompt":
        return CalculationSolver()._build_one_call_prompt(bundle)
    if symbol == "CalculationSolver._build_extract_prompt":
        return CalculationSolver()._build_extract_prompt(bundle)
    raise PromptRegistryError(
        f"registered prompt is inventory-only and has no A/B renderer adapter: {entry.ref}:{symbol}"
    )


def render_registered_prompt(
    registry: PromptRegistry,
    entry: PromptRegistryEntry,
    bundle: EvidenceBundle,
) -> str:
    if entry.template_mode == "source":
        return _render_source_prompt(entry, bundle)
    if entry.template_mode == "append":
        base = registry.resolve_base(entry)
        if base is None:
            raise PromptRegistryError(f"append prompt missing base: {entry.ref}")
        base_prompt = render_registered_prompt(registry, base, bundle)
        return base_prompt.rstrip() + "\n\n" + normalize_template_text(entry.inline_template or "").rstrip()
    if entry.template_mode == "inline":
        return normalize_template_text(entry.inline_template or "").rstrip()
    raise PromptRegistryError(f"unsupported prompt template mode: {entry.ref}:{entry.template_mode}")


def _allowed_doc_ids(bundle: EvidenceBundle) -> list[str]:
    result: list[str] = []
    for candidate in bundle.candidates:
        if candidate.doc_id and candidate.doc_id not in result:
            result.append(candidate.doc_id)
    return result


def _referenced_docs(text: str, allowed_docs: Sequence[str]) -> list[str]:
    found: list[str] = []
    for value in _DOC_TAG_RE.findall(text or ""):
        if value in allowed_docs and value not in found:
            found.append(value)
    line = _USED_DOC_LINE_RE.search(text or "")
    if line:
        declared = line.group(1)
        for doc_id in allowed_docs:
            if doc_id in declared and doc_id not in found:
                found.append(doc_id)
    return found


def _selection_metrics(bundle: EvidenceBundle, raw_output: str) -> dict[str, Any]:
    question = bundle.question
    contract = contract_from_question(question)
    judgments = {
        label: verdict
        for label, verdict, _reason in _OPTION_JUDGMENT_RE.findall(raw_output or "")
        if label in question.options
    }
    if question.answer_format == "multi" and judgments:
        answer = "".join(sorted(label for label, verdict in judgments.items() if verdict == "支持"))
        validation = validate_answer_against_contract(answer, contract)
    else:
        validation = normalize_answer_candidate(raw_output, contract)
        answer = validation.answer

    option_lines = {
        label: reason
        for label, _verdict, reason in _OPTION_JUDGMENT_RE.findall(raw_output or "")
        if label in question.options
    }
    allowed_docs = _allowed_doc_ids(bundle)
    referenced = _referenced_docs(raw_output, allowed_docs)
    with_local_ref = 0
    for label, reason in option_lines.items():
        if any(doc_id in _referenced_docs(reason, allowed_docs) for doc_id in allowed_docs):
            with_local_ref += 1

    option_total = len(question.options)
    return {
        "output_parseable": bool(validation.valid),
        "parsed_answer": answer,
        "answer_contract_valid": bool(validation.valid),
        "answer_contract_reason": validation.reason,
        "evidence_referenced_doc_ids": referenced,
        "evidence_reference_completeness": (
            round(len(referenced) / len(allowed_docs), 4) if allowed_docs else 1.0
        ),
        "per_option_structure": {
            "judged_options": sorted(judgments),
            "expected_options": sorted(question.options),
            "coverage": round(len(judgments) / option_total, 4) if option_total else 1.0,
        },
        "per_option_evidence_reference_completeness": (
            round(with_local_ref / option_total, 4) if option_total else 1.0
        ),
        "calculation_formula_structure": {"applicable": False, "coverage": None},
    }


def _freeform_metrics(bundle: EvidenceBundle, raw_output: str) -> dict[str, Any]:
    question = bundle.question
    slots = question.submission_slot_count or 1
    parsed = parse_freeform_submission_answers(
        raw_output,
        expected_slots=slots,
        question_text=question.text,
        expected_slot_contracts=question.submission_slot_contracts,
    )
    allowed_docs = _allowed_doc_ids(bundle)
    referenced: list[str] = []
    for doc_id in parsed.used_doc_ids:
        if doc_id in allowed_docs and doc_id not in referenced:
            referenced.append(doc_id)

    formula_required = 0
    formula_complete = 0
    slot_evidence_complete = 0
    for item in parsed.answer_items:
        kind = str(item.get("expected_kind") or item.get("kind") or "")
        refs = [str(value) for value in item.get("evidence_refs") or () if str(value) in allowed_docs]
        for doc_id in refs:
            if doc_id not in referenced:
                referenced.append(doc_id)
        if refs:
            slot_evidence_complete += 1
        if kind in {"number", "percentage", "percentage_point"}:
            formula_required += 1
            formula_text = str(item.get("formula_text") or "").strip()
            variables = item.get("variables")
            computed = item.get("computed_result")
            if formula_text and isinstance(variables, Mapping) and bool(variables) and computed not in (None, ""):
                formula_complete += 1

    return {
        "output_parseable": bool(parsed.valid),
        "parsed_answers": list(parsed.answers),
        "answer_contract_valid": bool(parsed.valid),
        "answer_contract_reason": parsed.reason,
        "slot_validations": [dict(item) for item in parsed.slot_validations],
        "evidence_referenced_doc_ids": referenced,
        "evidence_reference_completeness": (
            round(len(referenced) / len(allowed_docs), 4) if allowed_docs else 1.0
        ),
        "per_slot_evidence_reference_completeness": (
            round(slot_evidence_complete / slots, 4) if slots else 1.0
        ),
        "per_option_structure": {"applicable": False, "coverage": None},
        "calculation_formula_structure": {
            "applicable": bool(formula_required),
            "required_slots": formula_required,
            "complete_slots": formula_complete,
            "coverage": (
                round(formula_complete / formula_required, 4) if formula_required else None
            ),
        },
    }


def evaluate_output_fixture(bundle: EvidenceBundle, raw_output: str) -> dict[str, Any]:
    metrics = (
        _freeform_metrics(bundle, raw_output)
        if bundle.question.answer_format == "freeform"
        else _selection_metrics(bundle, raw_output)
    )
    metrics["output_chars"] = len(raw_output or "")
    metrics["output_fixture_sha256"] = hashlib.sha256((raw_output or "").encode("utf-8")).hexdigest()
    return metrics


def _prompt_metrics(prompt: str, model_id: str) -> dict[str, Any]:
    budget = estimate_prompt_budget(model_id=model_id, rendered_context_chars=len(prompt))
    return {
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "estimated_prompt_text_tokens": budget.context_estimated_tokens,
        "conservative_prompt_tokens_with_fixed_overhead": budget.prompt_estimated_tokens,
        "token_estimator_policy": budget.policy_source,
    }


def _validate_fixture_shape(payload: Mapping[str, Any]) -> None:
    if str(payload.get("schema_version") or "") != FIXTURE_SCHEMA_VERSION:
        raise PromptRegistryError(f"unsupported A/B fixture schema: {payload.get('schema_version')!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise PromptRegistryError("A/B fixture must contain non-empty cases array")
    for case in cases:
        if not isinstance(case, Mapping):
            raise PromptRegistryError("A/B case must be an object")
        frozen_input = case.get("frozen_input")
        model_run = case.get("model_run")
        arms = case.get("arms")
        if not isinstance(frozen_input, Mapping) or not isinstance(model_run, Mapping):
            raise PromptRegistryError("each A/B case requires frozen_input and model_run objects")
        if not isinstance(arms, list) or len(arms) != 2:
            raise PromptRegistryError("each A/B case must have exactly two arms")
        for arm in arms:
            if not isinstance(arm, Mapping):
                raise PromptRegistryError("A/B arm must be an object")
            forbidden = sorted(FORBIDDEN_ARM_KEYS.intersection(arm.keys()))
            if forbidden:
                raise PromptRegistryError(f"A/B arm attempts non-prompt input override: {forbidden}")
            for field in ("name", "prompt_id", "version", "model_output"):
                if field not in arm:
                    raise PromptRegistryError(f"A/B arm missing field: {field}")


def _numeric_delta(a: Any, b: Any) -> float | None:
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return round(float(b) - float(a), 4)
    return None


def _case_delta(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        ("prompt_chars", a["prompt_metrics"].get("prompt_chars"), b["prompt_metrics"].get("prompt_chars")),
        (
            "estimated_prompt_text_tokens",
            a["prompt_metrics"].get("estimated_prompt_text_tokens"),
            b["prompt_metrics"].get("estimated_prompt_text_tokens"),
        ),
        ("output_chars", a["output_metrics"].get("output_chars"), b["output_metrics"].get("output_chars")),
        (
            "evidence_reference_completeness",
            a["output_metrics"].get("evidence_reference_completeness"),
            b["output_metrics"].get("evidence_reference_completeness"),
        ),
    )
    deltas = {name: _numeric_delta(left, right) for name, left, right in fields}
    option_a = a["output_metrics"].get("per_option_evidence_reference_completeness")
    option_b = b["output_metrics"].get("per_option_evidence_reference_completeness")
    if option_a is not None or option_b is not None:
        deltas["per_option_evidence_reference_completeness"] = _numeric_delta(option_a, option_b)
    slot_a = a["output_metrics"].get("per_slot_evidence_reference_completeness")
    slot_b = b["output_metrics"].get("per_slot_evidence_reference_completeness")
    if slot_a is not None or slot_b is not None:
        deltas["per_slot_evidence_reference_completeness"] = _numeric_delta(slot_a, slot_b)
    formula_a = (a["output_metrics"].get("calculation_formula_structure") or {}).get("coverage")
    formula_b = (b["output_metrics"].get("calculation_formula_structure") or {}).get("coverage")
    if formula_a is not None or formula_b is not None:
        deltas["calculation_formula_structure_coverage"] = _numeric_delta(formula_a, formula_b)
    return deltas


def evaluate_fixture(
    registry: PromptRegistry,
    fixture_payload: Mapping[str, Any],
    *,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    _validate_fixture_shape(fixture_payload)
    fixture_before = _fingerprint(fixture_payload)
    case_reports: list[dict[str, Any]] = []

    for case in fixture_payload["cases"]:
        frozen_input = case["frozen_input"]
        input_fingerprint = _fingerprint(frozen_input)
        bundle = build_frozen_bundle(frozen_input)
        model_run = case["model_run"]
        model_id = str(model_run.get("model_id") or "unknown")
        generation_parameters = dict(model_run.get("generation_parameters") or {})
        retrieval_fingerprint = _fingerprint(frozen_input.get("retrieval") or frozen_input.get("evidence_bundle") or {})
        verification_fingerprint = _fingerprint(frozen_input.get("verification") or {})

        arm_reports: list[dict[str, Any]] = []
        for arm in case["arms"]:
            entry = registry.get(str(arm["prompt_id"]), str(arm["version"]))
            prompt = render_registered_prompt(registry, entry, bundle)
            raw_output = str(arm.get("model_output") or "")
            arm_reports.append(
                {
                    "name": str(arm["name"]),
                    "prompt_ref": entry.ref,
                    "registry_template_hash": entry.template_hash,
                    "registry_model_profile": entry.model_profile,
                    "registry_parameters": dict(entry.parameters),
                    "change_note": entry.change_note,
                    "prompt_metrics": _prompt_metrics(prompt, model_id),
                    "output_metrics": evaluate_output_fixture(bundle, raw_output),
                }
            )

        a, b = arm_reports
        prompt_changed = a["prompt_metrics"]["prompt_sha256"] != b["prompt_metrics"]["prompt_sha256"]
        b_entry = registry.get(str(case["arms"][1]["prompt_id"]), str(case["arms"][1]["version"]))
        prompt_change_record = {
            "from_prompt_ref": a["prompt_ref"],
            "to_prompt_ref": b["prompt_ref"],
            "from_rendered_prompt_sha256": a["prompt_metrics"]["prompt_sha256"],
            "to_rendered_prompt_sha256": b["prompt_metrics"]["prompt_sha256"],
            "template_mode": b_entry.template_mode,
            "base_version": b_entry.base_version,
            "change_note": b_entry.change_note,
            "added_instruction": b_entry.inline_template if b_entry.template_mode == "append" else None,
        }
        case_reports.append(
            {
                "case_id": str(case.get("case_id") or ""),
                "question_kind": str(case.get("question_kind") or ""),
                "input_fingerprint": input_fingerprint,
                "retrieval_fingerprint": retrieval_fingerprint,
                "verification_fingerprint": verification_fingerprint,
                "model_id": model_id,
                "generation_parameters": generation_parameters,
                "input_same_across_arms": True,
                "model_and_parameters_same_across_arms": True,
                "prompt_changed": prompt_changed,
                "prompt_change_record": prompt_change_record,
                "non_prompt_changes": [],
                "change_attribution": (
                    "PROMPT_ONLY_OFFLINE_FIXTURE" if prompt_changed else "NO_PROMPT_CHANGE"
                ),
                "arms": arm_reports,
                "b_minus_a": _case_delta(a, b),
            }
        )

    fixture_after = _fingerprint(fixture_payload)
    if fixture_after != fixture_before:
        raise PromptRegistryError("A/B fixture mutated during evaluation")

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "OFFLINE_FIXED_OUTPUT_FIXTURE_NO_PROVIDER",
        "quality_claim": "NOT_EVALUATED_FIXTURE_ONLY",
        "provider_calls": 0,
        "fixture_path": str(fixture_path) if fixture_path else None,
        "fixture_sha256": fixture_before,
        "fixture_immutable": True,
        "production_prompt_modified": False,
        "production_injection_enabled": registry.production_injection_enabled,
        "cases": case_reports,
    }


def run(
    *,
    registry_path: Path,
    fixture_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    registry = load_prompt_registry(registry_path, repo_root=ROOT)
    fixture_payload = _read_json(fixture_path)
    report = evaluate_fixture(registry, fixture_payload, fixture_path=fixture_path)
    inventory = registry.inventory()
    unregistered = {
        "schema_version": "unregistered_prompt_paths_v1",
        "count": inventory["unregistered_solver_prompt_builders"],
        "items": inventory["unregistered"],
    }

    _write_json(output_dir / "prompt_registry_inventory.json", inventory)
    _write_json(output_dir / "unregistered_prompt_paths.json", unregistered)
    _write_json(output_dir / "prompt_ab_baseline.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "config" / "prompt_registry.json")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "bb_p0_04c_prompt_ab_fixture.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evaluation_artifacts" / "bb_p0_04c",
    )
    args = parser.parse_args()
    report = run(registry_path=args.registry, fixture_path=args.fixture, output_dir=args.output_dir)
    summary = {
        "provider_calls": report["provider_calls"],
        "fixture_immutable": report["fixture_immutable"],
        "production_prompt_modified": report["production_prompt_modified"],
        "cases": len(report["cases"]),
        "output_dir": str(args.output_dir),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
