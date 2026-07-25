"""Minimal reusable orchestration protocol for evidence completion."""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from evidence_completion.contracts import CompletionResult, EvidenceGap


class EvidenceCompletionAdapter(Protocol):
    def classify_gaps(self, *args: Any, **kwargs: Any) -> Sequence[EvidenceGap]: ...
    def complete(self, *args: Any, **kwargs: Any) -> CompletionResult: ...


def run_completion(adapter: EvidenceCompletionAdapter, *args: Any, **kwargs: Any) -> CompletionResult:
    """Invoke one domain adapter without leaking domain enums into this layer."""
    return adapter.complete(*args, **kwargs)
