"""Pytest bootstrap for the enhanced-baseline offline regression harness.

Puts ``src/`` on ``sys.path`` exactly like ``run.py`` does, so test modules
can use the same top-level imports (``from retrieval.hybrid import ...``) as
production code. No network, no API, no full dataset, no ``output/`` writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
