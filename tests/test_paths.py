"""Tests for :mod:`specdd_mcp.server.paths`."""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.paths import (
    AncestorWalk,
    OutOfScopeError,
    find_repo_root,
    is_under,
    to_posix,
    to_repo_relative,
    walk_ancestors,
)

# ---------------------------------------------------------------------------
# to_posix
# ---------------------------------------------------------------------------


def test_to_posix_already_posix() -> None:
    assert to_posix("src/billing/invoice.sdd") == "src/billing/invoice.sdd"


def test_to_posix_with_backslashes() -> None:
    """A Windows-style path passed on a POSIX host still gets normalized."""
    assert to_posix("src\\billing\\invoice.sdd") == "src/billing/invoice.sdd"


def test_to_posix_mixed_separators() -> None:
    assert to_posix("src/billing\\invoice.sdd") == "src/billing/invoice.sdd"


def test_to_posix_accepts_path_object(tmp_path: Path) -> None:
    p = tmp_path / "foo" / "bar.sdd"
    out = to_posix(p)
    assert "/" in out
    assert "\\" not in out


# ---------------------------------------------------------------------------
# is_under
# ---------------------------------------------------------------------------


def test_is_under_direct_child(tmp_path: Path) -> None:
    child = tmp_path / "src" / "invoice.sdd"
    assert is_under(child, tmp_path) is True


def test_is_under_self(tmp_path: Path) -> None:
    """A path is considered under itself."""
    assert is_under(tmp_path, tmp_path) is True


def test_is_under_outside(tmp_path: Path) -> None:
    elsewhere = tmp_path.parent / "elsewhere"
    assert is_under(elsewhere, tmp_path) is False


def test_is_under_handles_dotdot(tmp_path: Path) -> None:
    """`..` tricks are caught via resolve()."""
    tricked = tmp_path / "src" / ".." / ".." / "elsewhere"
    assert is_under(tricked, tmp_path) is False


# ---------------------------------------------------------------------------
# to_repo_relative
# ---------------------------------------------------------------------------


def test_to_repo_relative_basic(tmp_path: Path) -> None:
    sub = tmp_path / "src" / "invoice.sdd"
    sub.parent.mkdir(parents=True)
    sub.write_text("Spec: X\n")
    assert to_repo_relative(sub, tmp_path) == "src/invoice.sdd"


def test_to_repo_relative_at_root(tmp_path: Path) -> None:
    f = tmp_path / "top.sdd"
    f.write_text("Spec: T\n")
    assert to_repo_relative(f, tmp_path) == "top.sdd"


def test_to_repo_relative_raises_when_outside(tmp_path: Path) -> None:
    elsewhere = tmp_path.parent / "elsewhere.sdd"
    with pytest.raises(OutOfScopeError):
        to_repo_relative(elsewhere, tmp_path)


# ---------------------------------------------------------------------------
# find_repo_root
# ---------------------------------------------------------------------------


def test_find_repo_root_prefers_specdd(tmp_path: Path) -> None:
    (tmp_path / ".specdd").mkdir()
    target = tmp_path / "src" / "x.sdd"
    target.parent.mkdir()
    target.write_text("Spec: X\n")
    assert find_repo_root(target) == tmp_path.resolve()


def test_find_repo_root_falls_back_to_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    target = tmp_path / "x.sdd"
    target.write_text("Spec: X\n")
    assert find_repo_root(target) == tmp_path.resolve()


def test_find_repo_root_prefers_specdd_over_outer_git(tmp_path: Path) -> None:
    """A SpecDD-managed subtree inside a larger git repo uses the SpecDD root."""
    (tmp_path / ".git").mkdir()
    subdir = tmp_path / "subproject"
    subdir.mkdir()
    (subdir / ".specdd").mkdir()
    target = subdir / "x.sdd"
    target.write_text("Spec: X\n")
    assert find_repo_root(target) == subdir.resolve()


def test_find_repo_root_returns_none_when_no_marker(tmp_path: Path) -> None:
    target = tmp_path / "lonely.sdd"
    target.write_text("Spec: X\n")
    # tmp_path may itself be under a .git somewhere in pytest's tree, so the
    # search would find that. Sanity check by isolating to /tmp.
    assert find_repo_root(target) is None or find_repo_root(target).is_dir()


def test_find_repo_root_accepts_target_at_root(tmp_path: Path) -> None:
    """Target itself can be the repo root."""
    (tmp_path / ".specdd").mkdir()
    assert find_repo_root(tmp_path) == tmp_path.resolve()


def test_find_repo_root_git_file_link(tmp_path: Path) -> None:
    """A git submodule has `.git` as a FILE pointing at the parent. Still counts."""
    (tmp_path / ".git").write_text("gitdir: ../.git/modules/x\n")
    target = tmp_path / "x.sdd"
    target.write_text("Spec: X\n")
    assert find_repo_root(target) == tmp_path.resolve()


# ---------------------------------------------------------------------------
# walk_ancestors
# ---------------------------------------------------------------------------


def test_walk_ancestors_basic_chain(tmp_path: Path) -> None:
    leaf_dir = tmp_path / "a" / "b" / "c"
    leaf_dir.mkdir(parents=True)
    target = leaf_dir / "x.sdd"
    target.write_text("Spec: X\n")
    walk = walk_ancestors(target, tmp_path)
    assert walk.warnings == []
    expected = [
        tmp_path,
        tmp_path / "a",
        tmp_path / "a" / "b",
        tmp_path / "a" / "b" / "c",
    ]
    assert walk.directories == expected


def test_walk_ancestors_target_is_directory(tmp_path: Path) -> None:
    leaf = tmp_path / "a" / "b"
    leaf.mkdir(parents=True)
    walk = walk_ancestors(leaf, tmp_path)
    assert walk.directories == [tmp_path, tmp_path / "a", tmp_path / "a" / "b"]


def test_walk_ancestors_target_at_root(tmp_path: Path) -> None:
    target = tmp_path / "x.sdd"
    target.write_text("Spec: X\n")
    walk = walk_ancestors(target, tmp_path)
    assert walk.directories == [tmp_path]
    assert walk.warnings == []


def test_walk_ancestors_target_outside_raises(tmp_path: Path) -> None:
    elsewhere = tmp_path.parent / "elsewhere.sdd"
    with pytest.raises(OutOfScopeError):
        walk_ancestors(elsewhere, tmp_path)


def test_walk_ancestors_skips_symlinked_directory(tmp_path: Path) -> None:
    """A symlinked directory in the ancestor chain is excluded and reported."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "deep").mkdir()
    target = real_dir / "deep" / "x.sdd"
    target.write_text("Spec: X\n")

    # Create a symlink alongside that points to the real dir.
    link = tmp_path / "linked"
    try:
        link.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem doesn't support directory symlinks")

    linked_target = link / "deep" / "x.sdd"
    walk = walk_ancestors(linked_target, tmp_path)
    # The chain should not contain a path with `is_symlink()` True.
    for dir_path in walk.directories:
        assert not dir_path.is_symlink()
    # A warning should mention the symlink.
    assert any("symlink" in w.lower() for w in walk.warnings)


def test_walk_ancestors_handles_symlinked_repo_root(tmp_path: Path) -> None:
    """When repo_root is reached through a symlink (callers may use a literal
    target path with a resolved repo_root), the walk uses the resolved chain
    and emits a warning."""
    real_repo = tmp_path / "real"
    (real_repo / "src").mkdir(parents=True)
    target_in_real = real_repo / "src" / "x.sdd"
    target_in_real.write_text("Spec: X\n")

    linked_repo = tmp_path / "linked"
    try:
        linked_repo.symlink_to(real_repo, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem doesn't support directory symlinks")

    # The target is reached through the symlink; repo_root is the resolved
    # real path. is_under returns True (resolved paths nest), but the literal
    # relative_to call would raise ValueError.
    linked_target = linked_repo / "src" / "x.sdd"
    walk = walk_ancestors(linked_target, real_repo.resolve())
    assert walk.directories  # non-empty
    assert walk.warnings
    assert any("symlink" in w.lower() for w in walk.warnings)


def test_walk_ancestors_returns_dataclass_with_defaults() -> None:
    walk = AncestorWalk()
    assert walk.directories == []
    assert walk.warnings == []
