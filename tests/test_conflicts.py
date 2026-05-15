"""Tests for the conflict detectors in :mod:`specdd_mcp.operations.conflicts`.

Each detector is a pure function over :class:`EffectiveConstraints`. The
detectors are kept independently testable so each kind's behavior is locked
without depending on the rest of the merge pipeline.
"""

from __future__ import annotations

from specdd_mcp.operations.conflicts import (
    detect_depends_on_vs_forbids,
    detect_duplicate_parent_rule,
    detect_must_vs_must_not,
    detect_task_violates_must_not,
)
from specdd_mcp.types import (
    Constraint,
    EffectiveConstraints,
    TaskWithSource,
)


def _ec(
    *,
    depends_on: list[Constraint] | None = None,
    forbids: list[Constraint] | None = None,
    must: list[Constraint] | None = None,
    must_not: list[Constraint] | None = None,
    tasks: list[TaskWithSource] | None = None,
) -> EffectiveConstraints:
    """Build a minimal EffectiveConstraints with only the fields we need."""
    return EffectiveConstraints(
        target="dummy",
        depends_on=depends_on or [],
        forbids=forbids or [],
        must=must or [],
        must_not=must_not or [],
        tasks=tasks or [],
    )


def _c(rule: str, source: str = "spec.sdd", line: int = 1) -> Constraint:
    return Constraint(rule=rule, source=source, line=line)


def _task(text: str, source: str = "spec.sdd", line: int = 1) -> TaskWithSource:
    return TaskWithSource(
        state="open",
        state_symbol=" ",
        text=text,
        line=line,
        indent="  ",
        raw=f"  [ ] {text}",
        source=source,
    )


# ---------------------------------------------------------------------------
# depends_on_vs_forbids
# ---------------------------------------------------------------------------


def test_no_inputs_no_conflicts() -> None:
    assert detect_depends_on_vs_forbids(_ec()) == []


def test_exact_match_is_conflict() -> None:
    ec = _ec(
        depends_on=[_c("stripe", source="invoice.sdd", line=12)],
        forbids=[_c("stripe", source="module.sdd", line=14)],
    )
    conflicts = detect_depends_on_vs_forbids(ec)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind == "depends_on_vs_forbids"
    assert c.rule_a.rule == "stripe"
    assert c.rule_a.source == "invoice.sdd"
    assert c.rule_b.source == "module.sdd"


def test_substring_match_is_conflict() -> None:
    """`Forbids: stripe` catches `Depends on: stripe-node` — the forbidden
    name is contained in the dependency name."""
    ec = _ec(
        depends_on=[_c("stripe-node")],
        forbids=[_c("stripe")],
    )
    assert len(detect_depends_on_vs_forbids(ec)) == 1


def test_reverse_substring_is_not_a_conflict() -> None:
    """A short depends_on name is NOT a violation of a longer forbid name.
    `Depends on: stripe` doesn't pull in `stripe-node`."""
    ec = _ec(
        depends_on=[_c("stripe")],
        forbids=[_c("stripe-node")],
    )
    assert detect_depends_on_vs_forbids(ec) == []


def test_unrelated_names_no_conflict() -> None:
    ec = _ec(
        depends_on=[_c("redis")],
        forbids=[_c("stripe")],
    )
    assert detect_depends_on_vs_forbids(ec) == []


def test_one_depends_matches_multiple_forbids() -> None:
    """Each matching forbid emits its own conflict."""
    ec = _ec(
        depends_on=[_c("stripe-node")],
        forbids=[_c("stripe"), _c("node")],
    )
    conflicts = detect_depends_on_vs_forbids(ec)
    assert len(conflicts) == 2
    assert {c.rule_b.rule for c in conflicts} == {"stripe", "node"}


def test_multiple_depends_match_one_forbid() -> None:
    ec = _ec(
        depends_on=[_c("stripe-node"), _c("stripe-py")],
        forbids=[_c("stripe")],
    )
    conflicts = detect_depends_on_vs_forbids(ec)
    assert len(conflicts) == 2
    assert {c.rule_a.rule for c in conflicts} == {"stripe-node", "stripe-py"}


def test_empty_depends_with_forbids_no_conflict() -> None:
    ec = _ec(forbids=[_c("stripe")])
    assert detect_depends_on_vs_forbids(ec) == []


def test_empty_forbids_with_depends_no_conflict() -> None:
    ec = _ec(depends_on=[_c("stripe")])
    assert detect_depends_on_vs_forbids(ec) == []


def test_conflict_preserves_full_provenance() -> None:
    """Both sides of the conflict carry their source path AND line, so the
    slash command can quote 'invoice.sdd:42 conflicts with app.sdd:14'."""
    ec = _ec(
        depends_on=[_c("stripe", source="invoice.sdd", line=42)],
        forbids=[_c("stripe", source="app.sdd", line=14)],
    )
    conflict = detect_depends_on_vs_forbids(ec)[0]
    assert conflict.rule_a.source == "invoice.sdd"
    assert conflict.rule_a.line == 42
    assert conflict.rule_b.source == "app.sdd"
    assert conflict.rule_b.line == 14


def test_case_sensitive_match() -> None:
    """Match is case-sensitive — `Stripe` != `stripe`. Users name their
    dependencies consistently; we don't second-guess casing."""
    ec = _ec(
        depends_on=[_c("Stripe-Node")],
        forbids=[_c("stripe")],
    )
    assert detect_depends_on_vs_forbids(ec) == []


# ---------------------------------------------------------------------------
# duplicate_parent_rule
# ---------------------------------------------------------------------------


def test_duplicate_no_must_no_conflicts() -> None:
    assert detect_duplicate_parent_rule(_ec()) == []


def test_duplicate_same_rule_in_two_specs_is_conflict() -> None:
    """The bog-standard drift case: leaf spec restates a root spec's rule."""
    ec = _ec(
        must=[
            _c("Validate input.", source="app.sdd", line=4),
            _c("Validate input.", source="src/service.sdd", line=10),
        ],
    )
    conflicts = detect_duplicate_parent_rule(ec)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind == "duplicate_parent_rule"
    # rule_a = duplicate (child / later); rule_b = original (ancestor / earlier).
    assert c.rule_a.source == "src/service.sdd"
    assert c.rule_a.line == 10
    assert c.rule_b.source == "app.sdd"
    assert c.rule_b.line == 4


def test_duplicate_three_levels_emits_all_pairs() -> None:
    """rule appearing in 3 specs gives 3 conflict pairs:
    middle vs root, leaf vs root, leaf vs middle. Surface every drift
    surface so the user sees the full picture."""
    ec = _ec(
        must=[
            _c("Same rule.", source="app.sdd", line=1),       # root
            _c("Same rule.", source="src/module.sdd", line=2),  # middle
            _c("Same rule.", source="src/svc/service.sdd", line=3),  # leaf
        ],
    )
    conflicts = detect_duplicate_parent_rule(ec)
    assert len(conflicts) == 3
    pairs = {(c.rule_a.source, c.rule_b.source) for c in conflicts}
    assert pairs == {
        ("src/module.sdd", "app.sdd"),
        ("src/svc/service.sdd", "app.sdd"),
        ("src/svc/service.sdd", "src/module.sdd"),
    }


def test_duplicate_intra_spec_repeat_not_a_conflict() -> None:
    """If the same spec lists a rule twice (legal-weird, parser parses both),
    it's NOT a parent_rule duplicate. validate_spec handles intra-spec dupes
    as a separate concern."""
    ec = _ec(
        must=[
            _c("Same rule.", source="app.sdd", line=4),
            _c("Same rule.", source="app.sdd", line=10),
        ],
    )
    assert detect_duplicate_parent_rule(ec) == []


def test_duplicate_different_rule_text_not_conflict() -> None:
    ec = _ec(
        must=[
            _c("Rule A.", source="app.sdd", line=1),
            _c("Rule B.", source="service.sdd", line=2),
        ],
    )
    assert detect_duplicate_parent_rule(ec) == []


def test_duplicate_works_for_must_not_too() -> None:
    """Updated post-sibling-fix: sources must be in ancestor relationship."""
    ec = _ec(
        must_not=[
            _c("Call Stripe directly.", source="app.sdd", line=4),
            _c("Call Stripe directly.", source="src/service.sdd", line=10),
        ],
    )
    conflicts = detect_duplicate_parent_rule(ec)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "duplicate_parent_rule"


def test_duplicate_must_and_must_not_separately() -> None:
    """A `Must: X` and a `Must not: X` (cross-section) are NOT a duplicate.
    That's what `must_vs_must_not` detector (C11) handles separately."""
    ec = _ec(
        must=[_c("X.", source="app.sdd", line=1)],
        must_not=[_c("X.", source="service.sdd", line=2)],
    )
    assert detect_duplicate_parent_rule(ec) == []


def test_duplicate_case_sensitive_match() -> None:
    """Byte-identical means case-sensitive. `Validate` ≠ `validate`."""
    ec = _ec(
        must=[
            _c("Validate.", source="app.sdd", line=1),
            _c("validate.", source="service.sdd", line=2),
        ],
    )
    assert detect_duplicate_parent_rule(ec) == []


def test_duplicate_preserves_full_provenance() -> None:
    ec = _ec(
        must=[
            _c("Rule.", source="app.sdd", line=42),
            _c("Rule.", source="src/service.sdd", line=99),
        ],
    )
    conflict = detect_duplicate_parent_rule(ec)[0]
    assert conflict.rule_a.source == "src/service.sdd"
    assert conflict.rule_a.line == 99
    assert conflict.rule_b.source == "app.sdd"
    assert conflict.rule_b.line == 42


def test_duplicate_sibling_specs_not_flagged() -> None:
    """Same-directory specs sharing a rule are PEERS, not drift. Caught by
    benchmark data: `components/todo-form.sdd` and `components/todo-list.sdd`
    legitimately share Must rules — they're cross-cutting concerns, not a
    parent-vs-child copy."""
    ec = _ec(
        must=[
            _c("Be a Lit element.", source="src/components/form.sdd", line=4),
            _c("Be a Lit element.", source="src/components/list.sdd", line=4),
        ],
    )
    assert detect_duplicate_parent_rule(ec) == []


def test_duplicate_root_to_subdir_still_flagged() -> None:
    """An ancestor at root (no path prefix) vs descendant in any subdir
    DOES flag — root is ancestor of everything."""
    ec = _ec(
        must=[
            _c("Validate input.", source="app.sdd", line=4),
            _c("Validate input.", source="src/service.sdd", line=10),
        ],
    )
    assert len(detect_duplicate_parent_rule(ec)) == 1


def test_duplicate_deep_ancestor_still_flagged() -> None:
    """`src/module.sdd` is an ancestor of `src/billing/service.sdd`."""
    ec = _ec(
        must=[
            _c("Rule.", source="src/module.sdd", line=1),
            _c("Rule.", source="src/billing/service.sdd", line=2),
        ],
    )
    assert len(detect_duplicate_parent_rule(ec)) == 1


def test_duplicate_root_siblings_not_flagged() -> None:
    """Two specs at repo root sharing a rule are siblings, not parent/child."""
    ec = _ec(
        must=[
            _c("Rule.", source="app.sdd", line=1),
            _c("Rule.", source="other.sdd", line=2),
        ],
    )
    assert detect_duplicate_parent_rule(ec) == []


# ---------------------------------------------------------------------------
# task_violates_must_not
# ---------------------------------------------------------------------------


def test_task_no_tasks_no_must_not_no_conflicts() -> None:
    assert detect_task_violates_must_not(_ec()) == []


def test_task_direct_substring_is_conflict() -> None:
    """The Must not phrase appears verbatim inside the task text."""
    ec = _ec(
        must_not=[_c("Call Stripe", source="app.sdd", line=14)],
        tasks=[_task("Call Stripe in the service.", source="service.sdd", line=42)],
    )
    conflicts = detect_task_violates_must_not(ec)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind == "task_violates_must_not"
    # rule_a is the task (rebuilt as a Constraint), rule_b is the Must not.
    assert c.rule_a.rule == "Call Stripe in the service."
    assert c.rule_a.source == "service.sdd"
    assert c.rule_a.line == 42
    assert c.rule_b.source == "app.sdd"
    assert c.rule_b.line == 14


def test_task_case_insensitive_match() -> None:
    """The fuzzy match lowercases both sides — `CALCULATE TAX` matches
    `Must not: Calculate tax`."""
    ec = _ec(
        must_not=[_c("Calculate tax.")],
        tasks=[_task("Add CALCULATE TAX helper.")],
    )
    assert len(detect_task_violates_must_not(ec)) == 1


def test_task_trailing_period_stripped_on_both_sides() -> None:
    """`Must not: X.` matches `task: do X` (trailing periods stripped)."""
    ec = _ec(
        must_not=[_c("Calculate tax.")],
        tasks=[_task("Add Calculate tax helper")],  # no period on task
    )
    assert len(detect_task_violates_must_not(ec)) == 1


def test_task_no_overlap_no_conflict() -> None:
    ec = _ec(
        must_not=[_c("Calculate tax.")],
        tasks=[_task("Add invoice validation.")],
    )
    assert detect_task_violates_must_not(ec) == []


def test_task_negating_task_is_known_false_positive(
) -> None:
    """KNOWN LIMITATION (documented in detector docstring): a task that
    *reinforces* a Must not still matches by substring. The slash command
    must treat this kind as advisory only.

    This test locks in the false-positive behavior so any future change to
    "smarter" detection is intentional, not accidental."""
    ec = _ec(
        must_not=[_c("Calculate tax.")],
        tasks=[_task("Don't calculate tax in the service.")],
    )
    # Detector flags this — even though the task agrees with the rule.
    assert len(detect_task_violates_must_not(ec)) == 1


def test_task_with_empty_must_not_rule_skipped() -> None:
    """Defensive: an empty rule text shouldn't trigger spurious matches
    (empty-string-in-anything is always True)."""
    ec = _ec(
        must_not=[_c("")],
        tasks=[_task("any task")],
    )
    assert detect_task_violates_must_not(ec) == []


def test_task_with_empty_task_text_skipped() -> None:
    ec = _ec(
        must_not=[_c("Calculate tax.")],
        tasks=[_task("")],
    )
    assert detect_task_violates_must_not(ec) == []


def test_task_one_task_matches_multiple_must_nots() -> None:
    """A task can violate several rules. Emit one conflict per match."""
    ec = _ec(
        must_not=[
            _c("Calculate tax.", source="app.sdd"),
            _c("Tax", source="module.sdd"),  # substring of "Calculate tax"
        ],
        tasks=[_task("Calculate tax helper")],
    )
    conflicts = detect_task_violates_must_not(ec)
    assert len(conflicts) == 2


# ---------------------------------------------------------------------------
# must_vs_must_not
# ---------------------------------------------------------------------------


def test_mvmn_empty_inputs_no_conflicts() -> None:
    assert detect_must_vs_must_not(_ec()) == []


def test_mvmn_byte_identical_across_specs_is_conflict() -> None:
    """Different specs assert opposite rules with the same text — clearly
    contradictory."""
    ec = _ec(
        must=[_c("Persist after success.", source="app.sdd", line=4)],
        must_not=[_c("Persist after success.", source="service.sdd", line=10)],
    )
    conflicts = detect_must_vs_must_not(ec)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind == "must_vs_must_not"
    # rule_a = Must (must-do), rule_b = Must not (prohibition).
    assert c.rule_a.source == "app.sdd"
    assert c.rule_a.line == 4
    assert c.rule_b.source == "service.sdd"
    assert c.rule_b.line == 10


def test_mvmn_intra_spec_contradiction_still_flagged() -> None:
    """A single spec writing both `Must: X` and `Must not: X` is legal-weird
    but a real contradiction. We flag it (validate_spec might also flag it
    separately as a quality issue)."""
    ec = _ec(
        must=[_c("Validate input.", source="spec.sdd", line=4)],
        must_not=[_c("Validate input.", source="spec.sdd", line=10)],
    )
    conflicts = detect_must_vs_must_not(ec)
    assert len(conflicts) == 1
    assert conflicts[0].rule_a.line == 4
    assert conflicts[0].rule_b.line == 10


def test_mvmn_different_text_no_conflict() -> None:
    ec = _ec(
        must=[_c("Do X.")],
        must_not=[_c("Do Y.")],
    )
    assert detect_must_vs_must_not(ec) == []


def test_mvmn_case_sensitive_match() -> None:
    """Byte-identical = case-sensitive. `Validate` ≠ `validate`."""
    ec = _ec(
        must=[_c("Validate input.")],
        must_not=[_c("validate input.")],
    )
    assert detect_must_vs_must_not(ec) == []


def test_mvmn_empty_must_with_must_not_no_conflict() -> None:
    ec = _ec(must_not=[_c("X.")])
    assert detect_must_vs_must_not(ec) == []


def test_mvmn_empty_must_not_with_must_no_conflict() -> None:
    ec = _ec(must=[_c("X.")])
    assert detect_must_vs_must_not(ec) == []


def test_mvmn_multiple_pairs() -> None:
    """Multiple text overlaps emit one conflict per pair."""
    ec = _ec(
        must=[_c("Rule A."), _c("Rule B.")],
        must_not=[_c("Rule A."), _c("Rule B.")],
    )
    conflicts = detect_must_vs_must_not(ec)
    assert len(conflicts) == 2
    assert {c.rule_a.rule for c in conflicts} == {"Rule A.", "Rule B."}


def test_mvmn_provenance_preserved() -> None:
    ec = _ec(
        must=[_c("Rule.", source="app.sdd", line=42)],
        must_not=[_c("Rule.", source="leaf.sdd", line=99)],
    )
    conflict = detect_must_vs_must_not(ec)[0]
    assert conflict.rule_a.source == "app.sdd"
    assert conflict.rule_a.line == 42
    assert conflict.rule_b.source == "leaf.sdd"
    assert conflict.rule_b.line == 99
