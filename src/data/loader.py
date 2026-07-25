"""Question loading and normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from answer_contract import build_question_answer_contract, contract_to_dict
from contracts import Question


class JsonQuestionLoader:
    """Load Tianchi question JSON/JSONL files into the shared Question contract.

    Both legacy JSON arrays and multi-slot mixed JSON/JSONL inputs are supported.
    Files are decoded with ``utf-8-sig`` so an optional UTF-8 BOM is harmless.
    """

    def __init__(
        self,
        questions_dir: Path,
        *,
        submission_slot_count_by_qid: Mapping[str, int] | None = None,
        submission_slot_contracts_by_qid: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        require_submission_slot_contract: bool = False,
    ) -> None:
        self.questions_dir = Path(questions_dir)
        self.submission_slot_count_by_qid = {
            str(qid): int(count)
            for qid, count in dict(submission_slot_count_by_qid or {}).items()
        }
        self.submission_slot_contracts_by_qid = {
            str(qid): tuple(dict(slot) for slot in slots)
            for qid, slots in dict(submission_slot_contracts_by_qid or {}).items()
        }
        self.require_submission_slot_contract = bool(require_submission_slot_contract)

    def load(self) -> Sequence[Question]:
        questions: List[Question] = []
        seen_qids: dict[str, str] = {}
        files = sorted(
            [*self.questions_dir.glob("*.json"), *self.questions_dir.glob("*.jsonl")],
            key=lambda path: path.name,
        )
        for source_file in files:
            for row, source_line in self._iter_rows(source_file):
                source_ref = f"{source_file.name}:{source_line}"
                question = self._normalize(
                    row,
                    source_file=source_file,
                    source_line=source_line,
                )
                previous = seen_qids.get(question.qid)
                if previous is not None:
                    raise ValueError(
                        f"duplicate qid {question.qid!r} at {source_ref}; first seen at {previous}"
                    )
                seen_qids[question.qid] = source_ref
                questions.append(question)
        return sorted(questions, key=lambda item: item.qid)

    def _iter_rows(self, source_file: Path) -> Iterable[tuple[Mapping[str, Any], int]]:
        if source_file.suffix.lower() == ".jsonl":
            with source_file.open("r", encoding="utf-8-sig") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    if not raw_line.strip():
                        continue
                    try:
                        row = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"invalid JSONL at {source_file.name}:{line_number}: {exc.msg}"
                        ) from exc
                    if not isinstance(row, Mapping):
                        raise ValueError(
                            f"question row must be an object at {source_file.name}:{line_number}"
                        )
                    yield row, line_number
            return

        try:
            data = json.loads(source_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON at {source_file.name}:{exc.lineno}: {exc.msg}"
            ) from exc
        rows = data if isinstance(data, list) else [data]
        for record_index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                raise ValueError(
                    f"question row must be an object at {source_file.name}:{record_index}"
                )
            # JSON arrays do not have a stable one-record-per-line contract; the
            # record index is retained as the best deterministic source locator.
            yield row, record_index

    def _normalize(
        self,
        row: Mapping[str, Any],
        *,
        source_file: Path,
        source_line: int,
    ) -> Question:
        source_ref = f"{source_file.name}:{source_line}"
        qid = str(row.get("qid") or "").strip()
        domain = str(row.get("domain") or "").strip()
        question_text = str(row.get("question") or "").strip()
        if not qid:
            raise ValueError(f"qid must be non-empty at {source_ref}")
        if not domain:
            raise ValueError(f"domain must be non-empty at {source_ref}")
        if not question_text:
            raise ValueError(f"question must be non-empty at {source_ref}")

        raw_type = row.get("type")
        raw_answer_format = row.get("answer_format")
        raw_options = row.get("options", {}) or {}
        if not isinstance(raw_options, Mapping):
            raise ValueError(f"options must be an object at {source_ref}")
        options: Dict[str, str] = {
            str(key): str(value) for key, value in raw_options.items()
        }
        if self.require_submission_slot_contract:
            declared_type = str(raw_type or "").strip()
            option_types = {"单选题", "多选题", "判断题"}
            freeform_types = {"计算题", "抽取题"}
            if declared_type in option_types and not options:
                raise ValueError(
                    f"multi-slot profile {declared_type} requires non-empty options at {source_ref}"
                )
            if declared_type in freeform_types and options:
                raise ValueError(
                    f"multi-slot profile {declared_type} requires options={{}} at {source_ref}"
                )
        contract = build_question_answer_contract(
            qid=qid,
            raw_type=raw_type,
            raw_answer_format=raw_answer_format,
            options=options,
        )
        enriched_raw = dict(row)
        enriched_raw["_raw_type"] = raw_type
        enriched_raw["_raw_answer_format"] = raw_answer_format
        enriched_raw["_answer_contract"] = contract_to_dict(contract)
        enriched_raw["_source_file"] = source_file.name
        enriched_raw["_source_path"] = str(source_file)
        enriched_raw["_source_line"] = int(source_line)

        submission_slot_count = self.submission_slot_count_by_qid.get(qid)
        if submission_slot_count is not None and submission_slot_count not in {1, 2, 3, 4}:
            raise ValueError(
                f"submission slot count must be 1..4 for {qid!r} at {source_ref}"
            )
        if self.require_submission_slot_contract and submission_slot_count is None:
            raise ValueError(
                f"missing submission slot contract for {qid!r} at {source_ref}"
            )
        slot_contracts = self.submission_slot_contracts_by_qid.get(qid, ())
        if self.require_submission_slot_contract and not slot_contracts:
            raise ValueError(
                f"missing expected answer slot contracts for {qid!r} at {source_ref}"
            )
        if slot_contracts and len(slot_contracts) != submission_slot_count:
            raise ValueError(
                f"answer slot contract count mismatch for {qid!r} at {source_ref}: "
                f"expected={submission_slot_count} actual={len(slot_contracts)}"
            )
        if submission_slot_count is not None:
            enriched_raw["_submission_slot_count"] = int(submission_slot_count)
            enriched_raw["_submission_columns"] = [
                f"answer_{index}" for index in range(1, int(submission_slot_count) + 1)
            ]
            enriched_raw["_submission_slot_contracts"] = [
                dict(slot) for slot in slot_contracts
            ]

        raw_doc_ids = row.get("doc_ids", []) or []
        if isinstance(raw_doc_ids, (str, bytes)):
            raw_doc_ids = [raw_doc_ids]
        if not isinstance(raw_doc_ids, Sequence):
            raise ValueError(f"doc_ids must be a sequence at {source_ref}")

        return Question(
            qid=qid,
            domain=domain,
            text=question_text,
            options=options,
            answer_format=contract.answer_format,
            doc_ids=[str(doc_id) for doc_id in raw_doc_ids if str(doc_id).strip()],
            submission_slot_count=submission_slot_count,
            submission_slot_contracts=tuple(dict(slot) for slot in slot_contracts),
            raw=enriched_raw,
            answer_contract=contract,
        )
