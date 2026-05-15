"""Byte-faithful read/write for spec files.

This module is the I/O foundation for ``update_task_status`` (PR 4 C6).
Every write to a ``.sdd`` file must preserve **every byte** that isn't
explicitly being changed: line endings, BOM, indentation, trailing
whitespace on unrelated lines, multi-byte characters, comments.

Two functions:

- :func:`read_preserving` opens a file, detects the UTF-8 BOM, decodes,
  and uses ``str.splitlines(keepends=True)`` to break the text into lines
  that keep their original terminators (``\\r\\n``, ``\\n``, or ``""``
  for the final unterminated line). Joining the list with ``"".join(...)``
  reconstructs the file byte-for-byte.
- :func:`write_atomic` does the inverse: re-encodes, prepends the BOM if
  one was present, and writes via a temp file + atomic rename so a
  half-written state is never visible.

C4 (surgical state-symbol edit) and C6 (orchestrator) compose these to
update a single byte inside one line of the file while leaving everything
else untouched.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.locks import file_lock
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import (
    Err,
    Ok,
    ParsedTask,
    TaskState,
    TaskStateSymbol,
    UpdateApplied,
    UpdateRequest,
    UpdateResult,
    UpdateTaskStatusResult,
)

_UTF8_BOM = b"\xef\xbb\xbf"

# Anchored to start of line. Captures:
#   prefix: leading whitespace + the opening ``[``
#   symbol: exactly one of the five valid state characters
#   suffix: the closing ``]`` plus everything until end-of-string —
#           includes any ID, task text, trailing whitespace, and the
#           line terminator (``\n`` / ``\r\n`` / ``""`` if final).
#
# ``re.DOTALL`` makes ``.`` match newlines, so a line ending in ``\n``
# or ``\r\n`` keeps its terminator in ``suffix``. ``\Z`` (not ``$``)
# matches only at end-of-string, never just before ``\n`` — so the
# terminator can't slip out of the capture.
TASK_STATE_RE = re.compile(
    r"^(?P<prefix>\s*\[)(?P<symbol>[ x\-!?])(?P<suffix>\].*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class ReadResult:
    """Captures everything needed to write the file back byte-for-byte.

    Attributes:
        bom_present: Whether the file started with a UTF-8 BOM. If
            ``True``, :func:`write_atomic` must prepend the BOM on output.
        lines: The file decoded, then split via ``str.splitlines(keepends=True)``.
            Each entry keeps its original terminator (``"\\r\\n"``,
            ``"\\n"``, or ``""`` for the last line if unterminated).
            ``"".join(lines)`` reproduces the (BOM-stripped) text exactly.
        content_hash: SHA-256 of the **raw bytes** read from disk
            (including BOM if present). This is the value the caller
            passes back as ``expected_content_hash`` to detect a stale
            file before writing.
    """

    bom_present: bool
    lines: list[str] = field(default_factory=list)
    content_hash: str = ""


def read_preserving(path: Path) -> ReadResult:
    """Read ``path`` in a way that preserves every byte for round-tripping.

    Raises:
        FileNotFoundError: when ``path`` doesn't exist.
        UnicodeDecodeError: when the (post-BOM) bytes aren't valid UTF-8.
            The orchestrator catches this and surfaces as ``ENCODING_ERROR``.
    """
    raw_bytes = path.read_bytes()
    bom_present = raw_bytes.startswith(_UTF8_BOM)
    body = raw_bytes[len(_UTF8_BOM):] if bom_present else raw_bytes
    text = body.decode("utf-8")
    lines = text.splitlines(keepends=True)
    return ReadResult(
        bom_present=bom_present,
        lines=lines,
        content_hash=content_hash(raw_bytes),
    )


def write_atomic(path: Path, *, bom_present: bool, lines: list[str]) -> str:
    """Write ``lines`` to ``path``, preserving the original BOM toggle,
    via a temp file + atomic rename.

    The temp file lives in the same directory as ``path`` (so the rename
    stays on the same filesystem and is genuinely atomic on POSIX). On
    Windows the rename uses ``os.replace`` semantics, which is atomic for
    the target's visibility but may briefly fail if the destination is
    open in another process — that's the same race ``file_lock`` covers.

    Returns the hex SHA-256 of the **bytes actually written** (BOM + UTF-8
    body). Callers feed this back on the next ``update_task_status`` call
    as ``expected_content_hash``.
    """
    text = "".join(lines)
    body_bytes = text.encode("utf-8")
    raw = _UTF8_BOM + body_bytes if bom_present else body_bytes

    # Use `path.with_name(path.name + ".tmp")` rather than `with_suffix`
    # so it appends ".tmp" without affecting any existing extension
    # (e.g. "foo.tar.gz" → "foo.tar.gz.tmp", not "foo.tar.gz.tmp.gz").
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    return content_hash(raw)


def replace_state_in_line(line: str, new_symbol: TaskStateSymbol) -> str:
    """Surgically replace the state symbol in a single task line.

    Given a line like ``"  [ ] #1 do thing\\n"`` and ``new_symbol="x"``,
    returns ``"  [x] #1 do thing\\n"`` — **exactly one byte changed**.
    Everything else (indentation, ID, task text, trailing whitespace,
    line terminator) is preserved byte-for-byte.

    This is the safety-critical primitive: every other byte the file
    contained before this call is the same byte after. The orchestrator
    (PR 4 C6) composes this with byte-faithful read/write so that the
    only diff between the on-disk file before and after ``update_task_status``
    is the single state symbol byte.

    Raises:
        ValueError: when the line doesn't match the canonical task pattern
            (``\\s*\\[<symbol>\\]<rest>``). Includes:

            - Non-task lines (no leading ``[X]``).
            - Lines where the bracket content is empty (``[]``) or not a
              valid state symbol (``[Y]``).
            - Scenario / Given-When-Then lines that happen to contain
              ``[x]`` mid-text — the anchor at start-of-line excludes them.
    """
    match = TASK_STATE_RE.match(line)
    if match is None:
        raise ValueError(f"not a task line: {line!r}")
    return match["prefix"] + new_symbol + match["suffix"]


# ---------------------------------------------------------------------------
# Identifier resolution: which task does the caller mean?
# ---------------------------------------------------------------------------


ResolverResult: TypeAlias = "Ok[ParsedTask] | Err"


def resolve_task_identifier(
    tasks: list[ParsedTask],
    *,
    task_id: str | None = None,
    task_line: int | None = None,
    task_text_prefix: str | None = None,
) -> ResolverResult:
    """Resolve exactly one of three identifier modes to a :class:`ParsedTask`.

    The caller of ``update_task_status`` (PR 4 C6+) provides at most one
    of ``task_id`` / ``task_line`` / ``task_text_prefix``. This helper
    finds the single matching task or surfaces a clean error.

    Identifier semantics:

    - ``task_id`` (e.g. ``"#42"``) — exact match on ``ParsedTask.id``.
      Unique by convention in a well-formed spec; if the spec accidentally
      has duplicate IDs, returns ``TASK_AMBIGUOUS``.
    - ``task_line`` (1-indexed) — exact match on ``ParsedTask.line``.
      The safest identifier — line numbers are unique per file by
      construction.
    - ``task_text_prefix`` — match where ``task.text.startswith(prefix)``.
      The most ergonomic for humans, but ambiguous when several tasks
      share a prefix. On ambiguity returns ``TASK_AMBIGUOUS`` with
      ``details.candidates`` so the caller can retry with ``task_line``.

    Error envelope:

    - ``INVALID_INPUT``  — zero or more than one identifier provided.
    - ``TASK_NOT_FOUND`` — identifier matched nothing.
    - ``TASK_AMBIGUOUS`` — identifier matched more than one.
      ``details.candidates`` is a list of ``{line, id, text, current_state}``
      dicts, one per match, so the caller can pick the right one.
    """
    provided = sum(
        x is not None for x in (task_id, task_line, task_text_prefix)
    )
    if provided == 0:
        return Err(
            error="INVALID_INPUT",
            message=(
                "provide exactly one of: task_id, task_line, task_text_prefix"
            ),
        )
    if provided > 1:
        return Err(
            error="INVALID_INPUT",
            message=(
                f"provide exactly ONE identifier, got {provided}"
            ),
        )

    if task_id is not None:
        matches = [t for t in tasks if t.id == task_id]
        identifier_desc = f"task_id={task_id!r}"
    elif task_line is not None:
        matches = [t for t in tasks if t.line == task_line]
        identifier_desc = f"task_line={task_line}"
    else:
        assert task_text_prefix is not None
        matches = [
            t for t in tasks if t.text.startswith(task_text_prefix)
        ]
        identifier_desc = f"task_text_prefix={task_text_prefix!r}"

    if not matches:
        return Err(
            error="TASK_NOT_FOUND",
            message=f"no task matched {identifier_desc}",
            details={"identifier": identifier_desc},
        )
    if len(matches) > 1:
        return Err(
            error="TASK_AMBIGUOUS",
            message=(
                f"{len(matches)} tasks matched {identifier_desc}; "
                f"retry with task_line"
            ),
            details={
                "identifier": identifier_desc,
                "candidates": [_task_to_candidate(t) for t in matches],
            },
        )
    return Ok(data=matches[0])


def _task_to_candidate(task: ParsedTask) -> dict[str, object]:
    """Compact ``ParsedTask`` view for ``TASK_AMBIGUOUS`` error details.

    Drops byte-faithful fields (``indent``, ``raw``) that the caller
    doesn't need to disambiguate — keeps the payload small enough to fit
    in a typical error message panel.
    """
    return {
        "line": task.line,
        "id": task.id,
        "text": task.text,
        "current_state": task.state,
    }


# ---------------------------------------------------------------------------
# Batch orchestrator: read → hash-check → parse → resolve all → write
# ---------------------------------------------------------------------------


# Inverse of ``specdd_mcp.parser.tasks._SYMBOL_TO_STATE``. Kept inline (not
# imported across the parser/operations boundary) so the operations layer
# never reaches into the parser's private symbols. A regression test in
# ``tests/test_mutate_orchestrator.py`` asserts the two maps remain
# mutual inverses so they can't silently drift apart.
_STATE_TO_SYMBOL: dict[TaskState, TaskStateSymbol] = {
    "open": " ",
    "done": "x",
    "skipped": "-",
    "blocked": "!",
    "needs_decision": "?",
}


def update_task_status(
    spec_path: Path,
    *,
    expected_content_hash: str,
    updates: list[UpdateRequest],
) -> UpdateTaskStatusResult:
    """Atomically apply a batch of task-state changes to one spec file.

    The contract is **whole-batch atomic on failure**: if any pre-write
    check fails (stale hash, unresolvable identifier, malformed line, etc.),
    the function returns an ``Err`` and the file on disk is **byte-for-byte
    unchanged**. Only when every update in the batch resolves cleanly does
    the function take the per-file lock, write atomically via temp+rename,
    and return ``Ok``.

    Pre-write checks (no file mutation possible at this stage):

      1. Empty / missing updates list → ``INVALID_INPUT``.
      2. Acquire :func:`~specdd_mcp.operations.locks.file_lock` for ``spec_path``
         (blocks if another process holds it).
      3. :func:`read_preserving` → current bytes + current ``content_hash``.
         ``FileNotFoundError`` → ``NOT_FOUND``;
         ``UnicodeDecodeError`` → ``ENCODING_ERROR``.
      4. Compare current hash to ``expected_content_hash``. Mismatch →
         ``STALE_FILE`` with ``details.expected_hash`` / ``details.actual_hash``
         so the caller can re-parse and retry.
      5. Re-parse the just-read content (off the in-memory string so we
         never read disk twice for one operation). Any parser failure on
         already-decoded content is unexpected but propagated cleanly.
      6. Resolve each ``UpdateRequest`` against ``ParsedSpec.tasks`` via
         :func:`resolve_task_identifier`. The first failure aborts the
         batch with that resolver's ``Err`` unchanged — so the caller sees
         a structured ``TASK_NOT_FOUND`` / ``TASK_AMBIGUOUS`` / ``INVALID_INPUT``
         rather than a vague write failure.

    Write step (only reached when every pre-check is green):

      7. Apply :func:`replace_state_in_line` for each resolved task,
         indexed by ``task.line - 1`` into the in-memory ``lines`` list.
         All edits are computed before any disk write — if any line fails
         to match the task pattern, the in-memory mutation is discarded
         and the file is untouched.
      8. :func:`write_atomic` → returns the SHA-256 of bytes written.
      9. ``difflib.unified_diff`` against the original ``lines`` → diff string.
      10. Release lock (via context manager).

    The return envelope carries ``new_content_hash`` so the caller can chain
    further updates without an extra :func:`read_preserving` round-trip.

    Args:
        spec_path: Absolute or relative path to the ``.sdd`` file.
        expected_content_hash: SHA-256 the caller last observed for this
            file. Required to prevent silent clobbering of concurrent edits.
        updates: One or more ``UpdateRequest`` entries; processed in order
            (the order matters only when two updates target the same line —
            the last one wins).
    """
    if not updates:
        return Err(
            error="INVALID_INPUT",
            message="updates list must not be empty",
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

        # Re-parse the bytes we just read, not via path — guarantees parser
        # and writer see identical content, even if another process raced
        # past our lock (which it shouldn't, but defense in depth).
        raw_text = "".join(read.lines)
        parse_result = parse_spec(
            content=raw_text,
            virtual_path=str(spec_path),
        )
        if isinstance(parse_result, Err):  # pragma: no cover — parsing
            # already-decoded UTF-8 content cannot fail with NOT_FOUND /
            # IO_ERROR / ENCODING_ERROR (those are path-mode errors), and
            # the binary heuristic wouldn't trigger on text the lexer just
            # accepted upstream. Surface defensively just in case.
            return parse_result
        tasks = parse_result.data.tasks or []

        # Resolve every identifier *before* mutating anything. First failure
        # aborts the batch with no file change.
        resolved: list[tuple[ParsedTask, TaskState]] = []
        applied: list[UpdateApplied] = []
        for req in updates:
            r = resolve_task_identifier(
                tasks,
                task_id=req.task_id,
                task_line=req.task_line,
                task_text_prefix=req.task_text_prefix,
            )
            if isinstance(r, Err):
                return r
            task = r.data
            resolved.append((task, req.new_state))
            applied.append(
                UpdateApplied(task=task, previous_state=task.state)
            )

        # Build the new line list in memory. Any failure here also aborts
        # without writing — disk state is still byte-for-byte unchanged.
        new_lines = list(read.lines)
        for task, new_state in resolved:
            idx = task.line - 1
            if idx < 0 or idx >= len(new_lines):  # pragma: no cover — parser
                # produces 1-indexed lines bounded by len(lines); reaching
                # this branch means a parser/reader desync we want to know
                # about loudly.
                return Err(
                    error="INVALID_INPUT",
                    message=(
                        f"task at line {task.line} is outside the file "
                        f"(file has {len(new_lines)} lines)"
                    ),
                )
            try:
                new_lines[idx] = replace_state_in_line(
                    new_lines[idx], _STATE_TO_SYMBOL[new_state]
                )
            except ValueError as exc:  # pragma: no cover — parser's
                # ``TASK_LINE_RE`` is strictly stricter than the writer's
                # ``TASK_STATE_RE`` (any line the parser accepted, the
                # writer can also match). Reaching this branch implies a
                # parser/writer regex desync we want to surface loudly.
                return Err(
                    error="INVALID_INPUT",
                    message=(
                        f"task at line {task.line} cannot be rewritten: {exc}"
                    ),
                )

        new_hash = write_atomic(
            spec_path,
            bom_present=read.bom_present,
            lines=new_lines,
        )
        diff = _unified_diff(spec_path, read.lines, new_lines)

    return Ok(
        data=UpdateResult(
            spec_path=str(spec_path),
            applied=applied,
            diff=diff,
            new_content_hash=new_hash,
        )
    )


def _unified_diff(
    path: Path, old_lines: list[str], new_lines: list[str]
) -> str:
    """Return a unified diff between ``old_lines`` and ``new_lines``.

    Both sides are passed to ``difflib.unified_diff`` with their original
    line terminators intact (we pass the same ``keepends=True`` lists used
    for round-trip writes), so the diff output is suitable for direct
    ``git apply`` consumption. Three lines of context is the standard
    default; small enough to stay readable in tool output panels.
    """
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=str(path),
            tofile=str(path),
            n=3,
        )
    )
