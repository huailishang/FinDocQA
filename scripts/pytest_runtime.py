from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _to_windows_path(path: Path) -> str:
    """Convert a WSL path to a Windows path when invoking a Windows executable."""
    text = str(path.resolve())
    if text.startswith("/mnt/") and len(text) > 6:
        drive = text[5].upper()
        rest = text[7:].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return text


def _run_pytest(command: list[str], *, root: Path, binary_output: bool = False) -> dict:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run(command, cwd=root, capture_output=True, env=env)
    if binary_output:
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
    else:
        stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
        stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr
    return {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "stdout": stdout[-6000:],
        "stderr": stderr[-6000:],
    }


def run_pytest_prefer_project_runtime(root: Path, test_files: Sequence[Path]) -> dict:
    """Run pytest with the active Python, then optionally fall back to a conda env.

    The fallback is intentionally machine-agnostic. Set FINDOCQA_PYTEST_CONDA_ENV
    to select a conda environment. The historical AFAC_PYTEST_CONDA_ENV variable
    is accepted as a compatibility alias.
    """
    local_check = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if local_check.returncode == 0:
        cmd = [sys.executable, "-m", "pytest", "-q", *[str(path) for path in test_files]]
        result = _run_pytest(cmd, root=root)
        return {
            **result,
            "runtime": "active_python",
            "python": sys.executable,
        }

    conda_env = os.getenv("FINDOCQA_PYTEST_CONDA_ENV") or os.getenv("AFAC_PYTEST_CONDA_ENV")
    conda_exe = os.getenv("CONDA_EXE") or shutil.which("conda") or shutil.which("conda.exe")
    if conda_env and conda_exe:
        is_windows_exe = str(conda_exe).lower().endswith(".exe")
        tests = [
            _to_windows_path(path) if is_windows_exe else str(path)
            for path in test_files
        ]
        cmd = [str(conda_exe), "run", "-n", conda_env, "python", "-m", "pytest", "-q", *tests]
        result = _run_pytest(cmd, root=root, binary_output=is_windows_exe)
        return {
            **result,
            "runtime": f"conda:{conda_env}",
            "python": f"{conda_exe} run -n {conda_env} python",
            "active_python_pytest_missing": True,
        }

    return {
        "status": "NOT_RUN_ENV_MISSING",
        "runtime": "none",
        "python": sys.executable,
        "returncode": local_check.returncode,
        "stdout": local_check.stdout[-2000:],
        "stderr": local_check.stderr[-2000:],
        "reason": (
            "pytest is unavailable in the active Python. Install pytest there or set "
            "FINDOCQA_PYTEST_CONDA_ENV and make conda available on PATH."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    import json

    args = list(argv if argv is not None else sys.argv[1:])
    test_args = [arg for arg in args if not arg.startswith("-")]
    if not test_args:
        print(json.dumps({"status": "FAIL", "reason": "no test files supplied"}, ensure_ascii=False, indent=2))
        return 2

    root = Path(__file__).resolve().parents[1]
    test_files = [Path(arg) if Path(arg).is_absolute() else root / arg for arg in test_args]
    result = run_pytest_prefer_project_runtime(root, test_files)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
