"""Content-hash utility for write-precondition checks.

:func:`~specdd_mcp.operations.mutate_tasks.update_task_status` (PR 4 C6)
requires callers to pass ``expected_content_hash`` — the hash they got
from the most recent parse of the spec file. If the file changed on disk
since then (another agent edited it, an editor saved over it, a concurrent
MCP call wrote to it), the recomputed hash will differ and the write is
rejected with ``STALE_FILE``. This stops the server from silently
clobbering concurrent edits.

SHA-256 chosen over (say) MD5 / SHA-1 / blake2 because:

- stdlib (no extra dependency)
- fast enough — spec files are small (< 100 KB typical)
- collision-resistant (we don't need crypto security, just stability)
- stable across platforms and Python versions
"""

from __future__ import annotations

import hashlib


def content_hash(content: bytes) -> str:
    """Return the hex-encoded SHA-256 hash of ``content``.

    The hash is a **byte-level** fingerprint of the file. Any change
    anywhere — including BOM toggle, whitespace, line-ending swap — flips
    the hash. That's the right defensive default for ``STALE_FILE`` checks:
    if the bytes on disk differ from what the caller saw, the caller's
    state is stale and they need to re-parse before writing.
    """
    return hashlib.sha256(content).hexdigest()
