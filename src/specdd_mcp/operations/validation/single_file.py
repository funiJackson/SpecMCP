"""Single-file validation rules (DESIGN.md §5.7).

Each rule is a pure :data:`~specdd_mcp.operations.validation.types.SingleFileRule`
callable: takes a :class:`~specdd_mcp.types.ParsedSpec`, returns zero or
more :class:`~specdd_mcp.types.ValidationIssue` instances. No I/O, no
logging, no mutable global state — the registry runner composes them in
:func:`~specdd_mcp.operations.validation.run_validation`.

The rule registry :data:`SINGLE_FILE_RULES` is the public surface; the
individual check functions are exported for direct unit testing.

Anatomy of a rule:

  * It receives a :class:`ParsedSpec` already produced by the parser.
    Parse-level errors (binary content, encoding) are surfaced by
    ``validate_spec`` upstream — rules never run on those inputs.
  * It returns a list of issues, possibly empty. One source-location =
    one issue (e.g. ``DUPLICATE_TASK_ID`` emits one issue per duplicate
    occurrence, not one per duplicated id).
  * It does **not** mutate the spec, the registry, or anything else.

The body-line helper :func:`_section_body_lines` reaches back into the
spec's raw text via :attr:`ParsedSpec.raw` plus
:attr:`ParsedSpec.positions`. The parser's ``Tasks:`` parser silently
drops lines that don't match its strict regex (that's a feature — it
keeps the in-memory ``ParsedTask`` list clean), so for
``INVALID_TASK_STATE`` we re-scan the body to find what was dropped.
"""

from __future__ import annotations

import re

from specdd_mcp.operations.validation.types import SingleFileRule
from specdd_mcp.types import (
    KnownSection,
    ParsedSpec,
    ValidationIssue,
)

#: Default ceiling for the ``LONG_SPEC`` warning. DESIGN §5.7 calls this
#: configurable; the MCP wrapper (C5) exposes a ``max_lines`` input that
#: overrides the keyword default on :func:`check_long_spec`.
DEFAULT_MAX_LINES = 80

__all__ = [
    "DEFAULT_MAX_LINES",
    "SINGLE_FILE_RULES",
    "check_duplicate_task_id",
    "check_empty_section",
    "check_invalid_task_state",
    "check_long_spec",
    "check_malformed_section",
    "check_missing_purpose",
    "check_missing_spec_header",
    "check_ownership_outside_directory",
    "check_unknown_section",
]


# ---------------------------------------------------------------------------
# Body-line helper (shared between rules that need raw lines)
# ---------------------------------------------------------------------------


def _section_body_lines(
    spec: ParsedSpec, section: KnownSection
) -> list[tuple[int, str]]:
    """Slice the spec's raw text to return one ``(line_no, text)`` tuple
    per body line of ``section``.

    ``line_no`` is 1-indexed (matches :class:`SectionPosition`).
    Returns ``[]`` when the section isn't present **or** its body is
    blank-only (``end_line == start_line``, the parser's
    "no-meaningful-content" sentinel).
    """
    pos = spec.positions.get(section)
    if pos is None:
        return []
    if pos.end_line <= pos.start_line:
        return []
    raw_lines = spec.raw.splitlines()
    out: list[tuple[int, str]] = []
    # Body runs from (header_line + 1) through end_line inclusive.
    # ``pos.start_line`` and ``pos.end_line`` are derived from the same
    # ``raw_lines`` we're slicing here, so the indices are guaranteed
    # in-bounds. No defensive guard — a parser/raw desync should fail
    # loudly with an IndexError, not silently drop lines.
    for line_no in range(pos.start_line + 1, pos.end_line + 1):
        out.append((line_no, raw_lines[line_no - 1]))
    return out


# ---------------------------------------------------------------------------
# MISSING_SPEC_HEADER (error)
# ---------------------------------------------------------------------------


def check_missing_spec_header(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires when the spec has no ``Spec:`` header.

    The parser already surfaces this as a soft warning. Validation
    promotes it to an error: every SpecDD file is supposed to lead with
    ``Spec: <name>``; a file without one is technically un-named and
    breaks downstream tooling that quotes the name in error messages.
    """
    if not spec.name:
        return [
            ValidationIssue(
                severity="error",
                code="MISSING_SPEC_HEADER",
                message="No `Spec:` header found.",
                line=1,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# INVALID_TASK_STATE (error)
# ---------------------------------------------------------------------------


# Valid state characters live as a set so a single-char lookup is O(1).
_VALID_STATE_CHARS = frozenset(" x-!?")

# Matches any line that *looks* like a task attempt: leading whitespace,
# an opening bracket, anything (or nothing) up to the closing bracket.
# The captured group is the bracket content; we then check it against
# ``_VALID_STATE_CHARS`` to decide if it's a valid task state symbol.
_TASK_ATTEMPT_RE = re.compile(r"^\s*\[(?P<sym>[^\]]*)\]")


def check_invalid_task_state(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires on every line in a ``Tasks:`` section that **looks** like a
    task but uses an invalid state symbol.

    The parser drops malformed task lines silently (so the in-memory
    ``ParsedTask`` list stays clean for downstream tools). That makes
    ``ParsedSpec.tasks`` insufficient on its own — we re-scan the raw
    body to surface what the parser swallowed. Each offending line
    becomes one issue with the file's 1-indexed line number.

    Examples that fire:

      * ``  [y] do thing``       — ``y`` not in ` x - ! ?`
      * ``  [ok] do thing``      — multi-char bracket
      * ``  [] do thing``        — empty bracket

    Examples that don't fire (parser accepts them):

      * ``  [ ] do thing``       — open
      * ``  [x] do thing``       — done
      * ``  [-] do thing``       — skipped
    """
    issues: list[ValidationIssue] = []
    for line_no, text in _section_body_lines(spec, "tasks"):
        match = _TASK_ATTEMPT_RE.match(text)
        if match is None:
            continue
        sym = match["sym"]
        if len(sym) == 1 and sym in _VALID_STATE_CHARS:
            continue
        issues.append(
            ValidationIssue(
                severity="error",
                code="INVALID_TASK_STATE",
                message=(
                    f"Invalid task state {sym!r}; "
                    f"expected one of ' ', 'x', '-', '!', '?'."
                ),
                line=line_no,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# DUPLICATE_TASK_ID (error)
# ---------------------------------------------------------------------------


def check_duplicate_task_id(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires on every task whose ``#N`` id has already appeared earlier
    in the same spec.

    Emits one issue **per duplicate occurrence** (not per duplicated
    id), with each issue pointing at the duplicate's own line and
    referencing the first occurrence in the message. So a spec where
    ``#1`` appears at lines 4, 10, 15 produces two issues (at 10 and
    15) — line 4 is the original and isn't flagged.

    Tasks without an ``id`` are skipped — ids are optional in SpecDD,
    and the resolver disambiguates by ``task_line`` when none is set.
    """
    issues: list[ValidationIssue] = []
    first_seen: dict[str, int] = {}
    for task in spec.tasks or []:
        if task.id is None:
            continue
        if task.id in first_seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="DUPLICATE_TASK_ID",
                    message=(
                        f"Duplicate task id {task.id}; "
                        f"first seen at line {first_seen[task.id]}."
                    ),
                    line=task.line,
                )
            )
        else:
            first_seen[task.id] = task.line
    return issues


# ---------------------------------------------------------------------------
# MALFORMED_SECTION (error)
# ---------------------------------------------------------------------------


def check_malformed_section(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires when a section has body content but the parser couldn't
    interpret any of it.

    The canonical example is ``Structure:`` with body lines that don't
    match ``path: description`` — the parser produces an empty list of
    entries and the spec ends up with a section the agent will treat
    as missing. This rule catches that mismatch.

    Distinguishing this from ``EMPTY_SECTION`` (warning): empty section
    = header with no body content at all (parser's ``end_line ==
    start_line``); malformed = header with body content that produced
    zero parsed entries.
    """
    issues: list[ValidationIssue] = []
    pos = spec.positions.get("structure")
    if (
        pos is not None
        and pos.end_line > pos.start_line
        and not spec.structure
    ):
        issues.append(
            ValidationIssue(
                severity="error",
                code="MALFORMED_SECTION",
                message=(
                    "`Structure:` section has content but no "
                    "`path: description` entries were recognized."
                ),
                line=pos.start_line,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# MISSING_PURPOSE (warning)
# ---------------------------------------------------------------------------


def check_missing_purpose(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires when the spec has no ``Purpose:`` section at all.

    A ``Purpose:`` header that's present but empty is *not* this rule's
    concern — that's :func:`check_empty_section`. The parser leaves
    :attr:`ParsedSpec.purpose` as ``None`` only when the header never
    appeared (an empty ``Purpose:`` parses to ``""``), so ``is None`` is
    the precise "absent vs. blank" discriminator.

    Severity is ``warning``, not ``error`` — DESIGN §5.7 downgraded this
    from v1's error: the README treats a purpose as a recommendation,
    not a hard requirement.
    """
    if spec.purpose is None:
        return [
            ValidationIssue(
                severity="warning",
                code="MISSING_PURPOSE",
                message=(
                    "No `Purpose:` section. SpecDD recommends a one-line "
                    "statement of what this spec is for."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# UNKNOWN_SECTION (warning)
# ---------------------------------------------------------------------------


def check_unknown_section(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires once per section header whose name isn't canonical.

    SpecDD is intentionally extensible, so an unknown section is a
    *warning*, never an error (DESIGN §5.7: "Many unknown sections →
    many warnings, no errors. By design."). Each issue points at the
    unknown section's header line so the agent can confirm the spelling
    or accept the custom section.
    """
    issues: list[ValidationIssue] = []
    for section in spec.unknown_sections or []:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="UNKNOWN_SECTION",
                message=(
                    f"Unknown section {section.name!r}; not in the canonical "
                    f"SpecDD section list. Kept verbatim."
                ),
                line=section.start_line,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# EMPTY_SECTION (warning)
# ---------------------------------------------------------------------------


def _section_is_empty(spec: ParsedSpec, section: KnownSection) -> bool:
    """True when ``section``'s parsed value carries no content.

    The canonical section key equals the :class:`ParsedSpec` attribute
    name for every section except ``"spec"`` (whose value lives on
    ``name``). Emptiness means: ``None``, a blank string, or an empty
    list — covering inline-value sections (``Purpose:`` etc.) and
    list-shaped sections (``Must:`` etc.) uniformly.

    The ``None`` guard is defensive: the sole caller
    (:func:`check_empty_section`) only passes sections present in
    :attr:`ParsedSpec.positions`, and the parser sets a concrete value
    (string or list) for every section it records a position for — so a
    section reached here is never actually ``None``. The guard keeps the
    helper correct if reused outside that contract.
    """
    attr = "name" if section == "spec" else section
    value = getattr(spec, attr)
    if value is None:  # pragma: no cover - defensive; see docstring
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return len(value) == 0


def check_empty_section(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires on a known section header that carries no content.

    "No content" means *no meaningful body* (the parser's ``end_line ==
    start_line`` sentinel) **and** no inline value — the latter matters
    for single-value sections like ``Purpose: x`` where the content sits
    on the header line. The parsed-value check (:func:`_section_is_empty`)
    captures both.

    Two deliberate exclusions:

      * ``"spec"`` is skipped — an absent or blank ``Spec:`` is already
        the more-severe :func:`check_missing_spec_header` (error). Double
        reporting one defect as both an error and a warning is noise.
      * A section with body content the parser couldn't interpret has
        ``end_line > start_line`` and is :func:`check_malformed_section`
        territory — this rule's body-presence guard means the two never
        double-fire on the same section.
    """
    issues: list[ValidationIssue] = []
    for section, pos in spec.positions.items():
        if section == "spec":
            continue
        if pos.end_line > pos.start_line:
            continue
        if _section_is_empty(spec, section):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="EMPTY_SECTION",
                    message=f"Section `{section}` has a header but no content.",
                    line=pos.start_line,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# LONG_SPEC (warning)
# ---------------------------------------------------------------------------


def check_long_spec(
    spec: ParsedSpec, *, max_lines: int = DEFAULT_MAX_LINES
) -> list[ValidationIssue]:
    """Fires when the spec exceeds ``max_lines`` lines.

    Strictly greater-than: a spec of exactly ``max_lines`` lines passes.
    The threshold is keyword-only with a default so the function still
    satisfies the bare ``(ParsedSpec) -> list`` rule signature in the
    registry; the MCP wrapper overrides ``max_lines`` from its input.

    No ``line`` is attached — this is a whole-file observation, not a
    point defect.
    """
    if spec.line_count > max_lines:
        return [
            ValidationIssue(
                severity="warning",
                code="LONG_SPEC",
                message=(
                    f"Spec is {spec.line_count} lines (> {max_lines}). "
                    f"Consider splitting it into child specs."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# OWNERSHIP_OUTSIDE_DIRECTORY (warning)
# ---------------------------------------------------------------------------


def _escape_reason(pattern: str) -> str | None:
    """Return a human reason if ``pattern`` escapes its spec's directory,
    else ``None``.

    Two ways to escape: an absolute path (leading ``/``) or a
    parent-directory traversal (a ``..`` path segment). SpecDD paths are
    POSIX and repo-relative, so both are suspect — a spec's authority is
    meant to stay within its own subtree.
    """
    p = pattern.strip()
    if p.startswith("/"):
        return "an absolute path"
    if ".." in p.split("/"):
        return "a parent-directory traversal (`..`)"
    return None


def check_ownership_outside_directory(spec: ParsedSpec) -> list[ValidationIssue]:
    """Fires once per ``Owns:`` / ``Can modify:`` pattern that escapes the
    spec's own directory.

    Walks both ownership sections, pairing each pattern with its source
    line via :attr:`ParsedSpec.bullet_lines`. An escaping pattern is one
    that's absolute or contains a ``..`` segment — it would grant the
    spec write authority outside its subtree, which the SpecDD model
    discourages (authority flows down the directory tree, not sideways
    or up).
    """
    issues: list[ValidationIssue] = []
    for section in ("owns", "can_modify"):
        patterns: list[str] | None = getattr(spec, section)
        if not patterns:
            continue
        lines = spec.bullet_lines.get(section, [])
        for idx, pattern in enumerate(patterns):
            reason = _escape_reason(pattern)
            if reason is None:
                continue
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="OWNERSHIP_OUTSIDE_DIRECTORY",
                    message=(
                        f"`{pattern}` escapes the spec's own directory "
                        f"({reason})."
                    ),
                    line=lines[idx] if idx < len(lines) else None,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Registry — errors first, then warnings (display order is irrelevant;
# callers sort by severity/line). DESIGN §5.7 lists these nine rules.
# ---------------------------------------------------------------------------


SINGLE_FILE_RULES: list[SingleFileRule] = [
    check_missing_spec_header,
    check_invalid_task_state,
    check_duplicate_task_id,
    check_malformed_section,
    check_missing_purpose,
    check_unknown_section,
    check_empty_section,
    check_long_spec,
    check_ownership_outside_directory,
]
