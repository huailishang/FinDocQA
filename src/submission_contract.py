"""Official multi-slot per-slot contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

_ALLOWED_SLOT_KINDS = {
    "selection",
    "number",
    "percentage",
    "percentage_point",
    "date",
    "ordering",
    "text",
}


def load_answer_slot_contracts(path: Path) -> dict[str, tuple[dict[str, Any], ...]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("multi-slot answer slot contract must be a JSON object")
    if payload.get("template_sample_values_used_as_truth") is not False:
        raise ValueError("template sample values must not be used as answer truth")
    if payload.get("template_sample_values_used_as_kind_authority") is not False:
        raise ValueError("template sample values must not be used as kind authority")
    raw_qids = payload.get("qids")
    if not isinstance(raw_qids, Mapping):
        raise ValueError("multi-slot answer slot contract requires qids mapping")

    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for raw_qid, raw_entry in raw_qids.items():
        qid = str(raw_qid).strip()
        if not qid or not isinstance(raw_entry, Mapping):
            raise ValueError(f"invalid qid contract entry: {raw_qid!r}")
        raw_slots = raw_entry.get("slots")
        if not isinstance(raw_slots, Sequence) or isinstance(raw_slots, (str, bytes)):
            raise ValueError(f"slots must be an array for {qid}")
        slots: list[dict[str, Any]] = []
        for expected_index, raw_slot in enumerate(raw_slots, start=1):
            if not isinstance(raw_slot, Mapping):
                raise ValueError(f"slot must be an object for {qid}.answer_{expected_index}")
            slot = dict(raw_slot)
            slot_qid = str(slot.get("qid") or qid)
            slot_index = int(slot.get("slot_index"))
            expected_kind = str(slot.get("expected_kind") or "").strip().lower()
            if slot_qid != qid or slot_index != expected_index:
                raise ValueError(f"non-contiguous slot contract for {qid}.answer_{expected_index}")
            if expected_kind not in _ALLOWED_SLOT_KINDS:
                raise ValueError(f"unsupported expected_kind {expected_kind!r} for {qid}.answer_{expected_index}")
            decimal_places = slot.get("expected_decimal_places")
            if decimal_places is not None:
                if isinstance(decimal_places, bool) or not isinstance(decimal_places, int) or not 0 <= decimal_places <= 12:
                    raise ValueError(f"invalid decimal places for {qid}.answer_{expected_index}")
            if expected_kind == "percentage" and slot.get("percent_suffix_required") is not True:
                raise ValueError(f"percentage suffix must be required for {qid}.answer_{expected_index}")
            if expected_kind == "date" and slot.get("date_format") != "YYYY年M月D日":
                raise ValueError(f"invalid date format contract for {qid}.answer_{expected_index}")
            if expected_kind == "ordering" and slot.get("ordering_delimiter") != ">":
                raise ValueError(f"invalid ordering delimiter contract for {qid}.answer_{expected_index}")
            slot["qid"] = qid
            slot["slot_index"] = slot_index
            slot["expected_kind"] = expected_kind
            slot["allowed_input_unit_suffixes"] = [
                str(value)
                for value in slot.get("allowed_input_unit_suffixes") or []
                if str(value)
            ]
            slots.append(slot)
        if not 1 <= len(slots) <= 4:
            raise ValueError(f"slot count must be 1..4 for {qid}")
        result[qid] = tuple(slots)
    return result


def strict_nonnegative_int(value: Any, *, field: str) -> int:
    """Return a non-negative integer without coercion or truncation."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a non-negative integer; got {value!r}")
    if value < 0:
        raise ValueError(f"{field} must be a non-negative integer; got {value!r}")
    return value


def validate_token_triplet(
    *,
    prompt_tokens: Any,
    completion_tokens: Any,
    total_tokens: Any,
    prefix: str = "tokens",
) -> tuple[int, int, int]:
    prompt = strict_nonnegative_int(prompt_tokens, field=f"{prefix}.prompt_tokens")
    completion = strict_nonnegative_int(completion_tokens, field=f"{prefix}.completion_tokens")
    total = strict_nonnegative_int(total_tokens, field=f"{prefix}.total_tokens")
    if total != prompt + completion:
        raise ValueError(
            f"{prefix}.total_tokens equation mismatch: "
            f"{total} != {prompt} + {completion}"
        )
    return prompt, completion, total


def validate_result_ledger_tokens(
    *,
    qid: str,
    prompt_tokens: Any,
    completion_tokens: Any,
    total_tokens: Any,
    metadata: Mapping[str, Any] | None,
) -> tuple[int, int, int]:
    result_tokens = validate_token_triplet(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prefix=f"{qid}.result",
    )
    meta = dict(metadata or {})
    ledger = meta.get("provider_ledger_token_totals")
    if ledger is None:
        return result_tokens
    if not isinstance(ledger, Mapping):
        raise ValueError(f"{qid}.provider_ledger_token_totals must be an object")
    ledger_tokens = validate_token_triplet(
        prompt_tokens=ledger.get("prompt_tokens"),
        completion_tokens=ledger.get("completion_tokens"),
        total_tokens=ledger.get("total_tokens"),
        prefix=f"{qid}.ledger",
    )
    if ledger_tokens != result_tokens:
        raise ValueError(
            f"{qid} ledger/result token mismatch: ledger={ledger_tokens} result={result_tokens}"
        )
    return result_tokens
