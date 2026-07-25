"""Optional focused-page retrieval extension point.

The public FinDocQA baseline deliberately ships without dataset-specific
question-to-page maps.  Projects may provide their own focused-page policy in a
private extension layer after document scope has already been resolved.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Mapping

from contracts import EvidenceCandidate, Question


def focused_page_candidates(
    question: Question,
    doc_dirs: Mapping[str, Path],
) -> List[EvidenceCandidate]:
    """Return additional pre-approved page candidates.

    The public implementation is intentionally empty so no question ID can
    bypass normal retrieval.  ``question`` and ``doc_dirs`` remain in the
    interface for downstream projects that inject an auditable policy.
    """
    del question, doc_dirs
    return []
