"""``list_tasks``: cross-spec task discovery.

Walks every ``.sdd`` file under a repo (or a narrower scope), parses them,
flattens out their tasks, and applies optional filters. This is what
``/specc`` and the future ``/specc:status`` slash command lean on when asking
"what's left to do".

Pure operation — no MCP wiring. The wrapper in
:mod:`specdd_mcp.server.tools` (PR 3 C3) handles serialization, logging, and
exception conversion.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from specdd_mcp.operations.walks import (
    DEFAULT_MAX_SPECS,
    TooLargeError,
    walk_specs,
)
from specdd_mcp.parser import parse_spec
from specdd_mcp.paths import OutOfScopeError, is_under, to_repo_relative
from specdd_mcp.types import Err, Ok, TaskState, TaskWithSource

ListTasksResult: TypeAlias = "Ok[list[TaskWithSource]] | Err"


def list_tasks(
    repo_root: Path,
    *,
    scope: Path | None = None,
    states: list[TaskState] | None = None,
    text_contains: str | None = None,
    task_id: str | None = None,
    include_blocked: bool = False,
    max_specs: int = DEFAULT_MAX_SPECS,
) -> ListTasksResult:
    """Collect tasks from every ``.sdd`` file under ``repo_root`` (or ``scope``).

    See DESIGN.md §5.4 for the contract.

    Args:
        repo_root: The repository root. Used as the base for repo-relative
            paths returned in :attr:`TaskWithSource.source`.
        scope: Optional sub-tree to narrow the walk. Must be inside ``repo_root``.
            When ``scope`` points at a file, its parent directory is walked.
        states: Task states to include. Defaults to ``["open"]``.
        text_contains: Case-insensitive substring filter on task text.
        task_id: Exact match on task ID (e.g. ``"#1"``).
        include_blocked: Shortcut — adds ``"blocked"`` and ``"needs_decision"``
            to whatever ``states`` was passed.
        max_specs: Per-call override of the monorepo guardrail.

    Returns:
        :class:`Ok` containing tasks sorted by ``(source, line)``. Warnings
        carry paths of specs that failed to parse — those tasks are not
        included in the list but the walk continues.

    Returns :class:`Err` for:
      - ``NOT_FOUND``    — ``repo_root`` or ``scope`` missing
      - ``OUT_OF_SCOPE`` — ``scope`` is outside ``repo_root``
      - ``TOO_LARGE``    — walk would yield more than ``max_specs`` files
    """
    if not repo_root.is_dir():
        return Err(
            error="NOT_FOUND",
            message=f"repo_root is not a directory: {repo_root}",
            details={"repo_root": str(repo_root)},
        )

    if scope is not None:
        if not scope.exists():
            return Err(
                error="NOT_FOUND",
                message=f"scope does not exist: {scope}",
                details={"scope": str(scope)},
            )
        if not is_under(scope, repo_root):
            return Err(
                error="OUT_OF_SCOPE",
                message=f"scope {scope} is not under repo_root {repo_root}",
                details={"scope": str(scope), "repo_root": str(repo_root)},
            )
        walk_dir = scope if scope.is_dir() else scope.parent
    else:
        walk_dir = repo_root

    try:
        walk = walk_specs(walk_dir, max_specs=max_specs)
    except TooLargeError as exc:
        return Err(
            error="TOO_LARGE",
            message=str(exc),
            details={"max_specs": max_specs},
        )

    states_filter: set[TaskState] = (
        set(states) if states is not None else {"open"}
    )
    if include_blocked:
        states_filter |= {"blocked", "needs_decision"}
    text_lower = text_contains.lower() if text_contains else None

    warnings = list(walk.warnings)
    collected: list[TaskWithSource] = []

    for sdd_path in walk.paths:
        try:
            rel_path = to_repo_relative(sdd_path, repo_root)
        except OutOfScopeError:  # pragma: no cover - unreachable defensive
            # walk_dir was validated above as under repo_root, so a path
            # produced by walk_specs(walk_dir) is always under repo_root.
            # Kept as a defensive guard in case walk semantics change.
            continue

        result = parse_spec(path=str(sdd_path))
        if isinstance(result, Err):
            warnings.append(
                f"could not parse {rel_path}: {result.error}: {result.message}"
            )
            continue

        if not result.data.tasks:
            continue

        for task in result.data.tasks:
            if task.state not in states_filter:
                continue
            if text_lower is not None and text_lower not in task.text.lower():
                continue
            if task_id is not None and task.id != task_id:
                continue
            collected.append(
                TaskWithSource(**task.model_dump(), source=rel_path)
            )

    collected.sort(key=lambda t: (t.source, t.line))
    return Ok(data=collected, warnings=warnings)
