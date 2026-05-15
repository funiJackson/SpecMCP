"""Tests for :func:`specdd_mcp.operations.mutate_tasks.update_task_status`.

The orchestrator is the highest-risk function in PR 4 — every other
component is a single primitive (hash, lock, read, write, replace,
resolve) but this one composes them all into one all-or-nothing
operation that touches files on disk. The tests here verify the two
core invariants that DESIGN.md §5.5 commits to:

1. **No partial writes.** Any pre-write failure (stale hash, bad
   identifier, malformed line) returns ``Err`` with the file
   byte-for-byte unchanged.
2. **Byte-faithful happy path.** Successful writes change exactly the
   state symbol byte(s) targeted by the batch — every other byte in
   the file is preserved.

Deeper concurrency / fixture-matrix coverage lives in
``test_mutate_preservation.py`` (PR 4 C8) and
``test_mutate_concurrency.py`` (PR 4 C9).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.mutate_tasks import (
    _STATE_TO_SYMBOL,
    update_task_status,
)
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.parser.tasks import _SYMBOL_TO_STATE
from specdd_mcp.types import Err, Ok, UpdateRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_SAMPLE_SPEC = (
    "Spec: Sample\n"
    "\n"
    "Tasks:\n"
    "  [ ] #1 First task\n"
    "  [ ] #2 Second task\n"
    "  [ ] #3 Third task\n"
    "  [x] #4 Already done\n"
)


def _write_spec(tmp_path: Path, content: str = _SAMPLE_SPEC) -> Path:
    """Write ``content`` to a fresh ``spec.sdd`` under ``tmp_path``."""
    spec = tmp_path / "spec.sdd"
    spec.write_bytes(content.encode("utf-8"))
    return spec


def _current_hash(path: Path) -> str:
    """Read the file and return its SHA-256 (what the parser would see)."""
    return content_hash(path.read_bytes())


# ---------------------------------------------------------------------------
# Symbol-state mapping consistency
# ---------------------------------------------------------------------------


def test_state_to_symbol_is_exact_inverse_of_parser_mapping() -> None:
    """``_STATE_TO_SYMBOL`` (writer) and ``_SYMBOL_TO_STATE`` (parser) must
    stay mutual inverses — if they drift, a state symbol round-trip through
    parse → mutate produces a different byte than the source. Regression
    guard so future edits to one side force matching edits to the other."""
    for symbol, state in _SYMBOL_TO_STATE.items():
        assert _STATE_TO_SYMBOL[state] == symbol
    for state, symbol in _STATE_TO_SYMBOL.items():
        assert _SYMBOL_TO_STATE[symbol] == state


# ---------------------------------------------------------------------------
# Happy path — single update
# ---------------------------------------------------------------------------


def test_single_update_marks_task_done(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Ok)
    assert "[x] #1 First task" in spec.read_text()
    # Sibling task lines must remain byte-identical.
    assert "[ ] #2 Second task" in spec.read_text()
    assert "[ ] #3 Third task" in spec.read_text()
    assert "[x] #4 Already done" in spec.read_text()


def test_single_update_returns_new_content_hash_matching_disk(
    tmp_path: Path,
) -> None:
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Ok)
    assert result.data.new_content_hash == _current_hash(spec), (
        "returned new_content_hash must match a re-read of the file — "
        "otherwise callers chaining updates will hit a spurious STALE_FILE"
    )


def test_single_update_only_changes_one_byte(tmp_path: Path) -> None:
    """The whole point of the byte-faithful pipeline: the only byte
    difference between before-bytes and after-bytes is the state symbol."""
    spec = _write_spec(tmp_path)
    before = spec.read_bytes()
    expected = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )
    assert isinstance(result, Ok)

    after = spec.read_bytes()
    diffs = [
        i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b
    ]
    assert len(diffs) == 1, (
        f"expected exactly 1 byte change, got {len(diffs)} at {diffs}"
    )
    assert before[diffs[0]:diffs[0] + 1] == b" "
    assert after[diffs[0]:diffs[0] + 1] == b"x"


def test_single_update_returns_unified_diff(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Ok)
    diff = result.data.diff
    # Unified-diff hallmark: ``---``/``+++`` headers + hunk marker.
    assert diff.startswith("---")
    assert "\n+++ " in diff
    assert "@@" in diff
    assert "-  [ ] #1 First task" in diff
    assert "+  [x] #1 First task" in diff


def test_applied_carries_previous_state_and_resolved_task(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Ok)
    assert len(result.data.applied) == 1
    entry = result.data.applied[0]
    assert entry.task.id == "#1"
    assert entry.previous_state == "open", (
        "previous_state must be the state observed *before* mutation, "
        "so callers can undo accurately"
    )


# ---------------------------------------------------------------------------
# Happy path — multi-update batch
# ---------------------------------------------------------------------------


def test_multi_update_applies_all_changes(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[
            UpdateRequest(new_state="done", task_id="#1"),
            UpdateRequest(new_state="skipped", task_id="#2"),
            UpdateRequest(new_state="blocked", task_id="#3"),
        ],
    )

    assert isinstance(result, Ok)
    content = spec.read_text()
    assert "[x] #1 First task" in content
    assert "[-] #2 Second task" in content
    assert "[!] #3 Third task" in content
    assert "[x] #4 Already done" in content  # untouched


def test_multi_update_applied_list_preserves_batch_order(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[
            UpdateRequest(new_state="blocked", task_id="#3"),
            UpdateRequest(new_state="done", task_id="#1"),
            UpdateRequest(new_state="skipped", task_id="#2"),
        ],
    )

    assert isinstance(result, Ok)
    ids_in_order = [entry.task.id for entry in result.data.applied]
    assert ids_in_order == ["#3", "#1", "#2"]


def test_mixed_identifier_modes_in_one_batch(tmp_path: Path) -> None:
    """A batch can mix ``task_id`` / ``task_line`` / ``task_text_prefix``
    freely — the resolver picks per request."""
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[
            UpdateRequest(new_state="done", task_id="#1"),
            UpdateRequest(new_state="done", task_line=5),  # #2
            UpdateRequest(new_state="done", task_text_prefix="Third"),
        ],
    )

    assert isinstance(result, Ok)
    content = spec.read_text()
    assert "[x] #1 First task" in content
    assert "[x] #2 Second task" in content
    assert "[x] #3 Third task" in content


# ---------------------------------------------------------------------------
# Whole-batch atomicity: any failure → file untouched
# ---------------------------------------------------------------------------


def test_stale_hash_returns_err_without_touching_file(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    before = spec.read_bytes()

    result = update_task_status(
        spec,
        expected_content_hash="0" * 64,  # wrong on purpose
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Err)
    assert result.error == "STALE_FILE"
    assert result.details["expected_hash"] == "0" * 64
    assert result.details["actual_hash"] == _current_hash(spec)
    assert spec.read_bytes() == before, (
        "STALE_FILE must leave the file byte-for-byte unchanged"
    )


def test_unresolvable_identifier_in_middle_of_batch_rolls_back_all(
    tmp_path: Path,
) -> None:
    """An invalid identifier on the **second** update must leave the file
    untouched — including changes the **first** update would have made.
    This is the whole-batch-atomic invariant."""
    spec = _write_spec(tmp_path)
    before = spec.read_bytes()
    expected = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[
            UpdateRequest(new_state="done", task_id="#1"),  # would succeed
            UpdateRequest(new_state="done", task_id="#999"),  # doesn't exist
        ],
    )

    assert isinstance(result, Err)
    assert result.error == "TASK_NOT_FOUND"
    assert spec.read_bytes() == before, (
        "batch atomicity violated: first update wrote despite later failure"
    )


def test_ambiguous_identifier_rolls_back_batch(tmp_path: Path) -> None:
    content = (
        "Spec: Sample\n"
        "\n"
        "Tasks:\n"
        "  [ ] Add validation for currency\n"
        "  [ ] Add validation for amount\n"
    )
    spec = _write_spec(tmp_path, content)
    before = spec.read_bytes()
    expected = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )

    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    candidates = result.details["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 2
    assert spec.read_bytes() == before


def test_invalid_request_zero_identifiers_rolls_back_batch(
    tmp_path: Path,
) -> None:
    spec = _write_spec(tmp_path)
    before = spec.read_bytes()
    expected = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done")],  # no identifier
    )

    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"
    assert spec.read_bytes() == before


def test_invalid_request_two_identifiers_rolls_back_batch(
    tmp_path: Path,
) -> None:
    spec = _write_spec(tmp_path)
    before = spec.read_bytes()
    expected = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[
            UpdateRequest(new_state="done", task_id="#1", task_line=4),
        ],
    )

    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"
    assert spec.read_bytes() == before


def test_empty_updates_list_returns_invalid_input(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)
    before = spec.read_bytes()
    expected = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[],
    )

    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"
    assert spec.read_bytes() == before


def test_missing_file_returns_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.sdd"

    result = update_task_status(
        missing,
        expected_content_hash="0" * 64,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"
    assert "does_not_exist.sdd" in result.message


def test_invalid_utf8_returns_encoding_error(tmp_path: Path) -> None:
    """File with invalid UTF-8 bytes is reported, not silently corrupted."""
    spec = tmp_path / "spec.sdd"
    # 0xFF is not valid UTF-8 anywhere
    spec.write_bytes(b"Spec: Bad\n\xff\xff\n")
    expected = content_hash(spec.read_bytes())

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Err)
    assert result.error == "ENCODING_ERROR"


# ---------------------------------------------------------------------------
# Lock acquisition — sidecar evidence
# ---------------------------------------------------------------------------


def test_lock_sidecar_file_is_created_during_update(tmp_path: Path) -> None:
    """Indirect proof that ``file_lock`` was invoked: its sidecar file
    persists on the filesystem. A future C9 subprocess test exercises the
    cross-process exclusivity directly."""
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Ok)
    assert (tmp_path / "spec.sdd.lock").exists()


# ---------------------------------------------------------------------------
# Round-trip via parse_spec → update_task_status
# ---------------------------------------------------------------------------


def test_caller_pattern_parse_then_hash_then_update(tmp_path: Path) -> None:
    """The recommended caller pattern: ``parse_spec`` → grab hash from
    a separate read → call ``update_task_status``. Verifies the end-to-end
    flow works without the caller having to know about ``read_preserving``."""
    spec = _write_spec(tmp_path)

    # Caller parses to find the task
    parsed = parse_spec(path=str(spec))
    assert isinstance(parsed, Ok)
    assert parsed.data.tasks is not None and len(parsed.data.tasks) == 4

    # Caller computes the hash via the same bytes the parser saw
    expected_hash = content_hash(spec.read_bytes())

    result = update_task_status(
        spec,
        expected_content_hash=expected_hash,
        updates=[UpdateRequest(new_state="done", task_line=4)],
    )
    assert isinstance(result, Ok)

    # Chain a second update using the returned hash — no STALE_FILE.
    result2 = update_task_status(
        spec,
        expected_content_hash=result.data.new_content_hash,
        updates=[UpdateRequest(new_state="done", task_line=5)],
    )
    assert isinstance(result2, Ok)
    content = spec.read_text()
    assert "[x] #1 First task" in content
    assert "[x] #2 Second task" in content


# ---------------------------------------------------------------------------
# Duplicate-line update behavior (last-write-wins)
# ---------------------------------------------------------------------------


def test_two_updates_targeting_same_task_last_one_wins(tmp_path: Path) -> None:
    """If a batch happens to contain two updates for the same task line,
    the second update overwrites the first in the in-memory ``new_lines``
    list — final disk state matches the last update. ``applied`` still
    records both. Not a recommended caller pattern but the semantics need
    to be defined."""
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[
            UpdateRequest(new_state="done", task_id="#1"),
            UpdateRequest(new_state="skipped", task_id="#1"),
        ],
    )

    assert isinstance(result, Ok)
    assert "[-] #1 First task" in spec.read_text()
    assert "[x] #1 First task" not in spec.read_text()
    assert len(result.data.applied) == 2


# ---------------------------------------------------------------------------
# No-op semantics: every update sets state to current state
# ---------------------------------------------------------------------------


def test_noop_update_still_succeeds_and_new_hash_matches(tmp_path: Path) -> None:
    """Setting a task to its current state writes the same bytes back.
    The new_content_hash equals the original hash; the diff is empty."""
    spec = _write_spec(tmp_path)
    expected = _current_hash(spec)

    result = update_task_status(
        spec,
        expected_content_hash=expected,
        updates=[UpdateRequest(new_state="open", task_id="#1")],
    )

    assert isinstance(result, Ok)
    assert result.data.new_content_hash == expected
    assert result.data.diff == ""


# ---------------------------------------------------------------------------
# Pydantic input validation on UpdateRequest itself
# ---------------------------------------------------------------------------


def test_update_request_rejects_unknown_state() -> None:
    """``new_state`` is a ``Literal``; Pydantic refuses values outside it."""
    with pytest.raises(ValidationError):
        UpdateRequest(new_state="frobnicated")  # type: ignore[arg-type]
