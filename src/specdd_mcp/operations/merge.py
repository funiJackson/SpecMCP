"""Build :class:`EffectiveConstraints` from a :class:`SpecChain`.

This is the merge step that ``get_effective_constraints`` (the highest-value
tool in ``/specc``) calls once per implementation task. It collapses a chain
of ``ParsedSpec`` into one merged view with full ``path:line`` provenance
for every rule.

Layered build-out across PR 3:

- **C5 (this file)** — rule arrays (``must``, ``must_not``, ``forbids``,
  ``depends_on``) plus ``tasks``, each carrying source spec + line.
- **C6** — ``effective_write_scope`` (Owns/Can modify globs expanded against
  the live filesystem).
- **C7** — ``done_when``, ``effective_read_scope``, ``references``,
  ``chain_summary``.
- **C8-C11** — the four conflict detectors populate ``conflicts``.

The function is pure: input is a ``SpecChain``, output is an
``EffectiveConstraints``. No filesystem I/O in C5 (that arrives in C6 with
glob expansion).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from specdd_mcp.operations.conflicts import (
    detect_depends_on_vs_forbids,
    detect_duplicate_parent_rule,
    detect_must_vs_must_not,
    detect_task_violates_must_not,
)
from specdd_mcp.operations.globs import expand_pattern
from specdd_mcp.types import (
    ChainSummaryEntry,
    Conflict,
    Constraint,
    EffectiveConstraints,
    KnownSection,
    ParsedSpec,
    ReferenceEntry,
    SpecChain,
    TaskWithSource,
    WriteScopeEntry,
)

# Sections whose bodies are flat lists of strings that map one-to-one to
# :class:`Constraint` entries (rule text + source + line). Adding a new
# constraint-shaped section is a one-line change here plus one line in
# :func:`build_effective_constraints`.
_ConstraintSection = Literal[
    "must",
    "must_not",
    "forbids",
    "depends_on",
    "done_when",
    "can_read",
]

# Sections that grant write authority. The nearest spec (last in the chain)
# carrying any of these is the ``write_authority_source``.
_SCOPE_SECTIONS: tuple[Literal["owns", "can_modify"], ...] = ("owns", "can_modify")


def build_effective_constraints(
    chain: SpecChain,
    repo_root: Path,
) -> EffectiveConstraints:
    """Merge a parsed spec chain into an :class:`EffectiveConstraints` view.

    Args:
        chain: The result of ``resolve_spec_chain(target)``.
        repo_root: Used by C6's glob expansion for ``effective_write_scope``.
            Kept on the C5 signature so adding write-scope assembly later
            doesn't shuffle the call site.

    Returns:
        :class:`EffectiveConstraints` with rule arrays (``must``,
        ``must_not``, ``forbids``, ``depends_on``) and ``tasks`` populated.
        The remaining fields (write/read scope, done_when, references,
        chain_summary, conflicts) stay at default; subsequent PR 3 commits
        light them up.
    """
    must: list[Constraint] = []
    must_not: list[Constraint] = []
    forbids: list[Constraint] = []
    depends_on: list[Constraint] = []
    done_when: list[Constraint] = []
    read_scope: list[Constraint] = []
    tasks: list[TaskWithSource] = []
    write_scope: list[WriteScopeEntry] = []
    write_authority_source: str | None = None
    references: list[ReferenceEntry] = []
    chain_summary: list[ChainSummaryEntry] = [
        ChainSummaryEntry(path=spec.path, level=spec.level)
        for spec in chain.chain
    ]

    for spec in chain.chain:
        must.extend(_constraints_for(spec, "must"))
        must_not.extend(_constraints_for(spec, "must_not"))
        forbids.extend(_constraints_for(spec, "forbids"))
        depends_on.extend(_constraints_for(spec, "depends_on"))
        done_when.extend(_constraints_for(spec, "done_when"))
        read_scope.extend(_constraints_for(spec, "can_read"))
        if spec.tasks:
            for task in spec.tasks:
                tasks.append(
                    TaskWithSource(**task.model_dump(), source=spec.path)
                )
        # References: each ``References:`` entry surfaces as a
        # ``ReferenceEntry(from=<spec.path>, to=<the literal value>, line=<...>)``.
        # We don't try to resolve the target's path — that's a separate
        # operation if/when something needs it.
        refs = spec.references or []
        ref_lines = spec.bullet_lines.get("references", [])
        for index, ref_to in enumerate(refs):
            # ``from`` is a Python keyword; ReferenceEntry uses the ``from_``
            # attribute name with a Pydantic alias to the JSON key ``from``.
            # ``model_validate`` accepts the dict form (with the alias) and
            # keeps mypy happy without a custom cast.
            references.append(
                ReferenceEntry.model_validate(
                    {
                        "from": spec.path,
                        "to": ref_to,
                        "line": (
                            ref_lines[index] if index < len(ref_lines) else 0
                        ),
                    }
                )
            )

        # Write scope from Owns: + Can modify:, with glob expansion against
        # the live filesystem. Spec's own directory is the base for relative
        # patterns. The nearest spec carrying either section wins
        # write_authority_source.
        spec_dir = (repo_root / spec.path).parent
        spec_has_scope_section = False
        for scope_section in _SCOPE_SECTIONS:
            patterns: list[str] = getattr(spec, scope_section) or []
            if patterns:
                spec_has_scope_section = True
            line_numbers = spec.bullet_lines.get(
                cast(KnownSection, scope_section), []
            )
            for index, pattern in enumerate(patterns):
                expansion = expand_pattern(pattern, spec_dir, repo_root)
                write_scope.append(
                    WriteScopeEntry(
                        pattern=expansion.pattern,
                        matches=expansion.matches,
                        source=spec.path,
                        source_line=(
                            line_numbers[index]
                            if index < len(line_numbers)
                            else 0
                        ),
                    )
                )
        if spec_has_scope_section:
            write_authority_source = spec.path

    result = EffectiveConstraints(
        target=chain.target,
        chain_summary=chain_summary,
        must=must,
        must_not=must_not,
        forbids=forbids,
        depends_on=depends_on,
        done_when=done_when,
        effective_read_scope=read_scope,
        effective_write_scope=write_scope,
        write_authority_source=write_authority_source,
        tasks=tasks,
        references=references,
    )
    # Run all four conflict detectors against the assembled view. Each is
    # pure: same input → same output. Detector ordering doesn't matter to
    # correctness, but we run them in roughly increasing fuzziness order
    # (high-signal first) so the conflicts list has the most actionable
    # entries near the top.
    conflicts: list[Conflict] = []
    conflicts.extend(detect_depends_on_vs_forbids(result))
    conflicts.extend(detect_duplicate_parent_rule(result))
    conflicts.extend(detect_must_vs_must_not(result))
    conflicts.extend(detect_task_violates_must_not(result))
    result.conflicts = conflicts
    return result


def _constraints_for(
    spec: ParsedSpec,
    section: _ConstraintSection,
) -> list[Constraint]:
    """Zip a spec's bullet-list section text with its per-bullet line numbers
    and emit :class:`Constraint` objects carrying full provenance.

    Falls back to line 0 if the parser somehow produced ``rules`` without a
    matching ``bullet_lines[section]`` array — defensive against future
    parser changes that forget to keep them aligned.
    """
    rules: list[str] | None = getattr(spec, section)
    if not rules:
        return []
    section_key = cast(KnownSection, section)
    line_numbers = spec.bullet_lines.get(section_key, [])
    constraints: list[Constraint] = []
    for index, rule in enumerate(rules):
        line = line_numbers[index] if index < len(line_numbers) else 0
        constraints.append(
            Constraint(rule=rule, source=spec.path, line=line)
        )
    return constraints
