"""Cross-spec validation rules (DESIGN.md §5.7, deferred from PR 5 to PR 7).

These three rules light up when ``validate_spec`` is called with
``check_inheritance=True`` and a ``repo_root``. They look *past* the
single file under validation, at the rules it inherits from its ancestor
specs:

  * ``DUPLICATE_PARENT_RULE``   — the spec restates an ancestor's
    ``Must`` / ``Must not`` byte-identically (drift risk).
  * ``CONFLICTING_INHERITANCE`` — the spec's ``Depends on:`` pulls in
    something an ancestor ``Forbids:``.
  * ``TASK_VIOLATES_MUSTNOT``   — one of the spec's tasks mechanically
    restates a ``Must not:`` somewhere in the chain (string-level; false
    positives accepted, hence ``warning`` not ``error``).

All three are **warnings** — see the per-kind notes in
:mod:`specdd_mcp.operations.conflicts`, which already implements the
mechanical detection these rules surface.

Design note — one rule, three codes (diverges from ``single_file.py``'s
one-rule-one-code convention). Every cross-spec finding is derived from a
*single* merged-chain computation (:func:`build_effective_constraints`,
which resolves the chain and expands write-scope globs against the
filesystem). Splitting the three codes into three registry entries would
re-resolve and re-merge the same chain three times per ``validate_spec``
call. Instead one orchestrator rule (:func:`check_inheritance_conflicts`)
does the I/O once and hands the merged view to the pure mapper
:func:`conflicts_to_issues`, which stays trivially unit-testable on a
hand-built :class:`EffectiveConstraints`.

The runner contract is fixed by :mod:`specdd_mcp.operations.validation`:
a cross-spec rule is ``(ParsedSpec, Path | None) -> list[ValidationIssue]``
and the runner only invokes it when ``check_inheritance`` is ``True``.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.merge import build_effective_constraints
from specdd_mcp.operations.validation.types import CrossSpecRule
from specdd_mcp.parser.resolve_chain import resolve_spec_chain
from specdd_mcp.types import (
    Conflict,
    ConflictKind,
    EffectiveConstraints,
    Err,
    ParsedSpec,
    ValidationCode,
    ValidationIssue,
)

__all__ = [
    "CROSS_SPEC_RULES",
    "check_inheritance_conflicts",
    "conflicts_to_issues",
]


#: The three conflict kinds ``validate_spec`` surfaces, mapped to their
#: stable validation codes. ``must_vs_must_not`` is intentionally absent —
#: ``build_effective_constraints`` still detects it for
#: ``get_effective_constraints``, but DESIGN §5.7 does not list it as a
#: ``validate_spec`` rule, so it never becomes a ``ValidationIssue``.
_KIND_TO_CODE: dict[ConflictKind, ValidationCode] = {
    "duplicate_parent_rule": "DUPLICATE_PARENT_RULE",
    "depends_on_vs_forbids": "CONFLICTING_INHERITANCE",
    "task_violates_must_not": "TASK_VIOLATES_MUSTNOT",
}


def _message_for(code: ValidationCode, conflict: Conflict) -> str:
    """Human-readable message for a mapped conflict.

    ``rule_a`` is the local/violator side (the spec under validation);
    ``rule_b`` is the inherited rule it ties to. Both carry ``source`` +
    ``line`` provenance so the message can quote the ancestor exactly.
    """
    rule_a, rule_b = conflict.rule_a, conflict.rule_b
    where = f"{rule_b.source}:{rule_b.line}"
    if code == "DUPLICATE_PARENT_RULE":
        return (
            f"`{rule_a.rule}` is restated verbatim from an ancestor "
            f"({where}). Write each rule once in the spec that owns it and "
            f"let inheritance carry it down."
        )
    if code == "CONFLICTING_INHERITANCE":
        return (
            f"`Depends on: {rule_a.rule}` conflicts with an inherited "
            f"`Forbids: {rule_b.rule}` ({where})."
        )
    # TASK_VIOLATES_MUSTNOT
    return (
        f"Task `{rule_a.rule}` may violate the inherited "
        f"`Must not: {rule_b.rule}` ({where}). Mechanical match — review "
        f"before acting."
    )


def conflicts_to_issues(
    constraints: EffectiveConstraints,
) -> list[ValidationIssue]:
    """Map a merged chain's conflicts to ``validate_spec`` warnings.

    Pure function — no I/O. Keeps only the conflicts where the spec under
    validation is the **violator** (``rule_a.source == constraints.target``).
    A conflict whose ``rule_a`` belongs to some *other* descendant in the
    chain is that spec's problem, not this one's, and would be surfaced when
    *it* is validated. Conflicts of an unmapped kind (``must_vs_must_not``)
    are skipped.

    Each emitted issue points ``line`` at the local rule and
    ``related_spec`` / ``related_line`` at the inherited rule, so the slash
    command can quote both sides.
    """
    issues: list[ValidationIssue] = []
    for conflict in constraints.conflicts:
        code = _KIND_TO_CODE.get(conflict.kind)
        if code is None:
            continue
        if conflict.rule_a.source != constraints.target:
            continue
        issues.append(
            ValidationIssue(
                severity="warning",
                code=code,
                message=_message_for(code, conflict),
                line=conflict.rule_a.line,
                related_spec=conflict.rule_b.source,
                related_line=conflict.rule_b.line,
            )
        )
    return issues


def check_inheritance_conflicts(
    spec: ParsedSpec, repo_root: Path | None
) -> list[ValidationIssue]:
    """Resolve ``spec``'s chain and surface its inherited-rule conflicts.

    The single registered cross-spec rule. Returns ``[]`` (no findings,
    never raises) when cross-spec analysis can't run:

      * ``repo_root is None`` — caller asked for inheritance without a root
        to anchor the chain. Nothing to inherit from.
      * the chain can't be resolved — e.g. the spec was validated from raw
        ``content`` (its ``path`` isn't a real file), or the target sits
        outside ``repo_root``. Cross-spec checks degrade silently rather
        than failing the whole ``validate_spec`` call; the single-file
        rules still ran.

    On success it builds the merged chain once and delegates the mapping to
    :func:`conflicts_to_issues`.
    """
    if repo_root is None:
        return []
    chain_result = resolve_spec_chain(spec.path, repo_root=str(repo_root))
    if isinstance(chain_result, Err):
        return []
    constraints = build_effective_constraints(chain_result.data, repo_root)
    return conflicts_to_issues(constraints)


#: Cross-spec rule registry. One orchestrator entry (see module docstring
#: for why it isn't three). The runner appends these to the single-file
#: findings only when ``check_inheritance`` is ``True``.
CROSS_SPEC_RULES: list[CrossSpecRule] = [check_inheritance_conflicts]
