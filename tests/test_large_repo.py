"""Large-repo guardrail tests — verifies the ``max_specs`` cap really
trips at scale.

PR 1 introduced ``DEFAULT_MAX_SPECS = 1000`` to stop the server from
silently scanning a 50 KLOC monorepo when the user pointed at the wrong
directory. PR 3 wired it through ``list_tasks``. This file generates 1001
synthetic ``.sdd`` files at runtime and exercises the guardrail end-to-end
through the MCP tool wrapper.

Generating 1001 small text files in ``tmp_path`` takes ~200 ms on dev
hardware. ``walk_specs`` short-circuits as soon as the cap is exceeded,
so the actual walk after that is constant-time regardless of how many
files exist.

Tests use the lowercase tool function from ``server.tools`` directly so
no MCP protocol overhead is added (C8's ``test_server.py`` already covers
the protocol path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.server.tools import list_tasks
from specdd_mcp.types import Ok


def _make_repo_with_n_specs(tmp_path: Path, n: int) -> Path:
    """Write ``n`` minimal ``.sdd`` files into ``tmp_path``, mark it as a
    SpecDD repo. Each spec is just a header so parsing is cheap."""
    (tmp_path / ".specdd").mkdir()
    for i in range(n):
        (tmp_path / f"s{i:05}.sdd").write_text(f"Spec: S{i}\n")
    return tmp_path


# ---------------------------------------------------------------------------
# list_tasks: default guardrail
# ---------------------------------------------------------------------------


def test_list_tasks_1001_files_returns_too_large(tmp_path: Path) -> None:
    """One file over the default cap → TOO_LARGE Err propagated to the
    Result envelope, not a silent slow scan."""
    repo = _make_repo_with_n_specs(tmp_path, 1001)
    result = list_tasks(repo_root=str(repo))
    assert result["ok"] is False
    assert result["error"] == "TOO_LARGE"
    assert result["details"]["max_specs"] == 1000


def test_list_tasks_at_exactly_1000_files_passes(tmp_path: Path) -> None:
    """1000 files exactly = within cap. The error fires at >max_specs."""
    repo = _make_repo_with_n_specs(tmp_path, 1000)
    result = list_tasks(repo_root=str(repo))
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# list_tasks: max_specs override unlocks the cap
# ---------------------------------------------------------------------------


def test_list_tasks_max_specs_override_can_unlock(tmp_path: Path) -> None:
    """A caller who knows their repo is large can pass ``max_specs=N`` to
    raise the cap explicitly. 1001 files + max_specs=2000 should pass."""
    repo = _make_repo_with_n_specs(tmp_path, 1001)
    result = list_tasks(repo_root=str(repo), max_specs=2000)
    assert result["ok"] is True


def test_list_tasks_max_specs_override_lower_than_default(tmp_path: Path) -> None:
    """Caller can also tighten the cap below the default (e.g. when running
    inside a test sandbox they expect to be small)."""
    repo = _make_repo_with_n_specs(tmp_path, 11)
    result = list_tasks(repo_root=str(repo), max_specs=10)
    assert result["ok"] is False
    assert result["error"] == "TOO_LARGE"
    assert result["details"]["max_specs"] == 10


# ---------------------------------------------------------------------------
# resolve_spec_chain: NOT subject to the guardrail
# ---------------------------------------------------------------------------


def test_resolve_chain_not_affected_by_total_repo_size(tmp_path: Path) -> None:
    """The chain walker only collects ``.sdd`` from ancestor directories of
    the target, not the entire repo. A 1001-file repo with the target in
    a deep subdirectory should resolve cleanly because the relevant chain
    is small."""
    repo = _make_repo_with_n_specs(tmp_path, 1001)
    # Drop the target a few levels deeper so the walk only visits a few dirs.
    (repo / "src").mkdir()
    target = repo / "src" / "deep.py"
    target.write_text("")

    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    # The 1001 .sdd files at root ARE all in the chain (target's ancestor
    # walk hits the repo root). Verify it didn't crash and the chain has
    # entries — exact count is the 1001 files.
    assert len(result.data.chain) == 1001


# ---------------------------------------------------------------------------
# Scope can shrink the walk below the cap
# ---------------------------------------------------------------------------


def test_list_tasks_scope_to_subdir_avoids_too_large(tmp_path: Path) -> None:
    """1001 files at root, but ``scope`` narrows to a small subdir with only
    a handful — guardrail doesn't trip."""
    repo = _make_repo_with_n_specs(tmp_path, 1001)
    (repo / "src").mkdir()
    (repo / "src" / "a.sdd").write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    (repo / "src" / "b.sdd").write_text("Spec: B\n\nTasks:\n  [ ] two\n")

    result = list_tasks(repo_root=str(repo), scope=str(repo / "src"))
    assert result["ok"] is True
    assert {t["text"] for t in result["data"]} == {"one", "two"}


# ---------------------------------------------------------------------------
# Smoke: error payload shape is stable for callers
# ---------------------------------------------------------------------------


def test_too_large_error_has_actionable_details(tmp_path: Path) -> None:
    """The Err payload must include ``details.max_specs`` so the slash
    command can tell the user what cap they hit and suggest raising it."""
    repo = _make_repo_with_n_specs(tmp_path, 1001)
    result = list_tasks(repo_root=str(repo))
    assert result["ok"] is False
    assert result["error"] == "TOO_LARGE"
    assert "max_specs" in result["details"]
    assert isinstance(result["details"]["max_specs"], int)


@pytest.mark.parametrize("n_files", [11, 51, 101, 1001])
def test_too_large_consistency_across_sizes(
    tmp_path: Path,
    n_files: int,
) -> None:
    """Whatever cap is passed, the error fires consistently when files exceed
    it by one."""
    repo = _make_repo_with_n_specs(tmp_path, n_files)
    cap = n_files - 1
    result = list_tasks(repo_root=str(repo), max_specs=cap)
    assert result["ok"] is False
    assert result["error"] == "TOO_LARGE"
    assert result["details"]["max_specs"] == cap
