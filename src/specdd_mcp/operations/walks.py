"""Bounded ``.sdd`` walks: iterate spec files under a directory with a cap.

Every cross-spec scan goes through this module so:

1. The monorepo guardrail (``max_specs``) is enforced in one place rather than
   replicated in every consumer.
2. Common noise directories (``.git``, ``.venv``, ``node_modules``, etc.) are
   excluded uniformly without having to remember which ones to skip in each
   tool.
3. macOS AppleDouble metadata (``._foo.sdd``) is filtered out defensively
   (same fix we applied to the benchmark snapshot in PR 1).
4. Symlinked directories are **not** followed, which avoids loops and
   prevents surprise content from appearing in cross-spec scans.

Public surface:

- :func:`walk_specs` — the iterator entry point.
- :class:`WalkResult` — sorted paths + warnings.
- :class:`TooLargeError` — raised when the cap would be exceeded; the MCP
  tool wrappers catch this and convert to ``TOO_LARGE`` :class:`Err`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_SPECS = 1000
"""Default cap on ``.sdd`` files returned by a single walk.

The number isn't sacred — it's a heuristic protecting us against silently
scanning a 50 KLOC monorepo when the user pointed at the wrong directory.
``list_tasks`` and friends accept a per-call override.
"""

# Directories that never contain SpecDD content and that we should never
# descend into. Listed by basename (not absolute path). Hidden tool/cache
# dirs dominate; everything here is "definitely not a SpecDD project area".
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".specdd",  # the SpecDD config dir itself; no .sdd files live here
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
)


class TooLargeError(Exception):
    """Raised when a walk would yield more than ``max_specs`` results.

    Maps to the ``TOO_LARGE`` :class:`~specdd_mcp.types.ErrorCode` at the MCP
    tool boundary.
    """


@dataclass(frozen=True)
class WalkResult:
    """Sorted list of ``.sdd`` paths found, plus any warnings.

    ``warnings`` carries non-fatal observations (e.g. a symlinked directory
    that we elected not to follow). It is propagated by the tool wrappers as
    ``Result.warnings`` so callers can decide what to surface.
    """

    paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def walk_specs(
    directory: Path,
    *,
    max_specs: int = DEFAULT_MAX_SPECS,
) -> WalkResult:
    """Return all ``.sdd`` files under ``directory``.

    Excludes :data:`EXCLUDED_DIR_NAMES`, AppleDouble metadata, and symlinked
    directories. Output is sorted lexicographically for stable ordering.

    Args:
        directory: The root of the walk. Callers should resolve containment
            (e.g. via :func:`specdd_mcp.paths.is_under`) before passing — this
            function trusts the caller.
        max_specs: Cap on the number of returned files. Trips
            :class:`TooLargeError` when exceeded.

    Returns:
        :class:`WalkResult` with sorted paths and any warnings.

    Raises:
        TooLargeError: when more than ``max_specs`` ``.sdd`` files would be
            returned. The exception's message includes the cap that was
            exceeded; the MCP wrapper attaches more detail.
    """
    paths: list[Path] = []
    warnings: list[str] = []

    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        # Walk-time pruning: prevent descent into known-noise directories.
        # Also detect symlinked sub-directories (which os.walk yields with
        # followlinks=False but we still need to NOT recurse into) and warn.
        kept_dirs: list[str] = []
        for dirname in dirnames:
            full_path = Path(dirpath) / dirname
            if dirname in EXCLUDED_DIR_NAMES:
                continue
            if full_path.is_symlink():
                warnings.append(f"skipped symlinked directory: {full_path}")
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            if not filename.endswith(".sdd"):
                continue
            if filename.startswith("._"):
                continue
            paths.append(Path(dirpath) / filename)
            if len(paths) > max_specs:
                raise TooLargeError(
                    f"walk under {directory} would return more than "
                    f"{max_specs} .sdd files; narrow the scope or raise max_specs"
                )

    paths.sort()
    return WalkResult(paths=paths, warnings=warnings)
