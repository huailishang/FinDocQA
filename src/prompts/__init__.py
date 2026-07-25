"""Offline prompt registry and evaluation support.

BB-P0-04C deliberately keeps this package disconnected from the production
solver path.  Production solvers remain the source of truth for live prompts;
this package only inventories, fingerprints and evaluates prompt assets.
"""

from .registry import (
    FewShotAsset,
    PromptRegistry,
    PromptRegistryEntry,
    PromptRegistryError,
    discover_solver_prompt_builders,
    load_prompt_registry,
    normalize_template_text,
    sha256_text,
)

__all__ = [
    "FewShotAsset",
    "PromptRegistry",
    "PromptRegistryEntry",
    "PromptRegistryError",
    "discover_solver_prompt_builders",
    "load_prompt_registry",
    "normalize_template_text",
    "sha256_text",
]
