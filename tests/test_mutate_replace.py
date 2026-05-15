"""Tests for the surgical task-state replacement in
:mod:`specdd_mcp.operations.mutate_tasks.replace_state_in_line`.

This is the safety-critical primitive — these tests are the byte-level
contract for ``update_task_status`` (PR 4 C6+). Any divergence here would
silently corrupt user spec files.

Every test asserts: replacing the state symbol changes **exactly one
byte** in the output. Everything else (indent, ID, text, trailing
whitespace, line terminator) is preserved.
"""

from __future__ import annotations

import pytest

from specdd_mcp.operations.mutate_tasks import replace_state_in_line
from specdd_mcp.types import TaskStateSymbol

# ---------------------------------------------------------------------------
# All five state symbol transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("new_symbol", [" ", "x", "-", "!", "?"])
def test_replace_to_each_canonical_symbol(new_symbol: TaskStateSymbol) -> None:
    """The five valid TaskStateSymbol values are all accepted as targets."""
    out = replace_state_in_line("[ ] task\n", new_symbol)
    assert out == f"[{new_symbol}] task\n"


@pytest.mark.parametrize("old_symbol", [" ", "x", "-", "!", "?"])
def test_replace_from_each_canonical_symbol(old_symbol: TaskStateSymbol) -> None:
    """The function accepts any of the five valid starting symbols."""
    out = replace_state_in_line(f"  [{old_symbol}] task\n", "x")
    assert out == "  [x] task\n"


# ---------------------------------------------------------------------------
# Preservation: only the one byte changes
# ---------------------------------------------------------------------------


def test_indent_preserved(tmp_path: object = None) -> None:
    """Leading whitespace (any depth, tabs or spaces) is preserved
    verbatim."""
    cases = [
        ("[ ] x\n", "[?] x\n"),
        ("  [ ] x\n", "  [?] x\n"),
        ("    [ ] x\n", "    [?] x\n"),
        ("\t[ ] x\n", "\t[?] x\n"),
        ("\t\t[ ] x\n", "\t\t[?] x\n"),
    ]
    for input_line, expected in cases:
        assert replace_state_in_line(input_line, "?") == expected


def test_task_id_preserved() -> None:
    """An optional ``#N`` ID stays exactly where it was."""
    out = replace_state_in_line("  [ ] #42 do the thing\n", "x")
    assert out == "  [x] #42 do the thing\n"


def test_long_task_text_preserved() -> None:
    """Task text after ``]`` is captured by ``suffix`` and re-emitted intact."""
    text = "this is a long task with various punctuation: commas, periods. And quotes!"
    line = f"  [ ] {text}\n"
    out = replace_state_in_line(line, "x")
    assert out == f"  [x] {text}\n"


def test_trailing_whitespace_preserved() -> None:
    """Trailing whitespace BEFORE the terminator stays."""
    out = replace_state_in_line("  [ ] task   \n", "x")
    assert out == "  [x] task   \n"


# ---------------------------------------------------------------------------
# Line terminators
# ---------------------------------------------------------------------------


def test_lf_terminator_preserved() -> None:
    out = replace_state_in_line("[ ] task\n", "x")
    assert out == "[x] task\n"
    assert out.endswith("\n")
    assert not out.endswith("\r\n")


def test_crlf_terminator_preserved() -> None:
    out = replace_state_in_line("[ ] task\r\n", "x")
    assert out == "[x] task\r\n"


def test_no_terminator_preserved() -> None:
    """The last line of a file may lack a terminator. The output must
    likewise lack one — no spurious ``\\n`` added."""
    out = replace_state_in_line("[ ] last task", "x")
    assert out == "[x] last task"
    assert not out.endswith("\n")


# ---------------------------------------------------------------------------
# Exactly one byte changes
# ---------------------------------------------------------------------------


def test_only_one_byte_differs_at_symbol_position() -> None:
    """Concrete byte-level check: input and output differ at exactly one
    index, and that index is the state symbol position."""
    original = "  [ ] #1 task\n"
    out = replace_state_in_line(original, "x")
    # Same length.
    assert len(original) == len(out)
    # Identify diffs.
    diffs = [
        (i, original[i], out[i])
        for i in range(len(original))
        if original[i] != out[i]
    ]
    assert len(diffs) == 1
    idx, a, b = diffs[0]
    assert a == " "
    assert b == "x"
    # The differing index is between the `[` and `]`.
    assert original[idx - 1] == "["
    assert original[idx + 1] == "]"


def test_no_op_when_new_state_equals_current() -> None:
    """Replacing a symbol with itself returns an identical string. The
    orchestrator can call this unconditionally without special-casing
    'already in target state'."""
    line = "  [x] done already\n"
    assert replace_state_in_line(line, "x") == line


# ---------------------------------------------------------------------------
# Non-task lines must raise (so the orchestrator never edits the wrong line)
# ---------------------------------------------------------------------------


def test_non_task_line_raises() -> None:
    """A regular text line without ``[X]`` structure raises — protects
    against the orchestrator accidentally writing to the wrong line."""
    with pytest.raises(ValueError, match="not a task line"):
        replace_state_in_line("Spec: Foo\n", "x")


def test_empty_brackets_raise() -> None:
    """``[]`` (no symbol char) is not a valid task line."""
    with pytest.raises(ValueError):
        replace_state_in_line("  [] missing symbol\n", "x")


def test_invalid_symbol_raises() -> None:
    """``[Y]`` etc — anything outside the five canonical symbols is not
    a task line."""
    for invalid_line in ["  [Y] x\n", "  [1] x\n", "  [.] x\n", "  [yes] x\n"]:
        with pytest.raises(ValueError):
            replace_state_in_line(invalid_line, "x")


def test_scenario_line_with_brackets_in_middle_does_not_match() -> None:
    """A Given/When/Then line that mentions ``[x]`` in its body — common
    in scenario steps — must NOT be matched. Anchored start-of-line
    requirement is what protects us."""
    scenario_line = "Given the task [x] is complete\n"
    with pytest.raises(ValueError):
        replace_state_in_line(scenario_line, " ")


def test_blank_line_raises() -> None:
    with pytest.raises(ValueError):
        replace_state_in_line("\n", "x")
    with pytest.raises(ValueError):
        replace_state_in_line("", "x")
    with pytest.raises(ValueError):
        replace_state_in_line("   \n", "x")


# ---------------------------------------------------------------------------
# Multi-byte / Unicode in task text
# ---------------------------------------------------------------------------


def test_unicode_text_preserved() -> None:
    """CJK + emoji in task text round-trip unchanged."""
    line = "  [ ] 添加验证 🎯 中文\n"
    out = replace_state_in_line(line, "x")
    assert out == "  [x] 添加验证 🎯 中文\n"


def test_task_text_containing_brackets_preserved() -> None:
    """Task text that happens to contain ``[]`` somewhere AFTER the state
    symbol must not confuse the parser — only the FIRST ``[X]`` at line
    start is the state symbol."""
    out = replace_state_in_line("  [ ] handle [special] cases\n", "x")
    assert out == "  [x] handle [special] cases\n"


def test_line_with_bom_at_start_does_not_match() -> None:
    """A UTF-8 BOM-prefixed line (first line of a BOM file before BOM
    stripping) shouldn't match — BOM isn't whitespace from the regex's
    perspective, and we don't want to silently treat it as a task line."""
    bom_line = "﻿[ ] task\n"
    with pytest.raises(ValueError):
        replace_state_in_line(bom_line, "x")
