"""Validation rule registry and entry point.

``validate_spec`` (the MCP tool — see :mod:`specdd_mcp.server.tools`)
ultimately funnels through :func:`run_validation` here. The registry
pattern keeps each rule a pure callable so:

  * Rules can be unit-tested in isolation (no MCP, no parser cascade).
  * Adding a rule is one line in either :data:`SINGLE_FILE_RULES`
    (:mod:`~specdd_mcp.operations.validation.single_file`) or
    :data:`CROSS_SPEC_RULES`
    (:mod:`~specdd_mcp.operations.validation.cross_spec`). No edits to
    ``run_validation``'s body.
  * PR 7 populated the cross-spec registry. The runner signature stayed
    stable across PR 5 → PR 7: cross-spec rules keep the
    ``(ParsedSpec, repo_root)`` shape and the runner still gates them on
    ``check_inheritance``.

Rule ordering is intentionally **not** semantically significant: any
rule's findings are merged into one flat list and downstream callers
sort by (severity, line) when displaying. Two rules that surface
identical issues from the same input are tolerated — duplicates only
arise from accidental rule overlap, which the per-rule tests catch.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.validation.cross_spec import CROSS_SPEC_RULES
from specdd_mcp.operations.validation.single_file import (
    DEFAULT_MAX_LINES,
    SINGLE_FILE_RULES,
    check_long_spec,
)
from specdd_mcp.operations.validation.types import (
    SingleFileRule,
)
from specdd_mcp.types import (
    ParsedSpec,
    ValidateSpecData,
    ValidationIssue,
    ValidationSummary,
)

__all__ = [
    "CROSS_SPEC_RULES",
    "SINGLE_FILE_RULES",
    "run_validation",
]


def run_validation(
    spec: ParsedSpec,
    *,
    check_inheritance: bool = False,
    repo_root: Path | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> ValidateSpecData:
    """Run every registered rule against ``spec`` and assemble the result.

    Args:
        spec: The :class:`ParsedSpec` to validate. Caller is responsible
            for surfacing any parse-level errors before reaching this
            function — if the parser couldn't produce a ``ParsedSpec``,
            ``validate_spec`` returns ``Err(PARSE_ERROR)`` upstream
            without invoking this runner.
        check_inheritance: When ``True``, also runs every callable in
            :data:`CROSS_SPEC_RULES`. In PR 5 this list is empty so the
            flag is effectively a no-op — accepted for forward compat.
        repo_root: Passed through to cross-spec rules (they may need it
            to walk the chain). Ignored when ``check_inheritance`` is
            ``False`` or the cross-spec registry is empty.
        max_lines: Ceiling for the ``LONG_SPEC`` warning. Overrides
            :func:`check_long_spec`'s keyword default so the MCP wrapper
            can expose a configurable threshold without the registry
            entries having to carry per-rule config.

    Returns:
        :class:`ValidateSpecData` with every issue produced by every
        rule, plus a pre-computed :class:`ValidationSummary` so the
        caller doesn't have to count.
    """
    issues: list[ValidationIssue] = []

    for rule in SINGLE_FILE_RULES:
        # ``check_long_spec`` is the one rule with tunable config; bind the
        # caller's ``max_lines`` to it while every other rule runs bare. The
        # registry stays a flat list of bare ``SingleFileRule`` callables.
        if rule is check_long_spec:
            issues.extend(check_long_spec(spec, max_lines=max_lines))
        else:
            issues.extend(rule(spec))

    if check_inheritance:
        for cross_rule in CROSS_SPEC_RULES:
            issues.extend(cross_rule(spec, repo_root))

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")

    return ValidateSpecData(
        issues=issues,
        summary=ValidationSummary(errors=errors, warnings=warnings),
    )


# Re-export the single-file registry for tests / introspection.
_ = SingleFileRule  # silence unused-import for the type alias
