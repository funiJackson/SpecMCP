"""Infer a :data:`SpecLevel` from a `.sdd` file's path.

Resolution priority (first match wins):

1. **Filename suffix** — ``foo.service.sdd`` → ``"service"``. The dot-separated
   token immediately before ``.sdd`` is matched against the canonical levels.
2. **Whole-filename match** — ``module.sdd`` → ``"module"``. Useful when a
   directory's name already conveys the area (e.g. ``src/billing/module.sdd``).
3. **Parent directory hint** — ``services/foo.sdd`` → ``"service"``. The
   parent directory's name (lowercased, singular or plural) maps to a level.
4. **Custom suffix fallback** — ``foo.bar.sdd`` → ``"custom"``. The pattern
   ``<name>.<something>.sdd`` is structurally a SpecDD spec at *some* level,
   even if ``<something>`` isn't canonical.
5. **No signal** — ``foo.sdd`` (just a name, no dots, no directory hint) →
   ``"unknown"``.

Case-insensitive throughout. Backslashes in the input are normalized to POSIX
separators (so callers passing Windows paths don't silently degrade).

A file whose name doesn't end in ``.sdd`` returns ``"unknown"`` outright —
this function is only meant for SpecDD specs.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import cast, get_args

from specdd_mcp.types import SpecLevel

# The set of canonical levels (everything in SpecLevel except "custom" and
# "unknown", which are inferred-result fallbacks rather than user-chosen).
_CANONICAL_LEVELS: frozenset[str] = frozenset(get_args(SpecLevel)) - {"custom", "unknown"}

# Parent-directory name (lowercased) → level. Both singular and plural forms
# are accepted because real-world projects use either.
_DIR_HINT_TO_LEVEL: dict[str, SpecLevel] = {
    "service": "service",
    "services": "service",
    "model": "model",
    "models": "model",
    "adapter": "adapter",
    "adapters": "adapter",
    "api": "api",
    "apis": "api",
    "component": "component",
    "components": "component",
    "job": "job",
    "jobs": "job",
    "event": "event",
    "events": "event",
    "policy": "policy",
    "policies": "policy",
    "feature": "feature",
    "features": "feature",
    "module": "module",
    "modules": "module",
}


def infer_level(path: str) -> SpecLevel:
    """Classify a spec file path into a :data:`SpecLevel` literal.

    See the module docstring for the resolution order.
    """
    # Normalize Windows-style separators defensively. Callers should pass POSIX
    # paths (DESIGN §3.8) but this keeps a stray backslash from silently
    # demoting an otherwise-recognizable filename to "unknown".
    normalized = path.replace("\\", "/")
    p = PurePosixPath(normalized)
    name_lower = p.name.lower()

    if not name_lower.endswith(".sdd"):
        return "unknown"

    stem_lower = name_lower[:-4]  # everything before ".sdd"

    # Rule 1: filename has a canonical dot-separated suffix.
    if "." in stem_lower:
        last_part = stem_lower.rsplit(".", 1)[1]
        if last_part in _CANONICAL_LEVELS:
            return cast(SpecLevel, last_part)

    # Rule 2: filename without extension is itself a canonical level name.
    if stem_lower in _CANONICAL_LEVELS:
        return cast(SpecLevel, stem_lower)

    # Rule 3: parent directory hint.
    parent_lower = p.parent.name.lower()
    if parent_lower in _DIR_HINT_TO_LEVEL:
        return _DIR_HINT_TO_LEVEL[parent_lower]

    # Rule 4: filename has a non-canonical suffix — recognizably custom.
    if "." in stem_lower:
        return "custom"

    # Rule 5: nothing matched.
    return "unknown"
