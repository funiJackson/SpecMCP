"""MCP server layer.

This package exposes the singleton :class:`FastMCP` instance plus the tool
wrappers that delegate to the parser/operations layers.
"""

from specdd_mcp.server.app import mcp

__all__ = ["mcp"]
