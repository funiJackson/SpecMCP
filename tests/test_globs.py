"""Tests for :mod:`specdd_mcp.operations.globs`."""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.globs import GlobExpansion, expand_pattern

# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_glob_expansion_dataclass_defaults() -> None:
    e = GlobExpansion(pattern="foo")
    assert e.pattern == "foo"
    assert e.matches == []


# ---------------------------------------------------------------------------
# Literal patterns (no glob chars)
# ---------------------------------------------------------------------------


def test_literal_pattern_matches_existing_file(tmp_path: Path) -> None:
    spec_dir = tmp_path / "src" / "billing"
    spec_dir.mkdir(parents=True)
    (spec_dir / "invoice.ts").write_text("// code\n")
    result = expand_pattern("invoice.ts", spec_dir, repo_root=tmp_path)
    assert result.pattern == "invoice.ts"
    assert result.matches == ["src/billing/invoice.ts"]


def test_literal_pattern_for_missing_file_returns_empty(tmp_path: Path) -> None:
    spec_dir = tmp_path / "src"
    spec_dir.mkdir()
    result = expand_pattern("ghost.ts", spec_dir, repo_root=tmp_path)
    assert result.matches == []


# ---------------------------------------------------------------------------
# Glob characters: *, **, ?
# ---------------------------------------------------------------------------


def test_star_matches_single_dir(tmp_path: Path) -> None:
    spec_dir = tmp_path / "src" / "billing"
    spec_dir.mkdir(parents=True)
    (spec_dir / "invoice.ts").write_text("")
    (spec_dir / "invoice.test.ts").write_text("")
    (spec_dir / "subdir").mkdir()
    (spec_dir / "subdir" / "deep.ts").write_text("")
    result = expand_pattern("*.ts", spec_dir, repo_root=tmp_path)
    assert set(result.matches) == {
        "src/billing/invoice.ts",
        "src/billing/invoice.test.ts",
    }


def test_double_star_recurses(tmp_path: Path) -> None:
    """``**/*.ts`` recurses through all subdirectories. In Python 3.13's
    pathlib, ``**`` matches zero or more directories, so top-level files
    are also included."""
    spec_dir = tmp_path / "src"
    spec_dir.mkdir()
    (spec_dir / "top.ts").write_text("")
    (spec_dir / "a").mkdir()
    (spec_dir / "a" / "mid.ts").write_text("")
    (spec_dir / "a" / "b").mkdir()
    (spec_dir / "a" / "b" / "deep.ts").write_text("")
    result = expand_pattern("**/*.ts", spec_dir, repo_root=tmp_path)
    assert set(result.matches) == {
        "src/top.ts",
        "src/a/mid.ts",
        "src/a/b/deep.ts",
    }


def test_question_mark_matches_single_char(tmp_path: Path) -> None:
    spec_dir = tmp_path / "src"
    spec_dir.mkdir()
    (spec_dir / "a.ts").write_text("")
    (spec_dir / "b.ts").write_text("")
    (spec_dir / "ab.ts").write_text("")
    result = expand_pattern("?.ts", spec_dir, repo_root=tmp_path)
    assert set(result.matches) == {"src/a.ts", "src/b.ts"}


# ---------------------------------------------------------------------------
# Directory + AppleDouble exclusions
# ---------------------------------------------------------------------------


def test_directories_are_not_matched(tmp_path: Path) -> None:
    """`*` matches both files and dirs in pathlib, but we filter to files."""
    spec_dir = tmp_path / "src"
    spec_dir.mkdir()
    (spec_dir / "subdir").mkdir()
    (spec_dir / "file.ts").write_text("")
    result = expand_pattern("*", spec_dir, repo_root=tmp_path)
    assert result.matches == ["src/file.ts"]


def test_appledouble_files_excluded(tmp_path: Path) -> None:
    spec_dir = tmp_path / "src"
    spec_dir.mkdir()
    (spec_dir / "real.ts").write_text("")
    (spec_dir / "._real.ts").write_bytes(b"\x00\x01binary")
    result = expand_pattern("*.ts", spec_dir, repo_root=tmp_path)
    assert result.matches == ["src/real.ts"]


def test_excluded_ancestor_dirs_blocked(tmp_path: Path) -> None:
    """``**/*.ts`` should NOT pick up files inside `.venv`, `node_modules`, etc."""
    repo = tmp_path
    (repo / ".venv").mkdir()
    (repo / ".venv" / "stranded.ts").write_text("")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.ts").write_text("")
    (repo / "src").mkdir()
    (repo / "src" / "real.ts").write_text("")
    result = expand_pattern("**/*.ts", repo, repo_root=repo)
    assert result.matches == ["src/real.ts"]


# ---------------------------------------------------------------------------
# Non-portable inputs
# ---------------------------------------------------------------------------


def test_absolute_pattern_returns_empty_matches(tmp_path: Path) -> None:
    """Absolute paths in Owns: are non-portable. validate_spec flags them on
    the source side; expansion just returns no matches."""
    (tmp_path / "real.ts").write_text("")
    result = expand_pattern("/abs/path/real.ts", tmp_path, repo_root=tmp_path)
    assert result.matches == []
    assert result.pattern == "/abs/path/real.ts"  # original preserved


def test_pattern_escaping_repo_root_silently_skipped(tmp_path: Path) -> None:
    """``../../foo.ts`` may match a real file outside the repo. Skip silently;
    validate_spec flags it on the source side."""
    outside = tmp_path / "outside.ts"
    outside.write_text("")
    repo = tmp_path / "inside"
    repo.mkdir()
    spec_dir = repo / "deep"
    spec_dir.mkdir()
    result = expand_pattern("../../outside.ts", spec_dir, repo_root=repo)
    assert result.matches == []


def test_pattern_with_parent_relative_still_under_repo_matches(
    tmp_path: Path,
) -> None:
    """``../models/*.ts`` from services/ where models/ is a sibling — allowed."""
    repo = tmp_path
    (repo / "src" / "services").mkdir(parents=True)
    (repo / "src" / "models").mkdir()
    (repo / "src" / "models" / "invoice.ts").write_text("")
    spec_dir = repo / "src" / "services"
    result = expand_pattern("../models/*.ts", spec_dir, repo_root=repo)
    assert result.matches == ["src/models/invoice.ts"]


def test_windows_style_backslashes_normalized(tmp_path: Path) -> None:
    """Defensive: a user writing ``Owns: src\\foo.ts`` shouldn't silently
    produce zero matches just because of separator style."""
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "src" / "foo.ts").write_text("")
    result = expand_pattern("src\\foo.ts", repo, repo_root=repo)
    assert result.matches == ["src/foo.ts"]


# ---------------------------------------------------------------------------
# Output stability
# ---------------------------------------------------------------------------


def test_output_sorted_and_deduplicated(tmp_path: Path) -> None:
    spec_dir = tmp_path
    (spec_dir / "z.ts").write_text("")
    (spec_dir / "a.ts").write_text("")
    (spec_dir / "m.ts").write_text("")
    result = expand_pattern("*.ts", spec_dir, repo_root=tmp_path)
    assert result.matches == ["a.ts", "m.ts", "z.ts"]


def test_empty_directory_returns_empty_matches(tmp_path: Path) -> None:
    result = expand_pattern("*.ts", tmp_path, repo_root=tmp_path)
    assert result.matches == []


# ---------------------------------------------------------------------------
# Snapshot semantics — documented behavior
# ---------------------------------------------------------------------------


def test_snapshot_semantics(tmp_path: Path) -> None:
    """The expansion captures filesystem state at call time. A new file
    added after the call doesn't appear in the prior result. Caller
    re-calls if it wants a fresh view."""
    (tmp_path / "first.ts").write_text("")
    snapshot_1 = expand_pattern("*.ts", tmp_path, repo_root=tmp_path)
    (tmp_path / "second.ts").write_text("")
    # snapshot_1 unchanged.
    assert snapshot_1.matches == ["first.ts"]
    snapshot_2 = expand_pattern("*.ts", tmp_path, repo_root=tmp_path)
    assert set(snapshot_2.matches) == {"first.ts", "second.ts"}
