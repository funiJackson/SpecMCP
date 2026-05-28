"""``create_spec``: scaffold a new ``.sdd`` file (DESIGN §6.2).

Assembles a well-formed spec from structured inputs, validates it, and writes
it — refusing to clobber an existing file. The third write tool, but unlike
``update_task_status`` / ``add_task`` it creates rather than edits, so it uses
an exclusive ``O_EXCL`` create instead of the read-modify-write path: that
atomically fails if the target already exists, needs no stale-hash check
(there is no prior content), and leaves no sidecar lock file behind.

Formatting is fixed and canonical — sections are emitted in the SpecDD
reference order (``Spec``, ``Platform``, ``Purpose``, ``Owns``, ``Can
modify``, ``Can read``, ``References``, ``Must``, ``Must not``, ``Depends
on``, ``Forbids``, ``Tasks``), each bullet indented two spaces, one blank
line between sections, trailing newline. A section with no content is
omitted entirely rather than written as an empty header.

"Validate before writing" is enforced: the assembled text is parsed and run
through the single-file validation rules; any **error** aborts the write
(warnings are surfaced but don't block). Because the formatting is controlled
here, errors are near-impossible in practice — the check is a guarantee, not
a likely failure mode.

Pure operation — no MCP wiring. The wrapper in
:mod:`specdd_mcp.server.tools` handles serialization, logging, and exception
conversion.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.validation import run_validation
from specdd_mcp.parser.levels import infer_level
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import (
    CreateSpecData,
    CreateSpecResult,
    Err,
    Ok,
    SpecLevel,
    ValidationIssue,
)

_INDENT = "  "

#: List-shaped sections emitted in canonical SpecDD order. The tuple order is
#: the file's section order; ``Spec`` / ``Platform`` / ``Purpose`` (scalar)
#: and ``Tasks`` (state-prefixed) are handled separately around this list.
_LIST_SECTIONS: tuple[tuple[str, str], ...] = (
    ("owns", "Owns:"),
    ("can_modify", "Can modify:"),
    ("can_read", "Can read:"),
    ("references", "References:"),
    ("must", "Must:"),
    ("must_not", "Must not:"),
    ("depends_on", "Depends on:"),
    ("forbids", "Forbids:"),
)


def create_spec(
    path: Path,
    *,
    name: str,
    level: SpecLevel | None = None,
    platform: str | None = None,
    purpose: str | None = None,
    owns: list[str] | None = None,
    can_modify: list[str] | None = None,
    can_read: list[str] | None = None,
    references: list[str] | None = None,
    must: list[str] | None = None,
    must_not: list[str] | None = None,
    depends_on: list[str] | None = None,
    forbids: list[str] | None = None,
    tasks: list[str] | None = None,
) -> CreateSpecResult:
    """Scaffold a new ``.sdd`` file at ``path`` from structured inputs.

    See DESIGN.md §6.2 for the contract.

    Args:
        path: Destination for the new file. Parent directories are created if
            missing. Refuses to overwrite an existing file.
        name: The ``Spec:`` header. Must be non-empty.
        level: Optional declared :data:`SpecLevel`. Purely advisory — level is
            path-derived in SpecDD, so a value that disagrees with the
            path-inferred level produces a warning, not a write.
        platform: Optional ``Platform:`` value (inline).
        purpose: Optional ``Purpose:`` block (multi-line allowed).
        owns, can_modify, can_read, references, must, must_not, depends_on,
        forbids: Optional list sections. Empty/blank entries are dropped; a
            section with no surviving content is omitted.
        tasks: Optional task texts, each written as an ``open`` (``[ ]``) task.

    Returns:
        :class:`Ok` wrapping :class:`CreateSpecData` (the written path, the
        exact content, and its SHA-256). Warnings carry validation findings
        and any level mismatch.

    Returns :class:`Err` for:
      - ``INVALID_INPUT``  — empty ``name``, or the assembled spec fails
        validation with one or more errors (``details.issues``)
      - ``ALREADY_EXISTS`` — ``path`` already exists
    """
    if not name.strip():
        return Err(error="INVALID_INPUT", message="name must not be empty")

    if path.exists():
        return Err(
            error="ALREADY_EXISTS",
            message=f"refusing to overwrite existing file: {path}",
            details={"path": str(path)},
        )

    sections = {
        "owns": owns,
        "can_modify": can_modify,
        "can_read": can_read,
        "references": references,
        "must": must,
        "must_not": must_not,
        "depends_on": depends_on,
        "forbids": forbids,
    }
    content = _build_content(name, platform, purpose, sections, tasks)

    parsed = parse_spec(content=content, virtual_path=str(path))
    if isinstance(parsed, Err):  # pragma: no cover — generated content parses
        return parsed
    validation = run_validation(parsed.data, check_inheritance=False)
    if validation.summary.errors > 0:
        return Err(
            error="INVALID_INPUT",
            message="assembled spec failed validation",
            details={
                "issues": [
                    issue.model_dump()
                    for issue in validation.issues
                    if issue.severity == "error"
                ]
            },
        )

    warnings = [_format_issue(issue) for issue in validation.issues]
    if level is not None:
        inferred = infer_level(str(path))
        if inferred != level:
            warnings.append(
                f"declared level {level!r} but path infers {inferred!r}; "
                f"rename the file so its path conveys the intended level"
            )

    raw = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # ``x`` = exclusive create: atomically fails if the path appeared
        # since the early existence check, closing the TOCTOU window.
        with open(path, "xb") as handle:
            handle.write(raw)
    except FileExistsError:
        return Err(
            error="ALREADY_EXISTS",
            message=f"refusing to overwrite existing file: {path}",
            details={"path": str(path)},
        )

    return Ok(
        data=CreateSpecData(
            path=str(path),
            content=content,
            content_hash=content_hash(raw),
        ),
        warnings=warnings,
    )


def _build_content(
    name: str,
    platform: str | None,
    purpose: str | None,
    sections: dict[str, list[str] | None],
    tasks: list[str] | None,
) -> str:
    """Assemble canonical ``.sdd`` text from the provided inputs.

    Sections appear in SpecDD reference order; empty ones are skipped. Blocks
    are joined by one blank line and the file ends with a single newline.
    """
    blocks: list[str] = [f"Spec: {name.strip()}"]

    if platform and platform.strip():
        blocks.append(f"Platform: {platform.strip()}")

    if purpose and purpose.strip():
        blocks.append(_scalar_block("Purpose:", purpose))

    for attr, header in _LIST_SECTIONS:
        items = _clean(sections.get(attr))
        if items:
            blocks.append(_list_block(header, items))

    task_items = _clean(tasks)
    if task_items:
        blocks.append(
            "\n".join(["Tasks:", *(f"{_INDENT}[ ] {item}" for item in task_items)])
        )

    return "\n\n".join(blocks) + "\n"


def _scalar_block(header: str, value: str) -> str:
    """A header followed by an indented (possibly multi-line) free-text body."""
    body = [f"{_INDENT}{line}" for line in value.strip().split("\n")]
    return "\n".join([header, *body])


def _list_block(header: str, items: list[str]) -> str:
    """A header followed by one indented bullet per item."""
    return "\n".join([header, *(f"{_INDENT}{item}" for item in items)])


def _clean(items: list[str] | None) -> list[str]:
    """Strip each entry and drop the empties; ``None`` becomes ``[]``."""
    if not items:
        return []
    return [stripped for stripped in (item.strip() for item in items) if stripped]


def _format_issue(issue: ValidationIssue) -> str:
    """Render a validation warning as a flat string for ``Result.warnings``."""
    where = f" (line {issue.line})" if issue.line is not None else ""
    return f"{issue.code}: {issue.message}{where}"
