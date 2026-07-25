#!/usr/bin/env python3
"""Parse a staged MinerU GPU batch sequentially with resume support.

The input directory should preserve domain subdirectories, for example:
  data/mineru_gpu_batch/input/financial_contracts/text04.pdf

Outputs are written as:
  <output-root>/<domain>/<doc_id>/auto/<doc_id>_content_list_v2.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--backend", default="pipeline")
    parser.add_argument("--method", default="auto")
    parser.add_argument("--timeout", type=int, default=7200, help="Per-document timeout in seconds.")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def run_with_timeout(cmd: list[str], timeout: int) -> tuple[subprocess.CompletedProcess[str], bool]:
    kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr), False
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr), True


def main() -> int:
    args = parse_args()
    input_root = args.input_root.resolve()
    output_root = args.output_root.resolve()
    manifest_dir = output_root / "_manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(
        (p for p in input_root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda p: p.as_posix().lower(),
    )
    results: list[dict[str, object]] = []
    started_at = time.time()

    for index, pdf in enumerate(pdfs, start=1):
        relative = pdf.relative_to(input_root)
        domain = relative.parts[0] if len(relative.parts) > 1 else "unclassified"
        doc_id = pdf.stem
        domain_output = output_root / domain
        expected = domain_output / doc_id / "auto" / f"{doc_id}_content_list_v2.json"

        if expected.is_file() and not args.force:
            print(f"[{index}/{len(pdfs)}] SKIP {domain}/{doc_id}", flush=True)
            result_entry = {
                "domain": domain,
                "doc_id": doc_id,
                "source_pdf": relative.as_posix(),
                "status": "skipped_existing",
                "success": True,
                "elapsed_s": 0.0,
            }
        else:
            domain_output.mkdir(parents=True, exist_ok=True)
            cmd = [
                "mineru",
                "-p", str(pdf),
                "-o", str(domain_output),
                "-b", args.backend,
                "-m", args.method,
            ]
            print(f"[{index}/{len(pdfs)}] START {domain}/{doc_id}", flush=True)
            t0 = time.time()
            completed, timed_out = run_with_timeout(cmd, args.timeout)
            elapsed = round(time.time() - t0, 1)
            log_path = manifest_dir / f"{domain}_{doc_id}.log"
            log_path.write_text(
                f"command={cmd!r}\nreturncode={completed.returncode}\ntimed_out={timed_out}\n"
                f"\n--- stdout ---\n{completed.stdout or ''}\n"
                f"\n--- stderr ---\n{completed.stderr or ''}\n",
                encoding="utf-8",
                errors="replace",
            )
            success = expected.is_file()
            status = "parsed" if success else (f"timeout>{args.timeout}s" if timed_out else "failed")
            print(f"[{index}/{len(pdfs)}] {status.upper()} {domain}/{doc_id} ({elapsed}s)", flush=True)
            result_entry = {
                "domain": domain,
                "doc_id": doc_id,
                "source_pdf": relative.as_posix(),
                "status": status,
                "success": success,
                "elapsed_s": elapsed,
                "returncode": completed.returncode,
                "log": log_path.as_posix(),
            }

        results.append(result_entry)
        manifest = {
            "input_root": str(input_root),
            "output_root": str(output_root),
            "backend": args.backend,
            "method": args.method,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total": len(pdfs),
            "processed": len(results),
            "success": sum(bool(r["success"]) for r in results),
            "failed": [r["doc_id"] for r in results if not r["success"]],
            "docs": results,
        }
        (manifest_dir / "gpu_batch_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    failed = [r for r in results if not r["success"]]
    print(f"Complete: success={len(results) - len(failed)}/{len(results)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
