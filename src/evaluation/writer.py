"""Submission and debug output writer."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from answer_contract import contract_from_mapping
from contracts import PipelineResult
from evaluation.formal_submission import (
    COMPATIBLE_TEMPLATE_HEADERS,
    FORMAL_SUBMISSION_HEADER,
    LEGACY_TEMPLATE_HEADER,
    validate_reasoning_contract,
)
from solvers.base import validate_submission_answer
from submission_contract import validate_result_ledger_tokens
from verification.dual_lineage import accepted_final_state
from verification.production_integrity import validate_results_before_write


SUBMISSION_HEADER = FORMAL_SUBMISSION_HEADER
LEGACY_TEMPLATE_HEADER = LEGACY_TEMPLATE_HEADER


@dataclass(frozen=True)
class SubmissionTemplate:
    """Safe schema view of the official multi-slot submission template.

    Only qid order, answer-slot presence and column order are retained. Template
    placeholder values are deliberately discarded so they cannot become answer
    truth or inference input.
    """

    header: tuple[str, ...]
    qid_order: tuple[str, ...]
    slot_count_by_qid: Mapping[str, int]

    @classmethod
    def load(cls, path: Path) -> "SubmissionTemplate":
        template_path = Path(path)
        with template_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            raise ValueError(f"empty multi-slot submission template: {template_path}")
        header = tuple(str(value).strip() for value in rows[0])
        if header not in set(COMPATIBLE_TEMPLATE_HEADERS):
            raise ValueError(
                "invalid multi-slot submission template header: "
                f"expected legacy {LEGACY_TEMPLATE_HEADER!r} or formal {SUBMISSION_HEADER!r}, got {header!r}"
            )

        summary_rows = [index for index, row in enumerate(rows[1:], start=1) if row and row[0].strip() == "summary"]
        if summary_rows != [1]:
            raise ValueError("multi-slot template must contain exactly one summary row immediately after header")

        qid_order: list[str] = []
        slot_count_by_qid: dict[str, int] = {}
        for row_number, raw_row in enumerate(rows[2:], start=3):
            row = list(raw_row) + [""] * max(0, len(header) - len(raw_row))
            qid = str(row[0]).strip()
            if not qid:
                continue
            if qid in slot_count_by_qid:
                raise ValueError(f"duplicate qid {qid!r} in multi-slot template row {row_number}")
            occupied = [bool(str(value).strip()) for value in row[1:5]]
            slot_count = sum(occupied)
            if slot_count < 1:
                raise ValueError(f"qid {qid!r} has no answer slot in multi-slot template")
            if occupied != ([True] * slot_count + [False] * (4 - slot_count)):
                raise ValueError(f"qid {qid!r} has non-contiguous answer slots in multi-slot template")
            qid_order.append(qid)
            slot_count_by_qid[qid] = slot_count

        if not qid_order:
            raise ValueError("multi-slot template contains no business qids")
        return cls(
            header=header,
            qid_order=tuple(qid_order),
            slot_count_by_qid=dict(slot_count_by_qid),
        )

    @property
    def slot_distribution(self) -> dict[int, int]:
        distribution: dict[int, int] = {}
        for count in self.slot_count_by_qid.values():
            distribution[int(count)] = distribution.get(int(count), 0) + 1
        return distribution


class CsvSubmissionWriter:
    VALID_ARTIFACT_MODES = {"standard", "evaluation-only"}
    VALID_SUBMISSION_MODES = {"legacy_single", "multi_slot"}

    def __init__(
        self,
        output_dir: Path,
        artifact_mode: str = "standard",
        *,
        submission_mode: str = "legacy_single",
        submission_template_path: Path | None = None,
    ) -> None:
        mode = str(artifact_mode or "standard").strip().lower()
        if mode not in self.VALID_ARTIFACT_MODES:
            raise ValueError(f"unsupported artifact_mode: {artifact_mode!r}")
        board_mode = str(submission_mode or "legacy_single").strip().lower()
        if board_mode not in self.VALID_SUBMISSION_MODES:
            raise ValueError(f"unsupported submission_mode: {submission_mode!r}")
        self.output_dir = Path(output_dir)
        self.artifact_mode = mode
        self.submission_mode = board_mode
        self.submission_template_path = (
            Path(submission_template_path) if submission_template_path is not None else None
        )

    @property
    def submission_enabled(self) -> bool:
        return self.artifact_mode == "standard"

    def write_checkpoint(self, results: Sequence[PipelineResult]) -> None:
        """Persist partial/debug state without attempting submission emission."""
        validate_results_before_write(results, allow_failed=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        debug_path = self.output_dir / "debug_results.json"
        with debug_path.open("w", encoding="utf-8") as handle:
            json.dump([asdict(result) for result in results], handle, ensure_ascii=False, indent=2)

    def write_final(self, results: Sequence[PipelineResult]) -> None:
        """Emit a final submission only after the board contract is complete."""
        if not self.submission_enabled:
            return
        validate_results_before_write(results, allow_failed=True)
        if self.submission_mode == "multi_slot":
            submission_rows = self._build_multi_slot_rows(results)
        else:
            submission_rows = self._build_legacy_rows(results)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        submission_path = self.output_dir / "submission.csv"
        with submission_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(submission_rows)

    def write(self, results: Sequence[PipelineResult]) -> None:
        """Backward-compatible complete artifact write.

        Final validation runs before debug emission so the historical legacy
        behavior remains atomic. Runtime checkpoints should call
        ``write_checkpoint`` explicitly.
        """
        if self.submission_enabled:
            self.write_final(results)
        self.write_checkpoint(results)

    def selection_matches_final_contract(self, qids: Sequence[str]) -> bool:
        """Return whether a selected run explicitly targets a final submission set."""
        if self.submission_mode != "multi_slot":
            return True
        if self.submission_template_path is None:
            return False
        template = SubmissionTemplate.load(self.submission_template_path)
        selected = tuple(str(qid) for qid in qids)
        return len(selected) == len(set(selected)) and set(selected) == set(template.qid_order)

    def _accepted_results(self, results: Sequence[PipelineResult]) -> list[PipelineResult]:
        accepted: list[PipelineResult] = []
        for item in results:
            final_state = str(item.metadata.get("final_state") or "accepted")
            if accepted_final_state(final_state) and not item.error:
                accepted.append(item)
        return accepted

    def _validate_one_answer(self, item: PipelineResult, answer: Any) -> tuple[str, str | None]:
        answer_format = item.metadata.get("answer_format")
        raw_contract = item.metadata.get("answer_contract")
        contract = contract_from_mapping(raw_contract)
        check = validate_submission_answer(
            str(answer or ""),
            str(answer_format or ""),
            answer_contract=contract,
        )
        return check.answer, None if check.valid else check.reason

    def _build_legacy_rows(self, results: Sequence[PipelineResult]) -> list[list[Any]]:
        validated: list[tuple[PipelineResult, str]] = []
        invalid: list[tuple[str, str, Any, str]] = []
        for item in self._accepted_results(results):
            answer, reason = self._validate_one_answer(item, item.answer)
            if reason is None:
                validated.append((item, answer))
            else:
                invalid.append((item.qid, item.answer, item.metadata.get("answer_format"), reason))
        if invalid:
            details = ", ".join(
                f"{qid}={answer!r} format={fmt!r} reason={reason}"
                for qid, answer, fmt, reason in invalid
            )
            raise ValueError(f"invalid submission answer(s): {details}")

        ordered = sorted(validated, key=lambda pair: pair[0].qid)
        token_rows = {
            item.qid: validate_result_ledger_tokens(
                qid=item.qid,
                prompt_tokens=item.prompt_tokens,
                completion_tokens=item.completion_tokens,
                total_tokens=item.total_tokens,
                metadata=item.metadata,
            )
            for item, _ in ordered
        }
        total_prompt = sum(values[0] for values in token_rows.values())
        total_completion = sum(values[1] for values in token_rows.values())
        rows: list[list[Any]] = [
            ["qid", "answer", "prompt_tokens", "completion_tokens", "total_tokens"],
            ["summary", "", total_prompt, total_completion, total_prompt + total_completion],
        ]
        for item, answer in ordered:
            prompt, completion, total = token_rows[item.qid]
            rows.append([item.qid, answer, prompt, completion, total])
        return rows

    def _build_multi_slot_rows(self, results: Sequence[PipelineResult]) -> list[list[Any]]:
        if self.submission_template_path is None:
            raise ValueError("multi_slot submission_mode requires submission_template_path")
        template = SubmissionTemplate.load(self.submission_template_path)
        accepted = self._accepted_results(results)
        by_qid: dict[str, PipelineResult] = {}
        duplicates: list[str] = []
        for item in accepted:
            if item.qid in by_qid:
                duplicates.append(item.qid)
            by_qid[item.qid] = item
        if duplicates:
            raise ValueError(f"duplicate accepted multi-slot result qids: {sorted(set(duplicates))}")

        template_qids = set(template.qid_order)
        result_qids = set(by_qid)
        missing = [qid for qid in template.qid_order if qid not in result_qids]
        extra = sorted(result_qids - template_qids)
        if missing or extra:
            raise ValueError(
                "multi-slot result qid set does not match template: "
                f"missing={missing[:20]} extra={extra[:20]}"
            )

        normalized_by_qid: dict[str, tuple[str, ...]] = {}
        reasoning_by_qid: dict[str, str] = {}
        invalid: list[str] = []
        for qid in template.qid_order:
            item = by_qid[qid]
            raw_answers: Sequence[Any]
            if item.submission_answers:
                raw_answers = tuple(item.submission_answers)
            else:
                metadata_answers = item.metadata.get("submission_answers")
                if isinstance(metadata_answers, Sequence) and not isinstance(metadata_answers, (str, bytes)):
                    raw_answers = tuple(metadata_answers)
                else:
                    raw_answers = (item.answer,)
            expected_slots = int(template.slot_count_by_qid[qid])
            if len(raw_answers) != expected_slots:
                invalid.append(
                    f"{qid} requires {expected_slots} answer slots but received {len(raw_answers)}"
                )
                continue
            normalized: list[str] = []
            for index, raw_answer in enumerate(raw_answers, start=1):
                answer, reason = self._validate_one_answer(item, raw_answer)
                if reason is not None:
                    invalid.append(
                        f"{qid}.answer_{index}={raw_answer!r} reason={reason}"
                    )
                normalized.append(answer)
            normalized_by_qid[qid] = tuple(normalized)
            reasoning_check = validate_reasoning_contract(item.reasoning, answers=tuple(normalized))
            if not reasoning_check.valid:
                invalid.append(f"{qid}.reasoning reason={reasoning_check.reason}")
            reasoning_by_qid[qid] = reasoning_check.normalized
        if invalid:
            raise ValueError("invalid multi-slot submission answer(s): " + "; ".join(invalid))

        token_rows = {
            qid: validate_result_ledger_tokens(
                qid=qid,
                prompt_tokens=by_qid[qid].prompt_tokens,
                completion_tokens=by_qid[qid].completion_tokens,
                total_tokens=by_qid[qid].total_tokens,
                metadata=by_qid[qid].metadata,
            )
            for qid in template.qid_order
        }
        total_prompt = sum(values[0] for values in token_rows.values())
        total_completion = sum(values[1] for values in token_rows.values())
        total_all = sum(values[2] for values in token_rows.values())
        if total_all != total_prompt + total_completion:
            raise ValueError("multi-slot summary token equation mismatch")
        rows: list[list[Any]] = [
            list(SUBMISSION_HEADER),
            ["summary", "", "", "", "", total_prompt, total_completion, total_all, ""],
        ]
        for qid in template.qid_order:
            answers = list(normalized_by_qid[qid])
            prompt, completion, total = token_rows[qid]
            rows.append(
                [qid, *answers, *("" for _ in range(4 - len(answers))), prompt, completion, total, reasoning_by_qid[qid]]
            )
        return rows
