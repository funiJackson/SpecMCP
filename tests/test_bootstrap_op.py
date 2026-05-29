"""Tests for :func:`specdd_mcp.operations.bootstrap.bootstrap_project`.

Covers a fresh init, refuse-to-clobber, the ``with_app`` scaffold, and the
returned created/skipped structure. The CLI and MCP surfaces share this
function, so testing it here covers the core behavior of both.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.bootstrap import BOOTSTRAP_FILES, bootstrap_project
from specdd_mcp.types import Ok


def test_fresh_init_creates_all_files(tmp_path: Path) -> None:
    result = bootstrap_project(tmp_path)
    assert isinstance(result, Ok)
    assert set(result.data.created) == {rel for rel, _ in BOOTSTRAP_FILES}
    assert result.data.skipped == []
    for rel, _ in BOOTSTRAP_FILES:
        assert (tmp_path / rel).exists()
    assert "SpecDD Bootstrap" in (tmp_path / ".specdd" / "bootstrap.md").read_text()


def test_refuses_to_clobber(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("KEEP ME\n")
    result = bootstrap_project(tmp_path)
    assert isinstance(result, Ok)
    assert agents.read_text() == "KEEP ME\n"  # untouched
    assert "AGENTS.md" in result.data.skipped
    assert "CLAUDE.md" in result.data.created  # the rest still written


def test_rerun_skips_everything(tmp_path: Path) -> None:
    bootstrap_project(tmp_path)
    second = bootstrap_project(tmp_path)
    assert isinstance(second, Ok)
    assert second.data.created == []
    assert set(second.data.skipped) == {rel for rel, _ in BOOTSTRAP_FILES}


def test_with_app_scaffolds_app_spec(tmp_path: Path) -> None:
    result = bootstrap_project(tmp_path, with_app=True)
    assert isinstance(result, Ok)
    assert "app.sdd" in result.data.created
    app = tmp_path / "app.sdd"
    assert app.exists()
    assert app.read_text().startswith("Spec: ")


def test_with_app_existing_app_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "app.sdd").write_text("Spec: Existing\n")
    result = bootstrap_project(tmp_path, with_app=True)
    assert isinstance(result, Ok)
    assert "app.sdd" in result.data.skipped
    assert (tmp_path / "app.sdd").read_text() == "Spec: Existing\n"


def test_directory_field_echoes_target(tmp_path: Path) -> None:
    result = bootstrap_project(tmp_path)
    assert isinstance(result, Ok)
    assert result.data.directory == str(tmp_path)
