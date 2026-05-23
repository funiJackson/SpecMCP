"""In-process simulation of the ``/specc`` 9-step workflow (PR 5 C11).

``/specc`` is a slash command: in production a Claude Code session drives it,
calling one MCP tool per step. This test runs that same sequence of tool
calls *in-process* — no Claude, no stdio, just the wrapper functions from
:mod:`specdd_mcp.server.tools` invoked in order against a real fixture tree.

Why it earns its place next to the per-tool tests: every individual tool is
covered elsewhere, but nothing else asserts they *compose*. A regression
where each tool works alone but the handoff between them breaks (a path
shape, a hash round-trip, a task identifier) would slip through every other
suite and only surface in a live ``/specc`` run. This is the cheap, fast
guard against that.

The fixture (``chains/simple_3_level``) is copied into ``tmp_path`` first
because step 7 mutates the spec file — the committed fixture must stay clean.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.server.tools import (
    check_modification_scope,
    get_effective_constraints,
    update_task_status,
    validate_spec,
)
from tests.conftest import CHAINS_DIR

_NEAREST_SPEC = "src/billing/services/invoice.sdd"
_TARGET = "src/billing/services/invoice.ts"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A writable copy of the simple_3_level chain (step 7 mutates a spec)."""
    dest = tmp_path / "repo"
    shutil.copytree(CHAINS_DIR / "simple_3_level", dest)
    return dest


def test_specc_workflow_end_to_end(repo: Path) -> None:
    """Walk steps 2 → 8 of /specc as a sequence of tool calls."""
    repo_root = str(repo)

    # --- Step 2: resolve effective constraints ---------------------------
    constraints = get_effective_constraints(target=_TARGET, repo_root=repo_root)
    assert constraints["ok"] is True, constraints
    data = constraints["data"]
    # A clean chain: no conflicts, and a concrete write authority.
    assert data["conflicts"] == []
    assert data["write_authority_source"] == _NEAREST_SPEC

    # --- Step 3: confirm the task (select #1 by its line) ----------------
    task = _find_task(data["tasks"], task_id="#1", source=_NEAREST_SPEC)
    assert task["state"] == "open"
    task_line = task["line"]

    # --- Step 4: plan the edits → scope check ----------------------------
    scope = check_modification_scope(
        target=_TARGET, proposed_files=[_TARGET], repo_root=repo_root
    )
    assert scope["ok"] is True, scope
    assert _TARGET in scope["data"]["allowed"]
    assert scope["data"]["out_of_scope"] == []

    # --- Step 5/6: implement + verify (no-op here; nothing to edit) ------

    # --- Step 7: flip the task to done -----------------------------------
    spec_path = repo / _NEAREST_SPEC
    before_hash = content_hash(spec_path.read_bytes())
    update = update_task_status(
        spec_path=str(spec_path),
        expected_content_hash=before_hash,
        updates=[{"task_line": task_line, "new_state": "done"}],
    )
    assert update["ok"] is True, update
    # The single targeted task is now done, and the diff shows the flip.
    applied = update["data"]["applied"]
    assert len(applied) == 1
    assert applied[0]["previous_state"] == "open"
    assert applied[0]["task"]["id"] == "#1"
    assert "[x] #1" in update["data"]["diff"]
    new_hash = update["data"]["new_content_hash"]
    assert new_hash == content_hash(spec_path.read_bytes())

    # --- Step 8: validate the nearest spec -------------------------------
    validation = validate_spec(path=str(spec_path), check_inheritance=True)
    assert validation["ok"] is True, validation
    # check_inheritance=true is accepted but adds zero cross-spec issues today.
    assert validation["data"]["summary"]["errors"] == 0


def test_scope_rejects_unowned_sibling(repo: Path) -> None:
    """A file the nearest spec does not own is reported out_of_scope — the
    gate that stops /specc from editing outside its authority."""
    scope = check_modification_scope(
        target=_TARGET,
        proposed_files=["src/billing/services/secrets.py"],
        repo_root=str(repo),
    )
    assert scope["ok"] is True, scope
    assert scope["data"]["allowed"] == []
    assert scope["data"]["out_of_scope"] == ["src/billing/services/secrets.py"]


def test_stale_hash_blocks_write(repo: Path) -> None:
    """update_task_status refuses a write when the hash is stale — the
    concurrency guard /specc step 7 relies on."""
    spec_path = repo / _NEAREST_SPEC
    result = update_task_status(
        spec_path=str(spec_path),
        expected_content_hash="0" * 64,
        updates=[{"task_id": "#1", "new_state": "done"}],
    )
    assert result["ok"] is False
    assert result["error"] == "STALE_FILE"


def _find_task(
    tasks: list[dict[str, object]], *, task_id: str, source: str
) -> dict[str, object]:
    """Pick the one task matching ``task_id`` in ``source`` from step 2's list."""
    for task in tasks:
        if task.get("id") == task_id and task.get("source") == source:
            return task
    raise AssertionError(f"task {task_id} not found in {source}: {tasks}")
