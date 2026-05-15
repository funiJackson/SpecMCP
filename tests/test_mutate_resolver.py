"""Tests for :func:`specdd_mcp.operations.mutate_tasks.resolve_task_identifier`.

The resolver maps one of three identifier modes (``task_id``, ``task_line``,
``task_text_prefix``) onto a single :class:`ParsedTask`, or returns a
clean ``Err``. The orchestrator (PR 4 C6) calls this before mutating
anything on disk — a bad identifier must surface as a structured error,
never as a write to the wrong task.
"""

from __future__ import annotations

from specdd_mcp.operations.mutate_tasks import resolve_task_identifier
from specdd_mcp.types import Err, Ok, ParsedTask


def _task(
    *,
    line: int,
    text: str,
    id: str | None = None,
    state: str = "open",
    symbol: str = " ",
) -> ParsedTask:
    """Compact helper to construct ParsedTask fixtures."""
    return ParsedTask(
        state=state,  # type: ignore[arg-type]
        state_symbol=symbol,  # type: ignore[arg-type]
        text=text,
        id=id,
        line=line,
        indent="  ",
        raw=f"  [{symbol}] {f'{id} ' if id else ''}{text}",
    )


# ---------------------------------------------------------------------------
# task_id mode
# ---------------------------------------------------------------------------


def test_task_id_matches_unique() -> None:
    tasks = [
        _task(line=4, id="#1", text="one"),
        _task(line=5, id="#2", text="two"),
    ]
    result = resolve_task_identifier(tasks, task_id="#2")
    assert isinstance(result, Ok)
    assert result.data.text == "two"
    assert result.data.line == 5


def test_task_id_not_found() -> None:
    tasks = [_task(line=4, id="#1", text="one")]
    result = resolve_task_identifier(tasks, task_id="#99")
    assert isinstance(result, Err)
    assert result.error == "TASK_NOT_FOUND"
    assert "#99" in result.message


def test_task_id_duplicate_returns_ambiguous() -> None:
    """A spec with two tasks both labelled ``#1`` (parser keeps both;
    validate_spec flags via DUPLICATE_TASK_ID) → resolver still has to
    decide and refuses with TASK_AMBIGUOUS."""
    tasks = [
        _task(line=4, id="#1", text="first"),
        _task(line=10, id="#1", text="second"),
    ]
    result = resolve_task_identifier(tasks, task_id="#1")
    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    candidates = result.details["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 2


# ---------------------------------------------------------------------------
# task_line mode
# ---------------------------------------------------------------------------


def test_task_line_matches() -> None:
    tasks = [
        _task(line=4, text="line four"),
        _task(line=10, text="line ten"),
    ]
    result = resolve_task_identifier(tasks, task_line=10)
    assert isinstance(result, Ok)
    assert result.data.text == "line ten"


def test_task_line_not_found() -> None:
    tasks = [_task(line=4, text="one")]
    result = resolve_task_identifier(tasks, task_line=99)
    assert isinstance(result, Err)
    assert result.error == "TASK_NOT_FOUND"
    assert "99" in result.message


# ---------------------------------------------------------------------------
# task_text_prefix mode
# ---------------------------------------------------------------------------


def test_text_prefix_unique() -> None:
    tasks = [
        _task(line=4, text="Add validation for currency."),
        _task(line=5, text="Remove deprecated API."),
    ]
    result = resolve_task_identifier(tasks, task_text_prefix="Remove")
    assert isinstance(result, Ok)
    assert result.data.line == 5


def test_text_prefix_ambiguous() -> None:
    """Two tasks starting with the same prefix → TASK_AMBIGUOUS with
    candidates."""
    tasks = [
        _task(line=4, id="#1", text="Add validation for currency"),
        _task(line=5, id="#2", text="Add validation for amount"),
        _task(line=6, id="#3", text="Persist invoice"),
    ]
    result = resolve_task_identifier(tasks, task_text_prefix="Add validation")
    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    candidates = result.details["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 2


def test_text_prefix_not_found() -> None:
    tasks = [_task(line=4, text="one")]
    result = resolve_task_identifier(tasks, task_text_prefix="nope")
    assert isinstance(result, Err)
    assert result.error == "TASK_NOT_FOUND"


def test_text_prefix_case_sensitive() -> None:
    """``startswith`` is case-sensitive — be predictable."""
    tasks = [_task(line=4, text="Add validation")]
    result = resolve_task_identifier(tasks, task_text_prefix="add")
    assert isinstance(result, Err)
    assert result.error == "TASK_NOT_FOUND"


# ---------------------------------------------------------------------------
# Candidate payload shape
# ---------------------------------------------------------------------------


def test_candidates_include_line_id_text_state() -> None:
    """The ``candidates`` list in TASK_AMBIGUOUS details has the four
    fields a caller needs to disambiguate."""
    tasks = [
        _task(line=4, id="#1", text="Add A", state="open", symbol=" "),
        _task(line=5, id="#2", text="Add B", state="done", symbol="x"),
    ]
    result = resolve_task_identifier(tasks, task_text_prefix="Add")
    assert isinstance(result, Err)
    candidates = result.details["candidates"]
    assert candidates == [
        {"line": 4, "id": "#1", "text": "Add A", "current_state": "open"},
        {"line": 5, "id": "#2", "text": "Add B", "current_state": "done"},
    ]


def test_candidate_with_no_id_returns_none_for_id_field() -> None:
    tasks = [
        _task(line=4, text="Add A"),  # no id
        _task(line=5, text="Add B"),
    ]
    result = resolve_task_identifier(tasks, task_text_prefix="Add")
    assert isinstance(result, Err)
    candidates = result.details["candidates"]
    assert candidates[0]["id"] is None  # type: ignore[index]


# ---------------------------------------------------------------------------
# Input validation: exactly one identifier required
# ---------------------------------------------------------------------------


def test_no_identifier_returns_invalid_input() -> None:
    tasks = [_task(line=4, text="x")]
    result = resolve_task_identifier(tasks)
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_two_identifiers_return_invalid_input() -> None:
    tasks = [_task(line=4, id="#1", text="x")]
    result = resolve_task_identifier(tasks, task_id="#1", task_line=4)
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_three_identifiers_return_invalid_input() -> None:
    tasks = [_task(line=4, id="#1", text="x")]
    result = resolve_task_identifier(
        tasks, task_id="#1", task_line=4, task_text_prefix="x"
    )
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# Empty tasks list
# ---------------------------------------------------------------------------


def test_empty_tasks_with_any_identifier_returns_not_found() -> None:
    for kwargs in (
        {"task_id": "#1"},
        {"task_line": 1},
        {"task_text_prefix": "anything"},
    ):
        result = resolve_task_identifier([], **kwargs)
        assert isinstance(result, Err)
        assert result.error == "TASK_NOT_FOUND"
