"""Tests for :func:`specdd_mcp.operations.merge.build_effective_constraints`.

C5 only covers the rule arrays + tasks. Write-scope (C6), other aggregations
(C7), and the conflict detectors (C8-C11) get their own test files.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.merge import build_effective_constraints
from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.types import Ok


def _make_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specdd").mkdir(exist_ok=True)
    return tmp_path


def _resolve(target: Path) -> Ok:
    """Helper: resolve_spec_chain and assert success."""
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    return result


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------


def test_empty_chain_returns_empty_constraints(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    code = repo / "code.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.must == []
    assert constraints.must_not == []
    assert constraints.forbids == []
    assert constraints.depends_on == []
    assert constraints.tasks == []
    assert constraints.target == "code.py"


# ---------------------------------------------------------------------------
# Rule aggregation
# ---------------------------------------------------------------------------


def test_single_spec_rules_carry_provenance(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n"      # line 1
        "\n"
        "Must:\n"          # line 3
        "  Rule one.\n"    # line 4
        "  Rule two.\n"    # line 5
        "\n"
        "Forbids:\n"       # line 7? actually line 7
        "  stripe\n"       # line 8
    )
    code = repo / "code.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert len(constraints.must) == 2
    assert constraints.must[0].rule == "Rule one."
    assert constraints.must[0].source == "app.sdd"
    assert constraints.must[0].line == 4
    assert constraints.must[1].line == 5

    assert len(constraints.forbids) == 1
    assert constraints.forbids[0].rule == "stripe"
    assert constraints.forbids[0].source == "app.sdd"
    assert constraints.forbids[0].line == 8


def test_must_not_and_depends_on_aggregated(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n"
        "\n"
        "Must not:\n"
        "  Call Stripe directly.\n"
        "  Calculate tax.\n"
        "\n"
        "Depends on:\n"
        "  InvoiceRepository\n"
        "  BillingProviderPort\n"
    )
    code = repo / "code.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert [c.rule for c in constraints.must_not] == [
        "Call Stripe directly.",
        "Calculate tax.",
    ]
    assert [c.rule for c in constraints.depends_on] == [
        "InvoiceRepository",
        "BillingProviderPort",
    ]


# ---------------------------------------------------------------------------
# Multi-spec chain — root → leaf order
# ---------------------------------------------------------------------------


def test_chain_aggregates_root_to_leaf(tmp_path: Path) -> None:
    """The merged rule list preserves chain order: root spec's rules first,
    then each successive spec's. This matches `/specc`'s mental model where
    the most-specific spec appears last."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nMust:\n  app rule.\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "module.sdd").write_text(
        "Spec: Module\n\nMust:\n  module rule.\n"
    )
    (repo / "src" / "code.py").write_text("x = 1\n")
    chain_result = _resolve(repo / "src" / "code.py")
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    # Root spec's rule comes first; module's rule comes after.
    assert [c.rule for c in constraints.must] == [
        "app rule.",
        "module rule.",
    ]
    assert [c.source for c in constraints.must] == [
        "app.sdd",
        "src/module.sdd",
    ]


def test_each_constraint_keeps_its_own_source(tmp_path: Path) -> None:
    """Rules from different specs must each carry THEIR spec's path, not
    just the leaf's."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n\nForbids:\n  stripe\n")
    (repo / "src").mkdir()
    (repo / "src" / "module.sdd").write_text(
        "Spec: Module\n\nForbids:\n  redis\n"
    )
    (repo / "src" / "code.py").write_text("x = 1\n")
    chain_result = _resolve(repo / "src" / "code.py")
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert [(c.rule, c.source) for c in constraints.forbids] == [
        ("stripe", "app.sdd"),
        ("redis", "src/module.sdd"),
    ]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def test_tasks_aggregated_across_chain_with_source(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nTasks:\n  [ ] root task\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "module.sdd").write_text(
        "Spec: Module\n\nTasks:\n  [x] module task\n"
    )
    (repo / "src" / "code.py").write_text("x = 1\n")
    chain_result = _resolve(repo / "src" / "code.py")
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert [(t.text, t.source) for t in constraints.tasks] == [
        ("root task", "app.sdd"),
        ("module task", "src/module.sdd"),
    ]
    # ParsedTask fields preserved (indent, raw, state).
    assert constraints.tasks[0].state == "open"
    assert constraints.tasks[1].state == "done"


def test_spec_with_no_tasks_contributes_nothing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "no_tasks.sdd").write_text("Spec: NT\n\nMust:\n  rule.\n")
    code = repo / "code.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.tasks == []
    assert len(constraints.must) == 1


# ---------------------------------------------------------------------------
# Continuation lines: line anchors at bullet start
# ---------------------------------------------------------------------------


def test_continuation_bullet_line_anchors_at_start(tmp_path: Path) -> None:
    """A multi-line Must rule (continuation indent) should carry the line
    number of where the bullet started, not where it ended."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n"        # 1
        "\n"               # 2
        "Must:\n"          # 3
        "  bullet one\n"   # 4 ← anchor
        "    spanning\n"   # 5 (continuation)
        "    lines\n"      # 6 (continuation)
        "  bullet two\n"   # 7
    )
    code = repo / "code.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert constraints.must[0].line == 4  # bullet one start
    assert constraints.must[1].line == 7  # bullet two start


# ---------------------------------------------------------------------------
# Target field
# ---------------------------------------------------------------------------


def test_target_field_propagates_from_chain(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    code = repo / "src" / "foo.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.target == "src/foo.py"


# ---------------------------------------------------------------------------
# Unfilled fields (will populate in later commits)
# ---------------------------------------------------------------------------


def test_only_conflicts_remain_unfilled(tmp_path: Path) -> None:
    """C5/C6/C7 cover all EffectiveConstraints fields except `conflicts`,
    which C8-C11 fill. A spec with no scope/read/done_when/references
    sections produces empty arrays for those — but `chain_summary` always
    has one entry per spec in the chain."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nMust:\n  rule.\n")
    code = repo / "code.py"
    code.write_text("x = 1\n")
    chain_result = _resolve(code)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    # No Owns / Can modify / Done when / Can read / References in the spec.
    assert constraints.effective_write_scope == []
    assert constraints.write_authority_source is None
    assert constraints.effective_read_scope == []
    assert constraints.done_when == []
    assert constraints.references == []
    # chain_summary always populated — one entry per spec.
    assert len(constraints.chain_summary) == 1
    assert constraints.chain_summary[0].path == "a.sdd"
    # Only conflicts truly empty pending C8-C11.
    assert constraints.conflicts == []


# ---------------------------------------------------------------------------
# C6: effective_write_scope
# ---------------------------------------------------------------------------


def test_owns_expands_to_matching_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "billing").mkdir()
    (repo / "src" / "billing" / "invoice.ts").write_text("// code\n")
    (repo / "src" / "billing" / "invoice.test.ts").write_text("// test\n")
    (repo / "src" / "billing" / "invoice.sdd").write_text(
        "Spec: Invoice\n"
        "\n"
        "Owns:\n"
        "  invoice.ts\n"
        "  invoice.test.ts\n"
    )
    target = repo / "src" / "billing" / "invoice.ts"
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert len(constraints.effective_write_scope) == 2
    entries = sorted(constraints.effective_write_scope, key=lambda e: e.pattern)
    assert entries[0].pattern == "invoice.test.ts"
    assert entries[0].matches == ["src/billing/invoice.test.ts"]
    assert entries[0].source == "src/billing/invoice.sdd"
    assert entries[1].pattern == "invoice.ts"
    assert entries[1].matches == ["src/billing/invoice.ts"]


def test_can_modify_contributes_entries(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.ts").write_text("")
    (repo / "src" / "spec.sdd").write_text(
        "Spec: S\n\nCan modify:\n  a.ts\n"
    )
    target = repo / "src" / "a.ts"
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert len(constraints.effective_write_scope) == 1
    entry = constraints.effective_write_scope[0]
    assert entry.pattern == "a.ts"
    assert entry.matches == ["src/a.ts"]
    assert entry.source == "src/spec.sdd"


def test_owns_glob_pattern_expands(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "billing").mkdir()
    (repo / "src" / "billing" / "a.ts").write_text("")
    (repo / "src" / "billing" / "b.ts").write_text("")
    (repo / "src" / "billing" / "c.py").write_text("")
    (repo / "src" / "module.sdd").write_text(
        "Spec: M\n\nOwns:\n  billing/*.ts\n"
    )
    target = repo / "src" / "billing" / "a.ts"
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    entry = constraints.effective_write_scope[0]
    assert entry.pattern == "billing/*.ts"
    assert set(entry.matches) == {"src/billing/a.ts", "src/billing/b.ts"}


def test_write_authority_source_is_nearest_spec_with_scope(tmp_path: Path) -> None:
    """When multiple chain levels grant write scope, the leaf wins."""
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "billing").mkdir()
    (repo / "src" / "billing" / "invoice.ts").write_text("")
    # Root spec also has Owns:
    (repo / "app.sdd").write_text(
        "Spec: App\n\nOwns:\n  src/**/*.ts\n"
    )
    # Leaf spec has Owns: too — should win as write_authority_source.
    (repo / "src" / "billing" / "invoice.sdd").write_text(
        "Spec: Invoice\n\nOwns:\n  invoice.ts\n"
    )
    target = repo / "src" / "billing" / "invoice.ts"
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    # Both specs contribute entries.
    assert len(constraints.effective_write_scope) == 2
    # But the NEAREST spec with scope wins authority.
    assert constraints.write_authority_source == "src/billing/invoice.sdd"


def test_write_authority_source_skips_specs_without_scope(tmp_path: Path) -> None:
    """A middle spec with no Owns/Can modify shouldn't reset authority."""
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "billing").mkdir()
    (repo / "app.sdd").write_text(
        "Spec: App\n\nOwns:\n  **/*.ts\n"
    )
    # Middle spec has no scope sections at all.
    (repo / "src" / "module.sdd").write_text(
        "Spec: M\n\nMust:\n  rule.\n"
    )
    # No leaf spec with scope.
    target = repo / "src" / "billing" / "x.ts"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    # Authority stays at the root spec (the only one with scope).
    assert constraints.write_authority_source == "app.sdd"


def test_write_authority_source_none_when_no_spec_has_scope(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text("Spec: A\n\nMust:\n  rule.\n")
    target = repo / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.effective_write_scope == []
    assert constraints.write_authority_source is None


def test_write_scope_entries_carry_source_line(tmp_path: Path) -> None:
    """source_line points at the line of the Owns/Can modify bullet — the
    `/specc` body needs this to quote 'allowed by src/.../invoice.sdd:14'."""
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "invoice.ts").write_text("")
    (repo / "src" / "spec.sdd").write_text(
        "Spec: Inv\n"        # line 1
        "\n"                  # line 2
        "Owns:\n"             # line 3
        "  invoice.ts\n"      # line 4 ← source_line should be 4
    )
    target = repo / "src" / "invoice.ts"
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.effective_write_scope[0].source_line == 4


def test_owns_and_can_modify_both_aggregate(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "owned.ts").write_text("")
    (repo / "src" / "modifiable.ts").write_text("")
    (repo / "src" / "spec.sdd").write_text(
        "Spec: S\n"
        "\n"
        "Owns:\n"
        "  owned.ts\n"
        "\n"
        "Can modify:\n"
        "  modifiable.ts\n"
    )
    target = repo / "src" / "owned.ts"
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert len(constraints.effective_write_scope) == 2
    patterns = {e.pattern for e in constraints.effective_write_scope}
    assert patterns == {"owned.ts", "modifiable.ts"}


# ---------------------------------------------------------------------------
# C7: done_when, effective_read_scope, references, chain_summary
# ---------------------------------------------------------------------------


def test_done_when_aggregated_with_provenance(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n"          # 1
        "\n"                  # 2
        "Done when:\n"        # 3
        "  All tests pass.\n" # 4
        "  No drift.\n"       # 5
    )
    target = repo / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert len(constraints.done_when) == 2
    assert constraints.done_when[0].rule == "All tests pass."
    assert constraints.done_when[0].source == "a.sdd"
    assert constraints.done_when[0].line == 4
    assert constraints.done_when[1].line == 5


def test_effective_read_scope_comes_from_can_read(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n"
        "\n"
        "Can read:\n"
        "  ../models/*\n"
        "  ../ports/*\n"
    )
    target = repo / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert [c.rule for c in constraints.effective_read_scope] == [
        "../models/*",
        "../ports/*",
    ]
    assert all(c.source == "a.sdd" for c in constraints.effective_read_scope)


def test_references_carry_from_to_and_line(tmp_path: Path) -> None:
    """ReferenceEntry uses JSON key 'from' but Python attr 'from_' (reserved
    word). Both forms must work over MCP."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n"                          # 1
        "\n"                                  # 2
        "References:\n"                       # 3
        "  ../models/invoice.sdd\n"           # 4
        "  ../ports/billing-provider.sdd\n"   # 5
    )
    target = repo / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert len(constraints.references) == 2
    first = constraints.references[0]
    assert first.from_ == "a.sdd"
    assert first.to == "../models/invoice.sdd"
    assert first.line == 4
    second = constraints.references[1]
    assert second.to == "../ports/billing-provider.sdd"
    assert second.line == 5


def test_references_serialize_to_from_key_over_mcp() -> None:
    """`from_` Python attr → `from` JSON key (Pydantic alias). This is the
    shape Claude actually sees through MCP."""
    from specdd_mcp.types import ReferenceEntry

    entry = ReferenceEntry(**{"from": "a.sdd", "to": "b.sdd", "line": 3})
    blob = entry.model_dump_json(by_alias=True)
    assert '"from":"a.sdd"' in blob
    assert "from_" not in blob


def test_chain_summary_has_one_entry_per_spec(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text("Spec: App\n")
    (repo / "src").mkdir()
    (repo / "src" / "module.sdd").write_text("Spec: M\n")
    (repo / "src" / "billing").mkdir()
    (repo / "src" / "billing" / "service.sdd").write_text("Spec: S\n")
    target = repo / "src" / "billing" / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert [s.path for s in constraints.chain_summary] == [
        "app.sdd",
        "src/module.sdd",
        "src/billing/service.sdd",
    ]
    # Levels inferred from filename/path.
    levels = [s.level for s in constraints.chain_summary]
    assert levels[0] == "app"
    assert levels[1] == "module"


def test_chain_summary_empty_when_no_specs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    target = repo / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.chain_summary == []


def test_all_c7_fields_aggregate_across_chain(tmp_path: Path) -> None:
    """A multi-spec chain should aggregate done_when, can_read, and
    references from every spec, preserving root→leaf order."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nDone when:\n  app-level done.\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "module.sdd").write_text(
        "Spec: M\n\nCan read:\n  ../app/*\n"
        "\n"
        "References:\n  ../app.sdd\n"
    )
    target = repo / "src" / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert [c.rule for c in constraints.done_when] == ["app-level done."]
    assert [c.rule for c in constraints.effective_read_scope] == ["../app/*"]
    assert [r.from_ for r in constraints.references] == ["src/module.sdd"]
    assert [r.to for r in constraints.references] == ["../app.sdd"]


# ---------------------------------------------------------------------------
# C8: conflicts (depends_on_vs_forbids) surfaced through full pipeline
# ---------------------------------------------------------------------------


def test_depends_vs_forbids_conflict_surfaces_in_pipeline(tmp_path: Path) -> None:
    """A leaf spec's Depends on: stripe conflicts with the root spec's
    Forbids: stripe. The full merge → conflicts pipeline surfaces this."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nForbids:\n  stripe\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "service.sdd").write_text(
        "Spec: Service\n\nDepends on:\n  stripe\n"
    )
    target = repo / "src" / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    assert len(constraints.conflicts) == 1
    conflict = constraints.conflicts[0]
    assert conflict.kind == "depends_on_vs_forbids"
    # rule_a is the Depends on side; rule_b is the Forbids side.
    assert conflict.rule_a.rule == "stripe"
    assert conflict.rule_a.source == "src/service.sdd"
    assert conflict.rule_b.rule == "stripe"
    assert conflict.rule_b.source == "app.sdd"


def test_no_conflicts_when_no_overlap(tmp_path: Path) -> None:
    """A spec with no Depends on/Forbids overlap surfaces no conflicts."""
    repo = _make_repo(tmp_path)
    (repo / "a.sdd").write_text(
        "Spec: A\n\nForbids:\n  stripe\n\nDepends on:\n  redis\n"
    )
    target = repo / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)
    assert constraints.conflicts == []


def test_duplicate_parent_rule_surfaces_in_pipeline(tmp_path: Path) -> None:
    """A leaf spec restating a parent's Must rule byte-identically should
    fire `duplicate_parent_rule`. Real-spec end-to-end check."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nMust:\n  Validate input.\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "service.sdd").write_text(
        "Spec: S\n\nMust:\n  Validate input.\n"
    )
    target = repo / "src" / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    dupes = [c for c in constraints.conflicts if c.kind == "duplicate_parent_rule"]
    assert len(dupes) == 1
    # rule_a is the child duplicate, rule_b the ancestor original.
    assert dupes[0].rule_a.source == "src/service.sdd"
    assert dupes[0].rule_b.source == "app.sdd"


def test_task_violates_must_not_surfaces_in_pipeline(tmp_path: Path) -> None:
    """A task that mechanically restates a parent's Must not should fire
    `task_violates_must_not`. Treated as advisory by /specc, not a hard stop."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nMust not:\n  Call Stripe directly.\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "service.sdd").write_text(
        "Spec: S\n\nTasks:\n  [ ] Call Stripe directly from helper.\n"
    )
    target = repo / "src" / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    advisories = [
        c for c in constraints.conflicts if c.kind == "task_violates_must_not"
    ]
    assert len(advisories) == 1
    assert "Call Stripe directly" in advisories[0].rule_a.rule
    assert advisories[0].rule_a.source == "src/service.sdd"
    assert advisories[0].rule_b.source == "app.sdd"


def test_must_vs_must_not_surfaces_in_pipeline(tmp_path: Path) -> None:
    """A byte-identical Must / Must not pair (rare in practice) is surfaced
    as a `must_vs_must_not` conflict via the full pipeline."""
    repo = _make_repo(tmp_path)
    (repo / "app.sdd").write_text(
        "Spec: App\n\nMust:\n  Persist after success.\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "service.sdd").write_text(
        "Spec: S\n\nMust not:\n  Persist after success.\n"
    )
    target = repo / "src" / "code.py"
    target.write_text("")
    chain_result = _resolve(target)
    constraints = build_effective_constraints(chain_result.data, repo_root=repo)

    contradictions = [
        c for c in constraints.conflicts if c.kind == "must_vs_must_not"
    ]
    assert len(contradictions) == 1
    # rule_a = Must (the would-be action), rule_b = Must not (the override).
    assert contradictions[0].rule_a.source == "app.sdd"
    assert contradictions[0].rule_b.source == "src/service.sdd"
