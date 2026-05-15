"""Tests for :mod:`specdd_mcp.operations.walks`."""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.operations.walks import (
    DEFAULT_MAX_SPECS,
    EXCLUDED_DIR_NAMES,
    TooLargeError,
    WalkResult,
    walk_specs,
)

# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_empty_directory_returns_empty_walk_result(tmp_path: Path) -> None:
    result = walk_specs(tmp_path)
    assert result.paths == []
    assert result.warnings == []


def test_walk_result_is_a_dataclass_with_defaults() -> None:
    result = WalkResult()
    assert result.paths == []
    assert result.warnings == []


def test_single_sdd_file_at_root(tmp_path: Path) -> None:
    target = tmp_path / "app.sdd"
    target.write_text("Spec: A\n")
    result = walk_specs(tmp_path)
    assert result.paths == [target]


def test_nested_sdd_files_collected(tmp_path: Path) -> None:
    (tmp_path / "a.sdd").write_text("Spec: A\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.sdd").write_text("Spec: B\n")
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "c.sdd").write_text("Spec: C\n")
    result = walk_specs(tmp_path)
    assert {p.name for p in result.paths} == {"a.sdd", "b.sdd", "c.sdd"}


def test_output_is_sorted(tmp_path: Path) -> None:
    """Stable ordering across runs is important for deterministic test
    snapshots."""
    for name in ["z.sdd", "a.sdd", "m.sdd"]:
        (tmp_path / name).write_text("Spec: x\n")
    result = walk_specs(tmp_path)
    assert [p.name for p in result.paths] == ["a.sdd", "m.sdd", "z.sdd"]


# ---------------------------------------------------------------------------
# File-level filtering
# ---------------------------------------------------------------------------


def test_non_sdd_files_excluded(tmp_path: Path) -> None:
    (tmp_path / "spec.sdd").write_text("Spec: X\n")
    (tmp_path / "README.md").write_text("# readme\n")
    (tmp_path / "code.py").write_text("x = 1\n")
    result = walk_specs(tmp_path)
    assert [p.name for p in result.paths] == ["spec.sdd"]


def test_appledouble_metadata_excluded(tmp_path: Path) -> None:
    """``._foo.sdd`` macOS resource forks must not show up in scans."""
    (tmp_path / "real.sdd").write_text("Spec: X\n")
    (tmp_path / "._real.sdd").write_bytes(b"\x00\x05\x16binary-junk")
    result = walk_specs(tmp_path)
    assert [p.name for p in result.paths] == ["real.sdd"]


# ---------------------------------------------------------------------------
# Directory exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "excluded_dir",
    sorted(EXCLUDED_DIR_NAMES),
)
def test_excluded_dirs_are_not_descended_into(
    excluded_dir: str, tmp_path: Path
) -> None:
    (tmp_path / "visible.sdd").write_text("Spec: A\n")
    (tmp_path / excluded_dir).mkdir()
    (tmp_path / excluded_dir / "hidden.sdd").write_text("Spec: B\n")
    result = walk_specs(tmp_path)
    assert [p.name for p in result.paths] == ["visible.sdd"]


def test_specdd_marker_dir_skipped_even_with_inner_sdd(tmp_path: Path) -> None:
    """A stray .sdd file inside .specdd/ should not show up — that directory
    is reserved for SpecDD config, not spec content."""
    (tmp_path / ".specdd").mkdir()
    (tmp_path / ".specdd" / "stray.sdd").write_text("Spec: stray\n")
    (tmp_path / "real.sdd").write_text("Spec: real\n")
    result = walk_specs(tmp_path)
    assert [p.name for p in result.paths] == ["real.sdd"]


def test_other_hidden_dirs_not_auto_excluded(tmp_path: Path) -> None:
    """Only well-known noise dirs are excluded. A user's own ``.foo/`` dir
    is left alone — be permissive, since unusual project layouts exist."""
    (tmp_path / ".myproject").mkdir()
    (tmp_path / ".myproject" / "a.sdd").write_text("Spec: A\n")
    result = walk_specs(tmp_path)
    assert [p.name for p in result.paths] == ["a.sdd"]


# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------


def test_symlinked_subdir_not_followed_and_warned(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "deep.sdd").write_text("Spec: D\n")
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem doesn't support directory symlinks")

    (tmp_path / "top.sdd").write_text("Spec: T\n")
    result = walk_specs(tmp_path)
    # The .sdd inside the symlinked dir must not be double-counted.
    names = [p.name for p in result.paths]
    # Walk follows the real path normally; the symlinked one is reported but
    # not entered. So we see top.sdd and deep.sdd (via real/) but NOT the
    # same deep.sdd a second time via linked/.
    assert names.count("deep.sdd") == 1
    assert "top.sdd" in names
    assert any("symlink" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# max_specs guardrail
# ---------------------------------------------------------------------------


def test_default_max_specs_is_1000() -> None:
    """The constant is documented in DESIGN.md §3.8; lock it in via test."""
    assert DEFAULT_MAX_SPECS == 1000


def test_exactly_max_specs_does_not_trip(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"s{i}.sdd").write_text("Spec: x\n")
    result = walk_specs(tmp_path, max_specs=5)
    assert len(result.paths) == 5


def test_one_over_max_specs_raises_too_large(tmp_path: Path) -> None:
    for i in range(6):
        (tmp_path / f"s{i}.sdd").write_text("Spec: x\n")
    with pytest.raises(TooLargeError) as exc_info:
        walk_specs(tmp_path, max_specs=5)
    assert "5" in str(exc_info.value)


def test_too_large_short_circuits(tmp_path: Path) -> None:
    """The walker should bail as soon as the cap is exceeded, not enumerate
    the whole tree. We don't directly observe that here but the test is a
    contract anchor: if someone removes the early raise, ``max_specs`` would
    end up only enforcing a post-walk check."""
    for i in range(20):
        (tmp_path / f"s{i:03}.sdd").write_text("Spec: x\n")
    with pytest.raises(TooLargeError):
        walk_specs(tmp_path, max_specs=10)


# ---------------------------------------------------------------------------
# Defensive
# ---------------------------------------------------------------------------


def test_walk_specs_takes_a_directory_path(tmp_path: Path) -> None:
    """The directory argument must accept ``pathlib.Path`` (not just str).
    Regress if someone tightens the signature to ``str``."""
    (tmp_path / "x.sdd").write_text("Spec: x\n")
    result = walk_specs(tmp_path)
    assert result.paths
