"""Smoke tests for the FastMCP application skeleton.

PR 2 commit 1 — verifies the server can be imported and the singleton is
configured. Actual stdio behavior and tool registration are tested in later
commits (C4 onwards for tools, C8 for end-to-end through the MCP protocol).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from specdd_mcp.server import mcp
from specdd_mcp.server.app import mcp as mcp_from_app


def test_mcp_singleton_exists() -> None:
    assert mcp is not None
    assert isinstance(mcp, FastMCP)


def test_mcp_singleton_is_shared_across_imports() -> None:
    """`server.mcp` and `server.app.mcp` must be the same object so tool
    registrations don't get lost depending on which import path is used."""
    assert mcp is mcp_from_app


def test_main_entry_point_callable() -> None:
    """`__main__.main` exists and is callable. We don't invoke it here because
    `mcp.run(transport='stdio')` blocks forever — the end-to-end test in C8
    spawns the server as a subprocess instead."""
    from specdd_mcp.__main__ import main

    assert callable(main)
