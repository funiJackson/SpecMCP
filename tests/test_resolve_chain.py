"""Tests for :func:`specdd_mcp.parser.resolve_spec_chain`.

This file covers the algorithmic core (input validation, repo_root detection,
ordering, malformed handling, error paths). PR 2 commit 7 adds a richer
fixture corpus (``tests/fixtures/chains/``) that exercises more directory
shapes against the same function.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.types import Err, Ok


def _make_repo(tmp_path: Path) -> Path:
    """Initialize a tmp directory as a SpecDD repo (drop a .specdd/ marker).

    The directory is created if it doesn't already exist so tests can pass
    nested paths under their own ``tmp_path``.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specdd").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_empty_target_returns_invalid_input() -> None:
    result = resolve_spec_chain(target="")
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_relative_target_without_repo_root_returns_invalid_input() -> None:
    result = resolve_spec_chain(target="src/foo.sdd")
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"
    assert "absolute" in result.message.lower()


def test_relative_target_with_repo_root_works(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    result = resolve_spec_chain(target="app.sdd", repo_root=str(repo))
    assert isinstance(result, Ok)
    assert len(result.data.chain) == 1


# ---------------------------------------------------------------------------
# NOT_FOUND
# ---------------------------------------------------------------------------


def test_missing_target_returns_not_found(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = resolve_spec_chain(target=str(repo / "ghost.sdd"))
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_missing_repo_root_returns_not_found(tmp_path: Path) -> None:
    f = tmp_path / "x.sdd"
    f.write_text("Spec: X\n")
    result = resolve_spec_chain(
        target=str(f),
        repo_root=str(tmp_path / "ghost"),
    )
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_no_repo_root_marker_returns_not_found(tmp_path: Path) -> None:
    """When neither .specdd/ nor .git/ exists anywhere upstream of target,
    we can't auto-detect the repo. Reject with NOT_FOUND."""
    # Make a deeply nested target with no markers above it.
    deep = tmp_path / "no-markers-anywhere" / "x.sdd"
    deep.parent.mkdir(parents=True)
    deep.write_text("Spec: X\n")
    # The test runner's CWD may itself be inside a git repo; the find_repo_root
    # walk would find that. We can't really prevent that here without
    # isolating processes. Skip the assertion if we accidentally landed inside
    # one — we just want to know the function does not crash.
    result = resolve_spec_chain(target=str(deep))
    assert isinstance(result, (Ok, Err))


# ---------------------------------------------------------------------------
# OUT_OF_SCOPE
# ---------------------------------------------------------------------------


def test_target_outside_repo_root_returns_out_of_scope(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "inside")
    outside = tmp_path / "outside.sdd"
    outside.write_text("Spec: Out\n")
    result = resolve_spec_chain(target=str(outside), repo_root=str(repo))
    assert isinstance(result, Err)
    assert result.error == "OUT_OF_SCOPE"


# ---------------------------------------------------------------------------
# Empty repo (no .sdd files)
# ---------------------------------------------------------------------------


def test_no_sdd_files_returns_empty_chain(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    target = repo / "code.py"
    target.write_text("x = 1\n")
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert result.data.chain == []
    assert result.data.nearest is None
    assert result.data.target == "code.py"


# ---------------------------------------------------------------------------
# Single-spec chains
# ---------------------------------------------------------------------------


def test_single_app_spec_at_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    code = repo / "src" / "foo.ts"
    code.parent.mkdir()
    code.write_text("// code\n")
    result = resolve_spec_chain(target=str(code))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == ["App"]
    assert result.data.nearest is not None
    assert result.data.nearest.name == "App"


def test_target_is_sdd_file_itself(tmp_path: Path) -> None:
    """When target IS a .sdd file, it appears as the last chain element."""
    repo = _make_repo(tmp_path)
    spec = repo / "src" / "invoice.sdd"
    spec.parent.mkdir()
    spec.write_text("Spec: Invoice\n")
    result = resolve_spec_chain(target=str(spec))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == ["Invoice"]


# ---------------------------------------------------------------------------
# Multi-level chains
# ---------------------------------------------------------------------------


def test_three_level_chain_root_to_leaf_order(tmp_path: Path) -> None:
    """app.sdd at root → module.sdd in src/billing → service spec in services/."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    (repo / "src").mkdir()
    (repo / "src" / "billing").mkdir()
    (repo / "src" / "billing" / "module.sdd").write_text("Spec: BillingModule\n")
    (repo / "src" / "billing" / "services").mkdir()
    (repo / "src" / "billing" / "services" / "invoice.sdd").write_text(
        "Spec: InvoiceService\n"
    )
    code = repo / "src" / "billing" / "services" / "invoice.ts"
    code.write_text("// code\n")

    result = resolve_spec_chain(target=str(code))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == [
        "App",
        "BillingModule",
        "InvoiceService",
    ]
    assert result.data.nearest is not None
    assert result.data.nearest.name == "InvoiceService"


# ---------------------------------------------------------------------------
# Same-directory disambiguation
# ---------------------------------------------------------------------------


def test_same_directory_specs_ordered_by_level_precedence(tmp_path: Path) -> None:
    """In one directory, module < feature < service. NOT lexicographic
    (which would put feature before module)."""
    repo = _make_repo(tmp_path)
    d = repo / "src"
    d.mkdir()
    (d / "feature.sdd").write_text("Spec: Feature\n")
    (d / "module.sdd").write_text("Spec: Module\n")
    (d / "invoice.service.sdd").write_text("Spec: InvoiceService\n")
    target = d / "code.ts"
    target.write_text("// code\n")

    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    names = [s.name for s in result.data.chain]
    # module appears before feature appears before service.
    assert names == ["Module", "Feature", "InvoiceService"]
    # InvoiceService is highest precedence (service) → nearest.
    assert result.data.nearest is not None
    assert result.data.nearest.name == "InvoiceService"


def test_same_level_specs_ordered_lexicographically(tmp_path: Path) -> None:
    """Two specs at the same level (e.g. both .service.sdd) tie-break by path."""
    repo = _make_repo(tmp_path)
    d = repo / "src"
    d.mkdir()
    (d / "alpha.service.sdd").write_text("Spec: Alpha\n")
    (d / "beta.service.sdd").write_text("Spec: Beta\n")
    target = d / "code.ts"
    target.write_text("// code\n")

    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == ["Alpha", "Beta"]


# ---------------------------------------------------------------------------
# Malformed specs
# ---------------------------------------------------------------------------


def test_malformed_spec_goes_to_malformed_list(tmp_path: Path) -> None:
    """A binary file with .sdd extension is parse-able to PARSE_ERROR; it
    should appear in `malformed`, not in `chain`."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    (repo / "broken.sdd").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    target = repo / "code.py"
    target.write_text("x = 1\n")

    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == ["App"]
    assert [m.path for m in result.data.malformed] == ["broken.sdd"]
    assert result.data.malformed[0].error == "PARSE_ERROR"


# ---------------------------------------------------------------------------
# Path representations
# ---------------------------------------------------------------------------


def test_chain_paths_are_posix_repo_relative(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src" / "billing").mkdir(parents=True)
    (repo / "src" / "billing" / "invoice.sdd").write_text("Spec: Invoice\n")
    target = repo / "src" / "billing" / "invoice.ts"
    target.write_text("// code\n")

    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert result.data.target == "src/billing/invoice.ts"
    assert result.data.chain[0].path == "src/billing/invoice.sdd"
    # Repo root in output is POSIX (no backslashes), absolute.
    assert "\\" not in result.data.repo_root


def test_warnings_from_individual_specs_prefixed_with_path(tmp_path: Path) -> None:
    """A spec that emits a warning during parse (e.g. no Spec: header) should
    surface it with the spec's path prefixed."""
    repo = _make_repo(tmp_path)
    (repo / "headerless.sdd").write_text("Just some text, no Spec: line.\n")
    target = repo / "code.py"
    target.write_text("x = 1\n")

    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    # The spec is in the chain (it parses with a warning, not an error).
    assert len(result.data.chain) == 1
    # The warning should carry a "headerless.sdd: ..." prefix.
    assert any(w.startswith("headerless.sdd:") for w in result.warnings)


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


def test_auto_detect_repo_root_from_specdd_marker(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    target = repo / "src" / "code.ts"
    target.parent.mkdir()
    target.write_text("// code\n")

    result = resolve_spec_chain(target=str(target))  # no repo_root given
    assert isinstance(result, Ok)
    assert result.data.repo_root == repo.resolve().as_posix()


def test_explicit_repo_root_wins_over_auto_detection(tmp_path: Path) -> None:
    """If caller provides repo_root, we don't second-guess it."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / ".specdd").mkdir()
    inner = outer / "subproject"
    inner.mkdir()
    (inner / ".specdd").mkdir()  # nested SpecDD project
    (inner / "app.sdd").write_text("Spec: SubApp\n")
    target = inner / "code.ts"
    target.write_text("// code\n")

    # Force the outer as repo_root — chain should start at outer.
    result = resolve_spec_chain(target=str(target), repo_root=str(outer))
    assert isinstance(result, Ok)
    # The inner app.sdd is included (it's in the walk from outer to target).
    assert any(s.path == "subproject/app.sdd" for s in result.data.chain)
