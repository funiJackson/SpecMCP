"""Performance smoke tests for ``get_effective_constraints``.

PR3.md called for: 10-deep chain x ~50 rules/spec → return in < 200 ms.
This file generalizes that target into a small set of thresholds that
catch pathological slowness (an accidental ``O(n³)``) without flaking on
slow CI.

These are **smoke tests, not regression gates**. The thresholds are
generous (3-5x the typical dev-hardware timing). If they start tightening,
move the strict-threshold checks into a separate file marked ``slow`` and
keep the loose ones here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from specdd_mcp.server.tools import get_effective_constraints


def _make_deep_chain(
    tmp_path: Path,
    *,
    levels: int,
    rules_per_spec: int,
    unique_rules: bool = True,
) -> Path:
    """Build a nested ``levels``-deep tree. Each level has a spec with
    ``rules_per_spec`` Must rules. ``unique_rules=False`` makes every spec
    repeat the same rule — useful for stressing ``duplicate_parent_rule``.

    Returns the target ``code.py`` at the deepest level.
    """
    (tmp_path / ".specdd").mkdir()
    current = tmp_path
    for level in range(levels):
        rules_text = "\n".join(
            (
                f"  Rule {level}-{i}."
                if unique_rules
                else "  Same rule across levels."
            )
            for i in range(rules_per_spec)
        )
        spec = current / f"L{level}.sdd"
        spec.write_text(f"Spec: L{level}\n\nMust:\n{rules_text}\n")
        if level < levels - 1:
            current = current / f"L{level + 1}"
            current.mkdir()
    target = current / "code.py"
    target.write_text("")
    return target


# ---------------------------------------------------------------------------
# Primary target: 10-deep chain x 50 unique rules per spec
# ---------------------------------------------------------------------------


def test_10_deep_chain_50_rules_per_spec_under_500ms(tmp_path: Path) -> None:
    """The number PR3.md called out — 10 levels, ~50 rules each. Typical
    dev hardware runs this in ~20-50 ms; the 500 ms threshold is generous
    to absorb CI jitter. If it starts pushing 500 ms, something is wrong."""
    target = _make_deep_chain(tmp_path, levels=10, rules_per_spec=50)

    start = time.perf_counter()
    result = get_effective_constraints(target=str(target))
    elapsed = time.perf_counter() - start

    assert result["ok"] is True
    assert len(result["data"]["must"]) == 10 * 50
    assert elapsed < 0.5, (
        f"build_effective_constraints took {elapsed * 1000:.0f} ms "
        f"on a 10-deep x 50-rule chain — investigate"
    )


def test_10_deep_chain_provenance_is_complete(tmp_path: Path) -> None:
    """Every one of the 500 merged Constraints has a non-zero line number.
    Catches a regression where bullet_lines wiring drops for deep chains."""
    target = _make_deep_chain(tmp_path, levels=10, rules_per_spec=50)
    result = get_effective_constraints(target=str(target))
    assert result["ok"] is True
    lines = [c["line"] for c in result["data"]["must"]]
    assert all(line > 0 for line in lines), (
        "some Must constraints have line=0 — bullet_lines wiring broken"
    )


# ---------------------------------------------------------------------------
# Conflict detection scalability
# ---------------------------------------------------------------------------


def test_conflict_heavy_5_deep_chain_20_rules_under_1s(tmp_path: Path) -> None:
    """All 5 levels carry the same 20 rules (each rule text repeated within
    each spec). Every descendant rule pairs with every ancestor rule of the
    same text → ``duplicate_parent_rule`` cartesian explosion.

    Counting: level k has 20 rules, each pairs with 20 rules from each of
    its k path-ancestors. Sum across descendant levels:
        Σ (k=1..4) 20 rules x k ancestor specs x 20 rules-per-ancestor
        = 20 x 20 x (1 + 2 + 3 + 4)
        = 400 x 10
        = 4000 conflicts.

    Detector is O(n²) per section with n = 100 merged rules ⇒ ~5000
    pair comparisons. Well under 1 s on any reasonable hardware.
    """
    target = _make_deep_chain(
        tmp_path, levels=5, rules_per_spec=20, unique_rules=False
    )

    start = time.perf_counter()
    result = get_effective_constraints(target=str(target))
    elapsed = time.perf_counter() - start

    assert result["ok"] is True
    conflicts = result["data"]["conflicts"]
    # All conflicts must be the same kind (no other detector accidentally
    # fires on identical rules).
    assert all(c["kind"] == "duplicate_parent_rule" for c in conflicts)
    # See docstring for the count derivation.
    assert len(conflicts) == 4000

    assert elapsed < 1.0, (
        f"conflict-heavy build took {elapsed * 1000:.0f} ms "
        f"(target < 1 s) — duplicate_parent_rule detector may be slow"
    )


# ---------------------------------------------------------------------------
# Scaling smoke: linear in number of rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rules_per_spec", [10, 50, 100, 200])
def test_scaling_in_rules_is_not_pathological(
    tmp_path: Path,
    rules_per_spec: int,
) -> None:
    """At 5 levels, varying rules-per-spec from 10 → 200 should produce a
    bounded runtime growth. If runtime explodes super-linearly with rule
    count, there's an O(n³) accident somewhere. Threshold is intentionally
    loose: even 200 rules x 5 levels x all-unique should finish well under
    500 ms; this catches catastrophic regressions, not micro-perf changes."""
    target = _make_deep_chain(
        tmp_path,
        levels=5,
        rules_per_spec=rules_per_spec,
        unique_rules=True,
    )
    start = time.perf_counter()
    result = get_effective_constraints(target=str(target))
    elapsed = time.perf_counter() - start

    assert result["ok"] is True
    assert len(result["data"]["must"]) == 5 * rules_per_spec
    assert elapsed < 0.5, (
        f"{rules_per_spec} rules x 5 levels took {elapsed * 1000:.0f} ms"
    )
