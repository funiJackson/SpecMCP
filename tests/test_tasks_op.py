"""Tests for :func:`specdd_mcp.operations.tasks.list_tasks`.

Distinct from :mod:`tests.test_tasks` (which tests the parser-level task line
detector) — this file exercises the cross-spec aggregation operation.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.tasks import list_tasks
from specdd_mcp.types import Err, Ok


def _make_repo(tmp_path: Path) -> Path:
    """Mark ``tmp_path`` as a SpecDD repo.

    Creates the directory itself if needed so tests can pass nested paths
    under their own ``tmp_path``.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specdd").mkdir(exist_ok=True)
    return tmp_path


def _write_spec_with_tasks(path: Path, tasks: list[str]) -> None:
    """Helper: write a minimal spec with the given task lines."""
    body = "Spec: " + path.stem.title() + "\n\nTasks:\n"
    for task in tasks:
        body += f"  {task}\n"
    path.write_text(body)


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


def test_empty_repo_returns_empty_list(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = list_tasks(repo)
    assert isinstance(result, Ok)
    assert result.data == []
    assert result.warnings == []


def test_single_spec_three_tasks_returned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] one", "[ ] two", "[ ] three"],
    )
    result = list_tasks(repo)
    assert isinstance(result, Ok)
    assert [t.text for t in result.data] == ["one", "two", "three"]
    assert all(t.source == "a.sdd" for t in result.data)


def test_output_sorted_by_source_then_line(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    _write_spec_with_tasks(repo / "z.sdd", ["[ ] first in z"])
    _write_spec_with_tasks(repo / "a.sdd", ["[ ] first in a", "[ ] second in a"])
    _write_spec_with_tasks(repo / "src" / "m.sdd", ["[ ] in m"])
    result = list_tasks(repo)
    assert isinstance(result, Ok)
    sources = [t.source for t in result.data]
    # 'a.sdd' < 'src/m.sdd' < 'z.sdd' lexicographically
    assert sources == ["a.sdd", "a.sdd", "src/m.sdd", "z.sdd"]
    assert [t.text for t in result.data] == [
        "first in a",
        "second in a",
        "in m",
        "first in z",
    ]


def test_task_with_source_carries_all_parser_fields(tmp_path: Path) -> None:
    """The ParsedTask fields (indent, raw, id) must survive the operation."""
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(repo / "a.sdd", ["[x] #42 done with id"])
    result = list_tasks(repo, states=["done"])
    assert isinstance(result, Ok)
    task = result.data[0]
    assert task.state == "done"
    assert task.state_symbol == "x"
    assert task.id == "#42"
    assert task.text == "done with id"
    assert task.raw == "  [x] #42 done with id"
    assert task.indent == "  "
    assert task.source == "a.sdd"


# ---------------------------------------------------------------------------
# State filtering
# ---------------------------------------------------------------------------


def test_default_states_is_open(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] open", "[x] done", "[!] blocked", "[?] decide", "[-] skip"],
    )
    result = list_tasks(repo)  # no states arg
    assert isinstance(result, Ok)
    assert [t.state for t in result.data] == ["open"]


def test_explicit_states_filter(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] o1", "[x] d1", "[ ] o2", "[!] b1"],
    )
    result = list_tasks(repo, states=["open", "done"])
    assert isinstance(result, Ok)
    assert {t.state for t in result.data} == {"open", "done"}


def test_include_blocked_adds_blocked_and_needs_decision(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] o1", "[x] d1", "[!] b1", "[?] q1", "[-] s1"],
    )
    result = list_tasks(repo, include_blocked=True)
    assert isinstance(result, Ok)
    # default states=["open"] PLUS blocked + needs_decision
    states = {t.state for t in result.data}
    assert states == {"open", "blocked", "needs_decision"}


def test_explicit_states_with_include_blocked_union(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] o1", "[x] d1", "[!] b1", "[?] q1"],
    )
    result = list_tasks(
        repo, states=["done"], include_blocked=True
    )
    assert isinstance(result, Ok)
    assert {t.state for t in result.data} == {"done", "blocked", "needs_decision"}


def test_empty_states_returns_no_tasks(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(repo / "a.sdd", ["[ ] one", "[x] two"])
    result = list_tasks(repo, states=[])
    assert isinstance(result, Ok)
    assert result.data == []


# ---------------------------------------------------------------------------
# Text and ID filtering
# ---------------------------------------------------------------------------


def test_text_contains_is_case_insensitive(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] Add validation", "[ ] Remove deprecated API", "[ ] add tests"],
    )
    result = list_tasks(repo, text_contains="ADD")
    assert isinstance(result, Ok)
    assert [t.text for t in result.data] == ["Add validation", "add tests"]


def test_task_id_exact_match(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] #1 one", "[ ] #10 ten", "[ ] #100 hundred"],
    )
    result = list_tasks(repo, task_id="#10")
    assert isinstance(result, Ok)
    assert [t.id for t in result.data] == ["#10"]


def test_task_id_no_match_returns_empty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(repo / "a.sdd", ["[ ] #1 one"])
    result = list_tasks(repo, task_id="#999")
    assert isinstance(result, Ok)
    assert result.data == []


def test_text_contains_and_states_compose(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(
        repo / "a.sdd",
        ["[ ] add validation", "[x] add tests", "[ ] remove old code"],
    )
    result = list_tasks(repo, states=["open"], text_contains="add")
    assert isinstance(result, Ok)
    assert [t.text for t in result.data] == ["add validation"]


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_scope_limits_walk(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    _write_spec_with_tasks(repo / "top.sdd", ["[ ] top task"])
    _write_spec_with_tasks(repo / "src" / "sub.sdd", ["[ ] sub task"])
    result = list_tasks(repo, scope=repo / "src")
    assert isinstance(result, Ok)
    assert [t.text for t in result.data] == ["sub task"]


def test_scope_outside_repo_returns_out_of_scope(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    result = list_tasks(repo, scope=outside)
    assert isinstance(result, Err)
    assert result.error == "OUT_OF_SCOPE"


def test_scope_does_not_exist_returns_not_found(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = list_tasks(repo, scope=repo / "ghost")
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_scope_file_walks_its_parent(tmp_path: Path) -> None:
    """When scope points at a file, walk the file's parent directory."""
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(repo / "a.sdd", ["[ ] in a"])
    _write_spec_with_tasks(repo / "b.sdd", ["[ ] in b"])
    result = list_tasks(repo, scope=repo / "a.sdd")
    assert isinstance(result, Ok)
    # Both a.sdd and b.sdd are in the same directory.
    assert {t.source for t in result.data} == {"a.sdd", "b.sdd"}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_repo_root_returns_not_found(tmp_path: Path) -> None:
    ghost = tmp_path / "ghost"
    result = list_tasks(ghost)
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_too_large_walk_returns_too_large(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    for i in range(15):
        _write_spec_with_tasks(repo / f"s{i:02}.sdd", ["[ ] one"])
    result = list_tasks(repo, max_specs=10)
    assert isinstance(result, Err)
    assert result.error == "TOO_LARGE"
    assert result.details["max_specs"] == 10


def test_malformed_spec_warns_and_continues(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write_spec_with_tasks(repo / "ok.sdd", ["[ ] real task"])
    # Binary content (PNG-ish header with NULs) → PARSE_ERROR kind=binary
    (repo / "bad.sdd").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    result = list_tasks(repo)
    assert isinstance(result, Ok)
    # ok.sdd's tasks present.
    assert [t.text for t in result.data] == ["real task"]
    # bad.sdd surfaced as a warning.
    assert any("bad.sdd" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Spec without tasks
# ---------------------------------------------------------------------------


def test_spec_without_tasks_section_is_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "no_tasks.sdd").write_text("Spec: NoTasks\n\nPurpose:\n  No tasks.\n")
    _write_spec_with_tasks(repo / "with_tasks.sdd", ["[ ] real task"])
    result = list_tasks(repo)
    assert isinstance(result, Ok)
    assert {t.source for t in result.data} == {"with_tasks.sdd"}
