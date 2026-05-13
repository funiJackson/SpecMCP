"""Tests for the MCP tool wrappers in :mod:`specdd_mcp.server.tools`.

Covers:
- The wrapper produces the Result envelope shape FastMCP serializes.
- Logging happens at invocation and completion.
- Unexpected exceptions are caught and converted to Err.
- The tool is registered against the FastMCP singleton (verified via
  ``mcp.list_tools()``, the supported public API).

End-to-end protocol behavior is tested in PR 2 commit 8 (`test_server.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from specdd_mcp.server.app import mcp
from specdd_mcp.server.logging import TOOL_LOGGER
from specdd_mcp.server.tools import parse_spec, resolve_spec_chain

# ---------------------------------------------------------------------------
# Wrapper output shape
# ---------------------------------------------------------------------------


def test_parse_spec_returns_ok_for_valid_content() -> None:
    result = parse_spec(content="Spec: Foo\n")
    assert result["ok"] is True
    assert result["data"]["name"] == "Foo"
    assert result["data"]["level"] == "unknown"
    assert "warnings" in result


def test_parse_spec_with_virtual_path_infers_level() -> None:
    result = parse_spec(
        content="Spec: Inv\n",
        virtual_path="src/billing/services/invoice.sdd",
    )
    assert result["ok"] is True
    assert result["data"]["level"] == "service"


def test_parse_spec_returns_err_for_missing_file(tmp_path: Path) -> None:
    result = parse_spec(path=str(tmp_path / "missing.sdd"))
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_parse_spec_returns_err_for_both_inputs() -> None:
    """Both path and content → INVALID_INPUT, propagated through the wrapper."""
    result = parse_spec(path="x.sdd", content="Spec: X\n")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_parse_spec_returns_err_for_no_inputs() -> None:
    result = parse_spec()
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_parse_spec_returns_err_for_binary_content() -> None:
    result = parse_spec(content="some\x00binary\x00data")
    assert result["ok"] is False
    assert result["error"] == "PARSE_ERROR"
    assert result["details"].get("kind") == "binary"


def test_parse_spec_result_envelope_keys_present_on_success() -> None:
    result = parse_spec(content="Spec: X\n")
    assert set(result.keys()) >= {"ok", "data", "warnings"}


def test_parse_spec_result_envelope_keys_present_on_failure() -> None:
    result = parse_spec(path="/nonexistent/should-not-exist.sdd")
    assert set(result.keys()) >= {"ok", "error", "message", "details"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_logs_invocation_and_ok_result(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        parse_spec(content="Spec: X\n")
    messages = [r.getMessage() for r in caplog.records]
    assert any("parse_spec called with" in m for m in messages)
    assert any("parse_spec → ok" in m for m in messages)


def test_logs_error_code_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        parse_spec(path="x.sdd", content="Spec: X\n")
    messages = [r.getMessage() for r in caplog.records]
    assert any("→ err INVALID_INPUT" in m for m in messages)


def test_logs_truncate_long_content_in_invocation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 50 KB content arg should NOT dump 50 KB to stderr."""
    big = "Spec: X\n" + "x" * 50_000
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        parse_spec(content=big)
    invocation_msg = next(
        m for r in caplog.records if "called with" in (m := r.getMessage())
    )
    assert "more chars" in invocation_msg
    assert len(invocation_msg) < 2_000  # generous upper bound


# ---------------------------------------------------------------------------
# Unexpected exception conversion
# ---------------------------------------------------------------------------


def test_unexpected_parser_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the underlying parser raises (programming bug, not modeled error),
    the wrapper converts to INVALID_INPUT with the exception details."""

    def _raise_unexpected(**_: object) -> None:
        raise RuntimeError("simulated parser bug")

    monkeypatch.setattr(
        "specdd_mcp.server.tools._parse_spec",
        _raise_unexpected,
    )
    result = parse_spec(content="Spec: X\n")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated parser bug" in result["message"]
    assert result["details"]["exception_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_spec_registered_with_mcp_singleton() -> None:
    """The @mcp.tool() decorator registers parse_spec on the FastMCP singleton.

    Uses the supported public API (`mcp.list_tools()`) rather than poking at
    internal attributes — this stays robust across MCP SDK versions.
    """
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]
    assert "parse_spec" in names


@pytest.mark.asyncio
async def test_parse_spec_tool_has_description_for_agents() -> None:
    """The docstring becomes the tool description — Claude reads this when
    deciding whether to use the tool."""
    tools = await mcp.list_tools()
    parse_spec_tool = next(t for t in tools if t.name == "parse_spec")
    assert parse_spec_tool.description is not None
    assert len(parse_spec_tool.description) > 100
    # The "when to prefer this" pitch is the load-bearing part of the
    # docstring. Regress if it's ever dropped.
    assert "Prefer this over" in parse_spec_tool.description


# ---------------------------------------------------------------------------
# resolve_spec_chain wrapper
# ---------------------------------------------------------------------------


def _make_repo_with_spec(tmp_path: Path) -> Path:
    (tmp_path / ".specdd").mkdir()
    (tmp_path / "app.sdd").write_text("Spec: App\n")
    return tmp_path


def test_resolve_chain_returns_ok_with_chain(tmp_path: Path) -> None:
    repo = _make_repo_with_spec(tmp_path)
    code = repo / "src" / "x.ts"
    code.parent.mkdir()
    code.write_text("// x\n")
    result = resolve_spec_chain(target=str(code))
    assert result["ok"] is True
    assert len(result["data"]["chain"]) == 1
    assert result["data"]["chain"][0]["name"] == "App"


def test_resolve_chain_returns_err_for_missing_target(tmp_path: Path) -> None:
    _make_repo_with_spec(tmp_path)
    result = resolve_spec_chain(target=str(tmp_path / "ghost.sdd"))
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_resolve_chain_returns_err_for_relative_target_no_repo(tmp_path: Path) -> None:
    result = resolve_spec_chain(target="src/foo.sdd")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_resolve_chain_logs_invocation_and_ok_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _make_repo_with_spec(tmp_path)
    code = repo / "x.ts"
    code.write_text("// x\n")
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        resolve_spec_chain(target=str(code))
    messages = [r.getMessage() for r in caplog.records]
    assert any("resolve_spec_chain called with" in m for m in messages)
    assert any("resolve_spec_chain → ok" in m for m in messages)


def test_resolve_chain_unexpected_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_unexpected(**_: object) -> None:
        raise RuntimeError("simulated chain bug")

    monkeypatch.setattr(
        "specdd_mcp.server.tools._resolve_spec_chain",
        _raise_unexpected,
    )
    result = resolve_spec_chain(target="/nonexistent.sdd")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated chain bug" in result["message"]


@pytest.mark.asyncio
async def test_resolve_chain_registered_with_mcp_singleton() -> None:
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]
    assert "resolve_spec_chain" in names


@pytest.mark.asyncio
async def test_resolve_chain_tool_has_description_for_agents() -> None:
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "resolve_spec_chain")
    assert tool.description is not None
    assert "Prefer this over" in tool.description
