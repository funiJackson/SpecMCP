"""Full-pipeline integration tests for ``get_effective_constraints``.

Walks the entire stack — file system → lexer → parser → chain resolver →
merge → conflict detectors — against:

1. The ``simple_3_level`` chain fixture (PR 2): comprehensive field-by-field
   assertions to lock in the pipeline output shape.
2. The ``multiple_in_one_dir`` fixture (PR 2): verifies the same-directory
   precedence rule survives through ``effective_write_scope`` and
   ``write_authority_source``.
3. The ``specdd/benchmark`` corpus (cloned via the conftest fixture): smoke
   check that every benchmark spec resolves cleanly with zero conflicts.

If any of these fail, something on the path from ``.sdd`` text to
``EffectiveConstraints`` regressed. Unit tests for individual stages live
in their own files; this is the contract test for the whole chain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.operations.merge import build_effective_constraints
from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.types import EffectiveConstraints, Ok
from tests.conftest import CHAINS_DIR


def _ec_for(target: Path, repo_root: Path) -> EffectiveConstraints:
    """Run the full pipeline (chain + merge) and return EffectiveConstraints."""
    chain_result = resolve_spec_chain(target=str(target))
    assert isinstance(chain_result, Ok), (
        f"chain resolution failed for {target}: {chain_result!r}"
    )
    return build_effective_constraints(
        chain_result.data,
        repo_root=repo_root,
    )


# ---------------------------------------------------------------------------
# simple_3_level: comprehensive field-by-field check
# ---------------------------------------------------------------------------


def test_simple_3_level_chain_summary() -> None:
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)
    assert ec.target == "src/billing/services/invoice.ts"
    assert [s.path for s in ec.chain_summary] == [
        "app.sdd",
        "src/billing/module.sdd",
        "src/billing/services/invoice.sdd",
    ]
    assert [s.level for s in ec.chain_summary] == ["app", "module", "service"]


def test_simple_3_level_must_rules_aggregate() -> None:
    """Must rules from all three specs surface in chain (root → leaf) order,
    each carrying its source path + line."""
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)

    rules = [(c.rule, c.source) for c in ec.must]
    assert rules == [
        # app.sdd
        ("Represent money as integer minor units.", "app.sdd"),
        ("Access persistence only through repositories.", "app.sdd"),
        # src/billing/module.sdd
        ("Normalize provider errors before they leave the module.",
         "src/billing/module.sdd"),
        # src/billing/services/invoice.sdd
        ("Validate input before provider calls.",
         "src/billing/services/invoice.sdd"),
        ("Persist invoice after provider success.",
         "src/billing/services/invoice.sdd"),
    ]
    # Lines populated for every rule (not 0).
    assert all(c.line > 0 for c in ec.must)


def test_simple_3_level_must_not_rules_aggregate() -> None:
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)

    assert [c.rule for c in ec.must_not] == [
        "Put business logic in UI components.",
        "Use floating point numbers for money.",
        "Call Stripe directly.",
        "Calculate tax.",
    ]
    sources = {c.source for c in ec.must_not}
    assert sources == {"app.sdd", "src/billing/services/invoice.sdd"}


def test_simple_3_level_forbids_and_depends_on() -> None:
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)

    assert [c.rule for c in ec.forbids] == ["stripe"]
    assert ec.forbids[0].source == "src/billing/module.sdd"

    assert [c.rule for c in ec.depends_on] == [
        "InvoiceRepository",
        "BillingProviderPort",
    ]


def test_simple_3_level_write_scope_with_glob_expansion() -> None:
    """`Owns: src/billing/*` (module) expands; `Owns: invoice.ts` (service)
    is a literal match. write_authority_source is the leaf with Owns."""
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)

    patterns = [(e.pattern, e.source) for e in ec.effective_write_scope]
    # Module's `src/billing/*` glob plus service's two literal patterns.
    assert ("src/billing/*", "src/billing/module.sdd") in patterns
    assert ("invoice.ts", "src/billing/services/invoice.sdd") in patterns
    assert ("invoice.test.ts", "src/billing/services/invoice.sdd") in patterns

    # invoice.ts matches in service's directory.
    invoice_entry = next(
        e for e in ec.effective_write_scope if e.pattern == "invoice.ts"
    )
    assert "src/billing/services/invoice.ts" in invoice_entry.matches

    # Leaf wins write_authority_source.
    assert ec.write_authority_source == "src/billing/services/invoice.sdd"


def test_simple_3_level_tasks_with_source() -> None:
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)

    assert [t.source for t in ec.tasks] == [
        "src/billing/services/invoice.sdd",
        "src/billing/services/invoice.sdd",
    ]
    assert [t.id for t in ec.tasks] == ["#1", "#2"]
    assert all(t.state == "open" for t in ec.tasks)


def test_simple_3_level_no_conflicts() -> None:
    """The canonical chain fixture is clean — no detector should fire."""
    repo = CHAINS_DIR / "simple_3_level"
    target = repo / "src" / "billing" / "services" / "invoice.ts"
    ec = _ec_for(target, repo)
    assert ec.conflicts == []


# ---------------------------------------------------------------------------
# multiple_in_one_dir: same-directory precedence survives end-to-end
# ---------------------------------------------------------------------------


def test_multiple_in_one_dir_write_authority_is_service() -> None:
    """Among module + feature + service in one directory, only the service
    spec has Owns: code.ts — it wins write_authority_source."""
    repo = CHAINS_DIR / "multiple_in_one_dir"
    target = repo / "src" / "billing" / "code.ts"
    ec = _ec_for(target, repo)
    assert ec.write_authority_source == "src/billing/invoice.service.sdd"


def test_multiple_in_one_dir_chain_summary_order() -> None:
    repo = CHAINS_DIR / "multiple_in_one_dir"
    target = repo / "src" / "billing" / "code.ts"
    ec = _ec_for(target, repo)
    # Module → feature → service order per the SpecLevel precedence table.
    assert [s.level for s in ec.chain_summary] == ["module", "feature", "service"]


# ---------------------------------------------------------------------------
# Benchmark corpus: pipeline runs cleanly on every spec
# ---------------------------------------------------------------------------


def test_benchmark_specs_produce_clean_effective_constraints(
    benchmark_repo: Path,
) -> None:
    """Every ``.sdd`` in the benchmark corpus, used as a target, produces an
    EffectiveConstraints without crashing. This is a smoke test — it would
    catch any pipeline regression that breaks on real-world specs."""
    specs = sorted(
        p for p in benchmark_repo.rglob("*.sdd")
        if ".git" not in p.parts and not p.name.startswith("._")
    )
    assert specs, "expected at least one .sdd in benchmark"

    failures: list[str] = []
    for spec_path in specs:
        chain_result = resolve_spec_chain(target=str(spec_path))
        if not isinstance(chain_result, Ok):
            failures.append(f"{spec_path}: chain resolution failed")
            continue
        ec = build_effective_constraints(
            chain_result.data,
            repo_root=benchmark_repo,
        )
        # Sanity: chain_summary has at least one entry for every reachable
        # target — the target's own spec at minimum.
        if not ec.chain_summary:
            failures.append(f"{spec_path}: empty chain_summary")

    if failures:
        pytest.fail("benchmark pipeline regressions:\n" + "\n".join(failures))


def test_benchmark_specs_produce_zero_high_signal_conflicts(
    benchmark_repo: Path,
) -> None:
    """The benchmark corpus is canonical SpecDD. If a high-signal detector
    (anything except task_violates_must_not, which is advisory) fires here,
    either the corpus has a real bug or our detectors are over-eager."""
    specs = sorted(
        p for p in benchmark_repo.rglob("*.sdd")
        if ".git" not in p.parts and not p.name.startswith("._")
    )
    high_signal_kinds = {
        "depends_on_vs_forbids",
        "duplicate_parent_rule",
        "must_vs_must_not",
    }
    surprises: list[str] = []
    for spec_path in specs:
        chain_result = resolve_spec_chain(target=str(spec_path))
        if not isinstance(chain_result, Ok):
            continue
        ec = build_effective_constraints(
            chain_result.data,
            repo_root=benchmark_repo,
        )
        for conflict in ec.conflicts:
            if conflict.kind in high_signal_kinds:
                surprises.append(
                    f"{spec_path}: {conflict.kind} between "
                    f"{conflict.rule_a.source}:{conflict.rule_a.line} and "
                    f"{conflict.rule_b.source}:{conflict.rule_b.line}"
                )

    if surprises:
        pytest.fail(
            "benchmark surfaced high-signal conflicts (corpus has a real "
            "bug, or detectors are over-eager):\n" + "\n".join(surprises)
        )
