#!/usr/bin/env python3
"""CLI wrapper for the MinerU corpus validator (Lane A).

Usage:
    python scripts/validate_corpus.py <target_root> --domain insurance

Validates an adapted corpus directory for page continuity, image-only pages,
table/formula blocks, doc-id mapping and degraded flags. Read-only.

See ``src/structure/corpus_validator.py`` for the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from structure.corpus_validator import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
