"""Generic JSON/JSONL output adapters."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from result_output.contracts import ResultRecord


class JsonResultWriter:
    """Write output-neutral ResultRecord objects as JSON or JSONL."""

    name = "json"

    def __init__(self, path: str | Path, *, jsonl: bool = False) -> None:
        self.path = Path(path)
        self.jsonl = bool(jsonl)

    def write(self, results: Sequence[ResultRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(result) for result in results]
        if self.jsonl:
            with self.path.open("w", encoding="utf-8") as handle:
                for item in payload:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            return
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
