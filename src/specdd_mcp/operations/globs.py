"""Pattern expansion for ``Owns:`` / ``Can modify:`` entries.

A SpecDD spec declares writable surface with patterns like::

    Owns:
      invoice.ts
      invoice.test.ts

    Can modify:
      src/billing/*
      **/*.test.ts

This module resolves those patterns against the live filesystem and returns
the POSIX repo-relative paths that match **right now**. Snapshot semantics:
callers re-call when they want a fresh view.

Conventions:

- Patterns are interpreted relative to the **spec file's directory**, not
  ``repo_root``. A spec at ``src/billing/invoice.sdd`` with ``Owns: invoice.ts``
  means ``src/billing/invoice.ts``.
- Globs support ``*``, ``**``, and ``?`` per ``pathlib.Path.glob``.
- Matches are **files only**; directories are filtered out.
- Noise directories (``.git``, ``.venv``, ``node_modules``, …) are skipped
  even if a pattern would otherwise match.
- macOS AppleDouble metadata (``._foo``) is filtered defensively.
- Absolute patterns are non-portable and produce zero matches; ``validate_spec``
  (PR 5) flags this on the source side.
- Patterns that resolve outside ``repo_root`` are silently skipped; the spec
  is claiming surface it doesn't own — also handled by ``validate_spec``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from specdd_mcp.operations.walks import EXCLUDED_DIR_NAMES
from specdd_mcp.paths import OutOfScopeError, to_repo_relative


@dataclass(frozen=True)
class GlobExpansion:
    """One ``Owns:`` / ``Can modify:`` pattern + its current matches.

    ``pattern`` is the literal text as written in the spec (used for
    provenance and re-expansion). ``matches`` is the POSIX repo-relative
    paths that resolved at expansion time.
    """

    pattern: str
    matches: list[str] = field(default_factory=list)


def expand_pattern(
    pattern: str,
    spec_dir: Path,
    repo_root: Path,
) -> GlobExpansion:
    """Expand a single ``Owns`` / ``Can modify`` pattern.

    Args:
        pattern: The line from the spec, exactly as written.
        spec_dir: The directory containing the spec that wrote this pattern.
            Relative patterns resolve against this.
        repo_root: Used to compute repo-relative output paths and to anchor
            the excluded-directory filter.

    Returns:
        :class:`GlobExpansion` with the original pattern preserved and sorted,
        deduplicated, POSIX-formatted matches.
    """
    # Defensive: a Windows-style separator slipped into a portable pattern.
    pattern_normalized = pattern.replace("\\", "/")

    # Absolute patterns are non-portable. validate_spec flags them; here we
    # just don't match anything.
    if PurePosixPath(pattern_normalized).is_absolute():
        return GlobExpansion(pattern=pattern, matches=[])

    matches: list[str] = []
    for path in spec_dir.glob(pattern_normalized):
        if not path.is_file():
            continue
        if path.name.startswith("._"):
            continue
        if _has_excluded_ancestor(path, repo_root):
            continue
        try:
            rel = to_repo_relative(path, repo_root)
        except OutOfScopeError:
            # Pattern walked outside repo_root (e.g. ``../../../foo.ts``).
            # Skip silently; validate_spec flags the source.
            continue
        matches.append(rel)

    return GlobExpansion(pattern=pattern, matches=sorted(set(matches)))


def _has_excluded_ancestor(path: Path, repo_root: Path) -> bool:
    """True if any directory between ``repo_root`` and ``path`` (exclusive of
    the final filename) is in :data:`EXCLUDED_DIR_NAMES`.

    Uses resolved paths so symlinks don't sneak a noise directory past the
    check.
    """
    try:
        rel = path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return False
    return any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1])
