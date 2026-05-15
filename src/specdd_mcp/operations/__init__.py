"""Cross-spec operations layer.

Built on top of :mod:`specdd_mcp.parser` (which produces ``ParsedSpec`` /
``SpecChain``) and :mod:`specdd_mcp.paths` (filesystem helpers).

Modules:

- :mod:`walks` — bounded ``.sdd`` file iteration with monorepo guardrail.
- :mod:`tasks` — ``list_tasks`` operation (PR 3 C2).
- :mod:`globs` — pattern expansion for ``Owns:`` / ``Can modify:`` (PR 3 C4).
- :mod:`merge` — chain → ``EffectiveConstraints`` aggregation (PR 3 C5-C7).
- :mod:`conflicts` — four conflict detectors (PR 3 C8-C11).

Layering rule (enforced by convention, not tooling): operations import from
``parser`` and ``paths``, never the other way round. Anything that needs to
walk the filesystem to compute cross-spec results lives here, not in
``parser/``.
"""
