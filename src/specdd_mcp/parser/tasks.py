"""Parser for the Tasks section.

Each task line matches:

    ^<indent>\\[<state_symbol>\\] (#<id> )?<text>$

State symbols are exactly one of:

    " "  open
    "x"  done
    "-"  skipped
    "!"  blocked
    "?"  needs_decision

``indent`` and ``raw`` are preserved on the returned :class:`ParsedTask` so
PR 4's ``update_task_status`` can rewrite the file byte-faithfully.

Lines that don't match the task pattern are skipped silently here; PR 5's
``validate_spec`` flags them as ``INVALID_TASK_STATE``.
"""

from __future__ import annotations

import re
from typing import cast

from specdd_mcp.parser.lexer import Line
from specdd_mcp.types import ParsedTask, TaskState, TaskStateSymbol

# Anchored to start of line, allowing arbitrary leading whitespace. The state
# symbol is exactly one character from the canonical set. An optional ``#N``
# identifier may follow with at least one space before the text. The task text
# is captured non-greedily and any trailing whitespace is consumed by ``\s*$``.
TASK_LINE_RE = re.compile(
    r"^(?P<indent>\s*)\[(?P<symbol>[ x\-!?])\]\s*"
    r"(?P<id>#\d+)?\s*"
    r"(?P<text>\S.*?)\s*$"
)

_SYMBOL_TO_STATE: dict[TaskStateSymbol, TaskState] = {
    " ": "open",
    "x": "done",
    "-": "skipped",
    "!": "blocked",
    "?": "needs_decision",
}


def parse_tasks(body_lines: list[Line]) -> list[ParsedTask]:
    """Extract every task line from a Tasks section's body.

    Returns one :class:`ParsedTask` per matching line, in source order.
    Non-matching lines (blank lines, malformed entries) are skipped.
    """
    tasks: list[ParsedTask] = []
    for line in body_lines:
        match = TASK_LINE_RE.match(line.text)
        if match is None:
            continue
        symbol = cast(TaskStateSymbol, match.group("symbol"))
        state = _SYMBOL_TO_STATE[symbol]
        task_id = match.group("id") or None
        tasks.append(
            ParsedTask(
                state=state,
                state_symbol=symbol,
                text=match.group("text"),
                id=task_id,
                line=line.line_no,
                indent=match.group("indent"),
                raw=line.text,
            )
        )
    return tasks
