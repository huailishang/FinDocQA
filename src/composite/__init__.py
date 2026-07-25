"""Composite solver contracts (P7D-C offline foundation).

This package defines parser-neutral, deterministic contracts for composite
question solving — questions that combine multiple capabilities such as
multi-select plus calculation, or cross-document plus calculation.

The contracts are NOT wired into the production ``RoutedSolver``. They exist
so that routing decisions and option/document support can be unit-tested
without an LLM. Promoting a composite solver requires Final Reviewer
approval + A/B evaluation.

See ``docs/p7d-workstream-c-implementation.md`` for the design notes.
"""

from composite.capabilities import (
    Capability,
    CapabilityProfile,
    CompositeType,
    classify_composite,
)
from composite.helpers import (
    CrossDocSupportResult,
    DocumentValue,
    OptionCheck,
    OptionCheckResult,
    build_cross_doc_support,
    check_option_against_values,
    parse_numeric_value,
    safe_eval_numeric,
)
from composite.solver import (
    C1_FLAG_KEY,
    CompositeCrossCalcResult,
    CompositeCrossCalcSolver,
    CompositeMultiCalcResult,
    CompositeMultiCalcSolver,
    is_c1_enabled,
)

__all__ = [
    "Capability",
    "CapabilityProfile",
    "CompositeType",
    "classify_composite",
    "CrossDocSupportResult",
    "DocumentValue",
    "OptionCheck",
    "OptionCheckResult",
    "build_cross_doc_support",
    "check_option_against_values",
    "parse_numeric_value",
    "safe_eval_numeric",
    "C1_FLAG_KEY",
    "CompositeCrossCalcResult",
    "CompositeCrossCalcSolver",
    "CompositeMultiCalcResult",
    "CompositeMultiCalcSolver",
    "is_c1_enabled",
]
