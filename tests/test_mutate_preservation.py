"""Byte-faithful preservation tests for ``update_task_status``.

DESIGN.md §5.5 commits: writing to a ``.sdd`` spec changes **only** the
state-symbol bytes for the targeted tasks; every other byte — line
endings, BOM, indentation, multi-byte characters, comments, even
deliberately weird content like ``[note]`` text inside a task's body —
is preserved exactly.

This file pins that contract down with a fixture-driven matrix. Each
fixture is a hand-built byte sequence covering one preservation
hazard, and the parametrized tests below verify:

  1. **Happy-path byte diff is 1 byte.** Run ``update_task_status`` on
     the fixture, read it back, and assert exactly one byte differs from
     the original (the state symbol). Side effects on terminators, BOM,
     or non-ASCII bytes would surface as a >1-byte diff.
  2. **Round-trip is bit-exact.** Flip the task to a new state, then
     flip it back to the original state; the bytes on disk are
     identical to the source. Catches any subtle re-encoding bug.
  3. **No-op write is a no-op.** Setting a task to its current state
     produces zero bytes of change (same hash; empty diff).

Fixtures are inline byte literals rather than separate ``.sdd`` files
to bypass git autocrlf / editor line-ending normalization, which would
otherwise silently rewrite CRLF fixtures to LF in transit.

Concurrent-process safety lives in ``test_mutate_concurrency.py`` (C9).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.mutate_tasks import update_task_status
from specdd_mcp.types import Ok, TaskState, UpdateRequest

# ---------------------------------------------------------------------------
# Fixture descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreservationFixture:
    """One preservation hazard, ready to be written to ``tmp_path`` verbatim.

    Attributes:
        name: Short label — used as the parametrize id.
        raw: The exact bytes to write to disk (no trailing transformations).
        task_line: 1-indexed line number of the task to update.
        new_state: The state to set on that task.
        expected_diff_byte_index: Byte offset (into ``raw``) where the
            state symbol lives. After the update, exactly this one byte
            (and no others) must differ.
        expected_new_byte: The state-symbol byte that should appear at
            ``expected_diff_byte_index`` after the update.
    """

    name: str
    raw: bytes
    task_line: int
    new_state: TaskState
    expected_diff_byte_index: int
    expected_new_byte: bytes


def _idx_after(haystack: bytes, marker: bytes) -> int:
    """Byte offset of the first ``marker`` occurrence — fails loudly if
    the marker isn't in the fixture (catches typos in fixture bytes)."""
    idx = haystack.find(marker)
    if idx < 0:
        raise AssertionError(f"marker {marker!r} not in fixture")
    return idx


# ---------------------------------------------------------------------------
# The nine fixtures
# ---------------------------------------------------------------------------


def _build_fixtures() -> list[PreservationFixture]:
    """Construct every preservation fixture with its expected diff index.

    The indices are computed from ``bytes.find`` rather than hand-counted —
    less error-prone when editing the fixture text.
    """
    fixtures: list[PreservationFixture] = []

    # ---- 1. LF line endings (Unix baseline) ----
    lf = b"Spec: LF\n\nTasks:\n  [ ] #1 Hello\n  [ ] #2 World\n"
    fixtures.append(
        PreservationFixture(
            name="lf_endings",
            raw=lf,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(lf, b"[ ] #1") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 2. CRLF line endings (Windows) ----
    crlf = b"Spec: CRLF\r\n\r\nTasks:\r\n  [ ] #1 Hello\r\n  [ ] #2 World\r\n"
    fixtures.append(
        PreservationFixture(
            name="crlf_endings",
            raw=crlf,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(crlf, b"[ ] #1") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 3. UTF-8 BOM at file start ----
    bom = (
        b"\xef\xbb\xbf"
        b"Spec: BOM\n\nTasks:\n  [ ] #1 Hello\n"
    )
    fixtures.append(
        PreservationFixture(
            name="utf8_bom",
            raw=bom,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(bom, b"[ ] #1") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 4. Tab indentation ----
    tabs = b"Spec: Tabs\n\nTasks:\n\t[ ] #1 Hello\n\t[ ] #2 World\n"
    fixtures.append(
        PreservationFixture(
            name="tab_indent",
            raw=tabs,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(tabs, b"[ ] #1") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 5. Deep indentation (8 spaces) ----
    deep = (
        b"Spec: Deep\n\nTasks:\n"
        b"        [ ] #1 Hello\n"
    )
    fixtures.append(
        PreservationFixture(
            name="deep_indent_8_spaces",
            raw=deep,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(deep, b"[ ] #1") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 6. Multi-byte UTF-8 in task text (CJK + emoji + accent) ----
    mb = (
        b"Spec: MultiByte\n\nTasks:\n"
        b"  [ ] #1 \xe4\xb8\xad\xe6\x96\x87 t\xc3\xa9st "
        b"\xf0\x9f\x9a\x80\n"
        b"  [ ] #2 Plain\n"
    )
    fixtures.append(
        PreservationFixture(
            name="multibyte_utf8",
            raw=mb,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(mb, b"[ ] #1") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 7. Many task IDs (sparse) ----
    ids = (
        b"Spec: Ids\n\nTasks:\n"
        b"  [ ] #1 first\n"
        b"  [ ] #42 forty-two\n"
        b"  [ ] #1024 ten-twenty-four\n"
    )
    # Target the middle task (#42). Its state symbol comes after the
    # second `[ ]` marker; ``find`` finds the first, so locate by id.
    line_42_offset = ids.find(b"#42")
    state_offset = ids.rfind(b"[ ]", 0, line_42_offset) + 1
    fixtures.append(
        PreservationFixture(
            name="three_digit_id",
            raw=ids,
            task_line=5,
            new_state="done",
            expected_diff_byte_index=state_offset,
            expected_new_byte=b"x",
        )
    )

    # ---- 8. Adjacent brackets inside task text ----
    # Task text contains a literal ``[note]`` — must not be mistaken for
    # the state bracket. Only the first ``[ ]`` (the actual state marker)
    # should be touched.
    adj = (
        b"Spec: Adjacent\n\nTasks:\n"
        b"  [ ] #1 [note] body with brackets\n"
    )
    fixtures.append(
        PreservationFixture(
            name="adjacent_brackets_in_text",
            raw=adj,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(adj, b"[ ]") + 1,
            expected_new_byte=b"x",
        )
    )

    # ---- 9. Task line followed by continuation/indented lines ----
    # An indented body line that follows a task. The body line happens
    # to start with ``    `` and shouldn't be touched. Only the task's
    # own state symbol changes.
    cont = (
        b"Spec: Continuation\n\nTasks:\n"
        b"  [ ] #1 First task with a long description\n"
        b"      continuation line that is not itself a task\n"
        b"  [ ] #2 Second\n"
    )
    fixtures.append(
        PreservationFixture(
            name="task_with_continuation_line",
            raw=cont,
            task_line=4,
            new_state="done",
            expected_diff_byte_index=_idx_after(cont, b"[ ]") + 1,
            expected_new_byte=b"x",
        )
    )

    return fixtures


_FIXTURES = _build_fixtures()


def _write_fixture(tmp_path: Path, fixture: PreservationFixture) -> Path:
    spec = tmp_path / "spec.sdd"
    spec.write_bytes(fixture.raw)
    return spec


# ---------------------------------------------------------------------------
# 1. Exactly one byte changes after a happy-path update
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda f: f.name)
def test_update_changes_exactly_one_byte(
    tmp_path: Path, fixture: PreservationFixture
) -> None:
    """Run one update against the fixture; assert one byte differs."""
    spec = _write_fixture(tmp_path, fixture)
    before = spec.read_bytes()
    expected_hash = content_hash(before)

    result = update_task_status(
        spec,
        expected_content_hash=expected_hash,
        updates=[
            UpdateRequest(
                new_state=fixture.new_state, task_line=fixture.task_line
            )
        ],
    )

    assert isinstance(result, Ok), (
        f"{fixture.name}: expected Ok, got {result}"
    )

    after = spec.read_bytes()
    assert len(after) == len(before), (
        f"{fixture.name}: file length changed by "
        f"{len(after) - len(before)} bytes (must be 0)"
    )
    diffs = [
        i
        for i, (a, b) in enumerate(zip(before, after, strict=True))
        if a != b
    ]
    assert diffs == [fixture.expected_diff_byte_index], (
        f"{fixture.name}: expected diff at exactly "
        f"[{fixture.expected_diff_byte_index}], got {diffs}"
    )
    assert (
        after[
            fixture.expected_diff_byte_index : fixture.expected_diff_byte_index
            + 1
        ]
        == fixture.expected_new_byte
    )


# ---------------------------------------------------------------------------
# 2. Round-trip: flip state then flip back → bytes identical to source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda f: f.name)
def test_round_trip_to_done_and_back_is_byte_identical(
    tmp_path: Path, fixture: PreservationFixture
) -> None:
    """Flip ``open → done``, then flip ``done → open``. The final bytes on
    disk must equal the source — proves no encoding/decoding accumulation."""
    spec = _write_fixture(tmp_path, fixture)
    original = spec.read_bytes()
    h0 = content_hash(original)

    # Forward
    forward = update_task_status(
        spec,
        expected_content_hash=h0,
        updates=[
            UpdateRequest(new_state="done", task_line=fixture.task_line)
        ],
    )
    assert isinstance(forward, Ok)

    # Backward
    backward = update_task_status(
        spec,
        expected_content_hash=forward.data.new_content_hash,
        updates=[
            UpdateRequest(new_state="open", task_line=fixture.task_line)
        ],
    )
    assert isinstance(backward, Ok)

    assert spec.read_bytes() == original, (
        f"{fixture.name}: round-trip lost byte fidelity"
    )
    assert backward.data.new_content_hash == h0


# ---------------------------------------------------------------------------
# 3. No-op write produces zero changes (same hash, empty diff)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda f: f.name)
def test_setting_task_to_its_current_state_is_a_byte_noop(
    tmp_path: Path, fixture: PreservationFixture
) -> None:
    """Setting a task to its already-current state still re-encodes and
    rewrites the file — but the resulting bytes must equal the source,
    and the returned diff must be empty."""
    spec = _write_fixture(tmp_path, fixture)
    original = spec.read_bytes()
    h0 = content_hash(original)

    result = update_task_status(
        spec,
        expected_content_hash=h0,
        updates=[
            UpdateRequest(new_state="open", task_line=fixture.task_line)
        ],
    )

    assert isinstance(result, Ok)
    assert spec.read_bytes() == original, (
        f"{fixture.name}: no-op write changed the file"
    )
    assert result.data.new_content_hash == h0
    assert result.data.diff == ""


# ---------------------------------------------------------------------------
# 4. BOM preservation — explicit test (covered by fixture 3, but worth
# making the invariant unmistakable for future maintainers)
# ---------------------------------------------------------------------------


def test_bom_is_preserved_byte_for_byte_after_update(tmp_path: Path) -> None:
    """The 3-byte UTF-8 BOM at file start must survive the
    read → mutate → write cycle untouched."""
    bom = b"\xef\xbb\xbf"
    raw = bom + b"Spec: BOM\n\nTasks:\n  [ ] #1 Hello\n"
    spec = tmp_path / "spec.sdd"
    spec.write_bytes(raw)

    result = update_task_status(
        spec,
        expected_content_hash=content_hash(raw),
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Ok)
    after = spec.read_bytes()
    assert after.startswith(bom), "BOM was stripped during update"


# ---------------------------------------------------------------------------
# 5. Line-ending preservation across a batch
# ---------------------------------------------------------------------------


def test_crlf_endings_preserved_across_multi_update_batch(
    tmp_path: Path,
) -> None:
    """A batch update on a CRLF file must keep CRLF on every line —
    including the ones being modified."""
    raw = (
        b"Spec: CRLF\r\n\r\nTasks:\r\n"
        b"  [ ] #1 Hello\r\n"
        b"  [ ] #2 World\r\n"
    )
    spec = tmp_path / "spec.sdd"
    spec.write_bytes(raw)

    result = update_task_status(
        spec,
        expected_content_hash=content_hash(raw),
        updates=[
            UpdateRequest(new_state="done", task_id="#1"),
            UpdateRequest(new_state="skipped", task_id="#2"),
        ],
    )

    assert isinstance(result, Ok)
    after = spec.read_bytes()
    # The two state bytes flipped, every CRLF survived.
    assert after.count(b"\r\n") == raw.count(b"\r\n")
    # Defensive: no bare LF crept in.
    assert b"\n\n" not in after.replace(b"\r\n", b"")
    assert b"[x] #1 Hello\r\n" in after
    assert b"[-] #2 World\r\n" in after
