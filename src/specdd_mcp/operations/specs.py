"""``list_specs``: repo-wide (or scoped) index of ``.sdd`` files.

The orientation tool for ``/specc:status`` and ``/specc:audit`` (v2 slash
commands) and for any caller wanting a dashboard-style overview. Walks every
``.sdd`` file under a repo (or a narrower scope), parses each, and returns a
sorted, deduplicated index — optionally with per-state task counts.

Same shape as :func:`specdd_mcp.operations.tasks.list_tasks`: pure operation,
no MCP wiring, monorepo guardrail enforced through
:func:`specdd_mcp.operations.walks.walk_specs`. A spec that fails to parse is
skipped and reported in ``warnings`` — the index keeps moving (DESIGN §5.8).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TypeAlias

from specdd_mcp.operations.walks import (
    DEFAULT_MAX_SPECS,
    TooLargeError,
    walk_specs,
)
from specdd_mcp.parser import parse_spec
from specdd_mcp.paths import OutOfScopeError, is_under, to_repo_relative
from specdd_mcp.types import (
    Err,
    Ok,
    ParsedTask,
    SpecIndexEntry,
    SpecLevel,
    TaskSummary,
)

ListSpecsResult: TypeAlias = "Ok[list[SpecIndexEntry]] | Err"


def list_specs(
    repo_root: Path,
    *,
    scope: Path | None = None,
    levels: list[SpecLevel] | None = None,
    include_task_summary: bool = True,
    max_specs: int = DEFAULT_MAX_SPECS,
) -> ListSpecsResult:
    """Index every ``.sdd`` file under ``repo_root`` (or ``scope``).

    See DESIGN.md §5.8 for the contract.

    Args:
        repo_root: The repository root. Used as the base for the repo-relative
            ``path`` returned in each :class:`SpecIndexEntry`.
        scope: Optional sub-tree to narrow the walk. Must be inside
            ``repo_root``. When ``scope`` points at a file, its parent
            directory is walked.
        levels: Optional :data:`SpecLevel` allow-list. ``None`` returns every
            level; an empty list returns nothing.
        include_task_summary: When ``True`` (default), each entry carries a
            :class:`TaskSummary` of per-state counts; when ``False``,
            ``task_summary`` is ``None`` and tasks are not counted.
        max_specs: Per-call override of the monorepo guardrail.

    Returns:
        :class:`Ok` containing entries sorted by ``path`` ascending. Warnings
        carry the paths of specs that failed to parse — those are omitted from
        the index but the walk continues.

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

    levels_filter: set[SpecLevel] | None = (
        set(levels) if levels is not None else None
    )

    warnings = list(walk.warnings)
    entries: list[SpecIndexEntry] = []

    for sdd_path in walk.paths:
        try:
            rel_path = to_repo_relative(sdd_path, repo_root)
        except OutOfScopeError:  # pragma: no cover - defensive, see list_tasks
            continue

        result = parse_spec(path=str(sdd_path))
        if isinstance(result, Err):
            warnings.append(
                f"could not parse {rel_path}: {result.error}: {result.message}"
            )
            continue

        spec = result.data
        if levels_filter is not None and spec.level not in levels_filter:
            continue

        entries.append(
            SpecIndexEntry(
                path=rel_path,
                name=spec.name,
                level=spec.level,
                line_count=spec.line_count,
                task_summary=(
                    _summarize_tasks(spec.tasks) if include_task_summary else None
                ),
            )
        )

    entries.sort(key=lambda e: e.path)
    return Ok(data=entries, warnings=warnings)


def _summarize_tasks(tasks: list[ParsedTask] | None) -> TaskSummary:
    """Count tasks by state. A spec with no ``Tasks:`` section (``None``)
    summarizes to all zeros — see :class:`TaskSummary`."""
    if not tasks:
        return TaskSummary()
    counts = Counter(task.state for task in tasks)
    return TaskSummary(
        open=counts.get("open", 0),
        done=counts.get("done", 0),
        skipped=counts.get("skipped", 0),
        blocked=counts.get("blocked", 0),
        needs_decision=counts.get("needs_decision", 0),
    )
