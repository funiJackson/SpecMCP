"""Tests for :mod:`specdd_mcp.operations.dependencies`.

Two layers:
  * ``find_dependency_violations`` — pure matcher, exercised on hand-built
    :class:`EffectiveConstraints` (no filesystem).
  * ``check_dependencies`` — the orchestrator, exercised against real spec
    chains on disk (resolution + merge + match).
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.dependencies import (
    check_dependencies,
    find_dependency_violations,
)
from specdd_mcp.types import Constraint, EffectiveConstraints, Err, Ok


def _constraints(
    *,
    forbids: list[Constraint] | None = None,
    must_not: list[Constraint] | None = None,
) -> EffectiveConstraints:
    return EffectiveConstraints(
        target="t.sdd",
        forbids=forbids or [],
        must_not=must_not or [],
    )


# ---------------------------------------------------------------------------
# Pure matcher
# ---------------------------------------------------------------------------


def test_no_violations_when_clean() -> None:
    c = _constraints(forbids=[Constraint(rule="stripe", source="a.sdd", line=1)])
    assert find_dependency_violations(c, ["react", "vue"]) == []


def test_forbids_substring_match() -> None:
    c = _constraints(forbids=[Constraint(rule="stripe", source="app.sdd", line=4)])
    out = find_dependency_violations(c, ["stripe-node"])
    assert len(out) == 1
    assert out[0].dependency == "stripe-node"
    assert out[0].kind == "forbids"
    assert out[0].constraint.source == "app.sdd"
    assert out[0].constraint.line == 4


def test_forbids_is_case_sensitive() -> None:
    """Mirrors detect_depends_on_vs_forbids: case-sensitive containment."""
    c = _constraints(forbids=[Constraint(rule="Stripe", source="a.sdd", line=1)])
    assert find_dependency_violations(c, ["stripe-node"]) == []


def test_forbids_reverse_direction_not_a_match() -> None:
    """`Depends on: stripe` with `Forbids: stripe-node` is NOT a violation —
    the dependency doesn't pull in the forbidden longer name."""
    c = _constraints(
        forbids=[Constraint(rule="stripe-node", source="a.sdd", line=1)]
    )
    assert find_dependency_violations(c, ["stripe"]) == []


def test_must_not_case_insensitive_substring() -> None:
    c = _constraints(
        must_not=[
            Constraint(
                rule="Use the legacy auth module", source="svc.sdd", line=7
            )
        ]
    )
    out = find_dependency_violations(c, ["legacy auth"])
    assert len(out) == 1
    assert out[0].kind == "must_not"
    assert out[0].constraint.line == 7


def test_one_dep_can_trip_multiple_rules() -> None:
    c = _constraints(
        forbids=[Constraint(rule="stripe", source="app.sdd", line=4)],
        must_not=[
            Constraint(rule="integrate stripe directly", source="b.sdd", line=2)
        ],
    )
    out = find_dependency_violations(c, ["stripe"])
    kinds = sorted(v.kind for v in out)
    assert kinds == ["forbids", "must_not"]


def test_output_sorted_stably() -> None:
    c = _constraints(
        forbids=[
            Constraint(rule="lodash", source="app.sdd", line=5),
            Constraint(rule="stripe", source="app.sdd", line=4),
        ]
    )
    out = find_dependency_violations(c, ["stripe-node", "lodash-es"])
    assert [(v.dependency, v.kind) for v in out] == [
        ("lodash-es", "forbids"),
        ("stripe-node", "forbids"),
    ]


def test_empty_dependencies_returns_empty() -> None:
    c = _constraints(forbids=[Constraint(rule="stripe", source="a.sdd", line=1)])
    assert find_dependency_violations(c, []) == []


# ---------------------------------------------------------------------------
# Orchestrator (real chains on disk)
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / ".specdd").mkdir()
    (tmp_path / "app.sdd").write_text("Spec: App\n\nForbids:\n  stripe\n  lodash\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.sdd").write_text(
        "Spec: Svc\n\nMust not:\n  use the legacy auth module\n"
    )
    return tmp_path


def test_orchestrator_merges_chain_and_matches(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = check_dependencies(
        "src/service.sdd",
        proposed_dependencies=["stripe-node", "react", "legacy auth"],
        repo_root=str(repo),
    )
    assert isinstance(result, Ok)
    found = {(v.dependency, v.kind, v.constraint.source) for v in result.data}
    assert ("stripe-node", "forbids", "app.sdd") in found
    assert ("legacy auth", "must_not", "src/service.sdd") in found
    assert not any(v.dependency == "react" for v in result.data)


def test_orchestrator_clean_returns_empty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = check_dependencies(
        "src/service.sdd",
        proposed_dependencies=["react", "vue"],
        repo_root=str(repo),
    )
    assert isinstance(result, Ok)
    assert result.data == []


def test_orchestrator_unknown_target_returns_not_found(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = check_dependencies(
        "ghost.sdd", proposed_dependencies=["x"], repo_root=str(repo)
    )
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_orchestrator_relative_target_without_repo_root_invalid() -> None:
    result = check_dependencies(
        "src/service.sdd", proposed_dependencies=["x"]
    )
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"
