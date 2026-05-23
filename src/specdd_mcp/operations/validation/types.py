"""Internal types for the validation rule registry.

Wire-facing shapes (:class:`~specdd_mcp.types.ValidationIssue`,
:class:`~specdd_mcp.types.ValidationSummary`,
:class:`~specdd_mcp.types.ValidateSpecData`) live in the top-level
:mod:`specdd_mcp.types` so the MCP server can serialize them. This
module holds the *rule signature* — the Python-internal contract every
single-file or cross-spec rule honors. It never crosses the wire.

A rule is a pure callable: it takes a parsed spec (and, for cross-spec
rules, a repo root) and returns zero or more issues. No side effects,
no I/O, no logging. That keeps every rule individually testable in
isolation and makes the registry order irrelevant to correctness.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

from specdd_mcp.types import ParsedSpec, ValidationIssue

#: Single-file rule signature. Takes a :class:`ParsedSpec` and returns a
#: (possibly empty) list of :class:`ValidationIssue` instances. Pure
#: function — no I/O, no logging, no mutable global state.
SingleFileRule: TypeAlias = Callable[[ParsedSpec], list[ValidationIssue]]

#: Cross-spec rule signature. PR 7 populates the cross-spec registry with
#: rules that walk the resolved spec chain. The signature is keyword-only
#: on ``repo_root`` so an empty placeholder list in PR 5 doesn't lock the
#: shape in awkwardly.
CrossSpecRule: TypeAlias = Callable[
    [ParsedSpec, "Path | None"], list[ValidationIssue]
]
