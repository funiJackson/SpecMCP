"""Tests for :func:`specdd_mcp.operations.specs.list_specs`.

Exercises the repo-wide spec index: walking, level filtering, task summaries,
sorting, malformed-spec tolerance, and the scope / guardrail error paths.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.specs import list_specs
from specdd_mcp.types import Err, Ok


def _make_repo(tmp_path: Path) -> Path:
    """Mark ``tmp_path`` as a SpecDD repo."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specdd").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


def test_empty_repo_returns_empty_list(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = list_specs(repo)
    assert isinstance(result, Ok)
    assert result.data == []
    assert result.warnings == []


def test_single_spec_carries_identity_fields(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: My App\n\nPurpose:\n  Do things.\n")
    result = list_specs(repo)
    assert isinstance(result, Ok)
    assert len(result.data) == 1
    entry = result.data[0]
    assert entry.path == "app.sdd"
    assert entry.name == "My App"
    assert entry.level == "app"
    assert entry.line_count == 4


def test_level_inferred_from_directory_hint(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "services").mkdir()
    (repo / "services" / "billing.sdd").write_text("Spec: Billing\n")
    result = list_specs(repo)
    assert isinstance(result, Ok)
    assert result.data[0].level == "service"


# ---------------------------------------------------------------------------
# Task summary
# ---------------------------------------------------------------------------


def test_task_summary_counts_each_state(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n\nTasks:\n"
        "  [ ] open one\n"
        "  [ ] open two\n"
        "  [x] done one\n"
        "  [-] skipped one\n"
        "  [!] blocked one\n"
        "  [?] needs decision one\n"
    )
    result = list_specs(repo)
    assert isinstance(result, Ok)
    summary = result.data[0].task_summary
    assert summary is not None
    assert summary.open == 2
    assert summary.done == 1
    assert summary.skipped == 1
    assert summary.blocked == 1
    assert summary.needs_decision == 1


def test_spec_without_tasks_summarizes_to_zeros(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nPurpose:\n  No tasks here.\n")
    result = list_specs(repo)
    assert isinstance(result, Ok)
    summary = result.data[0].task_summary
    assert summary is not None
    assert summary.open == 0
    assert summary.done == 0
    assert summary.skipped == 0
    assert summary.blocked == 0
    assert summary.needs_decision == 0


def test_include_task_summary_false_omits_summary(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nTasks:\n  [ ] one\n")
    result = list_specs(repo, include_task_summary=False)
    assert isinstance(result, Ok)
    assert result.data[0].task_summary is None


# ---------------------------------------------------------------------------
# Level filtering
# ---------------------------------------------------------------------------


def test_levels_filter_keeps_only_requested(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    (repo / "services").mkdir()
    (repo / "services" / "billing.sdd").write_text("Spec: Billing\n")
    result = list_specs(repo, levels=["service"])
    assert isinstance(result, Ok)
    assert [e.path for e in result.data] == ["services/billing.sdd"]


def test_empty_levels_list_returns_nothing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    result = list_specs(repo, levels=[])
    assert isinstance(result, Ok)
    assert result.data == []


# ---------------------------------------------------------------------------
# Ordering, malformed tolerance, scope, guardrail
# ---------------------------------------------------------------------------


def test_output_sorted_by_path_ascending(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "z.sdd").write_text("Spec: Z\n")
    (repo / "a.sdd").write_text("Spec: A\n")
    (repo / "src" / "m.sdd").write_text("Spec: M\n")
    result = list_specs(repo)
    assert isinstance(result, Ok)
    assert [e.path for e in result.data] == ["a.sdd", "src/m.sdd", "z.sdd"]


def test_malformed_spec_skipped_and_warned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "good.sdd").write_text("Spec: Good\n")
    (repo / "bad.sdd").write_bytes(b"\x00\x01\x02 binary not a spec")
    result = list_specs(repo)
    assert isinstance(result, Ok)
    assert [e.path for e in result.data] == ["good.sdd"]
    assert any("bad.sdd" in w for w in result.warnings)


def test_scope_narrows_walk(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "root.sdd").write_text("Spec: Root\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "leaf.sdd").write_text("Spec: Leaf\n")
    result = list_specs(repo, scope=repo / "sub")
    assert isinstance(result, Ok)
    assert [e.path for e in result.data] == ["sub/leaf.sdd"]


def test_missing_repo_root_returns_not_found(tmp_path: Path) -> None:
    result = list_specs(tmp_path / "ghost")
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_scope_outside_repo_returns_out_of_scope(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    result = list_specs(repo, scope=outside)
    assert isinstance(result, Err)
    assert result.error == "OUT_OF_SCOPE"


def test_too_many_specs_returns_too_large(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    for i in range(15):
        (repo / f"s{i:02}.sdd").write_text(f"Spec: S{i}\n")
    result = list_specs(repo, max_specs=10)
    assert isinstance(result, Err)
    assert result.error == "TOO_LARGE"
