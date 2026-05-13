"""Path utilities for the MCP server.

All outputs are POSIX forward-slash strings regardless of the host OS — this
is DESIGN §3.8's path-normalization rule, applied at every boundary. Inputs
may be ``str`` or ``Path``.

Public surface:

- :func:`to_posix` — normalize any path to a POSIX string.
- :func:`is_under` — check whether one path is contained in another (handles
  ``..`` tricks via ``Path.resolve``).
- :func:`to_repo_relative` — convert an absolute path to its POSIX repo-relative
  form; raises :class:`OutOfScopeError` if the path is outside the repo.
- :func:`find_repo_root` — walk up from a target looking for a SpecDD or git
  marker. Prefers ``.specdd/``, falls back to ``.git/``.
- :func:`walk_ancestors` — build the directory chain from repo root down to a
  target, excluding symlinked directories and reporting them as warnings.

Everything here is pure-function-shaped. The MCP tool wrappers in
:mod:`specdd_mcp.server.tools` (added in PR 2 commit 4) use these helpers
without keeping any path state on the server itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_SPECDD_MARKER = ".specdd"
_GIT_MARKER = ".git"


class OutOfScopeError(Exception):
    """Raised when a path is outside the repository root.

    Maps to the ``OUT_OF_SCOPE`` :data:`~specdd_mcp.types.ErrorCode` at the MCP
    tool boundary.
    """


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def to_posix(path: str | Path) -> str:
    """Convert any path to a POSIX forward-slash string.

    Pre-normalizes backslashes in string input so that a Windows-style path
    passed on a POSIX host still ends up with forward slashes — Python's
    ``Path`` only does the conversion on the host's own platform.
    """
    if isinstance(path, str):
        path = path.replace("\\", "/")
    return Path(path).as_posix()


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


def is_under(path: Path, root: Path) -> bool:
    """Return True iff ``path`` is at or under ``root``.

    Uses ``Path.resolve(strict=False)`` so ``..`` tricks and unresolved
    symlinks are handled correctly without requiring the path to exist.
    """
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def to_repo_relative(absolute: Path, repo_root: Path) -> str:
    """Return the POSIX repo-relative form of an absolute path.

    Raises:
        OutOfScopeError: when ``absolute`` is not inside ``repo_root``.
    """
    if not is_under(absolute, repo_root):
        raise OutOfScopeError(f"{absolute} is not under {repo_root}")
    rel = absolute.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    return rel.as_posix()


# ---------------------------------------------------------------------------
# Repo root detection
# ---------------------------------------------------------------------------


def find_repo_root(target: Path) -> Path | None:
    """Walk up from ``target`` looking for a repo root marker.

    Resolution order:

    1. Nearest ancestor (or the target itself) containing a ``.specdd/``
       directory wins. This handles SpecDD-managed subtrees inside a larger
       git monorepo — the SpecDD root, not the outer git root, is treated as
       ``repo_root``.
    2. Otherwise, the nearest ancestor containing a ``.git/`` entry (directory
       or file — gitlinks are files).
    3. Otherwise ``None``.
    """
    target = target.resolve(strict=False)
    candidates = [target, *target.parents]

    for parent in candidates:
        if (parent / _SPECDD_MARKER).is_dir():
            return parent
    for parent in candidates:
        if (parent / _GIT_MARKER).exists():
            return parent
    return None


# ---------------------------------------------------------------------------
# Ancestor walk
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AncestorWalk:
    """Result of :func:`walk_ancestors`.

    ``directories`` is in root-to-leaf order: ``directories[0]`` is
    ``repo_root``, ``directories[-1]`` is the target's containing directory
    (the target itself if it is a directory). Symlinked directories are
    excluded from this list and instead surface in ``warnings``.
    """

    directories: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def walk_ancestors(target: Path, repo_root: Path) -> AncestorWalk:
    """Build the directory chain from ``repo_root`` down to ``target``.

    The walk operates on literal (non-resolved) paths so symlinked
    directories ARE detected and skipped — using ``target.resolve()``
    would silently follow them and miss the warning we want to emit.

    Raises:
        OutOfScopeError: when ``target`` is not under ``repo_root``.
    """
    if not is_under(target, repo_root):
        raise OutOfScopeError(f"{target} is not under {repo_root}")

    target_dir = target if target.is_dir() else target.parent
    # Use literal containment for the walk (not resolved): we want to detect
    # symlinks per-segment, not follow them silently.
    try:
        rel = target_dir.relative_to(repo_root)
    except ValueError:
        # ``target_dir`` is under the resolved repo_root but not its literal
        # form — likely reached through a symlinked ancestor. Walk the
        # resolved chain instead and emit a warning.
        return _walk_resolved_ancestors(target_dir, repo_root)

    chain: list[Path] = [repo_root]
    current = repo_root
    for part in rel.parts:
        current = current / part
        chain.append(current)

    directories: list[Path] = []
    warnings: list[str] = []
    for dir_path in chain:
        if dir_path.is_symlink():
            warnings.append(f"skipped symlinked directory: {to_posix(dir_path)}")
            continue
        directories.append(dir_path)
    return AncestorWalk(directories=directories, warnings=warnings)


def _walk_resolved_ancestors(target_dir: Path, repo_root: Path) -> AncestorWalk:
    """Fallback when the literal target path can't be made relative to
    ``repo_root`` directly — usually because a symlinked ancestor is in play.

    Uses the resolved paths and emits a warning naming the discrepancy. The
    resulting chain is the most useful approximation we can build.
    """
    resolved_repo = repo_root.resolve(strict=False)
    resolved_target = target_dir.resolve(strict=False)
    rel = resolved_target.relative_to(resolved_repo)
    chain = [resolved_repo]
    current = resolved_repo
    for part in rel.parts:
        current = current / part
        chain.append(current)
    return AncestorWalk(
        directories=chain,
        warnings=[
            "target was reached through a symlink; using resolved chain "
            f"for {to_posix(target_dir)}"
        ],
    )
