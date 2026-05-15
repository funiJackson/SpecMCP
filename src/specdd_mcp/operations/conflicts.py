"""Conflict detectors for :class:`EffectiveConstraints`.

The four kinds from DESIGN.md §3.6 fire when an assembled spec chain contains
mechanically-detectable disagreements:

- ``depends_on_vs_forbids`` (C8) — a ``Depends on:`` entry's name contains a
  ``Forbids:`` entry. **High signal**: a clear violation.
- ``duplicate_parent_rule`` (C9) — a child spec restates a parent's
  ``Must`` / ``Must not`` byte-identically. Drift risk.
- ``task_violates_must_not`` (C10) — a task's text mechanically restates a
  ``Must not``. **Warning-quality** (high false-positive rate); the slash
  command treats this as advisory only.
- ``must_vs_must_not`` (C11) — a ``Must`` and a ``Must not`` are byte-
  identical after section-prefix stripping. Defensive, very rare in practice.

Each detector is a pure function. The merge orchestrator in
:mod:`specdd_mcp.operations.merge` calls them and unions the results into
:attr:`EffectiveConstraints.conflicts`.

Convention across all four: ``rule_a`` is the child / newer / violator side,
``rule_b`` is the parent / inherited / rule being violated. Downstream
callers can rely on this ordering for messaging.
"""

from __future__ import annotations

from specdd_mcp.types import Conflict, Constraint, EffectiveConstraints


def detect_depends_on_vs_forbids(
    constraints: EffectiveConstraints,
) -> list[Conflict]:
    """Flag every ``Depends on:`` whose name contains a ``Forbids:`` entry.

    Match rule: ``forbid.rule in depend.rule`` — case-sensitive,
    substring-or-equal. ``Depends on: stripe-node`` with ``Forbids: stripe``
    is a conflict because the forbidden name is contained in the dependency
    name. The reverse direction (``Depends on: stripe`` with
    ``Forbids: stripe-node``) is **not** a conflict — the dependency doesn't
    pull in the forbidden thing.

    rule_a = ``Depends on:`` (the violator); rule_b = ``Forbids:`` (the rule).
    """
    conflicts: list[Conflict] = []
    for depend in constraints.depends_on:
        for forbid in constraints.forbids:
            if forbid.rule in depend.rule:
                conflicts.append(
                    Conflict(
                        kind="depends_on_vs_forbids",
                        rule_a=depend,
                        rule_b=forbid,
                    )
                )
    return conflicts


def detect_task_violates_must_not(
    constraints: EffectiveConstraints,
) -> list[Conflict]:
    """Flag tasks whose text mechanically restates a ``Must not:`` rule.

    **Warning-quality detector with a HIGH FALSE-POSITIVE rate.** The match
    is a case-insensitive substring of the (period-stripped) ``Must not:``
    text inside the (period-stripped) task text.

    Real cases this catches:

    - Task: ``[ ] Add tax calculation`` + ``Must not: Calculate tax`` →
      flagged (substring overlap on the action verb-phrase).

    Real cases this **mis**-flags:

    - Task: ``[ ] Don't calculate tax`` + ``Must not: Calculate tax`` →
      flagged, even though the task *reinforces* the rule.
    - Task: ``[ ] Document that we don't calculate tax`` → flagged.

    Because of these false positives, the ``/specc`` slash command body
    treats ``task_violates_must_not`` conflicts as **advisory only** —
    never a hard stop. They surface for human review, not for the agent to
    refuse work.

    rule_a = the task (built as a ``Constraint`` from the task's text +
    source spec + line); rule_b = the ``Must not:`` rule.
    """
    conflicts: list[Conflict] = []
    for task in constraints.tasks:
        task_normalized = _normalize_for_fuzzy_match(task.text)
        if not task_normalized:
            continue
        for must_not in constraints.must_not:
            rule_normalized = _normalize_for_fuzzy_match(must_not.rule)
            if rule_normalized and rule_normalized in task_normalized:
                conflicts.append(
                    Conflict(
                        kind="task_violates_must_not",
                        rule_a=Constraint(
                            rule=task.text,
                            source=task.source,
                            line=task.line,
                        ),
                        rule_b=must_not,
                    )
                )
    return conflicts


def _normalize_for_fuzzy_match(text: str) -> str:
    """Lowercase + strip trailing period for the fuzzy substring comparison
    used by :func:`detect_task_violates_must_not`. Defensive against empty
    inputs."""
    return text.lower().rstrip(".").strip()


def detect_must_vs_must_not(
    constraints: EffectiveConstraints,
) -> list[Conflict]:
    """Flag byte-identical rules appearing in both ``Must`` and ``Must not``.

    Vanishingly rare in practice — no one writes a ``Must: X`` and a
    ``Must not: X`` saying the same thing intentionally. But when it does
    fire, the contradiction is 100% real, so this is a cheap defensive
    check worth keeping.

    Match rule: case-sensitive byte-identical equality. Works whether the
    two rules are in the same spec (legal but suspicious) or different
    specs.

    rule_a = ``Must:`` (the must-do that contradicts); rule_b =
    ``Must not:`` (the stronger rule being violated). DESIGN.md §"Conflict
    handling" treats ``Must not`` as stronger than ``Must``, so the
    convention is: rule_a is the would-be action, rule_b is the
    prohibition that overrides it.
    """
    conflicts: list[Conflict] = []
    for must in constraints.must:
        for must_not in constraints.must_not:
            if must.rule == must_not.rule:
                conflicts.append(
                    Conflict(
                        kind="must_vs_must_not",
                        rule_a=must,
                        rule_b=must_not,
                    )
                )
    return conflicts


def detect_duplicate_parent_rule(
    constraints: EffectiveConstraints,
) -> list[Conflict]:
    """Flag every ``Must`` / ``Must not`` rule that a child spec restates
    byte-identically from a **path-ancestor** spec.

    Why surface this? When a child spec copies a parent's rule verbatim, the
    two copies drift independently: a future change to the parent rule won't
    update the child copy. The DESIGN encourages "write each rule once, in
    the spec that owns it, and let inheritance carry it down."

    Two relationships filtered OUT explicitly:

    - **Intra-spec duplicates** (same ``source``) — that's a quality issue,
      not a drift-vs-parent issue; ``validate_spec`` flags it in PR 5.
    - **Sibling specs** in the same directory (e.g. ``components/form.sdd``
      and ``components/list.sdd``) — peers, not parents. Real benchmark data
      revealed this: SpecDD-style projects often have several leaf specs in
      one directory that share legitimate cross-cutting rules. Flagging them
      as drift would be noise.

    Conflict convention: rule_a = the duplicate (child / deeper in tree);
    rule_b = the original (ancestor / shallower). With three+ levels of
    duplication, every (descendant, ancestor) pair emits its own conflict
    so the caller sees every drift surface.
    """
    conflicts: list[Conflict] = []
    for section_list in (constraints.must, constraints.must_not):
        for i in range(1, len(section_list)):
            current = section_list[i]
            for j in range(i):
                earlier = section_list[j]
                if (
                    earlier.rule == current.rule
                    and earlier.source != current.source
                    and _is_path_ancestor(earlier.source, current.source)
                ):
                    conflicts.append(
                        Conflict(
                            kind="duplicate_parent_rule",
                            rule_a=current,
                            rule_b=earlier,
                        )
                    )
    return conflicts


def _is_path_ancestor(ancestor: str, descendant: str) -> bool:
    """Return True iff ``ancestor``'s containing directory is a **strict
    prefix** of ``descendant``'s containing directory.

    Used by :func:`detect_duplicate_parent_rule` to distinguish real
    parent-vs-child drift from same-directory peer relationships.

    Examples:

    - ``app.sdd`` (root) vs ``src/service.sdd`` → True (root is ancestor).
    - ``src/module.sdd`` vs ``src/billing/service.sdd`` → True.
    - ``src/components/form.sdd`` vs ``src/components/list.sdd`` → False
      (siblings, same directory).
    - ``app.sdd`` vs ``other.sdd`` (both at root) → False (siblings).
    """
    a_dir = ancestor.rsplit("/", 1)[0] if "/" in ancestor else ""
    d_dir = descendant.rsplit("/", 1)[0] if "/" in descendant else ""
    if a_dir == d_dir:
        return False
    if a_dir == "":
        return True
    return d_dir.startswith(a_dir + "/")
