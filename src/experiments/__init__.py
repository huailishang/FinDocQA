"""P7D Workstream D — evaluation and combination harness.

This package implements the offline evaluation/combination layer described in
``docs/p7d-parallel-workstreams.md`` Workstream D. It is deliberately
decoupled from the live pipeline: it consumes result artifacts (recorded per
question) and produces experiment manifests, per-question diffs, error
taxonomy reports and combination-matrix summaries.

Workstream D must never require an LLM merely to compare artifacts or generate
structural reports. All logic here is deterministic and standard-library only,
exercised by synthetic fixtures under ``tests/fixtures/p7d_d/``.

It does NOT change retrieval, solver, verifier, writer or submission behavior.
"""

from __future__ import annotations

from .diff import DiffEngine, DiffResult, diff_experiments
from .manifest import (
    ExperimentManifest,
    ExperimentResult,
    QuestionResult,
    build_manifest,
)
from .report import (
    ErrorTaxonomyReport,
    CombinationReport,
    generate_error_taxonomy_report,
    generate_combination_report,
)
from .freeze import (
    CandidateBuildResult,
    FrozenBaseline,
    build_candidate,
    freeze_baseline,
    load_frozen_baseline,
)

__all__ = [
    "ExperimentManifest",
    "ExperimentResult",
    "QuestionResult",
    "build_manifest",
    "DiffEngine",
    "DiffResult",
    "diff_experiments",
    "ErrorTaxonomyReport",
    "CombinationReport",
    "generate_error_taxonomy_report",
    "generate_combination_report",
    "CandidateBuildResult",
    "FrozenBaseline",
    "build_candidate",
    "freeze_baseline",
    "load_frozen_baseline",
]
