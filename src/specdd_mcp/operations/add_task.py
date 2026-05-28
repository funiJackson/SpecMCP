"""``add_task``: insert a new task into a spec, byte-faithfully (DESIGN §6.3).

The second write tool. It reuses the same I/O foundation as
``update_task_status`` — :func:`read_preserving`, :func:`write_atomic`, and
the per-file :func:`file_lock` — so the same guarantees hold: a stale-hash
check before any mutation, an atomic temp-file + rename, and every byte that
isn't the inserted line preserved exactly (BOM, CRLF/LF, indentation,
multi-byte characters, comments).

A new task is always ``open`` (``[ ]``). The caller may supply an ``#N`` id
(rejected if malformed or already used) and may anchor the insertion after an
existing task via ``after_task_id``; otherwise the task is appended to the
spec's ``Tasks:`` section. Three placement cases:

  * **Tasks section with tasks** — insert after the anchor (``after_task_id``)
    or after the last task. The new line inherits the anchor task's indent.
  * **Empty Tasks section** (header only) — insert right after the ``Tasks:``
    header at the default two-space indent.
  * **No Tasks section** — append a fresh ``Tasks:`` section at end of file,
    separated from prior content by one blank line.

After building the new line list in memory, the result is re-parsed to obtain
the canonical :class:`ParsedTask` (line/indent/raw as written) — this both
fills the return payload and validates that what we inserted parses back as a
task before anything touches disk.

Pure operation — no MCP wiring. The wrapper in
:mod:`specdd_mcp.server.tools` handles serialization, logging, and exception
conversion.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from specdd_mcp.operations.locks import file_lock
from specdd_mcp.operations.mutate_tasks import read_preserving, write_atomic
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import (
    AddTaskData,
    AddTaskResult,
    Err,
    Ok,
    ParsedSpec,
)

#: A task id is a hash followed by one or more digits (mirrors the parser's
#: ``TASK_LINE_RE`` id group). Validated on the caller-supplied ``task_id``
#: so what we write parses back as an id rather than as part of the text.
_TASK_ID_RE = re.compile(r"#\d+")

#: Indent used when no existing task anchors the new one (empty or absent
#: ``Tasks:`` section). Two spaces matches the convention in every fixture.
_DEFAULT_INDENT = "  "


def add_task(
    spec_path: Path,
    *,
    text: str,
    expected_content_hash: str,
    task_id: str | None = None,
    after_task_id: str | None = None,
) -> AddTaskResult:
    """Insert one ``open`` task into ``spec_path``'s ``Tasks:`` section.

    See DESIGN.md §6.3 for the contract.

    Args:
        spec_path: Absolute or relative path to the ``.sdd`` file.
        text: The task text (the part after ``[ ]`` and the optional id).
            Stripped of surrounding whitespace; must be non-empty and span a
            single line.
        expected_content_hash: SHA-256 the caller last observed for this file.
            Required to prevent silently clobbering a concurrent edit.
        task_id: Optional ``#N`` id for the new task. Rejected when malformed
            or already used by an existing task.
        after_task_id: Optional ``#N`` id of an existing task to insert after.
            When omitted, the task is appended to the section.

    Returns:
        :class:`Ok` wrapping :class:`AddTaskData` (the inserted task, a unified
        diff, and the new content hash).

    Returns :class:`Err` for:
      - ``INVALID_INPUT``  — empty/multiline ``text`` or malformed ``task_id``
      - ``NOT_FOUND``      — ``spec_path`` missing
      - ``ENCODING_ERROR`` — file is not valid UTF-8
      - ``STALE_FILE``     — current hash ≠ ``expected_content_hash``
      - ``ALREADY_EXISTS`` — ``task_id`` is already used in the spec
      - ``TASK_NOT_FOUND`` — ``after_task_id`` matches no task
    """
    clean_text = text.strip()
    if not clean_text:
        return Err(error="INVALID_INPUT", message="task text must not be empty")
    if "\n" in clean_text or "\r" in clean_text:
        return Err(
            error="INVALID_INPUT",
            message="task text must be a single line",
        )
    if task_id is not None and _TASK_ID_RE.fullmatch(task_id) is None:
        return Err(
            error="INVALID_INPUT",
            message=f"task_id must match '#<digits>', got {task_id!r}",
            details={"task_id": task_id},
        )

    with file_lock(spec_path):
        try:
            read = read_preserving(spec_path)
        except FileNotFoundError:
            return Err(
                error="NOT_FOUND",
                message=f"spec not found: {spec_path}",
                details={"path": str(spec_path)},
            )
        except UnicodeDecodeError as exc:
            return Err(
                error="ENCODING_ERROR",
                message=f"spec is not valid UTF-8: {exc.reason}",
                details={"path": str(spec_path)},
            )

        if read.content_hash != expected_content_hash:
            return Err(
                error="STALE_FILE",
                message=(
                    "content_hash mismatch — file changed since last parse; "
                    "re-parse the spec and retry"
                ),
                details={
                    "expected_hash": expected_content_hash,
                    "actual_hash": read.content_hash,
                    "path": str(spec_path),
                },
            )

        parse_result = parse_spec(
            content="".join(read.lines),
            virtual_path=str(spec_path),
        )
        if isinstance(parse_result, Err):  # pragma: no cover — see mutate_tasks
            return parse_result
        spec = parse_result.data
        existing = spec.tasks or []

        if task_id is not None and any(t.id == task_id for t in existing):
            return Err(
                error="ALREADY_EXISTS",
                message=f"task id {task_id} already exists in {spec_path}",
                details={"task_id": task_id, "path": str(spec_path)},
            )

        placement = _resolve_placement(spec, after_task_id)
        if isinstance(placement, Err):
            return placement
        anchor_index, indent = placement

        new_body = _format_task_line(indent, task_id, clean_text)
        new_lines, inserted_line_no = _splice(
            read.lines, anchor_index, new_body
        )

        # Re-parse the candidate content: yields the canonical ParsedTask and
        # proves the inserted line reads back as a task before we write.
        reparse = parse_spec(
            content="".join(new_lines), virtual_path=str(spec_path)
        )
        if isinstance(reparse, Err):  # pragma: no cover — defensive
            return reparse
        inserted = next(
            (t for t in (reparse.data.tasks or []) if t.line == inserted_line_no),
            None,
        )
        if inserted is None:  # pragma: no cover — defensive
            return Err(
                error="INVALID_INPUT",
                message="inserted line did not parse as a task",
                details={"line": inserted_line_no},
            )

        new_hash = write_atomic(
            spec_path, bom_present=read.bom_present, lines=new_lines
        )
        diff = "".join(
            difflib.unified_diff(
                read.lines,
                new_lines,
                fromfile=str(spec_path),
                tofile=str(spec_path),
                n=3,
            )
        )

    return Ok(
        data=AddTaskData(
            spec_path=str(spec_path),
            task=inserted,
            diff=diff,
            new_content_hash=new_hash,
        )
    )


def _resolve_placement(
    spec: ParsedSpec, after_task_id: str | None
) -> tuple[int, str] | Err:
    """Decide where the new task goes and at what indent.

    Returns ``(anchor_index, indent)`` where ``anchor_index`` is the 0-based
    index in the file's line list **after which** the new line is inserted
    (``-1`` means "append a brand-new ``Tasks:`` section at end of file").
    Returns :class:`Err` only for an unmatched ``after_task_id``.
    """
    tasks = spec.tasks or []

    if after_task_id is not None:
        anchor = next((t for t in tasks if t.id == after_task_id), None)
        if anchor is None:
            return Err(
                error="TASK_NOT_FOUND",
                message=f"after_task_id {after_task_id} matches no task",
                details={"after_task_id": after_task_id},
            )
        return anchor.line - 1, anchor.indent

    if tasks:
        last = tasks[-1]
        return last.line - 1, last.indent

    tasks_position = spec.positions.get("tasks")
    if tasks_position is not None:
        # Empty Tasks section: insert just after the "Tasks:" header.
        return tasks_position.start_line - 1, _DEFAULT_INDENT

    # No Tasks section anywhere — signal "append a new section".
    return -1, _DEFAULT_INDENT


def _format_task_line(indent: str, task_id: str | None, text: str) -> str:
    """Build the task line body (no terminator): ``{indent}[ ] {#N }{text}``."""
    id_part = f"{task_id} " if task_id is not None else ""
    return f"{indent}[ ] {id_part}{text}"


def _splice(
    lines: list[str], anchor_index: int, new_body: str
) -> tuple[list[str], int]:
    """Insert ``new_body`` into a copy of ``lines`` and report its 1-based line.

    ``anchor_index == -1`` appends a fresh ``Tasks:`` section at end of file
    (one blank line of separation from prior content). Otherwise the new line
    is inserted immediately after ``lines[anchor_index]``, inheriting that
    line's terminator — or, when the anchor is an unterminated final line,
    terminating the anchor and leaving the new line as the final one.
    """
    term = _detect_terminator(lines)
    new_lines = list(lines)

    if anchor_index < 0:
        if new_lines and not _is_terminated(new_lines[-1]):
            new_lines[-1] = new_lines[-1] + term
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append(term)
        new_lines.append(f"Tasks:{term}")
        new_lines.append(f"{new_body}{term}")
        return new_lines, len(new_lines)

    anchor = new_lines[anchor_index]
    if _is_terminated(anchor):
        anchor_term = "\r\n" if anchor.endswith("\r\n") else "\n"
        new_lines.insert(anchor_index + 1, f"{new_body}{anchor_term}")
    else:
        # Anchor is the file's final, unterminated line. Terminate it and let
        # the new task become the final (unterminated) line, preserving the
        # file's "no trailing newline" style.
        new_lines[anchor_index] = anchor + term
        new_lines.insert(anchor_index + 1, new_body)
    return new_lines, anchor_index + 2


def _is_terminated(line: str) -> bool:
    """True when ``line`` ends with a recognized terminator (``\\n``/``\\r\\n``)."""
    return line.endswith("\n")


def _detect_terminator(lines: list[str]) -> str:
    """The file's dominant line terminator; ``\\n`` when none is present."""
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"
