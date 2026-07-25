"""Claim/option aggregation over BB-P0-13 atom evidence verdicts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate
from .atom_evidence_verifier import AtomVerdict, REFUTE, SUPPORT, UNRESOLVED, verify_atom
from .claim_atoms import ClaimAtom, atomize_claim


CLAIM_SUPPORTED = "CLAIM_SUPPORTED"
CLAIM_REFUTED = "CLAIM_REFUTED"
CLAIM_UNRESOLVED = "CLAIM_UNRESOLVED"


@dataclass(frozen=True)
class ClaimVerificationResult:
    option_label: str
    claim_text: str
    atoms: tuple[ClaimAtom, ...]
    atom_verdicts: tuple[AtomVerdict, ...]
    aggregate_verdict: str
    unresolved_atom_ids: tuple[str, ...]
    refuted_atom_ids: tuple[str, ...]
    supporting_evidence_lineage: tuple[Mapping[str, str], ...]
    provider_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_label": self.option_label,
            "claim_text": self.claim_text,
            "atoms": [atom.to_dict() for atom in self.atoms],
            "atom_verdicts": [verdict.to_dict() for verdict in self.atom_verdicts],
            "aggregate_verdict": self.aggregate_verdict,
            "unresolved_atom_ids": list(self.unresolved_atom_ids),
            "refuted_atom_ids": list(self.refuted_atom_ids),
            "supporting_evidence_lineage": [dict(row) for row in self.supporting_evidence_lineage],
            "provider_calls": self.provider_calls,
        }


def verify_claim(
    claim_text: str,
    candidates: Sequence[EvidenceCandidate],
    *,
    option_label: str = "",
    subject_hint: str = "",
) -> ClaimVerificationResult:
    """Atomize and verify one claim without using option label as evidence."""
    atomization = atomize_claim(claim_text, subject_hint=subject_hint)
    atoms = tuple(atomization.atoms)
    verdicts = tuple(verify_atom(atom, candidates) for atom in atoms)
    aggregate = aggregate_atom_verdicts(verdicts)
    unresolved = tuple(row.atom_id for row in verdicts if row.verdict == UNRESOLVED)
    refuted = tuple(row.atom_id for row in verdicts if row.verdict == REFUTE)
    lineage = tuple(
        {
            "atom_id": row.atom_id,
            "doc_id": row.bound_doc_id,
            "page": row.bound_page,
            "source": row.bound_source,
            "verdict": row.verdict,
        }
        for row in verdicts
        if row.binding_auditable and row.bound_source
    )
    return ClaimVerificationResult(
        option_label=str(option_label or ""),
        claim_text=str(claim_text or ""),
        atoms=atoms,
        atom_verdicts=verdicts,
        aggregate_verdict=aggregate,
        unresolved_atom_ids=unresolved,
        refuted_atom_ids=refuted,
        supporting_evidence_lineage=lineage,
        provider_calls=0,
    )


def verify_options(
    options: Mapping[str, str],
    candidates: Sequence[EvidenceCandidate],
) -> dict[str, ClaimVerificationResult]:
    """Verify each option independently; labels are output indexes only."""
    return {
        str(label): verify_claim(str(text), candidates, option_label=str(label))
        for label, text in options.items()
    }


def aggregate_atom_verdicts(verdicts: Sequence[AtomVerdict]) -> str:
    """Deterministic fail-closed claim aggregation."""
    rows = tuple(verdicts)
    if not rows:
        return CLAIM_UNRESOLVED
    if any(row.verdict == REFUTE for row in rows):
        return CLAIM_REFUTED
    if all(row.verdict == SUPPORT for row in rows):
        return CLAIM_SUPPORTED
    return CLAIM_UNRESOLVED


__all__ = [
    "CLAIM_SUPPORTED",
    "CLAIM_REFUTED",
    "CLAIM_UNRESOLVED",
    "ClaimVerificationResult",
    "aggregate_atom_verdicts",
    "verify_claim",
    "verify_options",
]
