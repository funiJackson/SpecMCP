"""Cross-platform per-file exclusive locking.

:func:`~specdd_mcp.operations.mutate_tasks.update_task_status` (PR 4 C6)
acquires a lock on every spec file it writes to. The lock is
**belt-and-suspenders** alongside the content-hash precondition:

- The hash check (``STALE_FILE``) catches editor / external-process races
  that happen **before** the lock is acquired. That's the common case.
- The lock prevents in-process or sibling-MCP-call races during the
  actual write. Less common but cheap insurance.

We use a sidecar ``<path>.lock`` file rather than locking the spec file
directly:

- Doesn't interfere with reads of the spec (``Read`` can run while a
  write holds the lock).
- Decouples lock semantics from open-mode differences across OSes.
- Makes the lock visible to operators (``ls *.lock`` shows what's held).

The lock is **NOT reentrant**. Calling :func:`file_lock` on a path while
already holding the lock for that path in the same process will deadlock.
``update_task_status`` holds the lock for the duration of one batch and
does not recurse, so reentrance isn't needed.

Cross-platform implementation:

- POSIX: :func:`fcntl.flock` with ``LOCK_EX`` — blocking exclusive lock
  on the lockfile's file descriptor.
- Windows: :func:`msvcrt.locking` with ``LK_LOCK`` — blocking acquire on
  the first byte of the file.

Sidecar lock files are **not** deleted on release. They're zero-byte
cruft, but cleaning them up risks a TOCTOU race where a concurrent
process re-creates the file with a different inode mid-cleanup — and a
new inode means no mutual exclusion. Leave them alone.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO


def _lock_path(target: Path) -> Path:
    """Return the sidecar lock path for ``target``: same dir, name suffixed
    with ``.lock``."""
    return target.with_suffix(target.suffix + ".lock")


if sys.platform == "win32":  # pragma: no cover - selected at import on Windows
    import msvcrt

    def _acquire(fp: IO[bytes]) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)

    def _release(fp: IO[bytes]) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _acquire(fp: IO[bytes]) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)

    def _release(fp: IO[bytes]) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock(target: Path) -> Iterator[None]:
    """Acquire an exclusive lock on ``target`` via a sidecar lock file.

    Blocks until the lock can be acquired. The lock is released on
    context exit, including via exception. Re-acquiring after release
    works; nested acquisition on the same path within one process
    deadlocks.
    """
    lock_path = _lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    fp = open(lock_path, "r+b")  # noqa: SIM115 - explicit close below
    try:
        _acquire(fp)
        try:
            yield
        finally:
            _release(fp)
    finally:
        fp.close()
