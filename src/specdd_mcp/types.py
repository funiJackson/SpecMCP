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

    # Per-bullet line numbers for list-shaped sections (``must``, ``must_not``,
    # ``owns``, ``forbids``, ``depends_on``, etc.). Parallel arrays: the
    # ``i``-th entry in ``must`` corresponds to ``bullet_lines["must"][i]``.
    # Used by ``operations/merge.py`` to build ``Constraint`` objects with
    # exact ``path:line`` provenance (DESIGN §3.5 mandates this). Continuation
    # lines anchor at the bullet's start.
    bullet_lines: dict[KnownSection, list[int]] = Field(default_factory=dict)

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


# ---------------------------------------------------------------------------
# update_task_status — batch task-state mutation (DESIGN §5.5)
# ---------------------------------------------------------------------------


class UpdateRequest(BaseModel):
    """One desired state change within a batch ``update_task_status`` call.

    The caller picks exactly one of ``task_id`` / ``task_line`` /
    ``task_text_prefix`` to identify which task to update — the resolver
    enforces this and surfaces ``INVALID_INPUT`` otherwise.
    """

    new_state: TaskState
    task_id: str | None = None
    task_line: int | None = None
    task_text_prefix: str | None = None


class UpdateApplied(BaseModel):
    """One element of ``UpdateResult.applied`` — the post-update task plus
    the state it carried before the update fired."""

    task: ParsedTask
    previous_state: TaskState


class UpdateResult(BaseModel):
    """Success payload for ``update_task_status``.

    Attributes:
        spec_path: The spec that was modified (caller-supplied form).
        applied: One entry per update in batch order. Each carries the
            resolved :class:`ParsedTask` (as observed before the write) and
            the state it had pre-update — useful for "undo" workflows.
        diff: Unified diff between the pre- and post-write file contents.
            Empty when the batch was effectively a no-op (e.g. every
            update set a task to its current state).
        new_content_hash: SHA-256 of the bytes just written. Callers feed
            this back as ``expected_content_hash`` on the next call.
    """

    spec_path: str
    applied: list[UpdateApplied] = Field(default_factory=list)
    diff: str
    new_content_hash: str


UpdateTaskStatusResult: TypeAlias = "Ok[UpdateResult] | Err"


# ---------------------------------------------------------------------------
# validate_spec — single-file + (PR 7) cross-spec validation (DESIGN §5.7)
# ---------------------------------------------------------------------------


ValidationCode: TypeAlias = Literal[
    # Errors
    "MISSING_SPEC_HEADER",
    "INVALID_TASK_STATE",
    "DUPLICATE_TASK_ID",
    "MALFORMED_SECTION",
    # Warnings (single-file)
    "MISSING_PURPOSE",
    "UNKNOWN_SECTION",
    "EMPTY_SECTION",
    "LONG_SPEC",
    "OWNERSHIP_OUTSIDE_DIRECTORY",
    # Warnings (cross-spec — populated in PR 7)
    "DUPLICATE_PARENT_RULE",
    "CONFLICTING_INHERITANCE",
    "TASK_VIOLATES_MUSTNOT",
]


ValidationSeverity: TypeAlias = Literal["error", "warning"]


class ValidationIssue(BaseModel):
    """One finding from ``validate_spec`` (DESIGN §5.7).

    Single-file rules populate ``severity`` / ``code`` / ``message`` / ``line``.
    Cross-spec rules (PR 7) additionally populate ``related_spec`` (the
    ancestor spec the finding ties to, in ``path:line`` form) and
    ``related_line`` so the slash command can quote both sides.
    """

    severity: ValidationSeverity
    code: ValidationCode
    message: str
    line: int | None = None
    related_spec: str | None = None
    related_line: int | None = None


class ValidationSummary(BaseModel):
    """Aggregate counts for quick UI display — pre-computed so a caller
    doesn't have to count ``severity == "error"`` themselves."""

    errors: int
    warnings: int


class ValidateSpecData(BaseModel):
    """Success payload for ``validate_spec``."""

    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: ValidationSummary


ValidateSpecResult: TypeAlias = "Ok[ValidateSpecData] | Err"


# ---------------------------------------------------------------------------
# check_modification_scope (DESIGN §5.6)
# ---------------------------------------------------------------------------


class MultipleAuthority(BaseModel):
    """One claim on a proposed file from a spec other than the nearest one.

    Multiple-authority entries are emitted in *spec-chain order* — root
    first — so a UI can render the inheritance ladder top-down.
    """

    spec: str
    line: int
    file: str


class ScopeReport(BaseModel):
    """Success payload for ``check_modification_scope`` (DESIGN §5.6).

    Attributes:
        authority_source: Repo-relative path of the **nearest** spec
            that grants write authority — ``None`` when the target has
            no SpecDD coverage.
        effective_scope: The nearest spec's ``Owns:`` / ``Can modify:``
            patterns with their snapshot expansions (same shape as
            :class:`WriteScopeEntry`).
        allowed: Proposed files that are inside the effective scope —
            either because the file already exists and matched a glob,
            or because the file's *intended* path matches a literal /
            glob pattern (new-file allowance).
        out_of_scope: Proposed files that do **not** match any pattern.
        multiple_authorities: Populated when more than one spec in the
            chain claims a proposed file (the SpecDD README warns
            against this; we surface it rather than refusing to operate).
            ``None`` when no overlap exists.
        reason: Human-facing summary when no authority is found at all.
    """

    authority_source: str | None
    effective_scope: list[WriteScopeEntry] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    multiple_authorities: list[MultipleAuthority] | None = None
    reason: str | None = None


CheckModificationScopeResult: TypeAlias = "Ok[ScopeReport] | Err"


# ---------------------------------------------------------------------------
# list_specs — repo-wide spec index (DESIGN §5.8)
# ---------------------------------------------------------------------------


class TaskSummary(BaseModel):
    """Per-state task counts for one spec in a :class:`SpecIndexEntry`.

    One field per :data:`TaskState`. A spec with no ``Tasks:`` section
    summarizes to all zeros rather than ``None`` so callers can sum across
    the index without null-checking.
    """

    open: int = 0
    done: int = 0
    skipped: int = 0
    blocked: int = 0
    needs_decision: int = 0


class SpecIndexEntry(BaseModel):
    """One row in the ``list_specs`` index (DESIGN §5.8).

    ``task_summary`` is present only when the caller asked for it
    (``include_task_summary=True``); otherwise it is ``None``.
    """

    path: str
    name: str
    level: SpecLevel
    line_count: int
    task_summary: TaskSummary | None = None


ListSpecsResult: TypeAlias = "Ok[list[SpecIndexEntry]] | Err"


# ---------------------------------------------------------------------------
# find_ownership_conflicts — multi-owner overlap detection (DESIGN §5.9)
# ---------------------------------------------------------------------------


OwnershipConflictKind: TypeAlias = Literal[
    "literal",
    "glob_overlap",
    "glob_vs_literal",
]


class OwnershipOwner(BaseModel):
    """One spec's claim on a contested item via ``Owns:``.

    ``pattern`` is the literal ``Owns:`` line as written; ``line`` is where
    that line sits in ``spec`` so callers can quote ``path:line`` provenance.
    """

    spec: str
    line: int
    pattern: str


class OwnershipConflict(BaseModel):
    """An item that more than one spec claims via ``Owns:`` (DESIGN §5.9).

    ``item`` is the resolved repo-relative path the owners collide on.
    ``kind`` records how the collision arises: two literals, two overlapping
    globs, or a glob that subsumes a literal. ``owners`` lists every claiming
    spec, ordered by ``(spec, line)``.
    """

    item: str
    kind: OwnershipConflictKind
    owners: list[OwnershipOwner] = Field(default_factory=list)


FindOwnershipConflictsResult: TypeAlias = "Ok[list[OwnershipConflict]] | Err"
