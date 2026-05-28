"""``find_ownership_conflicts``: detect items owned by more than one spec.

The SpecDD README is explicit: "only one spec should own a specific item at
any given time." This operation mechanically enforces that invariant across a
repo (or a narrower scope) by collecting every ``Owns:`` claim, resolving each
to concrete repo-relative paths, and flagging any path claimed by two or more
distinct specs (DESIGN §5.9).

Resolution is uniform and filesystem-anchored (same snapshot semantics as
``get_effective_constraints``):

  * A **literal** ``Owns:`` entry (no glob metacharacters) resolves to the one
    repo-relative path it names — whether or not that file exists yet, since an
    explicit literal claim stands on its own.
  * A **glob** entry resolves to the set of files it matches on disk *right
    now* via :func:`specdd_mcp.operations.globs.expand_pattern`.

A path claimed by ≥2 distinct specs is a conflict. Its ``kind`` falls out of
the claim shapes that collide on it:

  * all literals          → ``"literal"``
  * all globs             → ``"glob_overlap"``
  * a mix of the two      → ``"glob_vs_literal"``

Only ``Owns:`` is considered — ``Can modify:`` grants shared write access by
design and is not an ownership claim. Multiple claims from the *same* spec on
one path are not a conflict (that's a single-file concern ``validate_spec``
covers); a conflict needs at least two distinct specs.

Pure operation — no MCP wiring. The wrapper in
:mod:`specdd_mcp.server.tools` handles serialization, logging, and exception
conversion.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeAlias

from specdd_mcp.operations.globs import expand_pattern
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
    OwnershipConflict,
    OwnershipConflictKind,
    OwnershipOwner,
)

FindOwnershipConflictsResult: TypeAlias = "Ok[list[OwnershipConflict]] | Err"

#: Glob metacharacters per ``pathlib.Path.glob``. A pattern carrying any of
#: these is treated as a glob; otherwise it is a literal.
_GLOB_CHARS: tuple[str, ...] = ("*", "?", "[")


@dataclass(frozen=True)
class _Claim:
    """One ``Owns:`` claim on a single resolved path."""

    spec: str
    line: int
    pattern: str
    is_glob: bool


def find_ownership_conflicts(
    repo_root: Path,
    *,
    scope: Path | None = None,
    max_specs: int = DEFAULT_MAX_SPECS,
) -> FindOwnershipConflictsResult:
    """Find items claimed by more than one spec via ``Owns:``.

    See DESIGN.md §5.9 for the contract.

    Args:
        repo_root: The repository root. Base for the repo-relative ``spec``
            paths and resolved ``item`` paths in the result.
        scope: Optional sub-tree to narrow the walk. Must be inside
            ``repo_root``. When ``scope`` points at a file, its parent
            directory is walked.
        max_specs: Per-call override of the monorepo guardrail.

    Returns:
        :class:`Ok` containing conflicts sorted by ``item`` ascending, each
        with owners ordered by ``(spec, line)``. Empty when no path is
        multiply-owned. Warnings carry the paths of specs that failed to parse.

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

    warnings = list(walk.warnings)
    # resolved repo-relative path -> every spec claim that lands on it.
    claims: dict[str, list[_Claim]] = defaultdict(list)

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
        owns = spec.owns or []
        if not owns:
            continue

        # Patterns resolve relative to the spec's own directory (the same
        # convention globs.py uses). Use the on-disk parent rather than
        # spec.path so we don't depend on how the parser labeled the path.
        spec_dir = sdd_path.parent
        line_numbers = spec.bullet_lines.get("owns", [])

        for index, pattern in enumerate(owns):
            line = line_numbers[index] if index < len(line_numbers) else 0
            is_glob = _is_glob(pattern)
            if is_glob:
                resolved_paths = expand_pattern(pattern, spec_dir, repo_root).matches
            else:
                literal = _resolve_literal(pattern, spec_dir, repo_root)
                resolved_paths = [literal] if literal is not None else []
            for item in resolved_paths:
                claims[item].append(
                    _Claim(
                        spec=rel_path,
                        line=line,
                        pattern=pattern,
                        is_glob=is_glob,
                    )
                )

    conflicts: list[OwnershipConflict] = []
    for item in sorted(claims):
        owners = claims[item]
        if len({owner.spec for owner in owners}) < 2:
            continue
        owners_sorted = sorted(owners, key=lambda c: (c.spec, c.line))
        conflicts.append(
            OwnershipConflict(
                item=item,
                kind=_classify(owners_sorted),
                owners=[
                    OwnershipOwner(spec=c.spec, line=c.line, pattern=c.pattern)
                    for c in owners_sorted
                ],
            )
        )

    return Ok(data=conflicts, warnings=warnings)


def _is_glob(pattern: str) -> bool:
    """True when ``pattern`` carries any glob metacharacter."""
    return any(char in pattern for char in _GLOB_CHARS)


def _resolve_literal(
    pattern: str, spec_dir: Path, repo_root: Path
) -> str | None:
    """Resolve a literal ``Owns:`` entry to its repo-relative path.

    Returns ``None`` for absolute patterns (non-portable) or patterns that
    escape ``repo_root`` (e.g. ``../../x.ts``) — both are flagged on the
    source side by ``validate_spec`` and contribute no ownership claim here.
    Existence on disk is irrelevant: an explicit literal claim stands whether
    or not the file has been created yet.
    """
    normalized = pattern.replace("\\", "/")
    if PurePosixPath(normalized).is_absolute():
        return None
    try:
        return to_repo_relative(spec_dir / normalized, repo_root)
    except OutOfScopeError:
        return None


def _classify(owners: list[_Claim]) -> OwnershipConflictKind:
    """Derive the conflict kind from the colliding claim shapes."""
    any_glob = any(owner.is_glob for owner in owners)
    any_literal = any(not owner.is_glob for owner in owners)
    if any_glob and any_literal:
        return "glob_vs_literal"
    if any_glob:
        return "glob_overlap"
    return "literal"
