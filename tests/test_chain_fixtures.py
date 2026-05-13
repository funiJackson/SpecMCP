"""Tests for resolve_spec_chain against committed multi-file fixtures.

The directories under ``tests/fixtures/chains/`` are realistic SpecDD trees
with ``.specdd/`` markers and multi-level structures. They serve two roles:

1. A test corpus that exercises the chain resolver against shapes that
   would be tedious to construct inline via ``tmp_path``.
2. A documentation corpus — anyone can ``find tests/fixtures/chains/ -name
   '*.sdd'`` to see what a realistic SpecDD project layout looks like.

Inline algorithmic tests (input validation, error paths, edge cases) stay in
``test_resolve_chain.py``.
"""

from __future__ import annotations

from specdd_mcp.parser import resolve_spec_chain
from specdd_mcp.types import Ok
from tests.conftest import CHAINS_DIR

# ---------------------------------------------------------------------------
# simple_3_level: app → module → service
# ---------------------------------------------------------------------------


def test_simple_3_level_chain_root_to_leaf() -> None:
    target = (
        CHAINS_DIR
        / "simple_3_level"
        / "src"
        / "billing"
        / "services"
        / "invoice.ts"
    )
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)

    assert [s.name for s in result.data.chain] == [
        "Billing Platform",
        "Billing Module",
        "Invoice Service",
    ]
    assert [s.level for s in result.data.chain] == ["app", "module", "service"]
    assert result.data.nearest is not None
    assert result.data.nearest.name == "Invoice Service"
    # Repo-relative POSIX paths.
    assert [s.path for s in result.data.chain] == [
        "app.sdd",
        "src/billing/module.sdd",
        "src/billing/services/invoice.sdd",
    ]


def test_simple_3_level_chain_target_is_sdd_file_itself() -> None:
    """When target is a .sdd file (not the .ts code), the chain still resolves
    correctly because the target's containing directory is what matters."""
    target = (
        CHAINS_DIR
        / "simple_3_level"
        / "src"
        / "billing"
        / "services"
        / "invoice.sdd"
    )
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == [
        "Billing Platform",
        "Billing Module",
        "Invoice Service",
    ]


def test_simple_3_level_chain_target_is_directory() -> None:
    """Target as a directory: walk to that directory, do not descend further.

    With target=src/billing/, the chain stops at the billing dir. The
    services/ subdir's invoice.sdd is NOT included.
    """
    target = CHAINS_DIR / "simple_3_level" / "src" / "billing"
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert [s.name for s in result.data.chain] == [
        "Billing Platform",
        "Billing Module",
    ]
    assert result.data.nearest is not None
    assert result.data.nearest.name == "Billing Module"


def test_simple_3_level_chain_with_explicit_repo_root() -> None:
    """Passing repo_root explicitly works the same as auto-detection."""
    repo = CHAINS_DIR / "simple_3_level"
    result = resolve_spec_chain(
        target="src/billing/services/invoice.ts",
        repo_root=str(repo),
    )
    assert isinstance(result, Ok)
    assert len(result.data.chain) == 3


def test_simple_3_level_chain_inheritance_content() -> None:
    """The chain's specs actually carry their parsed content (not just names).

    This is what get_effective_constraints (PR 3) will reach into to build
    the merged view.
    """
    target = (
        CHAINS_DIR
        / "simple_3_level"
        / "src"
        / "billing"
        / "services"
        / "invoice.ts"
    )
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)

    app, module, service = result.data.chain
    # App-level rules apply to everything below.
    assert app.must is not None
    assert "Represent money as integer minor units." in app.must
    # Module-level forbids.
    assert module.forbids == ["stripe"]
    # Service-level local rules + tasks.
    assert service.must_not is not None
    assert "Call Stripe directly." in service.must_not
    assert service.tasks is not None
    assert len(service.tasks) == 2


# ---------------------------------------------------------------------------
# multiple_in_one_dir: module + feature + service all in the same directory
# ---------------------------------------------------------------------------


def test_multiple_in_one_dir_ordered_by_level_precedence() -> None:
    """Three specs in one directory should appear in level-precedence order
    (module → feature → service), NOT alphabetical (which would be
    feature → invoice.service → module)."""
    target = (
        CHAINS_DIR / "multiple_in_one_dir" / "src" / "billing" / "code.ts"
    )
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)

    assert [s.name for s in result.data.chain] == [
        "Billing Module",
        "Invoice Feature",
        "Invoice Service",
    ]
    assert [s.level for s in result.data.chain] == [
        "module",
        "feature",
        "service",
    ]


def test_multiple_in_one_dir_nearest_is_highest_precedence() -> None:
    target = (
        CHAINS_DIR / "multiple_in_one_dir" / "src" / "billing" / "code.ts"
    )
    result = resolve_spec_chain(target=str(target))
    assert isinstance(result, Ok)
    assert result.data.nearest is not None
    assert result.data.nearest.name == "Invoice Service"
    assert result.data.nearest.level == "service"


# ---------------------------------------------------------------------------
# Smoke: every fixture parses and produces a chain
# ---------------------------------------------------------------------------


def test_all_chain_fixtures_resolve_without_error() -> None:
    """Every committed chain fixture should resolve cleanly when its sample
    target is asked for. Catches regressions where a fixture content drifts
    away from what the parser/resolver can handle."""
    targets = [
        CHAINS_DIR
        / "simple_3_level"
        / "src"
        / "billing"
        / "services"
        / "invoice.ts",
        CHAINS_DIR / "multiple_in_one_dir" / "src" / "billing" / "code.ts",
    ]
    for target in targets:
        result = resolve_spec_chain(target=str(target))
        assert isinstance(result, Ok), f"{target} did not resolve cleanly"
        assert result.data.malformed == [], (
            f"{target}: unexpected malformed specs: {result.data.malformed}"
        )
        assert result.warnings == [], (
            f"{target}: unexpected warnings: {result.warnings}"
        )
