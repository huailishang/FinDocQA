#!/usr/bin/env python3
"""Parse regulatory domain documents referenced by A-set questions.

Handles three types:
  1. PDF attachments (csrc_NNNN_attM.pdf) — via pymupdf4llm
  2. HTML files (csrc_NNNN.html) — via html2text or built-in HTML parsing
  3. TXT files (strict_v3_NNN_xxx.txt) — direct copy as page_0001.md

Only parses doc_ids referenced by the 20 regulatory questions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv(
    "FINDOCQA_REGULATORY_OUTPUT_ROOT",
    str(ROOT / "data" / "processed_pymupdf4llm"),
))
RAW_BASE = Path(os.getenv(
    "FINDOCQA_REGULATORY_RAW_ROOT",
    str(ROOT / "data" / "raw_dataset" / "raw" / "regulatory"),
))
QUESTIONS_FILE = Path(os.getenv(
    "FINDOCQA_REGULATORY_QUESTIONS",
    str(ROOT / "data" / "raw_dataset" / "questions" / "group_a" / "regulatory_questions.json"),
))


# ── HTML to Markdown converter (stdlib only) ────────────────────────

class _HTMLToText(HTMLParser):
    """Minimal HTML-to-text converter."""
    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip = True
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._buffer.append(data)

    def text(self) -> str:
        result = re.sub(r"\n{3,}", "\n\n", "".join(self._buffer))
        return result.strip()


def html_to_markdown(html_path: Path) -> str:
    parser = _HTMLToText()
    parser.feed(html_path.read_text(encoding="utf-8", errors="replace"))
    return parser.text()


# ── PDF parsing ─────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path, out_dir: Path) -> int:
    """Parse PDF to markdown pages. Returns page count."""
    import pymupdf4llm
    chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, chunk in enumerate(chunks):
        page_num = i + 1
        md_path = out_dir / f"page_{page_num:04d}.md"
        md_path.write_text(chunk["text"], encoding="utf-8")
    return len(chunks)


# ── main ────────────────────────────────────────────────────────────

def main() -> None:
    # Collect unique doc_ids from regulatory questions
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    rows = questions if isinstance(questions, list) else [questions]
    doc_ids: set[str] = set()
    for row in rows:
        for d in row.get("doc_ids", []):
            doc_ids.add(str(d))

    print(f"Regulatory questions: {len(rows)}")
    print(f"Unique doc_ids: {len(doc_ids)}")
    print()

    results: list[dict] = []
    for doc_id in sorted(doc_ids):
        out_dir = DATA_DIR / "regulatory" / doc_id
        if out_dir.exists():
            page_count = len(list(out_dir.glob("page_*.md")))
            if page_count > 0:
                print(f"[SKIP] doc {doc_id} already parsed ({page_count} pages)")
                results.append({"doc_id": doc_id, "status": "ok", "pages": page_count})
                continue

        # Determine source type
        pdf_path = RAW_BASE / "attachments" / f"{doc_id}.pdf"
        html_path = RAW_BASE / "html" / f"{doc_id}.html"

        if pdf_path.exists():
            # Type 1: PDF attachment
            print(f"[PARSE] doc {doc_id} (PDF) ...", end=" ", flush=True)
            try:
                pages = parse_pdf(pdf_path, out_dir)
                print(f"{pages} pages")
                results.append({"doc_id": doc_id, "status": "ok", "pages": pages, "source": str(pdf_path)})
            except Exception as exc:
                print(f"ERROR: {exc}")
                results.append({"doc_id": doc_id, "status": "error", "error": str(exc)})

        elif html_path.exists():
            # Type 2: HTML file
            print(f"[CONV] doc {doc_id} (HTML) ...", end=" ", flush=True)
            try:
                text = html_to_markdown(html_path)
                if text.strip():
                    out_dir.mkdir(parents=True, exist_ok=True)
                    md_path = out_dir / "page_0001.md"
                    md_path.write_text(text, encoding="utf-8")
                    page_count = 1
                    print(f"OK ({len(text)} chars)")
                else:
                    print("WARN: empty content")
                    page_count = 0
                results.append({"doc_id": doc_id, "status": "ok" if page_count > 0 else "warn", "pages": page_count})
            except Exception as exc:
                print(f"ERROR: {exc}")
                results.append({"doc_id": doc_id, "status": "error", "error": str(exc)})

        else:
            # Type 3: TXT file
            txt_path = RAW_BASE / "txt" / f"{doc_id}.txt"
            if txt_path.exists():
                print(f"[COPY] doc {doc_id} (TXT) ...", end=" ", flush=True)
                out_dir.mkdir(parents=True, exist_ok=True)
                md_path = out_dir / "page_0001.md"
                shutil.copy2(txt_path, md_path)
                page_count = 1
                print(f"OK ({txt_path.stat().st_size} bytes)")
                results.append({"doc_id": doc_id, "status": "ok", "pages": page_count, "source": str(txt_path)})
            else:
                print(f"[MISS] doc {doc_id}: no PDF/HTML/TXT found")
                results.append({"doc_id": doc_id, "status": "missing"})

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nDone: {ok}/{len(results)} succeeded")

    if ok < len(results):
        print("\nDetails:")
        for r in results:
            status = "OK" if r["status"] == "ok" else r.get("error", r["status"])
            print(f"  {r['doc_id']}: {status}")


if __name__ == "__main__":
    main()
