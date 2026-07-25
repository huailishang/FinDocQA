"""Diagnostics package — read-only pipeline instrumentation.

Currently provides the zero-evidence diagnostic (R2) that traces each
question through the retrieval funnel and reports where evidence drops to
zero. Does NOT change production pipeline behavior; enabled explicitly.
"""

from __future__ import annotations

from .zero_evidence import (
    DiagnosticReport,
    QuestionDiagnostic,
    diagnose_question,
    diagnose_questions,
)

__all__ = [
    "DiagnosticReport",
    "QuestionDiagnostic",
    "diagnose_question",
    "diagnose_questions",
]
