"""Tests for :func:`specdd_mcp.operations.add_task.add_task`.

Covers the three placement cases (append / after-anchor / empty section /
no section), id and text validation, the shared write-tool error envelope
(stale hash, missing file), and byte-faithful preservation (BOM, CRLF,
unrelated lines).
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.add_task import add_task
from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import Err, Ok


def _hash(path: Path) -> str:
    return content_hash(path.read_bytes())


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_append_after_last_task(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n  [x] #2 two\n")
    result = add_task(spec, text="three", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert result.data.task.text == "three"
    assert result.data.task.state == "open"
    assert result.data.task.line == 6
    assert spec.read_text().endswith("  [x] #2 two\n  [ ] three\n")


def test_insert_after_task_id(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] #1 one\n  [ ] #3 three\n")
    result = add_task(
        spec,
        text="two",
        task_id="#2",
        after_task_id="#1",
        expected_content_hash=_hash(spec),
    )
    assert isinstance(result, Ok)
    assert result.data.task.id == "#2"
    assert spec.read_text() == (
        "Spec: A\n\nTasks:\n  [ ] #1 one\n  [ ] #2 two\n  [ ] #3 three\n"
    )


def test_new_task_inherits_anchor_indent(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n    [ ] deep\n")
    result = add_task(spec, text="sibling", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert result.data.task.indent == "    "
    assert "    [ ] sibling\n" in spec.read_text()


def test_empty_tasks_section(tmp_path: Path) -> None:
    spec = tmp_path / "b.sdd"
    spec.write_text("Spec: B\n\nTasks:\n")
    result = add_task(spec, text="first", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert spec.read_text() == "Spec: B\n\nTasks:\n  [ ] first\n"


def test_no_tasks_section_creates_one(tmp_path: Path) -> None:
    spec = tmp_path / "c.sdd"
    spec.write_text("Spec: C\n\nPurpose:\n  hi\n")
    result = add_task(spec, text="do it", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert spec.read_text() == "Spec: C\n\nPurpose:\n  hi\n\nTasks:\n  [ ] do it\n"


def test_no_tasks_section_no_double_blank_line(tmp_path: Path) -> None:
    """A trailing blank line in the source is not doubled before Tasks:."""
    spec = tmp_path / "c.sdd"
    spec.write_text("Spec: C\n\nPurpose:\n  hi\n\n")
    result = add_task(spec, text="do it", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert spec.read_text() == "Spec: C\n\nPurpose:\n  hi\n\nTasks:\n  [ ] do it\n"


# ---------------------------------------------------------------------------
# Validation / errors
# ---------------------------------------------------------------------------


def test_empty_text_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    result = add_task(spec, text="   ", expected_content_hash=_hash(spec))
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_multiline_text_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    result = add_task(
        spec, text="line one\nline two", expected_content_hash=_hash(spec)
    )
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_text_is_stripped(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    result = add_task(spec, text="  padded  ", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert result.data.task.text == "padded"


def test_malformed_task_id_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    result = add_task(
        spec, text="x", task_id="abc", expected_content_hash=_hash(spec)
    )
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_duplicate_task_id_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] #1 one\n")
    result = add_task(
        spec, text="dup", task_id="#1", expected_content_hash=_hash(spec)
    )
    assert isinstance(result, Err)
    assert result.error == "ALREADY_EXISTS"


def test_unknown_after_task_id_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] #1 one\n")
    result = add_task(
        spec, text="x", after_task_id="#9", expected_content_hash=_hash(spec)
    )
    assert isinstance(result, Err)
    assert result.error == "TASK_NOT_FOUND"


def test_stale_hash_rejected_without_writing(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    before = spec.read_bytes()
    result = add_task(spec, text="x", expected_content_hash="deadbeef")
    assert isinstance(result, Err)
    assert result.error == "STALE_FILE"
    assert spec.read_bytes() == before  # untouched


def test_missing_file_returns_not_found(tmp_path: Path) -> None:
    result = add_task(
        tmp_path / "ghost.sdd", text="x", expected_content_hash="abc"
    )
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Byte-faithfulness + envelope
# ---------------------------------------------------------------------------


def test_unrelated_lines_preserved_byte_for_byte(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    original = "Spec: A\n\nPurpose:\n  keep me exactly\n\nTasks:\n  [x] #1 done\n"
    spec.write_text(original)
    result = add_task(spec, text="new", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    after = spec.read_text()
    # everything before the insertion is identical
    assert after.startswith(original)
    assert after == original + "  [ ] new\n"


def test_crlf_terminator_preserved(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_bytes(b"Spec: A\r\n\r\nTasks:\r\n  [ ] one\r\n")
    result = add_task(spec, text="two", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    assert spec.read_bytes() == b"Spec: A\r\n\r\nTasks:\r\n  [ ] one\r\n  [ ] two\r\n"


def test_bom_preserved(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_bytes("﻿Spec: A\n\nTasks:\n  [ ] one\n".encode())
    result = add_task(spec, text="two", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    raw = spec.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw == "﻿Spec: A\n\nTasks:\n  [ ] one\n  [ ] two\n".encode()


def test_returned_hash_matches_disk_and_chains(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    first = add_task(spec, text="two", expected_content_hash=_hash(spec))
    assert isinstance(first, Ok)
    assert first.data.new_content_hash == _hash(spec)
    # chain a second add using the returned hash, no re-read needed
    second = add_task(
        spec, text="three", expected_content_hash=first.data.new_content_hash
    )
    assert isinstance(second, Ok)
    assert [t.text for t in (parse_spec(path=str(spec)).data.tasks or [])] == [
        "one",
        "two",
        "three",
    ]


def test_diff_describes_only_the_insertion(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    result = add_task(spec, text="two", expected_content_hash=_hash(spec))
    assert isinstance(result, Ok)
    added = [
        ln
        for ln in result.data.diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    assert added == ["+  [ ] two"]
