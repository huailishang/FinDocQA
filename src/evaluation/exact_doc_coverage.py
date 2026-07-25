"""Exact document-id attribution and required-document coverage.

Document ids are never inferred from arbitrary substrings, hashes, page numbers,
line numbers, prefixes, or fragments. Canonical source paths may expose a
declared document id either as an exact path segment immediately following a
known domain segment, or as an exact filename stem inside a controlled nested
raw-source container such as attachments/, txt/ or html/.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

KNOWN_DOMAINS = {
    "financial_reports",
    "financial_contracts",
    "regulatory",
    "research",
    "insurance",
}

# These container names are present in the project raw corpus and are the only
# nested source layouts accepted by the parser. The filename stem must exactly
# equal a declared doc id; no substring/prefix matching is permitted.
CONTROLLED_NESTED_RAW_CONTAINERS = {"attachments", "txt", "html"}


@dataclass(frozen=True)
class DocIdParseResult:
    source: str
    doc_id: str
    method: str
    parsed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "doc_id": self.doc_id,
            "method": self.method,
            "parsed": self.parsed,
            "reason": self.reason,
        }


def _normalise_source(source: Any) -> str:
    return str(source or "").strip().replace("\\", "/")


def parse_doc_id_from_source(
    source: Any,
    *,
    known_doc_ids: Sequence[str],
    known_domains: Sequence[str] = tuple(sorted(KNOWN_DOMAINS)),
    nested_raw_containers: Sequence[str] = tuple(sorted(CONTROLLED_NESTED_RAW_CONTAINERS)),
) -> DocIdParseResult:
    """Parse one declared document id from a canonical source path.

    Accepted layouts are deliberately narrow:

    1. <domain>/<doc_id>/... via exact path-segment equality.
    2. <domain>/<controlled-container>/<doc_id>.<ext> via exact filename
       stem equality.

    Query/fragment suffixes are ignored because they can carry page/table
    anchors, but they are never used to infer a document id.
    """
    raw = _normalise_source(source)
    if not raw:
        return DocIdParseResult(raw, "", "NONE", False, "empty_source")
    path_only = raw.split("#", 1)[0].split("?", 1)[0]
    parts = [part for part in PurePosixPath(path_only).parts if part not in {"/", ""}]
    domains = {str(value) for value in known_domains}
    allowed = {str(value) for value in known_doc_ids}
    containers = {str(value) for value in nested_raw_containers}

    for index, part in enumerate(parts):
        if part not in domains:
            continue
        if index + 1 >= len(parts):
            return DocIdParseResult(
                raw,
                "",
                "DOMAIN_WITHOUT_DOCUMENT_SEGMENT",
                False,
                "domain_is_terminal_path_segment",
            )

        candidate = parts[index + 1]
        if candidate in allowed:
            return DocIdParseResult(raw, candidate, "DOMAIN_NEXT_PATH_SEGMENT", True)

        if candidate in containers:
            if index + 2 >= len(parts):
                return DocIdParseResult(
                    raw,
                    "",
                    "NESTED_RAW_CONTAINER_WITHOUT_FILE",
                    False,
                    f"nested_container_missing_file:{candidate}",
                )
            nested_name = parts[index + 2]
            nested_stem = PurePosixPath(nested_name).stem
            if nested_stem in allowed:
                return DocIdParseResult(
                    raw,
                    nested_stem,
                    "DOMAIN_NESTED_RAW_EXACT_FILENAME_STEM",
                    True,
                )
            return DocIdParseResult(
                raw,
                "",
                "DOMAIN_NESTED_RAW_FILENAME_STEM_REJECTED",
                False,
                f"filename_stem_not_declared:{nested_stem}",
            )

        return DocIdParseResult(
            raw,
            "",
            "DOMAIN_NEXT_PATH_SEGMENT_REJECTED",
            False,
            f"segment_after_domain_not_declared:{candidate}",
        )

    return DocIdParseResult(
        raw,
        "",
        "NO_KNOWN_DOMAIN_SEGMENT",
        False,
        "known_path_structure_not_found",
    )


def option_doc_coverage(
    *,
    option_label: str,
    option_verdict: Mapping[str, Any],
    question_required_doc_ids: Sequence[str],
) -> dict[str, Any]:
    declared = tuple(dict.fromkeys(str(value) for value in question_required_doc_ids if str(value)))
    observed: set[str] = set()
    required: set[str] = set()
    parse_rows: list[dict[str, Any]] = []

    for fact in option_verdict.get("source_facts") or []:
        explicit = str(fact.get("doc_id") or "").strip()
        source = str(fact.get("canonical_source") or "")
        if explicit:
            method = "SOURCE_FACT_DOC_ID"
            accepted = explicit in declared
            parse_rows.append({
                "source": source,
                "doc_id": explicit if accepted else "",
                "explicit_doc_id": explicit,
                "method": method,
                "parsed": accepted,
                "reason": "" if accepted else "explicit_doc_id_not_declared",
            })
            if accepted:
                observed.add(explicit)
                required.add(explicit)
        elif source:
            parsed = parse_doc_id_from_source(source, known_doc_ids=declared)
            parse_rows.append(parsed.to_dict())
            if parsed.parsed:
                observed.add(parsed.doc_id)
                required.add(parsed.doc_id)

    sources = (
        option_verdict.get("canonical_sources")
        or option_verdict.get("resolved_evidence_refs")
        or option_verdict.get("evidence_refs")
        or []
    )
    for source in sources:
        parsed = parse_doc_id_from_source(source, known_doc_ids=declared)
        parse_rows.append(parsed.to_dict())
        if parsed.parsed:
            observed.add(parsed.doc_id)
            required.add(parsed.doc_id)

    # If a verdict has no attributable source, the option's required scope is
    # conservatively the full declared question scope and therefore fails.
    if not required:
        required.update(declared)
    missing = sorted(required - observed)
    unexpected = sorted(observed - set(declared))
    return {
        "option_label": option_label,
        "option_required_doc_ids": sorted(required),
        "observed_doc_ids": sorted(observed),
        "option_required_docs_covered": not missing and not unexpected and bool(observed),
        "missing_required_doc_ids": missing,
        "unexpected_observed_doc_ids": unexpected,
        "source_to_doc_id_parse_method": parse_rows,
    }


def question_doc_coverage(
    *,
    question_required_doc_ids: Sequence[str],
    option_verdicts: Mapping[str, Mapping[str, Any]],
    changed_labels: Sequence[str] = (),
) -> dict[str, Any]:
    declared = tuple(dict.fromkeys(str(value) for value in question_required_doc_ids if str(value)))
    option_rows = {
        str(label): option_doc_coverage(
            option_label=str(label),
            option_verdict=dict(verdict or {}),
            question_required_doc_ids=declared,
        )
        for label, verdict in option_verdicts.items()
    }
    observed = {doc_id for row in option_rows.values() for doc_id in row["observed_doc_ids"]}
    missing = sorted(set(declared) - observed)
    unexpected = sorted(observed - set(declared))
    changed = [str(label) for label in changed_labels]
    changed_rows = [option_rows.get(label) for label in changed if option_rows.get(label) is not None]
    all_changed_covered = bool(changed_rows) and all(row["option_required_docs_covered"] for row in changed_rows)
    return {
        "question_required_doc_ids": list(declared),
        "question_observed_doc_ids": sorted(observed),
        "question_required_docs_covered": not missing and not unexpected and bool(declared),
        "all_changed_options_required_docs_covered": all_changed_covered,
        "changed_labels": changed,
        "missing_required_doc_ids": missing,
        "unexpected_observed_doc_ids": unexpected,
        "option_required_docs_covered": {
            label: row["option_required_docs_covered"] for label, row in option_rows.items()
        },
        "option_rows": option_rows,
    }
