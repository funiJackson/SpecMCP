"""Tests for :mod:`specdd_mcp.parser.tasks`."""

from __future__ import annotations

from specdd_mcp.parser.lexer import Line
from specdd_mcp.parser.tasks import TASK_LINE_RE, parse_tasks


def _lines(*texts: str, start: int = 5) -> list[Line]:
    return [Line(line_no=start + i, text=text) for i, text in enumerate(texts)]


# ---------------------------------------------------------------------------
# State-symbol detection
# ---------------------------------------------------------------------------


def test_all_five_states() -> None:
    tasks = parse_tasks(
        _lines(
            "  [ ] open task",
            "  [x] done task",
            "  [-] skipped task",
            "  [!] blocked task",
            "  [?] needs decision task",
        )
    )
    assert [t.state for t in tasks] == [
        "open",
        "done",
        "skipped",
        "blocked",
        "needs_decision",
    ]
    assert [t.state_symbol for t in tasks] == [" ", "x", "-", "!", "?"]


def test_task_text_extracted_cleanly() -> None:
    tasks = parse_tasks(_lines("  [ ] Validate input."))
    assert tasks[0].text == "Validate input."


def test_task_line_number_preserved() -> None:
    tasks = parse_tasks(_lines("  [ ] one", "  [x] two", "  [!] three", start=10))
    assert [t.line for t in tasks] == [10, 11, 12]


# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------


def test_task_with_id() -> None:
    tasks = parse_tasks(_lines("  [ ] #1 Add validation."))
    assert tasks[0].id == "#1"
    assert tasks[0].text == "Add validation."


def test_task_without_id() -> None:
    tasks = parse_tasks(_lines("  [ ] Add validation."))
    assert tasks[0].id is None


def test_task_multi_digit_id() -> None:
    tasks = parse_tasks(_lines("  [ ] #42 Task forty-two."))
    assert tasks[0].id == "#42"
    assert tasks[0].text == "Task forty-two."


def test_hash_in_text_without_space_is_text_not_id() -> None:
    """``#foo`` with no preceding space-after-id rules is treated as text."""
    tasks = parse_tasks(_lines("  [ ] commit #abc"))
    assert tasks[0].id is None
    assert tasks[0].text == "commit #abc"


# ---------------------------------------------------------------------------
# Indent and raw preservation (critical for PR 4's surgical writes)
# ---------------------------------------------------------------------------


def test_indent_preserved_as_literal_string() -> None:
    tasks = parse_tasks(
        _lines(
            "[ ] no indent",
            "  [ ] two space indent",
            "    [ ] four space indent",
            "\t[ ] tab indent",
        )
    )
    assert [t.indent for t in tasks] == ["", "  ", "    ", "\t"]


def test_raw_line_preserved_verbatim() -> None:
    tasks = parse_tasks(_lines("  [ ] #1 Original text   "))
    # Note: raw includes the leading indent and the trailing whitespace.
    assert tasks[0].raw == "  [ ] #1 Original text   "
    # But text is trimmed.
    assert tasks[0].text == "Original text"


# ---------------------------------------------------------------------------
# Skipping non-task lines (PR 5 flags them as INVALID_TASK_STATE)
# ---------------------------------------------------------------------------


def test_non_task_lines_skipped() -> None:
    tasks = parse_tasks(
        _lines(
            "  [ ] real task",
            "  this is not a task",
            "  [Y] bad state symbol",
            "  [ ] another real task",
        )
    )
    assert [t.text for t in tasks] == ["real task", "another real task"]


def test_blank_lines_skipped() -> None:
    tasks = parse_tasks(_lines("  [ ] one", "", "  [x] two", "  "))
    assert len(tasks) == 2


def test_bracket_with_no_text_skipped() -> None:
    """`[ ]` with nothing after isn't a valid task (no description)."""
    tasks = parse_tasks(_lines("  [ ]", "  [ ] real one"))
    assert len(tasks) == 1
    assert tasks[0].text == "real one"


# ---------------------------------------------------------------------------
# Regex behavior (anchored)
# ---------------------------------------------------------------------------


def test_regex_anchored_to_start_of_line() -> None:
    """A `[x]` appearing mid-line in scenario text must NOT be matched."""
    assert TASK_LINE_RE.match("Given the task [x] is complete") is None


def test_regex_rejects_invalid_symbol() -> None:
    assert TASK_LINE_RE.match("  [Y] something") is None
    assert TASK_LINE_RE.match("  [12] something") is None


# ---------------------------------------------------------------------------
# Multi-byte / Unicode text
# ---------------------------------------------------------------------------


def test_multibyte_task_text() -> None:
    tasks = parse_tasks(_lines("  [ ] 添加验证 🎉"))
    assert tasks[0].text == "添加验证 🎉"
    assert tasks[0].state == "open"
