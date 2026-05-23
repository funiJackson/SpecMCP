"""Shared ``Owns:`` / ``Can modify:`` expansion — one source of truth.

Two tools turn a spec's write-authority sections into expanded
:class:`~specdd_mcp.types.WriteScopeEntry` rows:

  * ``get_effective_constraints`` (``operations/merge.py``) — accumulates the
    write scope across the *whole* chain.
  * ``check_modification_scope`` (``operations/scope.py``) — classifies
    proposed files against the *nearest* spec's write scope.

Extracting the per-spec expansion here keeps the two from drifting on what
"the writable surface of this spec" means. The helper is pure: a spec plus a
repo root in, a list of entries (with full ``path:line`` provenance) out.
Patterns resolve relative to the spec's own directory, matching the
convention in :mod:`specdd_mcp.operations.globs`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from specdd_mcp.operations.globs import expand_pattern
from specdd_mcp.types import KnownSection, ParsedSpec, WriteScopeEntry

#: Sections that grant write authority, in declaration-precedence order
#: (``Owns:`` before ``Can modify:``). A spec carrying either is a
#: write-authority source.
SCOPE_SECTIONS: tuple[Literal["owns", "can_modify"], ...] = ("owns", "can_modify")


def spec_grants_write_authority(spec: ParsedSpec) -> bool:
    """True when ``spec`` declares any ``Owns:`` or ``Can modify:`` pattern.

    Used to find the *nearest* spec in a chain that grants write authority —
    the one whose scope a write is checked against.
    """
    return bool(spec.owns or spec.can_modify)


def compute_write_scope(spec: ParsedSpec, repo_root: Path) -> list[WriteScopeEntry]:
    """Expand one spec's ``Owns:`` / ``Can modify:`` patterns against the live
    filesystem.

    Args:
        spec: The spec whose write-authority sections to expand. ``spec.path``
            is the POSIX repo-relative path the resolver assigned, so the
            spec's own directory (``repo_root / spec.path``'s parent) is the
            base for relative patterns.
        repo_root: Absolute repo root, used to anchor the expansion and emit
            repo-relative match paths.

    Returns:
        One :class:`WriteScopeEntry` per pattern — even patterns that match
        nothing right now (so the caller can still see what was *claimed*,
        not just what currently exists). Entries preserve declaration order:
        all ``Owns:`` patterns, then all ``Can modify:`` patterns.
    """
    spec_dir = (repo_root / spec.path).parent
    entries: list[WriteScopeEntry] = []
    for scope_section in SCOPE_SECTIONS:
        patterns: list[str] = getattr(spec, scope_section) or []
        line_numbers = spec.bullet_lines.get(cast(KnownSection, scope_section), [])
        for index, pattern in enumerate(patterns):
            expansion = expand_pattern(pattern, spec_dir, repo_root)
            entries.append(
                WriteScopeEntry(
                    pattern=expansion.pattern,
                    matches=expansion.matches,
                    source=spec.path,
                    source_line=(
                        line_numbers[index] if index < len(line_numbers) else 0
                    ),
                )
            )
    return entries
