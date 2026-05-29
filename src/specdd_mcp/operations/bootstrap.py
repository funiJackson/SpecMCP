"""``bootstrap_project``: initialize SpecDD in a repo (DESIGN §6.4).

Drops the canonical SpecDD bootstrap files — ``.specdd/bootstrap*.md``,
``AGENTS.md``, ``CLAUDE.md``, and optionally a starter ``app.sdd`` — into a
target directory, **refusing to clobber** anything that already exists. The
templates are bundled package data read via :func:`importlib.resources.files`.

This is the single source of truth for both surfaces: the ``specdd-mcp
bootstrap`` CLI subcommand and the ``bootstrap_project`` MCP tool (which lets
agents in non-Claude-Code clients run setup) both call :func:`bootstrap_project`
and only differ in how they present the result.

Pure-ish operation: it writes files (like ``create_spec`` and
``update_task_status``), but returns structured data rather than printing.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from specdd_mcp.operations.create_spec import create_spec
from specdd_mcp.types import BootstrapData, BootstrapResult, Err, Ok

#: Bootstrap files written, mapped to their template name in
#: :mod:`specdd_mcp.templates`. Order is the report order.
BOOTSTRAP_FILES: tuple[tuple[str, str], ...] = (
    (".specdd/bootstrap.md", "bootstrap.md"),
    (".specdd/bootstrap.project.md", "bootstrap.project.md"),
    (".specdd/bootstrap.local.md", "bootstrap.local.md"),
    ("AGENTS.md", "AGENTS.md"),
    ("CLAUDE.md", "CLAUDE.md"),
)


def bootstrap_project(
    directory: Path,
    *,
    with_app: bool = False,
) -> BootstrapResult:
    """Write the SpecDD bootstrap files into ``directory``.

    See DESIGN.md §6.4 for the contract.

    Args:
        directory: Target repo directory. Parent directories of each written
            file are created as needed.
        with_app: When ``True``, also scaffold a starter ``app.sdd`` via
            :func:`~specdd_mcp.operations.create_spec.create_spec`.

    Returns:
        :class:`Ok` wrapping :class:`BootstrapData` with the repo-relative
        ``created`` and ``skipped`` paths. Existing files are never
        overwritten — they land in ``skipped``. Warnings carry any unexpected
        failure scaffolding ``app.sdd`` (which never blocks the rest).
    """
    created: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for rel, template_name in BOOTSTRAP_FILES:
        dest = directory / rel
        if dest.exists():
            skipped.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_template(template_name), encoding="utf-8")
        created.append(rel)

    if with_app:
        app_name = directory.resolve().name or "App"
        result = create_spec(
            directory / "app.sdd",
            name=app_name,
            level="app",
            purpose=f"Top-level spec for {app_name}.",
        )
        if isinstance(result, Err):
            if result.error == "ALREADY_EXISTS":
                skipped.append("app.sdd")
            else:  # pragma: no cover — generated app spec is always valid
                warnings.append(f"could not scaffold app.sdd: {result.message}")
        else:
            created.append("app.sdd")

    return Ok(
        data=BootstrapData(
            directory=str(directory),
            created=created,
            skipped=skipped,
        ),
        warnings=warnings,
    )


def _template(name: str) -> str:
    """Read a bundled bootstrap template by filename."""
    return (files("specdd_mcp.templates") / name).read_text(encoding="utf-8")
