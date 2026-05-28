"""Tests for :func:`specdd_mcp.operations.ownership.find_ownership_conflicts`.

Covers each conflict ``kind`` (literal / glob_overlap / glob_vs_literal),
the distinct-spec requirement, ``Owns:``-only scoping, per-spec-directory
literal resolution, ordering, provenance line numbers, and the scope /
guardrail error paths.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.ownership import find_ownership_conflicts
from specdd_mcp.types import Err, Ok


def _make_repo(tmp_path: Path) -> Path:
    """Mark ``tmp_path`` as a SpecDD repo."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specdd").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# No conflict
# ---------------------------------------------------------------------------


def test_empty_repo_returns_empty_list(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


def test_distinct_owners_no_overlap(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  a.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  b.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


def test_same_literal_in_one_spec_is_not_a_conflict(tmp_path: Path) -> None:
    """A single spec owning a path (even via two patterns) needs no second
    owner — that's a single-file concern, not an ownership conflict."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  shared.ts\n  shared.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


# ---------------------------------------------------------------------------
# kind: literal
# ---------------------------------------------------------------------------


def test_two_specs_same_literal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  shared.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  shared.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert len(result.data) == 1
    conflict = result.data[0]
    assert conflict.item == "shared.ts"
    assert conflict.kind == "literal"
    assert [o.spec for o in conflict.owners] == ["a.sdd", "b.sdd"]
    assert all(o.pattern == "shared.ts" for o in conflict.owners)


def test_literal_conflict_without_file_on_disk(tmp_path: Path) -> None:
    """An explicit literal claim stands whether or not the file exists yet."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  ghost.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  ghost.ts\n")
    assert not (repo / "ghost.ts").exists()
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert len(result.data) == 1
    assert result.data[0].kind == "literal"


def test_literals_resolve_relative_to_spec_directory(tmp_path: Path) -> None:
    """``Owns: x.ts`` in two different directories names two different paths,
    so it is not a conflict."""
    repo = _make_repo(tmp_path)
    (repo / "one").mkdir()
    (repo / "two").mkdir()
    (repo / "one" / "a.sdd").write_text("Spec: A\n\nOwns:\n  x.ts\n")
    (repo / "two" / "b.sdd").write_text("Spec: B\n\nOwns:\n  x.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


def test_owner_line_numbers_are_accurate(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  shared.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nPurpose:\n  p\n\nOwns:\n  shared.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    owners = {o.spec: o.line for o in result.data[0].owners}
    assert owners["a.sdd"] == 4  # line of "  shared.ts" in a.sdd
    assert owners["b.sdd"] == 7  # line of "  shared.ts" in b.sdd


# ---------------------------------------------------------------------------
# kind: glob_vs_literal
# ---------------------------------------------------------------------------


def test_glob_subsumes_literal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "billing").mkdir()
    (repo / "src" / "billing" / "invoice.ts").write_text("// invoice\n")
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  src/billing/*\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  src/billing/invoice.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert len(result.data) == 1
    conflict = result.data[0]
    assert conflict.item == "src/billing/invoice.ts"
    assert conflict.kind == "glob_vs_literal"
    patterns = {o.spec: o.pattern for o in conflict.owners}
    assert patterns == {"a.sdd": "src/billing/*", "b.sdd": "src/billing/invoice.ts"}


# ---------------------------------------------------------------------------
# kind: glob_overlap
# ---------------------------------------------------------------------------


def test_two_globs_overlap_on_a_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "invoice.ts").write_text("// invoice\n")
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  src/*.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  src/in*.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert len(result.data) == 1
    conflict = result.data[0]
    assert conflict.item == "src/invoice.ts"
    assert conflict.kind == "glob_overlap"


def test_non_overlapping_globs_no_conflict(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "invoice.ts").write_text("// invoice\n")
    (repo / "src" / "report.py").write_text("# report\n")
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  src/*.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  src/*.py\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


# ---------------------------------------------------------------------------
# Owns-only, ordering, scope, guardrail
# ---------------------------------------------------------------------------


def test_can_modify_is_ignored(tmp_path: Path) -> None:
    """Only ``Owns:`` is an ownership claim; ``Can modify:`` grants shared
    write access by design."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  shared.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nCan modify:\n  shared.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


def test_conflicts_sorted_by_item(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  z.ts\n  a.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  z.ts\n  a.ts\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert [c.item for c in result.data] == ["a.ts", "z.ts"]


def test_absolute_pattern_contributes_no_claim(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  /etc/passwd\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  /etc/passwd\n")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert result.data == []


def test_scope_narrows_walk(tmp_path: Path) -> None:
    """A conflict outside the scope is not reported."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  shared.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  shared.ts\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "c.sdd").write_text("Spec: C\n\nOwns:\n  only.ts\n")
    result = find_ownership_conflicts(repo, scope=repo / "sub")
    assert isinstance(result, Ok)
    assert result.data == []


def test_missing_repo_root_returns_not_found(tmp_path: Path) -> None:
    result = find_ownership_conflicts(tmp_path / "ghost")
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_scope_outside_repo_returns_out_of_scope(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    result = find_ownership_conflicts(repo, scope=outside)
    assert isinstance(result, Err)
    assert result.error == "OUT_OF_SCOPE"


def test_too_many_specs_returns_too_large(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    for i in range(15):
        (repo / f"s{i:02}.sdd").write_text(f"Spec: S{i}\n\nOwns:\n  x.ts\n")
    result = find_ownership_conflicts(repo, max_specs=10)
    assert isinstance(result, Err)
    assert result.error == "TOO_LARGE"


def test_malformed_spec_skipped_and_warned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nOwns:\n  shared.ts\n")
    (repo / "b.sdd").write_text("Spec: B\n\nOwns:\n  shared.ts\n")
    (repo / "bad.sdd").write_bytes(b"\x00\x01\x02 binary")
    result = find_ownership_conflicts(repo)
    assert isinstance(result, Ok)
    assert len(result.data) == 1  # a.sdd vs b.sdd still detected
    assert any("bad.sdd" in w for w in result.warnings)
