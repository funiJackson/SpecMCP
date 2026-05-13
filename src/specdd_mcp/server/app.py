"""The FastMCP application instance.

Tools register against this singleton via the ``@mcp.tool()`` decorator from
:mod:`specdd_mcp.server.tools` (added in PR 2 commit 4+). Keeping the instance
in its own module avoids import cycles between the entry point and the tool
modules.

The instance name ``"specdd"`` is what Claude Code (and other MCP clients)
prefix to every tool — e.g. ``mcp__specdd__parse_spec``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp: FastMCP = FastMCP("specdd")
"""Singleton FastMCP instance. Tool registrations land here."""
