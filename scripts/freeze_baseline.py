#!/usr/bin/env python3
"""CLI wrapper for freezing a scored submission (R3).

Usage:
    python scripts/freeze_baseline.py freeze submission.csv freeze_dir/
        --name v3-test --score 54.8 [--date 2026-06-25] [--formats-json formats.json]

    python scripts/freeze_baseline.py candidate freeze_dir/
        --baseline-name v3-test --patches-json '{"case_013":"B"}' --output candidate.csv

See ``src/experiments/freeze.py`` for the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiments.freeze import main


if __name__ == "__main__":
    raise SystemExit(main())
