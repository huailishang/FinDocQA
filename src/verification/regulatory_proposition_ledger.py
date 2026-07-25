"""Generic regulatory source adapter and typed proposition ledger.

The module intentionally contains no dataset qids, option text, or expected
answers.  Evaluator-owned proposition seeds are materialized only when every
configured anchor is present in an auditable declared source.  Administrative
party arguments are never authoritative unless a later regulator finding
explicitly adopts them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


AUTHORITATIVE_FACTUAL_STATUSES = {"supported", "contradicted"}
ALL_FACTUAL_STATUSES = AUTHORITATIVE_FACTUAL_STATUSES | {"unresolved"}
ALL_INTENT_STATUSES = {"matched", "mismatch", "unresolved"}
PARTY_ROLES = {"regulated_party", "party_representative"}
AUTHORITATIVE_STATEMENT_ROLES = {
    "operative_rule",
    "regulator_finding",
    "sanction",
    "effective_date",
    "repeal_date",
}


def compact_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u3000", " ").replace("％", "%")
    text = re.sub(r"\s+", "", text)
    return text.lower()


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


class _VisibleHTMLParser(HTMLParser):
    BLOCK_TAGS = {
        "article", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2",
        "h3", "h4", "h5", "h6", "li", "main", "p", "section", "table", "td",
        "th", "tr",
    }
    HIDDEN_TAGS = {"script", "style", "nav", "noscript", "svg", "header", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.HIDDEN_TAGS:
            self.hidden_depth += 1
        elif self.hidden_depth == 0 and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        elif self.hidden_depth == 0 and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0 and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        lines = []
        for raw in "".join(self.parts).splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)


@dataclass(frozen=True)
class RegulatorySource:
    doc_id: str
    source_relpath: str
    source_sha256: str
    source_type: str
    adapter_type: str
    adapter_relpath: str
    adapter_sha256: str
    text: str

    def inventory_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("text", None)
        payload["text_chars"] = len(self.text)
        return payload


class RegulatorySourceAdapter:
    """Resolve regulatory PDF/HTML/TXT documents into auditable text."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        raw_relpath: str = "raw_dataset/raw/regulatory",
        processed_relpath: str = "processed_mineru",
        retrieval_relpath: str = "processed_mineru_retrieval",
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.raw_root = self.data_root / raw_relpath
        self.processed_root = self.data_root / processed_relpath / "regulatory"
        self.retrieval_root = self.data_root / retrieval_relpath / "regulatory"
        self._cache: dict[str, RegulatorySource] = {}

    def resolve(self, doc_id: str) -> RegulatorySource:
        key = str(doc_id)
        if key not in self._cache:
            self._cache[key] = self._resolve_uncached(key)
        return self._cache[key]

    def inventory(self, doc_ids: Sequence[str]) -> list[dict[str, Any]]:
        return [self.resolve(doc_id).inventory_dict() for doc_id in _dedupe(doc_ids)]

    def _resolve_uncached(self, doc_id: str) -> RegulatorySource:
        txt = sorted((self.raw_root / "txt").glob(f"{doc_id}*.txt"))
        if txt:
            return self._from_text_file(doc_id, txt[0], "normative_rule", "raw_txt")

        html = self.raw_root / "html" / f"{doc_id}.html"
        if html.exists():
            raw = html.read_bytes()
            decoded = raw.decode("utf-8-sig", errors="replace")
            parser = _VisibleHTMLParser()
            parser.feed(decoded)
            text = parser.text()
            if not text.strip():
                raise ValueError(f"empty visible HTML text: {html}")
            return RegulatorySource(
                doc_id=doc_id,
                source_relpath=html.relative_to(self.data_root).as_posix(),
                source_sha256=sha256(raw).hexdigest(),
                source_type="administrative_decision",
                adapter_type="raw_html_visible_text",
                adapter_relpath=html.relative_to(self.data_root).as_posix(),
                adapter_sha256=sha256(text.encode("utf-8")).hexdigest(),
                text=text,
            )

        pdf = self.raw_root / "attachments" / f"{doc_id}.pdf"
        if pdf.exists():
            adapter = self.processed_root / doc_id / "auto" / f"{doc_id}.md"
            if adapter.exists():
                return self._from_pdf_adapter(doc_id, pdf, adapter)
            pages = sorted((self.retrieval_root / doc_id).glob("page_*.md"))
            if pages:
                text = "\n\n".join(page.read_text(encoding="utf-8-sig", errors="replace") for page in pages)
                raw = pdf.read_bytes()
                adapter_bytes = text.encode("utf-8")
                return RegulatorySource(
                    doc_id=doc_id,
                    source_relpath=pdf.relative_to(self.data_root).as_posix(),
                    source_sha256=sha256(raw).hexdigest(),
                    source_type="normative_rule",
                    adapter_type="retrieval_markdown_pages",
                    adapter_relpath=(self.retrieval_root / doc_id).relative_to(self.data_root).as_posix(),
                    adapter_sha256=sha256(adapter_bytes).hexdigest(),
                    text=text,
                )
            raise ValueError(f"PDF has no auditable text adapter: {pdf}")

        retrieval_dir = self.retrieval_root / doc_id
        pages = sorted(retrieval_dir.glob("page_*.md"))
        if pages:
            text = "\n\n".join(page.read_text(encoding="utf-8-sig", errors="replace") for page in pages)
            data = text.encode("utf-8")
            return RegulatorySource(
                doc_id=doc_id,
                source_relpath=retrieval_dir.relative_to(self.data_root).as_posix(),
                source_sha256=sha256(data).hexdigest(),
                source_type="derived_regulatory_text",
                adapter_type="retrieval_markdown_pages",
                adapter_relpath=retrieval_dir.relative_to(self.data_root).as_posix(),
                adapter_sha256=sha256(data).hexdigest(),
                text=text,
            )
        raise FileNotFoundError(f"regulatory declared document not found: {doc_id}")

    def _from_text_file(
        self, doc_id: str, path: Path, source_type: str, adapter_type: str
    ) -> RegulatorySource:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        if not text.strip():
            raise ValueError(f"empty regulatory source: {path}")
        return RegulatorySource(
            doc_id=doc_id,
            source_relpath=path.relative_to(self.data_root).as_posix(),
            source_sha256=sha256(raw).hexdigest(),
            source_type=source_type,
            adapter_type=adapter_type,
            adapter_relpath=path.relative_to(self.data_root).as_posix(),
            adapter_sha256=sha256(text.encode("utf-8")).hexdigest(),
            text=text,
        )

    def _from_pdf_adapter(self, doc_id: str, pdf: Path, adapter: Path) -> RegulatorySource:
        raw = pdf.read_bytes()
        adapter_raw = adapter.read_bytes()
        text = adapter_raw.decode("utf-8-sig", errors="replace")
        if not text.strip():
            raise ValueError(f"empty PDF text adapter: {adapter}")
        return RegulatorySource(
            doc_id=doc_id,
            source_relpath=pdf.relative_to(self.data_root).as_posix(),
            source_sha256=sha256(raw).hexdigest(),
            source_type="normative_rule",
            adapter_type="mineru_markdown",
            adapter_relpath=adapter.relative_to(self.data_root).as_posix(),
            adapter_sha256=sha256(adapter_raw).hexdigest(),
            text=text,
        )


@dataclass(frozen=True)
class PropositionSeed:
    proposition_id: str
    doc_id: str
    anchors: tuple[str, ...]
    article_or_section: str
    speaker_role: str
    statement_role: str
    adjudicative_status: str
    factual_status: str
    entity_scope: str = ""
    condition_or_trigger: str = ""
    amount_threshold: str = ""
    currency: str = ""
    period_or_deadline: str = ""
    effective_state: str = ""
    normative_scope: str = ""
    question_intent_type: str = ""

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "PropositionSeed":
        return cls(
            proposition_id=str(row.get("proposition_id") or ""),
            doc_id=str(row.get("doc_id") or ""),
            anchors=_dedupe(row.get("anchors") or []),
            article_or_section=str(row.get("article_or_section") or ""),
            speaker_role=str(row.get("speaker_role") or "document_author"),
            statement_role=str(row.get("statement_role") or "case_fact"),
            adjudicative_status=str(row.get("adjudicative_status") or "asserted"),
            factual_status=str(row.get("factual_status") or "unresolved"),
            entity_scope=str(row.get("entity_scope") or ""),
            condition_or_trigger=str(row.get("condition_or_trigger") or ""),
            amount_threshold=str(row.get("amount_threshold") or ""),
            currency=str(row.get("currency") or ""),
            period_or_deadline=str(row.get("period_or_deadline") or ""),
            effective_state=str(row.get("effective_state") or ""),
            normative_scope=str(row.get("normative_scope") or ""),
            question_intent_type=str(row.get("question_intent_type") or ""),
        )


@dataclass(frozen=True)
class RegulatoryProposition:
    proposition_id: str
    doc_id: str
    source_relpath: str
    source_sha256: str
    source_type: str
    article_or_section: str
    speaker_role: str
    statement_role: str
    adjudicative_status: str
    factual_status: str
    entity_scope: str
    condition_or_trigger: str
    amount_threshold: str
    currency: str
    period_or_deadline: str
    effective_state: str
    normative_scope: str
    question_intent_type: str
    canonical_source: str
    local_window: str
    anchor_start: int
    anchor_end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def authoritative(self) -> bool:
        if self.factual_status not in AUTHORITATIVE_FACTUAL_STATUSES:
            return False
        if self.statement_role == "party_argument" or self.speaker_role in PARTY_ROLES:
            return self.adjudicative_status == "adopted"
        return (
            self.statement_role in AUTHORITATIVE_STATEMENT_ROLES
            and self.adjudicative_status in {"adopted", "final_decision"}
        )


class RegulatoryPropositionLedger:
    """Materialize typed propositions with source lineage and role guards."""

    def __init__(
        self,
        adapter: RegulatorySourceAdapter,
        seeds: Sequence[PropositionSeed | Mapping[str, Any]],
    ) -> None:
        self.adapter = adapter
        self._items: dict[str, RegulatoryProposition] = {}
        for value in seeds:
            seed = value if isinstance(value, PropositionSeed) else PropositionSeed.from_mapping(value)
            if not seed.proposition_id:
                raise ValueError("proposition_id is required")
            if seed.proposition_id in self._items:
                raise ValueError(f"duplicate proposition_id: {seed.proposition_id}")
            if not seed.anchors:
                raise ValueError(f"anchors are required: {seed.proposition_id}")
            self._items[seed.proposition_id] = self._materialize(seed)

    def get(self, proposition_id: str) -> RegulatoryProposition:
        return self._items[str(proposition_id)]

    def all(self) -> tuple[RegulatoryProposition, ...]:
        return tuple(self._items.values())

    def _materialize(self, seed: PropositionSeed) -> RegulatoryProposition:
        source = self.adapter.resolve(seed.doc_id)
        compact = compact_text(source.text)
        positions: list[tuple[int, int]] = []
        for anchor in seed.anchors:
            needle = compact_text(anchor)
            position = compact.find(needle)
            if position < 0:
                raise ValueError(
                    f"regulatory proposition anchor missing: {seed.proposition_id}: "
                    f"{seed.doc_id}: {anchor}"
                )
            positions.append((position, position + len(needle)))
        start = min(item[0] for item in positions)
        end = max(item[1] for item in positions)
        if end - start > 9000:
            raise ValueError(f"proposition anchors too far apart: {seed.proposition_id}")
        original_start = max(0, self._approx_original_index(source.text, start) - 450)
        original_end = min(len(source.text), self._approx_original_index(source.text, end) + 850)
        window = source.text[original_start:original_end].strip()
        factual_status = seed.factual_status
        if factual_status not in ALL_FACTUAL_STATUSES:
            raise ValueError(f"invalid factual_status: {seed.proposition_id}: {factual_status}")
        return RegulatoryProposition(
            proposition_id=seed.proposition_id,
            doc_id=seed.doc_id,
            source_relpath=source.source_relpath,
            source_sha256=source.source_sha256,
            source_type=source.source_type,
            article_or_section=seed.article_or_section,
            speaker_role=seed.speaker_role,
            statement_role=seed.statement_role,
            adjudicative_status=seed.adjudicative_status,
            factual_status=factual_status,
            entity_scope=seed.entity_scope,
            condition_or_trigger=seed.condition_or_trigger,
            amount_threshold=seed.amount_threshold,
            currency=seed.currency,
            period_or_deadline=seed.period_or_deadline,
            effective_state=seed.effective_state,
            normative_scope=seed.normative_scope,
            question_intent_type=seed.question_intent_type,
            canonical_source=f"{source.source_relpath}#sha256={source.source_sha256}",
            local_window=window,
            anchor_start=start,
            anchor_end=end,
        )

    @staticmethod
    def _approx_original_index(text: str, compact_index: int) -> int:
        count = 0
        for index, char in enumerate(text):
            if not char.isspace() and char != "\u3000":
                if count >= compact_index:
                    return index
                count += 1
        return len(text)

    def evaluate_option(
        self,
        *,
        proposition_ids: Sequence[str],
        accepted_question_intents: Sequence[str],
        explicit_status: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        propositions = [self.get(item) for item in proposition_ids]
        accepted = set(str(item) for item in accepted_question_intents)
        if not propositions:
            return {
                "factual_status": "unresolved",
                "question_intent_status": "unresolved",
                "speaker_role": "unknown",
                "statement_role": "unknown",
                "adjudicative_status": "asserted",
                "source_refs": [],
                "reason": reason or "no materialized proposition",
                "trusted": False,
            }

        authoritative = [item for item in propositions if item.authoritative]
        rejected_arguments = [
            item for item in propositions
            if item.statement_role == "party_argument"
            and item.adjudicative_status in {"rejected", "partially_adopted"}
        ]
        if explicit_status in ALL_FACTUAL_STATUSES:
            factual_status = explicit_status
        elif rejected_arguments and not authoritative:
            factual_status = "contradicted"
        elif authoritative and all(item.factual_status == "supported" for item in authoritative):
            factual_status = "supported"
        elif authoritative and any(item.factual_status == "contradicted" for item in authoritative):
            factual_status = "contradicted"
        else:
            factual_status = "unresolved"

        intents = {item.question_intent_type for item in propositions if item.question_intent_type}
        if factual_status == "unresolved":
            intent_status = "unresolved"
        elif not accepted or not intents:
            intent_status = "matched"
        elif intents & accepted:
            intent_status = "matched"
        else:
            intent_status = "mismatch"

        primary = authoritative[-1] if authoritative else propositions[-1]
        trusted = factual_status in AUTHORITATIVE_FACTUAL_STATUSES and (
            bool(authoritative) or bool(rejected_arguments)
        )
        if primary.statement_role == "case_fact" and primary.normative_scope == "general":
            trusted = False
            factual_status = "unresolved"

        return {
            "factual_status": factual_status,
            "question_intent_status": intent_status,
            "speaker_role": primary.speaker_role,
            "statement_role": primary.statement_role,
            "adjudicative_status": primary.adjudicative_status,
            "source_refs": [
                {
                    "proposition_id": item.proposition_id,
                    "doc_id": item.doc_id,
                    "canonical_source": item.canonical_source,
                    "article_or_section": item.article_or_section,
                    "local_window": item.local_window,
                }
                for item in propositions
            ],
            "reason": reason,
            "trusted": trusted,
        }


def infer_question_intents(question_text: str) -> tuple[str, ...]:
    compact = compact_text(question_text)
    intents: list[str] = []
    rules = (
        ("内部审批", "internal_approval"),
        ("金额门槛", "amount_threshold"),
        ("报告期限", "reporting_deadline"),
        ("保存", "retention_period"),
        ("生效", "effective_date"),
        ("施行", "effective_date"),
        ("停止施行", "repeal_or_stop_date"),
        ("处罚", "sanction_basis"),
        ("主管人员", "responsible_person_finding"),
        ("审计责任", "sanction_basis"),
        ("治理", "governance_rule"),
        ("年度报告", "annual_report_rule"),
        ("半年度报告", "annual_report_rule"),
    )
    for marker, intent in rules:
        if compact_text(marker) in compact:
            intents.append(intent)
    return _dedupe(intents)


def derive_answer(option_rows: Sequence[Mapping[str, Any]]) -> str:
    selected = []
    for row in option_rows:
        if (
            row.get("factual_status") == "supported"
            and row.get("question_intent_status") == "matched"
            and row.get("trusted") is True
        ):
            selected.append(str(row.get("option_label") or ""))
    return "".join(sorted(item for item in selected if item))
