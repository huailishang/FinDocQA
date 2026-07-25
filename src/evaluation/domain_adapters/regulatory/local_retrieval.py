"""Deterministic local retrieval for the REG-P production candidate map.

Candidates come only from immutable local corpus files declared by the question.
Evaluator oracle text and expected labels are never accepted as inputs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import canonical_source_v2, compact, resolve_candidate_path
from evaluation.domain_adapters.regulatory.proposition_atoms import PATTERNS as R22_PATTERNS
from evaluation.domain_adapters.regulatory.proposition_atoms import proposition_type as r22_type
from evaluation.domain_adapters.regulatory.regulatory_atoms import R23_PATTERNS
from evaluation.domain_adapters.regulatory.regulatory_atoms import proposition_type as r23_type


def _doc_roots(repo_root: Path, doc_id: str, initial: Sequence[EvidenceCandidate]) -> list[Path]:
    roots: list[Path] = []
    for candidate in initial:
        if str(candidate.doc_id) != doc_id:
            continue
        path = resolve_candidate_path(repo_root, candidate.source)
        if path is not None:
            roots.append(path.parent)
    roots.extend(
        [
            repo_root.parent / "data/processed_mineru_retrieval/regulatory" / doc_id,
            repo_root.parent / "data/processed_mineru/regulatory" / doc_id / "auto",
            repo_root.parent / "data/processed_pymupdf4llm/regulatory" / doc_id,
        ]
    )
    unique: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in unique:
            unique.append(resolved)
    return unique


def _files(repo_root: Path, doc_id: str, initial: Sequence[EvidenceCandidate]) -> list[Path]:
    found: list[Path] = []
    for root in _doc_roots(repo_root, doc_id, initial):
        for path in sorted(root.glob("*.md")):
            if path.is_file() and path not in found:
                found.append(path)
    return found


def _type_and_patterns(option_text: str) -> tuple[str, tuple[str, ...]]:
    proposition = r23_type(option_text)
    if proposition != "unresolved":
        return proposition, tuple(R23_PATTERNS.get(proposition, ()))
    proposition = r22_type(option_text)
    return proposition, tuple(R22_PATTERNS.get(proposition, ()))


def _terms(option_text: str) -> list[str]:
    value = re.sub(r"[，。；：、（）()《》\s]+", " ", option_text)
    terms = re.findall(r"[A-Za-z0-9.%]+|[\u4e00-\u9fff]{2,}", value)
    result: list[str] = []
    for term in terms:
        if len(term) > 10 and re.fullmatch(r"[\u4e00-\u9fff]+", term):
            result.extend(term[i : i + 5] for i in range(0, len(term) - 4, 4))
        else:
            result.append(term)
    return list(dict.fromkeys(term for term in result if len(term) >= 2))[:32]


def retrieve_production_candidates(
    *, repo_root: Path, question: Question, initial: Sequence[EvidenceCandidate], max_per_option: int = 3
) -> tuple[tuple[EvidenceCandidate, ...], list[dict[str, Any]]]:
    """Return bundle candidates plus deterministic, canonically reproducible hits."""
    existing = {
        (str(candidate.source), hashlib.sha256(str(candidate.text).encode("utf-8")).hexdigest())
        for candidate in initial
    }
    added: list[EvidenceCandidate] = []
    audit: list[dict[str, Any]] = []
    for label, option_text in question.options.items():
        proposition, patterns = _type_and_patterns(str(option_text))
        terms = _terms(str(option_text))
        ranked: list[tuple[int, int, Path, str, str]] = []
        scanned: list[str] = []
        for doc_id in map(str, question.doc_ids):
            for path in _files(repo_root, doc_id, initial):
                scanned.append(str(path))
                body = path.read_text(encoding="utf-8-sig", errors="replace")
                pattern_hits = sum(bool(re.search(pattern, body, re.I | re.S)) for pattern in patterns)
                lexical_hits = sum(compact(term) in compact(body) for term in terms)
                # Direct proposition matches outrank lexical fallback.  The
                # complete MinerU document is eligible when a clause crosses a
                # page boundary in page-split retrieval files.
                score = pattern_hits * 100 + lexical_hits
                if score <= 0:
                    continue
                ranked.append((score, -len(body), path, body, doc_id))
        ranked.sort(reverse=True, key=lambda row: (row[0], row[1], str(row[2])))
        option_added: list[EvidenceCandidate] = []
        for score, _, path, body, doc_id in ranked:
            candidate = EvidenceCandidate(
                domain=question.domain,
                doc_id=doc_id,
                source=str(path),
                text=body,
                score=float(score),
                retriever="ag_r2_3_deterministic_local_regulatory_retrieval",
                metadata={
                    "candidate_origin": "production_local_retrieval",
                    "retrieval_mode": "deterministic_local_canonical_scan",
                    "qid": question.qid,
                    "option": label,
                    "proposition": proposition,
                    "query_terms": terms,
                },
            )
            key = (str(candidate.source), hashlib.sha256(candidate.text.encode("utf-8")).hexdigest())
            if key in existing:
                continue
            canonical = canonical_source_v2(repo_root, candidate)
            if not canonical["candidate_matches_canonical_record"] or not canonical["lineage_doc_id_match"]:
                continue
            option_added.append(candidate)
            added.append(candidate)
            existing.add(key)
            if len(option_added) >= max_per_option:
                break
        audit.append(
            {
                "qid": question.qid,
                "option": label,
                "proposition": proposition,
                "candidate_origin": "production_local_retrieval",
                "scanned_files": sorted(set(scanned)),
                "new_candidate_count": len(option_added),
                "new_candidates": [
                    {
                        "source": row.source,
                        "doc_id": row.doc_id,
                        "retriever": row.retriever,
                        "source_sha256": canonical_source_v2(repo_root, row)["source_file_sha256"],
                    }
                    for row in option_added
                ],
                "stop_reason": "CANONICAL_HITS_FOUND" if option_added else "NO_NEW_CANONICAL_HIT",
            }
        )
    return tuple(initial) + tuple(added), audit
