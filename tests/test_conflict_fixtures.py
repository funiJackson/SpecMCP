"""Fixture-based tests for the four conflict detectors.

Each subdirectory under ``tests/fixtures/chains_with_conflicts/`` is a
minimal SpecDD tree built to fire exactly one conflict kind. These tests
lock in detector behavior against real on-disk specs (not just inline
strings), and the fixtures double as a documentation corpus — anyone can
``cat`` them to see what each kind of conflict looks like in practice.

Inline / synthetic detector tests stay in :mod:`tests.test_conflicts`.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.merge import build_effective_constraints
from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.types import Ok
from tests.conftest import CONFLICT_FIXTURES_DIR


def _build(fixture_name: str) -> tuple[Path, object]:
    """Resolve the chain for the fixture's ``src/code.ts`` and build
    EffectiveConstraints. Asserts the chain resolved cleanly."""
    fixture_root = CONFLICT_FIXTURES_DIR / fixture_name
    target = fixture_root / "src" / "code.ts"
    chain_result = resolve_spec_chain(target=str(target))
    assert isinstance(chain_result, Ok)
    constraints = build_effective_constraints(
        chain_result.data,
        repo_root=fixture_root,
    )
    return fixture_root, constraints


def _by_kind(constraints: object, kind: str) -> list[object]:
    """Filter the conflicts list to a specific kind."""
    return [c for c in constraints.conflicts if c.kind == kind]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# depends_on_vs_forbids
# ---------------------------------------------------------------------------


def test_depends_vs_forbids_fixture_fires_correctly() -> None:
    """`Depends on: stripe` in the leaf + `Forbids: stripe` in the root →
    exactly one conflict, with provenance on both sides."""
    _root, constraints = _build("depends_on_vs_forbids")
    conflicts = _by_kind(constraints, "depends_on_vs_forbids")

    assert len(conflicts) == 1
    c = conflicts[0]
    # rule_a is the violator (Depends on side, in the leaf spec).
    assert c.rule_a.rule == "stripe"
    assert c.rule_a.source == "src/service.sdd"
    # rule_b is the rule being violated (Forbids side, in the root spec).
    assert c.rule_b.rule == "stripe"
    assert c.rule_b.source == "app.sdd"
    # Lines populated (exact values depend on fixture content; we just
    # assert non-zero to confirm provenance is real).
    assert c.rule_a.line > 0
    assert c.rule_b.line > 0


def test_depends_vs_forbids_fixture_no_other_conflict_kinds() -> None:
    """The fixture should only fire its target kind, not accidentally light
    up the others."""
    _root, constraints = _build("depends_on_vs_forbids")
    kinds = {c.kind for c in constraints.conflicts}
    assert kinds == {"depends_on_vs_forbids"}


# ---------------------------------------------------------------------------
# duplicate_parent_rule
# ---------------------------------------------------------------------------


def test_duplicate_parent_rule_fixture_fires_correctly() -> None:
    """Identical Must in both parent and leaf → one duplicate_parent_rule."""
    _root, constraints = _build("duplicate_parent_rule")
    conflicts = _by_kind(constraints, "duplicate_parent_rule")

    assert len(conflicts) == 1
    c = conflicts[0]
    # rule_a = child duplicate; rule_b = ancestor original.
    assert c.rule_a.source == "src/service.sdd"
    assert c.rule_b.source == "app.sdd"
    assert c.rule_a.rule == c.rule_b.rule == "Validate input before provider calls."


def test_duplicate_parent_rule_fixture_no_other_kinds() -> None:
    _root, constraints = _build("duplicate_parent_rule")
    kinds = {c.kind for c in constraints.conflicts}
    assert kinds == {"duplicate_parent_rule"}


# ---------------------------------------------------------------------------
# task_violates_must_not
# ---------------------------------------------------------------------------


def test_task_violates_must_not_fixture_fires_correctly() -> None:
    """`Must not: Calculate tax` + task `Add Calculate tax helper` →
    one advisory conflict. Treated as warning-quality by /specc."""
    _root, constraints = _build("task_violates_must_not")
    conflicts = _by_kind(constraints, "task_violates_must_not")

    assert len(conflicts) == 1
    c = conflicts[0]
    # rule_a is the task (rebuilt as Constraint), rule_b is the Must not.
    assert "Calculate tax helper" in c.rule_a.rule
    assert c.rule_a.source == "src/service.sdd"
    assert c.rule_b.rule == "Calculate tax."
    assert c.rule_b.source == "app.sdd"


def test_task_violates_must_not_fixture_no_other_kinds() -> None:
    _root, constraints = _build("task_violates_must_not")
    kinds = {c.kind for c in constraints.conflicts}
    assert kinds == {"task_violates_must_not"}


# ---------------------------------------------------------------------------
# must_vs_must_not
# ---------------------------------------------------------------------------


def test_must_vs_must_not_fixture_fires_correctly() -> None:
    """Byte-identical text under `Must:` (parent) and `Must not:` (leaf)
    → one direct contradiction."""
    _root, constraints = _build("must_vs_must_not")
    conflicts = _by_kind(constraints, "must_vs_must_not")

    assert len(conflicts) == 1
    c = conflicts[0]
    # rule_a = Must (the would-be action), rule_b = Must not (the override).
    assert c.rule_a.rule == "Persist after success."
    assert c.rule_a.source == "app.sdd"
    assert c.rule_b.rule == "Persist after success."
    assert c.rule_b.source == "src/service.sdd"


def test_must_vs_must_not_fixture_no_other_kinds() -> None:
    _root, constraints = _build("must_vs_must_not")
    kinds = {c.kind for c in constraints.conflicts}
    assert kinds == {"must_vs_must_not"}


# ---------------------------------------------------------------------------
# Roster: ensure every conflict kind has a fixture
# ---------------------------------------------------------------------------


def test_every_conflict_kind_has_a_fixture() -> None:
    """If a new conflict kind is added to ``ConflictKind`` literal in
    types.py, this test fails — forcing the author to add a matching fixture
    so detectors are always covered by an on-disk example."""
    from typing import get_args

    from specdd_mcp.types import ConflictKind

    expected = set(get_args(ConflictKind))
    on_disk = {d.name for d in CONFLICT_FIXTURES_DIR.iterdir() if d.is_dir()}
    assert expected == on_disk, (
        f"ConflictKind literals not matched by fixtures.\n"
        f"  ConflictKind values: {expected}\n"
        f"  on-disk fixtures:    {on_disk}\n"
        f"  missing: {expected - on_disk}\n"
        f"  extra:   {on_disk - expected}"
    )
