"""Resolve a SpecDD spec chain: every `.sdd` from the repo root down to a target.

This is what ``/specc`` runs at the start of every implementation task. The
chain feeds :func:`get_effective_constraints` (PR 3), which merges its
contents into the bound rules.

The walk is deterministic:

1. Resolve ``target`` to an absolute path (relative inputs are interpreted as
   repo-relative when ``repo_root`` is given; otherwise must be absolute).
2. Resolve ``repo_root`` — auto-detect from ``target`` if not provided.
3. Walk from ``repo_root`` down to ``target``'s directory (or target itself
   if it is a directory). Symlinked ancestors are skipped with warnings.
4. For each directory in the walk, glob ``*.sdd``. Multiple specs in one
   directory are ordered by :data:`SpecLevel` precedence, with lexicographic
   tiebreaking — this beats pure lexicographic (which would put ``feature``
   before ``module`` and invert the inheritance order).
5. Parse each spec. Successes go into the chain; failures go into ``malformed``.

Errors:

- ``INVALID_INPUT`` — relative target without ``repo_root``.
- ``NOT_FOUND``    — target missing, or no repo root detectable.
- ``OUT_OF_SCOPE`` — target resolves outside ``repo_root``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from specdd_mcp.parser.levels import infer_level
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.paths import (
    OutOfScopeError,
    find_repo_root,
    to_posix,
    to_repo_relative,
    walk_ancestors,
)
from specdd_mcp.types import (
    Err,
    MalformedSpec,
    Ok,
    ParsedSpec,
    SpecChain,
    SpecLevel,
)

# Same-directory inheritance order: ``app`` is most general (inherited first),
# ``unknown`` is most specific (nearest). Lower numeric value → appears earlier
# in the chain. Lexicographic tiebreak inside the same level.
#
# Picking this ordering is partially a judgment call — the SpecDD README does
# not formally rank the leaf-level kinds. We commit to *some* deterministic
# ordering so ``SpecChain.nearest`` has a well-defined value when multiple
# kinds coexist in one directory.
_LEVEL_PRECEDENCE: dict[SpecLevel, int] = {
    "app": 0,
    "module": 1,
    "feature": 2,
    "service": 3,
    "model": 4,
    "adapter": 5,
    "api": 6,
    "component": 7,
    "job": 8,
    "event": 9,
    "policy": 10,
    "custom": 90,
    "unknown": 99,
}

ResolveChainResult: TypeAlias = "Ok[SpecChain] | Err"


def resolve_spec_chain(
    target: str,
    repo_root: str | None = None,
) -> ResolveChainResult:
    """Build the ordered chain of ``.sdd`` specs from ``repo_root`` to ``target``.

    See module docstring for the algorithm and error semantics.
    """
    # Step 1: input validation + path resolution.
    if not target:
        return Err(error="INVALID_INPUT", message="target is required")

    target_input = Path(target)
    if target_input.is_absolute():
        target_path = target_input
    elif repo_root is not None:
        target_path = Path(repo_root) / target_input
    else:
        return Err(
            error="INVALID_INPUT",
            message="target must be absolute when repo_root is omitted",
            details={"target": target},
        )

    target_path = target_path.resolve(strict=False)
    if not target_path.exists():
        return Err(
            error="NOT_FOUND",
            message=f"target does not exist: {target_path}",
            details={"target": target},
        )

    # Step 2: resolve repo_root (use given, or auto-detect).
    if repo_root is not None:
        root_path = Path(repo_root).resolve(strict=False)
        if not root_path.is_dir():
            return Err(
                error="NOT_FOUND",
                message=f"repo_root is not a directory: {repo_root}",
                details={"repo_root": repo_root},
            )
    else:
        detected = find_repo_root(target_path)
        if detected is None:
            return Err(
                error="NOT_FOUND",
                message="no repo root found (no .specdd/ or .git/ ancestor)",
                details={"target": target},
            )
        root_path = detected

    # Step 3: walk ancestors (catches OUT_OF_SCOPE here).
    try:
        walk = walk_ancestors(target_path, root_path)
    except OutOfScopeError as exc:
        return Err(
            error="OUT_OF_SCOPE",
            message=str(exc),
            details={"target": target, "repo_root": to_posix(root_path)},
        )

    # Step 4 + 5: glob, sort, parse.
    chain: list[ParsedSpec] = []
    malformed: list[MalformedSpec] = []
    warnings = list(walk.warnings)

    for directory in walk.directories:
        sdd_files = _collect_specs_in_directory(directory)
        for sdd_path in sdd_files:
            rel_path = to_repo_relative(sdd_path, root_path)
            result = parse_spec(path=str(sdd_path))
            if isinstance(result, Err):
                malformed.append(MalformedSpec(path=rel_path, error=result.error))
                continue
            # Replace ParsedSpec.path with the POSIX repo-relative form so all
            # downstream consumers see consistent paths.
            parsed = result.data.model_copy(update={"path": rel_path})
            chain.append(parsed)
            warnings.extend(f"{rel_path}: {w}" for w in result.warnings)

    nearest = chain[-1] if chain else None

    return Ok(
        data=SpecChain(
            target=to_repo_relative(target_path, root_path),
            repo_root=to_posix(root_path),
            chain=chain,
            nearest=nearest,
            malformed=malformed,
        ),
        warnings=warnings,
    )


def _collect_specs_in_directory(directory: Path) -> list[Path]:
    """Return ``.sdd`` files directly inside ``directory``, sorted by
    inheritance precedence (parent levels first) with lexicographic tiebreak.

    Excludes macOS AppleDouble metadata files (``._foo.sdd``) defensively.
    """
    return sorted(
        (
            path
            for path in directory.glob("*.sdd")
            if path.is_file() and not path.name.startswith("._")
        ),
        key=_sort_key,
    )


def _sort_key(sdd_path: Path) -> tuple[int, str]:
    """``(level_precedence, posix_path)`` sort key for same-directory specs."""
    level = infer_level(str(sdd_path))
    return (_LEVEL_PRECEDENCE.get(level, 99), sdd_path.as_posix())
