"""``python -m specdd_mcp`` / ``specdd-mcp`` entry point.

Starts the FastMCP server on stdio. Blocking — runs until the client
disconnects or the process is terminated.

Importing :mod:`specdd_mcp.server.tools` is required for its side effect:
each ``@mcp.tool()`` decorator registers a tool against the
:class:`FastMCP` singleton. The import must happen *before* :func:`run` is
called, otherwise the server starts with an empty tool catalog.
"""

from __future__ import annotations

# Side-effect import: registers every tool on the FastMCP singleton.
import specdd_mcp.server.tools  # noqa: F401
from specdd_mcp.server import mcp
from specdd_mcp.server.logging import SERVER_LOGGER, configure


def main() -> None:  # pragma: no cover - blocks on stdio; verified by E2E in C8/C9
    """Start the MCP server on stdio."""
    import logging

    configure()
    logging.getLogger(SERVER_LOGGER).info("specdd-mcp starting on stdio transport")
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover - script-only entry
    main()
