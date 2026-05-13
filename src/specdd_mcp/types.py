"""Pydantic models for SpecDD MCP.

The source of truth is DESIGN.md §3. Every shape in this module is a direct
translation of one of the TypeScript interfaces there. No logic lives in this
module — it is purely data definitions plus serialization config. Behavior
belongs in :mod:`specdd_mcp.parser` and :mod:`specdd_mcp.operations`.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from specdd_mcp import __version__ as _PKG_VERSION

# ---------------------------------------------------------------------------
# Enums (Literal types — cheaper than enum.Enum for JSON round-tripping)
# ---------------------------------------------------------------------------

SpecLevel: TypeAlias = Literal[
    "app",
    "module",
    "feature",
    "service",
    "model",
    "adapter",
    "api",
    "component",
    "job",
    "event",
    "policy",
    "custom",
    "unknown",
]

KnownSection: TypeAlias = Literal[
    "spec",
    "platform",
    "purpose",
    "structure",
    "owns",
    "can_modify",
    "can_read",
    "references",
    "must",
    "must_not",
    "depends_on",
    "forbids",
    "exposes",
    "accepts",
    "returns",
    "raises",
    "handles",
    "tasks",
    "scenarios",
    "examples",
    "done_when",
]

TaskState: TypeAlias = Literal[
    "open",
    "done",
    "skipped",
    "blocked",
    "needs_decision",
]

TaskStateSymbol: TypeAlias = Literal[" ", "x", "-", "!", "?"]

ErrorCode: TypeAlias = Literal[
    "NOT_FOUND",
    "PARSE_ERROR",
    "OUT_OF_SCOPE",
    "TASK_NOT_FOUND",
    "TASK_AMBIGUOUS",
    "STALE_FILE",
    "ALREADY_EXISTS",
    "INVALID_INPUT",
    "IO_ERROR",
    "ENCODING_ERROR",
    "TOO_LARGE",
]

ConflictKind: TypeAlias = Literal[
    "depends_on_vs_forbids",
    "must_vs_must_not",
    "duplicate_parent_rule",
    "task_violates_must_not",
]


# ---------------------------------------------------------------------------
# Section position + structure entries
# ---------------------------------------------------------------------------


class SectionPosition(BaseModel):
    """Line range of a section (1-indexed, inclusive of both endpoints)."""

    start_line: int
    end_line: int


class UnknownSection(BaseModel):
    """A section whose header isn't in the canonical list (DESIGN §3.1)."""

    name: str
    lines: list[str]
    start_line: int
    end_line: int


class StructureEntry(BaseModel):
    """One `path: description` line under a Structure: section."""

    path: str
    description: str


# ---------------------------------------------------------------------------
# Tasks and scenarios
# ---------------------------------------------------------------------------


class ParsedTask(BaseModel):
    """One task line. `raw` and `indent` enable byte-faithful rewriting in PR 4."""

    state: TaskState
    state_symbol: TaskStateSymbol
    text: str
    id: str | None = None
    line: int
    indent: str
    raw: str


class ParsedScenario(BaseModel):
    """A Gherkin-style scenario block."""

    name: str
    steps: list[str]
    start_line: int
    end_line: int


# ---------------------------------------------------------------------------
# ParsedSpec — the top-level parsed shape (DESIGN §3.1)
# ---------------------------------------------------------------------------


class ParsedSpec(BaseModel):
    """The full parsed view of one `.sdd` file.

    Every section is optional and present only when it appeared in the source.
    Positions for each known section that appeared are recorded in
    :attr:`positions` so downstream tools can quote ``path:line`` provenance.
    Unknown sections are preserved verbatim in :attr:`unknown_sections`.
    """

    # Identity
    path: str
    name: str
    level: SpecLevel

    # Raw fallback
    raw: str
    line_count: int
    encoding: Literal["utf-8"] = "utf-8"
    parser_version: str = Field(default=_PKG_VERSION)

    # Sections — list-shaped sections are lists of stripped lines.
    platform: str | None = None
    purpose: str | None = None
    structure: list[StructureEntry] | None = None

    owns: list[str] | None = None
    can_modify: list[str] | None = None
    can_read: list[str] | None = None
    references: list[str] | None = None

    must: list[str] | None = None
    must_not: list[str] | None = None
    depends_on: list[str] | None = None
    forbids: list[str] | None = None

    exposes: list[str] | None = None
    accepts: list[str] | None = None
    returns: list[str] | None = None
    raises: list[str] | None = None
    handles: list[str] | None = None

    tasks: list[ParsedTask] | None = None
    scenarios: list[ParsedScenario] | None = None
    examples: list[str] | None = None
    done_when: list[str] | None = None

    # Line spans of every known section that appeared. Allows
    # validate_spec / update_task_status / etc. to point at exact locations.
    positions: dict[KnownSection, SectionPosition] = Field(default_factory=dict)

    # Sections whose names aren't in the canonical list. Preserved with
    # full content + line numbers for forward compatibility.
    unknown_sections: list[UnknownSection] | None = None


# ---------------------------------------------------------------------------
# SpecChain (DESIGN §3.4)
# ---------------------------------------------------------------------------


class MalformedSpec(BaseModel):
    """A spec found in the chain that failed to parse but did not abort resolution."""

    path: str
    error: str


class SpecChain(BaseModel):
    """The ordered chain of specs from repo root to a target."""

    target: str
    repo_root: str
    chain: list[ParsedSpec] = Field(default_factory=list)
    nearest: ParsedSpec | None = None
    malformed: list[MalformedSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Constraint and merged-view types (DESIGN §3.5, §3.6)
# ---------------------------------------------------------------------------


class Constraint(BaseModel):
    """One inherited rule with full ``path:line`` provenance.

    The ``line`` field is mandatory — without it, downstream tools can't surface
    where a rule came from, which defeats half the point of merging.
    """

    rule: str
    source: str
    line: int


class ChainSummaryEntry(BaseModel):
    """One-line orientation per spec in the chain."""

    path: str
    level: SpecLevel


class WriteScopeEntry(BaseModel):
    """One entry in ``effective_write_scope``.

    ``pattern`` is the original ``Owns:`` / ``Can modify:`` line as written.
    ``matches`` is the snapshot expansion against the live filesystem at the
    moment the call was made.
    """

    pattern: str
    matches: list[str]
    source: str
    source_line: int


class ReferenceEntry(BaseModel):
    """One horizontal ``References:`` entry surfaced from the chain.

    The Python attribute is :attr:`from_` because ``from`` is a reserved
    keyword; the JSON key remains ``from`` via the alias.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    line: int


class Conflict(BaseModel):
    """A mechanically detected disagreement between two rules in the chain.

    Convention: ``rule_a`` is the child / newer / more local rule; ``rule_b``
    is the parent / ancestor / inherited rule. PR 3's conflict detectors honor
    this ordering so callers can rely on it for messaging.
    """

    kind: ConflictKind
    rule_a: Constraint
    rule_b: Constraint


class TaskWithSource(ParsedTask):
    """A task plus the repo-relative path of the spec it lives in."""

    source: str


class EffectiveConstraints(BaseModel):
    """Merged view of the entire spec chain for one target (DESIGN §3.6).

    This is what ``get_effective_constraints`` returns and what the ``/specc``
    slash command leans on once per implementation task.
    """

    target: str
    chain_summary: list[ChainSummaryEntry] = Field(default_factory=list)

    must: list[Constraint] = Field(default_factory=list)
    must_not: list[Constraint] = Field(default_factory=list)
    forbids: list[Constraint] = Field(default_factory=list)
    depends_on: list[Constraint] = Field(default_factory=list)
    done_when: list[Constraint] = Field(default_factory=list)

    effective_read_scope: list[Constraint] = Field(default_factory=list)

    effective_write_scope: list[WriteScopeEntry] = Field(default_factory=list)
    write_authority_source: str | None = None

    tasks: list[TaskWithSource] = Field(default_factory=list)

    conflicts: list[Conflict] = Field(default_factory=list)

    references: list[ReferenceEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Result envelope (DESIGN §3.7)
# ---------------------------------------------------------------------------


T = TypeVar("T")


class Ok(BaseModel, Generic[T]):
    """Success branch of :data:`Result`."""

    ok: Literal[True] = True
    data: T
    warnings: list[str] = Field(default_factory=list)


class Err(BaseModel):
    """Failure branch of :data:`Result`.

    ``details`` carries error-specific context (e.g. ``candidates`` for
    ``TASK_AMBIGUOUS``, ``expected_hash`` / ``actual_hash`` for ``STALE_FILE``).
    """

    ok: Literal[False] = False
    error: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)


# A generic-ish alias for documentation purposes. Tools at call sites declare
# their own concrete result types (e.g. ``Ok[ParsedSpec] | Err``) so static
# checkers can narrow on the ``ok`` discriminator.
Result: TypeAlias = "Ok[T] | Err"


# ---------------------------------------------------------------------------
# Concrete result aliases — one per tool that exists today.
# Future PRs add more here rather than at each call site.
# ---------------------------------------------------------------------------

ParseSpecResult: TypeAlias = "Ok[ParsedSpec] | Err"
ResolveChainResult: TypeAlias = "Ok[SpecChain] | Err"
