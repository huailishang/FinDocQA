"""Input adapters that normalize external document representations."""
from .mineru import canonical_from_adapted_mineru, canonical_from_raw_mineru
from .text import canonical_from_markdown_file, canonical_from_text_file

__all__ = [
    "canonical_from_adapted_mineru",
    "canonical_from_raw_mineru",
    "canonical_from_markdown_file",
    "canonical_from_text_file",
]
