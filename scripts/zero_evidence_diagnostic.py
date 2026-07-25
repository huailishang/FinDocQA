#!/usr/bin/env python3
"""CLI wrapper for the zero-evidence diagnostic (R2).

Usage:
    python scripts/zero_evidence_diagnostic.py [--qid QID] [--domain DOMAIN] [--limit N]
        [--output-json path.json] [--output-md path.md]

See ``src/diagnostics/zero_evidence.py`` for the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from diagnostics.zero_evidence import main


if __name__ == "__main__":
    raise SystemExit(main())
