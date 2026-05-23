"""Tests for ``check_modification_scope`` (the pre-edit gate, DESIGN §5.6).

Two layers:

  * Scenario tests against committed fixtures under ``tests/fixtures/scope/``
    — one directory per documented scenario in the PR 5 plan. These lock in
    the *behavior* a ``/specc`` run depends on.
  * Inline unit tests for the matching internals (new-file pattern match,
    out-of-repo normalization, error propagation) that would be awkward to
    express as full fixtures.

The fixtures double as documentation: ``find tests/fixtures/scope -name
'*.sdd'`` shows what each ownership shape looks like.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.operations.scope import (
    _glob_to_regex,
    _normalize_proposed,
    _pattern_matches_path,
    check_modification_scope,
)
from specdd_mcp.types import Err, Ok, ScopeReport
from tests.conftest import SCOPE_FIXTURES_DIR


def _run(
    fixture: str,
    target: str,
    proposed: list[str],
) -> ScopeReport:
    """Resolve a scope check against a committed fixture and unwrap the Ok."""
    root = SCOPE_FIXTURES_DIR / fixture
    result = check_modification_scope(
        target=str(root / target),
        proposed_files=proposed,
        repo_root=str(root),
    )
    assert isinstance(result, Ok), result
    return result.data


# ---------------------------------------------------------------------------
# Scenario 1: single_authority — one spec owns *.ts
# ---------------------------------------------------------------------------


class TestSingleAuthority:
    def test_ts_allowed_py_out_of_scope(self) -> None:
        report = _run(
            "single_authority",
            "src/code.ts",
            ["src/code.ts", "src/script.py"],
        )
        assert report.authority_source == "src/feature.sdd"
        assert report.allowed == ["src/code.ts"]
        assert report.out_of_scope == ["src/script.py"]
        assert report.multiple_authorities is None

    def test_new_ts_allowed_by_pattern(self) -> None:
        """A .ts file that doesn't exist yet is allowed via pattern match."""
        report = _run("single_authority", "src/code.ts", ["src/brand_new.ts"])
        assert report.allowed == ["src/brand_new.ts"]
        assert report.out_of_scope == []


# ---------------------------------------------------------------------------
# Scenario 2: multiple_authorities — module *.ts + feature invoice.ts
# ---------------------------------------------------------------------------


class TestMultipleAuthorities:
    def test_both_specs_surface(self) -> None:
        report = _run(
            "multiple_authorities",
            "src/billing/invoice.ts",
            ["src/billing/invoice.ts"],
        )
        # Nearest spec granting authority is the feature (last in the chain).
        assert report.authority_source == "src/billing/feature.sdd"
        assert report.allowed == ["src/billing/invoice.ts"]
        assert report.multiple_authorities is not None
        claimants = {m.spec for m in report.multiple_authorities}
        assert claimants == {
            "src/billing/module.sdd",
            "src/billing/feature.sdd",
        }
        # Every entry references the proposed file and a real line number.
        for entry in report.multiple_authorities:
            assert entry.file == "src/billing/invoice.ts"
            assert entry.line > 0

    def test_chain_order_root_first(self) -> None:
        """Entries are emitted in chain order (module before feature)."""
        report = _run(
            "multiple_authorities",
            "src/billing/invoice.ts",
            ["src/billing/invoice.ts"],
        )
        assert report.multiple_authorities is not None
        specs = [m.spec for m in report.multiple_authorities]
        assert specs.index("src/billing/module.sdd") < specs.index(
            "src/billing/feature.sdd"
        )


# ---------------------------------------------------------------------------
# Scenario 3: new_file_in_glob — owns src/billing/*, propose a new file
# ---------------------------------------------------------------------------


class TestNewFileInGlob:
    def test_new_file_allowed_by_glob(self) -> None:
        report = _run(
            "new_file_in_glob",
            "src/billing/existing.ts",
            ["src/billing/new_file.ts"],
        )
        assert report.authority_source == "app.sdd"
        assert report.allowed == ["src/billing/new_file.ts"]
        assert report.out_of_scope == []

    def test_existing_file_also_allowed(self) -> None:
        report = _run(
            "new_file_in_glob",
            "src/billing/existing.ts",
            ["src/billing/existing.ts"],
        )
        assert report.allowed == ["src/billing/existing.ts"]

    def test_nested_new_file_not_matched_by_single_star(self) -> None:
        """``src/billing/*`` does not cross a directory — a nested new file is
        out of scope (pathlib-glob semantics, not fnmatch)."""
        report = _run(
            "new_file_in_glob",
            "src/billing/existing.ts",
            ["src/billing/sub/deep.ts"],
        )
        assert report.allowed == []
        assert report.out_of_scope == ["src/billing/sub/deep.ts"]


# ---------------------------------------------------------------------------
# Scenario 4: no_spec_coverage — target governed by no spec
# ---------------------------------------------------------------------------


class TestNoSpecCoverage:
    def test_null_authority_everything_out_of_scope(self) -> None:
        report = _run(
            "no_spec_coverage",
            "src/orphan.ts",
            ["src/orphan.ts", "src/other.ts"],
        )
        assert report.authority_source is None
        assert report.allowed == []
        assert report.out_of_scope == ["src/orphan.ts", "src/other.ts"]
        assert report.multiple_authorities is None
        assert report.reason == "No SpecDD coverage for this target."


# ---------------------------------------------------------------------------
# Scenario 5: glob_vs_literal — root owns src/billing/*, module owns invoice.ts
# ---------------------------------------------------------------------------


class TestGlobVsLiteral:
    def test_both_surface_in_multiple_authorities(self) -> None:
        report = _run(
            "glob_vs_literal",
            "src/billing/invoice.ts",
            ["src/billing/invoice.ts"],
        )
        assert report.multiple_authorities is not None
        claimants = {m.spec for m in report.multiple_authorities}
        assert claimants == {"app.sdd", "src/billing/module.sdd"}
        # Nearest authority is the module.
        assert report.authority_source == "src/billing/module.sdd"
        assert report.allowed == ["src/billing/invoice.ts"]


# ---------------------------------------------------------------------------
# Scenario 6: parent_authority — nearest spec grants nothing; parent does
# ---------------------------------------------------------------------------


class TestParentAuthority:
    """The nearest spec (``src/feature.sdd``) declares no ``Owns:`` /
    ``Can modify:``, so the leaf→root walk must skip it and return the
    parent ``app.sdd`` as the authority."""

    def test_walks_up_to_parent_for_authority(self) -> None:
        report = _run(
            "parent_authority",
            "src/code.ts",
            ["src/code.ts"],
        )
        assert report.authority_source == "app.sdd"
        assert report.allowed == ["src/code.ts"]
        assert report.out_of_scope == []
        # Only one spec grants authority, so there's no ambiguity to surface.
        assert report.multiple_authorities is None


# ---------------------------------------------------------------------------
# Normalization + matching internals
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_absolute_inside_repo_normalized(self, tmp_path: Path) -> None:
        rel = _normalize_proposed(str(tmp_path / "src" / "a.ts"), tmp_path)
        assert rel == "src/a.ts"

    def test_relative_collapses_dot_segments(self, tmp_path: Path) -> None:
        assert _normalize_proposed("./src/./a.ts", tmp_path) == "src/a.ts"

    def test_parent_escape_returns_none(self, tmp_path: Path) -> None:
        """A path that climbs out of the repo can't be classified — None."""
        assert _normalize_proposed("../outside.ts", tmp_path) is None

    def test_backslashes_normalized(self, tmp_path: Path) -> None:
        assert _normalize_proposed("src\\billing\\a.ts", tmp_path) == "src/billing/a.ts"

    def test_out_of_repo_file_reported_verbatim(self) -> None:
        """An out-of-repo proposed path lands in out_of_scope as-passed."""
        report = _run("single_authority", "src/code.ts", ["../escape.ts"])
        assert report.out_of_scope == ["../escape.ts"]
        assert report.allowed == []


class TestGlobToRegex:
    @pytest.mark.parametrize(
        ("pattern", "path", "matches"),
        [
            ("src/billing/*", "src/billing/a.ts", True),
            ("src/billing/*", "src/billing/sub/a.ts", False),  # * stays in segment
            ("*.ts", "a.ts", True),
            ("*.ts", "a.py", False),
            ("**/*.test.ts", "a.test.ts", True),  # ** spans zero dirs
            ("**/*.test.ts", "x/y/a.test.ts", True),  # ** spans many dirs
            ("**/*.test.ts", "a.ts", False),
            ("src/**", "src/a.ts", True),  # bare ** (not followed by /)
            ("src/**", "src/a/b.ts", True),
            ("invoice.ts", "invoice.ts", True),
            ("invoice.ts", "invoiceXts", False),  # '.' is literal, not any-char
            ("a?.ts", "ab.ts", True),
            ("a?.ts", "abc.ts", False),
        ],
    )
    def test_pathlib_glob_semantics(
        self, pattern: str, path: str, matches: bool
    ) -> None:
        assert (_glob_to_regex(pattern).match(path) is not None) is matches


class TestPatternMatchesPath:
    """The new-file matcher anchors patterns at the spec's own directory."""

    def test_literal_anchored_at_spec_dir(self) -> None:
        assert _pattern_matches_path("invoice.ts", "src/billing", "src/billing/invoice.ts")

    def test_glob_from_root_spec(self) -> None:
        assert _pattern_matches_path("src/billing/*", ".", "src/billing/new.ts")

    def test_absolute_pattern_never_matches(self) -> None:
        """Absolute Owns: patterns are non-portable — they grant nothing here
        (validate_spec flags them on the source side)."""
        assert not _pattern_matches_path("/abs/invoice.ts", "src/billing", "abs/invoice.ts")

    def test_empty_pattern_never_matches(self) -> None:
        assert not _pattern_matches_path("   ", "src/billing", "src/billing/invoice.ts")


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    def test_relative_target_without_repo_root_is_invalid_input(self) -> None:
        result = check_modification_scope(
            target="src/code.ts", proposed_files=["src/code.ts"]
        )
        assert isinstance(result, Err)
        assert result.error == "INVALID_INPUT"

    def test_missing_target_is_not_found(self) -> None:
        root = SCOPE_FIXTURES_DIR / "single_authority"
        result = check_modification_scope(
            target=str(root / "src" / "ghost.ts"),
            proposed_files=["src/code.ts"],
            repo_root=str(root),
        )
        assert isinstance(result, Err)
        assert result.error == "NOT_FOUND"
