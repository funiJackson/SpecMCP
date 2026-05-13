"""End-to-end MCP protocol tests.

Spawns ``specdd-mcp`` as a subprocess and exercises the full JSON-RPC
protocol — initialize handshake, ``list_tools``, ``call_tool``. These tests
catch integration issues that the wrapper unit tests in ``test_server_tools``
cannot (FastMCP schema generation, JSON serialization, response shape).

We use an inline ``asynccontextmanager`` instead of a ``pytest-asyncio``
fixture because ``stdio_client`` enters a cancel scope that must be exited
in the SAME asyncio task — pytest-asyncio's fixture machinery doesn't
guarantee that, so a fixture-based approach raises
``RuntimeError: Attempted to exit cancel scope in a different task``.
Calling the helper inline keeps setup + teardown in the test's own task.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.conftest import CHAINS_DIR


@asynccontextmanager
async def mcp_session() -> AsyncIterator[ClientSession]:
    """Spawn ``specdd-mcp`` and yield an initialized client session.

    The subprocess is torn down on context exit. Stderr from the server is
    inherited by the test runner (and captured by pytest's stderr capture).
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "specdd_mcp"],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def _extract_payload(result: Any) -> dict[str, Any]:
    """Pull the JSON payload out of a CallToolResult.

    MCP places a tool's structured output in either ``structuredContent``
    (modern path) or as ``TextContent`` under ``content``. Handle both so
    this works across MCP SDK versions.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    if result.content:
        first = result.content[0]
        if getattr(first, "type", None) == "text":
            return json.loads(first.text)
    raise AssertionError(f"unexpected CallToolResult: {result!r}")


# ---------------------------------------------------------------------------
# Handshake + registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_initializes_cleanly() -> None:
    """The handshake completing without error is itself the assertion. This
    catches the case where ``python -m specdd_mcp`` crashes at startup."""
    async with mcp_session() as session:
        assert session is not None


@pytest.mark.asyncio
async def test_list_tools_returns_parse_spec_and_resolve_chain() -> None:
    async with mcp_session() as session:
        tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    assert "parse_spec" in names
    assert "resolve_spec_chain" in names


@pytest.mark.asyncio
async def test_tool_descriptions_carry_agent_pitch() -> None:
    """The 'Prefer this over...' pitch must survive MCP schema generation —
    otherwise Claude sees a stripped description and the load-bearing sales
    line is gone."""
    async with mcp_session() as session:
        tools = await session.list_tools()
    parse_spec = next(t for t in tools.tools if t.name == "parse_spec")
    assert parse_spec.description is not None
    assert "Prefer this over" in parse_spec.description


# ---------------------------------------------------------------------------
# parse_spec round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_parse_spec_with_inline_content() -> None:
    async with mcp_session() as session:
        result = await session.call_tool(
            "parse_spec",
            {"content": "Spec: Inline Test\n"},
        )
    assert not result.isError
    payload = _extract_payload(result)
    assert payload["ok"] is True
    assert payload["data"]["name"] == "Inline Test"


@pytest.mark.asyncio
async def test_call_parse_spec_with_missing_path_returns_err_not_protocol_error() -> None:
    """A missing file is a tool-level Err, not a protocol error: ``isError``
    stays False, the payload carries the structured error code."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "parse_spec",
            {"path": "/definitely-does-not-exist-12345.sdd"},
        )
    assert not result.isError
    payload = _extract_payload(result)
    assert payload["ok"] is False
    assert payload["error"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_call_parse_spec_with_invalid_input_round_trips_as_err() -> None:
    """Both ``path`` and ``content`` → INVALID_INPUT, propagated through MCP."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "parse_spec",
            {"path": "x.sdd", "content": "Spec: X\n"},
        )
    assert not result.isError
    payload = _extract_payload(result)
    assert payload["ok"] is False
    assert payload["error"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# resolve_spec_chain round-trip against a committed fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_resolve_spec_chain_on_simple_3_level_fixture() -> None:
    target = (
        CHAINS_DIR
        / "simple_3_level"
        / "src"
        / "billing"
        / "services"
        / "invoice.ts"
    )
    async with mcp_session() as session:
        result = await session.call_tool(
            "resolve_spec_chain",
            {"target": str(target)},
        )
    assert not result.isError
    payload = _extract_payload(result)
    assert payload["ok"] is True
    chain = payload["data"]["chain"]
    assert [s["name"] for s in chain] == [
        "Billing Platform",
        "Billing Module",
        "Invoice Service",
    ]
    assert payload["data"]["nearest"]["name"] == "Invoice Service"


@pytest.mark.asyncio
async def test_call_resolve_spec_chain_with_relative_target_no_repo_root() -> None:
    async with mcp_session() as session:
        result = await session.call_tool(
            "resolve_spec_chain",
            {"target": "src/foo.sdd"},
        )
    assert not result.isError
    payload = _extract_payload(result)
    assert payload["ok"] is False
    assert payload["error"] == "INVALID_INPUT"
