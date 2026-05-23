"""``check_modification_scope`` — the pre-edit gate (DESIGN §5.6).

This is ``/specc`` step 4: before editing code, the agent asks "am I allowed
to write these files, and does more than one spec claim them?" The answer is
pure composition of three pieces that already exist:

  1. :func:`resolve_spec_chain` (PR 2) — the ordered chain governing the target.
  2. :func:`compute_write_scope` (PR 3, shared via ``write_scope.py``) — the
     nearest spec's expanded writable surface.
  3. Glob / literal matching — to classify proposed files, *including files
     that don't exist yet* (a brand-new file is "allowed" if its intended
     path matches an ``Owns:`` / ``Can modify:`` pattern).

Two-tier matching per proposed file:

  * **Existing file** — matched directly against the snapshot expansion
    (``expand_pattern``), so it's exactly what's writable on disk right now.
  * **New file** — the file isn't on disk yet, so it can't appear in any
    expansion. We fall back to matching the file's *intended* repo-relative
    path against the pattern itself. "Allowed" then means "you may create
    this here," not "this exists." This is the common case where the agent
    is about to author a new module file inside an owned directory.

The multiple-authorities check walks the **whole** chain (not just the
nearest spec): if two or more specs claim the same proposed file, that's the
"two specs both Own the same thing" hazard the SpecDD README warns against.
We surface it rather than refusing to operate — the agent decides.
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

from specdd_mcp.operations.globs import expand_pattern
from specdd_mcp.operations.write_scope import (
    SCOPE_SECTIONS,
    compute_write_scope,
    spec_grants_write_authority,
)
from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.paths import OutOfScopeError, to_repo_relative
from specdd_mcp.types import (
    CheckModificationScopeResult,
    Err,
    MultipleAuthority,
    Ok,
    ParsedSpec,
    ScopeReport,
    SpecChain,
)


def check_modification_scope(
    target: str,
    proposed_files: list[str],
    repo_root: str | None = None,
) -> CheckModificationScopeResult:
    """Classify ``proposed_files`` against the spec chain governing ``target``.

    Args:
        target: Repo-relative path (when ``repo_root`` is given) or absolute
            path to the file or directory the work concerns. Resolved through
            :func:`resolve_spec_chain`, so it inherits the same error modes.
        proposed_files: Paths the agent intends to create or modify. Each may
            be repo-relative or absolute; each is normalized to a POSIX
            repo-relative path before classification. Files that don't exist
            yet are fine — they're matched by pattern (see module docstring).
        repo_root: Absolute repo root. When omitted, auto-detected by walking
            up from ``target`` looking for ``.specdd/`` or ``.git/``.

    Returns:
        ``Ok[ScopeReport]`` on success (even when nothing is allowed — that's
        a valid answer, not an error), or the ``Err`` propagated from
        :func:`resolve_spec_chain`.

    Notes:
        ``allowed`` / ``out_of_scope`` report each proposed file in normalized
        POSIX repo-relative form when it resolves inside the repo, and verbatim
        (as passed) when it resolves outside — an out-of-repo path has no
        repo-relative form and is always ``out_of_scope``.
    """
    chain_result = resolve_spec_chain(target, repo_root=repo_root)
    if isinstance(chain_result, Err):
        return chain_result
    chain = chain_result.data
    root = Path(chain.repo_root)

    # (original, normalized-or-None). ``None`` means the path resolves outside
    # the repo and so can never be claimed by a spec — straight to out_of_scope.
    normalized: list[tuple[str, str | None]] = [
        (raw, _normalize_proposed(raw, root)) for raw in proposed_files
    ]
    in_repo = [rel for _, rel in normalized if rel is not None]

    authority = _nearest_write_authority(chain)
    if authority is None:
        reason = (
            "No SpecDD coverage for this target."
            if not chain.chain
            else (
                "No spec in the chain declares Owns: or Can modify:; "
                "nothing grants write authority."
            )
        )
        return Ok(
            data=ScopeReport(
                authority_source=None,
                effective_scope=[],
                allowed=[],
                out_of_scope=[rel if rel is not None else raw for raw, rel in normalized],
                multiple_authorities=None,
                reason=reason,
            ),
            warnings=chain_result.warnings,
        )

    allowed: list[str] = []
    out_of_scope: list[str] = []
    for raw, rel in normalized:
        if rel is not None and _spec_claim_lines(authority, rel, root):
            allowed.append(rel)
        else:
            out_of_scope.append(rel if rel is not None else raw)

    multiple = detect_multiple_authorities(chain, in_repo, root)
    return Ok(
        data=ScopeReport(
            authority_source=authority.path,
            effective_scope=compute_write_scope(authority, root),
            allowed=allowed,
            out_of_scope=out_of_scope,
            multiple_authorities=multiple or None,
        ),
        warnings=chain_result.warnings,
    )


def detect_multiple_authorities(
    chain: SpecChain,
    proposed_files: list[str],
    root: Path,
) -> list[MultipleAuthority]:
    """Find proposed files claimed by more than one spec in the chain.

    For each file, every spec whose ``Owns:`` / ``Can modify:`` claims it
    contributes one :class:`MultipleAuthority` entry per matching line. The
    file only surfaces when **two or more distinct specs** claim it — a single
    spec matching via two of its own patterns isn't a conflict.

    Entries are emitted in chain order (root first), so a UI can render the
    inheritance ladder top-down.

    Args:
        chain: The resolved chain (already root→leaf ordered).
        proposed_files: Normalized POSIX repo-relative paths (in-repo only).
        root: Absolute repo root, for snapshot expansion.
    """
    out: list[MultipleAuthority] = []
    for proposed in proposed_files:
        claimants: list[tuple[str, int]] = [
            (spec.path, line)
            for spec in chain.chain
            for line in _spec_claim_lines(spec, proposed, root)
        ]
        if len({spec_path for spec_path, _ in claimants}) > 1:
            out.extend(
                MultipleAuthority(spec=spec_path, line=line, file=proposed)
                for spec_path, line in claimants
            )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _nearest_write_authority(chain: SpecChain) -> ParsedSpec | None:
    """The most-specific spec in the chain that declares write authority.

    Walks the chain leaf→root and returns the first spec with an ``Owns:`` or
    ``Can modify:`` section. Mirrors ``build_effective_constraints``'s
    ``write_authority_source`` (last-wins over a root→leaf walk) so the two
    tools never disagree on which spec is the authority.
    """
    for spec in reversed(chain.chain):
        if spec_grants_write_authority(spec):
            return spec
    return None


def _normalize_proposed(raw: str, root: Path) -> str | None:
    """Normalize a proposed path to POSIX repo-relative, or ``None`` if it
    resolves outside the repo.

    Absolute inputs are taken as-is; relative inputs are interpreted as
    repo-relative (anchored at ``root``). Both go through
    :func:`to_repo_relative`, which collapses ``..`` and rejects escapes.
    """
    candidate = Path(raw.replace("\\", "/"))
    absolute = candidate if candidate.is_absolute() else (root / candidate)
    try:
        return to_repo_relative(absolute, root)
    except OutOfScopeError:
        return None


def _spec_claim_lines(spec: ParsedSpec, proposed_rel: str, root: Path) -> list[int]:
    """Line numbers of ``spec``'s ``Owns:`` / ``Can modify:`` patterns that
    claim ``proposed_rel`` (a normalized repo-relative path).

    A pattern claims the file if either the live-filesystem expansion already
    contains it (existing file) or the file's intended path matches the
    pattern (new file). Returns one line per matching pattern, in declaration
    order. Empty list ⇒ this spec does not claim the file.
    """
    spec_dir_abs = (root / spec.path).parent
    spec_dir_rel = str(PurePosixPath(spec.path).parent)
    lines: list[int] = []
    for section in SCOPE_SECTIONS:
        patterns: list[str] = getattr(spec, section) or []
        bullet_lines = spec.bullet_lines.get(section, [])
        for index, pattern in enumerate(patterns):
            if _pattern_claims(pattern, spec_dir_abs, spec_dir_rel, proposed_rel, root):
                lines.append(bullet_lines[index] if index < len(bullet_lines) else 0)
    return lines


def _pattern_claims(
    pattern: str,
    spec_dir_abs: Path,
    spec_dir_rel: str,
    proposed_rel: str,
    root: Path,
) -> bool:
    """True if ``pattern`` claims ``proposed_rel`` — existing-file expansion
    first, then a new-file pattern match."""
    if proposed_rel in expand_pattern(pattern, spec_dir_abs, root).matches:
        return True
    return _pattern_matches_path(pattern, spec_dir_rel, proposed_rel)


def _pattern_matches_path(pattern: str, spec_dir_rel: str, proposed_rel: str) -> bool:
    """Match a spec ``Owns:`` / ``Can modify:`` pattern against an intended
    repo-relative path.

    The pattern is anchored at the spec's own directory (``spec_dir_rel``),
    matching the convention in :mod:`specdd_mcp.operations.globs`. Absolute or
    empty patterns never match here (absolute patterns are non-portable and
    flagged by ``validate_spec``).
    """
    norm = pattern.replace("\\", "/").strip()
    if not norm or PurePosixPath(norm).is_absolute():
        return False
    prefix = "" if spec_dir_rel in ("", ".") else f"{spec_dir_rel}/"
    full = posixpath.normpath(prefix + norm)
    return _glob_to_regex(full).match(proposed_rel) is not None


@lru_cache(maxsize=256)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a POSIX glob pattern to a regex with pathlib-glob semantics:
    ``*`` stays within a path segment, ``**/`` spans zero or more directories,
    ``?`` matches a single non-separator character.

    Cached because the same handful of patterns are re-tested across every
    proposed file in a call.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
            elif pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile("^" + "".join(out) + "$")
