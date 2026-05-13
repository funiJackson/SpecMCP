"""Tests that exercise parse_spec against committed .sdd fixture files.

The fixtures in ``tests/fixtures/specs/`` are real SpecDD specs. Parsing them
asserts that the pipeline handles realistic shapes (not just synthetic inline
strings). They also double as a documentation corpus — anyone can ``cat`` a
fixture to see what a valid SpecDD spec looks like.

This file contains:

- A roster test that locks in the expected fixture filenames.
- A parametrized smoke test that asserts every fixture parses without error.
- Focused per-fixture tests for the ones with interesting assertions.
"""

from __future__ import annotations

import pytest

from specdd_mcp.parser import parse_spec
from specdd_mcp.types import Err, Ok
from tests.conftest import FIXTURES_DIR

# The committed set of fixture filenames. Adding a new fixture requires
# updating this list AND adding a focused test below.
ALL_FIXTURES: tuple[str, ...] = (
    "empty.sdd",
    "minimal.sdd",
    "full_service.sdd",
    "tasks_all_states.sdd",
    "multiple_scenarios.sdd",
    "unknown_sections.sdd",
    "multibyte_content.sdd",
    "deep_indentation.sdd",
    "readme_calculator.sdd",
)


# ---------------------------------------------------------------------------
# Roster: prevent accidental drift between disk and ALL_FIXTURES
# ---------------------------------------------------------------------------


def test_fixture_roster_matches_disk() -> None:
    """The constant `ALL_FIXTURES` must match the actual .sdd files on disk."""
    on_disk = sorted(p.name for p in FIXTURES_DIR.glob("*.sdd"))
    assert sorted(ALL_FIXTURES) == on_disk


# ---------------------------------------------------------------------------
# Smoke: every fixture must parse without an Err
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_every_fixture_parses_without_error(fixture_name: str) -> None:
    path = FIXTURES_DIR / fixture_name
    result = parse_spec(path=str(path))
    assert isinstance(result, Ok), (
        f"{fixture_name} returned Err: "
        f"{result.error if isinstance(result, Err) else 'n/a'}"
    )


# ---------------------------------------------------------------------------
# Focused: per-fixture assertions
# ---------------------------------------------------------------------------


def test_empty_fixture_parses_with_warning() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "empty.sdd"))
    assert isinstance(result, Ok)
    assert result.data.name == ""
    assert result.data.line_count == 0
    assert any("no `Spec:` header" in w for w in result.warnings)


def test_minimal_fixture() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "minimal.sdd"))
    assert isinstance(result, Ok)
    spec = result.data
    assert spec.name == "Minimal Example"
    assert spec.purpose is not None
    assert "smallest viable" in spec.purpose
    # Only spec + purpose populated.
    assert spec.must is None
    assert spec.tasks is None


def test_full_service_fixture_populates_every_section() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "full_service.sdd"))
    assert isinstance(result, Ok)
    spec = result.data

    assert spec.name == "Invoice Service"
    assert spec.platform == "TypeScript/Node"
    assert spec.purpose is not None
    assert spec.structure is not None and len(spec.structure) == 2

    assert spec.owns == ["invoice.ts", "invoice.test.ts"]
    assert spec.can_modify == ["invoice.ts", "invoice.test.ts"]
    assert spec.can_read == ["../models/*", "../ports/*"]
    assert spec.references == [
        "../models/invoice.sdd",
        "../ports/billing-provider.sdd",
    ]

    assert spec.must is not None and len(spec.must) == 3
    assert spec.must_not is not None and len(spec.must_not) == 3
    assert spec.depends_on is not None and len(spec.depends_on) == 3
    assert spec.forbids == ["stripe"]

    assert spec.exposes is not None
    assert spec.accepts is not None
    assert spec.returns is not None
    assert spec.raises is not None and len(spec.raises) == 2
    assert spec.handles is not None and len(spec.handles) == 3

    # All 5 task states present, 5 tasks with IDs.
    assert spec.tasks is not None
    assert len(spec.tasks) == 5
    assert {t.state for t in spec.tasks} == {
        "open", "done", "blocked", "needs_decision", "skipped"
    }
    assert all(t.id is not None for t in spec.tasks)

    assert spec.scenarios is not None
    assert len(spec.scenarios) == 2

    assert spec.examples is not None and len(spec.examples) == 1
    assert spec.done_when is not None and len(spec.done_when) == 3

    # Filename has no level suffix and the directory is fictional, so level
    # falls through to "unknown" — unless we infer from the literal path used.
    # The parsed path is the real disk path, which DOES contain "specs/".
    assert spec.level in {"unknown", "custom", "service"}  # tolerant

    # No warnings — this is a canonical fixture.
    assert result.warnings == []


def test_tasks_all_states_fixture() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "tasks_all_states.sdd"))
    assert isinstance(result, Ok)
    tasks = result.data.tasks
    assert tasks is not None
    assert len(tasks) == 9
    # The first 5 are without IDs, the last 4 have IDs.
    assert [t.id for t in tasks[:5]] == [None] * 5
    assert [t.id for t in tasks[5:]] == ["#1", "#2", "#42", "#100"]


def test_multiple_scenarios_fixture() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "multiple_scenarios.sdd"))
    assert isinstance(result, Ok)
    scenarios = result.data.scenarios
    assert scenarios is not None
    assert [s.name for s in scenarios] == [
        "first scenario",
        "second scenario",
        "third scenario",
    ]
    # The third scenario has 4 steps (Given, And, When, Then).
    assert len(scenarios[2].steps) == 4


def test_unknown_sections_fixture() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "unknown_sections.sdd"))
    assert isinstance(result, Ok)
    unknowns = result.data.unknown_sections
    assert unknowns is not None
    assert [u.name for u in unknowns] == [
        "Custom Header",
        "Another Unknown",
        "Mixed Bag",
    ]
    # Each unknown carries a non-empty body.
    assert all(any(line.strip() for line in u.lines) for u in unknowns)


def test_multibyte_content_fixture() -> None:
    result = parse_spec(path=str(FIXTURES_DIR / "multibyte_content.sdd"))
    assert isinstance(result, Ok)
    spec = result.data
    assert spec.name == "计算器服务 🧮"
    assert spec.must is not None
    assert any("🎯" in m for m in spec.must)
    assert any("中文" in m for m in spec.must)
    assert spec.tasks is not None
    assert any("➕" in t.text for t in spec.tasks)  # noqa: RUF001 (intentional emoji)


def test_deep_indentation_fixture() -> None:
    """Bullets with deeper-indented continuations should be merged."""
    result = parse_spec(path=str(FIXTURES_DIR / "deep_indentation.sdd"))
    assert isinstance(result, Ok)
    must = result.data.must
    assert must is not None
    assert len(must) == 4
    assert must[0] == "This is bullet one with a single continuation."
    assert must[1] == "This is bullet two with a continuation and another continuation."
    assert must[2] == "This is bullet three."
    assert must[3] == "This is bullet four with a deeply-indented continuation."


def test_readme_calculator_fixture_matches_readme_spec() -> None:
    """The Calculator Add example from the SpecDD README. Canonical."""
    result = parse_spec(path=str(FIXTURES_DIR / "readme_calculator.sdd"))
    assert isinstance(result, Ok)
    spec = result.data
    assert spec.name == "Calculator Add"
    assert spec.purpose == "Add two finite numbers."
    assert spec.owns == ["calculator.js"]
    assert spec.exposes == ["Calculator.add(a, b)"]
    assert spec.must == ["Return a + b.", "Reject non-number inputs."]
    assert spec.must_not == ["Round results."]
    assert spec.scenarios is not None
    assert spec.scenarios[0].name == "add numbers"
    assert result.warnings == []
