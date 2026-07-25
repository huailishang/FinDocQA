"""Atomic single-run guard for paid/API runners.

The guard is deliberately small and local. A package/run_id pair owns one
lock file. Concurrent starts fail before the caller creates an API client.
Stale locks are detected explicitly and never overwritten automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
from typing import Any, Mapping


class SingleRunGuardError(RuntimeError):
    """Base error for run guard failures."""


class ConcurrentRunError(SingleRunGuardError):
    """Raised when the same package/run_id is already active."""


class StaleRunLockError(SingleRunGuardError):
    """Raised when a stale lock exists and requires explicit cleanup."""


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "run"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # os.kill(pid, 0) is not a reliable existence probe on Windows.
        # Querying a process handle avoids false stale-lock classification.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class LockInspection:
    state: str
    lock_path: str
    payload: Mapping[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "lock_path": self.lock_path,
            "payload": dict(self.payload),
            "reason": self.reason,
        }


class SingleRunGuard:
    """Context manager implementing one active runner per package/run_id."""

    def __init__(self, *, lock_dir: Path, package: str, run_id: str) -> None:
        self.lock_dir = Path(lock_dir)
        self.package = str(package)
        self.run_id = str(run_id)
        self.owner = platform.node() or "unknown-host"
        self.pid = os.getpid()
        self.lock_path = self.lock_dir / f"{_safe_name(self.package)}__{_safe_name(self.run_id)}.lock.json"
        self.acquired = False

    def _payload(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "run_id": self.run_id,
            "pid": self.pid,
            "owner": self.owner,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    def inspect_existing(self) -> LockInspection:
        if not self.lock_path.exists():
            return LockInspection("ABSENT", str(self.lock_path), {}, "no lock exists")
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return LockInspection("STALE", str(self.lock_path), {}, f"unreadable lock: {type(exc).__name__}")
        owner = str(payload.get("owner") or "")
        pid = int(payload.get("pid") or 0)
        same_identity = payload.get("package") == self.package and payload.get("run_id") == self.run_id
        if not same_identity:
            return LockInspection("STALE", str(self.lock_path), payload, "lock filename payload identity mismatch")
        if owner == self.owner and _pid_alive(pid):
            return LockInspection("ACTIVE", str(self.lock_path), payload, "same host pid is alive")
        return LockInspection("STALE", str(self.lock_path), payload, "owner differs or pid is no longer alive")

    def acquire(self) -> "SingleRunGuard":
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        payload = self._payload()
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            inspection = self.inspect_existing()
            if inspection.state == "ACTIVE":
                raise ConcurrentRunError(
                    f"active runner exists for package={self.package} run_id={self.run_id}: {inspection.payload}"
                )
            raise StaleRunLockError(
                f"stale lock requires explicit cleanup for package={self.package} run_id={self.run_id}: "
                f"{inspection.to_dict()}"
            )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except Exception:
            try:
                self.lock_path.unlink()
            finally:
                raise
        self.acquired = True
        return self

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SingleRunGuardError(f"cannot verify owned lock before release: {exc}") from exc
        owned = (
            payload.get("package") == self.package
            and payload.get("run_id") == self.run_id
            and int(payload.get("pid") or 0) == self.pid
            and str(payload.get("owner") or "") == self.owner
        )
        if not owned:
            raise SingleRunGuardError("refusing to release lock not owned by current runner")
        self.lock_path.unlink()
        self.acquired = False

    def __enter__(self) -> "SingleRunGuard":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


def append_api_ledger(path: Path, *, run_id: str, row: Mapping[str, Any]) -> None:
    """Append an API ledger row while forcing run_id into every record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**dict(row), "run_id": str(run_id)}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


__all__ = [
    "SingleRunGuard",
    "SingleRunGuardError",
    "ConcurrentRunError",
    "StaleRunLockError",
    "LockInspection",
    "append_api_ledger",
]
