"""Tests for the cross-spec validation rules (PR 7, DESIGN §5.7).

Three layers, mirroring how the code is split:

  * Pure-mapper tests (:class:`TestConflictsToIssues`) construct an
    :class:`EffectiveConstraints` by hand and assert
    :func:`conflicts_to_issues` maps / filters / drops the right
    conflicts. No filesystem, no parser.
  * Orchestrator tests (:class:`TestInheritanceConflictsFixtures`) run the
    registered rule against the committed ``chains_with_conflicts/``
    corpus — the same fixtures the conflict *detectors* use, validated
    here from the leaf spec's point of view. Reusing them keeps one
    source of truth for "what each conflict looks like on disk."
  * End-to-end tests (:class:`TestValidateSpecCrossSpec`) drive the public
    ``validate_spec`` tool with ``check_inheritance=True`` so the codes are
    proven to surface through the real Result envelope.

The ``must_vs_must_not`` fixture is the key negative control: the detector
finds a real contradiction there, but it is **not** a ``validate_spec``
rule, so it must produce zero validation issues.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.validation.cross_spec import (
    CROSS_SPEC_RULES,
    check_inheritance_conflicts,
    conflicts_to_issues,
)
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.server.tools import validate_spec
from specdd_mcp.types import (
    Conflict,
    ConflictKind,
    Constraint,
    EffectiveConstraints,
    Ok,
    ParsedSpec,
)
from tests.conftest import CONFLICT_FIXTURES_DIR

_TARGET = "src/service.sdd"


def _conflict(
    kind: ConflictKind,
    *,
    a_source: str = _TARGET,
    a_line: int = 5,
    b_source: str = "app.sdd",
    b_line: int = 9,
) -> Conflict:
    """A conflict with distinct provenance on each side. ``rule_a`` defaults
    to the target spec (the violator) so the common case isn't filtered out."""
    return Conflict(
        kind=kind,
        rule_a=Constraint(rule="local rule", source=a_source, line=a_line),
        rule_b=Constraint(rule="inherited rule", source=b_source, line=b_line),
    )


def _constraints(*conflicts: Conflict, target: str = _TARGET) -> EffectiveConstraints:
    return EffectiveConstraints(target=target, conflicts=list(conflicts))


# ===========================================================================
# conflicts_to_issues — the pure mapper
# ===========================================================================


class TestConflictsToIssues:
    def test_maps_each_kind_to_its_code(self) -> None:
        constraints = _constraints(
            _conflict("duplicate_parent_rule"),
            _conflict("depends_on_vs_forbids"),
            _conflict("task_violates_must_not"),
        )
        codes = {i.code for i in conflicts_to_issues(constraints)}
        assert codes == {
            "DUPLICATE_PARENT_RULE",
            "CONFLICTING_INHERITANCE",
            "TASK_VIOLATES_MUSTNOT",
        }

    def test_drops_must_vs_must_not(self) -> None:
        """``must_vs_must_not`` is detected for get_effective_constraints but
        is not a validate_spec rule — it never becomes an issue."""
        constraints = _constraints(_conflict("must_vs_must_not"))
        assert conflicts_to_issues(constraints) == []

    def test_filters_to_target_as_violator(self) -> None:
        """A conflict whose ``rule_a`` belongs to another spec in the chain is
        that spec's problem — surfaced when *it* is validated, not here."""
        constraints = _constraints(
            _conflict("duplicate_parent_rule", a_source="src/other.sdd"),
        )
        assert conflicts_to_issues(constraints) == []

    def test_populates_line_and_related_provenance(self) -> None:
        constraints = _constraints(
            _conflict(
                "depends_on_vs_forbids", a_line=4, b_source="app.sdd", b_line=12
            ),
        )
        [issue] = conflicts_to_issues(constraints)
        assert issue.severity == "warning"
        assert issue.line == 4  # the local rule
        assert issue.related_spec == "app.sdd"  # the inherited rule
        assert issue.related_line == 12

    def test_all_findings_are_warnings(self) -> None:
        constraints = _constraints(
            _conflict("duplicate_parent_rule"),
            _conflict("depends_on_vs_forbids"),
            _conflict("task_violates_must_not"),
        )
        assert all(i.severity == "warning" for i in conflicts_to_issues(constraints))

    def test_empty_conflicts_yields_no_issues(self) -> None:
        assert conflicts_to_issues(_constraints()) == []

    def test_message_quotes_the_inherited_source(self) -> None:
        constraints = _constraints(
            _conflict("duplicate_parent_rule", b_source="app.sdd", b_line=9),
        )
        [issue] = conflicts_to_issues(constraints)
        assert "app.sdd:9" in issue.message


# ===========================================================================
# check_inheritance_conflicts — the orchestrator, against real fixtures
# ===========================================================================


def _leaf_spec(fixture: str) -> tuple[ParsedSpec, Path]:
    """Parse the leaf ``src/service.sdd`` of a conflict fixture and return it
    with the fixture root (the repo_root for chain resolution)."""
    root = CONFLICT_FIXTURES_DIR / fixture
    result = parse_spec(path=str(root / "src" / "service.sdd"))
    assert isinstance(result, Ok), result
    return result.data, root


class TestInheritanceConflictsFixtures:
    def test_depends_on_vs_forbids_fires_conflicting_inheritance(self) -> None:
        spec, root = _leaf_spec("depends_on_vs_forbids")
        issues = check_inheritance_conflicts(spec, root)
        codes = {i.code for i in issues}
        assert "CONFLICTING_INHERITANCE" in codes
        # Provenance points back at the app-level Forbids.
        finding = next(i for i in issues if i.code == "CONFLICTING_INHERITANCE")
        assert finding.related_spec == "app.sdd"
        assert finding.severity == "warning"

    def test_duplicate_parent_rule_fires(self) -> None:
        spec, root = _leaf_spec("duplicate_parent_rule")
        codes = {i.code for i in check_inheritance_conflicts(spec, root)}
        assert "DUPLICATE_PARENT_RULE" in codes

    def test_task_violates_must_not_fires(self) -> None:
        spec, root = _leaf_spec("task_violates_must_not")
        codes = {i.code for i in check_inheritance_conflicts(spec, root)}
        assert "TASK_VIOLATES_MUSTNOT" in codes

    def test_must_vs_must_not_produces_no_validation_issue(self) -> None:
        """The detector fires for this fixture, but must_vs_must_not is not a
        validate_spec rule, so the cross-spec pass stays silent."""
        spec, root = _leaf_spec("must_vs_must_not")
        assert check_inheritance_conflicts(spec, root) == []


# ===========================================================================
# Guards — cross-spec analysis degrades silently, never raises
# ===========================================================================


class TestGuards:
    def test_repo_root_none_returns_empty(self) -> None:
        spec, _ = _leaf_spec("depends_on_vs_forbids")
        assert check_inheritance_conflicts(spec, None) == []

    def test_unresolvable_chain_returns_empty(self, tmp_path: Path) -> None:
        """A spec parsed from raw content has a virtual path that doesn't
        exist on disk — the chain can't resolve, so cross-spec is skipped
        rather than blowing up the whole validation."""
        result = parse_spec(content="Spec: Ghost\n", virtual_path="ghost.sdd")
        assert isinstance(result, Ok)
        assert check_inheritance_conflicts(result.data, tmp_path) == []


# ===========================================================================
# Registry
# ===========================================================================


def test_registry_holds_the_single_orchestrator() -> None:
    """One orchestrator rule (see cross_spec module docstring for why it
    isn't three)."""
    assert len(CROSS_SPEC_RULES) == 1
    assert CROSS_SPEC_RULES[0] is check_inheritance_conflicts


# ===========================================================================
# End-to-end through the public validate_spec tool
# ===========================================================================


class TestValidateSpecCrossSpec:
    def test_check_inheritance_true_surfaces_cross_spec_code(self) -> None:
        root = CONFLICT_FIXTURES_DIR / "depends_on_vs_forbids"
        result = validate_spec(
            path=str(root / "src" / "service.sdd"),
            check_inheritance=True,
            repo_root=str(root),
        )
        assert result["ok"] is True
        codes = {i["code"] for i in result["data"]["issues"]}
        assert "CONFLICTING_INHERITANCE" in codes
        assert result["data"]["summary"]["warnings"] >= 1

    def test_check_inheritance_false_omits_cross_spec_code(self) -> None:
        """Same spec, flag off → the cross-spec rule never runs, so no
        inheritance findings appear (single-file rules are unaffected)."""
        root = CONFLICT_FIXTURES_DIR / "depends_on_vs_forbids"
        result = validate_spec(
            path=str(root / "src" / "service.sdd"),
            check_inheritance=False,
            repo_root=str(root),
        )
        assert result["ok"] is True
        codes = {i["code"] for i in result["data"]["issues"]}
        assert "CONFLICTING_INHERITANCE" not in codes
