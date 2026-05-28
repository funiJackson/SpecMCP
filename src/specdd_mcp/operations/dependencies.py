"""``check_dependencies``: proposed deps vs the chain's prohibitions (§6.1).

Given a target and a list of dependencies the caller is about to introduce
(module names, import paths), this surfaces every dependency that collides
with a ``Forbids:`` or ``Must not:`` rule inherited from the target's spec
chain — each with full ``Constraint`` provenance so the caller can quote the
rule's ``source:line``.

Two matchers, split by section because the two sections carry differently
shaped text:

  * **Forbids:** entries are short dependency names (``stripe``, ``lodash``).
    A proposed dep violates one when the forbidden name is contained in it:
    ``forbid.rule in dependency``. This is byte-for-byte the same rule the
    :func:`~specdd_mcp.operations.conflicts.detect_depends_on_vs_forbids`
    conflict detector uses, so ``Depends on:`` and ``check_dependencies``
    never disagree about what "forbidden" means.

  * **Must not:** entries are free-text behavioral rules (``Must not: use the
    legacy auth module``). A dep violates one when its name appears inside the
    rule: ``dependency in must_not.rule`` (case-insensitive). Mechanical and
    higher false-positive — a short dep name can appear incidentally — so,
    like ``task_violates_must_not``, callers treat these as advisory.

The orchestrator resolves the chain and merges it with the same
``resolve_spec_chain`` → ``build_effective_constraints`` path as
``get_effective_constraints``; the matcher itself is pure and unit-testable on
a hand-built :class:`EffectiveConstraints`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from specdd_mcp.operations.merge import build_effective_constraints
from specdd_mcp.parser.resolve_chain import resolve_spec_chain
from specdd_mcp.types import (
    DependencyViolation,
    EffectiveConstraints,
    Err,
    Ok,
)

CheckDependenciesResult: TypeAlias = "Ok[list[DependencyViolation]] | Err"


def find_dependency_violations(
    constraints: EffectiveConstraints,
    dependencies: list[str],
) -> list[DependencyViolation]:
    """Match ``dependencies`` against ``constraints``' prohibitions.

    Pure function — no I/O. For each dependency, emits one violation per
    matching ``Forbids:`` entry (``forbid.rule in dependency``) and one per
    matching ``Must not:`` entry (``dependency in must_not.rule``,
    case-insensitive). A dependency that trips several rules yields several
    violations. Output is sorted by ``(dependency, kind, source, line)`` for
    stable ordering.
    """
    violations: list[DependencyViolation] = []
    for dependency in dependencies:
        dep_lower = dependency.lower()
        for forbid in constraints.forbids:
            if forbid.rule and forbid.rule in dependency:
                violations.append(
                    DependencyViolation(
                        dependency=dependency,
                        kind="forbids",
                        constraint=forbid,
                    )
                )
        for must_not in constraints.must_not:
            if dep_lower and dep_lower in must_not.rule.lower():
                violations.append(
                    DependencyViolation(
                        dependency=dependency,
                        kind="must_not",
                        constraint=must_not,
                    )
                )

    violations.sort(
        key=lambda v: (
            v.dependency,
            v.kind,
            v.constraint.source,
            v.constraint.line,
        )
    )
    return violations


def check_dependencies(
    target: str,
    *,
    proposed_dependencies: list[str],
    repo_root: str | None = None,
) -> CheckDependenciesResult:
    """Check ``proposed_dependencies`` against ``target``'s inherited rules.

    See DESIGN.md §6.1 for the contract.

    Args:
        target: Repo-relative (with ``repo_root``) or absolute path to the
            file/dir the dependencies are being added to. Resolved through the
            same chain walk as ``get_effective_constraints``.
        proposed_dependencies: Module names / import paths to vet. An empty
            list returns ``Ok([])``.
        repo_root: Absolute repo root; auto-detected from ``target`` when
            omitted.

    Returns:
        :class:`Ok` wrapping the (possibly empty) list of
        :class:`DependencyViolation`, carrying the chain-resolution warnings.

    Returns :class:`Err` (propagated from ``resolve_spec_chain``):
      - ``INVALID_INPUT`` — relative ``target`` without ``repo_root``
      - ``NOT_FOUND``     — target missing, or no repo root detectable
      - ``OUT_OF_SCOPE``  — target resolves outside ``repo_root``
    """
    chain_result = resolve_spec_chain(target=target, repo_root=repo_root)
    if isinstance(chain_result, Err):
        return chain_result

    constraints = build_effective_constraints(
        chain_result.data,
        repo_root=Path(chain_result.data.repo_root),
    )
    violations = find_dependency_violations(constraints, proposed_dependencies)
    return Ok(data=violations, warnings=chain_result.warnings)
