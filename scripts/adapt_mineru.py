#!/usr/bin/env python3
"""CLI wrapper for the MinerU adapter (R1).

Usage (single document):
    python scripts/adapt_mineru.py mineru_output/doc1 target/insurance/doc1
        --domain insurance --doc-id doc1

Usage (whole domain corpus):
    python scripts/adapt_mineru.py mineru_output/insurance target/insurance
        --domain insurance --corpus

See ``src/structure/mineru_adapter.py`` for the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from structure.mineru_adapter import main


if __name__ == "__main__":
    raise SystemExit(main())
