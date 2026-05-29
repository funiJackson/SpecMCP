"""``python -m specdd_mcp`` / ``specdd-mcp`` entry point.

Delegates to the :mod:`specdd_mcp.cli` dispatcher. With no subcommand the CLI
starts the FastMCP server on stdio (the behavior MCP clients depend on);
subcommands (`bootstrap`, `validate`, `version`) are handled there too.
"""

from __future__ import annotations

import sys

from specdd_mcp.cli import main as _cli_main


def main() -> None:
    """Console-script entry point: dispatch and exit with the CLI's code."""
    sys.exit(_cli_main())


if __name__ == "__main__":  # pragma: no cover - script-only entry
    main()
