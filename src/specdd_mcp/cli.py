"""``specdd-mcp`` command-line interface (DESIGN §7.3).

A thin ``argparse`` dispatcher — zero extra dependencies — over four
subcommands:

- (none) / ``serve`` — start the MCP server on stdio. This is the default so
  the bare ``specdd-mcp`` invocation MCP clients spawn keeps working.
- ``bootstrap`` — drop the SpecDD bootstrap files (``.specdd/bootstrap*.md``,
  ``AGENTS.md``, ``CLAUDE.md``, optional ``app.sdd``) into a repo, refusing to
  clobber anything that already exists.
- ``validate`` — parse + validate one spec or every spec under a path, print a
  report, and exit non-zero when any **error** is found (CI hook).
- ``version`` — print the package version.

Each handler returns an ``int`` exit code; :func:`main` returns it and the
``__main__`` entry point feeds it to ``sys.exit``. The server is imported
lazily inside the serve handler so ``validate`` / ``bootstrap`` / ``version``
don't pay the cost of loading FastMCP and registering every tool.
"""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

from specdd_mcp import __version__
from specdd_mcp.operations.create_spec import create_spec
from specdd_mcp.operations.validation import run_validation
from specdd_mcp.operations.walks import DEFAULT_MAX_SPECS, TooLargeError, walk_specs
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.paths import find_repo_root, to_repo_relative
from specdd_mcp.types import Err

#: Bootstrap files written by ``bootstrap``, mapped to their template name in
#: :mod:`specdd_mcp.templates`. Order is the report order.
_BOOTSTRAP_FILES: tuple[tuple[str, str], ...] = (
    (".specdd/bootstrap.md", "bootstrap.md"),
    (".specdd/bootstrap.project.md", "bootstrap.project.md"),
    (".specdd/bootstrap.local.md", "bootstrap.local.md"),
    ("AGENTS.md", "AGENTS.md"),
    ("CLAUDE.md", "CLAUDE.md"),
)

#: Slash-command files shipped by ``install-commands``, as POSIX paths
#: relative to both ``specdd_mcp/templates/commands/`` (the bundled source)
#: and the install target (``~/.claude/commands/``). The repo-root ``commands/``
#: tree is the human-editable source; a drift-guard test keeps the two equal.
_COMMAND_FILES: tuple[str, ...] = (
    "specc.md",
    "specc/audit.md",
    "specc/status.md",
    "specc/draft.md",
)

#: Default install target for ``install-commands`` — Claude Code's user-level
#: command directory.
_DEFAULT_COMMANDS_DIR = Path.home() / ".claude" / "commands"


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser. Default action (no subcommand) is serve."""
    parser = argparse.ArgumentParser(
        prog="specdd-mcp",
        description=(
            "Deterministic tooling for SpecDD .sdd files. With no subcommand, "
            "starts the MCP server on stdio."
        ),
    )
    parser.set_defaults(func=cmd_serve)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the MCP server on stdio (default)")
    serve.set_defaults(func=cmd_serve)

    bootstrap = sub.add_parser(
        "bootstrap",
        help="write SpecDD bootstrap files into a repo (refuses to clobber)",
    )
    bootstrap.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="target directory (default: current directory)",
    )
    bootstrap.add_argument(
        "--with-app",
        action="store_true",
        help="also scaffold a starter app.sdd spec",
    )
    bootstrap.set_defaults(func=cmd_bootstrap)

    validate = sub.add_parser(
        "validate",
        help="validate one spec or every spec under a path (exit 1 on errors)",
    )
    validate.add_argument(
        "path",
        nargs="?",
        default=".",
        help="a .sdd file or a directory to walk (default: current directory)",
    )
    validate.add_argument(
        "--max-specs",
        type=int,
        default=DEFAULT_MAX_SPECS,
        help=f"walk-time cap on .sdd files (default: {DEFAULT_MAX_SPECS})",
    )
    validate.set_defaults(func=cmd_validate)

    install = sub.add_parser(
        "install-commands",
        help="copy the /specc slash commands into ~/.claude/commands/",
    )
    install.add_argument(
        "--dir",
        dest="directory",
        default=None,
        help="install target (default: ~/.claude/commands)",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="overwrite command files that already exist",
    )
    install.set_defaults(func=cmd_install_commands)

    version = sub.add_parser("version", help="print the package version")
    version.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the selected subcommand handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_serve(_args: argparse.Namespace) -> int:
    """Start the MCP server on stdio. Blocks until the client disconnects.

    The server (and FastMCP) is imported here, not at module top, so the other
    subcommands stay lightweight.
    """
    import logging

    # Side-effect import: registers every @mcp.tool() on the singleton.
    import specdd_mcp.server.tools  # noqa: F401
    from specdd_mcp.server import mcp
    from specdd_mcp.server.logging import SERVER_LOGGER, configure

    configure()
    logging.getLogger(SERVER_LOGGER).info(
        "specdd-mcp starting on stdio transport"
    )
    mcp.run(transport="stdio")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    """Print the package version."""
    print(__version__)
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Write the SpecDD bootstrap files into ``args.directory``.

    Existing files are never overwritten — each is skipped and reported. With
    ``--with-app``, a starter ``app.sdd`` is scaffolded via ``create_spec``
    (which also refuses to clobber).
    """
    target = Path(args.directory)
    created: list[str] = []
    skipped: list[str] = []

    for rel, template_name in _BOOTSTRAP_FILES:
        dest = target / rel
        if dest.exists():
            skipped.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_template(template_name), encoding="utf-8")
        created.append(rel)

    if args.with_app:
        app_name = target.resolve().name or "App"
        result = create_spec(
            target / "app.sdd",
            name=app_name,
            level="app",
            purpose=f"Top-level spec for {app_name}.",
        )
        if isinstance(result, Err):
            if result.error == "ALREADY_EXISTS":
                skipped.append("app.sdd")
            else:  # pragma: no cover — generated app spec is always valid
                print(f"could not scaffold app.sdd: {result.message}")
        else:
            created.append("app.sdd")

    for rel in created:
        print(f"created  {rel}")
    for rel in skipped:
        print(f"skipped  {rel} (already exists)")
    if not created:
        print("nothing to do — every bootstrap file already exists")
    return 0


def cmd_install_commands(args: argparse.Namespace) -> int:
    """Copy the bundled ``/specc`` slash commands into a commands directory.

    Defaults to ``~/.claude/commands/``. Existing files are skipped (and
    reported) unless ``--force`` is given. Subdirectory structure is preserved
    so ``specc/audit.md`` lands as ``specc/audit.md``.
    """
    target = Path(args.directory) if args.directory else _DEFAULT_COMMANDS_DIR
    created: list[str] = []
    skipped: list[str] = []
    overwritten: list[str] = []

    for rel in _COMMAND_FILES:
        dest = target / rel
        exists = dest.exists()
        if exists and not args.force:
            skipped.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_command_template(rel), encoding="utf-8")
        (overwritten if exists else created).append(rel)

    for rel in created:
        print(f"installed  {rel}")
    for rel in overwritten:
        print(f"overwrote  {rel}")
    for rel in skipped:
        print(f"skipped    {rel} (already exists — use --force to overwrite)")
    print(f"\ntarget: {target}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate one spec or every spec under a path. Exit 1 if any errors.

    Cross-spec inheritance rules run when a repo root is detectable (so the
    chain can be resolved); otherwise only single-file rules apply.
    """
    target = Path(args.path)
    if not target.exists():
        print(f"path does not exist: {target}")
        return 1

    repo_root = find_repo_root(target)

    if target.is_file():
        spec_paths = [target]
    else:
        try:
            spec_paths = walk_specs(target, max_specs=args.max_specs).paths
        except TooLargeError as exc:
            print(str(exc))
            return 1

    if not spec_paths:
        print(f"no .sdd files found under {target}")
        return 0

    total_errors = 0
    total_warnings = 0
    clean = 0

    for spec_path in spec_paths:
        label = _label(spec_path, repo_root)
        parsed = parse_spec(path=str(spec_path))
        if isinstance(parsed, Err):
            total_errors += 1
            print(f"{label}: error {parsed.error}: {parsed.message}")
            continue
        data = run_validation(
            parsed.data,
            check_inheritance=repo_root is not None,
            repo_root=repo_root,
        )
        total_errors += data.summary.errors
        total_warnings += data.summary.warnings
        if not data.issues:
            clean += 1
            continue
        for issue in data.issues:
            where = f":{issue.line}" if issue.line is not None else ""
            print(f"{label}{where}: {issue.severity} {issue.code}: {issue.message}")

    print(
        f"\n{len(spec_paths)} spec(s): {clean} clean, "
        f"{total_errors} error(s), {total_warnings} warning(s)"
    )
    return 1 if total_errors else 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _template(name: str) -> str:
    """Read a bundled bootstrap template by filename."""
    return (files("specdd_mcp.templates") / name).read_text(encoding="utf-8")


def _command_template(rel: str) -> str:
    """Read a bundled slash-command file by its POSIX-relative path.

    ``rel`` may contain ``/`` (e.g. ``specc/audit.md``); each segment is
    navigated in turn so it works through ``importlib.resources`` whether the
    package runs from source or a wheel.
    """
    node = files("specdd_mcp.templates") / "commands"
    for segment in rel.split("/"):
        node = node / segment
    return node.read_text(encoding="utf-8")


def _label(spec_path: Path, repo_root: Path | None) -> str:
    """Repo-relative POSIX label for a spec, falling back to the raw path."""
    if repo_root is not None:
        try:
            return to_repo_relative(spec_path, repo_root)
        except Exception:  # cosmetic label only; never fail the report here
            pass
    return str(spec_path)
