"""Tests for :mod:`specdd_mcp.operations.locks`.

Cross-process exclusivity (the real point of the lock) is tested via
subprocess harness in PR 4 C9. This file covers the in-process happy
paths: basic acquire/release, sidecar creation, exception cleanup,
re-acquisition, and multi-path independence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.operations.locks import file_lock

# ---------------------------------------------------------------------------
# Basic happy path
# ---------------------------------------------------------------------------


def test_basic_acquire_release_no_error(tmp_path: Path) -> None:
    target = tmp_path / "spec.sdd"
    with file_lock(target):
        pass  # acquire + release succeed


def test_sidecar_lock_file_created_in_target_directory(tmp_path: Path) -> None:
    target = tmp_path / "spec.sdd"
    target.write_text("Spec: X\n")
    assert not (tmp_path / "spec.sdd.lock").exists()
    with file_lock(target):
        assert (tmp_path / "spec.sdd.lock").exists()


def test_lock_path_naming_includes_full_target_basename(tmp_path: Path) -> None:
    """Sidecar appends ``.lock`` to the full basename, so two specs in the
    same dir (``a.sdd`` + ``b.sdd``) get distinct lock files."""
    a = tmp_path / "a.sdd"
    b = tmp_path / "b.sdd"
    a.write_text("")
    b.write_text("")
    with file_lock(a), file_lock(b):
        pass
    assert (tmp_path / "a.sdd.lock").exists()
    assert (tmp_path / "b.sdd.lock").exists()


def test_lock_creates_parent_directory_if_needed(tmp_path: Path) -> None:
    """``file_lock`` is sometimes called with a target whose parent dir
    doesn't yet exist (e.g. when initializing a new spec). The lock file's
    directory gets created on demand."""
    target = tmp_path / "deep" / "nested" / "spec.sdd"
    with file_lock(target):
        assert (tmp_path / "deep" / "nested" / "spec.sdd.lock").exists()


# ---------------------------------------------------------------------------
# Re-acquisition after release
# ---------------------------------------------------------------------------


def test_sequential_acquire_release_loop(tmp_path: Path) -> None:
    """Sequential lock/release/lock/release works repeatedly. The sidecar
    file is reusable across cycles."""
    target = tmp_path / "spec.sdd"
    for _ in range(5):
        with file_lock(target):
            pass


def test_locks_on_different_paths_are_independent(tmp_path: Path) -> None:
    """Holding the lock on path A doesn't prevent acquiring the lock on
    path B in the same process."""
    a = tmp_path / "a.sdd"
    b = tmp_path / "b.sdd"
    with file_lock(a), file_lock(b):
        pass


# ---------------------------------------------------------------------------
# Exception cleanup
# ---------------------------------------------------------------------------


def test_exception_inside_with_releases_lock(tmp_path: Path) -> None:
    """An exception raised inside the with-block releases the lock, so the
    next acquire doesn't hang."""
    target = tmp_path / "spec.sdd"

    with pytest.raises(ValueError, match="simulated"), file_lock(target):
        raise ValueError("simulated failure")

    # If the lock leaked, this would deadlock. We're testing that it
    # acquires immediately (no hang).
    with file_lock(target):
        pass


def test_nested_exception_in_different_paths_unwinds_correctly(
    tmp_path: Path,
) -> None:
    """Exception inside the inner with releases inner lock, propagates up,
    outer's __exit__ then releases outer lock."""
    a = tmp_path / "a.sdd"
    b = tmp_path / "b.sdd"

    with pytest.raises(RuntimeError), file_lock(a), file_lock(b):
        raise RuntimeError("inner")

    # Both must be re-acquirable.
    with file_lock(a), file_lock(b):
        pass


# ---------------------------------------------------------------------------
# Sidecar file persists (by design)
# ---------------------------------------------------------------------------


def test_sidecar_lock_file_persists_after_release(tmp_path: Path) -> None:
    """Lock files intentionally aren't deleted on release — see module
    docstring for the TOCTOU rationale. Tests we don't delete them."""
    target = tmp_path / "spec.sdd"
    with file_lock(target):
        pass
    assert (tmp_path / "spec.sdd.lock").exists()
