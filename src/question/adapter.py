"""Normalize competition payloads and real user queries into ``Question``.

C0 is intentionally small: it validates the visible input, preserves explicit
metadata when present, and supplies only safe defaults when metadata is absent.
It does not perform retrieval or call a model.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from answer_contract import build_question_answer_contract, contract_to_dict
from contracts import Question


class CanonicalQuestionAdapter:
    """Adapt a string or mapping into the shared ``Question`` contract."""

    def adapt(self, payload: str | Mapping[str, Any]) -> Question:
        if isinstance(payload, str):
            raw: dict[str, Any] = {"question": payload}
            source_kind = "natural_language"
        elif isinstance(payload, Mapping):
            raw = dict(payload)
            source_kind = "structured"
        else:
            raise TypeError("question payload must be a string or mapping")

        text = str(raw.get("question") or raw.get("text") or raw.get("query") or "").strip()
        if not text:
            raise ValueError("question text must be non-empty")

        raw_options = raw.get("options") or {}
        if not isinstance(raw_options, Mapping):
            raise ValueError("options must be an object when provided")
        options = {str(key): str(value) for key, value in raw_options.items()}

        raw_type = raw.get("type") or raw.get("question_type") or ""
        raw_answer_format = raw.get("answer_format") or ""
        if not raw_answer_format and not raw_type and not options:
            # A plain user query is an open answer by default. C1 can further
            # infer number/boolean/list/text shape without forcing A/B/C/D.
            raw_answer_format = "freeform"

        qid = str(raw.get("qid") or "").strip() or self._stable_qid(text, options)
        domain = str(raw.get("domain") or "unknown").strip() or "unknown"

        contract = build_question_answer_contract(
            qid=qid,
            raw_type=raw_type,
            raw_answer_format=raw_answer_format,
            options=options,
        )

        doc_ids = self._as_sequence(raw.get("doc_ids"))
        candidate_doc_ids = self._as_sequence(raw.get("candidate_doc_ids"))

        enriched_raw = dict(raw)
        enriched_raw.setdefault("question", text)
        enriched_raw["_input_adapter"] = "canonical_question_v1"
        enriched_raw["_input_source_kind"] = source_kind
        enriched_raw["_raw_type"] = raw_type
        enriched_raw["_raw_answer_format"] = raw_answer_format
        enriched_raw["_answer_contract"] = contract_to_dict(contract)

        return Question(
            qid=qid,
            domain=domain,
            text=text,
            options=options,
            answer_format=contract.answer_format,
            doc_ids=doc_ids,
            candidate_doc_ids=candidate_doc_ids,
            raw=enriched_raw,
            answer_contract=contract,
        )

    @staticmethod
    def _stable_qid(text: str, options: Mapping[str, str]) -> str:
        visible = text + "\n" + "\n".join(f"{key}:{options[key]}" for key in sorted(options))
        digest = hashlib.sha256(visible.encode("utf-8")).hexdigest()[:12]
        return f"query_{digest}"

    @staticmethod
    def _as_sequence(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            return (str(value),) if str(value).strip() else ()
        if not isinstance(value, Sequence):
            raise ValueError("document ids must be a sequence when provided")
        return tuple(str(item) for item in value if str(item).strip())
